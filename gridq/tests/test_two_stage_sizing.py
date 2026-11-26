"""Integration tests for the M4 two-stage sizing search (D32).

The goal-spec exit criterion is explicit: run the search twice on identical inputs and get the
identical sizing; the persisted cloud is a continuous surface (coarse + refine + confirm stages);
proxy evaluations never become the reported selection; the covenant gate stays wired through.
Uses a full synthetic metered year through the real MILP (slow-ish: several dozen small proxy
solves + a handful of full-year solves).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _milp_support import multi_rate_site
from gridq.assumptions import load_assumptions
from gridq.finance.debt import DebtTerms
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.two_stage import (
    COARSE_DURATION_HOURS,
    COARSE_POWER_FRACTIONS,
    POLISH_MAX_PROBES,
    SEARCH_VERSION,
    two_stage_sizing,
)
from gridq.sizing.objective import SizingObjective


def _write_year_csv(path) -> int:
    """A deterministic full-year HH series (weekday day/night + red-band peak shape)."""
    ts = pd.date_range("2025-01-01 00:00", periods=365 * 48, freq="30min")
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    load = np.full(len(ts), 60.0)
    load[(hour >= 8) & (hour < 20)] = 220.0
    load[weekday & (hour >= 16) & (hour < 19)] = 320.0
    # A mild deterministic seasonal swing so months differ (no RNG).
    month = ts.month.to_numpy()
    load = load * (1.0 + 0.1 * np.cos((month - 1) / 12.0 * 2.0 * np.pi))
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    return len(ts)


@pytest.fixture(scope="module")
def year_site(tmp_path_factory):
    path = tmp_path_factory.mktemp("two_stage") / "hh_year.csv"
    rows = _write_year_csv(path)
    return multi_rate_site(path, rows=rows, max_import_kw=352.0)


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v0_1")


@pytest.fixture(scope="module")
def run_pair(year_site, assumptions):
    """The same search run twice on identical inputs (module-scoped: solves are not free)."""

    def _run():
        return two_stage_sizing(
            year_site,
            assumptions,
            base_solver=MilpDispatchSolver(),
            objective=SizingObjective(min_p90_dscr_covenant=1.2),
            binding_capacity_kw=352.0,
            debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
            seed=0,
        )

    return _run(), _run()


def test_run_twice_converges_to_the_identical_sizing(run_pair):
    first, second = run_pair
    assert first.fingerprint() == second.fingerprint()
    assert first.selected is not None
    assert (
        first.selected.candidate.power_kw,
        first.selected.candidate.duration_hours,
    ) == (
        second.selected.candidate.power_kw,
        second.selected.candidate.duration_hours,
    )


def test_cloud_is_a_continuous_surface_with_staged_provenance(run_pair):
    run, _ = run_pair
    assert run.search_version == SEARCH_VERSION
    stages = {e.stage for e in run.candidates_evaluated}
    assert {"coarse", "refine", "confirm"} <= stages
    # The coarse sweep alone spans the wide box: 10 power fractions x 8 durations.
    coarse = [e for e in run.candidates_evaluated if e.stage == "coarse"]
    assert len(coarse) == len(COARSE_POWER_FRACTIONS) * len(COARSE_DURATION_HOURS)
    # Refinement explored beyond the coarse lattice (continuous, not a re-enumeration).
    refined = [e for e in run.candidates_evaluated if e.stage == "refine"]
    assert refined
    durations = {e.candidate.duration_hours for e in refined}
    assert any(d not in COARSE_DURATION_HOURS for d in durations)


def test_selected_size_is_full_series_confirmed_never_proxy(run_pair):
    run, _ = run_pair
    assert run.selected is not None
    assert run.selected.stage == "confirm"
    assert run.selected.proxy is False
    # Proxy evaluations disclose themselves on the result's warnings.
    proxy_evals = [e for e in run.candidates_evaluated if e.proxy]
    assert proxy_evals
    assert all(any("proxy" in w for w in e.optimisation_result.warnings) for e in proxy_evals)


def test_covenant_gate_is_wired_through_the_search(run_pair):
    run, _ = run_pair
    assert run.selected is not None
    assert run.selected.passes_covenant is True
    assert run.selected.min_p90_dscr is not None
    assert run.selected.min_p90_dscr >= 1.2 - 1e-9


def test_grid_projection_carries_stage_and_search_version(run_pair):
    run, _ = run_pair
    grid = run.sizing_grid()
    assert grid.search_version == SEARCH_VERSION
    assert grid.selected is not None and grid.selected.stage == "confirm"
    assert any(p.proxy for p in grid.points)
    assert any(p.stage == "refine" for p in grid.points)


# --- progress side channel (C035) ------------------------------------------


@pytest.fixture(scope="module")
def sink_run(year_site, assumptions):
    """One search run wired with a recording progress sink (module-scoped: solves aren't free)."""
    ticks: list[tuple[str, int | None, int | None]] = []
    run = two_stage_sizing(
        year_site,
        assumptions,
        base_solver=MilpDispatchSolver(),
        objective=SizingObjective(min_p90_dscr_covenant=1.2),
        binding_capacity_kw=352.0,
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        seed=0,
        progress=lambda phase, done, total: ticks.append((phase, done, total)),
    )
    return run, ticks


def test_progress_phases_appear_in_search_order(sink_run):
    _run, ticks = sink_run
    phases = [t[0] for t in ticks]
    assert phases, "the search emitted no progress"
    # First appearance of each stage is monotonically ordered coarse -> refine -> confirm
    # (polish is optional and, when present, appears last).
    order = ["coarse", "refine", "confirm", "polish"]
    seen = [p for p in order if p in phases]
    firsts = [phases.index(p) for p in seen]
    assert firsts == sorted(firsts)
    assert seen[:3] == ["coarse", "refine", "confirm"]


def test_progress_counts_increase_and_totals_are_correct(sink_run):
    _run, ticks = sink_run
    coarse = [t for t in ticks if t[0] == "coarse"]
    refine = [t for t in ticks if t[0] == "refine"]
    confirm = [t for t in ticks if t[0] == "confirm"]

    # Coarse: emitted once per actual solve, 1..N strictly increasing, total = the sweep size.
    assert coarse
    assert [t[1] for t in coarse] == list(range(1, len(coarse) + 1))
    assert len(coarse) == len(COARSE_POWER_FRACTIONS) * len(COARSE_DURATION_HOURS)
    assert all(t[2] == len(coarse) for t in coarse)

    # Refine: strictly increasing evaluation count, NO total claimed (the DE budget is opaque).
    assert refine
    assert [t[1] for t in refine] == list(range(1, len(refine) + 1))
    assert all(t[2] is None for t in refine)

    # Confirm: strictly increasing, total = the shortlist size.
    assert confirm
    assert [t[1] for t in confirm] == list(range(1, len(confirm) + 1))
    assert all(t[2] == len(confirm) for t in confirm)

    # Polish (Stage D): the bounded full-series compass search fires on this proxy-mode fixture
    # (the year_site stratifies into a typical-period proxy, so proxy_mode is True and a confirmed
    # candidate exists — the two gates at two_stage.py ~L342). It is deterministic: fixed step
    # schedule, fixed probe order, hard cap. Measured here: 6 probes emitted. Each emit is a real
    # full-series solve, counted 1..N strictly increasing, with the total pinned to the hard cap
    # POLISH_MAX_PROBES (the DE-style budget is opaque, but the probe count is not). Dedup-skipped
    # probes cost nothing and are not emitted, so the emitted count never exceeds the cap.
    polish = [t for t in ticks if t[0] == "polish"]
    assert polish, "the polish stage emitted no progress on the proxy-mode fixture"
    assert [t[1] for t in polish] == list(range(1, len(polish) + 1))
    assert all(t[2] == POLISH_MAX_PROBES for t in polish)
    assert len(polish) <= POLISH_MAX_PROBES


def test_progress_sink_does_not_change_the_selection(sink_run, run_pair):
    run, _ = sink_run
    no_sink, _ = run_pair
    # A recording sink is byte-neutral: identical fingerprint, selected candidate and warnings.
    assert run.fingerprint() == no_sink.fingerprint()
    assert run.selected is not None and no_sink.selected is not None
    assert (run.selected.candidate.power_kw, run.selected.candidate.duration_hours) == (
        no_sink.selected.candidate.power_kw,
        no_sink.selected.candidate.duration_hours,
    )
    assert len(run.warnings) == len(no_sink.warnings)
