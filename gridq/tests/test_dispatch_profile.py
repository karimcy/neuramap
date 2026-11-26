"""W5-2: the engine exposes a representative dispatch day (the dispatch chart's data source).

Proves the additive `DispatchProfile` seam end to end:
- `HalfHourlyDispatchSchedule.representative_day_profile()` selects ONE real day deterministically
  (the highest-total-discharge day, earliest index on ties) and copies its half-hourly values 1:1 —
  no average over days, no invented shape;
- the `MilpEngine` / `DemoEngine` expose it via the duck-typed `last_dispatch_profile()` hook
  (mirroring `last_sizing_run()`), and the quote pipeline persists it onto the `QuoteRecord`;
- the persistence is **additive / backward-compatible**: it does not change the quote `input_hash`
  or `quote_id`, a run with no half-hourly series leaves it `None` (honest), and an engine without
  the hook (`BaselineEngine`) simply leaves it absent.
"""

from __future__ import annotations

import numpy as np

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.optimisation import MilpEngine
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.quotes import assemble_quote_record
from gridq.quotes.pipeline import generate_quote


def _milp_site(tmp_path):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _schedule(charge, discharge, *, energy_price=None) -> HalfHourlyDispatchSchedule:
    """A synthetic schedule for unit-testing the representative-day projection (fixture only)."""
    n = len(charge)
    arr = lambda v: np.asarray(v, dtype=float)  # noqa: E731
    return HalfHourlyDispatchSchedule(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=arr(charge),
        discharge_kw=arr(discharge),
        soe_kwh=arr([0.0] * n),
        site_load_kw=arr([100.0] * n),
        duos_rate_gbp_per_kwh=arr([0.1] * n),
        red_band_mask=np.zeros(n, dtype=bool),
        energy_price_gbp_per_kwh=None if energy_price is None else arr(energy_price),
    )


# --- the builder, in isolation (deterministic selection, 1:1 copy) ----------


def test_representative_day_is_the_highest_discharge_day() -> None:
    """Two days; day 1 discharges more ⇒ it is the selected representative day, copied verbatim."""
    day0 = [1.0] * 48
    day1 = [5.0] * 48  # higher total discharge
    sched = _schedule(charge=[0.0] * 96, discharge=day0 + day1)
    profile = sched.representative_day_profile()

    assert profile.representative_day_index == 1
    assert profile.selection_basis == "highest-discharge day"
    assert len(profile.points) == 48
    assert [p.period for p in profile.points] == list(range(48))
    # Values are day 1's, copied 1:1 — not an average across the two days.
    assert all(p.discharge_kw == 5.0 for p in profile.points)


def test_ties_resolve_to_the_earliest_day() -> None:
    """Equal discharge across days ⇒ argmax picks the earliest (index 0)."""
    flat = [2.0] * 96
    profile = _schedule(charge=[0.0] * 96, discharge=flat).representative_day_profile()
    assert profile.representative_day_index == 0


def test_builder_clamps_solver_noise_and_carries_none_price() -> None:
    """Tiny negative powers (solver tolerance) clamp to 0; a missing ToU price stays None."""
    discharge = [-1e-9] * 48
    discharge[10] = 3.0
    profile = _schedule(charge=[-1e-9] * 48, discharge=discharge).representative_day_profile()
    assert all(p.charge_kw >= 0 and p.discharge_kw >= 0 for p in profile.points)
    assert profile.points[0].energy_price_gbp_per_kwh is None


def test_horizon_shorter_than_a_day_uses_the_whole_horizon() -> None:
    """A partial (sub-day) horizon is projected whole, with the honest basis label."""
    profile = _schedule(charge=[0.0] * 10, discharge=[1.0] * 10).representative_day_profile()
    assert len(profile.points) == 10
    assert profile.representative_day_index == 0
    assert "shorter than one day" in profile.selection_basis


# --- the engine hook + pipeline persistence (end to end) --------------------


def test_milp_engine_persists_a_representative_dispatch_day(tmp_path) -> None:
    """A real MILP solve over a metered HH series persists a populated, real dispatch day."""
    site, assumptions = _milp_site(tmp_path)
    run = generate_quote(site, assumptions, engine=MilpEngine())
    profile = run.quote.dispatch_profile

    assert profile is not None
    assert len(profile.points) == 48  # half-hourly day
    assert profile.interval_hours == 0.5
    assert profile.selection_basis == "highest-discharge day"
    # A real day of the strategy: some discharge actually happens.
    assert sum(p.discharge_kw for p in profile.points) > 0
    # Also surfaced on the QuoteRunResult detail (mirrors sizing_grid). Value equality, not
    # identity: the pipeline reconstructs the profile it persists, so the detail carries an equal
    # profile rather than the same object.
    assert run.dispatch_profile == profile


def test_dispatch_profile_is_deterministic(tmp_path) -> None:
    """Identical inputs ⇒ identical persisted profile (no wall-clock / RNG on the path)."""
    site, assumptions = _milp_site(tmp_path)
    a = generate_quote(site, assumptions, engine=MilpEngine()).quote.dispatch_profile
    b = generate_quote(site, assumptions, engine=MilpEngine()).quote.dispatch_profile
    assert a == b


def test_dispatch_profile_is_excluded_from_the_quote_hash(tmp_path) -> None:
    """Persisting a `dispatch_profile` must not change the quote `input_hash`/`quote_id`."""
    site, assumptions = _milp_site(tmp_path)
    run = generate_quote(site, assumptions, engine=MilpEngine())
    profile = run.dispatch_profile
    assert profile is not None

    without = assemble_quote_record(site, run.result, run.financial_case, assumptions)
    with_profile = assemble_quote_record(
        site, run.result, run.financial_case, assumptions, dispatch_profile=profile
    )
    assert without.dispatch_profile is None and with_profile.dispatch_profile is not None
    assert without.input_hash == with_profile.input_hash
    assert without.quote_id == with_profile.quote_id


def test_no_half_hourly_series_leaves_the_profile_none(tmp_path) -> None:
    """No metered HH series ⇒ insufficient data ⇒ no schedule ⇒ honest `None` (never invented)."""
    site, assumptions = _milp_site(tmp_path)
    load = site.load_data.model_copy(update={"half_hourly_file_ref": None})
    site_no_hh = site.model_copy(update={"load_data": load})
    run = generate_quote(site_no_hh, assumptions, engine=MilpEngine())
    assert run.quote.dispatch_profile is None


def test_baseline_engine_exposes_no_dispatch_profile(tmp_path) -> None:
    """An engine without the duck-typed hook (`BaselineEngine`) leaves the field absent."""
    site, assumptions = _milp_site(tmp_path)
    run = generate_quote(site, assumptions, engine=BaselineEngine())
    assert run.quote.dispatch_profile is None
