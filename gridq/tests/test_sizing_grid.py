"""W5-0: the sizing optimiser exposes its full evaluated grid (the 3D surface's data source).

Proves the additive `SizingGrid` seam end to end:
- a sizing run projects EVERY evaluated (power, duration) candidate — with its levered return,
  P90 DSCR and covenant feasibility — plus the chosen optimum (`SizingRun.sizing_grid`);
- the projection is **complete** (one point per candidate, scalars copied 1:1, nothing invented),
  **deterministic**, and **JSON-serializable** (so it persists and reloads);
- the `MilpEngine` exposes it via the duck-typed `last_sizing_run()` hook (mirroring
  `selected_dispatch_energy()`), and the quote pipeline persists it onto the `QuoteRecord`;
- the persistence is **additive / backward-compatible**: it does not change the quote `input_hash`
  or `quote_id`, and an engine without the hook (`BaselineEngine`) simply leaves it absent.
"""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult, SimpleSolver
from gridq.finance.debt import DebtTerms
from gridq.optimisation import MilpEngine
from gridq.quotes import assemble_quote_record
from gridq.quotes.pipeline import generate_quote
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing

COVENANT = 1.3


def _objective() -> SizingObjective:
    return SizingObjective(min_p90_dscr_covenant=COVENANT)


def _space() -> SearchSpace:
    return SearchSpace(power_kw_candidates=[50.0, 100.0], duration_hour_candidates=[1.0, 2.0])


