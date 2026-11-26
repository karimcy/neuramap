"""Tests for the parallel evaluation harness (issue 016 stage 4).

The load-bearing invariant: the parallel path is a pure speed-up — its results are IDENTICAL to
the sequential ``evaluate_many`` in order and value, regardless of worker count or scheduling,
and each worker owns its own evaluator (the stateful solver is never shared).
"""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.sizing import ContinuousSizingEvaluator, SizingObjective
from gridq.sizing.parallel import benchmark_parallel_evaluation, evaluate_points_parallel

POINTS = [(80.0, 160.0), (100.0, 270.0), (120.0, 480.0), (60.0, 90.0), (150.0, 600.0)]


class _SizeScaledSolver:
    """Synthetic solver whose FIRM revenue scales with power (test fixture only) — makes every
    point's score distinct, so an ordering mistake in the reduction cannot pass silently."""

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


def _factory(site: s.SiteModel) -> Callable[[], ContinuousSizingEvaluator]:
    lib = load_assumptions("gb_v0_1")

    def build() -> ContinuousSizingEvaluator:
        return ContinuousSizingEvaluator(
            site,
            lib,
            dispatch_solver=_SizeScaledSolver(),  # type: ignore[arg-type]  # Protocol test double
            objective=SizingObjective(min_p90_dscr_covenant=1.3),
        )

    return build


def _scalars(points: list) -> list[tuple]:
    return [
        (p.power_kw, p.energy_kwh, p.objective_metric, p.objective_value, p.feasible)
        for p in points
    ]


def test_parallel_equals_sequential(make_site: Callable[..., s.SiteModel]) -> None:
    factory = _factory(make_site())
    sequential = factory().evaluate_many(POINTS)
    parallel = evaluate_points_parallel(factory, POINTS, max_workers=4)
    assert _scalars(parallel) == _scalars(sequential)


def test_parallel_is_deterministic_across_runs(make_site: Callable[..., s.SiteModel]) -> None:
    factory = _factory(make_site())
    a = evaluate_points_parallel(factory, POINTS, max_workers=3)
    b = evaluate_points_parallel(factory, POINTS, max_workers=3)
    assert _scalars(a) == _scalars(b)


def test_single_worker_and_empty_inputs(make_site: Callable[..., s.SiteModel]) -> None:
    factory = _factory(make_site())
    assert evaluate_points_parallel(factory, [], max_workers=4) == []
    one = evaluate_points_parallel(factory, POINTS, max_workers=1)
    assert _scalars(one) == _scalars(factory().evaluate_many(POINTS))


def test_each_worker_builds_its_own_evaluator(make_site: Callable[..., s.SiteModel]) -> None:
    # The solver is stateful, so evaluators must never be shared across workers: with 4 workers
    # over 5 points the factory is called exactly once per worker (4), never once overall.
    site = make_site()
    inner = _factory(site)
    calls = 0

    def counting_factory() -> ContinuousSizingEvaluator:
        nonlocal calls
        calls += 1
        return inner()

    evaluate_points_parallel(counting_factory, POINTS, max_workers=4)
    assert calls == 4


def test_benchmark_reports_and_matches_results(make_site: Callable[..., s.SiteModel]) -> None:
    factory = _factory(make_site())
    results, report = benchmark_parallel_evaluation(factory, POINTS, max_workers=2)
    assert _scalars(results) == _scalars(factory().evaluate_many(POINTS))
    assert report.points == len(POINTS)
    assert report.workers == 2
    assert report.wall_clock_s > 0
    assert report.seconds_per_solve > 0
    assert report.solves_per_hour > 0
    assert report.objective_metric == results[0].objective_metric
