"""The design is consistent with the IC-5 edge-cloud contract (1.0.0) and the edge write
allow-list: stages, ANM, triggers, fail-safe and the composition of the two refusals."""

from __future__ import annotations

import json
import typing
from typing import Any

import _edge_support  # noqa: F401  (puts tests/contracts on sys.path)
import _ic5_fixtures as fx
import pytest
from _ic3_fixtures import FIXTURES_IC3

from gridq.contracts._base import not_applicable, unknown
from gridq.contracts.dispatch_envelope import DispatchEnvelope, resolve
from gridq.contracts.edge_cloud import (
    CONTRACT_VERSION,
    TRIGGERS,
    ControlStage,
    LinkProfile,
    RejectReason,
    SetpointSchedule,
    TriggerCode,
    check_setpoint_schedule,
    fail_safe_target,
)
from gridq.edge_design import query
from gridq.edge_design.invariants import WRITABLE_BY_ZONE, WRITE_MIN_STAGE
from gridq.edge_design.model import (
    COMMS_LOSS_TRIGGERS,
    BmsConduitMode,
    ControlKind,
    PayloadKind,
    PointClass,
    WriteScope,
    stage_at_least,
)
from gridq.edge_design.release import DESIGN

DC = FIXTURES_IC3 / "ic3_envelope_dc_thermal.synthetic.json"
STAGES = list(ControlStage)
L2, L3 = ControlStage.L2_BESS_CLOSED_LOOP, ControlStage.L3_CHILLER_BAND


def _schedule(chiller: bool, execution: str = "closed_loop") -> tuple[Any, SetpointSchedule]:
    """A one-period schedule on the IC-3 data-centre fixture, as IC-5's own staging test builds."""
    dc = DispatchEnvelope.model_validate_json(DC.read_text())
    band = {"point": "it_inlet", "low_tenths_c": 200, "high_tenths_c": 240}
    period = {
        "start": "2027-01-04T12:00:00Z",
        "end": "2027-01-04T12:30:00Z",
        "bess": {"kind": "idle"},
        "site_import_cap_kw": 1_000,
        "chiller_band": band if chiller else {"kind": "not_applicable", "reason": "x"},
        "expected": {
            "soe_end_kwh": 1_000,
            "site_import_kw": 0,
            "site_export_kw": 0,
            "temperature": band,
        },
    }
    data = fx.schedule_body()
    data.update(
        site_id=dc.site_id,
        execution=execution,
        resolved_hash=dc.resolved_hash,
        soe_start_kwh=1_000,
        periods=[period],
    )
    data["envelope"]["envelope_hash"] = dc.content_hash()
    return resolve(dc), SetpointSchedule.model_validate_json(json.dumps(data))


def _lowest_accepted(chiller: bool) -> ControlStage:
    timeline, schedule = _schedule(chiller)
    return next(s for s in STAGES if check_setpoint_schedule(timeline, schedule, s).accepted)


def test_ic5_contract_version_is_the_one_checked() -> None:
    assert CONTRACT_VERSION == DESIGN.ic5_contract_version == "1.0.0"


def test_point_class_stages_match_ic5() -> None:
    """The lowest stage at which IC-5 accepts a closed-loop battery schedule is the design's stage
    for battery writes, and likewise for a chiller band."""
    assert _lowest_accepted(chiller=False) is L2
    assert _lowest_accepted(chiller=True) is L3
    for pc in (PointClass.BESS_SETPOINT, PointClass.IMPORT_LIMIT):
        assert WRITE_MIN_STAGE[pc] is _lowest_accepted(chiller=False)
    for pc in (
        PointClass.CHILLER_SETPOINT,
        PointClass.CHILLER_SCHEDULE,
        PointClass.CHILLER_BAND_OFFER,
    ):
        assert WRITE_MIN_STAGE[pc] is _lowest_accepted(chiller=True)
    for c in DESIGN.all_conduits():
        if isinstance(c.authority, WriteScope):
            need = max((WRITE_MIN_STAGE[p] for p in c.authority.point_classes), key=STAGES.index)
            assert c.authority.min_stage is need, c.conduit_id


def test_anm_is_edge_local_in_design_and_contract() -> None:
    assert typing.get_args(LinkProfile.model_fields["anm_authority"].annotation) == ("edge_local",)
    carriers = [c.conduit_id for c in DESIGN.all_conduits() if PayloadKind.ANM_SIGNAL in c.payloads]
    assert carriers == ["C5"]
    c5 = next(c for c in DESIGN.base_conduits if c.conduit_id == "C5")
    assert isinstance(c5.authority, WriteScope) and c5.authority.tighten_only
    assert c5.authority.min_stage is ControlStage.L0_READ_ONLY
    for mode in BmsConduitMode:
        for stage in STAGES:
            assert all(
                b.point_class is not PointClass.ANM_SIGNAL
                for b in query.write_bindings(stage, mode)
            )


