"""The firmness certificate (D165, D166, D167, D169): the largest kW each start slot can
hold for each duration, at every start in the slot, within every binding limit.

**Envelopes (W-b).** For a season and a local half-hour slot, take every start in the slot on
every day of the season with a full 48-hour look-ahead. For each minute offset ``m`` after the
start, the upper envelope is the largest value any of those starts sees at ``m``, dilated over the
two half-hours a start anywhere inside its half-hour can fall in; the lower envelope likewise.
The thermal state runs on the worst deficit the envelopes allow.

**Dominating deficit (F-b, D-7 (1)).** At minute ``m`` and reduction ``K`` the deficit is at most
``F_dom(m) = max over T corners of delta(x_lo(m), T, K)``. The loop-hall system is cooperative
(Metzler) and the deficit enters positively, so the states under ``F_dom`` bound the states
under any real path inside the envelopes from above (comparison principle). Checks per event
minute, each monotone in the duration and the kW:

- **plant floor**: the extraction after the cut stays at or above the running units' minimum
  part load, and the running units can carry the load (`headroom`);
- **rate**: ``F_dom <= rho (C_w + C_a) / 3600`` keeps ``|dT_inlet/dt| <= rho`` (D-7 (2));
- **loop band**: the exact minute-end loop state (its maximum within a minute is at an end);
- **hall band**: ``a <= E / (C_w + C_a)`` while the imbalance ``d <= 0`` (always, from rest
  under ``F >= 0``), and ``E`` only falls during recovery, so ``E(D) / C`` bounds the hall for the
  whole event and recovery (D-7 (1), (5));
- **recovery**: the stored heat ``E(D)`` is removed by the recovery surplus (spare running
  capacity, capped at the rate and undershoot limits) within ``HORIZON_MIN`` of the start.

**Strategies (R-c, D-5 (1)).** ``unload``: the plant's own staging keeps running; the units
unload. ``switch_off``: needs both anti-cycling times. Staging is frozen at the start; at
``min_run`` after the start (when every unit running at the start has run its minimum) units
above ``m_keep`` stop; they stay off for ``min_off``. ``m_keep`` units carry the full load
through the lockout, so no deficit is forced by it. Each slot takes the better firm strategy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

import numpy as np

from gridq.contracts.thermal_flex import CLOCK, Rebound, Season
from gridq.thermal.inputs import HALF_HOUR_MIN, DriverSeries
from gridq.thermal.plant import NumericPlant, curve_validity, max_slope, settle_minutes

__all__ = [
    "HORIZON_MIN",
    "MAX_DURATION_MIN",
    "MIN_DURATION_MIN",
    "SLOTS_PER_DAY",
    "Certificate",
    "SlotAnswer",
    "SlotEnvelope",
    "StrategyPolicy",
    "build_envelopes",
    "certify",
]

#: Structural horizon (D-7 (4)): every event and its recovery fit in 48 h from the start.
HORIZON_MIN: Final = 2880
MAX_DURATION_MIN: Final = 1440
#: The shortest declared hold (D165 D-b).
MIN_DURATION_MIN: Final = 15
SLOTS_PER_DAY: Final = 48
_HH: Final = HORIZON_MIN // HALF_HOUR_MIN  # 96 half-hours
#: At most this many kW levels per sweep; larger plants are certified on a coarser integer grid
#: (a declared kW is always one that was certified).
K_GRID_MAX: Final = 256

Reason = Literal["plant_floor", "rate", "loop_band", "hall_band", "recovery"]
_REASONS: Final[tuple[Reason, ...]] = ("plant_floor", "rate", "loop_band", "hall_band", "recovery")
_WORDS: Final = {
    "plant_floor": "the running chillers' minimum part load (or their capacity) binds",
    "rate": "the IT-inlet rate-of-change limit binds",
    "loop_band": "the chilled-water setpoint band binds",
    "hall_band": "the IT-inlet limit (binding band, with humidity) binds",
    "recovery": "the stored heat cannot be recovered within the 48 h horizon",
}


# --------------------------------------------------------------------------------------------
# Envelopes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class SlotEnvelope:
    """One (season, half-hour slot): its starts and its minute envelopes over the horizon."""

    season_index: int
    slot: int
    #: Series indices of the starts (each a half-hour in this slot, in this season).
    starts: np.ndarray
    unknown_reason: str | None
    x_lo: np.ndarray | None = None
    x_hi: np.ndarray | None = None
    t_lo: np.ndarray | None = None
    t_hi: np.ndarray | None = None


def _local_calendar(drivers: DriverSeries) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Local month, slot and ordinal date of each half-hour of the series."""
    tz = ZoneInfo(CLOCK)
    n = len(drivers)
    month = np.empty(n, dtype=np.int16)
    slot = np.empty(n, dtype=np.int16)
    day = np.empty(n, dtype=np.int32)
    t = drivers.start_utc
    step = timedelta(minutes=HALF_HOUR_MIN)
    for i in range(n):
        loc = t.astimezone(tz)
        month[i] = loc.month
        slot[i] = (loc.hour * 60 + loc.minute) // HALF_HOUR_MIN
        day[i] = loc.toordinal()
        t += step
    return month, slot, day


