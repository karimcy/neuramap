"""Customer datasets in git: the committed manifest, and the scan that keeps it honest.

WP-GRC-05. ``customer_data_manifest.json`` (beside this file) lists every customer dataset
tracked in the repository on the day the DPA guard landed, each with its content hash, the
customer and the site ids it belongs to, and the status ``quarantined``: stored before any DPA
evidence object existed, so acceptance criterion 3 fails for it (spec section 5, S5). Deleting a
file or rewriting history is the owner's decision (spec section 6.2), so nothing here deletes
anything.

`scan` answers "which customer datasets are in git without DPA evidence?" and reports **paths
only**, never contents or figures. It also finds what would let the manifest go stale:

- ``unlisted``: a tracked file under a customer-data root that the manifest does not list;
- ``changed``: a listed file whose bytes no longer match its recorded hash;
- ``copy``: a listed file's bytes tracked at another path;
- ``stale``: a listed file that is no longer tracked (removal is progress: regenerate the manifest);
- ``unlisted_hh_shaped``: a tracked CSV outside the roots that looks like a half-hourly load series
  and is not declared as non-customer data.

A dataset is released only when **every** site id it maps to has a valid, verified DPA evidence
object (`gridq.governance.dpa_guard.require_dpa`). One executed DPA, registered once per site with
the same document hash, releases all of that customer's files.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal

from gridq.contracts.evidence import EvidenceObject
from gridq.governance.dpa_guard import (
    DpaEvidenceMissing,
    load_evidence_registry,
    repo_root,
    require_dpa,
    sha256_file,
)

__all__ = [
    "MANIFEST_PATH",
    "DatasetEntry",
    "Manifest",
    "ScanFinding",
    "ScanResult",
    "build_manifest",
    "classify_path",
    "load_manifest",
    "scan",
    "tracked_files",
]

MANIFEST_PATH: Final = Path(__file__).with_name("customer_data_manifest.json")
MANIFEST_SCHEMA: Final = "gridq.customer_data_manifest/1"

#: Customers whose data is in the repository, the site ids each one's data belongs to, and the
#: tokens that identify them in a path. Site ids are the ``site_id`` of each real site record.
CUSTOMERS: Final[dict[str, dict[str, Any]]] = {
    "grundon": {
        "display": "Grundon Waste Management",
        "site_ids": (
            "grundon_bishops_cleeve_ash_plant",
            "grundon_bishops_cleeve_mrf",
            "grundon_colnbrook",
            "grundon_ewelme",
            "grundon_knowl_hill",
            "grundon_tanhouse",
        ),
        "tokens": ("grundon",),
        "legal_entity_type_recorded": "limited_company",
    },
    "harwell": {
        "display": "Harwell Campus",
        "site_ids": ("harwell_campus_k16w304527",),
        "tokens": ("harwell", "k16w304527"),
        "legal_entity_type_recorded": "unknown",
    },
    "robert_thomas_farms": {
        "display": "Robert Thomas Farms",
        "site_ids": ("robert_thomas_haywood_oaks",),
        "tokens": ("robert_thomas", "haywood"),
        "legal_entity_type_recorded": "missing",
    },
}

#: Grundon site tokens, longest first: a token consumed by a longer match is not matched again.
_GRUNDON_SITE_TOKENS: Final = (
    ("bishops_cleeve_ash_plant", ("grundon_bishops_cleeve_ash_plant",)),
    ("bishops_cleeve_mrf", ("grundon_bishops_cleeve_mrf",)),
    ("bishops_cleeve", ("grundon_bishops_cleeve_ash_plant", "grundon_bishops_cleeve_mrf")),
    ("colnbrook", ("grundon_colnbrook",)),
    ("ewelme", ("grundon_ewelme",)),
    ("knowl_hill", ("grundon_knowl_hill",)),
    ("tanhouse", ("grundon_tanhouse",)),
)

_TEXT_SUFFIXES: Final = frozenset({".md", ".html", ".json", ".csv", ".txt"})
_TIME_COLUMNS: Final = frozenset(
    {"timestamp", "datetime", "date_time", "settlement_date", "period_start", "start", "time"}
)
_LOAD_MARKERS: Final = ("kw", "kwh", "import", "export", "consumption")
#: Fewer rows than one week of half-hours is not a series worth flagging.
_HH_MIN_ROWS: Final = 336

FindingKind = Literal["unlisted", "changed", "copy", "stale", "unlisted_hh_shaped"]


@dataclass(frozen=True)
class DatasetEntry:
    """One customer dataset tracked in git."""

    path: str
    sha256: str
    customer: str
    site_ids: tuple[str, ...]
    kind: str
    status: str = "quarantined"

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "customer": self.customer,
            "site_ids": list(self.site_ids),
            "kind": self.kind,
            "status": self.status,
        }


@dataclass(frozen=True)
class Manifest:
    """The committed customer-data manifest, parsed."""

    customer_roots: tuple[str, ...]
    not_customer_data: tuple[str, ...]
    datasets: tuple[DatasetEntry, ...]
    review_prose: tuple[str, ...]
    waiver_modules: tuple[str, ...]
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    def _rel(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(repo_root()).as_posix()
        except ValueError:
            return None

    def match(self, path: Path) -> DatasetEntry | None:
        """The entry listed at ``path`` (an absolute or repo-relative path), if any."""
        rel = self._rel(path if path.is_absolute() else repo_root() / path)
        if rel is None:
            return None
        for entry in self.datasets:
            if entry.path == rel:
                return entry
        return None

    def match_hash(self, digest: str) -> DatasetEntry | None:
        """The entry whose bytes hash to ``digest`` (a copy anywhere is the same dataset)."""
        for entry in self.datasets:
            if entry.sha256 == digest:
                return entry
        return None

    def has_any_hashes(self) -> bool:
        return bool(self.datasets)

    def under_customer_root(self, path: Path) -> bool:
        rel = self._rel(path)
        if rel is None:
            return False
        return any(rel == r or rel.startswith(r + "/") for r in self.customer_roots)

    def is_not_customer_data(self, path: Path) -> bool:
        rel = self._rel(path)
        return rel is not None and rel in self.not_customer_data


_CACHE: dict[Path, tuple[float, Manifest]] = {}


def load_manifest(path: Path | None = None) -> Manifest:
    """Parse the manifest (cached until the file changes)."""
    target = path if path is not None else MANIFEST_PATH
    mtime = target.stat().st_mtime
    cached = _CACHE.get(target)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    raw = json.loads(target.read_text(encoding="utf-8"))
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"{target.name}: unknown manifest schema {raw.get('schema')!r}")
    manifest = Manifest(
        customer_roots=tuple(raw["customer_roots"]),
        not_customer_data=tuple(e["path"] for e in raw["not_customer_data"]),
        datasets=tuple(
            DatasetEntry(
                path=e["path"],
                sha256=e["sha256"],
                customer=e["customer"],
                site_ids=tuple(e["site_ids"]),
                kind=e["kind"],
                status=e["status"],
            )
            for e in raw["datasets"]
        ),
        review_prose=tuple(e["path"] for e in raw["review_prose"]),
        waiver_modules=tuple(raw["legacy_validation_waiver"]["modules"]),
        raw=raw,
    )
    _CACHE[target] = (mtime, manifest)
    return manifest


def tracked_files(root: Path | None = None) -> list[str]:
    """Every path git tracks in ``root`` (repo-relative, POSIX). Raises if git cannot answer."""
    base = root if root is not None else repo_root()
    out = subprocess.run(
        ["git", "-C", str(base), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


# ---------------------------------------------------------------------------------------------
# Classification (used to build the manifest and to name what an unlisted file belongs to)
# ---------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return text.lower().replace(" ", "_").replace("-", "_")


def _customers_named(text: str) -> list[str]:
    norm = _norm(text)
    return [c for c, spec in CUSTOMERS.items() if any(t in norm for t in spec["tokens"])]


def _grundon_sites(norm: str) -> tuple[str, ...]:
    found: list[str] = []
    rest = norm
    for token, sites in _GRUNDON_SITE_TOKENS:
        if token in rest:
            found.extend(sites)
            rest = rest.replace(token, "")
    return tuple(dict.fromkeys(found))


def _kind(rel: str) -> str:
    suffix = Path(rel).suffix.lower()
    name = Path(rel).name.lower()
    if rel.startswith("deliverables/"):
        return "derived_output"
    if suffix == ".pdf":
        return "supplier_document"
    if suffix in (".xlsx", ".xls"):
        return "meter_or_invoice_workbook"
    if name == "manifest.json":
        return "derived_profile_manifest"
    if suffix == ".csv":
        return "hh_series"
    if ("invoice" in name and "invoice_anchored" not in name) or "monthly" in name:
        return "invoice_extract"
    if "summary" in name:
        return "hh_summary"
    if suffix == ".json":
        return "site_facts"
    return "hh_series"


def classify_path(rel: str, *, content: str | None = None) -> tuple[str, tuple[str, ...]] | None:
    """``(customer, site_ids)`` for a customer file, or ``None`` when nothing identifies it.

    Fails closed: a file that names no single site maps to every site of its customer, and a text
    file that also names another customer maps to that customer's sites too.
    """
    norm = _norm(rel)
    customers = _customers_named(rel)
    grundon_sites = _grundon_sites(norm)
    if not customers and grundon_sites:
        customers = ["grundon"]
    if not customers and content is not None:
        # The path names nobody: attribute the file to every customer its text names.
        customers = _customers_named(content)
        grundon_sites = _grundon_sites(_norm(content)) if "grundon" in customers else ()
    if not customers:
        return None
    sites: list[str] = []
    for customer in customers:
        if customer == "grundon" and grundon_sites:
            sites.extend(grundon_sites)
        else:
            sites.extend(CUSTOMERS[customer]["site_ids"])
    if content is not None:
        for other in _customers_named(content):
            if other not in customers:
                sites.extend(CUSTOMERS[other]["site_ids"])
    return customers[0], tuple(dict.fromkeys(sites))


def _looks_half_hourly(path: Path) -> bool:
    """A CSV whose header has a time column and a load column, with at least a week of rows."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            header = handle.readline()
            columns = {_norm(c.strip().strip('"')) for c in header.split(",")}
            if not columns & _TIME_COLUMNS:
                return False
            if not any(any(m in c for m in _LOAD_MARKERS) for c in columns):
                return False
            rows = sum(1 for _ in handle)
    except OSError:
        return False
    return rows >= _HH_MIN_ROWS


