"""Per-run persistence for the Wave-5 UI.

A run is written once (on submit) under a **gitignored** store keyed by the deterministic
``quote_id``, then reloaded for the navigable screens (Inspect → System spec → Memo & proposal →
Telemetry). Because ``quote_id`` is a pure function of the inputs + assumption/code version, writing
is idempotent and re-submitting the same site overwrites identically — the determinism smoke test
asserts is preserved.

Privacy (wave5_scope §5): a user's own submission is theirs to process; we persist it only to let
them navigate/revisit. The store lives at ``$GRIDQ_RUN_STORE`` (default ``<repo>/runs``), which is
**gitignored** — real third-party data is never committed or publicly indexed. ``delete_run`` is the
deletion path. The site model is stored alongside the quote because the counterparty / consents /
physical-gate sections and the memo/proposal need it (the frozen `QuoteRecord` carries only
``site_id``; D14 / C018).

DPA guard (WP-GRC-05, acceptance criterion 3): a stored run is customer data. `save_run` takes a
required `DpaPermit` (`gridq.governance.authorise_store`) and writes it beside the run as
``dpa_evidence.json``; `load_run` re-checks that record and treats a run without one (stored
before the guard, or outside it) or whose DPA no longer holds exactly like a missing run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from gridq.governance.dpa_guard import (
    DPA_SIDECAR,
    DpaEvidenceMissing,
    DpaPermit,
    check_load,
    write_dpa_sidecar,
)
from gridq.schemas import QuoteRecord, SiteModel, TelemetryEvent

_REPO_ROOT = Path(__file__).resolve().parents[3]  # src/gridq/app/store.py -> repo root
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class StoredRun:
    """A reloaded run: the stamped quote, its site context, telemetry, and its owner.

    ``owner_id`` is the accounts scoping key (``google:<sub>`` or ``anon:<workspace>`` — see
    ``gridq.app.auth``); ``None`` marks a legacy/shared run saved before ownership existed,
    which stays visible to everyone (deliberate: hiding previously-visible runs behind a new
    feature would read as data loss on a shared box).
    """

    quote: QuoteRecord
    site: SiteModel | None
    telemetry: TelemetryEvent | None
    owner_id: str | None = None


def run_store_dir() -> Path:
    """The gitignored directory runs are persisted under (env-overridable for tests/installs)."""
    env = os.environ.get("GRIDQ_RUN_STORE")
    return Path(env) if env else _REPO_ROOT / "runs"


def _run_dir(quote_id: str) -> Path:
    """Resolve a per-quote directory, rejecting anything that is not a plain id (no traversal)."""
    if not _SAFE_ID.match(quote_id):
        raise ValueError(f"unsafe run id: {quote_id!r}")
    return run_store_dir() / quote_id


def save_run(
    quote: QuoteRecord,
    site: SiteModel | None,
    telemetry: TelemetryEvent | None,
    *,
    dpa: DpaPermit,
    owner_id: str | None = None,
) -> str:
    """Persist a run under its ``quote_id`` and return that id (idempotent; deterministic id).

    ``dpa`` is required: nothing is written unless it is a `DpaPermit` for this run's site
    (`DpaEvidenceMissing` otherwise, before any directory is created). Its record is written as
    ``dpa_evidence.json`` beside the run.

    ``owner_id`` (accounts scoping) is stored as a small sidecar ``owner.json`` — never inside
    the stamped `QuoteRecord` (identity is presentation-layer state, not pricing input; the
    canonical record and its hashes stay byte-identical whoever runs it). Because the id is a
    pure function of the inputs, two visitors submitting IDENTICAL inputs (e.g. the shipped
    real-prospect example) produce one record: ownership is deliberately last-writer-wins so
    the most recent submitter can always see their result (both already hold the identical
    inputs, so nothing confidential moves between them). An unowned save (the shared demo)
    never touches an existing sidecar.
    """
    if not isinstance(dpa, DpaPermit) or not dpa.covers(quote.site_id):
        raise DpaEvidenceMissing(
            f"run {quote.quote_id} for site {quote.site_id} has no DPA permit for that site; "
            "it is not stored"
        )
    out = _run_dir(quote.quote_id)
    out.mkdir(parents=True, exist_ok=True)
    write_dpa_sidecar(out / DPA_SIDECAR, dpa)
    (out / "quote.json").write_text(quote.model_dump_json(indent=2), encoding="utf-8")
    if site is not None:
        (out / "site.json").write_text(site.model_dump_json(indent=2), encoding="utf-8")
    if telemetry is not None:
        (out / "telemetry.json").write_text(telemetry.model_dump_json(indent=2), encoding="utf-8")
    if owner_id:
        (out / "owner.json").write_text(
            json.dumps({"owner_id": owner_id}, indent=2), encoding="utf-8"
        )
    return quote.quote_id


def _read_owner(run_dir: Path) -> str | None:
    """The run's recorded owner id, or ``None`` for legacy/shared runs (no sidecar)."""
    path = run_dir / "owner.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = data.get("owner_id") if isinstance(data, dict) else None
    return str(owner) if owner else None


def load_run(quote_id: str) -> StoredRun | None:
    """Reload a persisted run by id, or ``None`` if it is not in the store.

    Also ``None`` when the DPA guard refuses the run: no ``dpa_evidence.json`` beside it, or the
    DPA it was stored under is no longer valid (expired, withdrawn from the registry).
    """
    run_dir = _run_dir(quote_id)
    quote_path = run_dir / "quote.json"
    if not quote_path.is_file():
        return None
    try:
        check_load(quote_path)
    except DpaEvidenceMissing:
        return None
    quote = QuoteRecord.model_validate_json(quote_path.read_text(encoding="utf-8"))
    site = None
    site_path = run_dir / "site.json"
    if site_path.is_file():
        site = SiteModel.model_validate_json(site_path.read_text(encoding="utf-8"))
    telemetry = None
    tel_path = run_dir / "telemetry.json"
    if tel_path.is_file():
        telemetry = TelemetryEvent.model_validate_json(tel_path.read_text(encoding="utf-8"))
    return StoredRun(quote=quote, site=site, telemetry=telemetry, owner_id=_read_owner(run_dir))


def list_runs(*, visible_to_id: str | None = None) -> list[StoredRun]:
    """Persisted runs, newest first by directory mtime. Empty when the store has none.

    With ``visible_to_id`` the listing is scoped for accounts: the visitor's own runs plus
    legacy/shared runs with no recorded owner (see ``StoredRun.owner_id``). Without it, every
    run is returned (internal/test use). Skips any directory that no longer parses (a partially
    written / hand-removed run) rather than raising, so the listing screens never 500.
    """
    base = run_store_dir()
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / "quote.json").is_file()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    runs: list[StoredRun] = []
    for d in dirs:
        try:
            loaded = load_run(d.name)
        except (ValueError, OSError):
            continue
        if loaded is None:
            continue
        if visible_to_id is not None and not (
            loaded.owner_id is None or loaded.owner_id == visible_to_id
        ):
            continue
        runs.append(loaded)
    return runs


def delete_run(quote_id: str) -> bool:
    """Delete a persisted run (the privacy deletion path). True if anything was removed."""
    run_dir = _run_dir(quote_id)
    if not run_dir.is_dir():
        return False
    for child in run_dir.iterdir():
        child.unlink()
    run_dir.rmdir()
    return True