class _FixedRevenueSolver:
    """Synthetic solver emitting a constant FIRM revenue regardless of size (test fixture only).

    Fixed revenue + capex/debt that scale with power ⇒ DSCR strictly decreases as power grows, so a
    wide power grid yields a clean pass/fail split — exercising the feasible-region projection.
    """

    def __init__(self, annual_revenue_gbp: float) -> None:
        self._rev = annual_revenue_gbp

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        return DispatchResult(
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
            value_streams=[
                s.ValueStreamResult(
                    stream=s.MarketStream.TARIFF_ARBITRAGE,
                    annual_revenue_gbp=round(self._rev, 2),
                    firmness=s.Firmness.FIRM,
                )
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


def _milp_site(tmp_path) -> tuple[s.SiteModel, object]:
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


# --- grid completeness (the headline contract) ------------------------------


def test_grid_has_one_point_per_evaluated_candidate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """`sizing_grid()` projects exactly the evaluated candidates, scalars copied 1:1."""
    lib = load_assumptions("gb_v0_1")
    run = enumerate_sizing(
        make_site(), lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    grid = run.sizing_grid()

    assert grid.search_space_size == run.search_space_size == 4
    assert len(grid.points) == len(run.candidates_evaluated) == 4
    # One point per (power, duration) candidate, in the same deterministic order.
    assert [(p.power_kw, p.duration_hours) for p in grid.points] == [
        (e.candidate.power_kw, e.candidate.duration_hours) for e in run.candidates_evaluated
    ]
    # Every plottable scalar is copied straight off the matching CandidateEvaluation.
    for point, ev in zip(grid.points, run.candidates_evaluated, strict=True):
        assert point.energy_kwh == ev.candidate.energy_kwh
        assert point.levered_return_pct == ev.levered_return_pct
        assert point.project_irr_pct == ev.project_irr_pct
        assert point.min_p90_dscr == ev.min_p90_dscr
        assert point.passes_covenant == ev.passes_covenant
        assert point.priceable == ev.priceable
        assert point.bankable == ev.bankable
        assert point.solver_status == ev.optimisation_result.solver_status
        assert point.end_of_life_deliverable == ev.end_of_life_deliverable


def test_grid_marks_chosen_optimum_and_feasible_region(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A covenant-passing optimum is marked, and the feasible region is distinguishable."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    # Fixed revenue + scaling capex: the tiny system clears the covenant, the huge one cannot.
    space = SearchSpace(power_kw_candidates=[25.0, 4000.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=120_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    grid = run.sizing_grid()

    assert run.selected is not None  # guard: this config selects a covenant-passer
    assert grid.selected is not None
    assert grid.selected.power_kw == run.selected.candidate.power_kw == 25.0
    assert grid.selected.duration_hours == run.selected.candidate.duration_hours
    assert grid.selected.levered_return_pct == run.selected.levered_return_pct
    assert grid.selected.passes_covenant is True
    assert grid.covenant == COVENANT
    # The feasible region is highlightable: both pass and fail points are present and labelled.
    assert any(p.passes_covenant is True for p in grid.points)
    assert any(p.passes_covenant is False for p in grid.points)


def test_grid_optimum_is_none_on_placeholder(make_site: Callable[..., s.SiteModel]) -> None:
    """No covenant-feasible candidate (placeholder, no debt) ⇒ no marked optimum (honest)."""
    lib = load_assumptions("gb_v0_1")
    run = enumerate_sizing(
        make_site(), lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    grid = run.sizing_grid()
    assert grid.selected is None
    assert grid.bankable is False
    assert grid.points  # the grid still carries every evaluated candidate


def test_grid_invents_no_metrics_without_debt(make_site: Callable[..., s.SiteModel]) -> None:
    """Without injected debt there is no levered split / DSCR — the grid stays ``None``, not 0."""
    lib = load_assumptions("gb_v0_1")
    run = enumerate_sizing(
        make_site(), lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    grid = run.sizing_grid()
    assert all(p.levered_return_pct is None for p in grid.points)
    assert all(p.min_p90_dscr is None for p in grid.points)
    assert all(p.passes_covenant is None for p in grid.points)


def test_empty_search_space_grid_is_empty(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    empty = SearchSpace(power_kw_candidates=[], duration_hour_candidates=[2.0])
    grid = enumerate_sizing(
        make_site(), lib, empty, dispatch_solver=SimpleSolver(), objective=_objective()
    ).sizing_grid()
    assert grid.points == []
    assert grid.search_space_size == 0
    assert grid.selected is None


# --- determinism + serialization (persistable) ------------------------------


def test_grid_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()
    a = enumerate_sizing(
        site, lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    ).sizing_grid()
    b = enumerate_sizing(
        site, lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    ).sizing_grid()
    assert a == b  # pydantic structural equality over the whole grid


def test_grid_json_round_trips(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    space = SearchSpace(power_kw_candidates=[25.0, 4000.0], duration_hour_candidates=[2.0])
    grid = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=120_000.0),
        objective=_objective(),
        debt_terms=debt,
    ).sizing_grid()
    assert grid.selected is not None  # a populated optimum survives the round-trip
    reloaded = s.SizingGrid.model_validate_json(grid.model_dump_json())
    assert reloaded == grid


# --- the engine seam + pipeline persistence ---------------------------------


def test_engine_exposes_full_grid_via_last_sizing_run(tmp_path) -> None:
    """`MilpEngine.last_sizing_run()` returns the two-stage evaluated cloud (D32/D34 default).

    The cloud spans the coarse sweep plus refinement and confirmation evaluations,
    stage-labelled, under `search_version` m4-two-stage-v2.
    """
    site, assumptions = _milp_site(tmp_path)
    engine = MilpEngine()
    engine.solve(engine.build_optimisation_request(site, assumptions))
    run = engine.last_sizing_run()
    assert run is not None
    grid = run.sizing_grid()
    assert grid.search_version == "m4-two-stage-v2"
    assert len(grid.points) >= 80  # at least the full coarse sweep
    assert {"coarse", "confirm"} <= {p.stage for p in grid.points}


def test_fresh_engine_has_no_sizing_run() -> None:
    """Before any solve there is no grid to expose."""
    assert MilpEngine().last_sizing_run() is None


def test_pipeline_persists_grid_on_quote_record(tmp_path) -> None:
    """The pipeline threads the engine grid onto both the run result and the persisted record."""
    site, assumptions = _milp_site(tmp_path)
    run = generate_quote(site, assumptions, engine=MilpEngine())
    assert run.sizing_grid is not None
    assert run.quote.sizing_grid is not None
    assert run.quote.sizing_grid == run.sizing_grid
    # The persisted cloud is the full two-stage evaluation (D32/D34), not a 3x3 grid.
    assert len(run.quote.sizing_grid.points) >= 80
    assert run.quote.sizing_grid.search_version == "m4-two-stage-v2"


def test_baseline_engine_leaves_grid_absent(make_site: Callable[..., s.SiteModel]) -> None:
    """An engine with no `last_sizing_run` hook (the default BaselineEngine) is unaffected."""
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"))  # default BaselineEngine
    assert run.sizing_grid is None
    assert run.quote.sizing_grid is None


# --- the field is additive / backward-compatible (out of the hash) ----------


def test_sizing_grid_is_excluded_from_the_quote_hash(tmp_path) -> None:
    """Persisting a `sizing_grid` must not change the quote `input_hash`/`quote_id` (additive)."""
    site, assumptions = _milp_site(tmp_path)
    run = generate_quote(site, assumptions, engine=MilpEngine())
    grid = run.sizing_grid
    assert grid is not None

    without = assemble_quote_record(site, run.result, run.financial_case, assumptions)
    with_grid = assemble_quote_record(
        site, run.result, run.financial_case, assumptions, sizing_grid=grid
    )
    assert without.sizing_grid is None and with_grid.sizing_grid is not None
    assert without.input_hash == with_grid.input_hash
    assert without.quote_id == with_grid.quote_id
