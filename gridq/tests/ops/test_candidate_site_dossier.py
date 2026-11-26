"""WP-OPS-01: the candidate site dossier (D107 to D113).

Acceptance (roadmap WP-OPS-01):

1. each field evidenced or marked unknown with the data ask;
2. connection terms captured as IC-1 instances;
3. the dossier makes no go/no-go statement (that is D-4).

Binding data rule: customer site data is read only through the DPA guard. With no DPA registered,
every customer-sourced field is unknown with the ask "DPA for <customer> not registered
(WP-GRC-05)" plus the specific document ask. The real-site test below runs through the guard
(``require_dpa`` and ``check_load`` are spied on, not replaced) and is never skipped.

Every other site here is a labelled SYNTHETIC- site, or the fictitious ``fixture_ops01_site``
released by a TEST FIXTURE DPA (`tests/ops/_dossier_support.py`).
"""

from __future__ import annotations

import json
import re
import subprocess
import typing
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.ops._dossier_support import (
    AS_OF,
    FIX,
    FIX_CUSTOMER,
    LOCATION_INSIDE,
    LOCATION_NEAR_EDGE,
    SYN,
    SYN_B,
    boundary,
    entry,
    evidence,
    fixture_dpa,
    full_synthetic_inputs,
    ic1,
    location,
    register,
    site_record,
    write_ic1,
    write_registry,
    write_transcription,
)

