"""IC-11 thermal flex v1.0.0 conformance (roadmap WP-THERM-02, NEW-IC-11).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. Availability with zero flex is valid and distinct from unavailable.
3. Consumers can run against fixtures without a plant model.

Plus the guards the design rests on (exact integers, the seasons x bands x duration grid, rebound,
declared IT-safety limits, evidence class backed by IC-4, the instruction/acknowledgement pair),
and mutation checks proving at least six of those guards are load-bearing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import typing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import _ic11_fixtures
import pytest
from _contracts_support import GOLDEN_HASHES, IC11_FIXTURES, dumps, load, run_mutant
from pydantic import ValidationError

from gridq.contracts import thermal_flex
from gridq.contracts._base import ContractModel, NotApplicable, Unknown, content_hash
from gridq.contracts.evidence import EvidenceRef, IssuerRole
from gridq.contracts.thermal_flex import (
    CONTRACT_VERSION,
    GIT_TAG,
    AvailabilityStatus,
    EvidenceClass,
    Flex,
    FlexAcknowledgement,
    FlexInstruction,
    ResponseReason,
    ThermalFlexAvailability,
    availability_ref,
    check_instruction,
    deliverable_kw,
    kw_to_mw,
    query,
    query_at,
    verify_acknowledgement,
)

MODELLED = "ic11_surface_chilled_water_modelled.synthetic.json"
METERED = "ic11_surface_metered_trial.synthetic.json"
INSTRUCTION = "ic11_instruction_winter_night.synthetic.json"
ACKS = {
    "accepted": "ic11_ack_accepted.synthetic.json",
    "partial": "ic11_ack_partial.synthetic.json",
    "rejected": "ic11_ack_rejected.synthetic.json",
}
TOP_LEVEL = (ThermalFlexAvailability, FlexInstruction, FlexAcknowledgement)
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _surface(data: dict[str, Any]) -> ThermalFlexAvailability:
    return ThermalFlexAvailability.model_validate_json(dumps(data))


def _instruction(data: dict[str, Any]) -> FlexInstruction:
    return FlexInstruction.model_validate_json(dumps(data))


def _ack(data: dict[str, Any]) -> FlexAcknowledgement:
    return FlexAcknowledgement.model_validate_json(dumps(data))


def _model_for(name: str) -> type[ContractModel]:
    if "_surface_" in name:
        return ThermalFlexAvailability
    if "_instruction_" in name:
        return FlexInstruction
    return FlexAcknowledgement


def _cell(data: dict[str, Any], season: str, band: str) -> dict[str, Any]:
    cell: dict[str, Any] = next(
        c for c in data["cells"] if (c["season_id"], c["band_id"]) == (season, band)
    )
    return cell


def _ref(h: str) -> dict[str, Any]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": "1.0.0",
        "evidence_hash": "sha256:" + h * 64,
    }


def _rebound(energy: int = 100, peak: int = 60, minutes: int = 120) -> dict[str, int]:
    return {"energy_kwh": energy, "peak_kw": peak, "recovery_min": minutes}


def _point(duration: int, kw: int) -> dict[str, Any]:
    return {"duration_min": duration, "reduction_kw": kw, "rebound": _rebound()}


# ---------------------------------------------------------------- 1. version tagged, fixtures pass


def test_contract_version_is_tagged_in_module_and_on_every_instance() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert GIT_TAG == f"contracts/ic11-thermal-flex/v{CONTRACT_VERSION}"
    assert len(IC11_FIXTURES) == 6
    for path in IC11_FIXTURES:
        obj = _model_for(path.name).model_validate_json(path.read_text(encoding="utf-8"))
        assert (obj.contract, obj.contract_version) == ("IC-11", "1.0.0")  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", [MODELLED, INSTRUCTION, ACKS["accepted"]])
@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "1.0.1"), ("contract_version", "2.0.0"), ("contract", "IC-2")],
)
def test_a_payload_of_another_contract_or_version_is_refused(
    name: str, field: str, value: str
) -> None:
    data = load(name)
    data[field] = value
    with pytest.raises(ValidationError):
        _model_for(name).model_validate_json(dumps(data))


def test_committed_fixtures_equal_a_fresh_build() -> None:
    for path, build in _ic11_fixtures.BUILDERS.items():
        assert path.read_text(encoding="utf-8") == _ic11_fixtures.render(build()), path.name


def test_spec_elements_are_required_fields() -> None:
    top = ThermalFlexAvailability.model_fields
    for f in ("seasons", "bands", "cells", "limits", "evidence_class", "evidence", "clock"):
        assert top[f].is_required(), f
    assert thermal_flex.FlexPoint.model_fields["rebound"].is_required()
    ins = FlexInstruction.model_fields
    for f in ("instruction_id", "reduction_kw", "start", "duration_min", "issuer_role"):
        assert ins[f].is_required(), f
    for f in ("instruction_hash", "acknowledged_at", "response"):
        assert FlexAcknowledgement.model_fields[f].is_required(), f


def _allows_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_allows_none(a) for a in typing.get_args(annotation))
    return origin is typing.Literal and None in typing.get_args(annotation)


def test_no_field_of_any_ic11_model_admits_none() -> None:
    models = [
        o
        for o in vars(thermal_flex).values()
        if isinstance(o, type) and issubclass(o, ContractModel) and o is not ContractModel
    ]
    assert set(TOP_LEVEL) <= set(models) and len(models) > 20
    for m in models:
        for name, f in m.model_fields.items():
            assert not _allows_none(f.annotation), f"{m.__name__}.{name}"


@pytest.mark.parametrize(
    "name,field",
    [
        (n, f)
        for n, m in ((MODELLED, ThermalFlexAvailability), (INSTRUCTION, FlexInstruction))
        for f in sorted(m.model_fields)
    ]
    + [(ACKS["partial"], f) for f in sorted(FlexAcknowledgement.model_fields)],
)
def test_null_is_refused_for_every_top_level_field(name: str, field: str) -> None:
    data = load(name)
    data[field] = None
    with pytest.raises(ValidationError):
        _model_for(name).model_validate_json(dumps(data))


@pytest.mark.parametrize("model", TOP_LEVEL, ids=lambda m: m.__name__)
def test_top_level_models_are_final_and_frozen(model: type[ContractModel]) -> None:
    with pytest.raises(TypeError, match="final"):
        type("Looser", (model,), {})
    obj = _surface(load(MODELLED))
    with pytest.raises(ValidationError):
        obj.surface_id = "changed"  # type: ignore[misc]


# ------------------------------------------- 2. zero flex is valid and distinct from unavailable


def test_fixtures_carry_all_four_cell_kinds() -> None:
    kinds = {
        c.availability.kind for name in (MODELLED, METERED) for c in _surface(load(name)).cells
    }
    assert kinds == {"flex", "zero_flex", "unavailable", "unknown"}


def test_zero_flex_unavailable_and_unknown_are_distinct_non_null_answers() -> None:
    modelled, metered = _surface(load(MODELLED)), _surface(load(METERED))
    zero = query(modelled, month=7, minute_of_day=15 * 60, duration_min=30)
    assert zero.status is AvailabilityStatus.ZERO_FLEX
    assert zero.deliverable_kw == 0 and type(zero.deliverable_kw) is int
    assert isinstance(zero.rebound, NotApplicable)

    off = query(metered, month=1, minute_of_day=12 * 60, duration_min=30)
    assert off.status is AvailabilityStatus.UNAVAILABLE
    assert isinstance(off.deliverable_kw, NotApplicable)
    assert "outside_agreed_window" in off.deliverable_kw.reason

    unknown = query(metered, month=7, minute_of_day=60, duration_min=30)
    assert unknown.status is AvailabilityStatus.UNKNOWN
    assert isinstance(unknown.deliverable_kw, Unknown)

    flex = query(metered, month=1, minute_of_day=15, duration_min=120)
    assert flex.status is AvailabilityStatus.FLEX and flex.deliverable_kw == 550
    answers = [zero, off, unknown, flex]
    assert len({a.status for a in answers}) == 4
    assert len({json.dumps(a.model_dump(mode="json")["deliverable_kw"]) for a in answers}) == 4


def test_the_same_cell_in_each_of_the_four_states_validates_and_hashes_differently() -> None:
    states: dict[str, dict[str, Any]] = {
        "flex": {"kind": "flex", "curve": [_point(30, 200)]},
        "zero_flex": {"kind": "zero_flex", "reason": "assessed: no headroom"},
        "unavailable": {
            "kind": "unavailable",
            "reason_code": "maintenance",
            "detail": "chiller 2 overhaul",
        },
        "unknown": {"kind": "unknown", "reason": "not yet assessed"},
    }
    hashes = {}
    for kind, availability in states.items():
        data = load(MODELLED)
        _cell(data, "summer", "afternoon")["availability"] = availability
        obj = _surface(data)
        assert query(obj, 7, 13 * 60, 30).status.value == kind
        hashes[kind] = obj.content_hash()
    assert len(set(hashes.values())) == 4


def test_a_cell_cannot_be_null_or_missing() -> None:
    data = load(MODELLED)
    _cell(data, "summer", "peak")["availability"] = None
    with pytest.raises(ValidationError):
        _surface(data)
    data = load(MODELLED)
    data["cells"] = [
        c for c in data["cells"] if (c["season_id"], c["band_id"]) != ("summer", "peak")
    ]
    with pytest.raises(ValidationError, match="exactly one entry per"):
        _surface(data)


def test_a_zero_cannot_hide_inside_a_flex_curve() -> None:
    """One form per state: 0 kW is a zero_flex cell, never a point (nor an empty curve)."""
    data = load(MODELLED)
    _cell(data, "summer", "afternoon")["availability"] = {
        "kind": "flex",
        "curve": [_point(30, 500), _point(60, 0)],
    }
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _surface(data)
    _cell(data, "summer", "afternoon")["availability"] = {"kind": "flex", "curve": []}
    with pytest.raises(ValidationError):
        _surface(data)


def test_instruction_check_keeps_zero_unavailable_and_unknown_apart() -> None:
    cases = {
        (MODELLED, "2027-07-01T13:00:00Z"): ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,
        (METERED, "2027-01-15T12:00:00Z"): ResponseReason.AVAILABILITY_UNAVAILABLE,
        (METERED, "2026-07-01T00:00:00Z"): ResponseReason.AVAILABILITY_UNKNOWN,
    }
    for (name, start), reason in cases.items():
        surface = _surface(load(name))
        ins = load(INSTRUCTION)
        ins["site_id"] = surface.site_id
        ins["availability"] = availability_ref(surface).model_dump(mode="json")
        ins["start"] = start
        ins["issued_at"] = "2026-06-01T00:00:00Z"
        ins["ack_deadline"] = "2026-06-01T00:10:00Z"
        check = check_instruction(surface, _instruction(ins))
        assert not check.ok and check.reasons == (reason,), (name, check.reasons)


# ---------------------------------------------------------------- the grid


def test_seasons_must_partition_the_twelve_months() -> None:
    data = load(MODELLED)
    data["seasons"][2]["months"] = [6, 7]
    with pytest.raises(ValidationError, match="partition the twelve months"):
        _surface(data)
    data = load(MODELLED)
    data["seasons"][2]["months"] = [5, 6, 7, 8]
    with pytest.raises(ValidationError, match="partition the twelve months"):
        _surface(data)
    data = load(MODELLED)
    data["seasons"].reverse()
    with pytest.raises(ValidationError, match="sorted by their first month"):
        _surface(data)


def test_bands_must_partition_the_local_day() -> None:
    for mutate, match in [
        (lambda d: d["bands"][1].update(start_minute=370), "partition the day"),
        (lambda d: d["bands"][1].update(start_minute=300), "partition the day"),
        (lambda d: d["bands"][-1].update(end_minute=1430), "minute 1440"),
        (lambda d: d["bands"][0].update(end_minute=0), "greater than or equal to 1"),
    ]:
        data = load(MODELLED)
        mutate(data)
        with pytest.raises(ValidationError, match=match):
            _surface(data)
    band = {"band_id": "wrap", "start_minute": 1320, "end_minute": 120}
    with pytest.raises(ValidationError, match="do not wrap midnight"):
        thermal_flex.TimeBand.model_validate_json(json.dumps(band))


def test_cells_are_in_one_canonical_order() -> None:
    data = load(MODELLED)
    data["cells"][0], data["cells"][1] = data["cells"][1], data["cells"][0]
    with pytest.raises(ValidationError, match="season order then band order"):
        _surface(data)


def test_the_curve_has_one_encoding() -> None:
    for curve, match in [
        ([_point(60, 500), _point(30, 400)], "strictly increasing"),
        ([_point(30, 500), _point(30, 400)], "strictly increasing"),
        ([_point(30, 500), _point(60, 500)], "strictly decrease"),
        ([_point(30, 400), _point(60, 500)], "strictly decrease"),
        ([_point(1441, 100)], "less than or equal to 1440"),
    ]:
        with pytest.raises(ValidationError, match=match):
            Flex.model_validate_json(json.dumps({"kind": "flex", "curve": curve}))


def test_deliverable_kw_reads_the_curve() -> None:
    flex = Flex.model_validate_json(
        json.dumps({"kind": "flex", "curve": [_point(30, 900), _point(120, 600), _point(408, 250)]})
    )
    assert [deliverable_kw(flex, d)[0] for d in (1, 30, 31, 120, 121, 408, 409)] == [
        900,
        900,
        600,
        600,
        250,
        250,
        0,
    ]
    assert deliverable_kw(flex, 409)[1] is None
    with pytest.raises(ValueError, match="at least 1"):
        deliverable_kw(flex, 0)


def test_rebound_is_self_consistent() -> None:
    ok = [_rebound(0, 0, 0), _rebound(0, 10, 5), _rebound(100, 60, 100), _rebound(1, 1, 1)]
    for r in ok:
        thermal_flex.Rebound.model_validate_json(json.dumps(r))
    for r, match in [
        (_rebound(10, 0, 0), "exceeds peak_kw"),
        (_rebound(0, 10, 0), "recovery time"),
        (_rebound(0, 0, 10), "recovery time"),
        (_rebound(101, 60, 100), "exceeds peak_kw"),
    ]:
        with pytest.raises(ValidationError, match=match):
            thermal_flex.Rebound.model_validate_json(json.dumps(r))


def test_reduction_cannot_exceed_a_known_cooling_capacity() -> None:
    data = load(MODELLED)
    data["cooling_electrical_capacity_kw"] = 800
    with pytest.raises(ValidationError, match="cooling_electrical_capacity_kw"):
        _surface(data)
    data["cooling_electrical_capacity_kw"] = {"kind": "unknown", "reason": "no nameplate yet"}
    _surface(data)


@pytest.mark.parametrize("value", [500.0, 500.5, "500", True])
def test_power_is_an_exact_integer_kw(value: Any) -> None:
    data = load(INSTRUCTION)
    data["reduction_kw"] = value
    with pytest.raises(ValidationError):
        _instruction(data)


def test_kw_converts_to_exact_mw() -> None:
    assert kw_to_mw(1250) * 4 == 5
    assert str(kw_to_mw(333)) == "333/1000"


# ---------------------------------------------------------------- IT-safety limits: declared


def test_binding_band_cannot_be_wider_than_ashrae() -> None:
    data = load(MODELLED)
    data["limits"]["binding"]["max_tenths_c"] = 271
    with pytest.raises(ValidationError, match="wider than the declared ASHRAE band"):
        _surface(data)


def test_binding_band_cannot_be_wider_than_a_known_sla() -> None:
    data = load(METERED)
    data["limits"]["ashrae"]["temperature"]["max_tenths_c"] = 250
    data["limits"]["binding"]["max_tenths_c"] = 240
    with pytest.raises(ValidationError, match="customer SLA"):
        _surface(data)
    data = load(MODELLED)
    data["limits"]["binding_source"] = "customer_sla"
    with pytest.raises(ValidationError, match="no customer SLA is declared"):
        _surface(data)


def test_limits_are_declared_not_derived_from_the_ashrae_class() -> None:
    # The contract holds no ASHRAE table: relabelling the class changes nothing it checks, and
    # the declared numbers stand as written.
    data = load(MODELLED)
    data["limits"]["ashrae"]["ashrae_class"] = "H1"
    obj = _surface(data)
    assert obj.limits.ashrae.temperature.max_tenths_c == 270
    assert obj.content_hash() != GOLDEN_HASHES[MODELLED]


def test_binding_band_is_measured_at_the_ashrae_point() -> None:
    """Review F2: a binding band of 5.0-45.0 C at supply air against H1 18-22 C at the IT inlet
    validated, because bands at different points were never compared."""
    data = load(METERED)
    assert data["limits"]["ashrae"]["temperature"] == {
        "point": "it_inlet",
        "min_tenths_c": 180,
        "max_tenths_c": 220,
    }
    data["limits"]["binding"] = {"point": "supply_air", "min_tenths_c": 50, "max_tenths_c": 450}
    with pytest.raises(ValidationError, match="the binding band must be measured at"):
        _surface(data)
    # Numerically inside the ASHRAE band but at another point: still refused.
    data["limits"]["binding"] = {"point": "supply_air", "min_tenths_c": 185, "max_tenths_c": 215}
    with pytest.raises(ValidationError, match="the binding band must be measured at"):
        _surface(data)


def test_a_known_sla_band_is_measured_at_the_ashrae_point() -> None:
    """Otherwise the SLA nesting would be skipped, as the ASHRAE nesting was (review F2)."""
    data = load(METERED)
    data["limits"]["customer_sla"]["temperature"]["point"] = "supply_air"
    with pytest.raises(ValidationError, match="a customer SLA band must be measured at"):
        _surface(data)


def test_binding_source_ashrae_means_the_ashrae_band() -> None:
    """Review F2: binding_source ashrae with 18.5-21.5 C, which is not the ASHRAE band."""
    data = load(METERED)
    data["limits"]["binding_source"] = "ashrae"
    assert (
        data["limits"]["binding"]["min_tenths_c"],
        data["limits"]["binding"]["max_tenths_c"],
    ) == (
        185,
        215,
    )
    with pytest.raises(ValidationError, match="binding_source is ashrae"):
        _surface(data)
    data["limits"]["binding"] = dict(data["limits"]["ashrae"]["temperature"])
    assert _surface(data).limits.binding_source is thermal_flex.LimitSource.ASHRAE


def test_binding_source_customer_sla_means_the_sla_band() -> None:
    """Review F2: binding_source customer_sla with the binding band equal to the ASHRAE band
    while the SLA (18.0-23.0 C) is looser."""
    data = load(METERED)
    data["limits"]["binding_source"] = "customer_sla"
    data["limits"]["binding"] = dict(data["limits"]["ashrae"]["temperature"])
    with pytest.raises(ValidationError, match="not the SLA band"):
        _surface(data)
    sla = {"point": "it_inlet", "min_tenths_c": 185, "max_tenths_c": 215}
    data["limits"]["customer_sla"]["temperature"] = sla
    data["limits"]["binding"] = dict(sla)
    assert _surface(data).limits.binding_source is thermal_flex.LimitSource.CUSTOMER_SLA


def test_binding_source_operator_margin_is_inside_both() -> None:
    data = load(METERED)
    assert data["limits"]["binding_source"] == "operator_margin"
    _surface(data)
    data["limits"]["binding"]["max_tenths_c"] = 221
    with pytest.raises(ValidationError, match="wider than the declared ASHRAE band"):
        _surface(data)


# ---------------------------------------------------------------- evidence class (IC-4 reuse)


def test_fixture_evidence_classes() -> None:
    assert _surface(load(MODELLED)).evidence_class is EvidenceClass.MODELLED
    assert _surface(load(MODELLED)).evidence.calibration_evidence == ()  # type: ignore[union-attr]
    assert _surface(load(METERED)).evidence_class is EvidenceClass.METERED
    assert thermal_flex.EVIDENCE_RANK == tuple(EvidenceClass)


def test_fixture_ashrae_source_names_no_edition_and_is_marked_unverified() -> None:
    """Review F4: the source cited the 5th edition beside a 60 % RH figure that may be another
    edition's. No number is changed; the label names no edition and says it is unverified."""
    for name in (MODELLED, METERED):
        source = _surface(load(name)).limits.ashrae.source
        assert "edition not stated" in source and "unverified" in source, name
        assert "5th" not in source and "2021" not in source, name


