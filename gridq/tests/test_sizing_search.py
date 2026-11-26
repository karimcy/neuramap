"""Tests for the sizing search engine (issue 016 stages 5-6): dense reference + adaptive refiner.

Invariants: every in-band grid point is scored (the surface is complete), selection is
deterministic and metric-honest, the covenant-gated winner is honestly None without debt, an
empty grid at coarse resolution warns instead of inventing a point — and the refiner is
certified against the dense reference (equal-or-better optimum on the same surface), respects
its evaluation budget, and logs a monotone convergence curve.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.finance.debt import DebtTerms
from gridq.sizing import (
    METRIC_LEVERED_RETURN,
    ContinuousSizingEvaluator,
    SizingDomain,
    SizingObjective,
)
from gridq.sizing.search import adaptive_refine_search, dense_grid_search, pso_search

COVENANT = 1.3


class _SizeScaledSolver:
    """FIRM revenue scaling with power (test fixture only) — distinct score per point."""

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
                    annual_revenue_gbp=round(100.0 * problem.power_kw, 2),
                    firmness=s.Firmness.FIRM,
                )
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


class _FixedRevenueSolver(_SizeScaledSolver):
    """Constant FIRM revenue (test fixture only) — DSCR falls as power/capex grow."""

    def __init__(self, annual_revenue_gbp: float) -> None:
        self._rev = annual_revenue_gbp

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        result = super().solve(problem)
        return result.model_copy(
            update={
                "value_streams": [
                    s.ValueStreamResult(
                        stream=s.MarketStream.TARIFF_ARBITRAGE,
                        annual_revenue_gbp=round(self._rev, 2),
                        firmness=s.Firmness.FIRM,
                    )
                ]
            }
        )


def _factory(
    site: s.SiteModel, *, solver: object, debt_terms: DebtTerms | None = None
) -> Callable[[], ContinuousSizingEvaluator]:
    lib = load_assumptions("gb_v0_1")

    def build() -> ContinuousSizingEvaluator:
        return ContinuousSizingEvaluator(
            site,
            lib,
            dispatch_solver=solver,  # type: ignore[arg-type]  # Protocol test double
            objective=SizingObjective(min_p90_dscr_covenant=COVENANT),
            debt_terms=debt_terms,
        )

    return build


def _domain() -> SizingDomain:
    return SizingDomain(
        power_kw_min=50.0, power_kw_max=200.0, energy_kwh_min=50.0, energy_kwh_max=800.0
    )


def test_dense_grid_scores_every_in_band_point(make_site: Callable[..., s.SiteModel]) -> None:
    domain = _domain()
    result = dense_grid_search(
        _factory(make_site(), solver=_SizeScaledSolver()), domain, n_power=4, n_energy=4
    )
    grid = domain.grid(4, 4)
    assert result.method == "dense_grid"
    assert result.n_evaluations == len(grid) == len(result.evaluated)
    assert [(p.power_kw, p.energy_kwh) for p in result.evaluated] == [
        (c.power_kw, c.energy_kwh) for c in grid
    ], "the surface must retain every in-band grid point, in deterministic order"


def test_selection_is_deterministic_and_matches_the_surface(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site()
    a = dense_grid_search(
        _factory(site, solver=_SizeScaledSolver()), _domain(), n_power=4, n_energy=4
    )
    b = dense_grid_search(
        _factory(site, solver=_SizeScaledSolver()), _domain(), n_power=4, n_energy=4
    )
    assert a.best is not None and b.best is not None
    assert (a.best.power_kw, a.best.energy_kwh) == (b.best.power_kw, b.best.energy_kwh)
    # The winner's value is the maximum on the surface within its metric tier (no invented point:
    # the best is always one of the evaluated points).
    same_metric = [p for p in a.evaluated if p.objective_metric == a.best.objective_metric]
    assert a.best.objective_value == max(p.objective_value for p in same_metric)
    assert any(
        (p.power_kw, p.energy_kwh) == (a.best.power_kw, a.best.energy_kwh) for p in a.evaluated
    )


def test_no_feasible_winner_without_debt_is_honest(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = dense_grid_search(
        _factory(make_site(), solver=_SizeScaledSolver()), _domain(), n_power=3, n_energy=3
    )
    assert result.best_feasible is None  # no DebtTerms ⇒ no DSCR ⇒ covenant can never pass
    assert any("indicative only" in w for w in result.warnings)


def test_feasible_winner_with_injected_debt_on_levered_metric(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = dense_grid_search(
        _factory(
            make_site(),
            solver=_FixedRevenueSolver(200_000.0),
            debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        ),
        _domain(),
        n_power=3,
        n_energy=3,
    )
    assert result.best_feasible is not None
    assert result.best_feasible.feasible is True
    assert result.best_feasible.objective_metric == METRIC_LEVERED_RETURN
    assert result.warnings == []


class _PeakedSolver(_SizeScaledSolver):
    """FIRM revenue sharply peaked at (137 kW, 400 kWh) (test fixture only) — a surface where
    the dense grid's resolution is the limit, so sub-grid refinement is provable, not lucky."""

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        revenue = (
            50_000.0 - 50.0 * abs(problem.power_kw - 137.0) - 2.0 * abs(problem.energy_kwh - 400.0)
        )
        result = super().solve(problem)
        return result.model_copy(
            update={
                "value_streams": [
                    s.ValueStreamResult(
                        stream=s.MarketStream.TARIFF_ARBITRAGE,
                        annual_revenue_gbp=round(max(revenue, 1.0), 2),
                        firmness=s.Firmness.FIRM,
                    )
                ]
            }
        )