def _minute_envelope(windows: np.ndarray, upper: bool) -> np.ndarray:
    """(starts, 97) half-hour windows -> (2880,) minute envelope with the half-hour dilation."""
    hh = windows.max(axis=0) if upper else windows.min(axis=0)
    pair = np.maximum(hh[:-1], hh[1:]) if upper else np.minimum(hh[:-1], hh[1:])
    return np.repeat(pair, HALF_HOUR_MIN)


def build_envelopes(
    drivers: DriverSeries, seasons: Sequence[Season], min_days_per_month: int
) -> list[SlotEnvelope]:
    """Envelopes for every (season, slot), season-major."""
    month, slot, day = _local_calendar(drivers)
    n = len(drivers)
    arrays = [drivers.heat_lo_kwth, drivers.heat_hi_kwth]
    temps_held = drivers.temperature_lo_c is not None and drivers.temperature_hi_c is not None
    if temps_held:
        assert drivers.temperature_lo_c is not None and drivers.temperature_hi_c is not None
        arrays += [drivers.temperature_lo_c, drivers.temperature_hi_c]
    finite = np.ones(n, dtype=bool)
    for a in arrays:
        finite &= np.isfinite(a)
    # A start is usable when its whole window (start + 96 half-hours) exists and is finite.
    usable = np.zeros(n, dtype=bool)
    if n > _HH:
        bad = np.concatenate([[0], np.cumsum(~finite)])
        idx = np.arange(n - _HH)
        usable[idx] = (bad[idx + _HH + 1] - bad[idx]) == 0
    out: list[SlotEnvelope] = []
    for si, season in enumerate(seasons):
        in_season = np.isin(month, season.months)
        for s in range(SLOTS_PER_DAY):
            starts = np.flatnonzero(in_season & (slot == s) & usable)
            if not temps_held:
                kind = drivers.temperature_kind.replace("_", " ")
                out.append(
                    SlotEnvelope(si, s, starts, f"the driving temperature ({kind}) is not held")
                )
                continue
            short = []
            for mo in season.months:
                days = len(np.unique(day[starts[month[starts] == mo]]))
                if days < min_days_per_month:
                    short.append(f"month {mo}: {days} days")
            if short:
                out.append(
                    SlotEnvelope(
                        si,
                        s,
                        starts,
                        "driver coverage below the stated minimum of "
                        f"{min_days_per_month} days per month ({'; '.join(short)})",
                    )
                )
                continue
            win = starts[:, None] + np.arange(_HH + 1)[None, :]
            assert drivers.temperature_lo_c is not None and drivers.temperature_hi_c is not None
            out.append(
                SlotEnvelope(
                    si,
                    s,
                    starts,
                    None,
                    x_lo=_minute_envelope(drivers.heat_lo_kwth[win], upper=False),
                    x_hi=_minute_envelope(drivers.heat_hi_kwth[win], upper=True),
                    t_lo=_minute_envelope(drivers.temperature_lo_c[win], upper=False),
                    t_hi=_minute_envelope(drivers.temperature_hi_c[win], upper=True),
                )
            )
    return out


# --------------------------------------------------------------------------------------------
# Strategy bounds: headroom (event) and spare (recovery)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyPolicy:
    """What the controller does under one strategy in one slot (exposed for the oracle)."""

    strategy: Literal["unload", "switch_off"]
    #: switch_off only: when units stop, how many keep running, and when the stopped ones may
    #: restart (minutes after the start).
    stop_at_min: int | None
    keep_units: int | None
    restart_from_min: int | None