def test_metered_fixture_serves_only_observed_months_at_its_class() -> None:
    """Review F4: evidence_class is the floor for every flex and zero_flex cell, so a cell in a
    month the trial did not observe is unknown. The trial ran January to March."""
    surface = _surface(load(METERED))
    basis = surface.evidence
    assert isinstance(basis, thermal_flex.MeteredBasis)
    observed: set[int] = set()
    day = basis.observed_from
    while day < basis.observed_until:
        observed.add(day.month)
        day += timedelta(days=1)
    assert observed == {1, 2, 3}
    months = {s.season_id: set(s.months) for s in surface.seasons}
    graded = [c for c in surface.cells if c.availability.kind in ("flex", "zero_flex")]
    assert graded and all(months[c.season_id] <= observed for c in graded)
    for month in (10, 11, 12):
        assert query(surface, month, 60, 30).status is AvailabilityStatus.UNKNOWN, month


def test_evidence_class_must_match_its_basis() -> None:
    data = load(MODELLED)
    data["evidence_class"] = "metered"
    with pytest.raises(ValidationError, match="needs evidence of kind metered"):
        _surface(data)
    data = load(METERED)
    data["evidence_class"] = "trialled"
    with pytest.raises(ValidationError, match="needs evidence of kind trialled"):
        _surface(data)