def test_adaptive_matches_or_beats_the_dense_reference(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # The stage-8 accuracy premise on the stub surface: same evaluator, so the refiner's optimum
    # must be at least the dense reference's within tolerance — the refiner is the fast path,
    # never a lossy one.
    site = make_site()
    domain = _domain()
    ref = dense_grid_search(_factory(site, solver=_PeakedSolver()), domain, n_power=9, n_energy=9)
    ada = adaptive_refine_search(_factory(site, solver=_PeakedSolver()), domain, seed=0)
    assert ref.best is not None and ada.best is not None
    assert ada.method == "adaptive_zoom" and ref.method == "dense_grid"
    assert ada.best.objective_metric == ref.best.objective_metric
    tol = max(1e-6, 0.01 * abs(ref.best.objective_value))
    assert ada.best.objective_value >= ref.best.objective_value - tol, (
        f"adaptive optimum {ada.best.objective_value} lost more than {tol} vs the dense "
        f"reference {ref.best.objective_value}"
    )
    # And it refined toward the true peak's neighbourhood (sub-grid precision).
    assert abs(ada.best.power_kw - 137.0) <= abs(ref.best.power_kw - 137.0) + 1e-6


def test_adaptive_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    site = make_site()
    a = adaptive_refine_search(_factory(site, solver=_PeakedSolver()), _domain(), seed=0)
    b = adaptive_refine_search(_factory(site, solver=_PeakedSolver()), _domain(), seed=0)
    assert [(p.power_kw, p.energy_kwh, p.objective_value) for p in a.evaluated] == [
        (p.power_kw, p.energy_kwh, p.objective_value) for p in b.evaluated
    ]
    assert [(r.round_index, r.evaluations_total, r.incumbent_value) for r in a.rounds] == [
        (r.round_index, r.evaluations_total, r.incumbent_value) for r in b.rounds
    ]


def test_adaptive_respects_the_evaluation_budget(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = adaptive_refine_search(
        _factory(make_site(), solver=_PeakedSolver()), _domain(), max_evaluations=10, seed=0
    )
    assert result.n_evaluations <= 10
    assert any("budget" in w for w in result.warnings)
    assert result.best is not None  # best-of-what-was-scored, never invented


def test_adaptive_convergence_curve_is_monotone(
    make_site: Callable[..., s.SiteModel],
) -> None:
    result = adaptive_refine_search(
        _factory(make_site(), solver=_PeakedSolver()), _domain(), seed=0
    )
    assert result.rounds, "the convergence curve must be logged"
    totals = [r.evaluations_total for r in result.rounds]
    assert totals == sorted(totals)
    values = [r.incumbent_value for r in result.rounds]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:], strict=False)), (
        "the incumbent value must never regress"
    )


