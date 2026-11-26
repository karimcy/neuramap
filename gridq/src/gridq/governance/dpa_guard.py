"""The DPA guard: no customer dataset is stored, or loaded, before its DPA evidence object exists.

WP-GRC-05, acceptance criterion 3 ("No customer dataset is stored before its DPA evidence object
exists"). Specification: ``atlas/roadmap/governance/data_protection/gridq_dpa_guard_spec.md``
(sections 3 to 5). Design rulings: Fable 5.2, 2026-11-25 (recorded in ``docs/changelog.md``).

**What counts as DPA evidence.** An IC-4 (1.1.0) `EvidenceObject` of type
``data_processing_agreement``, issued by the customer, scoped to the site, ``VALID`` as of the date
of storage and verified. `assess` already ignores synthetic fixtures and records Neura issued
itself, so neither can ever pass. Nothing in this module creates an evidence object: an executed
DPA is a person-only item.

**Where evidence lives.** A files-first registry (GridQ convention D4): one ``serialise_evidence``
JSON per object in the directory named by ``GRIDQ_EVIDENCE_DIR`` (default ``<repo>/evidence``,
git-ignored). An unreadable or invalid file makes the registry refuse to load (fail closed).

**Three choke points.**

1. `authorise_store` before anything is written: `gridq.app.store.save_run`, the web intake, and
   ``gridq run`` / ``gridq render``. It returns a `DpaPermit`, recorded beside what is stored as
   ``dpa_evidence.json``, so every stored dataset names the DPA document hash it was stored under.
2. `check_load` before a file is read: `load_half_hourly_csv`, `load_site_model_json` and the
   pipeline's invoice-evidence read. A file is a customer dataset when its path is under a
   customer-data root, it is listed in the committed manifest (by path, or by content hash, which
   catches a copy anywhere), or it sits in the run store or the transient workspace.
3. `TransientWorkspace`: where an upload and a modelled profile live while a run processes them.
   Content-addressed (so ``quote_id`` stays deterministic), deleted when the run ends, and the only
   place a load of un-DPA'd customer bytes is allowed (``transient_unstored``), matching the
   retention schedule's "processed in memory and discarded" row for the public tool.

**The files already in git** are listed in ``customer_data_manifest.json`` as quarantined. Loading
one is refused unless every site it maps to has a valid DPA. The single exception is
`legacy_validation_read`: a read-only waiver that only ``tests/conftest.py`` can open, only for the
test modules the manifest lists (a list that may only shrink), and each read is logged as
``quarantined_legacy_unevidenced``. It never permits a store. No environment variable or CLI flag
can open it. Whether those files stay, with executed DPAs registered, or leave the repository and
its history is the owner's decision (spec section 6.2).

Determinism: nothing here enters a pricing hash. ``as_of`` defaults to today at this boundary
(IC-4 itself has no clock), exactly as the access log already reads the wall clock.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

from gridq.contracts.evidence import (
    EvidenceCheck,
    EvidenceObject,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    SiteScope,
    assess,
)

__all__ = [
    "DEMO_ATTESTATION",
    "DEMO_SITE_ID",
    "DEMO_SITE_IDS",
    "DPA_SIDECAR",
    "EVIDENCE_DIR_ENV",
    "GUARD_VERSION",
    "SYNTHETIC_SITE_PREFIX",
    "TRANSIENT_DIR_ENV",
    "DpaEvidenceMissing",
    "DpaPermit",
    "EvidenceRegistryError",
    "LoadDecision",
    "TransientWorkspace",
    "authorise_store",
    "check_load",
    "demo_attestation_holds",
    "evidence_dir",
    "legacy_validation_read",
    "load_evidence_registry",
    "repo_root",
    "require_dpa",
    "sha256_file",
    "transient_root",
    "waiver_active",
    "write_dpa_sidecar",
]

GUARD_VERSION: Final = "1.0.0"
EVIDENCE_DIR_ENV: Final = "GRIDQ_EVIDENCE_DIR"
TRANSIENT_DIR_ENV: Final = "GRIDQ_TRANSIENT_DIR"
SYNTHETIC_SITE_PREFIX: Final = "SYNTHETIC-"
DEMO_SITE_ID: Final = "demo_coldstore"
#: Server-chosen ids under which the attested demo dataset is also run (never a visitor's input:
#: the web intake calls `authorise_store` with ``allow_exempt=False``).
DEMO_SITE_IDS: Final = frozenset({DEMO_SITE_ID, "theoretical_upside"})
DPA_SIDECAR: Final = "dpa_evidence.json"
#: The suffix of the sidecar written beside a persisted upload (``hh_<digest>.dpa.json``).
UPLOAD_SIDECAR_SUFFIX: Final = ".dpa.json"

#: The only issuer a customer DPA can come from (spec P1: "Neura is never an issuer").
_DPA_ISSUERS: Final = frozenset({IssuerRole.CUSTOMER})

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: The curated demo is exempt from the store guard only while its files are byte-identical to
#: the ones checked on 2026-11-25: the demo HH series was compared with every customer HH series
#: tracked in git (Pearson r at most 0.82, against a modelled archetype; never equal to, or a
#: scalar multiple of, any customer series; tests/governance/test_dpa_guard.py pins that check
#: against the manifest). Any change to either file ends the exemption until it is re-verified
#: and re-pinned. Re-pinned once, 2026-11-26, at the WP-UW-07 integration merge: IC-12 3.0.0
#: (D103/D104) made the demo `sitemodel.json` state its tariff basis and provenance. The diff was
#: re-verified to add exactly those two members, both the synthetic test basis (VAT excluded,
#: stated by the record; every component ``assumed``); no other byte moved and the HH series is
#: unchanged.
DEMO_ATTESTATION: Final[dict[str, str]] = {
    "examples/demo_site/hh_2025.csv": (
        "sha256:34754124385b83cb0f8f2e482953e65db89c3a19f282e845278d9b842dd47e97"
    ),
    "examples/demo_site/sitemodel.json": (
        "sha256:7f542e6bb77cefe359fa66b48469fc7fa5888a3468d6125daad736ed1cfe565e"
    ),
}


class DpaEvidenceMissing(PermissionError):
    """A customer dataset would be stored or loaded without valid, verified DPA evidence."""


class EvidenceRegistryError(ValueError):
    """The evidence registry holds a file that is not a valid IC-4 evidence object."""


def repo_root() -> Path:
    """The repository root this package was imported from."""
    return _REPO_ROOT


def sha256_file(path: str | Path) -> str:
    """``sha256:<hex>`` of a file's bytes, streamed (never held whole in memory)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


