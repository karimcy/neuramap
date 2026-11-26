"""Unit tests for the M4 two-stage sizing search components (D32, opt-in per the C025 gate).

Covers the deterministic DE refiner, the typical-day selector, and the weighted schedule
aggregates — each in isolation (no MILP). Duration-keyed assumption lookups keep the
conservative-flooring `value_for_duration` semantics per north-star D28 (interpolation is
deferred to sourced assumption content; the anti-conservative warranty direction is flagged
in goal FINDINGS F-002).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.sizing.refine import refine_sizing
from gridq.sizing.typical_periods import select_typical_days

# --- deterministic DE refiner ---------------------------------------------


def _smooth_key(power: float, duration: float) -> tuple[float, ...]:
    # Concave bowl with optimum at (300 kW, 3.0 h); orderable float tuple, bigger = better.
    return (1.0, -((power - 300.0) ** 2) - 5000.0 * (duration - 3.0) ** 2)


def test_refine_is_deterministic_and_converges_to_the_bowl():
    kwargs = dict(
        power_bounds=(50.0, 600.0),
        duration_bounds=(0.5, 8.0),
        seed=7,
        seed_candidates=[(100.0, 1.0)],
    )
    first = refine_sizing(_smooth_key, **kwargs)
    second = refine_sizing(_smooth_key, **kwargs)
    assert first.best == second.best
    assert first.evaluated == second.evaluated
    assert first.best[0] == pytest.approx(300.0, abs=15.0)
    assert first.best[1] == pytest.approx(3.0, abs=0.25)


def test_refine_respects_bounds_and_budget():
    trace = refine_sizing(
        _smooth_key,
        power_bounds=(50.0, 600.0),
        duration_bounds=(0.5, 8.0),
        seed=3,
        pop_size=10,
        max_generations=12,
    )
    for power, duration in trace.evaluated:
        assert 50.0 <= power <= 600.0
        assert 0.5 <= duration <= 8.0
    assert len(trace.evaluated) <= 10 * 13  # pop * (generations + init)


def test_refine_seed_candidates_are_evaluated_first():
    trace = refine_sizing(
        _smooth_key,
        power_bounds=(50.0, 600.0),
        duration_bounds=(0.5, 8.0),
        seed=5,
        seed_candidates=[(299.0, 3.0), (55.0, 7.5)],
    )
    assert trace.evaluated[0] == (299.0, 3.0)
    assert trace.evaluated[1] == (55.0, 7.5)


# --- typical-day selection --------------------------------------------------


def _year_series(gap_days: int = 0) -> tuple[pd.DatetimeIndex, np.ndarray]:
    ts = pd.date_range("2025-01-01 00:00", periods=365 * 48, freq="30min")
    if gap_days:
        # Knock a hole in mid-February: those days become incomplete and must be excluded.
        keep = ~(
            (ts >= "2025-02-10") & (ts < pd.Timestamp("2025-02-10") + pd.Timedelta(days=gap_days))
        )
        ts = ts[keep]
    hour = ts.hour.to_numpy()
    load = np.where((hour >= 8) & (hour < 20), 200.0, 60.0) + ts.dayofweek.to_numpy()
    return ts, load.astype(float)


def test_typical_days_one_medoid_per_stratum_weights_cover_all_days():
    ts, load = _year_series()
    typical = select_typical_days(ts, load, interval_min=30)
    assert typical is not None
    assert typical.day_count == 24  # 12 months x weekday/weekend
    assert typical.represented_days == 365
    assert len(typical.positions) == 24 * 48
    assert len(typical.interval_weights) == len(typical.positions)
    # Weights: each selected interval carries its stratum's day count; per-day blocks sum back
    # to the full year (48 intervals each carrying the day weight).
    assert float(typical.interval_weights.reshape(24, 48)[:, 0].sum()) == 365.0
    assert "D32" in typical.note


def test_typical_days_excludes_gap_broken_days():
    ts, load = _year_series(gap_days=3)
    typical = select_typical_days(ts, load, interval_min=30)
    assert typical is not None
    assert typical.represented_days == 365 - 3


def test_typical_days_thin_series_returns_none():
    ts = pd.date_range("2025-01-01 00:00", periods=5 * 48, freq="30min")
    load = np.full(len(ts), 100.0)
    assert select_typical_days(ts, load, interval_min=30) is None


def test_typical_days_deterministic():
    ts, load = _year_series()
    a = select_typical_days(ts, load, interval_min=30)
    b = select_typical_days(ts, load, interval_min=30)
    assert a is not None and b is not None
    assert np.array_equal(a.positions, b.positions)
    assert np.array_equal(a.interval_weights, b.interval_weights)


# --- weighted schedule aggregates -------------------------------------------


def _schedule(weights: np.ndarray | None, annual_factor: float) -> HalfHourlyDispatchSchedule:
    n = 48
    charge = np.zeros(n)
    discharge = np.zeros(n)
    charge[2:6] = 50.0
    discharge[34:38] = 44.0
    red = np.zeros(n, dtype=bool)
    red[32:38] = True
    return HalfHourlyDispatchSchedule(
        power_kw=50.0,
        energy_kwh=100.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=annual_factor,
        charge_kw=charge,
        discharge_kw=discharge,
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.where(red, 0.10, 0.0),
        red_band_mask=red,
        energy_price_gbp_per_kwh=np.where((np.arange(n) >= 16) & (np.arange(n) < 44), 0.3, 0.1),
        interval_weights=weights,
    )


def test_weights_of_one_match_unweighted_exactly():
    unweighted = _schedule(None, annual_factor=365.0)
    ones = _schedule(np.ones(48), annual_factor=365.0)
    assert unweighted.annual_charge_kwh == ones.annual_charge_kwh
    assert unweighted.annual_discharge_kwh == ones.annual_discharge_kwh
    assert unweighted.arbitrage_value_gbp == ones.arbitrage_value_gbp
    assert unweighted.duos_avoidance_value_gbp == ones.duos_avoidance_value_gbp
    assert unweighted.red_band_discharge_mwh == ones.red_band_discharge_mwh
    assert unweighted.ref() == ones.ref()


def test_weighted_day_representing_a_week_scales_like_seven_days():
    # One day standing for 7 identical days (weight 7, annual factor over 7x24h) must aggregate
    # exactly like the same day solved unweighted with a 7x shorter annualisation.
    weighted = _schedule(np.full(48, 7.0), annual_factor=8760.0 / (7 * 24.0))
    unweighted_week_equivalent = _schedule(None, annual_factor=8760.0 / 24.0)
    assert weighted.annual_charge_kwh == unweighted_week_equivalent.annual_charge_kwh
    assert weighted.annual_discharge_kwh == unweighted_week_equivalent.annual_discharge_kwh
    assert weighted.arbitrage_value_gbp == unweighted_week_equivalent.arbitrage_value_gbp
    assert weighted.duos_avoidance_value_gbp == unweighted_week_equivalent.duos_avoidance_value_gbp