def _min_over_corners(
    plant: NumericPlant, x0: np.ndarray, floor: np.ndarray, t_lo: np.ndarray, t_hi: np.ndarray
) -> np.ndarray:
    best = np.full(np.shape(x0), np.inf)
    for t, reg, mask in plant.curve.corners(t_lo, t_hi):
        h = plant.curve.headroom(x0, floor, t, reg)
        best = np.where(mask, np.minimum(best, h), best)
    return best


def _unload_bounds(plant: NumericPlant, env: SlotEnvelope) -> tuple[np.ndarray, np.ndarray]:
    assert env.x_lo is not None and env.x_hi is not None
    assert env.t_lo is not None and env.t_hi is not None
    q, r, n_max = plant.unit_kwth, plant.min_plr, plant.n_chillers
    xl, xh = env.x_lo[:MAX_DURATION_MIN], env.x_hi[:MAX_DURATION_MIN]
    tl, th = env.t_lo[:MAX_DURATION_MIN], env.t_hi[:MAX_DURATION_MIN]
    head = _min_over_corners(plant, xl, plant.n_on(xl) * r * q, tl, th)
    for k in range(1, n_max):
        kq = k * q
        inside = (xl <= kq) & (kq < xh)
        floor = np.full(xl.shape, min(n_max, k + 1 + plant.standby) * r * q)
        h = _min_over_corners(plant, np.full(xl.shape, kq), floor, tl, th)
        head = np.where(inside, np.minimum(head, h), head)
    head = np.where(env.x_hi[:MAX_DURATION_MIN] > plant.capacity_kwth, -np.inf, head)

    # Recovery spare: running units only until min_off has passed since the start (a unit the
    # plant stopped just before the start may be locked out); all units after.
    xl, xh = env.x_lo, env.x_hi
    run = plant.n_on(xh) * q - xh
    for k in range(1, n_max + 1):
        kq = k * q
        inside = (xl <= kq) & (kq <= xh)
        run = np.where(inside, np.minimum(run, min(n_max, k + plant.standby) * q - kq), run)
    spare = run.copy()
    if plant.min_off_min is not None:
        all_units = plant.capacity_kwth - xh
        spare[plant.min_off_min :] = all_units[plant.min_off_min :]
    return head, np.maximum(spare, 0.0)


def _switch_bounds(
    plant: NumericPlant, env: SlotEnvelope
) -> tuple[np.ndarray, np.ndarray, StrategyPolicy] | None:
    if plant.min_run_min is None or plant.min_off_min is None:
        return None
    assert env.x_lo is not None and env.x_hi is not None
    assert env.t_lo is not None and env.t_hi is not None
    q, r = plant.unit_kwth, plant.min_plr
    stop, lock = plant.min_run_min, plant.min_run_min + plant.min_off_min
    if lock >= HORIZON_MIN:
        return None
    n0_min = int(plant.n_on(np.array(env.x_lo[0])))
    n0_max = int(plant.n_on(np.array(env.x_hi[0])))
    keep = int(min(plant.n_chillers, max(1, math.ceil(env.x_hi[stop:lock].max() / q - 1e-12))))
    m = np.arange(HORIZON_MIN)
    run_max = np.where(m < stop, n0_max, min(n0_max, keep))
    run_min = np.where(m < stop, n0_min, min(n0_min, keep))
    sl = slice(0, MAX_DURATION_MIN)
    head = _min_over_corners(plant, env.x_lo[sl], run_max[sl] * r * q, env.t_lo[sl], env.t_hi[sl])
    head = np.where(run_min[sl] * q < env.x_hi[sl], -np.inf, head)
    spare = np.where(m < lock, run_min * q - env.x_hi, plant.capacity_kwth - env.x_hi)
    policy = StrategyPolicy("switch_off", stop, keep, lock)
    return head, np.maximum(spare, 0.0), policy


# --------------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Bounds:
    policy: StrategyPolicy
    head: np.ndarray  # (S, 1440)
    scum: np.ndarray  # (S, 2881) cumulative recovery surplus, kJ
    usurplus: np.ndarray  # (S, 2880) recovery surplus rate, kW