# ---------------------------------------------------------------------------------------------
# The rule itself (spec section 4, verbatim in behaviour)
# ---------------------------------------------------------------------------------------------
def require_dpa(objects: Iterable[EvidenceObject], *, site_id: str, as_of: date) -> EvidenceCheck:
    """The spec's guard. Returns the `EvidenceCheck` when a valid, verified DPA covers the site.

    Raises `DpaEvidenceMissing` for ``ABSENT``, ``EXPIRED``, ``NOT_YET_EFFECTIVE``,
    ``VALIDITY_UNKNOWN``, an unverified object, a synthetic fixture (``assess`` ignores it), a
    record Neura issued itself (``assess`` ignores it) and a DPA issued by anyone but the
    customer.
    """
    candidates = [o for o in objects if o.issuer.role in _DPA_ISSUERS]
    check = assess(
        candidates,
        evidence_type=EvidenceType.DATA_PROCESSING_AGREEMENT,
        scope=SiteScope(kind="site", site_id=site_id),
        as_of=as_of,
    )
    if (
        check.status is not EvidenceStatus.VALID
        or not check.best_is_verified
        or check.best_is_self_issued
    ):
        raise DpaEvidenceMissing(
            f"no valid, verified DPA evidence object for site {site_id} as of {as_of} "
            f"(status {check.status.value}"
            f"{', unverified' if check.matches and not check.best_is_verified else ''}); "
            "customer data is not stored"
        )
    return check


# ---------------------------------------------------------------------------------------------
# The files-first registry (spec P2)
# ---------------------------------------------------------------------------------------------
def evidence_dir() -> Path:
    """The registry directory: ``$GRIDQ_EVIDENCE_DIR``, else ``<repo>/evidence`` (git-ignored)."""
    env = os.environ.get(EVIDENCE_DIR_ENV)
    return Path(env) if env else _REPO_ROOT / "evidence"