def _trialled(refs: list[dict[str, Any]]) -> dict[str, Any]:
    data = load(METERED)
    data["evidence_class"] = "trialled"
    data["evidence"] = {
        "kind": "trialled",
        "trial_evidence": refs,
        "baseline_method": _ref("c"),
        "observed_from": "2026-01-05T00:00:00Z",
        "observed_until": "2026-01-20T00:00:00Z",
        "n_events": 6,
    }
    return data


def test_trialled_and_metered_need_ic4_evidence() -> None:
    assert _surface(_trialled([_ref("a"), _ref("b")])).evidence_class is EvidenceClass.TRIALLED
    with pytest.raises(ValidationError, match="at least 1 item"):
        _surface(_trialled([]))
    data = load(METERED)
    data["evidence"]["meter_evidence"] = []
    with pytest.raises(ValidationError, match="at least 1 item"):
        _surface(data)
    data = load(METERED)
    del data["evidence"]["baseline_method"]
    with pytest.raises(ValidationError, match="baseline_method"):
        _surface(data)
    data = load(METERED)
    data["evidence"]["meter_evidence"] = [{"kind": "unknown", "reason": "not registered"}]
    with pytest.raises(ValidationError):
        _surface(data)


def test_evidence_refs_are_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="sorted by evidence_hash"):
        _surface(_trialled([_ref("b"), _ref("a")]))
    with pytest.raises(ValidationError, match="sorted by evidence_hash"):
        _surface(_trialled([_ref("a"), _ref("a")]))