import gridq.ops.dossier as dossier_mod
import gridq.ops.dossier_inputs as inputs_mod
from gridq.contracts._base import Unknown
from gridq.contracts.evidence import EvidenceStatus, EvidenceType, evidence_ref
from gridq.governance.customer_data import CUSTOMERS, load_manifest
from gridq.governance.dpa_guard import EVIDENCE_DIR_ENV, DpaEvidenceMissing
from gridq.ops import (
    NO_VERDICT_STATEMENT,
    DossierField,
    DossierInputError,
    build_dossier,
    default_register,
    render_markdown,
    write_dossier,
)
from gridq.ops.dossier import (
    ADR_D4_COMMIT,
    ADR_D4_PATH,
    ADR_D4_SHA256,
    D4_CRITERIA,
    DossierWriteRefused,
    dossier_json,
    main,
)
from gridq.ops.dossier_inputs import CUSTOMER_SOURCED, FIELD_EVIDENCE, dpa_ask
from gridq.ops.dossier_model import (
    CandidateSiteDossier,
    Evidenced,
    SiteDossier,
    UnknownField,
)
from gridq.ops.public_sources import PublicSourceError, load_boundary_dataset

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def ev_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An evidence registry the guard and the builder both read (empty until a test writes)."""
    root = tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(root))
    return root


def _site(d: CandidateSiteDossier, site_id: str) -> SiteDossier:
    return next(s for s in d.sites if s.site_id == site_id)


def _requests(f: UnknownField) -> list[str]:
    return [a.request for a in f.asks]


# =============================================================================================
# The binding data rule: a real site with no DPA, through the guard
# =============================================================================================


def test_a_real_site_with_no_dpa_is_all_unknown_with_the_dpa_ask_through_the_guard(
    ev_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_id = "grundon_colnbrook"
    real = next(e for e in default_register(ev_root / "none.json").entries if e.site_id == site_id)
    assert real.record_paths, "the manifest names the site's record, so the guard is exercised"

    dpa_calls: list[tuple[str, str]] = []
    load_calls: list[tuple[str, str]] = []
    real_require, real_check = inputs_mod.require_dpa, inputs_mod.check_load

    def spy_require(objects: Any, *, site_id: str, as_of: date) -> Any:
        try:
            out = real_require(objects, site_id=site_id, as_of=as_of)
        except DpaEvidenceMissing:
            dpa_calls.append((site_id, "refused"))
            raise
        dpa_calls.append((site_id, "released"))
        return out

    def spy_check(path: Any, **kw: Any) -> Any:
        try:
            out = real_check(path, **kw)
        except DpaEvidenceMissing:
            load_calls.append((Path(path).name, "refused"))
            raise
        load_calls.append((Path(path).name, "allowed"))
        return out

    def must_not_run(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("customer data was read for a site with no DPA")

    monkeypatch.setattr(inputs_mod, "require_dpa", spy_require)
    monkeypatch.setattr(inputs_mod, "check_load", spy_check)
    monkeypatch.setattr(dossier_mod, "load_site_model_json", must_not_run)
    monkeypatch.setattr(dossier_mod, "read_site_agreements", must_not_run)
    monkeypatch.setattr(dossier_mod, "read_site_transcriptions", must_not_run)

    d = build_dossier(register(real), as_of=AS_OF, evidence_root=ev_root)
    s = _site(d, site_id)

    assert dpa_calls == [(site_id, "refused")]
    assert load_calls == [(Path(p).name, "refused") for p in real.record_paths]
    assert s.customer_data == "not_released" and s.dpa_blockers == ("grundon",)
    assert all(n.note.startswith("not read: the DPA guard refused it") for n in s.records)

    dpa = dpa_ask("grundon")
    assert dpa.request == "DPA for grundon not registered (WP-GRC-05)"
    for name in DossierField:
        f = s.field(name)
        assert isinstance(f, UnknownField), name
        assert not f.recorded_unevidenced and not f.candidate_evidence
        if name in CUSTOMER_SOURCED:
            assert f.asks[0] == dpa
            document_asks = [a for a in f.asks if a != dpa]
            assert document_asks, f"{name}: the specific document ask is listed"
            assert all(dpa.ask_id in a.prerequisites for a in document_asks)
    assert d.ask_ledger[0].ask == dpa


def test_the_default_register_without_dpas_releases_nothing(ev_root: Path) -> None:
    d = build_dossier(default_register(ev_root / "none.json"), as_of=AS_OF, evidence_root=ev_root)
    expected = {s for c in CUSTOMERS.values() for s in c["site_ids"]}
    assert {s.site_id for s in d.sites} == expected
    assert all(s.customer_data == "not_released" for s in d.sites)
    assert not any(isinstance(s.field(n), Evidenced) for s in d.sites for n in DossierField)
    dpa_requests = {e.ask.request for e in d.ask_ledger if e.ask.scope.startswith("customer:")}
    assert dpa_requests == {f"DPA for {c} not registered (WP-GRC-05)" for c in CUSTOMERS}
    # No Chainergy site is identified: a dossier-level ask, not a placeholder site.
    assert any(a.holder_name == "Chainergy" for a in d.dossier_asks)


def test_the_dossier_does_not_widen_the_read_waiver() -> None:
    waiver = load_manifest().waiver_modules
    assert len(waiver) == 12
    assert not any("ops" in Path(m).parts for m in waiver)


# =============================================================================================
# Acceptance 1: evidenced, or unknown with the ask
# =============================================================================================


def test_a_fully_documented_synthetic_site_is_evidenced_field_by_field(
    ev_root: Path, tmp_path: Path
) -> None:
    full_synthetic_inputs(ev_root)
    _, _, ds = boundary(tmp_path)
    d = build_dossier(
        register(entry(SYN, loc=location(LOCATION_INSIDE))),
        as_of=AS_OF,
        evidence_root=ev_root,
        boundaries=ds,
    )
    s = _site(d, SYN)
    assert s.customer_data == "synthetic"
    for name in DossierField:
        f = s.field(name)
        if name is DossierField.BESS_FUNDER:
            assert isinstance(f, UnknownField) and "G9" in f.reason
        else:
            assert isinstance(f, Evidenced), (name, f)
    ct = s.connection_type
    assert isinstance(ct, Evidenced) and ct.value.archetype.value == "firm"
    dno = s.dno
    assert isinstance(dno, Evidenced) and dno.value.operator.value == "nged"
    area = s.dno_licence_area
    assert isinstance(area, Evidenced) and area.value.licence_area == "TEST AREA WEST"
    assert area.evidence[0].kind == "public_source"
    completeness = {i.key: i.completeness for i in s.d4_inputs}
    assert completeness == {
        "criterion_i": "all_inputs_evidenced",
        "criterion_ii": "all_inputs_evidenced",
        "criterion_iii": "all_inputs_evidenced",
        "criterion_iv": "inputs_open",
        "criterion_v": "not_characterised_by_this_dossier",
        "criterion_vi": "all_inputs_evidenced",
        "spec_net_export": "all_inputs_evidenced",
        "spec_onsite_generation_r13": "all_inputs_evidenced",
    }


def test_a_synthetic_site_with_no_inputs_is_unknown_with_a_document_ask_per_field(
    ev_root: Path,
) -> None:
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    s = _site(d, SYN)
    for name in DossierField:
        f = s.field(name)
        assert isinstance(f, UnknownField) and f.asks, name
        if name in CUSTOMER_SOURCED:
            assert not any(a.request.startswith("DPA for") for a in f.asks)


def test_every_field_has_an_evidence_rule_and_nothing_evidences_the_funder() -> None:
    assert set(FIELD_EVIDENCE) == set(DossierField)
    assert FIELD_EVIDENCE[DossierField.BESS_FUNDER] == {}
    assert FIELD_EVIDENCE[DossierField.DNO_LICENCE_AREA] == {}
    for name in CUSTOMER_SOURCED:
        assert FIELD_EVIDENCE[name], name
    assert EvidenceType.DATA_PROCESSING_AGREEMENT not in {
        t for m in FIELD_EVIDENCE.values() for t in m
    }


_MPAN_VALUE = {
    "kind": "metering_class",
    "settlement": "cva",
    "measurement_class": {"kind": "not_applicable", "reason": "TEST FIXTURE: CVA"},
}


@pytest.mark.parametrize(
    ("kwargs", "status", "ask_fragment", "document_ask"),
    [
        ({"verified": False}, EvidenceStatus.VALID, "with its issuer", False),
        ({"validity_unknown": True}, EvidenceStatus.VALIDITY_UNKNOWN, "effective date", False),
        ({"expires_on": "2026-06-01"}, EvidenceStatus.EXPIRED, "MPAN registration", True),
        ({"scope_site_id": SYN_B}, EvidenceStatus.VALID, "MPAN registration", True),
        ({"evidence_type": "technical_report"}, EvidenceStatus.VALID, "MPAN registration", True),
    ],
    ids=["unverified", "validity_unknown", "expired", "other_site", "wrong_type"],
)
def test_a_document_below_the_bar_is_a_candidate_with_a_specific_ask(
    ev_root: Path,
    kwargs: dict[str, Any],
    status: EvidenceStatus,
    ask_fragment: str,
    document_ask: bool,
) -> None:
    etype = kwargs.pop("evidence_type", "mpan_registration")
    role = "independent_engineer" if etype == "technical_report" else "elexon"
    obj = evidence(SYN, etype, role, **kwargs)
    write_registry(ev_root, obj)
    write_transcription(ev_root, SYN, "metering_class", _MPAN_VALUE, evidence_ref(obj))
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    f = _site(d, SYN).metering_class
    assert isinstance(f, UnknownField)
    assert len(f.candidate_evidence) == 1
    assert f.candidate_evidence[0].status_as_of is status
    assert any(ask_fragment in r for r in _requests(f)), _requests(f)
    has_document_ask = any(r.startswith("MPAN registration or meter details") for r in _requests(f))
    assert has_document_ask is document_ask
    # The transcribed value is shown beside the ask, never as the field's value.
    assert f.recorded_unevidenced and f.recorded_unevidenced[0].value.kind == "metering_class"


def test_an_unregistered_reference_asks_for_its_registration(ev_root: Path) -> None:
    obj = evidence(SYN, "mpan_registration", "elexon")
    write_transcription(ev_root, SYN, "metering_class", _MPAN_VALUE, evidence_ref(obj))
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    f = _site(d, SYN).metering_class
    assert isinstance(f, UnknownField)
    assert f.candidate_evidence[0].verification == "not_registered"
    assert any(r.startswith("Register the IC-4 evidence object") for r in _requests(f))


def test_a_released_record_value_without_a_document_is_shown_but_not_evidence(
    ev_root: Path, tmp_path: Path
) -> None:
    write_registry(ev_root, fixture_dpa(FIX))
    rec = site_record(tmp_path / "fixture_site.json", FIX, dno_region="NGED_SW", mec_kw=120.0)
    d = build_dossier(
        register(entry(FIX, customer=FIX_CUSTOMER, records=(str(rec),))),
        as_of=AS_OF,
        evidence_root=ev_root,
    )
    s = _site(d, FIX)
    assert s.customer_data == "released_under_dpa"
    dno, export = s.dno, s.net_export
    assert isinstance(dno, UnknownField) and isinstance(export, UnknownField)
    assert dno.recorded_unevidenced[0].field_path == "connection.dno_region"
    assert export.recorded_unevidenced[0].field_path == "connection.mec_kw"
    assert any(r.startswith("A document naming the connecting") for r in _requests(dno))
    assert s.records[0].note == "read under the site's DPA"


def test_a_released_record_value_linked_to_an_admissible_document_is_evidenced(
    ev_root: Path, tmp_path: Path
) -> None:
    ca = evidence(FIX, "connection_agreement", "dno", record_class="registered")
    write_registry(ev_root, fixture_dpa(FIX), ca)
    rec = site_record(
        tmp_path / "fixture_site.json",
        FIX,
        links=[
            ("connection.dno_region", evidence_ref(ca)),
            ("connection.mec_kw", evidence_ref(ca)),
        ],
    )
    d = build_dossier(
        register(entry(FIX, customer=FIX_CUSTOMER, records=(str(rec),))),
        as_of=AS_OF,
        evidence_root=ev_root,
    )
    s = _site(d, FIX)
    assert isinstance(s.dno, Evidenced) and s.dno.value.licence_area == "NGED_SW"
    assert isinstance(s.net_export, Evidenced)
    assert s.net_export.value.measures[0].kw == 120.0


def test_a_record_naming_another_site_is_not_used(ev_root: Path, tmp_path: Path) -> None:
    write_registry(ev_root, fixture_dpa(FIX))
    rec = site_record(tmp_path / "other.json", "fixture_ops01_other_site")
    d = build_dossier(
        register(entry(FIX, customer=FIX_CUSTOMER, records=(str(rec),))),
        as_of=AS_OF,
        evidence_root=ev_root,
    )
    s = _site(d, FIX)
    assert "so not used" in s.records[0].note
    assert isinstance(s.dno, UnknownField) and not s.dno.recorded_unevidenced


def test_a_site_whose_data_is_not_released_cannot_carry_evidence(ev_root: Path) -> None:
    full_synthetic_inputs(ev_root, SYN)
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    good = _site(d, SYN).model_dump(mode="json")
    good.update(
        site_id="fixture_ops01_unreleased",
        origin="owner_register",
        customer_data="not_released",
        dpa_blockers=["fixture_ops01_customer"],
    )
    with pytest.raises(ValueError, match="not released"):
        SiteDossier.model_validate_json(json.dumps(good))


# =============================================================================================
# Synthetic data never reaches a real site (Fable binding rule 4)
# =============================================================================================


def test_synthetic_evidence_on_a_real_site_is_an_error(ev_root: Path) -> None:
    syn_obj = evidence(FIX, "mpan_registration", "elexon")  # record_class synthetic_fixture
    write_registry(ev_root, fixture_dpa(FIX), syn_obj)
    write_transcription(
        ev_root,
        FIX,
        "metering_class",
        _MPAN_VALUE,
        evidence_ref(syn_obj),
        record_class="registered",
    )
    with pytest.raises(DossierInputError, match="synthetic fixture"):
        build_dossier(
            register(entry(FIX, customer=FIX_CUSTOMER)), as_of=AS_OF, evidence_root=ev_root
        )


def test_a_synthetic_transcription_on_a_real_site_is_an_error(ev_root: Path) -> None:
    real_obj = evidence(FIX, "mpan_registration", "elexon", record_class="registered")
    write_registry(ev_root, fixture_dpa(FIX), real_obj)
    write_transcription(ev_root, FIX, "metering_class", _MPAN_VALUE, evidence_ref(real_obj))
    with pytest.raises(DossierInputError, match="synthetic transcription"):
        build_dossier(
            register(entry(FIX, customer=FIX_CUSTOMER)), as_of=AS_OF, evidence_root=ev_root
        )


def test_a_synthetic_archetype_ic1_on_a_real_site_is_an_error(ev_root: Path) -> None:
    write_registry(ev_root, fixture_dpa(FIX))
    write_ic1(ev_root, ic1(FIX))  # the template's source is a SyntheticArchetype
    with pytest.raises(DossierInputError, match="synthetic archetype"):
        build_dossier(
            register(entry(FIX, customer=FIX_CUSTOMER)), as_of=AS_OF, evidence_root=ev_root
        )


def test_a_synthetic_location_or_boundary_on_a_real_site_is_an_error(
    ev_root: Path, tmp_path: Path
) -> None:
    write_registry(ev_root, fixture_dpa(FIX))
    _, _, ds = boundary(tmp_path)
    reg = register(entry(FIX, customer=FIX_CUSTOMER, loc=location(LOCATION_INSIDE)))
    with pytest.raises(DossierInputError, match="synthetic fixture location"):
        build_dossier(reg, as_of=AS_OF, evidence_root=ev_root, boundaries=ds)
    reg = register(entry(FIX, customer=FIX_CUSTOMER, loc=location(LOCATION_INSIDE, "owner_survey")))
    with pytest.raises(DossierInputError, match="synthetic fixture boundary"):
        build_dossier(reg, as_of=AS_OF, evidence_root=ev_root, boundaries=ds)


# =============================================================================================
# Acceptance 2: connection terms as IC-1 instances (D110)
# =============================================================================================


def test_connection_terms_embed_the_ic1_instance_with_its_chassis_reference(
    ev_root: Path,
) -> None:
    full_synthetic_inputs(ev_root)
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    terms = _site(d, SYN).connection_terms
    assert isinstance(terms, Evidenced)
    (embedded,) = terms.value.agreements
    assert embedded.ref.contract == "IC-1"
    assert embedded.ref.agreement_hash == embedded.agreement.content_hash()
    assert terms.evidence[0].kind == "ic4"


def test_an_embedded_agreement_whose_hash_does_not_match_is_refused(ev_root: Path) -> None:
    full_synthetic_inputs(ev_root)
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    data = json.loads(dossier_json(d))
    terms = next(s for s in data["sites"] if s["site_id"] == SYN)["connection_terms"]
    terms["value"]["agreements"][0]["agreement"]["agreement_ref"] = "TAMPERED"
    with pytest.raises(ValueError, match="content hash"):
        CandidateSiteDossier.model_validate_json(json.dumps(data))


def test_two_ic1_instances_with_different_archetypes_leave_the_type_open(ev_root: Path) -> None:
    ca1 = evidence(SYN, "connection_agreement", "dno", tag="one")
    ca2 = evidence(SYN, "connection_offer", "dno", tag="two")
    write_registry(ev_root, ca1, ca2)
    write_ic1(ev_root, ic1(SYN, source=evidence_ref(ca1), archetype="firm", ref="A"))
    write_ic1(ev_root, ic1(SYN, source=evidence_ref(ca2), archetype="hybrid", ref="B"))
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    s = _site(d, SYN)
    assert isinstance(s.connection_type, UnknownField)
    assert any(
        r.startswith("Say which connection document is current")
        for r in _requests(s.connection_type)
    )
    assert isinstance(s.connection_terms, Evidenced)
    assert len(s.connection_terms.value.agreements) == 2


def test_an_ic1_with_no_source_or_no_operator_evidences_nothing_it_lacks(ev_root: Path) -> None:
    ca = evidence(SYN, "connection_agreement", "dno")
    write_registry(ev_root, ca)
    write_ic1(ev_root, ic1(SYN, source=evidence_ref(ca), operator=None, ref="A"))
    write_ic1(
        ev_root,
        ic1(SYN, source={"kind": "unknown", "reason": "TEST FIXTURE: no document"}, ref="B"),
    )
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    s = _site(d, SYN)
    assert isinstance(s.connection_terms, Evidenced)
    assert len(s.connection_terms.value.agreements) == 1  # the unsourced one is not evidence
    assert isinstance(s.dno, UnknownField)
    assert "does not name its network operator" in s.dno.reason


def test_a_record_naming_an_unregistered_ic1_raises_a_discrepancy_ask(
    ev_root: Path, tmp_path: Path
) -> None:
    ca = evidence(FIX, "connection_agreement", "dno", record_class="registered")
    write_registry(ev_root, fixture_dpa(FIX), ca)
    registered = ic1(FIX, source=evidence_ref(ca), ref="A")
    write_ic1(ev_root, registered)
    other = ic1(FIX, source=evidence_ref(ca), ref="NOT-FILED")
    rec = site_record(tmp_path / "fixture_site.json", FIX, agreement=other)
    d = build_dossier(
        register(entry(FIX, customer=FIX_CUSTOMER, records=(str(rec),))),
        as_of=AS_OF,
        evidence_root=ev_root,
    )
    s = _site(d, FIX)
    for f in (s.connection_type, s.connection_terms):
        assert isinstance(f, UnknownField)
        assert any(r.startswith("Register the IC-1 agreement") for r in _requests(f))


def test_an_ic1_filed_under_another_site_is_an_error(ev_root: Path) -> None:
    agreement = ic1(SYN_B)
    target = ev_root / "connection_agreements" / SYN
    target.mkdir(parents=True)
    (target / "x.json").write_text(agreement.model_dump_json(), encoding="utf-8")
    with pytest.raises(DossierInputError, match="filed under"):
        build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)


# =============================================================================================
# Public channel: licence area by local point-in-polygon (D111)
# =============================================================================================


def test_licence_area_needs_one_containing_area_and_a_clear_margin(
    ev_root: Path, tmp_path: Path
) -> None:
    _, _, ds = boundary(tmp_path)
    reg = register(
        entry(SYN, loc=location(LOCATION_NEAR_EDGE)),
        entry(SYN_B, loc=location({"latitude": 51.8, "longitude": -1.8})),  # in the hole
    )
    d = build_dossier(reg, as_of=AS_OF, evidence_root=ev_root, boundaries=ds)
    edge, hole = _site(d, SYN).dno_licence_area, _site(d, SYN_B).dno_licence_area
    assert isinstance(edge, UnknownField) and "margin" in edge.reason
    assert isinstance(hole, UnknownField) and "no published licence area" in hole.reason


def test_no_location_or_no_dataset_is_an_owner_ask_without_a_dpa_prerequisite(
    ev_root: Path,
) -> None:
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    f = _site(d, SYN).dno_licence_area
    assert isinstance(f, UnknownField)
    assert {a.holder.value for a in f.asks} == {"neura_owner"}
    assert all(not a.prerequisites for a in f.asks)


def test_a_boundary_file_that_is_not_the_cited_document_is_refused(tmp_path: Path) -> None:
    path, source, _ = boundary(tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PublicSourceError, match="do not hash"):
        load_boundary_dataset(path, source=source, area_property="area")


def test_a_projected_boundary_dataset_is_refused(tmp_path: Path) -> None:
    doc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}},
        "features": [],
    }
    with pytest.raises(PublicSourceError, match="CRS"):
        boundary(tmp_path, doc=doc)


# =============================================================================================
# Acceptance 3: no go/no-go statement (D109)
# =============================================================================================

_VERDICT = re.compile(
    r"\b(go|no-go|go/no-go|eligible|ineligible|suitable|unsuitable|recommend\w*|approve|"
    r"approved|reject\w*|pass|passes|passed|fail|fails|failed|viable|unviable|rank|ranked|"
    r"ranking|score\w*|readiness|ready|verdict|shortlist\w*|preferred|best|worst|winner|"
    r"proceed|select\w*|qualif\w*|distance)\b",
    re.IGNORECASE,
)


def _strings_and_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [k for k in node] + [s for v in node.values() for s in _strings_and_keys(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings_and_keys(v)]
    return [node] if isinstance(node, str) else []


def _dossiers(ev_root: Path, tmp_path: Path) -> list[CandidateSiteDossier]:
    full_synthetic_inputs(ev_root)
    _, _, ds = boundary(tmp_path)
    reg = default_register(ev_root / "none.json")
    real = build_dossier(reg, as_of=AS_OF, evidence_root=ev_root)
    syn = build_dossier(
        register(entry(SYN, loc=location(LOCATION_INSIDE)), entry(SYN_B)),
        as_of=AS_OF,
        evidence_root=ev_root,
        boundaries=ds,
    )
    return [real, syn]


def test_no_verdict_language_in_the_json_or_the_markdown(ev_root: Path, tmp_path: Path) -> None:
    for d in _dossiers(ev_root, tmp_path):
        texts = _strings_and_keys(json.loads(dossier_json(d)))
        texts.append(render_markdown(d))
        for text in texts:
            hits = _VERDICT.findall(text.replace(NO_VERDICT_STATEMENT, ""))
            assert not hits, (hits, text[:200])
        assert d.statement == NO_VERDICT_STATEMENT
        assert NO_VERDICT_STATEMENT in render_markdown(d)


def test_no_site_level_bool_score_or_rank_field() -> None:
    hints = typing.get_type_hints(SiteDossier, include_extras=True)
    for name, hint in hints.items():
        assert "bool" not in repr(hint), name
        assert not re.search(r"score|rank|verdict|eligib|ready|pass|fail", name), name
    for model in (CandidateSiteDossier,):
        for name in model.model_fields:
            assert not re.search(r"score|rank|verdict|eligib|ready", name), name


def test_the_d4_map_cites_adr_d4_and_reads_only_dossier_fields() -> None:
    keys = [c.key for c in D4_CRITERIA]
    assert keys == [
        "criterion_i",
        "criterion_ii",
        "criterion_iii",
        "criterion_iv",
        "criterion_v",
        "criterion_vi",
        "spec_net_export",
        "spec_onsite_generation_r13",
    ]
    read = {f for c in D4_CRITERIA for f in c.reads}
    assert read == set(DossierField) - {DossierField.DNO_LICENCE_AREA}
    assert next(c for c in D4_CRITERIA if c.key == "criterion_v").reads == ()
    assert ADR_D4_PATH == "atlas:roadmap/adr/ADR-D4.md"


def test_the_adr_d4_pin_matches_the_atlas_file() -> None:
    atlas = REPO.parent / "atlas"
    if not (atlas / ".git").exists():
        pytest.skip("the atlas repository is not checked out beside this one")
    show = subprocess.run(
        ["git", "-C", str(atlas), "show", f"{ADR_D4_COMMIT}:roadmap/adr/ADR-D4.md"],
        capture_output=True,
        check=False,
    )
    if show.returncode != 0:
        pytest.skip("the pinned atlas commit is not in this checkout")
    import hashlib

    assert "sha256:" + hashlib.sha256(show.stdout).hexdigest() == ADR_D4_SHA256


# =============================================================================================
# Determinism, ledger, rendering
# =============================================================================================


def test_the_build_is_deterministic_and_the_golden_hash_holds(
    ev_root: Path, tmp_path: Path
) -> None:
    full_synthetic_inputs(ev_root)
    _, _, ds = boundary(tmp_path)
    reg = register(entry(SYN, loc=location(LOCATION_INSIDE)), entry(SYN_B))
    a = build_dossier(reg, as_of=AS_OF, evidence_root=ev_root, boundaries=ds)
    b = build_dossier(reg, as_of=AS_OF, evidence_root=ev_root, boundaries=ds)
    assert dossier_json(a) == dossier_json(b)
    assert a.content_hash() == b.content_hash() == GOLDEN_SYNTHETIC_HASH
    assert render_markdown(a) == render_markdown(b)
    assert a.content_hash() in render_markdown(a)


#: Moves only when the dossier schema, the builder or these synthetic fixtures change.
GOLDEN_SYNTHETIC_HASH = "sha256:a625d54b80c7f6f49e3b36ad5b19b5c315b75f6332634aa8fe34597b42021961"


def test_the_ledger_puts_prerequisites_first_and_lists_each_ask_once(ev_root: Path) -> None:
    d = build_dossier(default_register(ev_root / "none.json"), as_of=AS_OF, evidence_root=ev_root)
    ids = [e.ask.ask_id for e in d.ask_ledger]
    assert len(ids) == len(set(ids))
    position = {i: n for n, i in enumerate(ids)}
    for e in d.ask_ledger:
        assert all(position[p] < position[e.ask.ask_id] for p in e.ask.prerequisites)
    grundon = next(e for e in d.ask_ledger if e.ask.request.startswith("DPA for grundon"))
    assert grundon.leverage == 6 * len(CUSTOMER_SOURCED)


def test_recorded_values_never_render_in_the_value_column(ev_root: Path, tmp_path: Path) -> None:
    write_registry(ev_root, fixture_dpa(FIX))
    rec = site_record(tmp_path / "fixture_site.json", FIX, dno_region="NGED_SW")
    d = build_dossier(
        register(entry(FIX, customer=FIX_CUSTOMER, records=(str(rec),))),
        as_of=AS_OF,
        evidence_root=ev_root,
    )
    md = render_markdown(d)
    dno_row = next(line for line in md.splitlines() if line.startswith("| dno |"))
    assert "NGED_SW" not in dno_row and "| - |" in dno_row
    assert "NGED_SW" in md  # shown under "Recorded but not evidenced"


# =============================================================================================
# Writing: fail closed (D112)
# =============================================================================================


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("dossiers/\n", encoding="utf-8")
    return repo


def test_a_real_site_dossier_is_written_only_where_git_ignores_it(
    ev_root: Path, tmp_path: Path
) -> None:
    d = build_dossier(default_register(ev_root / "none.json"), as_of=AS_OF, evidence_root=ev_root)
    repo = _git_repo(tmp_path)
    json_path, md_path = write_dossier(d, repo / "dossiers")
    assert json_path.is_file() and md_path.is_file()
    with pytest.raises(DossierWriteRefused):
        write_dossier(d, repo / "tracked")
    assert not (repo / "tracked").exists()
    with pytest.raises(DossierWriteRefused):
        write_dossier(d, tmp_path / "not_a_repo")


def test_a_synthetic_only_dossier_may_be_written_anywhere(ev_root: Path, tmp_path: Path) -> None:
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    json_path, _ = write_dossier(d, tmp_path / "anywhere")
    assert json_path.read_text(encoding="utf-8") == dossier_json(d)


def test_the_repository_ignores_the_dossiers_directory() -> None:
    probe = REPO / "dossiers" / "candidate_site_dossier_probe.json"
    result = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(probe)], check=False)
    assert result.returncode == 0


def test_the_cli_refuses_a_tracked_path_and_prints_no_values(
    ev_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path)
    args = [
        "--as-of",
        "2026-11-26",
        "--evidence-dir",
        str(ev_root),
        "--register",
        str(ev_root / "none.json"),
    ]
    assert main([*args, "--out", str(repo / "tracked")]) == 3
    assert main([*args, "--out", str(repo / "dossiers")]) == 0
    out = capsys.readouterr().out
    assert "sites: 8" in out
    for spec in CUSTOMERS.values():
        for site_id in spec["site_ids"]:
            assert site_id not in out


def test_unknown_values_are_typed_never_none(ev_root: Path) -> None:
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    assert "null" not in dossier_json(d)
    assert isinstance(Unknown(kind="unknown", reason="x"), Unknown)