# ---------------------------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ScanFinding:
    kind: FindingKind
    path: str
    detail: str


@dataclass(frozen=True)
class ScanResult:
    """What the scan found. Paths only."""

    unevidenced: tuple[DatasetEntry, ...]
    evidenced: tuple[DatasetEntry, ...]
    findings: tuple[ScanFinding, ...]
    review_prose: tuple[str, ...]

    @property
    def criterion_3_met(self) -> bool:
        """Acceptance criterion 3 for the repository: no customer dataset in git without DPA
        evidence, and nothing the manifest misses."""
        return not self.unevidenced and not self.findings

    def to_json(self) -> dict[str, Any]:
        return {
            "criterion_3_met": self.criterion_3_met,
            "unevidenced_count": len(self.unevidenced),
            "unevidenced": [
                {"path": e.path, "customer": e.customer, "kind": e.kind, "status": e.status}
                for e in self.unevidenced
            ],
            "evidenced": [e.path for e in self.evidenced],
            "findings": [
                {"kind": f.kind, "path": f.path, "detail": f.detail} for f in self.findings
            ],
            "review_prose": list(self.review_prose),
        }


def _released(entry: DatasetEntry, registry: tuple[EvidenceObject, ...], as_of: date) -> bool:
    try:
        for site in entry.site_ids:
            require_dpa(registry, site_id=site, as_of=as_of)
    except DpaEvidenceMissing:
        return False
    return True