def test_synthetic_record_class_matches_the_site_id() -> None:
    data = load(MODELLED)
    data["site_id"] = "REAL-LOOKING-SITE"
    with pytest.raises(ValidationError, match="SYNTHETIC-"):
        _surface(data)
    data["record_class"] = "registered"
    assert _surface(data).record_class == "registered"


def test_contract_reuses_ic4_evidence_ref_and_issuer_role() -> None:
    f = thermal_flex.MeteredBasis.model_fields
    assert typing.get_args(f["meter_evidence"].annotation)[0] is EvidenceRef
    assert f["baseline_method"].annotation is EvidenceRef
    assert IssuerRole in typing.get_args(FlexInstruction.model_fields["on_behalf_of"].annotation)
    data = load(INSTRUCTION)
    data["on_behalf_of"] = "neura"  # Neura is not an IC-4 issuer role
    with pytest.raises(ValidationError):
        _instruction(data)
    data["on_behalf_of"] = {"kind": "not_applicable", "reason": "site cost dispatch"}
    data["stream"] = {"kind": "not_applicable", "reason": "site cost dispatch"}
    _instruction(data)


# ---------------------------------------------------------------- instruction and acknowledgement


def test_instruction_fixture_fits_its_surface() -> None:
    surface, ins = _surface(load(METERED)), _instruction(load(INSTRUCTION))
    check = check_instruction(surface, ins)
    assert check.ok and check.reasons == ()
    assert ins.availability.availability_hash == surface.content_hash()
    assert ins.end == datetime(2027, 1, 15, 2, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("start", "2027-01-15T00:15:00", "UTC offset"),
        ("start", "2027-01-15T00:15:30Z", "whole minute"),
        ("start", "2027-01-14T23:00:00Z", "cannot precede issued_at"),
        ("ack_deadline", "2027-01-14T23:30:00Z", "ack_deadline"),
        ("ack_deadline", "2027-01-15T00:16:00Z", "ack_deadline"),
        ("reduction_kw", 0, "greater than or equal to 1"),
        ("duration_min", 0, "greater than or equal to 1"),
        ("issuer_role", "dno", "neura_dispatch"),
        ("stream", "local flex", "pattern"),
    ],
)
def test_malformed_instructions_are_refused(field: str, value: Any, match: str) -> None:
    data = load(INSTRUCTION)
    data[field] = value
    with pytest.raises(ValidationError, match=match):
        _instruction(data)