def _dominating_deficit(
    plant: NumericPlant, xl: np.ndarray, tl: np.ndarray, th: np.ndarray, k: np.ndarray
) -> np.ndarray:
    """``F_dom`` for slots (S,) at one minute and kW levels ``k`` (1, K) -> (S, K)."""
    best = np.zeros((xl.shape[0], k.shape[1]))
    for t, reg, mask in plant.curve.corners(tl, th):
        f = plant.curve.deficit(xl[:, None], t[:, None], reg[:, None], k)
        best = np.where(mask[:, None], np.maximum(best, f), best)
    return best


def _held(a: np.ndarray | None) -> np.ndarray:
    if a is None:
        raise ValueError("only a known envelope is certified")
    return a


def _sweep(
    plant: NumericPlant,
    envs: Sequence[SlotEnvelope],
    bounds: _Bounds,
    kgrid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """``dmax[s, j]``: the longest certified hold at ``kgrid[j]`` in slot ``s`` (0 if none), and
    the index into ``_REASONS`` of the check that ended it."""
    x_lo = np.stack([_held(e.x_lo) for e in envs])
    t_lo = np.stack([_held(e.t_lo) for e in envs])
    t_hi = np.stack([_held(e.t_hi) for e in envs])
    s_n, k_n = len(envs), len(kgrid)
    k = kgrid[None, :].astype(float)
    c, ca, cw = plant.c_total, plant.c_hall, plant.c_loop
    lam = plant.lam
    decay = math.exp(-lam * 60.0)
    rec_cap = bounds.scum[:, -1:] - bounds.scum  # (S, 2881): surplus still to come after t
    e = np.zeros((s_n, k_n))
    d = np.zeros((s_n, k_n))
    alive = np.ones((s_n, k_n), dtype=bool)
    dmax = np.full((s_n, k_n), MAX_DURATION_MIN, dtype=np.int32)
    why = np.full((s_n, k_n), -1, dtype=np.int8)
    for m in range(MAX_DURATION_MIN):
        f = _dominating_deficit(plant, x_lo[:, m], t_lo[:, m], t_hi[:, m], k)
        bad_f = ~np.isfinite(f)
        f = np.where(bad_f, 0.0, f)
        e = e + f * 60.0
        d_inf = -f / (cw * lam)
        d = d_inf + (d - d_inf) * decay
        checks = (
            bad_f | (k > bounds.head[:, m : m + 1]),
            f > plant.f_rate,
            (e - ca * d) / c > plant.loop_up,
            e / c > plant.hall_up,
            e > rec_cap[:, m + 1 : m + 2],
        )
        fail = np.zeros_like(alive)
        code = np.full((s_n, k_n), -1, dtype=np.int8)
        for i, chk in reversed(list(enumerate(checks))):
            code = np.where(chk, i, code)
            fail |= chk
        new = fail & alive
        if new.any():
            dmax[new] = m
            why[new] = code[new]
            alive &= ~fail
            if not alive.any():
                break
    # A longer hold at a larger kW cannot be certified when a smaller one fails (D-7 (1)).
    dmax = np.minimum.accumulate(dmax, axis=1)
    return dmax, why


def _kw_by_duration(dmax: np.ndarray, kgrid: np.ndarray) -> np.ndarray:
    """(S, K) longest holds -> (S, 1441) largest certified kW for each duration."""
    durations = np.arange(MAX_DURATION_MIN + 1)
    kw = np.zeros((dmax.shape[0], MAX_DURATION_MIN + 1), dtype=np.int64)
    for s in range(dmax.shape[0]):
        # dmax[s] is nonincreasing in kW, so the kW levels holding D minutes are a prefix.
        count = np.searchsorted(-dmax[s], -durations, side="right")
        kw[s] = np.where(count > 0, kgrid[np.maximum(count - 1, 0)], 0)
    return kw


# --------------------------------------------------------------------------------------------
# Answers and the certificate
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class SlotAnswer:
    """What one (season, slot) can hold: the producer-neutral seam the surface builder reads.

    A future interface-level adapter (research note 35, Q2) produces the same thing by asking
    for the largest holdable kW at starts across the slot and keeping the lowest answer.
    """

    status: Literal["flex", "zero_flex", "unknown"]
    #: ``kw_by_duration[D]``: certified kW for a hold of ``D`` minutes (index 0..1440).
    kw_by_duration: np.ndarray | None
    reason: str | None


@dataclass(frozen=True, eq=False)
class Certificate:
    """The certified slots of one surface, with everything an independent check needs."""

    plant: NumericPlant
    envelopes: tuple[SlotEnvelope, ...]
    answers: tuple[SlotAnswer, ...]
    #: Per strategy, per certified slot index (into ``envelopes``): kW by duration, policy,
    #: cumulative recovery surplus.
    strategy_kw: dict[str, np.ndarray]
    policies: dict[str, list[StrategyPolicy | None]]
    surplus: dict[str, np.ndarray]
    certified: tuple[int, ...]

    def answer(self, season_index: int, slot: int) -> SlotAnswer:
        return self.answers[season_index * SLOTS_PER_DAY + slot]

    def strategy_for(self, index: int, duration_min: int, kw: int) -> str:
        """The strategy a slot (index into ``envelopes``) uses for a declared point."""
        row = self.certified.index(index)
        for name in ("unload", "switch_off"):
            if name in self.strategy_kw and self.strategy_kw[name][row, duration_min] >= kw:
                return name
        raise ValueError("no strategy certifies this point")

    def policy(self, index: int, strategy: str) -> StrategyPolicy:
        row = self.certified.index(index)
        p = self.policies[strategy][row]
        assert p is not None
        return p

    def rebound(
        self, season_index: int, slots: Sequence[int], duration_min: int, kw: int
    ) -> Rebound:
        """The declared rebound for holding ``kw`` for ``duration_min`` from any start in
        ``slots`` (D-7 (4), B-b): energy and peak from the stored heat and the largest recovery
        surplus through the steepest point of the power curve, rounded up; recovery until the
        heat is cleared, plus the settle tail."""
        idx = [season_index * SLOTS_PER_DAY + s for s in slots]
        p = self.plant
        e_end, f_peak, u_peak, rec = 0.0, 0.0, 0.0, 0
        t_lo, t_hi = math.inf, -math.inf
        for i in idx:
            env = self.envelopes[i]
            assert env.x_lo is not None and env.t_lo is not None and env.t_hi is not None
            t_lo, t_hi = min(t_lo, float(env.t_lo.min())), max(t_hi, float(env.t_hi.max()))
            strat = self.strategy_for(i, duration_min, kw)
            row = self.certified.index(i)
            sl = slice(0, duration_min)
            f = _dominating_deficit(
                p, env.x_lo[sl], env.t_lo[sl], env.t_hi[sl], np.array([[float(kw)]])
            )[:, 0]
            # The same minute-by-minute sum the sweep accumulates.
            e = float(np.cumsum(f * 60.0)[-1])
            fmax = float(f.max())
            scum = self.surplus[strat][row]
            need = scum[duration_min] + e
            t_clear = int(np.searchsorted(scum, need - 1e-9, side="left"))
            if t_clear > HORIZON_MIN:
                raise AssertionError("a certified point must clear within the horizon")
            u = np.diff(scum)[duration_min:t_clear] / 60.0
            u_max = float(u.max()) if u.size else 0.0
            d0 = max(fmax, u_max) * p.c_hall / (p.ua * p.c_total)
            rec = max(rec, t_clear - duration_min + settle_minutes(p, d0))
            e_end, f_peak, u_peak = max(e_end, e), max(f_peak, fmax), max(u_peak, u_max)
        slope = max_slope(p, t_lo, t_hi)
        energy = math.ceil(e_end / 3600.0 * slope)
        peak = max(1, math.ceil(u_peak * slope))
        rec = max(rec, 1)
        while energy * 60 > peak * rec + 59:
            rec += 1
        return Rebound(energy_kwh=energy, peak_kw=peak, recovery_min=rec)


def _strategy_bounds(
    plant: NumericPlant, envs: Sequence[SlotEnvelope]
) -> dict[str, tuple[_Bounds, list[StrategyPolicy | None]]]:
    out: dict[str, tuple[_Bounds, list[StrategyPolicy | None]]] = {}
    u_cap = plant.u_cap
    heads, spares = [], []
    for env in envs:
        h, s = _unload_bounds(plant, env)
        heads.append(h)
        spares.append(s)
    u = np.minimum(np.stack(spares), u_cap)
    scum = np.concatenate([np.zeros((len(envs), 1)), np.cumsum(u * 60.0, axis=1)], axis=1)
    unload = StrategyPolicy("unload", None, None, None)
    out["unload"] = (_Bounds(unload, np.stack(heads), scum, u), [unload] * len(envs))
    sw = [_switch_bounds(plant, env) for env in envs]
    if any(x is not None for x in sw):
        heads = [x[0] if x else np.full(MAX_DURATION_MIN, -np.inf) for x in sw]
        spares = [x[1] if x else np.zeros(HORIZON_MIN) for x in sw]
        u = np.minimum(np.stack(spares), u_cap)
        scum = np.concatenate([np.zeros((len(envs), 1)), np.cumsum(u * 60.0, axis=1)], axis=1)
        pol: list[StrategyPolicy | None] = [x[2] if x else None for x in sw]
        generic = StrategyPolicy("switch_off", None, None, None)
        out["switch_off"] = (_Bounds(generic, np.stack(heads), scum, u), pol)
    return out


def certify(plant: NumericPlant, envelopes: Sequence[SlotEnvelope]) -> Certificate:
    """Certify every slot whose envelopes are known and whose power curve is valid on them."""
    answers: list[SlotAnswer] = []
    good: list[int] = []
    for i, env in enumerate(envelopes):
        if env.unknown_reason is not None:
            answers.append(SlotAnswer("unknown", None, env.unknown_reason))
            continue
        assert env.t_lo is not None and env.t_hi is not None and env.x_hi is not None
        bad = curve_validity(plant, float(env.t_lo.min()), float(env.t_hi.max()))
        if bad is None and float(env.x_hi.max()) > plant.capacity_kwth:
            bad = "the heat load exceeds the chillers' capacity: the baseline cannot hold"
        if bad is not None:
            answers.append(SlotAnswer("unknown", None, bad))
            continue
        answers.append(SlotAnswer("zero_flex", None, None))  # replaced below
        good.append(i)

    strategy_kw: dict[str, np.ndarray] = {}
    policies: dict[str, list[StrategyPolicy | None]] = {}
    surplus: dict[str, np.ndarray] = {}
    if good:
        envs = [envelopes[i] for i in good]
        sb = _strategy_bounds(plant, envs)
        first = slice(0, MIN_DURATION_MIN)
        k_top = max(float(np.max(b.head[:, first].min(axis=1))) for b, _ in sb.values())
        k_top = math.floor(min(k_top, 1e9))
        why_by: dict[str, np.ndarray] = {}
        if k_top >= 1:
            step = max(1, math.ceil(k_top / K_GRID_MAX))
            kgrid = np.arange(step, k_top + 1, step, dtype=np.int64)
            for name, (b, pol) in sb.items():
                dmax, why = _sweep(plant, envs, b, kgrid)
                strategy_kw[name] = _kw_by_duration(dmax, kgrid)
                why_by[name] = why[:, 0]
                policies[name] = pol
                surplus[name] = b.scum
        else:
            for name, (b, pol) in sb.items():
                strategy_kw[name] = np.zeros((len(envs), MAX_DURATION_MIN + 1), dtype=np.int64)
                why_by[name] = np.zeros(len(envs), dtype=np.int8)
                policies[name] = pol
                surplus[name] = b.scum
        best = np.max(np.stack(list(strategy_kw.values())), axis=0)
        for row, i in enumerate(good):
            kw = best[row]
            if kw[MIN_DURATION_MIN] >= 1:
                answers[i] = SlotAnswer("flex", kw, None)
            else:
                code = int(why_by["unload"][row])
                words = _WORDS[_REASONS[code]] if code >= 0 else _WORDS["plant_floor"]
                answers[i] = SlotAnswer(
                    "zero_flex",
                    None,
                    f"no 1 kW hold of {MIN_DURATION_MIN} min is firm from every start: {words}",
                )
    return Certificate(
        plant=plant,
        envelopes=tuple(envelopes),
        answers=tuple(answers),
        strategy_kw=strategy_kw,
        policies=policies,
        surplus=surplus,
        certified=tuple(good),
    )
