"""WP-THERM-05 ``thermaflex_dc_controller``: the IT-safety objective mode beside the comfort/cost
mode (spec TF-02), with the thermal limits as hard constraints. Clean room (ADR-D7 option (c)):
no ThermaFlex code, document or parameter; the comfort/cost mode is the `ComfortCostPolicy` seam.

Decisions D-NEW-THERM05-1 to -5 (docs/decisions.md; numbered at merge):

1. **Overlay** (`comfort_cost`). In ``comfort_cost`` mode the command is the policy's command,
   the same object, every minute. In ``it_safety_flex`` mode with no event and no recovery it is
   too, unless the safety filter must change it (logged as a binding).
2. **The safety filter is the only safety argument** (`safety_filter`). Every IT-safety-mode
   command passes through it: whatever the instruction, the minute it commands keeps the plant in
   a forward-invariant safe set. Admission and the certificate decide what is promised.
3. **Admission by proof** (`DcController.receive`). One acknowledgement per instruction, at
   receipt, a pure function of the surface, the certificate, the booking ledger, the live flags
   and the instruction. Contract reasons come from IC-11 ``check_instruction``; live reasons from
   the controller; a fixed precedence chooses the one reason a rejection carries, and ``detail``
   lists every reason that applied. A kW above the declared one is partially accepted at the
   declared kW when nothing else fails. Accepted events go in a ledger of non-overlapping busy
   windows ``[start, max(end + declared recovery, start + restart_from))``; the strategy is the
   certificate's for the start slot (D167 R-c).
4. **Evidence** (tests/thermal/): an independent exact closed-loop plant, a seeded fuzzer over
   instruction classes, admission-bypass runs and mutation checks.
5. **Release, then repay** (`DcController.step`). Any trigger during a hold (telemetry loss, a
   faulted unit, a measured load or ambient outside the certified bounds, authority withdrawn, a
   host override) releases the cut at once; the stored heat is then repaid at the certified
   surplus. An operator switch to comfort/cost is deferred until every accepted event and its
   recovery are done; a withdrawal or override is immediate and leaves the stored heat to the
   plant's own control. Each instruction gets one controller-side `EventRecord` (IC-11 has no
   message for it; WP-THERM-07 will).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal
from zoneinfo import ZoneInfo

import numpy as np

from gridq.contracts._base import NotApplicable
from gridq.contracts.thermal_flex import (
    CLOCK,
    Accepted,
    FlexAcknowledgement,
    FlexInstruction,
    PartiallyAccepted,
    Rebound,
    Rejected,
    ResponseReason,
    ResponseTerms,
    ThermalFlexAvailability,
    check_instruction,
)
from gridq.thermal.certificate import (
    HORIZON_MIN,
    SLOTS_PER_DAY,
    Certificate,
    SlotEnvelope,
    StrategyPolicy,
)
from gridq.thermal.comfort_cost import ComfortCostPolicy, Observation, PlantCommand
from gridq.thermal.inputs import HALF_HOUR_MIN
from gridq.thermal.plant import NumericPlant
from gridq.thermal.safety_filter import Binding, SafeSet

__all__ = [
    "PRECEDENCE",
    "REST_K",
    "DcController",
    "EventRecord",
    "ObjectiveMode",
    "Phase",
    "StepRecord",
]

#: The one reason a rejection carries is the first that applies, in this order (D-NEW-THERM05-3).
PRECEDENCE: Final[tuple[ResponseReason, ...]] = (
    ResponseReason.WRONG_SITE_OR_SURFACE,
    ResponseReason.OUTSIDE_VALIDITY_WINDOW,
    ResponseReason.AVAILABILITY_UNAVAILABLE,
    ResponseReason.AVAILABILITY_UNKNOWN,
    ResponseReason.INSUFFICIENT_NOTICE,
    ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,
    ResponseReason.CONTROL_AUTHORITY_NOT_GRANTED,
    ResponseReason.HOST_OVERRIDE,
    ResponseReason.TELEMETRY_LOSS,
    ResponseReason.PLANT_FAULT,
    ResponseReason.IT_SAFETY_LIMIT,
    ResponseReason.REBOUND_RECOVERY_IN_PROGRESS,
)

#: Stored heat (as ``E / C``, K) below which the plant is at rest, with ``|d|`` within the settle
#: tolerance (D169 (5)). A method constant, not a site figure.
REST_K: Final = 1e-6
#: A live reading older than this is stale (telemetry loss).
STALE_AFTER: Final = timedelta(minutes=2)


class ObjectiveMode(StrEnum):
    COMFORT_COST = "comfort_cost"
    IT_SAFETY_FLEX = "it_safety_flex"


class Phase(StrEnum):
    IDLE = "idle"
    HOLDING = "holding"
    RECOVERING = "recovering"
    UNOBSERVED = "unobserved"


Outcome = Literal["booked", "holding", "completed", "curtailed", "abandoned"]


@dataclass
class EventRecord:
    """The controller's record of one accepted instruction (not an IC-11 message)."""

    instruction_id: str
    instruction_hash: str
    accepted_kw: int
    start: datetime
    end: datetime
    busy_until: datetime
    strategy: Literal["unload", "switch_off"]
    policy: StrategyPolicy
    cert_index: int
    outcome: Outcome = "booked"
    reason: ResponseReason | None = None
    minutes_held: int = 0
    minutes_curtailed: int = 0
    stored_heat_at_release_kj: float | None = None
    released_at: datetime | None = None
    recovered_at: datetime | None = None
    switch_off_refused: bool = False
    #: Booked by `DcController.force_event` (admission bypassed; no envelope is claimed).
    forced: bool = False