def scan(
    root: Path | None = None,
    *,
    manifest: Manifest | None = None,
    registry: Iterable[EvidenceObject] | None = None,
    as_of: date | None = None,
    tracked: Sequence[str] | None = None,
) -> ScanResult:
    """Scan git-tracked files for customer datasets without DPA evidence."""
    base = root if root is not None else repo_root()
    man = manifest if manifest is not None else load_manifest()
    reg = tuple(registry) if registry is not None else load_evidence_registry()
    when = as_of if as_of is not None else date.today()
    files = list(tracked) if tracked is not None else tracked_files(base)
    tracked_set = set(files)
    listed = {e.path: e for e in man.datasets}
    by_hash: dict[str, DatasetEntry] = {e.sha256: e for e in man.datasets}
    roots = man.customer_roots
    not_customer = set(man.not_customer_data)

    findings: list[ScanFinding] = []
    for rel in files:
        path = base / rel
        if not path.is_file():
            continue
        in_root = any(rel == r or rel.startswith(r + "/") for r in roots)
        entry = listed.get(rel)
        if entry is not None:
            if sha256_file(path) != entry.sha256:
                findings.append(
                    ScanFinding("changed", rel, "bytes differ from the manifest's hash")
                )
            continue
        if in_root and rel not in not_customer:
            findings.append(
                ScanFinding("unlisted", rel, "under a customer-data root, not in the manifest")
            )
            continue
        digest = sha256_file(path)
        twin = by_hash.get(digest)
        if twin is not None:
            findings.append(ScanFinding("copy", rel, f"same bytes as listed {twin.path}"))
            continue
        if rel.lower().endswith(".csv") and rel not in not_customer and _looks_half_hourly(path):
            findings.append(
                ScanFinding(
                    "unlisted_hh_shaped",
                    rel,
                    "a half-hourly load series outside the customer roots; list it in the "
                    "manifest or declare it non-customer data with a reason",
                )
            )
    for rel in sorted(listed):
        if rel not in tracked_set:
            findings.append(
                ScanFinding("stale", rel, "listed but no longer tracked; regenerate the manifest")
            )

    unevidenced: list[DatasetEntry] = []
    evidenced: list[DatasetEntry] = []
    for entry in man.datasets:
        if entry.path not in tracked_set:
            continue
        (evidenced if _released(entry, reg, when) else unevidenced).append(entry)
    return ScanResult(
        unevidenced=tuple(unevidenced),
        evidenced=tuple(evidenced),
        findings=tuple(findings),
        review_prose=man.review_prose,
    )


