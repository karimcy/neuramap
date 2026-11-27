"""WP-THERM-05 mode transitions, "release, then repay" (D209). SYNTHETIC fixture.

Each trigger arrives 25 minutes into an accepted one-hour hold. Every run keeps every limit
(zero tolerance, the independent plant), the cut is released within the step the trigger is
seen in, and the event record carries the trigger's IC-11 reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _thermal_fixture as fx
import _thermal_harness as h
from _thermal_closed_loop import Plant, violations

from gridq.contracts.thermal_flex import Rejected, ResponseReason, query_at
from gridq.thermal.certificate import StrategyPolicy
from gridq.thermal.controller import DcController, ObjectiveMode, Phase

R = ResponseReason
MIN = timedelta(minutes=1)
START = datetime(2027, 1, 20, 2, 0, tzinfo=UTC)
LEAD = 35
TRIGGER = LEAD + 25


def _hold(
    hooks: dict[int, Any] | None = None,
    blind: Any = None,
    bend: Any = None,
    extra: h.Sends = (),
    minutes: int = 240,
) -> h.Run:
    surface = fx.built("base").surface
    kw = query_at(surface, START, 60).deliverable_kw
    assert isinstance(kw, int)
    ins = h.instruction(surface, START, 60, kw, "SYNTHETIC-HOLD")
    sends = [(ins.issued_at, ins), *extra]
    return h.simulate(
        "base",
        START - LEAD * MIN,
        minutes,
        "random",
        5,
        sends=sends,
        hooks=hooks,
        blind=blind,
        bend=bend,
    )


def _released_at_once(run: h.Run) -> None:
    s = run.steps[TRIGGER]
    assert s.phase is not Phase.HOLDING
    assert run.xs[TRIGGER] - s.command.extraction_kwth <= 1e-9, "no cut after the trigger"


def _event(run: h.Run) -> Any:
    return next(e for e in run.controller.events if e.instruction_id == "SYNTHETIC-HOLD")


def test_the_undisturbed_hold_completes() -> None:
    run = _hold()
    ev = _event(run)
    assert ev.outcome == "completed" and ev.recovered_at is not None
    assert run.steps[TRIGGER].phase is Phase.HOLDING
    assert violations(fx.RAW, run.log) == []


def test_telemetry_loss_releases_holds_blind_and_repays_when_it_returns() -> None:
    run = _hold(blind=lambda i: TRIGGER <= i < TRIGGER + 10)
    ev = _event(run)
    assert ev.outcome == "abandoned" and ev.reason is R.TELEMETRY_LOSS
    assert run.steps[TRIGGER].phase is Phase.UNOBSERVED
    assert all(s.command is s.base for s in run.steps[TRIGGER : TRIGGER + 10])
    assert ev.stored_heat_at_release_kj is not None and ev.recovered_at is not None
    assert violations(fx.RAW, run.log) == []


def test_a_chiller_fault_releases_and_repays_on_the_capacity_left() -> None:
    def trip(i: int, ctrl: DcController, plant: Plant) -> None:
        plant.fail(i, plant.on.index(True))

    run = _hold(hooks={TRIGGER: trip})
    ev = _event(run)
    assert ev.outcome == "abandoned" and ev.reason is R.PLANT_FAULT
    _released_at_once(run)
    assert ev.recovered_at is not None
    assert violations(fx.RAW, run.log) == []


def test_a_load_outside_the_certified_envelope_releases_and_repays_at_the_cap() -> None:
    run = _hold(bend=lambda i, x, t: (x + 150.0, t) if i >= TRIGGER else (x, t))
    ev = _event(run)
    assert ev.outcome == "abandoned" and ev.reason is R.IT_SAFETY_LIMIT
    _released_at_once(run)
    assert ev.recovered_at is not None
    assert violations(fx.RAW, run.log) == []


def test_withdrawn_authority_releases_at_once_and_stops_writing() -> None:
    def withdraw(i: int, ctrl: DcController, plant: Plant) -> None:
        ctrl.withdraw_authority(START + (i - LEAD) * MIN)

    run = _hold(hooks={TRIGGER: withdraw})
    ev = _event(run)
    assert ev.outcome == "abandoned" and ev.reason is R.CONTROL_AUTHORITY_NOT_GRANTED
    assert run.controller.mode is ObjectiveMode.COMFORT_COST
    assert all(s.command is s.base for s in run.steps[TRIGGER:])
    _released_at_once(run)
    assert violations(fx.RAW, run.log) == []


def test_a_host_override_releases_refuses_and_passes_the_policy_through() -> None:
    def on(i: int, ctrl: DcController, plant: Plant) -> None:
        ctrl.set_host_override(True, START + (i - LEAD) * MIN)

    def off(i: int, ctrl: DcController, plant: Plant) -> None:
        ctrl.set_host_override(False, START + (i - LEAD) * MIN)

    surface = fx.built("base").surface
    late = h.instruction(surface, START + 200 * MIN, 30, 1, "SYNTHETIC-LATE", notice_min=170)
    run = _hold(hooks={TRIGGER: on, TRIGGER + 15: off}, extra=[(late.issued_at, late)])
    ev = _event(run)
    assert ev.outcome == "abandoned" and ev.reason is R.HOST_OVERRIDE
    assert run.controller.mode is ObjectiveMode.IT_SAFETY_FLEX
    assert all(s.command is s.base for s in run.steps[TRIGGER : TRIGGER + 15])
    ack = run.acks["SYNTHETIC-LATE"]
    assert isinstance(ack.response, Rejected) and ack.response.reason_code is R.HOST_OVERRIDE
    assert violations(fx.RAW, run.log) == []


def test_an_operator_switch_to_comfort_cost_waits_for_the_event_and_its_repayment() -> None:
    def switch(i: int, ctrl: DcController, plant: Plant) -> None:
        ctrl.request_mode(ObjectiveMode.COMFORT_COST)

    surface = fx.built("base").surface
    other = h.instruction(surface, START + 150 * MIN, 30, 1, "SYNTHETIC-OTHER", notice_min=80)
    run = _hold(hooks={TRIGGER: switch}, extra=[(other.issued_at, other)])
    ev = _event(run)
    assert ev.outcome == "completed" and ev.minutes_curtailed == 0
    assert all(s.mode is ObjectiveMode.IT_SAFETY_FLEX for s in run.steps[: LEAD + 60])
    assert run.controller.mode is ObjectiveMode.COMFORT_COST
    assert ev.recovered_at is not None
    ack = run.acks["SYNTHETIC-OTHER"]
    assert isinstance(ack.response, Rejected)
    assert ack.response.reason_code is R.CONTROL_AUTHORITY_NOT_GRANTED
    assert violations(fx.RAW, run.log) == []


def test_entering_the_it_safety_mode_refuses_flex_until_the_plant_is_at_rest() -> None:
    res = fx.built("base")
    ctrl = h.controller("base", mode=ObjectiveMode.COMFORT_COST)
    ctrl.authority_granted = True

    def warm(i: int, c: DcController, plant: Plant) -> None:
        plant.w, plant.a = 0.4, 0.4

    def enter(i: int, c: DcController, plant: Plant) -> None:
        c.request_mode(ObjectiveMode.IT_SAFETY_FLEX)

    ins = h.instruction(res.surface, START, 60, 1, "SYNTHETIC-ENTER", notice_min=25)
    run = h.simulate(
        "base",
        START - LEAD * MIN,
        40,
        "random",
        2,
        ctrl=ctrl,
        hooks={0: warm, 5: enter},
        sends=[(ins.issued_at, ins)],
    )
    assert run.steps[6].mode is ObjectiveMode.IT_SAFETY_FLEX
    ack = run.acks["SYNTHETIC-ENTER"]
    assert isinstance(ack.response, Rejected) and ack.response.reason_code is R.IT_SAFETY_LIMIT


#: The tight fixture runs four units at ~590 kWth of load; three carry only 469.5 kWth.
SWITCH_START = datetime(2027, 3, 10, 12, 0, tzinfo=UTC)


def _too_few_units() -> h.Run:
    ctrl = h.controller("tight")
    ctrl.force_event(
        SWITCH_START, 60, 1, "SYNTHETIC-SWITCH", policy=StrategyPolicy("switch_off", 0, 3, 35)
    )
    return h.simulate("tight", SWITCH_START - 5 * MIN, 120, "random", 4, ctrl=ctrl)


def test_a_switch_off_that_would_leave_too_few_units_keeps_every_limit() -> None:
    """The node the lockout mutation runs: stopping a unit the kept units cannot replace for
    the restart lockout would break the limits; the controller refuses the stop."""
    assert violations(fx.RAW_TIGHT, _too_few_units().log) == []


def test_a_switch_off_that_would_leave_too_few_units_is_refused() -> None:
    run = _too_few_units()
    assert run.controller.events[0].switch_off_refused
    assert all(m.running == 4 for m in run.log)
