"""WP-THERM-05 admission ("admit by proof", D-NEW-THERM05-3). SYNTHETIC fixture only.

One acknowledgement per instruction, at receipt: the reason precedence, partial acceptance read
back through IC-11 ``verify_acknowledgement``, replay, the booking ledger, late receipt, and the
switch-off strategy end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _thermal_fixture as fx
import _thermal_harness as h
import numpy as np
import pytest
from _thermal_closed_loop import violations

from gridq.contracts.thermal_flex import (
    FlexInstruction,
    PartiallyAccepted,
    Rejected,
    ResponseReason,
    query_at,
    verify_acknowledgement,
)
from gridq.thermal.comfort_cost import Observation, SetpointHoldingPolicy
from gridq.thermal.controller import PRECEDENCE, DcController, ObjectiveMode

R = ResponseReason
MIN = timedelta(minutes=1)
#: A winter weekday night in 2027 (GMT): local 02:00, band 0000-0800.
START = datetime(2027, 1, 20, 2, 0, tzinfo=UTC)


def _surface() -> Any:
    return fx.built("base").surface


def _declared(start: datetime, d: int) -> int:
    kw = query_at(_surface(), start, d).deliverable_kw
    assert isinstance(kw, int)
    return kw


def _obs(at: datetime, **over: Any) -> Observation:
    """A reading at rest, inside the envelope (the middle of the driver half-hour)."""
    j = int((at - h.SHIFT - fx.START) // timedelta(minutes=30))
    drv = fx.drivers()
    assert drv.temperature_lo_c is not None and drv.temperature_hi_c is not None
    fields: dict[str, Any] = {
        "at": at,
        "heat_kwth": float(drv.heat_lo_kwth[j] + drv.heat_hi_kwth[j]) / 2,
        "ambient_c": float(drv.temperature_lo_c[j] + drv.temperature_hi_c[j]) / 2,
        "loop_dev_c": 0.0,
        "inlet_dev_c": 0.0,
        "running": 3,
        "startable": 1,
        "stoppable": 3,
        "faulted": 0,
    }
    fields.update(over)
    return Observation(**fields)


def _ready(at: datetime, **over: Any) -> DcController:
    ctrl = h.controller("base")
    ctrl.step(_obs(at, **over))
    return ctrl


def _ins(start: datetime = START, d: int = 60, kw: int | None = None, **kw_args: Any) -> Any:
    return h.instruction(
        _surface(), start, d, _declared(start, d) if kw is None else kw, "SYNTHETIC-A1", **kw_args
    )


def _reason(ack: Any) -> ResponseReason:
    assert isinstance(ack.response, Rejected), ack.response
    return ack.response.reason_code


def test_an_instruction_at_the_declared_kw_is_accepted_and_bound_to_its_hash() -> None:
    ins = _ins()
    at = ins.issued_at
    ack = _ready(at).receive(ins, at)
    assert ack.response.kind == "accepted"
    assert ack.acknowledged_at == at and ack.responder_id == "SYNTHETIC-THERM05-CTRL"
    assert verify_acknowledgement(ins, ack).accepted_kw == ins.reduction_kw
    assert not verify_acknowledgement(ins, ack).late


def test_above_the_declared_kw_is_partly_accepted_at_the_declared_kw() -> None:
    declared = _declared(START, 60)
    ins = _ins(kw=declared + 7)
    ack = _ready(ins.issued_at).receive(ins, ins.issued_at)
    assert isinstance(ack.response, PartiallyAccepted)
    assert ack.response.reason_code is R.EXCEEDS_DECLARED_AVAILABILITY
    assert verify_acknowledgement(ins, ack).accepted_kw == declared


def test_beyond_the_curve_is_rejected_not_partly_accepted() -> None:
    ins = _ins(d=1000, kw=1)
    assert _reason(_ready(ins.issued_at).receive(ins, ins.issued_at)) is (
        R.EXCEEDS_DECLARED_AVAILABILITY
    )


def _setup(name: str) -> tuple[DcController, FlexInstruction, datetime]:
    """One controller and instruction per row of the precedence table: each makes the named
    reason the highest that applies, with at least one lower reason also applying."""
    at = START - 30 * MIN
    if name == "wrong_site":
        ins = _ins(site_id="SYNTHETIC-ELSEWHERE", notice_min=3)
        return _ready(ins.issued_at), ins, ins.issued_at
    if name == "validity":
        s = datetime(2027, 12, 5, 2, 0, tzinfo=UTC)
        ins = h.instruction(_surface(), s, 60, 500, "SYNTHETIC-A1")
        return _ready(START), ins, ins.issued_at
    if name == "notice":
        ins = _ins(kw=500, notice_min=3)
        return _ready(ins.issued_at), ins, ins.issued_at
    if name == "exceeds":
        ins = _ins(d=1000, kw=5)
        ctrl = _ready(at)
        ctrl.request_mode(ObjectiveMode.COMFORT_COST)
        return ctrl, ins, at
    if name == "authority":
        ctrl = _ready(at, faulted=1)
        ctrl.withdraw_authority(at)
        return ctrl, _ins(), at
    if name == "override":
        ctrl = _ready(at, faulted=1)
        ctrl.set_host_override(True, at)
        return ctrl, _ins(), at
    if name == "telemetry":
        return _ready(at, loop_dev_c=float("nan"), faulted=1), _ins(), at
    if name == "fault":
        return _ready(at, faulted=1, inlet_dev_c=0.4, loop_dev_c=0.4), _ins(), at
    if name == "it_safety":
        return _ready(at, heat_kwth=2000.0), _ins(), at
    assert name == "rebound"
    ctrl = _ready(at)
    first = _ins()
    assert ctrl.receive(first, at).response.kind == "accepted"
    second = h.instruction(_surface(), START + 70 * MIN, 30, 1, "SYNTHETIC-A2", notice_min=120)
    return ctrl, second, at


TABLE = [
    ("wrong_site", R.WRONG_SITE_OR_SURFACE),
    ("validity", R.OUTSIDE_VALIDITY_WINDOW),
    ("notice", R.INSUFFICIENT_NOTICE),
    ("exceeds", R.EXCEEDS_DECLARED_AVAILABILITY),
    ("authority", R.CONTROL_AUTHORITY_NOT_GRANTED),
    ("override", R.HOST_OVERRIDE),
    ("telemetry", R.TELEMETRY_LOSS),
    ("fault", R.PLANT_FAULT),
    ("it_safety", R.IT_SAFETY_LIMIT),
    ("rebound", R.REBOUND_RECOVERY_IN_PROGRESS),
]


@pytest.mark.parametrize("name,reason", TABLE, ids=[t[0] for t in TABLE])
def test_the_highest_reason_that_applies_is_the_one_returned(
    name: str, reason: ResponseReason
) -> None:
    ctrl, ins, at = _setup(name)
    ack = ctrl.receive(ins, at)
    assert _reason(ack) is reason
    assert isinstance(ack.response, Rejected) and ack.response.detail


def test_the_table_covers_the_whole_precedence_except_unavailable_and_unknown() -> None:
    """The fixture surface has no unavailable or unknown cell; their order against the
    contract's other reasons is fixed by `PRECEDENCE` itself."""
    covered = {r for _, r in TABLE}
    assert set(PRECEDENCE) - covered == {R.AVAILABILITY_UNAVAILABLE, R.AVAILABILITY_UNKNOWN}
    assert len(PRECEDENCE) == len(set(PRECEDENCE)) == len(R)