def load_evidence_registry(directory: str | Path | None = None) -> tuple[EvidenceObject, ...]:
    """Every evidence object in the registry, sorted by content hash. Empty when there is none.

    Fail closed: a file that is not a valid IC-4 object raises `EvidenceRegistryError` rather than
    being skipped, because a registry nobody can read must not be mistaken for an empty one.
    """
    base = Path(directory) if directory is not None else evidence_dir()
    if not base.is_dir():
        return ()
    objects: list[EvidenceObject] = []
    for path in sorted(base.glob("*.json")):
        try:
            objects.append(EvidenceObject.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise EvidenceRegistryError(
                f"evidence registry file {path.name} is not a valid IC-4 evidence object: "
                f"{type(exc).__name__}"
            ) from exc
    objects.sort(key=lambda o: o.content_hash())
    return tuple(objects)


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DpaPermit:
    """Permission to store one site's data, and the record of why.

    ``basis`` is ``dpa`` (with the `EvidenceCheck` and the DPA document hashes), ``demo`` (the
    attested curated demo) or ``synthetic`` (a ``SYNTHETIC-`` site id, never a customer).
    """

    site_id: str
    basis: Literal["dpa", "demo", "synthetic"]
    as_of: date
    check: EvidenceCheck | None = None
    document_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.basis == "dpa":
            check = self.check
            if (
                check is None
                or check.status is not EvidenceStatus.VALID
                or not check.best_is_verified
                or check.best_is_self_issued
                or check.evidence_type is not EvidenceType.DATA_PROCESSING_AGREEMENT
                or not isinstance(check.scope, SiteScope)
                or check.scope.site_id != self.site_id
                or not self.document_hashes
            ):
                raise DpaEvidenceMissing(
                    f"a DPA permit for site {self.site_id} needs a VALID, verified, third-party "
                    "DPA check scoped to that site"
                )
        elif self.basis == "demo":
            if self.site_id not in DEMO_SITE_IDS or not demo_attestation_holds():
                raise DpaEvidenceMissing(
                    f"site {self.site_id} is not the attested curated demo; it cannot be stored "
                    "as the demo"
                )
        elif self.basis == "synthetic":
            if not self.site_id.startswith(SYNTHETIC_SITE_PREFIX):
                raise DpaEvidenceMissing(
                    f"site {self.site_id} is not a {SYNTHETIC_SITE_PREFIX} site id"
                )
        else:  # pragma: no cover - Literal guards this statically
            raise DpaEvidenceMissing(f"unknown permit basis {self.basis!r}")

    def covers(self, site_id: str) -> bool:
        """True when this permit was issued for ``site_id``."""
        return self.site_id == site_id

    def sidecar(self) -> dict[str, object]:
        """The ``dpa_evidence.json`` body: what the data was stored under. Names no person."""
        return {
            "guard_version": GUARD_VERSION,
            "work_package": "WP-GRC-05",
            "site_id": self.site_id,
            "basis": self.basis,
            "as_of": self.as_of.isoformat(),
            "dpa_document_hashes": list(self.document_hashes),
            "evidence_check": (
                None if self.check is None else json.loads(self.check.model_dump_json())
            ),
        }


def demo_attestation_holds(root: Path | None = None) -> bool:
    """True when the curated demo files are byte-identical to the attested ones."""
    base = root if root is not None else _REPO_ROOT
    for rel, expected in DEMO_ATTESTATION.items():
        path = base / rel
        if not path.is_file() or _cached_sha256(path) != expected:
            return False
    return True


_HASH_CACHE: dict[Path, tuple[int, int, str]] = {}


def _cached_sha256(path: Path) -> str:
    """`sha256_file`, cached on (size, mtime): the demo is re-attested on every run-store read."""
    stat = path.stat()
    cached = _HASH_CACHE.get(path)
    if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
        return cached[2]
    digest = sha256_file(path)
    _HASH_CACHE[path] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def authorise_store(
    site_id: str,
    *,
    as_of: date | None = None,
    objects: Iterable[EvidenceObject] | None = None,
    allow_exempt: bool = True,
) -> DpaPermit:
    """Permission to store ``site_id``'s data as of ``as_of``, or `DpaEvidenceMissing`.

    ``allow_exempt=False`` is for paths where the site id comes from a visitor (the web form): a
    visitor typing ``SYNTHETIC-x`` or ``demo_coldstore`` must not reach an exemption. The waiver
    never applies here: `legacy_validation_read` is read-only.
    """
    when = as_of if as_of is not None else date.today()
    if allow_exempt:
        if site_id in DEMO_SITE_IDS and demo_attestation_holds():
            return DpaPermit(site_id=site_id, basis="demo", as_of=when)
        if site_id.startswith(SYNTHETIC_SITE_PREFIX):
            return DpaPermit(site_id=site_id, basis="synthetic", as_of=when)
    registry = tuple(objects) if objects is not None else load_evidence_registry()
    check = require_dpa(registry, site_id=site_id, as_of=when)
    valid = {h for h, s in check.matches if s is EvidenceStatus.VALID}
    documents = tuple(
        sorted(
            {
                o.document_hash
                for o in registry
                if o.content_hash() in valid
                and o.is_verified
                and o.evidence_type is EvidenceType.DATA_PROCESSING_AGREEMENT
            }
        )
    )
    return DpaPermit(
        site_id=site_id, basis="dpa", as_of=when, check=check, document_hashes=documents
    )


def write_dpa_sidecar(path: str | Path, permit: DpaPermit) -> Path:
    """Write ``permit``'s record to ``path`` (a file path), returning it."""
    target = Path(path)
    target.write_text(json.dumps(permit.sidecar(), indent=2, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------------------------
# The transient workspace: processed, never stored
# ---------------------------------------------------------------------------------------------
#: Per-process reference counts and the one locked descriptor this process keeps per held file.
_HELD: dict[Path, int] = {}
_FDS: dict[Path, int] = {}
_HELD_LOCK = threading.Lock()
#: A leftover from a crashed process older than this, held by nobody, is swept away.
_STALE_SECONDS: Final = 10 * 60

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX: in-process reference counts only
    _fcntl = None  # type: ignore[assignment]


def transient_root() -> Path:
    """Where in-flight customer bytes live: ``$GRIDQ_TRANSIENT_DIR``, else a per-user temp dir.

    Never under the run store, so "nothing is written under ``runs/``" holds for a show-once run.
    """
    env = os.environ.get(TRANSIENT_DIR_ENV)
    if env:
        return Path(env)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"gridq-transient-{uid}"


def _publish_and_lock(target: Path, *, staged: Path | None, data: bytes | None) -> int:
    """Make ``target`` exist (create-if-absent, never overwrite) and hold a shared lock on it.

    Never rewrites an existing file, because another process may be reading it: the name is a
    content digest, so an existing file already holds these bytes. The shared lock (POSIX
    ``flock``) is what lets the last holder in ANY process, not just this one, delete it.
    """
    for _ in range(50):
        if not target.exists():
            source = staged
            scratch: Path | None = None
            if source is None:
                scratch = target.with_name(
                    f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                scratch.write_bytes(data or b"")
                source = scratch
            try:
                os.link(source, target)
            except FileExistsError:
                pass
            finally:
                if scratch is not None:
                    scratch.unlink(missing_ok=True)
        try:
            fd = os.open(target, os.O_RDONLY)
        except FileNotFoundError:
            continue
        if _fcntl is None:  # pragma: no cover - non-POSIX
            return fd
        _fcntl.flock(fd, _fcntl.LOCK_SH)
        try:
            same = os.stat(target).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if same:
            return fd
        os.close(fd)  # deleted by its last holder between our open and our lock: start again
    raise OSError(f"could not hold the transient file {target.name}")


def _release(path: Path, fd: int) -> None:
    """Drop this process's hold; delete the file when no other process holds it either."""
    try:
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                return  # another process still holds it; its last holder deletes it
        try:
            if os.stat(path).st_ino == os.fstat(fd).st_ino:
                path.unlink()
        except FileNotFoundError:
            pass
    finally:
        os.close(fd)


def _sweep_stale(root: Path) -> None:
    """Remove leftovers of a crashed run: older than `_STALE_SECONDS` and held by no process."""
    if _fcntl is None:  # pragma: no cover - non-POSIX
        return
    cutoff = time.time() - _STALE_SECONDS
    for child in root.iterdir():
        try:
            if child.stat().st_mtime > cutoff:
                continue
            if child.is_dir():
                if child.name.startswith(".staging-"):
                    shutil.rmtree(child, ignore_errors=True)
                continue
            fd = os.open(child, os.O_RDONLY)
        except OSError:
            continue
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            child.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            os.close(fd)


class TransientWorkspace:
    """A guard-owned place for the bytes of one run, deleted when the run ends.

    Files are content-addressed (the writers already name them by digest), so two identical
    submissions get the same path and the same deterministic ``quote_id``. A held file carries a
    shared lock and a process-wide reference count, so concurrent identical runs, in this process
    or another worker, never delete each other's input: the last holder deletes it. A generator
    writes into `root` (this workspace's private staging directory) and `adopt` publishes the
    result. Loads from the transient directory are allowed only while a workspace holds the file.
    """

    def __init__(self) -> None:
        self._held: list[Path] = []
        self._closed = False
        self.shared = transient_root()
        self.shared.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.shared, 0o700)
        except OSError:  # pragma: no cover - best effort on exotic filesystems
            pass
        _sweep_stale(self.shared)
        #: Private staging directory for generators; removed on `close`.
        self.root = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.shared))

    def _hold(self, target: Path, *, staged: Path | None = None, data: bytes | None = None) -> Path:
        if self._closed:
            raise ValueError("this transient workspace is closed")
        resolved = target.resolve()
        with _HELD_LOCK:
            count = _HELD.get(resolved, 0)
            if count == 0:
                _FDS[resolved] = _publish_and_lock(resolved, staged=staged, data=data)
            _HELD[resolved] = count + 1
        self._held.append(resolved)
        return resolved

    @staticmethod
    def _safe_name(name: str) -> str:
        if Path(name).name != name or name.startswith("."):
            raise ValueError(f"unsafe transient file name: {name!r}")
        return name

    def write_bytes(self, name: str, data: bytes) -> Path:
        """Put ``data`` in the transient directory as ``name`` (a plain file name); hold it."""
        return self._hold(self.shared / self._safe_name(name), data=data)

    def adopt(self, path: str | Path) -> Path:
        """Publish a file a generator wrote into `root` and hold it. Returns the held path."""
        staged = Path(path).resolve()
        if staged.parent != self.root.resolve():
            raise DpaEvidenceMissing(f"{staged.name} was not written in this transient workspace")
        return self._hold(self.shared / self._safe_name(staged.name), staged=staged)

    def close(self) -> None:
        """Release every held file; delete each one no open workspace anywhere still holds."""
        if self._closed:
            return
        self._closed = True
        with _HELD_LOCK:
            for path in self._held:
                count = _HELD.get(path, 0) - 1
                if count > 0:
                    _HELD[path] = count
                    continue
                _HELD.pop(path, None)
                fd = _FDS.pop(path, None)
                if fd is not None:
                    _release(path, fd)
        self._held.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> TransientWorkspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _transient_held(path: Path) -> bool:
    with _HELD_LOCK:
        return _HELD.get(path, 0) > 0


# ---------------------------------------------------------------------------------------------
# The read-only legacy validation waiver (Fable 5.2 ruling Q3)
# ---------------------------------------------------------------------------------------------
_WAIVER_STACK: list[str] = []
_WAIVER_LOCK = threading.Lock()
_CONFTEST: Final = (_REPO_ROOT / "tests" / "conftest.py").resolve()


class legacy_validation_read:  # noqa: N801 - used as a context manager, named like one
    """Allow reads (never stores) of quarantined customer data for one listed test module.

    Opened only from ``tests/conftest.py`` (checked against the caller's file), only for a module in
    the manifest's ``legacy_validation_waiver.modules``. Every load it lets through is logged with
    the basis ``quarantined_legacy_unevidenced``.
    """

    def __init__(self, module: str) -> None:
        caller = Path(sys._getframe(1).f_code.co_filename).resolve()
        if caller != _CONFTEST:
            raise DpaEvidenceMissing(
                "the legacy validation read waiver can only be opened by tests/conftest.py"
            )
        from gridq.governance.customer_data import load_manifest

        if module not in load_manifest().waiver_modules:
            raise DpaEvidenceMissing(
                f"{module} is not on the legacy validation waiver list "
                "(src/gridq/governance/customer_data_manifest.json)"
            )
        self.module = module

    def __enter__(self) -> legacy_validation_read:
        with _WAIVER_LOCK:
            _WAIVER_STACK.append(self.module)
        return self

    def __exit__(self, *exc: object) -> None:
        with _WAIVER_LOCK:
            if self.module in _WAIVER_STACK:
                _WAIVER_STACK.remove(self.module)


def waiver_active() -> str | None:
    """The module the read waiver is open for, or ``None``."""
    with _WAIVER_LOCK:
        return _WAIVER_STACK[-1] if _WAIVER_STACK else None


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------
LoadBasis = Literal[
    "not_customer_data", "dpa", "transient_unstored", "quarantined_legacy_unevidenced"
]


@dataclass(frozen=True)
class LoadDecision:
    """Why a load was allowed. ``basis`` is recorded on the access log entry."""

    basis: LoadBasis
    customer: str | None = None
    site_ids: tuple[str, ...] = ()
    dataset: str | None = None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _run_store_dir() -> Path:
    # Read lazily (tests re-point it per module); mirrors gridq.app.store.run_store_dir without
    # importing the web app into the ingestion layer.
    env = os.environ.get("GRIDQ_RUN_STORE")
    return Path(env) if env else _REPO_ROOT / "runs"


def _sites_released(
    site_ids: Iterable[str], as_of: date, registry: tuple[EvidenceObject, ...]
) -> tuple[bool, str]:
    """(True, "") when every site has a valid verified DPA; else (False, the first refusal)."""
    for site in site_ids:
        try:
            require_dpa(registry, site_id=site, as_of=as_of)
        except DpaEvidenceMissing as exc:
            return False, str(exc)
    return True, ""


def _run_store_decision(path: Path, store: Path, as_of: date) -> LoadDecision:
    """A file in the run store is loadable only under the permit recorded beside it."""
    sidecar = path.with_name(path.name + UPLOAD_SIDECAR_SUFFIX)
    if not sidecar.is_file():
        sidecar = path.parent / DPA_SIDECAR
    if not sidecar.is_file():
        raise DpaEvidenceMissing(
            f"{path.name} is in the run store with no DPA record beside it; it was stored before "
            "the guard existed or outside it, and is not loaded"
        )
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        site_id = str(record["site_id"])
        basis = str(record["basis"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DpaEvidenceMissing(f"the DPA record beside {path.name} is unreadable") from exc
    if basis in ("demo", "synthetic"):
        authorise_store(site_id, as_of=as_of)  # re-checks the exemption still holds
        return LoadDecision(basis="not_customer_data", site_ids=(site_id,), dataset=path.name)
    require_dpa(load_evidence_registry(), site_id=site_id, as_of=as_of)
    return LoadDecision(basis="dpa", site_ids=(site_id,), dataset=path.name)


def check_load(
    path: str | Path,
    *,
    site_id: str | None = None,
    as_of: date | None = None,
) -> LoadDecision:
    """Decide whether ``path`` may be read. Raises `DpaEvidenceMissing` when it may not.

    Called before the file is opened for parsing. ``site_id`` is the caller's attribution only: a
    listed dataset is released by the sites the manifest maps it to, never by the caller's claim.
    """
    from gridq.governance.customer_data import load_manifest

    when = as_of if as_of is not None else date.today()
    resolved = Path(path).resolve()

    transient = transient_root().resolve()
    if _is_within(resolved, transient):
        if _transient_held(resolved):
            return LoadDecision(basis="transient_unstored", site_ids=_opt(site_id))
        raise DpaEvidenceMissing(
            f"{resolved.name} is in the transient workspace but no open run holds it; transient "
            "customer bytes are never read after their run ends"
        )

    store = _run_store_dir().resolve()
    if _is_within(resolved, store):
        return _run_store_decision(resolved, store, when)

    manifest = load_manifest()
    entry = manifest.match(resolved)
    if entry is None and resolved.is_file() and manifest.has_any_hashes():
        entry = manifest.match_hash(sha256_file(resolved))
    if entry is not None:
        released, why = _sites_released(entry.site_ids, when, load_evidence_registry())
        if released:
            return LoadDecision(
                basis="dpa", customer=entry.customer, site_ids=entry.site_ids, dataset=entry.path
            )
        if waiver_active() is not None:
            return LoadDecision(
                basis="quarantined_legacy_unevidenced",
                customer=entry.customer,
                site_ids=entry.site_ids,
                dataset=entry.path,
            )
        raise DpaEvidenceMissing(
            f"{entry.path} is quarantined customer data ({entry.customer}): {why}. It is not "
            "loaded. Register the customer's executed DPA (one IC-4 object per site) or remove "
            "the file; the owner decides (gridq_dpa_guard_spec.md section 6.2)"
        )

    if (
        resolved.exists()
        and manifest.under_customer_root(resolved)
        and not manifest.is_not_customer_data(resolved)
    ):
        raise DpaEvidenceMissing(
            f"{resolved.name} is under a customer-data root but not in the manifest; unlisted "
            "customer data is never loaded (run python -m gridq.governance.dpa_scan)"
        )
    return LoadDecision(basis="not_customer_data", site_ids=_opt(site_id))


def _opt(site_id: str | None) -> tuple[str, ...]:
    return (site_id,) if site_id else ()


def iter_waiver_modules() -> Iterator[str]:
    """The test modules the read waiver may open for (from the committed manifest)."""
    from gridq.governance.customer_data import load_manifest

    yield from load_manifest().waiver_modules
