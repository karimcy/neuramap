"""The DPA guard (WP-GRC-05, acceptance criterion 3): the rule, the permit, the registry, the loads.

Every DPA in this module is a TEST FIXTURE for a fictitious site (`tests/_dpa_support.py`), written
to ``tmp_path`` and never committed. No test reads a customer file: a quarantined path is refused
before it is opened, and the tests that need a manifest build a fictitious one in ``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests._dpa_support import fixture_dpa, register_fixture_dpas, write_registry

from gridq.contracts.evidence import EvidenceStatus
from gridq.governance import customer_data, dpa_guard
from gridq.governance.customer_data import load_manifest
from gridq.governance.dpa_guard import (
    DEMO_SITE_ID,
    EVIDENCE_DIR_ENV,
    DpaEvidenceMissing,
    DpaPermit,
    EvidenceRegistryError,
    TransientWorkspace,
    authorise_store,
    check_load,
    demo_attestation_holds,
    legacy_validation_read,
    load_evidence_registry,
    require_dpa,
)
from gridq.schemas import DataTier, QuoteMode, QuoteRecord

AS_OF = date(2026, 11, 26)
SITE = "FIXTURE-SITE-ALPHA"


# ---------------------------------------------------------------------------------------------
# The rule (spec section 4)
# ---------------------------------------------------------------------------------------------
def test_a_valid_verified_customer_dpa_for_the_site_passes() -> None:
    check = require_dpa([fixture_dpa(SITE)], site_id=SITE, as_of=AS_OF)
    assert check.status is EvidenceStatus.VALID
    assert check.best_is_verified and not check.best_is_self_issued


@pytest.mark.parametrize(
    ("objects", "why"),
    [
        ([], "absent"),
        ([fixture_dpa(SITE, expires_on="2026-06-30")], "expired"),
        ([fixture_dpa(SITE, effective_from="2027-01-01")], "not_yet_effective"),
        ([fixture_dpa(SITE, validity_unknown=True)], "validity_unknown"),
        ([fixture_dpa(SITE, verified=False)], "unverified"),
        ([fixture_dpa(SITE, record_class="synthetic_fixture")], "synthetic fixture"),
        ([fixture_dpa(SITE, issuer_role="supplier")], "issued by the supplier"),
        ([fixture_dpa(SITE, issuer_role="dno")], "issued by the DNO"),
        ([fixture_dpa(SITE, scope_site_id="FIXTURE-SITE-BETA")], "another site"),
        ([fixture_dpa(SITE, evidence_type="data_sharing_agreement")], "not a DPA"),
        ([fixture_dpa(SITE, evidence_type="customer_consent")], "consent is not a DPA"),
    ],
)
def test_everything_short_of_a_valid_verified_customer_dpa_is_refused(
    objects: list[object], why: str
) -> None:
    with pytest.raises(DpaEvidenceMissing):
        require_dpa(objects, site_id=SITE, as_of=AS_OF)


def test_neura_cannot_self_issue_a_dpa_at_all() -> None:
    # IC-4 1.1.0 refuses the object itself; the guard's issuer filter is the second wall.
    with pytest.raises(ValidationError, match="self-issue"):
        fixture_dpa(SITE, issuer_role="neura_self_issued")


def test_an_expired_dpa_alongside_a_valid_one_still_passes_on_the_valid_one() -> None:
    objects = [fixture_dpa(SITE, expires_on="2026-01-31"), fixture_dpa(SITE)]
    assert require_dpa(objects, site_id=SITE, as_of=AS_OF).status is EvidenceStatus.VALID


# ---------------------------------------------------------------------------------------------
# The permit and the store exemptions
# ---------------------------------------------------------------------------------------------
def test_authorise_store_records_the_dpa_document_hash() -> None:
    dpa = fixture_dpa(SITE)
    permit = authorise_store(SITE, as_of=AS_OF, objects=[dpa])
    assert permit.basis == "dpa"
    assert permit.document_hashes == (dpa.document_hash,)
    body = permit.sidecar()
    assert body["basis"] == "dpa" and body["site_id"] == SITE
    assert body["dpa_document_hashes"] == [dpa.document_hash]
    assert body["evidence_check"]["status"] == "valid"


def test_a_permit_cannot_be_built_by_hand_without_a_valid_check() -> None:
    with pytest.raises(DpaEvidenceMissing):
        DpaPermit(site_id=SITE, basis="dpa", as_of=AS_OF)
    other = require_dpa(
        [fixture_dpa("FIXTURE-SITE-BETA")], site_id="FIXTURE-SITE-BETA", as_of=AS_OF
    )
    with pytest.raises(DpaEvidenceMissing):
        DpaPermit(site_id=SITE, basis="dpa", as_of=AS_OF, check=other, document_hashes=("x",))
    with pytest.raises(DpaEvidenceMissing):
        DpaPermit(site_id="grundon_ewelme", basis="demo", as_of=AS_OF)
    with pytest.raises(DpaEvidenceMissing):
        DpaPermit(site_id="grundon_ewelme", basis="synthetic", as_of=AS_OF)


def test_only_the_attested_demo_and_synthetic_ids_are_exempt() -> None:
    assert demo_attestation_holds()
    assert authorise_store(DEMO_SITE_ID, objects=[]).basis == "demo"
    assert authorise_store("SYNTHETIC-SITE-1", objects=[]).basis == "synthetic"
    with pytest.raises(DpaEvidenceMissing):
        authorise_store("grundon_ewelme", objects=[])


def test_a_visitor_typed_site_id_never_reaches_an_exemption() -> None:
    for typed in (DEMO_SITE_ID, "SYNTHETIC-SITE-1"):
        with pytest.raises(DpaEvidenceMissing):
            authorise_store(typed, objects=[], allow_exempt=False)


def test_the_demo_exemption_ends_when_a_demo_file_changes(tmp_path: Path) -> None:
    for rel in dpa_guard.DEMO_ATTESTATION:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((dpa_guard.repo_root() / rel).read_bytes())
    assert demo_attestation_holds(tmp_path)
    first = tmp_path / next(iter(dpa_guard.DEMO_ATTESTATION))
    first.write_bytes(first.read_bytes() + b"\n")
    assert not demo_attestation_holds(tmp_path)


# ---------------------------------------------------------------------------------------------
# The files-first registry
# ---------------------------------------------------------------------------------------------
def test_the_registry_reads_what_is_written_and_is_empty_when_absent(tmp_path: Path) -> None:
    assert load_evidence_registry(tmp_path / "missing") == ()
    write_registry(tmp_path, fixture_dpa(SITE), fixture_dpa("FIXTURE-SITE-BETA"))
    assert len(load_evidence_registry(tmp_path)) == 2


def test_an_unreadable_registry_fails_closed(tmp_path: Path) -> None:
    write_registry(tmp_path, fixture_dpa(SITE))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(EvidenceRegistryError):
        load_evidence_registry(tmp_path)


def test_the_registry_location_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_fixture_dpas(monkeypatch, tmp_path, SITE)
    assert authorise_store(SITE).basis == "dpa"
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(tmp_path / "elsewhere"))
    with pytest.raises(DpaEvidenceMissing):
        authorise_store(SITE)


def test_the_factory_never_fabricates_a_real_customers_dpa() -> None:
    for site in ("grundon_ewelme", "harwell_campus_k16w304527", "robert_thomas_haywood_oaks"):
        with pytest.raises(ValueError, match="real customer"):
            fixture_dpa(site)


# ---------------------------------------------------------------------------------------------
# Loads: every quarantined dataset is refused outside the waiver
# ---------------------------------------------------------------------------------------------
def test_every_quarantined_dataset_in_git_is_refused() -> None:
    manifest = load_manifest()
    assert manifest.datasets, "the manifest lists the customer datasets in git"
    for entry in manifest.datasets:
        path = dpa_guard.repo_root() / entry.path
        if not path.exists():
            continue
        with pytest.raises(DpaEvidenceMissing, match="quarantined"):
            check_load(path)


def test_the_hh_loader_refuses_a_quarantined_series_before_reading_it() -> None:
    from gridq.ingestion import load_half_hourly_csv

    entry = next(e for e in load_manifest().datasets if e.kind == "hh_series")
    with pytest.raises(DpaEvidenceMissing):
        load_half_hourly_csv(dpa_guard.repo_root() / entry.path)


def test_the_site_loader_refuses_a_real_site_record() -> None:
    from gridq.ingestion import load_site_model_json

    with pytest.raises(DpaEvidenceMissing):
        load_site_model_json(dpa_guard.repo_root() / "examples/real/sites/ewelme.json")


def test_non_customer_data_loads_and_is_logged_as_such() -> None:
    from gridq.ingestion import load_half_hourly_csv
    from gridq.ingestion.access_log import clear_access_log, get_access_log

    clear_access_log()
    demo = dpa_guard.repo_root() / "examples/demo_site/hh_2025.csv"
    assert check_load(demo).basis == "not_customer_data"
    load_half_hourly_csv(demo)
    assert get_access_log()[-1].dpa_basis == "not_customer_data"


def _fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sites: list[str]) -> Path:
    """A fictitious repository with one listed customer dataset mapped to ``sites``."""
    root = tmp_path / "repo"
    data = root / "customers" / "fixture_series.csv"
    data.parent.mkdir(parents=True)
    data.write_text("timestamp,import_kw\n2026-01-01T00:00,1.0\n", encoding="utf-8")
    manifest = {
        "schema": customer_data.MANIFEST_SCHEMA,
        "customer_roots": ["customers"],
        "not_customer_data": [{"path": "customers/declared_market_data.csv", "reason": "test"}],
        "review_prose": [],
        "legacy_validation_waiver": {"modules": []},
        "datasets": [
            {
                "path": "customers/fixture_series.csv",
                "sha256": dpa_guard.sha256_file(data),
                "customer": "fixture_customer",
                "site_ids": sites,
                "kind": "hh_series",
                "status": "quarantined",
            }
        ],
        "dataset_count": 1,
    }
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(customer_data, "MANIFEST_PATH", target)
    monkeypatch.setattr(customer_data, "repo_root", lambda: root)
    return root


def test_a_dataset_is_released_only_when_every_mapped_site_has_a_dpa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fake_repo(tmp_path, monkeypatch, ["FIXTURE-SITE-ALPHA", "FIXTURE-SITE-BETA"])
    series = root / "customers" / "fixture_series.csv"
    register_fixture_dpas(monkeypatch, tmp_path / "ev1", "FIXTURE-SITE-ALPHA")
    with pytest.raises(DpaEvidenceMissing, match="FIXTURE-SITE-BETA"):
        check_load(series)
    register_fixture_dpas(monkeypatch, tmp_path / "ev2", "FIXTURE-SITE-ALPHA", "FIXTURE-SITE-BETA")
    decision = check_load(series)
    assert decision.basis == "dpa"
    assert decision.site_ids == ("FIXTURE-SITE-ALPHA", "FIXTURE-SITE-BETA")


def test_a_copy_of_a_listed_dataset_anywhere_is_the_same_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fake_repo(tmp_path, monkeypatch, ["FIXTURE-SITE-ALPHA"])
    copy = tmp_path / "somewhere_else" / "renamed.csv"
    copy.parent.mkdir()
    copy.write_bytes((root / "customers" / "fixture_series.csv").read_bytes())
    with pytest.raises(DpaEvidenceMissing, match="quarantined"):
        check_load(copy)


def test_an_unlisted_file_under_a_customer_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fake_repo(tmp_path, monkeypatch, ["FIXTURE-SITE-ALPHA"])
    stray = root / "customers" / "new_upload.csv"
    stray.write_text("timestamp,import_kw\n", encoding="utf-8")
    with pytest.raises(DpaEvidenceMissing, match="not in the manifest"):
        check_load(stray)
    declared = root / "customers" / "declared_market_data.csv"
    declared.write_text("timestamp,price\n", encoding="utf-8")
    assert check_load(declared).basis == "not_customer_data"


def test_the_demo_prefill_is_hidden_without_a_dpa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridq.app.demo import load_haywood_oaks_prefill

    root = _fake_repo(tmp_path, monkeypatch, ["FIXTURE-SITE-ALPHA"])
    prefill = root / "customers" / "prefill.json"
    prefill.write_text(json.dumps({"site_name": "fictitious"}), encoding="utf-8")
    assert load_haywood_oaks_prefill(prefill) is None


# ---------------------------------------------------------------------------------------------
# The read-only waiver
# ---------------------------------------------------------------------------------------------
def test_only_conftest_can_open_the_waiver() -> None:
    with pytest.raises(DpaEvidenceMissing, match="conftest"):
        legacy_validation_read("tests/governance/test_dpa_guard.py")


def test_the_waiver_reads_but_never_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dpa_guard, "_WAIVER_STACK", ["tests/a_listed_module.py"])
    entry = load_manifest().datasets[0]
    decision = check_load(dpa_guard.repo_root() / entry.path)
    assert decision.basis == "quarantined_legacy_unevidenced"
    with pytest.raises(DpaEvidenceMissing):
        authorise_store(entry.site_ids[0], objects=[])


def test_no_environment_variable_or_flag_opens_the_waiver() -> None:
    source = Path(dpa_guard.__file__).read_text(encoding="utf-8")
    import re

    read = set(re.findall(r"os\.environ\.get\(([A-Z_\"]+)\)", source))
    assert read == {"EVIDENCE_DIR_ENV", "TRANSIENT_DIR_ENV", '"GRIDQ_RUN_STORE"'}


# ---------------------------------------------------------------------------------------------
# The transient workspace
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def transient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "transient"
    monkeypatch.setenv(dpa_guard.TRANSIENT_DIR_ENV, str(root))
    return root


def test_transient_files_are_readable_only_while_held_and_deleted_by_the_last_holder(
    transient: Path,
) -> None:
    first, second = TransientWorkspace(), TransientWorkspace()
    a = first.write_bytes("hh_fixture.csv", b"timestamp,import_kw\n")
    b = second.write_bytes("hh_fixture.csv", b"timestamp,import_kw\n")
    assert a == b  # content-addressed: identical submissions share one path (deterministic id)
    assert check_load(a).basis == "transient_unstored"
    first.close()
    assert a.exists() and check_load(a).basis == "transient_unstored"
    second.close()
    assert not a.exists()
    assert [p for p in transient.iterdir()] == []
    with pytest.raises(DpaEvidenceMissing, match="no open run holds it"):
        check_load(a)


def test_a_generator_writes_into_staging_and_adopt_publishes_it(transient: Path) -> None:
    with TransientWorkspace() as workspace:
        staged = workspace.root / "synthetic_fixture.csv"
        staged.write_text("timestamp,import_kw\n", encoding="utf-8")
        with pytest.raises(DpaEvidenceMissing):
            check_load(staged)  # staged, not held
        held = workspace.adopt(staged)
        assert held.parent == transient.resolve()
        assert check_load(held).basis == "transient_unstored"
    assert not held.exists() and not workspace.root.exists()


def test_adopt_refuses_a_file_from_outside_the_workspace(transient: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("x", encoding="utf-8")
    with TransientWorkspace() as workspace, pytest.raises(DpaEvidenceMissing):
        workspace.adopt(outside)


def test_a_held_transient_read_is_logged_as_unstored(transient: Path) -> None:
    from gridq.ingestion import load_half_hourly_csv
    from gridq.ingestion.access_log import get_access_log

    body = b"timestamp,import_kw\n2026-01-01T00:00,1.0\n2026-01-01T00:30,2.0\n"
    with TransientWorkspace() as workspace:
        path = workspace.write_bytes("hh_logged.csv", body)
        load_half_hourly_csv(path, site_id="FIXTURE-SITE-ALPHA")
    assert get_access_log()[-1].dpa_basis == "transient_unstored"


# ---------------------------------------------------------------------------------------------
# The run store
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def run_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store_dir = tmp_path / "runs"
    monkeypatch.setenv("GRIDQ_RUN_STORE", str(store_dir))
    return store_dir


def _quote(site_id: str) -> QuoteRecord:
    return QuoteRecord(
        quote_id=f"quote_{site_id.lower().replace('-', '_')}",
        quote_mode=QuoteMode.DRAFT,
        site_id=site_id,
        data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        warnings=[],
        status_history=[],
    )


def test_save_run_needs_a_permit_for_the_runs_own_site(
    run_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridq.app import store

    quote = _quote(SITE)
    with pytest.raises(TypeError):
        store.save_run(quote, None, None)  # type: ignore[call-arg, arg-type]
    register_fixture_dpas(monkeypatch, tmp_path / "ev", "FIXTURE-SITE-BETA")
    with pytest.raises(DpaEvidenceMissing):
        store.save_run(quote, None, None, dpa=authorise_store("FIXTURE-SITE-BETA"))
    assert not run_store.exists()


def test_a_stored_run_carries_its_dpa_record_and_stops_loading_when_the_dpa_goes(
    run_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridq.app import store

    quote = _quote(SITE)
    register_fixture_dpas(monkeypatch, tmp_path / "ev", SITE)
    quote_id = store.save_run(quote, None, None, dpa=authorise_store(SITE))
    record = json.loads((run_store / quote_id / "dpa_evidence.json").read_text(encoding="utf-8"))
    assert record["basis"] == "dpa" and record["site_id"] == SITE
    assert store.load_run(quote_id) is not None
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(tmp_path / "withdrawn"))
    assert store.load_run(quote_id) is None


def test_a_run_stored_before_the_guard_is_not_loaded(run_store: Path) -> None:
    from gridq.app import store

    quote = _quote("SYNTHETIC-LEGACY")
    quote_id = store.save_run(quote, None, None, dpa=authorise_store("SYNTHETIC-LEGACY"))
    assert store.load_run(quote_id) is not None
    (run_store / quote_id / "dpa_evidence.json").unlink()
    assert store.load_run(quote_id) is None
    assert store.list_runs() == []