def test_the_detail_names_every_reason_that_applied() -> None:
    ctrl, ins, at = _setup("fault")
    ack = ctrl.receive(ins, at)
    assert isinstance(ack.response, Rejected)
    assert "faulted" in ack.response.detail and "not at rest" in ack.response.detail


def test_a_replayed_instruction_returns_the_stored_acknowledgement() -> None:
    ins = _ins()
    ctrl = _ready(ins.issued_at)
    first = ctrl.receive(ins, ins.issued_at)
    again = ctrl.receive(ins, ins.issued_at + 5 * MIN)
    assert again is first
    assert len(ctrl.events) == 1


def test_a_reused_id_or_a_receipt_before_issue_is_an_error_not_an_acknowledgement() -> None:
    ins = _ins()
    ctrl = _ready(ins.issued_at)
    ctrl.receive(ins, ins.issued_at)
    other = _ins(kw=1)
    with pytest.raises(ValueError, match="reused"):
        ctrl.receive(other, other.issued_at)
    later = h.instruction(_surface(), START + 3 * 60 * MIN, 30, 1, "SYNTHETIC-A9")
    with pytest.raises(ValueError, match="before it was issued"):
        ctrl.receive(later, later.issued_at - MIN)


def test_a_receipt_after_the_deadline_is_refused_for_notice() -> None:
    ins = _ins(notice_min=30, deadline_min=10)
    at = ins.ack_deadline + MIN
    ack = _ready(at).receive(ins, at)
    assert _reason(ack) is R.INSUFFICIENT_NOTICE
    assert isinstance(ack.response, Rejected) and "deadline" in ack.response.detail
    assert verify_acknowledgement(ins, ack).late