def test_utc_offset_spelling_does_not_change_the_instruction_hash() -> None:
    data = load(INSTRUCTION)
    data["issued_at"] = "2027-01-15T00:30:00+01:00"
    data["start"] = "2027-01-15T01:15:00+01:00"
    assert _instruction(data).content_hash() == GOLDEN_HASHES[INSTRUCTION]


def test_instruction_check_reports_contract_reasons() -> None:
    surface = _surface(load(METERED))
    data = load(INSTRUCTION)
    data["reduction_kw"] = 551
    assert check_instruction(surface, _instruction(data)).reasons == (
        ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,
    )
    data = load(INSTRUCTION)
    data["issued_at"] = "2027-01-15T00:05:00Z"
    data["ack_deadline"] = "2027-01-15T00:06:00Z"
    assert check_instruction(surface, _instruction(data)).reasons == (
        ResponseReason.INSUFFICIENT_NOTICE,
    )
    data = load(INSTRUCTION)
    data["issued_at"], data["ack_deadline"] = "2027-05-01T00:00:00Z", "2027-05-01T00:05:00Z"
    data["start"] = "2027-05-02T00:15:00Z"
    reasons = check_instruction(surface, _instruction(data)).reasons
    assert reasons == tuple(
        sorted({ResponseReason.OUTSIDE_VALIDITY_WINDOW, ResponseReason.AVAILABILITY_UNKNOWN})
    )
    other = _surface(load(MODELLED))
    assert check_instruction(other, _instruction(load(INSTRUCTION))).reasons == (
        ResponseReason.WRONG_SITE_OR_SURFACE,
    )