def test_every_comms_loss_trigger_maps_to_a_conduit() -> None:
    link_like = {c for c, s in TRIGGERS.items() if c.value.endswith("_lost")}
    assert link_like == set(COMMS_LOSS_TRIGGERS)
    for mode in BmsConduitMode:
        for t in COMMS_LOSS_TRIGGERS:
            got = query.conduits_for_trigger(t, mode)
            if mode is BmsConduitMode.NO_BMS_CONDUIT and t is TriggerCode.THERMAL_COMMS_LOST:
                assert got == ()
            else:
                assert got, (mode, t)
    c1 = next(c for c in DESIGN.base_conduits if c.conduit_id == "C1")
    assert ControlKind.STORE_AND_FORWARD in c1.controls


def test_write_bindings_follow_stage_and_mode() -> None:
    for mode in BmsConduitMode:
        ceiling = next(v.max_stage for v in DESIGN.bms_variants if v.mode is mode)
        for stage in STAGES:
            got = query.write_bindings(stage, mode)
            classes = {b.point_class for b in got}
            if not stage_at_least(stage, L2):
                assert got == (), (mode, stage)
                continue
            assert {PointClass.BESS_SETPOINT, PointClass.IMPORT_LIMIT} <= classes
            chiller = classes & WRITABLE_BY_ZONE["facility_cooling"]
            assert bool(chiller) == (stage is L3 and ceiling is L3), (mode, stage)
            for b in got:
                assert b.point_class in WRITABLE_BY_ZONE[b.target_zone]
    offer = query.write_bindings(L3, BmsConduitMode.DIODE_WITH_BAND_OFFER)
    assert {b.point_class for b in offer if b.target_zone != "bess_oem"} == {
        PointClass.CHILLER_BAND_OFFER
    }


def test_fail_safe_writes_match_the_design() -> None:
    """IC-5's fail-safe writes nothing below L2, and the design binds no write below L2. Chillers
    go to the BMS default only where the stage, capped by the BMS mode, reaches L3."""
    dc = DispatchEnvelope.model_validate_json(DC.read_text())
    for mode in BmsConduitMode:
        for stage in STAGES:
            eff = query.effective_stage(stage, mode)
            held = fail_safe_target(dc, unknown("x"), eff)
            assert (held.writes == "enforce") == bool(query.write_bindings(stage, mode))
            assert fail_safe_target(not_applicable("x"), unknown("x"), eff).writes == "none"
            chiller_authority = held.chillers == "bms_default"
            assert chiller_authority == (eff is L3), (mode, stage)
    assert query.effective_stage(L3, unknown("mode not supplied")) is L2


def test_clock_and_chain_triggers_are_edge_zone_controls() -> None:
    edge = query.zone("edge_control")
    assert ControlKind.CLOCK_SYNC_MONITORED in edge.controls
    assert TRIGGERS[TriggerCode.CLOCK_UNSYNCHRONISED].category == "integrity"
    assert TRIGGERS[TriggerCode.CHAIN_INTEGRITY_FAILURE].category == "integrity"
    for cid in ("C1", "C2"):
        c = next(c for c in DESIGN.base_conduits if c.conduit_id == cid)
        assert ControlKind.HASH_CHAINED_FRAMES in c.controls


@pytest.mark.parametrize("mode", list(BmsConduitMode))
def test_a_chiller_band_is_outside_write_scope_without_an_l3_mode(mode: BmsConduitMode) -> None:
    timeline, chiller = _schedule(chiller=True)
    assert check_setpoint_schedule(timeline, chiller, L3).accepted
    l3_mode = next(v.max_stage for v in DESIGN.bms_variants if v.mode is mode) is L3
    expected = () if l3_mode else (RejectReason.OUTSIDE_WRITE_SCOPE,)
    assert query.design_write_scope_reasons(chiller, mode) == expected
    _, battery = _schedule(chiller=False)
    assert query.design_write_scope_reasons(battery, mode) == ()
    _, shadow = _schedule(chiller=True, execution="shadow")
    assert query.design_write_scope_reasons(shadow, mode) == ()


def test_an_unknown_mode_has_no_chiller_scope() -> None:
    _, chiller = _schedule(chiller=True)
    reasons = query.design_write_scope_reasons(chiller, unknown("not supplied"))
    assert reasons == (RejectReason.OUTSIDE_WRITE_SCOPE,)


def test_the_cloud_reaches_the_site_by_c1_only() -> None:
    assert [c.conduit_id for c in DESIGN.all_conduits() if "neura_cloud" in c.zones] == ["C1"]
    assert query.conduits_between("site_dmz", "neura_cloud")[0].initiator == "a"