@dataclass(frozen=True)
class StepRecord:
    at: datetime
    mode: ObjectiveMode
    phase: Phase
    base: PlantCommand
    command: PlantCommand
    target_cut_kw: int
    binding: Binding | Literal["telemetry_loss"] | None
    instruction_id: str | None


def _minutes(d: timedelta) -> int:
    return int(d // timedelta(minutes=1))


def _ack_id(instruction_hash: str) -> str:
    return "ack:" + instruction_hash.removeprefix("sha256:")[:32]


@dataclass
class DcController:
    """The controller for one site. ``certificate`` and ``surface`` are one WP-THERM-03 build;
    the live load and ambient are judged against the certificate's own envelopes."""

    surface: ThermalFlexAvailability
    certificate: Certificate
    policy: ComfortCostPolicy
    controller_id: str
    mode: ObjectiveMode = ObjectiveMode.COMFORT_COST
    authority_granted: bool = False
    events: list[EventRecord] = field(default_factory=list)
    _acks: dict[str, tuple[str, FlexAcknowledgement]] = field(default_factory=dict)
    _last: Observation | None = None
    _pending_comfort: bool = False
    _recovering: EventRecord | None = None
    _host_override: bool = False

    def __post_init__(self) -> None:
        if self.mode is ObjectiveMode.IT_SAFETY_FLEX and not self.authority_granted:
            raise ValueError("the IT-safety mode needs control authority")
        self._safe = SafeSet(self.certificate.plant)
        self._surface_hash = self.surface.content_hash()

    @property
    def plant(self) -> NumericPlant:
        return self.certificate.plant

    # ----------------------------------------------------------------------------------------
    # Modes and authority (D-NEW-THERM05-5)
    # ----------------------------------------------------------------------------------------

    def request_mode(self, mode: ObjectiveMode) -> None:
        """An operator's mode request. Into IT-safety: at once (instructions are refused until
        the plant is at rest). Into comfort/cost: deferred until every accepted event and its
        recovery are done."""
        if mode is ObjectiveMode.IT_SAFETY_FLEX:
            if not self.authority_granted or self._host_override:
                raise ValueError("the IT-safety mode needs control authority and no host override")
            self.mode, self._pending_comfort = mode, False
        else:
            self._pending_comfort = self.mode is ObjectiveMode.IT_SAFETY_FLEX

    def withdraw_authority(self, at: datetime) -> None:
        """Control authority withdrawn: at once, the cut is released, waiting events are
        abandoned, and the controller stops writing (comfort/cost mode, the policy's commands).
        The stored heat is left to the plant's own control."""
        self.authority_granted = False
        self._drop_everything(at, ResponseReason.CONTROL_AUTHORITY_NOT_GRANTED)
        self.mode, self._pending_comfort = ObjectiveMode.COMFORT_COST, False

    def set_host_override(self, active: bool, at: datetime) -> None:
        """A host override: like a withdrawal, but the mode is kept; while it lasts every
        command is the policy's and every instruction is refused with ``host_override``."""
        self._host_override = active
        if active:
            self._drop_everything(at, ResponseReason.HOST_OVERRIDE)

    def _drop_everything(self, at: datetime, reason: ResponseReason) -> None:
        for ev in self.events:
            if ev.outcome in ("booked", "holding"):
                self._abandon(ev, at, reason)
        self._recovering = None

    # ----------------------------------------------------------------------------------------
    # Live state helpers
    # ----------------------------------------------------------------------------------------

    def _stored(self, obs: Observation) -> tuple[float, float]:
        p = self.plant
        e = p.c_loop * obs.loop_dev_c + p.c_hall * obs.inlet_dev_c
        return e, obs.inlet_dev_c - obs.loop_dev_c

    def _at_rest(self, obs: Observation) -> bool:
        e, d = self._stored(obs)
        return abs(e) / self.plant.c_total <= REST_K and abs(d) <= self.plant.settle_eps

    def _envelope(self, index: int) -> SlotEnvelope | None:
        if index not in self.certificate.certified:
            return None
        env = self.certificate.envelopes[index]
        return None if env.x_lo is None else env

    def _in_envelope(self, obs: Observation, index: int, minute: int) -> bool:
        """The measured load and ambient lie inside the bounds the certificate proved slot
        ``index`` on, ``minute`` minutes after a start in it (the half-hour dilation of
        `build_envelopes` makes that hold for any start in the slot)."""
        env = self._envelope(index)
        if env is None or not 0 <= minute < HORIZON_MIN:
            return False
        assert env.x_lo is not None and env.x_hi is not None
        assert env.t_lo is not None and env.t_hi is not None
        x, t = obs.heat_kwth, obs.ambient_c
        return bool(
            env.x_lo[minute] <= x <= env.x_hi[minute] and env.t_lo[minute] <= t <= env.t_hi[minute]
        )

    def _in_envelope_now(self, obs: Observation) -> bool:
        """Inside the envelope of the slot the reading falls in (its first half-hour)."""
        return self._in_envelope(obs, self._cert_index(obs.at), 0)

    def _event_in_envelope(self, ev: EventRecord, obs: Observation) -> bool:
        if ev.forced:  # admission bypassed (test hook): no envelope is claimed
            return True
        return self._in_envelope(obs, ev.cert_index, _minutes(obs.at - ev.start))

    def _bounds_over(self, ev: EventRecord, m0: int, m1: int) -> tuple[float, float]:
        """(lowest, highest) load the event's envelope allows over minutes ``[m0, m1)``."""
        env = self._envelope(ev.cert_index)
        if env is None or m1 <= m0:
            return math.nan, math.nan
        assert env.x_lo is not None and env.x_hi is not None
        return float(np.min(env.x_lo[m0:m1])), float(np.max(env.x_hi[m0:m1]))

    def _cert_index(self, start: datetime) -> int:
        local = start.astimezone(ZoneInfo(CLOCK))
        si = next(i for i, s in enumerate(self.surface.seasons) if local.month in s.months)
        return si * SLOTS_PER_DAY + (local.hour * 60 + local.minute) // HALF_HOUR_MIN

    # ----------------------------------------------------------------------------------------
    # Admission (D-NEW-THERM05-3)
    # ----------------------------------------------------------------------------------------

    def _live_reasons(self, received_at: datetime) -> tuple[set[ResponseReason], list[str]]:
        reasons: set[ResponseReason] = set()
        notes: list[str] = []
        if (
            self.mode is not ObjectiveMode.IT_SAFETY_FLEX
            or not self.authority_granted
            or self._pending_comfort
        ):
            reasons.add(ResponseReason.CONTROL_AUTHORITY_NOT_GRANTED)
            notes.append("the controller is not in the IT-safety objective mode")
        if not self.policy.baseline_equivalent:
            reasons.add(ResponseReason.CONTROL_AUTHORITY_NOT_GRANTED)
            notes.append("the comfort/cost policy is not the certified setpoint-holding baseline")
        if self._host_override:
            reasons.add(ResponseReason.HOST_OVERRIDE)
            notes.append("a host override is active")
        obs = self._last
        if obs is None or received_at - obs.at > STALE_AFTER or not obs.telemetry_ok:
            reasons.add(ResponseReason.TELEMETRY_LOSS)
            notes.append("no current loop and inlet readings")
            return reasons, notes
        if obs.faulted > 0:
            reasons.add(ResponseReason.PLANT_FAULT)
            notes.append(f"{obs.faulted} chiller(s) faulted")
        if not self._in_envelope_now(obs):
            reasons.add(ResponseReason.IT_SAFETY_LIMIT)
            notes.append("the measured load or ambient is outside the certified bounds")
        if not self._safe.contains(obs.loop_dev_c, obs.inlet_dev_c):
            reasons.add(ResponseReason.IT_SAFETY_LIMIT)
            notes.append("the plant is outside its safe set")
        busy = any(ev.busy_until > received_at for ev in self._ledger())
        if not busy and not self._at_rest(obs):
            if self._recovering is not None:
                reasons.add(ResponseReason.REBOUND_RECOVERY_IN_PROGRESS)
                notes.append("stored heat from a released event is still being repaid")
            else:
                reasons.add(ResponseReason.IT_SAFETY_LIMIT)
                notes.append("the plant is not at rest and no accepted event accounts for it")
        return reasons, notes

    def _ledger(self) -> list[EventRecord]:
        """Events holding a busy window: booked, held, or released and still recovering."""
        return [
            ev
            for ev in self.events
            if ev.outcome in ("booked", "holding", "completed", "curtailed")
            or (ev.outcome == "abandoned" and ev.released_at is not None)
        ]

    def receive(self, instruction: FlexInstruction, received_at: datetime) -> FlexAcknowledgement:
        """Answer an instruction, once, at receipt."""
        h = instruction.content_hash()
        if instruction.instruction_id in self._acks:
            seen, ack = self._acks[instruction.instruction_id]
            if seen != h:
                raise ValueError("an instruction id was reused for a different instruction")
            return ack
        if received_at < instruction.issued_at:
            raise ValueError("an instruction cannot be received before it was issued")

        chk = check_instruction(self.surface, instruction)
        reasons = set(chk.reasons)
        notes = [f"contract: {r.value}" for r in chk.reasons]
        terms = self.surface.response
        late = received_at > instruction.ack_deadline
        short = isinstance(terms, ResponseTerms) and instruction.start - received_at < timedelta(
            minutes=terms.min_notice_min
        )
        if late or short:
            reasons.add(ResponseReason.INSUFFICIENT_NOTICE)
            notes.append("received after the deadline" if late else "too little notice at receipt")
        declared = 0
        rebound: Rebound | None = None
        if not isinstance(chk.answer, NotApplicable):
            if isinstance(chk.answer.deliverable_kw, int):
                declared = chk.answer.deliverable_kw
            if isinstance(chk.answer.rebound, Rebound):
                rebound = chk.answer.rebound
        live, live_notes = self._live_reasons(received_at)
        reasons |= live
        notes += live_notes

        accepted_kw = min(instruction.reduction_kw, declared)
        booking: EventRecord | None = None
        partial_only = reasons == {ResponseReason.EXCEEDS_DECLARED_AVAILABILITY} and declared >= 1
        if (not reasons or partial_only) and rebound is not None:
            booking = self._plan(instruction, h, accepted_kw, rebound)
            if booking is None:
                reasons.add(ResponseReason.IT_SAFETY_LIMIT)
                notes.append("the certificate proves no strategy for this point")
            elif any(
                ev.start < booking.busy_until and booking.start < ev.busy_until
                for ev in self._ledger()
            ):
                reasons.add(ResponseReason.REBOUND_RECOVERY_IN_PROGRESS)
                notes.append("the event overlaps another accepted event or its recovery")
                booking = None

        response: Accepted | Rejected | PartiallyAccepted
        detail = "; ".join(sorted(set(notes))) or "accepted"
        if not reasons and booking is not None:
            response = Accepted(kind="accepted")
        elif reasons == {ResponseReason.EXCEEDS_DECLARED_AVAILABILITY} and booking is not None:
            response = PartiallyAccepted(
                kind="partially_accepted",
                accepted_kw=accepted_kw,
                reason_code=ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,
                detail=detail,
            )
        else:
            booking = None
            top = next(r for r in PRECEDENCE if r in reasons)
            response = Rejected(kind="rejected", reason_code=top, detail=detail)
        if booking is not None:
            self.events.append(booking)
        ack = FlexAcknowledgement(
            ack_id=_ack_id(h),
            instruction_id=instruction.instruction_id,
            instruction_hash=h,
            acknowledged_at=received_at,
            responder_id=self.controller_id,
            response=response,
        )
        self._acks[instruction.instruction_id] = (h, ack)
        return ack

    def _plan(
        self, instruction: FlexInstruction, h: str, kw: int, rebound: Rebound
    ) -> EventRecord | None:
        idx = self._cert_index(instruction.start)
        if idx not in self.certificate.certified:
            return None
        try:
            strategy = self.certificate.strategy_for(idx, instruction.duration_min, kw)
        except ValueError:
            return None
        policy = self.certificate.policy(idx, strategy)
        busy = instruction.end + timedelta(minutes=rebound.recovery_min)
        if policy.restart_from_min is not None:
            busy = max(busy, instruction.start + timedelta(minutes=policy.restart_from_min))
        return EventRecord(
            instruction_id=instruction.instruction_id,
            instruction_hash=h,
            accepted_kw=kw,
            start=instruction.start,
            end=instruction.end,
            busy_until=busy,
            strategy="switch_off" if strategy == "switch_off" else "unload",
            policy=policy,
            cert_index=idx,
        )

    def force_event(
        self,
        start: datetime,
        duration_min: int,
        kw: int,
        instruction_id: str,
        policy: StrategyPolicy | None = None,
    ) -> EventRecord:
        """Book an event without admission (unload, unless ``policy`` says otherwise): the test
        hook that shows the safety filter alone holds the limits (D-NEW-THERM05-4). Never used
        by `receive`; a forced event claims no envelope and recovers at the cap."""
        pol = policy or StrategyPolicy("unload", None, None, None)
        ev = EventRecord(
            instruction_id=instruction_id,
            instruction_hash="sha256:" + hashlib.sha256(instruction_id.encode()).hexdigest(),
            accepted_kw=kw,
            start=start,
            end=start + timedelta(minutes=duration_min),
            busy_until=start + timedelta(minutes=duration_min),
            strategy=pol.strategy,
            policy=pol,
            cert_index=self._cert_index(start),
            forced=True,
        )
        self.events.append(ev)
        return ev

    # ----------------------------------------------------------------------------------------
    # The minute (D-NEW-THERM05-2, -5)
    # ----------------------------------------------------------------------------------------

    def _abandon(self, ev: EventRecord, at: datetime, reason: ResponseReason) -> None:
        if ev.outcome == "holding":
            self._release(ev, at)
            self._recovering = ev
        ev.outcome, ev.reason = "abandoned", reason

    def _release(self, ev: EventRecord, at: datetime) -> None:
        """Release the cut; record the heat stored then (from the last good reading)."""
        ev.released_at = at
        if self._last is not None and self._last.telemetry_ok:
            ev.stored_heat_at_release_kj = self._stored(self._last)[0]

    def _holding(self) -> EventRecord | None:
        return next((ev for ev in self.events if ev.outcome == "holding"), None)

    def _start_guard(self, ev: EventRecord, obs: Observation) -> ResponseReason | None:
        if not obs.telemetry_ok:
            return ResponseReason.TELEMETRY_LOSS
        if obs.faulted > 0:
            return ResponseReason.PLANT_FAULT
        if not ev.forced and not self._at_rest(obs):
            return ResponseReason.IT_SAFETY_LIMIT
        return None

    def _hold_trigger(self, ev: EventRecord, obs: Observation) -> ResponseReason | None:
        if not obs.telemetry_ok:
            return ResponseReason.TELEMETRY_LOSS
        if obs.faulted > 0:
            return ResponseReason.PLANT_FAULT
        if not self._event_in_envelope(ev, obs):
            return ResponseReason.IT_SAFETY_LIMIT
        return None

    def _advance(self, obs: Observation) -> EventRecord | None:
        """Start, finish or abandon events at this minute; return the event held now."""
        now = obs.at
        ev = self._holding()
        if ev is not None and now >= ev.end:
            ev.outcome = "completed" if ev.minutes_curtailed == 0 else "curtailed"
            if ev.minutes_curtailed:
                ev.reason = ResponseReason.IT_SAFETY_LIMIT
            self._release(ev, now)
            self._recovering, ev = ev, None
        if ev is not None:
            why = self._hold_trigger(ev, obs)
            if why is not None:
                self._abandon(ev, now, why)
                ev = None
        if ev is None:
            for b in self.events:
                if b.outcome == "booked" and b.start <= now:
                    if now >= b.end:
                        b.outcome, b.reason = "abandoned", ResponseReason.IT_SAFETY_LIMIT
                        continue
                    why = self._start_guard(b, obs) or self._hold_trigger(b, obs)
                    if why is not None:
                        b.outcome, b.reason = "abandoned", why
                        continue
                    b.outcome, ev = "holding", b
                    self._recovering = None
                    break
        return ev

    def _units_for(self, ev: EventRecord, obs: Observation, base: PlantCommand) -> int:
        pol = ev.policy
        m = _minutes(obs.at - ev.start)
        if (
            ev.strategy == "switch_off"
            and pol.stop_at_min is not None
            and pol.keep_units is not None
            and pol.restart_from_min is not None
            and pol.stop_at_min <= m < pol.restart_from_min
            and not ev.switch_off_refused
        ):
            p = self.plant
            lo, hi = self._bounds_over(ev, m, pol.restart_from_min)
            if (
                pol.keep_units * p.unit_kwth >= hi
                and pol.keep_units * p.min_plr * p.unit_kwth <= lo
            ):
                return min(base.running_units, pol.keep_units)
            ev.switch_off_refused = True
        return base.running_units

    def _recovery_surplus(self, obs: Observation, e: float) -> float:
        """The certified surplus for this minute of the recovery (D169 (3)), at most the heat
        still stored."""
        p = self.plant
        u = p.u_cap
        ev = self._recovering
        if ev is not None and not ev.forced and ev.cert_index in self.certificate.certified:
            row = self.certificate.certified.index(ev.cert_index)
            scum = self.certificate.surplus[ev.strategy][row]
            m = _minutes(obs.at - ev.start)
            if 0 <= m < HORIZON_MIN and self._event_in_envelope(ev, obs):
                u = float(scum[m + 1] - scum[m]) / 60.0
        return max(0.0, min(u, e / 60.0))

    def step(self, obs: Observation) -> StepRecord:
        """One minute: the policy's command, or the IT-safety mode's filtered command."""
        base = self.policy.command(obs)
        if self.mode is ObjectiveMode.COMFORT_COST or self._host_override:
            self._last = obs
            return StepRecord(obs.at, self.mode, Phase.IDLE, base, base, 0, None, None)

        if obs.telemetry_ok:
            self._last = obs
        ev = self._advance(obs)
        self._last = obs
        if not obs.telemetry_ok:
            # Release and hold the baseline blind: E cannot grow (D-NEW-THERM05-5).
            self._maybe_hand_over(obs)
            return StepRecord(
                obs.at, self.mode, Phase.UNOBSERVED, base, base, 0, "telemetry_loss", None
            )
        p = self.plant
        x, t = obs.heat_kwth, obs.ambient_c
        e, _ = self._stored(obs)
        target = 0
        if ev is not None:
            phase = Phase.HOLDING
            units = self._units_for(ev, obs, base)
            target = ev.accepted_kw
            reg = p.curve.regime(np.array(t))
            delta = float(p.curve.deficit(np.array(x), np.array(t), reg, np.array(float(target))))
            candidate = delta if math.isfinite(delta) else p.f_rate * 2.0
        elif self._recovering is not None and e / p.c_total > REST_K:
            phase = Phase.RECOVERING
            units = base.running_units
            candidate = -self._recovery_surplus(obs, e)
        else:
            phase = Phase.IDLE
            units = base.running_units
            candidate = x - base.extraction_kwth
            if self._recovering is not None:
                self._recovering.recovered_at = obs.at
                self._recovering = None

        k = min(max(units, obs.running - obs.stoppable), obs.running + obs.startable)
        f_min = x - k * p.unit_kwth
        f_max = x - k * p.min_plr * p.unit_kwth
        res = self._safe.filter(obs.loop_dev_c, obs.inlet_dev_c, candidate, f_min, f_max)
        if ev is not None:
            if res.binding is not None and res.deficit_kw < candidate - 1e-9:
                ev.minutes_curtailed += 1
            else:
                ev.minutes_held += 1
        if phase is Phase.IDLE and res.deficit_kw == candidate and units == base.running_units:
            command = base
        else:
            command = PlantCommand(running_units=units, extraction_kwth=x - res.deficit_kw)
        self._maybe_hand_over(obs)
        return StepRecord(
            obs.at,
            self.mode,
            phase,
            base,
            command,
            target,
            res.binding,
            ev.instruction_id if ev is not None else None,
        )

    def _maybe_hand_over(self, obs: Observation) -> None:
        """Complete a deferred switch to comfort/cost once nothing is booked, held or owed."""
        if not self._pending_comfort or not obs.telemetry_ok:
            return
        if self._recovering is not None or any(
            ev.outcome in ("booked", "holding") for ev in self.events
        ):
            return
        self.mode, self._pending_comfort = ObjectiveMode.COMFORT_COST, False