def _event(surface: ThermalFlexAvailability, start: str, minutes: int, kw: int) -> FlexInstruction:
    """An instruction against ``surface`` for ``[start, start + minutes)``, issued an hour ahead."""
    at = datetime.fromisoformat(start)
    data = load(INSTRUCTION)
    data.update(
        site_id=surface.site_id,
        availability=availability_ref(surface).model_dump(mode="json"),
        start=start,
        duration_min=minutes,
        reduction_kw=kw,
        issued_at=(at - timedelta(hours=1)).isoformat(),
        ack_deadline=(at - timedelta(minutes=55)).isoformat(),
    )
    return _instruction(data)


def test_an_event_that_runs_into_an_unavailable_cell_is_refused() -> None:
    """Review F1: the metered host allows control 19:00-07:00 only; 07:00 on is unavailable."""
    surface = _surface(load(METERED))
    for start, minutes, kw in [
        ("2027-01-15T06:45:00Z", 120, 500),
        ("2027-01-15T06:59:00Z", 300, 200),
    ]:
        check = check_instruction(surface, _event(surface, start, minutes, kw))
        assert check.reasons == (ResponseReason.AVAILABILITY_UNAVAILABLE,), (start, check.reasons)
        assert isinstance(check.answer, thermal_flex.FlexAnswer)
        assert (check.answer.band_id, check.answer.status) == ("night", AvailabilityStatus.FLEX)
    # The event is half-open: ending exactly at 07:00 does not touch the day cell.
    assert check_instruction(surface, _event(surface, "2027-01-15T06:45:00Z", 15, 500)).ok
    assert check_instruction(surface, _event(surface, "2027-01-15T06:45:00Z", 16, 500)).reasons == (
        ResponseReason.AVAILABILITY_UNAVAILABLE,
    )


def test_an_event_that_runs_past_valid_until_is_refused() -> None:
    """Review F1: the modelled surface is valid until 2028-01-01T00:00Z (exclusive)."""
    surface = _surface(load(MODELLED))
    check = check_instruction(surface, _event(surface, "2027-12-31T23:59:00Z", 240, 300))
    assert check.reasons == (ResponseReason.OUTSIDE_VALIDITY_WINDOW,)
    assert check_instruction(surface, _event(surface, "2027-12-31T20:00:00Z", 240, 300)).ok
    assert check_instruction(
        surface, _event(surface, "2027-12-31T20:01:00Z", 240, 300)
    ).reasons == (ResponseReason.OUTSIDE_VALIDITY_WINDOW,)


def test_an_event_that_runs_into_an_unknown_cell_is_refused() -> None:
    """The stated rule: an unknown cell refuses an event that runs into it, as one that starts in
    it. 23:00 BST on 31 March is a metered winter cell; 1 April was never observed."""
    surface = _surface(load(METERED))
    check = check_instruction(surface, _event(surface, "2027-03-31T22:00:00Z", 90, 300))
    assert check.reasons == (ResponseReason.AVAILABILITY_UNKNOWN,)
    assert isinstance(check.answer, thermal_flex.FlexAnswer)
    assert check.answer.status is AvailabilityStatus.FLEX
    # Ending exactly at local midnight does not touch April.
    assert check_instruction(surface, _event(surface, "2027-03-31T22:00:00Z", 60, 300)).ok


def test_the_whole_event_is_read_on_the_gb_clock_across_a_clock_change() -> None:
    """On 28 March 2027 the clock jumps from 01:00 GMT to 02:00 BST. An event from 00:30 GMT for
    60 minutes is local 00:30-01:00 and then 02:00-02:30, so it touches the 02:00-02:30 band even
    though its local start plus 60 minutes is only 01:30."""
    data = load(MODELLED)
    bands = [("a", 0, 90), ("b", 90, 120), ("x", 120, 150), ("d", 150, 1440)]
    data["bands"] = [{"band_id": i, "start_minute": s, "end_minute": e} for i, s, e in bands]
    flex = {"kind": "flex", "curve": [_point(120, 500)]}
    blocked = {"kind": "unavailable", "reason_code": "maintenance", "detail": "02:00-02:30 test"}
    data["cells"] = [
        {"season_id": s["season_id"], "band_id": i, "availability": blocked if i == "x" else flex}
        for s in data["seasons"]
        for i, _, _ in bands
    ]
    surface = _surface(data)
    jump = check_instruction(surface, _event(surface, "2027-03-28T00:30:00Z", 60, 100))
    assert jump.reasons == (ResponseReason.AVAILABILITY_UNAVAILABLE,)
    # The same local hour a week earlier, with no clock change, stays in band a.
    assert check_instruction(surface, _event(surface, "2027-03-21T00:30:00Z", 60, 100)).ok


