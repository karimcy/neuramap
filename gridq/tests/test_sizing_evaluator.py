"""Tests for the continuous-point evaluator (issue 016 stage 3).

The invariants that matter: the evaluator scores an arbitrary continuous point through the SAME
exact dispatch+finance path as the grid enumeration (certification premise), it is deterministic,
and — the objective-metric honesty scope addition — every scored point is labelled with the metric
it was actually scored on, so a search on the indicative fallback can never masquerade as the
levered objective (B001).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult, SimpleSolver
from gridq.finance.debt import DebtTerms
from gridq.optimisation.engine import MilpEngine
from gridq.sizing import (
    METRIC_LEVERED_RETURN,
    METRIC_PRICED_FLOOR,
    METRIC_PROJECT_IRR,
    ContinuousSizingEvaluator,
    SizingObjective,
)
from gridq.sizing.evaluator import priced_floor_value_gbp

COVENANT = 1.3


class _FixedRevenueSolver:
    """Synthetic solver emitting a constant FIRM revenue regardless of size (test fixture only).

    ``annual_cost_gbp`` is the signed dispatch component's negative part (`_split_signed`) — kept
    at £0 by default so every pre-existing pin is unmoved, and set non-zero by the N5 parity test
    so that test actually exercises the ``revenue − cost`` formula it claims to guard.
    """

    def __init__(self, annual_revenue_gbp: float, annual_cost_gbp: float = 0.0) -> None:
        self._rev = annual_revenue_gbp
        self._cost = annual_cost_gbp

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
                    annual_cost_gbp=round(self._cost, 2),
                    firmness=s.Firmness.FIRM,
                )
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


def _evaluator(
    site: s.SiteModel,
    *,
    solver: object,
    debt_terms: DebtTerms | None = None,
) -> ContinuousSizingEvaluator:
    return ContinuousSizingEvaluator(
        site,
        load_assumptions("gb_v0_1"),
        dispatch_solver=solver,  # type: ignore[arg-type]  # test doubles satisfy the Protocol
        objective=SizingObjective(min_p90_dscr_covenant=COVENANT),
        debt_terms=debt_terms,
    )


def test_placeholder_point_scores_on_priced_floor_and_is_infeasible(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # SimpleSolver prices nothing on the placeholder: no finance metric exists, so the point is
    # honestly scored on the priced-floor fallback (£0) and the covenant gate cannot pass.
    ev = _evaluator(make_site(), solver=SimpleSolver())
    p = ev.evaluate(100.0, 270.0)  # a continuous 2.7 h point no grid would enumerate
    assert p.objective_metric == METRIC_PRICED_FLOOR
    assert p.objective_value == 0.0
    assert p.feasible is False
    assert p.duration_hours == pytest.approx(2.7)


def test_fixed_revenue_without_debt_scores_on_project_irr(
    make_site: Callable[..., s.SiteModel],
) -> None:
    ev = _evaluator(make_site(), solver=_FixedRevenueSolver(200_000.0))
    p = ev.evaluate(100.0, 270.0)
    assert p.objective_metric == METRIC_PROJECT_IRR
    assert p.objective_value == p.evaluation.project_irr_pct
    assert p.evaluation.levered_return_pct is None  # no debt ⇒ no levered split, never guessed


def test_injected_debt_scores_on_the_levered_objective(
    make_site: Callable[..., s.SiteModel],
) -> None:
    ev = _evaluator(
        make_site(),
        solver=_FixedRevenueSolver(200_000.0),
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
    )
    p = ev.evaluate(100.0, 270.0)
    assert p.objective_metric == METRIC_LEVERED_RETURN
    assert p.objective_value == p.evaluation.levered_return_pct
    assert p.evaluation.min_p90_dscr is not None  # debt present ⇒ DSCR measurable
    # The covenant gate verdict matches the objective's own policy on this evaluation.
    assert p.feasible == SizingObjective(min_p90_dscr_covenant=COVENANT).feasible(p.evaluation)


def test_evaluator_is_deterministic_and_order_preserving(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site()
    points = [(80.0, 160.0), (100.0, 270.0), (120.0, 480.0)]
    a = _evaluator(site, solver=_FixedRevenueSolver(150_000.0)).evaluate_many(points)
    b = _evaluator(site, solver=_FixedRevenueSolver(150_000.0)).evaluate_many(points)
    assert [(x.power_kw, x.energy_kwh) for x in a] == points
    assert [
        (x.power_kw, x.energy_kwh, x.objective_metric, x.objective_value, x.feasible) for x in a
    ] == [(x.power_kw, x.energy_kwh, x.objective_metric, x.objective_value, x.feasible) for x in b]


def test_priced_floor_helper_matches_engine_ranking(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # Guard against drift: the evaluator's fallback metric mirrors MilpEngine._floor_value.
    # RE-PINNED for N5 (2026-08-10 review): the fixture now carries a real ``annual_cost_gbp``,
    # because the old zero-cost double made the parity assertion true for BOTH the wrong (gross
    # revenue) and the right (revenue − cost) formula — it pinned the wrong formula in two places.
    ev = _evaluator(make_site(), solver=_FixedRevenueSolver(150_000.0, 25_000.0))
    p = ev.evaluate(100.0, 200.0)
    assert priced_floor_value_gbp(p.evaluation.optimisation_result) == MilpEngine._floor_value(
        p.evaluation
    )
    assert priced_floor_value_gbp(p.evaluation.optimisation_result) == pytest.approx(125_000.0)


def test_rejects_non_positive_points(make_site: Callable[..., s.SiteModel]) -> None:
    ev = _evaluator(make_site(), solver=SimpleSolver())
    with pytest.raises(ValueError, match="strictly positive"):
        ev.evaluate(0.0, 100.0)
    with pytest.raises(ValueError, match="strictly positive"):
        ev.evaluate(100.0, -1.0)