@pytest.mark.parametrize(
    "offset,ok",
    [(-90, False), (-60, True), (60, False), (89, False), (90, True)],
    ids=["ends-inside", "ends-at-start", "starts-inside-hold", "starts-in-recovery", "after"],
)
def test_the_ledger_refuses_overlap_on_either_side(offset: int, ok: bool) -> None:
    """An accepted 60-minute event with a 30-minute declared recovery holds [start, +90 min):
    a second event overlapping it on either side is refused with the rebound reason."""
    ctrl = _ready(START - 120 * MIN)
    first = _ins(notice_min=200)
    assert ctrl.receive(first, START - 120 * MIN).response.kind == "accepted"
    assert ctrl.events[0].busy_until == START + 90 * MIN
    s2 = START + offset * MIN
    d2 = 90 if offset == -90 else 30
    second = h.instruction(_surface(), s2, d2, 1, "SYNTHETIC-A2", notice_min=offset + 150)
    ack = ctrl.receive(second, START - 120 * MIN)
    if ok:
        assert ack.response.kind == "accepted"
    else:
        assert _reason(ack) is R.REBOUND_RECOVERY_IN_PROGRESS


def test_a_policy_that_is_not_the_baseline_gets_no_flex() -> None:
    class Shifted(SetpointHoldingPolicy):
        @property
        def baseline_equivalent(self) -> bool:
            return False

    res = fx.built("base")
    ctrl = DcController(
        surface=res.surface,
        certificate=res.certificate,
        policy=Shifted(res.certificate.plant),
        controller_id="SYNTHETIC-THERM05-CTRL",
        mode=ObjectiveMode.IT_SAFETY_FLEX,
        authority_granted=True,
    )
    ins = _ins()
    ctrl.step(_obs(ins.issued_at))
    assert _reason(ctrl.receive(ins, ins.issued_at)) is R.CONTROL_AUTHORITY_NOT_GRANTED


def test_the_switch_off_strategy_end_to_end() -> None:
    """The fixture's one switch-off point (winter, 23:30-24:00 local, 31 minutes or more at
    6 kW): accepted, the certificate's strategy, two units kept from minute 20 to 35, the kW
    held every minute and every limit kept."""
    start = datetime(2027, 2, 9, 23, 35, tzinfo=UTC)
    d = 40
    kw = _declared(start, d)
    ins = h.instruction(_surface(), start, d, kw, "SYNTHETIC-SW")
    for mode in h.PATHS:
        run = h.simulate(
            "base", start - 35 * MIN, 35 + d + 60, mode, 3, sends=[(ins.issued_at, ins)]
        )
        ev = run.controller.events[0]
        assert run.acks["SYNTHETIC-SW"].response.kind == "accepted"
        assert ev.strategy == "switch_off" and not ev.switch_off_refused
        assert ev.busy_until == max(ev.end + 30 * MIN, start + 35 * MIN)
        running = [m.running for m in run.log[35 + 20 : 35 + 35]]
        assert running == [2] * 15
        assert violations(fx.RAW, run.log) == []
        cut = np.array([m.cut_kw for m in run.log[35 : 35 + d]])
        assert (cut >= kw - 1e-6).all() and ev.outcome == "completed"
