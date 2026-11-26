"""Test-only DPA evidence for fictitious sites (WP-GRC-05, Fable 5.2 ruling Q4).

Tests that legitimately store a run for a fixture site pass through the real guard: this factory
writes a valid, verified, ``registered`` IC-4 data_processing_agreement for a FICTITIOUS site id
into a ``tmp_path`` registry and points ``GRIDQ_EVIDENCE_DIR`` at it. It refuses any site id or
customer the customer-data manifest knows (a real customer's DPA is a person-only item and is
never fabricated), labels the issuer "TEST FIXTURE", and writes nothing into the repository.

`dpa_released` and `dpa_released_site` are the other half: the only way a test module outside the
legacy validation waiver reaches a quarantined customer file. They ask the guard first and skip the
test, naming its blocker, while no DPA is registered; once the customer's DPA is registered the
same test runs by itself. ``tests/governance/test_no_direct_quarantined_reads.py`` fails any test
module outside the waiver that opens a path under ``examples/real/`` any other way.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gridq.contracts.evidence import EvidenceObject, serialise_evidence
from gridq.governance.customer_data import CUSTOMERS, load_manifest
from gridq.governance.dpa_guard import (
    EVIDENCE_DIR_ENV,
    DpaEvidenceMissing,
    LoadDecision,
    check_load,
)
from gridq.ingestion.access_log import record_hh_access
from gridq.ingestion.loaders import load_site_model_json

if TYPE_CHECKING:
    from gridq.app.store import StoredRun
    from gridq.schemas.site import SiteModel

#: Every site id the manifest maps to a real customer, and the customers' path tokens.
_REAL_SITE_IDS = frozenset(s for c in CUSTOMERS.values() for s in c["site_ids"])
_REAL_TOKENS = tuple(t for c in CUSTOMERS.values() for t in c["tokens"])


def _refuse_real(site_id: str) -> None:
    lowered = site_id.lower()
    if site_id in _REAL_SITE_IDS or any(t in lowered for t in _REAL_TOKENS):
        raise ValueError(
            f"{site_id} is a real customer site; a test may never fabricate its DPA evidence"
        )


def fixture_dpa(
    site_id: str,
    *,
    effective_from: str = "2026-01-01",
    expires_on: str | None = None,
    verified: bool = True,
    issuer_role: str = "customer",
    record_class: str = "registered",
    evidence_type: str = "data_processing_agreement",
    validity_unknown: bool = False,
    scope_site_id: str | None = None,
) -> EvidenceObject:
    """A TEST FIXTURE DPA evidence object for a fictitious ``site_id``.

    The defaults give the one shape the guard accepts; each keyword breaks exactly one property.
    """
    _refuse_real(site_id)
    if scope_site_id is not None:
        _refuse_real(scope_site_id)
    payload: dict[str, object] = {
        "contract": "IC-4",
        "contract_version": "1.1.0",
        "record_class": record_class,
        "evidence_type": evidence_type,
        "title": f"TEST FIXTURE data processing agreement for fictitious site {site_id}",
        "issuer": {
            "name": f"TEST FIXTURE customer of {site_id} (not a real company)",
            "role": issuer_role,
            "reference": f"TEST-FIXTURE-DPA-{site_id}",
        },
        "document_hash": "sha256:"
        + hashlib.sha256(f"TEST FIXTURE DPA document for {site_id}".encode()).hexdigest(),
        "issued_on": effective_from,
        "effective_from": effective_from,
        "expiry": (
            {"kind": "unknown", "reason": "TEST FIXTURE: term not stated"}
            if validity_unknown
            else {"kind": "expires_on", "expires_on": expires_on}
            if expires_on
            else {"kind": "no_expiry", "reason": "TEST FIXTURE: runs until terminated"}
        ),
        "scope": {"kind": "site", "site_id": scope_site_id or site_id},
        "verification": (
            {
                "kind": "verified",
                "verifier": "TEST FIXTURE verifier",
                "verifier_role": "neura_internal",
                "method": "document_inspection",
                "verified_on": effective_from,
            }
            if verified
            else {"kind": "unverified", "reason": "TEST FIXTURE: not yet checked"}
        ),
    }
    return EvidenceObject.model_validate_json(json.dumps(payload))


def write_registry(directory: Path, *objects: EvidenceObject) -> Path:
    """Write ``objects`` into ``directory`` as a files-first registry; return the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for obj in objects:
        (directory / f"{obj.content_hash().split(':', 1)[1][:16]}.json").write_text(
            serialise_evidence(obj), encoding="utf-8"
        )
    return directory