def test_adaptive_feasibility_aware_with_injected_debt(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # Fixed revenue + debt scaling with capex ⇒ DSCR falls as power grows: the covenant carves
    # the feasible region and the refiner must return a genuinely feasible winner on the levered
    # metric, agreeing with the dense reference within tolerance.
    site = make_site()
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    ref = dense_grid_search(
        _factory(site, solver=_FixedRevenueSolver(200_000.0), debt_terms=debt),
        _domain(),
        n_power=9,
        n_energy=9,
    )
    ada = adaptive_refine_search(
        _factory(site, solver=_FixedRevenueSolver(200_000.0), debt_terms=debt),
        _domain(),
        seed=0,
    )
    assert ref.best_feasible is not None and ada.best_feasible is not None
    assert ada.best_feasible.feasible is True
    assert ada.best_feasible.objective_metric == METRIC_LEVERED_RETURN
    tol = max(1e-6, 0.01 * abs(ref.best_feasible.objective_value))
    assert ada.best_feasible.objective_value >= ref.best_feasible.objective_value - tol


def test_search_result_projects_onto_the_frozen_sizing_grid(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # Stage 7: the wider surface rides the existing SizingGrid contract — every scored point,
    # deterministic order, honest no-selection without debt, JSON round-trip intact.
    from gridq.sizing.search import search_result_to_sizing_grid

    result = adaptive_refine_search(
        _factory(make_site(), solver=_PeakedSolver()), _domain(), seed=0, max_evaluations=40
    )
    grid = search_result_to_sizing_grid(result)
    assert grid.search_space_size == result.n_evaluations == len(grid.points)
    assert [(gp.power_kw, gp.energy_kwh) for gp in grid.points] == [
        (p.power_kw, p.energy_kwh) for p in result.evaluated
    ]
    assert grid.selected is None  # no debt ⇒ no covenant winner ⇒ honest no-selection
    assert grid.bankable is False
    assert "indicative only" in grid.selection_reason
    assert s.SizingGrid.model_validate_json(grid.model_dump_json()) == grid


def test_search_grid_selected_and_covenant_with_injected_debt(
    make_site: Callable[..., s.SiteModel],
) -> None:
    from gridq.sizing.search import search_result_to_sizing_grid

    result = adaptive_refine_search(
        _factory(
            make_site(),
            solver=_FixedRevenueSolver(200_000.0),
            debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        ),
        _domain(),
        seed=0,
        max_evaluations=40,
    )
    grid = search_result_to_sizing_grid(result)
    assert result.best_feasible is not None
    assert grid.selected is not None
    assert grid.selected.passes_covenant is True
    assert grid.covenant == COVENANT
    # Bankable stays False on the placeholder library even with a feasible levered winner —
    # the projection can never soften the finance gate.
    assert grid.bankable is result.best_feasible.evaluation.bankable


def test_adaptive_rejects_bad_parameters(make_site: Callable[..., s.SiteModel]) -> None:
    factory = _factory(make_site(), solver=_SizeScaledSolver())
    with pytest.raises(ValueError, match="zoom_factor"):
        adaptive_refine_search(factory, _domain(), zoom_factor=1.0)
    with pytest.raises(ValueError, match="max_evaluations"):
        adaptive_refine_search(factory, _domain(), max_evaluations=0)


def test_pso_baseline_is_deterministic_in_band_and_budgeted(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site()
    domain = _domain()
    a = pso_search(_factory(site, solver=_PeakedSolver()), domain, seed=0, max_evaluations=60)
    b = pso_search(_factory(site, solver=_PeakedSolver()), domain, seed=0, max_evaluations=60)
    assert a.method == "pso"
    assert a.n_evaluations <= 60
    assert [(p.power_kw, p.energy_kwh, p.objective_value) for p in a.evaluated] == [
        (p.power_kw, p.energy_kwh, p.objective_value) for p in b.evaluated
    ]
    for p in a.evaluated:
        assert domain.contains(p.power_kw, p.energy_kwh), (
            f"PSO particle escaped the domain: ({p.power_kw}, {p.energy_kwh})"
        )
    assert a.rounds, "PSO must log its convergence curve"


def test_pso_baseline_reaches_a_sane_optimum_on_the_peaked_surface(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # PSO is the comparison baseline, not the reference — assert it lands in the right region,
    # with the bake-off (not this test) judging its efficiency vs the zoom refiner.
    site = make_site()
    ref = dense_grid_search(
        _factory(site, solver=_PeakedSolver()), _domain(), n_power=9, n_energy=9
    )
    pso = pso_search(_factory(site, solver=_PeakedSolver()), _domain(), seed=0)
    assert ref.best is not None and pso.best is not None
    assert pso.best.objective_metric == ref.best.objective_metric
    tol = max(1e-6, 0.05 * abs(ref.best.objective_value))
    assert pso.best.objective_value >= ref.best.objective_value - tol


def test_empty_grid_at_coarse_resolution_warns_never_invents(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # A valid domain (band-feasible power window [13.3, 24] is non-empty) whose 2×2 linspace
    # corners all fall outside the 2–3 h band: (10,40)=4h, (10,48)=4.8h, (25,40)=1.6h,
    # (25,48)=1.92h. The search must return an honest empty result, not a fabricated point.
    domain = SizingDomain(
        power_kw_min=10.0,
        power_kw_max=25.0,
        energy_kwh_min=40.0,
        energy_kwh_max=48.0,
        duration_h_min=2.0,
        duration_h_max=3.0,
    )
    result = dense_grid_search(
        _factory(make_site(), solver=_SizeScaledSolver()), domain, n_power=2, n_energy=2
    )
    assert result.n_evaluations == 0
    assert result.best is None and result.best_feasible is None
    assert any("no in-band points" in w for w in result.warnings)
