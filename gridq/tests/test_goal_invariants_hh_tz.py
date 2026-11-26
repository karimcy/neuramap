"""Executable HH timestamp/timezone invariants from the goal spec (docs/goal/FINDINGS.md F-003).

Covers the "DST/leap-year/timezone and missing-interval handling in the HH series" priority:

- a **naive local-clock** series across both 2025 GB DST transitions keeps its banding on the
  local label (red band 16:00-19:00), still solves, and is gap-segmented *only* at the
  transitions (spring-forward's 60-min label step; fall-back's duplicated labels) — never
  elsewhere;
- a **UTC-labelled tz-aware** series bands identically per physical instant to its naive-local
  twin (a summer 15:00 UTC half-hour is in the red band — 16:00 BST);
- a **mixed-offset CSV** (the +00:00/+01:00 UK export style across a clock change) parses to a
  single UTC axis without dropping a row, summarises with no phantom missing interval at the
  transition, and solves with red-band placement on the GB local clock and *no* segmentation
  (the physical axis is continuous).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _milp_support import multi_rate_site
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.ingestion import load_half_hourly_csv, summarise_half_hourly_data
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.tariff_calendar import (
    duos_band_labels,
    energy_price_series,
    red_band_mask,
)
from gridq.schemas import SolverStatus

_LONDON = "Europe/London"


@pytest.fixture
def assumptions():
    return load_assumptions("gb_v0_1")


def _problem(site, *, power=100.0, duration=2.0, assumptions=None):
    return DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=power,
        energy_kwh=power * duration,
        duration_hours=duration,
    )


def _local_instants(start_local: str, days: int) -> pd.DatetimeIndex:
    """Physically continuous 30-min instants, labelled on the Europe/London clock."""
    return pd.date_range(start_local, periods=days * 48, freq="30min", tz=_LONDON)


def _shaped_load(labels: pd.DatetimeIndex) -> np.ndarray:
    """The _milp_support load shape (trough/day/red-peak), keyed off local-clock labels."""
    hour = labels.hour.to_numpy()
    weekday = labels.dayofweek.to_numpy() < 5
    load = np.full(len(labels), 60.0)
    load[(hour >= 8) & (hour < 20)] = 220.0
    load[weekday & (hour >= 16) & (hour < 19)] = 320.0
    return load


def _write_csv(path, stamps: list[str], load: np.ndarray) -> int:
    pd.DataFrame({"timestamp": stamps, "import_kw": load}).to_csv(path, index=False)
    return len(stamps)


def _expected_red_mask(labels: pd.DatetimeIndex) -> np.ndarray:
    """The local-clock red-band expectation (weekday 16:00-19:00), computed independently."""
    hour = labels.hour.to_numpy()
    return (labels.dayofweek.to_numpy() < 5) & (hour >= 16) & (hour < 19)


def _discontinuity_warnings(result) -> list[str]:
    return [w for w in result.warnings if "discontinuit" in w]


def _solve_naive_local(tmp_path, assumptions, start_local: str, days: int):
    """Write a naive local-wall-clock CSV across a DST transition; solve; return pieces."""
    naive = _local_instants(start_local, days).tz_localize(None)  # the meter's wall clock
    path = tmp_path / "hh_naive.csv"
    rows = _write_csv(path, list(naive.strftime("%Y-%m-%d %H:%M:%S")), _shaped_load(naive))
    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    return naive, solver, result


def test_naive_local_spring_forward_red_band_on_local_label(tmp_path, assumptions):
    """Across 2025-03-30 spring-forward: solves; red band by local label; one segment boundary."""
    naive, solver, result = _solve_naive_local(tmp_path, assumptions, "2025-03-29 00:00", 3)
    assert result.solver_status == SolverStatus.OPTIMAL

    schedule = solver.last_schedule()
    assert schedule is not None
    # Labels stay monotonic at spring-forward (01:00-01:59 simply absent) — no re-order.
    expected = _expected_red_mask(naive)
    assert np.array_equal(schedule.red_band_mask, expected)
    assert int(schedule.red_band_mask.sum()) == 6  # Monday 31st only: 6 half-hours 16:00-19:00

    # Exactly ONE discontinuity — the conservative segment boundary at the 60-min label
    # step — and none anywhere else in the three days.
    seg = _discontinuity_warnings(result)
    assert len(seg) == 1
    assert seg[0].startswith("1 timestamp discontinuity")


def test_naive_local_fall_back_red_band_on_local_label(tmp_path, assumptions):
    """Across 2025-10-26 fall-back: solves; red band by local label; segments only at the dups."""
    naive, solver, result = _solve_naive_local(tmp_path, assumptions, "2025-10-25 00:00", 3)
    assert result.solver_status == SolverStatus.OPTIMAL

    schedule = solver.last_schedule()
    assert schedule is not None
    # Fall-back duplicates the 01:00/01:30 labels (non-monotonic file); the solver sorts
    # stably, so the expectation is computed on the sorted labels.
    sorted_labels = pd.DatetimeIndex(np.sort(naive.values, kind="stable"))
    expected = _expected_red_mask(sorted_labels)
    assert np.array_equal(schedule.red_band_mask, expected)
    assert int(schedule.red_band_mask.sum()) == 6  # Monday 27th only

    # Exactly TWO discontinuities — the two duplicated (zero-delta) labels at the
    # transition — and none anywhere else.
    seg = _discontinuity_warnings(result)
    assert len(seg) == 1
    assert seg[0].startswith("2 timestamp discontinuities")


def test_utc_labelled_bands_match_naive_local_per_instant():
    """A UTC-labelled tz-aware index bands identically to its local-clock twin, per instant."""
    # Spans the 2025-03-30 spring-forward, so both GMT and BST are exercised.
    local = _local_instants("2025-03-27 00:00", 6)
    utc = local.tz_convert("UTC")
    naive_local = local.tz_localize(None)

    assert np.array_equal(duos_band_labels(utc), duos_band_labels(naive_local))
    assert np.array_equal(red_band_mask(utc), red_band_mask(naive_local))

    rates = {"day": 0.30, "night": 0.10}
    prices_utc, warn_utc = energy_price_series(rates, utc)
    prices_naive, warn_naive = energy_price_series(rates, naive_local)
    assert warn_utc is None and warn_naive is None
    assert prices_utc is not None and prices_naive is not None
    assert np.array_equal(prices_utc, prices_naive)


def test_summer_1500_utc_half_hour_is_in_the_red_band():
    """In BST, a weekday 15:00 UTC half-hour is 16:00 local — inside the red band."""
    utc = pd.date_range("2025-07-07 00:00", periods=48, freq="30min", tz="UTC")  # a Monday
    mask = red_band_mask(utc)
    fifteen_utc = int(np.flatnonzero((utc.hour == 15) & (utc.minute == 0))[0])
    assert bool(mask[fifteen_utc])
    # And the naive 15:00 label would NOT be red — the conversion is doing real work.
    assert not bool(red_band_mask(utc.tz_localize(None))[fifteen_utc])


def test_mixed_offset_csv_round_trips_and_solves(tmp_path, assumptions):
    """A +00:00/+01:00 mixed-offset CSV parses to UTC, summarises cleanly and solves."""
    local = _local_instants("2025-03-27 00:00", 5)  # Thu-Mon across spring-forward
    stamps = list(local.strftime("%Y-%m-%dT%H:%M:%S%z"))
    assert {s[-5:] for s in stamps} == {"+0000", "+0100"}  # genuinely mixed offsets
    path = tmp_path / "hh_mixed.csv"
    rows = _write_csv(path, stamps, _shaped_load(local))

    df = load_half_hourly_csv(path)
    assert len(df) == rows  # no row dropped
    assert str(df["timestamp"].dt.tz) == "UTC"  # one physical axis, tz-aware

    # Coverage on the physical axis: no phantom missing interval at the clock change.
    summary = summarise_half_hourly_data(df)
    assert summary.coverage.missing_interval_count == 0
    assert summary.coverage.duplicate_timestamp_count == 0

    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL

    # Physically continuous across the transition: NO gap segmentation for tz-aware input.
    assert _discontinuity_warnings(result) == []

    schedule = solver.last_schedule()
    assert schedule is not None
    # Red-band placement follows the GB local clock: Thu + Fri + Mon, 6 half-hours each.
    expected = _expected_red_mask(local)
    assert np.array_equal(schedule.red_band_mask, expected)
    assert int(schedule.red_band_mask.sum()) == 18