def register_fixture_dpas(monkeypatch: pytest.MonkeyPatch, directory: Path, *site_ids: str) -> Path:
    """Register a TEST FIXTURE DPA for each fictitious site and point the guard at the registry."""
    write_registry(directory, *(fixture_dpa(s) for s in site_ids))
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(directory))
    return directory


def shown_once(quote_id: str) -> StoredRun:
    """A web run without a DPA (owner decision (a)): held for its visitor, never stored.

    Asserts the run is NOT in the run store and returns the in-memory result the run screens
    serve, so a test that used to read the stored record reads the same record from here.
    """
    from gridq.app import ephemeral, store

    assert store.load_run(quote_id) is None, f"{quote_id} was stored without a DPA"
    held = ephemeral.get(quote_id)
    assert held is not None, f"{quote_id} is neither stored nor held"
    return held


#: The skip reason of a test that needs a quarantined customer file while no DPA is registered.
QUARANTINE_SKIP = (
    "quarantined: no DPA registered for {customer} (WP-GRC-05); runs once the DPA is registered"
)


def _blocker(target: Path) -> str | None:
    """The customer whose missing DPA keeps ``target`` quarantined, or None if it is released."""
    try:
        check_load(target)
    except DpaEvidenceMissing:
        entry = load_manifest().match(target.resolve())
        return entry.customer if entry is not None else f"unlisted customer file {target.name}"
    return None


def _released_or_skip(target: Path) -> LoadDecision:
    try:
        return check_load(target)
    except DpaEvidenceMissing:
        pytest.skip(QUARANTINE_SKIP.format(customer=_blocker(target)))


def dpa_released(path: str | Path) -> Path:
    """``path``, once the WP-GRC-05 guard releases it; until then, skip the calling test.

    The guard's own DPA check decides (`check_load`), before a byte of the file is read. A file the
    manifest lists is released only when every site it maps to has a valid, verified DPA, so the
    test waits for the owner to register the customer's DPA and then runs by itself. This never
    opens the read waiver, and never fabricates evidence for a real customer. A released
    customer-data read is written to the access log, as the guarded loaders write theirs.
    """
    target = Path(path)
    decision = _released_or_skip(target)
    if decision.basis != "not_customer_data":
        record_hh_access(str(target), action="test_read", dpa_basis=decision.basis)
    return target


def dpa_released_site(path: str | Path, *, run_cross_checks: bool = True) -> SiteModel:
    """The SiteModel at ``path`` through the guarded loader, or a skip naming its blocker."""
    target = Path(path)
    _released_or_skip(target)
    return load_site_model_json(target, run_cross_checks=run_cross_checks)


def dpa_released_sites(paths: Iterable[Path], *, run_cross_checks: bool = True) -> list[SiteModel]:
    """Every site at ``paths``, or one skip naming every customer whose DPA is still missing.

    For a test over a whole set: nothing is read until every file is released, so the test checks
    the full set or skips as a whole; it can never check a subset and pass.
    """
    targets = [Path(p) for p in paths]
    blocked = sorted({b for b in map(_blocker, targets) if b is not None})
    if blocked:
        pytest.skip(QUARANTINE_SKIP.format(customer=", ".join(blocked)))
    return [load_site_model_json(t, run_cross_checks=run_cross_checks) for t in targets]


def campaign_site_records() -> list[Path]:
    """Every campaign site record (``examples/real/sites/*.json``), from the committed manifest.

    Enumerated from the manifest, never by listing the quarantined directory, so a test can
    parametrize over the records and let each one wait on its own DPA through `dpa_released_site`.
    """
    root = Path(__file__).resolve().parents[1]
    return sorted(
        root / e.path for e in load_manifest().datasets if e.path.startswith("examples/real/sites/")
    )
