"""WP-SITE-06: the ride-through flag, a cited obligation ledger that never passes (D-NEW-SITE06-3).

All inputs are SYNTHETIC (``tests/_ramp_ridethrough_support.py``).
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from _ramp_ridethrough_support import (
    AS_OF,
    FRT_STUDY,
    G99_APPROVAL,
    agreement,
    all_study_evidence,
    declaration,
    evidence,
    study,
    unknown,
)
from gridq.contracts.evidence import EvidenceNotFound, EvidenceRefused, evidence_ref
from gridq.ops.dossier_model import AskHolder
from gridq.ramp_ridethrough import RideThroughFlag, screen_ridethrough
from gridq.ramp_ridethrough.ridethrough import candidate_types


def _pcc(kv: float) -> dict[str, Any]:
    return {"kind": "pcc_terms", "voltage_kv": kv, "description": "SYNTHETIC PCC"}


def _flag(*, bess: Any = None, a: Any = None, s: Any = None, ev: Any = None) -> RideThroughFlag:
    return screen_ridethrough(
        a or agreement(),
        bess or declaration(),
        s or study(),
        all_study_evidence() if ev is None else ev,
        as_of=AS_OF,
        synthetic_run=True,
    )


U = unknown()


@pytest.mark.parametrize(
    ("cap", "kv", "types"),
    [
        (500_000, Fraction(11), ("A",)),
        (5_000_000, Fraction(33), ("B",)),
        (20_000_000, Fraction(33), ("C",)),
        (60_000_000, Fraction(33), ("D",)),
        (5_000_000, Fraction(132), ("D",)),
        (5_000_000, Fraction(110), ("D",)),
        (500, Fraction(4, 10), ()),
        (5_000_000, U, ("B", "D")),
        (U, Fraction(33), ("A", "B", "C", "D")),
        (U, Fraction(132), ("D",)),
    ],
)
def test_g99_type_is_a_candidate_set_unknowns_widen_it(cap: Any, kv: Any, types: Any) -> None:
    assert candidate_types(cap, kv) == types


def test_type_b_without_evidence_is_required_open_with_asks_to_psal_and_the_dno() -> None:
    f = _flag()
    assert f.state == "required_open" and f.candidate_types == ("B",)
    (ob,) = f.obligations
    assert ob.frt == "required" and ob.fast_fault_current == "required"
    holders = {(a.holder, str(a.holder_name)) for a in f.requests}
    assert any(h is AskHolder.PARTNER and "PSAL" in n for h, n in holders)
    assert any(h is AskHolder.NETWORK_OPERATOR for h, _ in holders)


def test_table_12_2_parameters_are_cited_requirements() -> None:
    (ob,) = _flag().obligations
    vat = {p.key: p for p in ob.voltage_against_time}  # type: ignore[union-attr]
    assert vat["g99.t12_2.u_ret_pu"].value.as_fraction() == Fraction(1, 10)
    assert vat["g99.t12_2.u_rec2_pu"].value.as_fraction() == Fraction(85, 100)
    assert vat["g99.t12_2.t_clear_s"].value.as_fraction() == Fraction(14, 100)
    assert vat["g99.t12_2.t_rec3_s"].value.as_fraction() == Fraction(22, 10)
    assert len(vat) == 8
    assert {p.label for p in vat.values()} == {"requirement_cited"}
    assert {p.cite.clause_id for p in vat.values()} == {"12.3.1.6 Table 12.2"}
    assert ob.study_retained_voltage.value.as_fraction() == Fraction(1, 10)  # type: ignore[union-attr]


def test_both_legs_make_it_required_evidenced_and_never_a_pass() -> None:
    f = _flag(s=study(frt=True))
    assert f.state == "required_evidenced"
    assert [leg.satisfied for leg in f.evidence] == [True, True]
    assert f.evidence[0].held == (FRT_STUDY.content_hash(),)
    assert f.evidence[1].held == (G99_APPROVAL.content_hash(),)
    assert "pass" not in " ".join(get_args(RideThroughFlag.model_fields["state"].annotation))


def test_one_leg_alone_leaves_it_open() -> None:
    f = _flag(s=study(dno_frt_acceptance=evidence_ref(G99_APPROVAL)))
    assert f.state == "required_open"
    assert [leg.satisfied for leg in f.evidence] == [False, True]


def test_unverified_or_expired_evidence_leaves_the_leg_open() -> None:
    stale = evidence("technical_report", "oem", "stale frt study", expires="2026-11-01")
    raw = evidence("technical_report", "oem", "unverified frt study", verified=False)
    for obj in (stale, raw):
        f = _flag(
            s=study(frt=True, frt_study_report=evidence_ref(obj)),
            ev=(*all_study_evidence(), obj),
        )
        assert f.state == "required_open" and not f.evidence[0].satisfied


def test_another_sites_evidence_does_not_count() -> None:
    other = evidence("technical_report", "oem", "other site study", site="SYNTHETIC-OTHER")
    f = _flag(
        s=study(frt=True, frt_study_report=evidence_ref(other)),
        ev=(*all_study_evidence(), other),
    )
    assert not f.evidence[0].satisfied


def test_the_wrong_kind_of_document_in_a_leg_is_refused() -> None:
    wrong = evidence("connection_offer", "dno", "an offer is not a g99 approval")
    with pytest.raises(EvidenceRefused):
        _flag(
            s=study(frt=True, dno_frt_acceptance=evidence_ref(wrong)),
            ev=(*all_study_evidence(), wrong),
        )


def test_an_unresolvable_leg_reference_raises() -> None:
    with pytest.raises(EvidenceNotFound):
        _flag(s=study(frt=True), ev=all_study_evidence()[:-2])


def test_a_neura_self_issued_study_cannot_exist() -> None:
    with pytest.raises(ValidationError):
        evidence("technical_report", "neura_self_issued", "self issued study")


def test_responsibility_is_fixed_neura_never_delivers() -> None:
    f = _flag()
    assert (f.delivered_by, f.studied_by, f.neura_role) == (
        "oem_pcs_ppc",
        "psal_rms_emt",
        "screen_only",
    )
    data = f.model_dump(mode="json")
    data["delivered_by"] = "neura"
    with pytest.raises(ValidationError):
        RideThroughFlag.model_validate_json(json.dumps(data))


def test_type_a_is_conditional_on_dno_agreement() -> None:
    bess = declaration(
        registered_capacity_w=500_000,
        rated_import_power_w=500_000,
        facility_registered_capacity_w=500_000,
    )
    f = _flag(bess=bess, a=agreement(pcc=_pcc(11.0)))
    assert f.state == "conditional_on_dno_agreement"
    assert f.obligations[0].frt == "conditional_on_dno_agreement"
    assert any("11.3.1" in a.request for a in f.requests)


def test_unknown_capacity_is_indeterminate_with_the_union_of_obligations() -> None:
    f = _flag(bess=declaration(registered_capacity_w=unknown()))
    assert f.state == "indeterminate"
    assert [o.g99_type for o in f.obligations] == ["A", "B", "C", "D"]
    assert any("Registered Capacity" in a.request for a in f.requests)


def test_exactly_110_kv_needs_the_dno_to_state_the_retained_voltage() -> None:
    bess = declaration(
        registered_capacity_w=60_000_000,
        rated_import_power_w=60_000_000,
        facility_registered_capacity_w=60_000_000,
    )
    f = _flag(bess=bess, a=agreement(pcc=_pcc(110.0)))
    (ob,) = f.obligations
    assert ob.g99_type == "D"
    assert {p.key.split(".")[1] for p in ob.voltage_against_time} == {"t13_6"}  # type: ignore[union-attr]
    assert ob.study_retained_voltage.kind == "unknown"
    assert any("exactly 110 kV" in a.request for a in f.requests)


@pytest.mark.parametrize(
    ("facility", "region", "status"),
    [
        (5_000_000, "england_wales", "not_applicable"),
        (150_000_000, "england_wales", "applies"),
        (50_000_000, U, "indeterminate"),
        (50_000_000, "south_scotland", "applies"),
        (50_000_000, "england_wales", "not_applicable"),
        (U, "england_wales", "indeterminate"),
    ],
)
def test_grid_code_applicability_by_capacity_and_region(
    facility: Any, region: Any, status: str
) -> None:
    f = _flag(bess=declaration(facility_registered_capacity_w=facility, gb_region=region))
    assert f.grid_code.status == status
    ecc = {c.clause_id for c in f.grid_code.clauses if c.source_id == "GRID_CODE_I6R33"}
    assert (ecc == {"ECC.6.3.15", "ECC.6.3.16"}) is (status == "applies")