def test_time_of_day_is_read_on_the_gb_clock() -> None:
    surface = _surface(load(MODELLED))
    # 23:15 UTC on 1 July is 00:15 BST on 2 July: summer, night.
    a = query_at(surface, datetime(2027, 7, 1, 23, 15, tzinfo=UTC), 30)
    assert (a.season_id, a.band_id, a.deliverable_kw) == ("summer", "night", 400)
    # 23:15 UTC on 31 March 2027 is 00:15 BST on 1 April: shoulder, night.
    b = query_at(surface, datetime(2027, 3, 31, 23, 15, tzinfo=UTC), 30)
    assert (b.season_id, b.band_id) == ("shoulder", "night")
    with pytest.raises(ValueError, match="timezone-aware"):
        query_at(surface, datetime(2027, 7, 1, 23, 15), 30)


def test_acknowledgements_answer_the_instruction() -> None:
    ins = _instruction(load(INSTRUCTION))
    got = {k: verify_acknowledgement(ins, _ack(load(v))) for k, v in ACKS.items()}
    assert {k: (c.accepted_kw, c.late) for k, c in got.items()} == {
        "accepted": (500, False),
        "partial": (350, False),
        "rejected": (0, True),
    }


def test_a_partial_acceptance_must_be_below_the_instruction() -> None:
    ins = _instruction(load(INSTRUCTION))
    data = load(ACKS["partial"])
    data["response"]["accepted_kw"] = 500
    with pytest.raises(ValueError, match="less than the instructed kW"):
        verify_acknowledgement(ins, _ack(data))
    data["response"]["accepted_kw"] = 0
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _ack(data)


