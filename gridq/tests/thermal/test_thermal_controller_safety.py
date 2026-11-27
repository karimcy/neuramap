"""WP-THERM-05 acceptance (1): in simulation the controller never violates the thermal limits
under any flex instruction (D-NEW-THERM05-4). SYNTHETIC fixture only.

The plant is `_thermal_closed_loop` (exact per minute, extremes inside the minute included,
imports nothing from ``gridq.thermal``); the limits are judged on its own numbers with zero
tolerance (1e-9 K round-off). Three kinds of evidence:

- **Through admission** (`test_no_instruction_breaks_a_limit_*`): a seeded fuzzer over
  instruction classes (the declared kW, above it, multiples of the plant's capacity, beyond the
  curve, the wrong surface, short notice, late receipt, an event asked for during another's
  recovery, back-to-back trains) on random and worst-case driver paths, on both fixture
  variants. Every run keeps every limit; every accepted or partly accepted kW is delivered every
  minute of its window with no curtailment, and its stored heat is repaid inside the declared
  recovery.
- **Admission bypassed** (`test_the_filter_alone_*`, `test_forced_events_*`): the safety filter
  alone, and the controller with events booked without admission at any kW and duration, keep
  every limit. Admission is not part of the safety argument (D-NEW-THERM05-2).
- **Cross-check** against the WP-THERM-03 Euler oracle for single accepted holds from rest.
"""

from __future__ import annotations

import functools
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import _thermal_fixture as fx
import _thermal_harness as h
import numpy as np
import pytest
from _thermal_closed_loop import Plant, staging, violations
from _thermal_oracle import Batch
from _thermal_oracle import simulate as euler

from gridq.contracts.thermal_flex import (
    Flex,
    FlexInstruction,
    ResponseReason,
    ThermalFlexAvailability,
    query_at,
)
from gridq.thermal.controller import EventRecord
from gridq.thermal.plant import resolve
from gridq.thermal.safety_filter import SafeSet

SEED = 20261127
TZ = ZoneInfo("Europe/London")
CLASSES = (
    "declared",
    "above_declared",
    "capacity_multiple",
    "beyond_curve",
    "wrong_surface",
    "short_notice",
    "late",
    "during_recovery",
    "train",
)
MIN = timedelta(minutes=1)


def _minutes(d: timedelta) -> int:
    return int(d // MIN)


def _cells(surface: ThermalFlexAvailability) -> list[tuple[Any, Any, Flex]]:
    out = []
    for c in surface.cells:
        if isinstance(c.availability, Flex):
            season = next(s for s in surface.seasons if s.season_id == c.season_id)
            band = next(b for b in surface.bands if b.band_id == c.band_id)
            out.append((season, band, c.availability))
    return out


def _start(rng: np.random.Generator, months: tuple[int, ...], lo: int, hi: int) -> datetime:
    """A whole-minute UTC start on a safe 2027 day in ``months``, local minute in [lo, hi)."""
    days = [d for d in h.SAFE_DAYS if (date(2027, 1, 1) + timedelta(days=d - 1)).month in months]
    d = date(2027, 1, 1) + timedelta(days=int(rng.choice(days)) - 1)
    m = int(rng.integers(lo, hi))
    return datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=TZ).astimezone(UTC)


def _declared(surface: ThermalFlexAvailability, start: datetime, d: int) -> tuple[int, int]:
    ans = query_at(surface, start, d)
    kw = ans.deliverable_kw if isinstance(ans.deliverable_kw, int) else 0
    rec = getattr(ans.rebound, "recovery_min", 0)
    return kw, rec


Sends = list[tuple[datetime, FlexInstruction]]


