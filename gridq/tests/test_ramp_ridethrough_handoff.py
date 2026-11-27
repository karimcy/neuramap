"""WP-SITE-06 acceptance 2: the handoff bundle holds what PSAL needs for RMS/EMT (D-NEW-SITE06-4).

All inputs are SYNTHETIC (``tests/_ramp_ridethrough_support.py``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from _ramp_ridethrough_support import (
    AS_OF,
    agreement,
    all_study_evidence,
    declaration,
    evidence,
    study,
    unknown,
)
from gridq.contracts.evidence import EvidenceRef, EvidenceType
from gridq.ops.dossier_model import AskHolder
from gridq.ramp_ridethrough import (
    CATALOGUE,
    CATALOGUE_HASH,
    SOURCES,
    case_readiness,
    screen_ramp_ridethrough,
)
from gridq.ramp_ridethrough.handoff import (
    EVIDENCE_ONLY,
    BundleItem,
    DeclaredValue,
    Provided,
    PublicConstantValue,
)
from gridq.ramp_ridethrough.ridethrough import cited_parameter


def _screen(*, bess: Any = None, a: Any = None, s: Any = None, ev: tuple = ()) -> Any:  # type: ignore[type-arg]
    return screen_ramp_ridethrough(
        a or agreement(),
        bess or declaration(),
        s or study(),
        evidence=(*all_study_evidence(), *ev),
        as_of=AS_OF,
        record_class="synthetic_fixture",
    )


def _readiness(screen: Any) -> dict[str, str]:
    return {c.case_id: c.readiness.kind for c in screen.case_readiness}


def test_bundle_holds_exactly_the_catalogue_items_each_in_one_state() -> None:
    b = _screen().handoff
    assert [i.item_id for i in b.items] == [i.item_id for i in CATALOGUE.items]
    assert {i.entry.kind for i in b.items} <= {"provided", "asked", "not_applicable"}
    assert b.catalogue_hash == CATALOGUE_HASH and b.catalogue_version == "1.0.0"


def test_every_catalogue_anchor_resolves_to_a_retrieved_source() -> None:
    for item in CATALOGUE.items:
        assert item.anchors or item.ic1_fields, item.item_id
        for c in item.anchors:
            src = SOURCES[c.source_id]
            assert (c.url, c.sha256) == (src.url, src.sha256)
    for case in CATALOGUE.cases:
        assert case.anchors
        assert set(case.requires) <= {i.item_id for i in CATALOGUE.items}


def test_catalogue_covers_rms_and_emt_studies() -> None:
    assert {c.case_id for c in CATALOGUE.cases} == {
        "frt_three_phase",
        "frt_unbalanced",
        "ffci",
        "post_fault_recovery",
        "pcs_trip_rvc",
        "transformer_energisation_rvc",
        "ppc_ramp_response",
    }
    domains = {i.domain for i in CATALOGUE.items}
    assert {"rms", "emt", "rms_and_emt"} <= domains
    ids = {i.item_id for i in CATALOGUE.items}
    for needed in (
        "site_notified_minimum_fault_level",
        "x_over_r",
        "transformer_data",
        "oem_rms_model",
        "oem_emt_model",
        "ppc_settings",
        "protection_settings",
        "g99_type_and_frt_parameters",
    ):
        assert needed in ids


def test_full_synthetic_fixture_makes_every_case_ready() -> None:
    s = _screen()
    assert set(_readiness(s).values()) == {"ready"}
    b = s.handoff
    for item_id in ("oem_rms_model", "oem_emt_model", "transformer_data"):
        e = b.item(item_id).entry
        assert isinstance(e, Provided) and isinstance(e.value, EvidenceRef)
    g = b.item("g99_type_and_frt_parameters").entry
    assert isinstance(g, Provided) and g.value.obligation.g99_type == "B"  # type: ignore[union-attr]
    assert {r.evidence_hash for r in b.evidence_refs} >= {
        e.value.evidence_hash  # type: ignore[union-attr]
        for i in b.items
        if isinstance((e := i.entry), Provided) and isinstance(e.value, EvidenceRef)
    }


def test_empty_fixture_asks_the_right_holders_and_blocks_every_case() -> None:
    s = _screen(
        s=study(sk_va=None, x_over_r=None, documents=False),
        bess=declaration(reactive_range=unknown()),
    )
    b = s.handoff

    def holders(item_id: str) -> set[Any]:
        e = b.item(item_id).entry
        assert e.kind == "asked", item_id
        return {(a.holder, str(a.holder_name)) for a in e.asks}  # type: ignore[union-attr]

    for item_id in ("site_notified_minimum_fault_level", "x_over_r", "network_equivalent"):
        assert {h for h, _ in holders(item_id)} == {AskHolder.NETWORK_OPERATOR}
    assert {h for h, _ in holders("transformer_data")} == {AskHolder.CUSTOMER}
    for item_id in ("oem_rms_model", "oem_emt_model", "protection_settings"):
        assert holders(item_id) == {(AskHolder.PARTNER, "SYNTHETIC-OEM (fixture)")}
    assert set(_readiness(s).values()) == {"blocked"}


def test_the_fault_level_is_never_filled_from_the_g99_generic_figure() -> None:
    s = _screen(s=study(sk_va=None))
    b = s.handoff
    assert b.item("site_notified_minimum_fault_level").entry.kind == "asked"
    g = b.item("g99_generic_minimum_short_circuit_power").entry
    assert isinstance(g, Provided) and isinstance(g.value, PublicConstantValue)
    (p,) = g.value.parameters
    assert p.value.as_fraction() == 50_000_000 and p.label == "requirement_cited"
    r = _readiness(s)
    for case in ("frt_three_phase", "frt_unbalanced", "ffci", "post_fault_recovery"):
        assert r[case] == "ready_generic_only"
    for case in ("pcs_trip_rvc", "transformer_energisation_rvc"):
        assert r[case] == "blocked"
    generic = next(c for c in s.case_readiness if c.case_id == "frt_three_phase").readiness
    assert generic.label == "generic_g99_fault_level_not_site_compliant"  # type: ignore[union-attr]


@pytest.mark.parametrize("item_id", sorted(EVIDENCE_ONLY))
def test_site_electrical_data_is_provided_only_from_evidence(item_id: str) -> None:
    stand_in = Provided(
        kind="provided",
        value=PublicConstantValue(
            kind="public_constant",
            parameters=(cited_parameter("g99.study.generic_min_short_circuit_va"),),
        ),
        provenance="public_constant",
    )
    with pytest.raises(ValidationError):
        BundleItem(item_id=item_id, entry=stand_in)  # type: ignore[arg-type]


def test_a_declared_value_cannot_stand_in_for_the_fault_level() -> None:
    declared = Provided(
        kind="provided",
        value=DeclaredValue(
            kind="declared_value",
            value=cited_parameter("g99.study.generic_min_short_circuit_va").value,
            unit="W",
            declared_by=declaration().declared_by,
        ),
        provenance="bess_declaration",
    )
    with pytest.raises(ValidationError):
        BundleItem(item_id="site_notified_minimum_fault_level", entry=declared)


def test_catalogue_stays_proposed_until_psal_confirms_its_hash() -> None:
    s = _screen()
    assert s.handoff.catalogue_status == "neura_proposed"
    psal = [a for a in s.person_only_asks if a.holder is AskHolder.PARTNER]
    assert len(psal) == 1 and str(psal[0].holder_name) == "PSAL"
    assert psal[0].closes_with == (EvidenceType.METHOD_AGREEMENT,)
    assert CATALOGUE_HASH in psal[0].request

    wrong = evidence("method_agreement", "research_partner", "psal other catalogue")
    assert _screen(ev=(wrong,)).handoff.catalogue_status == "neura_proposed"

    ok = evidence(
        "method_agreement", "research_partner", "psal catalogue", document_hash=CATALOGUE_HASH
    )
    confirmed = _screen(ev=(ok,))
    assert confirmed.handoff.catalogue_status == "psal_confirmed"
    assert confirmed.handoff.catalogue_confirmations == (ok.content_hash(),)
    assert not [a for a in confirmed.person_only_asks if a.holder is AskHolder.PARTNER]


def test_readiness_is_derived_from_the_bundle() -> None:
    s = _screen(s=study(x_over_r=None))
    assert s.case_readiness == case_readiness(s.handoff)
    assert _readiness(s)["pcs_trip_rvc"] == "blocked"
    assert type(s).model_validate_json(s.model_dump_json()) == s
    data = s.model_dump(mode="json")
    data["case_readiness"][0]["readiness"] = {"kind": "ready"}
    data["case_readiness"][4]["readiness"] = {"kind": "ready"}
    with pytest.raises(ValidationError):
        type(s).model_validate_json(json.dumps(data))


def test_ic1_not_applicable_terms_are_not_applicable_items() -> None:
    a = agreement(
        ramp={"kind": "not_applicable", "reason": "SYNTHETIC: none"},
        power_factor={"kind": "not_applicable", "reason": "SYNTHETIC: none"},
    )
    b = _screen(a=a).handoff
    assert b.item("ic1_ramp_terms").entry.kind == "not_applicable"
    assert b.item("ic1_power_factor_terms").entry.kind == "not_applicable"
