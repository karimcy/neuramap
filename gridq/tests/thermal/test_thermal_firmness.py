"""WP-THERM-03 acceptance (2), (3) and (4), as property tests against an independent oracle.

For every flex cell of the SYNTHETIC fixture surface and every point on its curve, events start
at sampled instants across the cell (a seeded sampler over the cell's real start half-hours and
a minute offset inside each), on four kinds of driver path:

- ``random``: every minute's heat load and dry bulb drawn inside its half-hour bounds;
- ``worst_low_t`` / ``worst_high_t``: the lowest load the half-hours allow during the hold (the
  largest deficit per kW, the least part-load headroom) and the highest after it (the least spare
  for recovery), at the lowest or the highest dry bulb;
- ``slot_worst``: the worst load and ambient the start's whole slot covers in that season,
  computed here from the raw series (every start at that half-hour on every day, either
  half-hour a start inside it can reach), at each temperature extreme.

The oracle (`_thermal_oracle.py`, naive forward Euler, imports nothing from ``gridq.thermal``)
then checks the declared kW is held for the declared minutes (2: no path leaves the binding band,
the loop band, the humidity limit or the rate limit), that the rebound after the event stays
within the declared energy, peak and recovery time (3), and that the kW was held (4).

Hypothesis is not installed in the shared environment, so the sampler is seeded numpy
(D166 permits either; the deviation is recorded in docs/decisions.md).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import _thermal_fixture as fx
import numpy as np
import pytest
from _thermal_oracle import Batch, Outcome, rh_percent, simulate

from gridq.contracts.thermal_flex import Flex

SEED = 20261126
MODES = ("random", "worst_low_t", "worst_high_t", "slot_worst_low_t", "slot_worst_high_t")


@dataclass
class Run:
    season: str
    band: str
    duration: int
    kw: int
    energy: int
    peak: int
    recovery: int
    start_index: int
    offset: int
    mode: str


def _calendar() -> tuple[np.ndarray, np.ndarray]:
    """Local (month, minute of day) of each half-hour, computed here from the series start."""
    tz = ZoneInfo("Europe/London")
    months, minutes = [], []
    t = fx.START
    for _ in range(fx.N_HALF_HOURS):
        loc = t.astimezone(tz)
        months.append(loc.month)
        minutes.append(loc.hour * 60 + loc.minute)
        t += timedelta(minutes=30)
    return np.array(months), np.array(minutes)


def _paths(
    drv: Any, runs: list[Run], horizon: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    months, minutes = _calendar()
    last = fx.N_HALF_HOURS - 97
    x = np.zeros((len(runs), horizon))
    t = np.zeros((len(runs), horizon))
    m = np.arange(horizon)
    for j, run in enumerate(runs):
        hh = run.start_index + (run.offset + m) // 30
        xl, xh = drv.heat_lo_kwth[hh], drv.heat_hi_kwth[hh]
        tl, th = drv.temperature_lo_c[hh], drv.temperature_hi_c[hh]
        if run.mode == "random":
            x[j] = rng.uniform(xl, xh)
            t[j] = rng.uniform(tl, th)
            continue
        if run.mode.startswith("slot_worst"):
            season = next(s for s in fx.SEASONS if s.season_id == run.season)
            slot = minutes[run.start_index]
            same = np.flatnonzero(
                np.isin(months, season.months)
                & (minutes == slot)
                & (np.arange(len(months)) <= last)
            )
            k = m // 30
            idx = same[:, None] + k[None, :]
            xl = np.minimum(drv.heat_lo_kwth[idx], drv.heat_lo_kwth[idx + 1]).min(axis=0)
            xh = np.maximum(drv.heat_hi_kwth[idx], drv.heat_hi_kwth[idx + 1]).max(axis=0)
            tl = np.minimum(drv.temperature_lo_c[idx], drv.temperature_lo_c[idx + 1]).min(axis=0)
            th = np.maximum(drv.temperature_hi_c[idx], drv.temperature_hi_c[idx + 1]).max(axis=0)
        x[j] = np.where(m < run.duration, xl, xh)
        t[j] = tl if run.mode.endswith("low_t") else th
    return x, t


@functools.cache
def _results() -> tuple[list[Run], Outcome, Any]:
    return _sample("base", MODES, 2)


def _sample(variant: str, modes: tuple[str, ...], per: int) -> tuple[list[Run], Outcome, Any]:
    result = fx.built(variant)
    surface, cert = result.surface, result.certificate
    drv = fx.variant_drivers(variant)
    rng = np.random.default_rng(SEED)
    months, minutes = _calendar()
    last = fx.N_HALF_HOURS - 97
    runs: list[Run] = []
    for cell in surface.cells:
        a = cell.availability
        if not isinstance(a, Flex):
            continue
        season = next(s for s in surface.seasons if s.season_id == cell.season_id)
        band = next(b for b in surface.bands if b.band_id == cell.band_id)
        starts = np.flatnonzero(
            np.isin(months, season.months)
            & (minutes >= band.start_minute)
            & (minutes < band.end_minute)
            & (np.arange(len(months)) <= last)
        )
        for p in a.curve:
            for mode in modes:
                for _ in range(per):
                    runs.append(
                        Run(
                            cell.season_id,
                            cell.band_id,
                            p.duration_min,
                            p.reduction_kw,
                            p.rebound.energy_kwh,
                            p.rebound.peak_kw,
                            p.rebound.recovery_min,
                            int(rng.choice(starts)),
                            int(rng.integers(0, 30)),
                            mode,
                        )
                    )
    horizon = min(2880, max(r.duration + r.recovery for r in runs) + 90)
    x, t = _paths(drv, runs, horizon, rng)
    # The controller's settings for each run: the strategy and units kept that the certificate
    # chose for the start's slot (control settings, not claims; the oracle checks the outcome).
    season_index = {s.season_id: i for i, s in enumerate(surface.seasons)}
    switch = np.zeros(len(runs), dtype=bool)
    keep = np.zeros(len(runs))
    for j, r in enumerate(runs):
        idx = season_index[r.season] * 48 + minutes[r.start_index] // 30
        strategy = cert.strategy_for(idx, r.duration, r.kw)
        if strategy == "switch_off":
            switch[j] = True
            keep[j] = cert.policy(idx, strategy).keep_units
    batch = Batch(
        x=x,
        t=t,
        duration=np.array([r.duration for r in runs]),
        kw=np.array([float(r.kw) for r in runs]),
        switch_off=switch,
        keep=keep,
        u_cap=cert.plant.u_cap,
    )
    return runs, simulate(fx.raw(variant), batch), result


def _violations(variant: str, runs: list[Run], out: Outcome) -> dict[str, np.ndarray]:
    """Every acceptance check, as a mask of the runs that break it."""
    raw, tol = fx.raw(variant), out.tol_k
    declared = np.array([[r.energy, r.peak, r.recovery] for r in runs], dtype=float)
    return {
        "kW not held": ~out.held,
        "inlet above binding max": raw["inlet_hi"] + out.inlet_dev_max > raw["binding_max"] + tol,
        "inlet below binding min": raw["inlet_lo"] + out.inlet_dev_min < raw["binding_min"] - tol,
        "loop band left": (raw["setpoint"] + out.loop_max > raw["loop_max"] + tol)
        | (raw["setpoint"] + out.loop_min < raw["loop_min"] - tol),
        "rate above limit": (out.rate60 > raw["rate_c_per_h"] + tol)
        | (out.rate15 > raw["rate_c_per_h"] / 4 + tol),
        "recovery late": ~(out.recovered_by <= declared[:, 2]),
        "rebound peak above declared": out.rebound_peak_kw > declared[:, 1] + 1e-6,
        "rebound energy above declared": out.rebound_kwh > declared[:, 0] + 1e-6,
        "inlet RH above the maximum": np.array(
            [rh_percent(raw["inlet_lo"] + d - tol, raw["dew_hi"]) for d in out.inlet_dev_min]
        )
        > raw["max_rh"],
    }


QUICK_MODES = {
    "base": ("slot_worst_low_t", "slot_worst_high_t"),
    "tight": ("slot_worst_low_t",),
}


@pytest.mark.parametrize("variant", fx.VARIANTS)
def test_quick_firmness_check(variant: str) -> None:
    """A smaller draw (the slot-worst paths only, one start per point) of every acceptance
    check at once, on each fixture variant: the nodes the mutation checks run against."""
    # The tight variant's events recover over about two days, so its draw keeps the low dry
    # bulb only (the larger deficit per kW, where its checks bind first).
    modes = QUICK_MODES[variant]
    runs, out, _ = _sample(variant, modes, 1)
    assert len(runs) >= 100
    for what, bad in _violations(variant, runs, out).items():
        assert not bad.any(), _fail(runs, bad, what)


def _fail(runs: list[Run], bad: np.ndarray, what: str) -> str:
    ex = [runs[i] for i in np.flatnonzero(bad)[:3]]
    return f"{int(bad.sum())} of {len(runs)} runs: {what}; e.g. {ex}"


def test_the_sampler_reaches_every_flex_point_on_every_path_kind() -> None:
    runs, out, result = _results()
    points = {
        (c.season_id, c.band_id, p.duration_min)
        for c in result.surface.cells
        if isinstance(c.availability, Flex)
        for p in c.availability.curve
    }
    assert {(r.season, r.band, r.duration) for r in runs} == points
    assert {r.mode for r in runs} == set(MODES)
    assert len(runs) >= 500
    assert out.tol_k < 0.05, "the Euler error bound is small against every band allowance (>= 1 K)"


def test_every_declared_kw_is_held_for_its_minutes() -> None:
    """Acceptance (4): the declared kW is held for the declared minutes at every sampled start,
    worst-case load and ambient included."""
    runs, out, _ = _results()
    assert out.held.all(), _fail(runs, ~out.held, "the plant could not hold the declared kW")


def test_no_simulated_path_leaves_the_binding_limits() -> None:
    """Acceptance (2): no path exceeds the tighter of ASHRAE A1 (recommended) and the SLA band,
    nor the humidity limit, the rate-of-change limit or the chilled-water band."""
    runs, out, _ = _results()
    raw, tol = fx.RAW, out.tol_k
    hot = raw["inlet_hi"] + out.inlet_dev_max > raw["binding_max"] + tol
    assert not hot.any(), _fail(runs, hot, "IT inlet above the binding maximum")
    cold = raw["inlet_lo"] + out.inlet_dev_min < raw["binding_min"] - tol
    assert not cold.any(), _fail(runs, cold, "IT inlet below the binding minimum")
    rh = np.array([rh_percent(raw["inlet_lo"] + d - tol, raw["dew_hi"]) for d in out.inlet_dev_min])
    wet = rh > raw["max_rh"]
    assert not wet.any(), _fail(runs, wet, "inlet RH above the ASHRAE maximum")
    assert raw["min_dp"] <= raw["dew_lo"] <= raw["dew_hi"] <= raw["max_dp"]
    rate = (out.rate60 > raw["rate_c_per_h"] + tol) | (out.rate15 > raw["rate_c_per_h"] / 4 + tol)
    assert not rate.any(), _fail(runs, rate, "inlet rate of change above the limit")
    loop_hi = raw["setpoint"] + out.loop_max > raw["loop_max"] + tol
    loop_lo = raw["setpoint"] + out.loop_min < raw["loop_min"] - tol
    assert not (loop_hi | loop_lo).any(), _fail(runs, loop_hi | loop_lo, "loop band left")


def test_rebound_is_accounted_for_after_every_event() -> None:
    """Acceptance (3): after every event the stored heat is recovered within the declared
    recovery time, and the extra plant power stays within the declared peak and energy."""
    runs, out, _ = _results()
    declared = np.array([[r.energy, r.peak, r.recovery] for r in runs], dtype=float)
    assert (declared > 0).all(), "every flex point declares a rebound"
    late = ~(out.recovered_by <= declared[:, 2])
    assert not late.any(), _fail(runs, late, "stored heat not recovered in the declared time")
    big = out.rebound_peak_kw > declared[:, 1] + 1e-6
    assert not big.any(), _fail(runs, big, "rebound peak above the declared peak")
    more = out.rebound_kwh > declared[:, 0] + 1e-6
    assert not more.any(), _fail(runs, more, "rebound energy above the declared energy")


def test_first_law_closes_on_every_run() -> None:
    """B-b: heat not extracted during the hold = heat recovered after it + heat still stored."""
    runs, out, _ = _results()
    residual = np.abs(out.deficit_kj - out.surplus_kj - out.stored_end_kj)
    assert (residual <= 1e-6 * np.maximum(out.deficit_kj, 1.0)).all()
    assert (np.abs(out.stored_end_kj) <= 1e-6 * np.maximum(out.deficit_kj, 1.0)).all(), (
        "every run ends fully recovered"
    )


@pytest.mark.parametrize("excess", [1, 5])
def test_more_than_the_declared_kw_is_not_firm_somewhere(excess: int) -> None:
    """The oracle is not vacuous: asking for more than a cell's first point declares fails on
    some worst-case path in most cells."""
    runs, _, result = _results()
    cert = result.certificate
    picked = [r for r in runs if r.mode == "slot_worst_low_t"]
    first = {}
    for c in result.surface.cells:
        if isinstance(c.availability, Flex):
            p = c.availability.curve[-1]
            first[(c.season_id, c.band_id)] = (p.duration_min, p.reduction_kw)
    chosen = [r for r in picked if (r.duration, r.kw) == first[(r.season, r.band)]]
    drv = fx.drivers()
    x, t = _paths(drv, chosen, 2880, np.random.default_rng(1))
    dur = np.array([r.duration * 4 for r in chosen])
    batch = Batch(
        x=x,
        t=t,
        duration=np.minimum(dur, 1440),
        kw=np.array([float(r.kw + excess) for r in chosen]),
        switch_off=np.zeros(len(chosen), dtype=bool),
        keep=np.zeros(len(chosen)),
        u_cap=cert.plant.u_cap,
    )
    out = simulate(fx.RAW, batch)
    raw = fx.RAW
    broke = (
        ~out.held
        | (raw["inlet_hi"] + out.inlet_dev_max > raw["binding_max"])
        | (raw["setpoint"] + out.loop_max > raw["loop_max"])
    )
    assert broke.mean() > 0.5