def build_manifest(root: Path | None = None, *, previous: Manifest | None = None) -> dict[str, Any]:
    """A fresh manifest body from the tracked files and the classification rules.

    Keeps the policy sections (roots, non-customer list, review prose, waiver modules) from
    ``previous``; recomputes the datasets. A file under a root that the rules cannot attribute to
    a customer raises, so nobody can list a dataset without saying whose it is.
    """
    base = root if root is not None else repo_root()
    prev = previous if previous is not None else load_manifest()
    raw = dict(prev.raw)
    datasets: list[dict[str, Any]] = []
    for rel in tracked_files(base):
        in_root = any(rel == r or rel.startswith(r + "/") for r in prev.customer_roots)
        if not in_root or rel in prev.not_customer_data:
            continue
        path = base / rel
        content = None
        if path.suffix.lower() in _TEXT_SUFFIXES and path.suffix.lower() != ".csv":
            content = path.read_text(encoding="utf-8", errors="replace")
        owner = classify_path(rel, content=content)
        if owner is None:
            raise ValueError(f"{rel}: cannot attribute this customer-root file to a customer")
        customer, sites = owner
        datasets.append(
            DatasetEntry(
                path=rel,
                sha256=sha256_file(path),
                customer=customer,
                site_ids=sites,
                kind=_kind(rel),
            ).to_json()
        )
    raw["datasets"] = datasets
    raw["dataset_count"] = len(datasets)
    return raw