def test_an_acknowledgement_is_bound_to_its_instruction() -> None:
    ins = _instruction(load(INSTRUCTION))
    data = load(ACKS["accepted"])
    data["instruction_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="instruction_hash"):
        verify_acknowledgement(ins, _ack(data))
    data = load(ACKS["accepted"])
    data["instruction_id"] = "another"
    with pytest.raises(ValueError, match="another instruction_id"):
        verify_acknowledgement(ins, _ack(data))
    data = load(ACKS["accepted"])
    data["acknowledged_at"] = "2027-01-14T23:29:59Z"
    with pytest.raises(ValueError, match="cannot precede"):
        verify_acknowledgement(ins, _ack(data))


@pytest.mark.parametrize(
    "response",
    [
        {"kind": "rejected", "detail": "x"},
        {"kind": "rejected", "reason_code": "because", "detail": "x"},
        {"kind": "partially_accepted", "reason_code": "plant_fault", "detail": "x"},
        {"kind": "accepted", "accepted_kw": 500},
        {"kind": "maybe"},
    ],
)
def test_malformed_responses_are_refused(response: dict[str, Any]) -> None:
    data = load(ACKS["accepted"])
    data["response"] = response
    with pytest.raises(ValidationError):
        _ack(data)


def test_dump_and_reparse_is_a_fixed_point() -> None:
    for path in IC11_FIXTURES:
        model = _model_for(path.name)
        obj = model.model_validate_json(path.read_text(encoding="utf-8"))
        again = model.model_validate_json(obj.model_dump_json(indent=2))
        assert again == obj and content_hash(again) == GOLDEN_HASHES[path.name]


# ------------------------------------------------------------- 3. consumers without a plant model


_CONSUMER = """
import json, pathlib, sys
from datetime import UTC, datetime
from gridq.contracts.thermal_flex import (
    FlexAcknowledgement, FlexInstruction, ThermalFlexAvailability, check_instruction, query,
    query_at, verify_acknowledgement,
)
Surface, Ack = ThermalFlexAvailability, FlexAcknowledgement
fx = pathlib.Path(sys.argv[1])
read = lambda n: (fx / n).read_text(encoding="utf-8")
out = {"surfaces": {}}
for name in sorted(p.name for p in fx.glob("ic11_surface_*.synthetic.json")):
    s = Surface.model_validate_json(read(name))
    grid = {}
    for month in (1, 4, 7):
        for b in s.bands:
            a = query(s, month, b.start_minute, 30)
            kw = a.deliverable_kw if isinstance(a.deliverable_kw, int) else a.deliverable_kw.kind
            grid[f"{month}/{b.band_id}"] = [a.status.value, kw]
    out["surfaces"][name] = {
        "hash": s.content_hash(), "class": s.evidence_class.value, "grid": grid,
    }
metered = Surface.model_validate_json(read("ic11_surface_metered_trial.synthetic.json"))
ins = FlexInstruction.model_validate_json(read("ic11_instruction_winter_night.synthetic.json"))
out["check"] = check_instruction(metered, ins).ok
out["acks"] = {
    p.name: verify_acknowledgement(ins, Ack.model_validate_json(p.read_text())).accepted_kw
    for p in sorted(fx.glob("ic11_ack_*.synthetic.json"))
}
out["at"] = query_at(metered, datetime(2027, 1, 15, 0, 15, tzinfo=UTC), 120).deliverable_kw
out["engine_modules"] = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
out["heavy"] = sorted(
    m for m in ("numpy", "pandas", "linopy", "highspy", "xarray", "scipy") if m in sys.modules
)
print(json.dumps(out))
"""


def test_a_consumer_runs_on_the_fixtures_without_a_plant_model() -> None:
    """Fresh interpreter, contract package only: no GridQ engine or thermal module, no solver."""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, str(IC11_FIXTURES[0].parent)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine_modules"] == [] and out["heavy"] == [], out
    m = out["surfaces"][MODELLED]
    assert m["hash"] == GOLDEN_HASHES[MODELLED] and m["class"] == "modelled"
    assert m["grid"]["7/afternoon"] == ["zero_flex", 0]
    assert m["grid"]["1/night"] == ["flex", 900]
    t = out["surfaces"][METERED]
    assert t["hash"] == GOLDEN_HASHES[METERED] and t["class"] == "metered"
    assert t["grid"]["1/day"] == ["unavailable", "not_applicable"]
    assert t["grid"]["7/night"] == ["unknown", "unknown"]
    assert out["check"] is True and out["at"] == 550
    assert out["acks"] == {
        ACKS["accepted"]: 500,
        ACKS["partial"]: 350,
        ACKS["rejected"]: 0,
    }


# ---------------------------------------------------------------- mutation checks

#: (guard, exact source text, its in-memory replacement, the test that must go red).
MUTATIONS = [
    (
        "a flex point is at least 1 kW (zero is zero_flex)",
        "    duration_min: Duration\n    reduction_kw: PosKw\n",
        "    duration_min: Duration\n    reduction_kw: Kw\n",
        "test_a_zero_cannot_hide_inside_a_flex_curve",
    ),
    (
        "every (season, band) cell is present, in order",
        "        if got != expected:\n",
        "        if False:\n",
        "test_a_cell_cannot_be_null_or_missing",
    ),
    (
        "evidence class matches its basis",
        "        if self.evidence.kind != _BASIS_FOR[self.evidence_class]:\n",
        "        if False:\n",
        "test_evidence_class_must_match_its_basis",
    ),
    (
        "trialled evidence needs IC-4 refs",
        "    trial_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)\n",
        "    trial_evidence: tuple[EvidenceRef, ...] = Field(min_length=0)\n",
        "test_trialled_and_metered_need_ic4_evidence",
    ),
    (
        "binding band inside the ASHRAE band",
        "        if not binding.within(outer):\n",
        "        if False:\n",
        "test_binding_band_cannot_be_wider_than_ashrae",
    ),
    (
        "binding band inside a known SLA band (review F2)",
        "            if not binding.within(sla.temperature):\n",
        "            if False:\n",
        "test_binding_band_cannot_be_wider_than_a_known_sla",
    ),
    (
        "binding band at the ASHRAE point (review F2)",
        "        if binding.point is not outer.point:\n",
        "        if False:\n",
        "test_binding_band_is_measured_at_the_ashrae_point",
    ),
    (
        "a known SLA band at the ASHRAE point (review F2)",
        "            if sla.temperature.point is not outer.point:\n",
        "            if False:\n",
        "test_a_known_sla_band_is_measured_at_the_ashrae_point",
    ),
    (
        "binding_source ashrae is the ASHRAE band (review F2)",
        "        if self.binding_source is LimitSource.ASHRAE and binding != outer:\n",
        "        if False:\n",
        "test_binding_source_ashrae_means_the_ashrae_band",
    ),
    (
        "binding_source customer_sla is the SLA band (review F2)",
        "            if binding != sla.temperature:\n",
        "            if False:\n",
        "test_binding_source_customer_sla_means_the_sla_band",
    ),
    (
        "partial acceptance below the instruction",
        "    if isinstance(r, PartiallyAccepted) and r.accepted_kw >= instruction.reduction_kw:\n",
        "    if False:\n",
        "test_a_partial_acceptance_must_be_below_the_instruction",
    ),
    (
        "an event may not run past valid_until (review F1)",
        "    if not (surface.valid_from <= instruction.start and instruction.end <= "
        "surface.valid_until):\n",
        "    if not (surface.valid_from <= instruction.start < surface.valid_until):\n",
        "test_an_event_that_runs_past_valid_until_is_refused",
    ),
    (
        "an event may not run into an unavailable cell (review F1)",
        "    if AvailabilityStatus.UNAVAILABLE in crossed:\n",
        "    if answer.status is AvailabilityStatus.UNAVAILABLE:\n",
        "test_an_event_that_runs_into_an_unavailable_cell_is_refused",
    ),
    (
        "an event may not run into an unknown cell (review F1)",
        "    if AvailabilityStatus.UNKNOWN in crossed:\n",
        "    if answer.status is AvailabilityStatus.UNKNOWN:\n",
        "test_an_event_that_runs_into_an_unknown_cell_is_refused",
    ),
    (
        "the event walk does not step over a clock change (review F1)",
        "        if (t + timedelta(minutes=step)).astimezone(tz).utcoffset() != "
        "local.utcoffset():\n",
        "        if False:\n",
        "test_the_whole_event_is_read_on_the_gb_clock_across_a_clock_change",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    # The runner is shared with the IC-4/IC-1/IC-2 mutants (WP-BO-06): _contracts_support.
    return run_mutant(
        "gridq.contracts.thermal_flex", old, new, f"{Path(__file__).resolve()}::{test}"
    )


def test_mutation_runner_passes_on_the_unmutated_source() -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    for _, _, _, test in MUTATIONS:
        assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = Path(thermal_flex.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    # Exit code 1 is pytest's "tests ran and failed": the guard is load-bearing. A mutant that
    # cannot load exits 3, and a collection error exits 2, so neither passes as red.
    assert _run_mutant(old, new, test) == 1, guard