def _episode(
    variant: str, k: int, cls: str, rng: np.random.Generator
) -> tuple[Sends, dict[str, set[str]]]:
    """One episode's instructions (with receipt instants) and, per instruction, the response
    kinds the class allows."""
    surface = fx.built(variant).surface
    season, band, flex = _cells(surface)[int(rng.integers(len(_cells(surface))))]
    point = flex.curve[int(rng.integers(len(flex.curve)))]
    d = int(rng.integers(max(1, point.duration_min // 2), point.duration_min + 1))
    start = _start(rng, season.months, band.start_minute, band.end_minute)
    kw, rec = _declared(surface, start, d)
    ident = f"SYNTHETIC-{variant}-{k}"
    sends: Sends = []
    allow: dict[str, set[str]] = {}

    def send(ins: FlexInstruction, at: datetime | None = None, ok: set[str] | None = None) -> None:
        sends.append((at or ins.issued_at, ins))
        allow[ins.instruction_id] = ok or {"accepted"}

    if cls == "declared":
        send(h.instruction(surface, start, d, int(rng.integers(1, kw + 1)), ident))
    elif cls == "above_declared":
        ins = h.instruction(surface, start, d, kw + int(rng.integers(1, 60)), ident)
        send(ins, ok={"partially_accepted"})
    elif cls == "capacity_multiple":
        big = int(fx.raw(variant)["n"] * fx.raw(variant)["q_unit"] * rng.uniform(1.0, 4.0))
        send(h.instruction(surface, start, d, big, ident), ok={"partially_accepted"})
    elif cls == "beyond_curve":
        long = min(1440, flex.curve[-1].duration_min + int(rng.integers(1, 120)))
        send(h.instruction(surface, start, long, 1, ident), ok={"rejected"})
    elif cls == "wrong_surface":
        ins = h.instruction(surface, start, d, 1, ident, site_id="SYNTHETIC-ELSEWHERE")
        send(ins, ok={"rejected"})
    elif cls == "short_notice":
        send(h.instruction(surface, start, d, 1, ident, notice_min=3), ok={"rejected"})
    elif cls == "late":
        ins = h.instruction(surface, start, d, 1, ident, notice_min=30, deadline_min=10)
        send(ins, at=start - 3 * MIN, ok={"rejected"})
    elif cls == "during_recovery":
        first = h.instruction(surface, start, d, kw, ident + "a")
        send(first)
        # Asked for while the first is held or recovering, to start inside its busy window.
        at = first.end - int(rng.integers(0, min(d, 20) + 1)) * MIN + int(rng.integers(0, 3)) * MIN
        s2 = max(at + 6 * MIN, first.end + int(rng.integers(0, max(rec - 1, 1))) * MIN)
        if s2 < first.end + rec * MIN:
            k2, _ = _declared(surface, s2, 15)
            second = h.instruction(surface, s2, 15, max(k2, 1), ident + "b", notice_min=6)
            send(second, ok={"rejected"})
    else:  # train: two or three events, each from the end of the last one's declared recovery
        t, last = start, None
        for j in range(int(rng.integers(2, 4))):
            kj, rj = _declared(surface, t, d)
            if kj < 1 or t + (d + rj) * MIN > surface.valid_until:
                break
            ins = h.instruction(surface, t, d, kj, f"{ident}t{j}")
            # A switch-off event's restart window can outlast the declared recovery; the next
            # event is then refused with the rebound reason, never accepted unsafely.
            send(ins, ok={"accepted"} if last is None else {"accepted", "rejected"})
            last = ins
            t = ins.end + (rj + int(rng.integers(0, 3))) * MIN
    return sends, allow


def _run_episode(variant: str, k: int, cls: str, seed: int) -> tuple[h.Run, dict[str, set[str]]]:
    rng = np.random.default_rng(seed)
    sends, allow = _episode(variant, k, cls, rng)
    first = min(at for at, _ in sends)
    t0 = first.replace(second=0, microsecond=0) - 5 * MIN
    surface = fx.built(variant).surface
    tail = 0
    for _, ins in sends:
        _, rec = _declared(surface, ins.start, ins.duration_min)
        tail = max(tail, _minutes(ins.end - t0) + rec + 30)
    mode = h.PATHS[int(rng.integers(len(h.PATHS)))]
    return h.simulate(variant, t0, tail, mode, seed, sends=sends), allow


def _check(run: h.Run, allow: dict[str, set[str]], what: str) -> dict[str, int]:
    raw = run.plant.raw
    assert violations(raw, run.log) == [], what
    events = {e.instruction_id: e for e in run.controller.events}
    seen = {"accepted": 0, "partially_accepted": 0, "rejected": 0}
    for ident, ack in run.acks.items():
        kind = ack.response.kind
        seen[kind] += 1
        assert kind in allow[ident], (what, ident, ack.response)
        if kind == "rejected":
            assert ident not in events, what
            continue
        ev = events[ident]
        _assert_firm(run, ev, what)
    return seen


def _assert_firm(run: h.Run, ev: EventRecord, what: str) -> None:
    """The accepted kW in every minute of the window, no curtailment, repaid in time."""
    assert ev.outcome == "completed" and ev.minutes_curtailed == 0, (what, ev)
    i0 = _minutes(ev.start - run.t0)
    i1 = _minutes(ev.end - run.t0)
    cut = np.array([m.cut_kw for m in run.log[i0:i1]])
    assert cut.size == i1 - i0 and (cut >= ev.accepted_kw - 1e-6).all(), (what, ev, cut.min())
    assert ev.recovered_at is not None and ev.recovered_at <= ev.busy_until, (what, ev)


@functools.cache
def _fuzz(variant: str, n: int) -> list[tuple[str, dict[str, int], int]]:
    out = []
    for k in range(n):
        cls = CLASSES[k % len(CLASSES)]
        run, allow = _run_episode(variant, k, cls, SEED + 1000 * k)
        seen = _check(run, allow, f"{variant} episode {k} ({cls})")
        switch = sum(e.strategy == "switch_off" for e in run.controller.events)
        out.append((cls, seen, switch))
    return out


def test_no_instruction_breaks_a_limit_on_the_base_fixture() -> None:
    """Acceptance (1), base variant: 135 seeded episodes, 15 of each instruction class."""
    out = _fuzz("base", 15 * len(CLASSES))
    total = {k: sum(s[k] for _, s, _ in out) for k in ("accepted", "partially_accepted")}
    assert total["accepted"] >= 40 and total["partially_accepted"] >= 20, total
    assert {c for c, _, _ in out} == set(CLASSES)


def test_no_instruction_breaks_a_limit_on_the_tight_fixture() -> None:
    """Acceptance (1), tight variant (loop band, 5 K/h rate and recovery binding): one episode
    of each class, recoveries of about two days included."""
    out = _fuzz("tight", len(CLASSES))
    assert sum(s["accepted"] for _, s, _ in out) >= 3


def test_back_to_back_trains_are_all_accepted_at_their_declared_kw() -> None:
    """A queued train starting each event as the last one's declared recovery ends: every one
    is accepted (no switch-off restart window intervenes on these draws) and firm."""
    trains = [s for c, s, sw in _fuzz("base", 15 * len(CLASSES)) if c == "train" and not sw]
    assert trains and all(s["rejected"] == 0 for s in trains)
    assert sum(s["accepted"] for s in trains) >= 2 * len(trains)


# --------------------------------------------------------------------------------------------
# Admission bypassed
# --------------------------------------------------------------------------------------------


@functools.cache
def _numeric(variant: str) -> Any:
    res = resolve(fx.plant(fx.raw(variant), fx.MEDIA[variant]))
    assert not hasattr(res, "missing"), res
    return res.plant


PATTERNS = ("push_up", "push_down", "bang_bang", "random")


def _candidate(pattern: str, i: int, rng: np.random.Generator, scale: float) -> float:
    if pattern == "push_up":
        return scale if i < 150 else -scale
    if pattern == "push_down":
        return -scale if i < 150 else scale
    if pattern == "bang_bang":
        return scale if (i // 45) % 2 == 0 else -scale
    return float(rng.uniform(-scale, scale))


def _filter_alone(
    variant: str, pattern: str, seed: int, scale: float | None = None, minutes: int = 240
) -> list[str]:
    """The filter alone over an adversarial candidate stream, on the plant's own staging."""
    raw = fx.raw(variant)
    safe = SafeSet(_numeric(variant))
    drv = fx.variant_drivers(variant)
    assert drv.temperature_lo_c is not None and drv.temperature_hi_c is not None
    rng = np.random.default_rng(seed)
    j0 = int(rng.integers(0, len(drv) - 200))
    if scale is None:
        scale = float(rng.choice([50.0, 400.0, 3.0 * raw["n"] * raw["q_unit"]]))
    hh = [j0 + i // 30 for i in range(minutes)]
    xs = [float(rng.uniform(drv.heat_lo_kwth[j], drv.heat_hi_kwth[j])) for j in hh]
    ts = [float(rng.uniform(drv.temperature_lo_c[j], drv.temperature_hi_c[j])) for j in hh]
    plant = Plant(raw, xs[0])
    for i in range(minutes):
        x, t = xs[i], ts[i]
        want = staging(raw, x)
        k = min(max(want, plant.running - plant.stoppable(i)), plant.running + plant.startable(i))
        f_min, f_max = x - k * raw["q_unit"], x - k * raw["min_plr"] * raw["q_unit"]
        res = safe.filter(plant.w, plant.a, _candidate(pattern, i, rng, scale), f_min, f_max)
        assert res.binding != "no_safe_command", (variant, pattern, i)
        plant.step(i, x, t, k, x - res.deficit_kw)
    return violations(raw, plant.log)


@pytest.mark.parametrize("variant", fx.VARIANTS)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_the_filter_alone_holds_every_limit(variant: str, pattern: str) -> None:
    """Any candidate stream (pushed to either limit, bang-bang, random; up to three times the
    plant's capacity), filtered, keeps every limit: the filter is the safety argument."""
    for seed in range(5):
        assert _filter_alone(variant, pattern, SEED + seed) == [], (variant, pattern, seed)


QUICK = {"base": ("push_up", "push_down"), "tight": ("push_up", "bang_bang")}


@pytest.mark.parametrize("variant", fx.VARIANTS)
def test_the_filter_alone_quick(variant: str) -> None:
    """The node the filter's mutation checks run (each guard's adversarial pattern)."""
    for pattern in QUICK[variant]:
        assert _filter_alone(variant, pattern, SEED, scale=5000.0) == [], (variant, pattern)


def test_the_filter_drives_each_limit_to_its_edge() -> None:
    """Not vacuous: the adversarial streams bring the hall, the loop and the floor to within
    0.05 K of their limits, and the tight fixture's 15-minute rate window to within 10 %."""

    def extremes(variant: str, pattern: str) -> tuple[float, float, float, float]:
        raw = fx.raw(variant)
        safe = SafeSet(_numeric(variant))
        plant = Plant(raw, 600.0)
        rng = np.random.default_rng(SEED)
        k = staging(raw, 600.0)
        f_min, f_max = 600.0 - k * raw["q_unit"], 600.0 - k * raw["min_plr"] * raw["q_unit"]
        for i in range(240):
            res = safe.filter(plant.w, plant.a, _candidate(pattern, i, rng, 5000.0), f_min, f_max)
            plant.step(i, 600.0, 15.0, k, 600.0 - res.deficit_kw)
        a = np.array([0.0] + [m.a_end for m in plant.log])
        rate15 = float(np.max(np.abs(a[15:] - a[:-15])))
        return (
            max(m.a_hi for m in plant.log) - safe.hall_top,
            max(m.w_hi for m in plant.log) - safe.loop_top,
            min(m.a_lo for m in plant.log) - safe.floor,
            rate15 / (raw["rate_c_per_h"] / 4.0),
        )

    hall, _, floor, _ = extremes("base", "push_up")
    assert -0.05 <= hall <= 0, hall
    _, _, floor, _ = extremes("base", "push_down")
    assert 0 <= floor <= 0.05, floor
    _, loop, _, _ = extremes("tight", "push_up")
    assert -0.05 <= loop <= 0, loop
    _, _, _, rate = extremes("tight", "bang_bang")
    assert 0.9 <= rate <= 1.0 + 1e-9, rate


def test_forced_events_at_any_kw_never_break_a_limit() -> None:
    """Events booked without admission, at up to three times the plant's capacity and up to a
    day long, on both variants and every path kind: the run keeps every limit, and the
    controller curtails (and records) whatever the plant cannot hold."""
    rng = np.random.default_rng(SEED)
    curtailed = 0
    for k in range(18):
        variant = fx.VARIANTS[k % 2]
        raw = fx.raw(variant)
        start = _start(rng, tuple(range(1, 12)), 0, 1440)
        d = int(rng.choice([15, 60, 240, 720]))
        kw = int(rng.integers(1, 3 * raw["n"] * raw["q_unit"]))
        mode = h.PATHS[k % 3]
        run = h.simulate(variant, start - 5 * MIN, d + 60, mode, k, forced=[(start, d, kw)])
        assert violations(raw, run.log) == [], (variant, start, d, kw, mode)
        ev = run.controller.events[0]
        curtailed += ev.minutes_curtailed > 0
    assert curtailed >= 9, "most forced events ask for more than the plant can hold"


# --------------------------------------------------------------------------------------------
# Cross-check against the WP-THERM-03 Euler oracle
# --------------------------------------------------------------------------------------------


def test_held_trajectories_agree_with_the_euler_oracle() -> None:
    """For single accepted unload holds from rest, the closed-loop peaks of the loop and the
    inlet over the hold agree with the independent Euler oracle within its own error bound."""
    surface = fx.built("base").surface
    cert = fx.built("base").certificate
    rng = np.random.default_rng(SEED + 7)
    checked = 0
    for k in range(40):
        season, band, flex = _cells(surface)[k % len(_cells(surface))]
        point = flex.curve[int(rng.integers(len(flex.curve)))]
        start = _start(rng, season.months, band.start_minute, band.end_minute)
        d = point.duration_min
        kw, _ = _declared(surface, start, d)
        ins = h.instruction(surface, start, d, kw, f"SYNTHETIC-X{k}")
        run = h.simulate(
            "base", start - 35 * MIN, 35 + d, "random", k, sends=[(ins.issued_at, ins)]
        )
        ev = run.controller.events[0]
        if ev.strategy != "unload":
            continue
        hold = run.log[35 : 35 + d]
        x = np.array([run.xs[35 : 35 + d]])
        t = np.array([run.ts[35 : 35 + d]])
        out = euler(
            fx.RAW,
            Batch(
                x=x,
                t=t,
                duration=np.array([d]),
                kw=np.array([float(kw)]),
                switch_off=np.array([False]),
                keep=np.array([0.0]),
                u_cap=cert.plant.u_cap,
            ),
        )
        assert out.held.all()
        assert abs(max(m.a_hi for m in hold) - out.inlet_dev_max[0]) <= out.tol_k, k
        assert abs(max(m.w_hi for m in hold) - out.loop_max[0]) <= out.tol_k, k
        checked += 1
    assert checked >= 20


def test_a_live_reason_rejects_an_instruction_the_contract_accepts() -> None:
    """Control: the same declared instruction is accepted at rest and refused with
    IT_SAFETY_LIMIT when the plant is not at rest and no accepted event accounts for it."""
    surface = fx.built("base").surface
    rng = np.random.default_rng(SEED + 3)
    season, band, _ = _cells(surface)[0]
    start = _start(rng, season.months, band.start_minute, band.end_minute - 60)
    kw, _ = _declared(surface, start, 30)
    ins = h.instruction(surface, start, 30, kw, "SYNTHETIC-LIVE")

    def warm(i: int, ctrl: Any, plant: Plant) -> None:
        plant.a, plant.w = 0.5, 0.5

    sends = [(ins.issued_at, ins)]
    run = h.simulate("base", start - 40 * MIN, 45, "random", 1, sends=sends, hooks={9: warm})
    ack = run.acks["SYNTHETIC-LIVE"]
    assert ack.response.kind == "rejected"
    assert ack.response.reason_code is ResponseReason.IT_SAFETY_LIMIT
