"""The scenario seam is Monte-Carlo-ready (L09 · M6 scenarios).

System Brief: "Monte Carlo deferred to portfolio work behind the same interface." These tests pin
that the seam — the `ScenarioEvaluator` protocol, whose one method is
`run(base_case, scenario_set) -> ScenarioRunResult` — lets a future MC evaluator drop in without
changing callers: the v1 discrete `ScenarioEngine` satisfies it, a stub MC-style evaluator
satisfies it too, and a caller written against the protocol runs unchanged against both.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.finance.financial_model import capital_recovery_account
from gridq.scenarios import (
    Scenario,
    ScenarioEngine,
    ScenarioEvaluator,
    ScenarioName,
    ScenarioRunResult,
    ScenarioSet,
    WeightedSummary,
)


def _base() -> s.FinancialCase:
    return s.FinancialCase(
        case_id="fc_seam",
        p50_cashflows=[-1000.0, 200.0, 200.0],
        p90_cashflows=[-1000.0, 170.0, 170.0],
        dscr_summary=s.DscrSummary(),
        capital_recovery=capital_recovery_account([-1000.0, 200.0, 200.0]),
        debt_assumptions=s.DebtAssumptions(),
        price_recommendation=s.PriceRecommendation(),
        covenant_status=s.CovenantStatus(),
    )


def _set() -> ScenarioSet:
    return ScenarioSet(
        scenario_set_id="seam",
        assumption_version="gb_v0_1",
        scenarios=[
            Scenario(scenario_id="central", name=ScenarioName.CENTRAL, probability_weight=1.0)
        ],
    )


class _StubMonteCarloEvaluator:
    """Stands in for the deferred portfolio MC engine: same seam, different internals.

    A real MC evaluator would sample distributions; this only needs to honour the contract to
    prove it slots in. It does not subclass anything — it conforms structurally.
    """

    def run(self, base_case: s.FinancialCase, scenario_set: ScenarioSet) -> ScenarioRunResult:
        return ScenarioRunResult(
            scenario_set_id=scenario_set.scenario_set_id,
            assumption_version=scenario_set.assumption_version,
            results=[],
            weighted_summary=WeightedSummary(
                weighted_p50_cashflows=list(base_case.p50_cashflows),
                weighted_p90_cashflows=list(base_case.p90_cashflows),
                weighted_total_p50_gbp=sum(base_case.p50_cashflows),
                weighted_total_p90_gbp=sum(base_case.p90_cashflows),
                weights_normalised=True,
                original_weight_sum=1.0,
            ),
        )


def _consume(evaluator: ScenarioEvaluator, base: s.FinancialCase, scn: ScenarioSet) -> float:
    """A caller that depends only on the seam (protocol), never on a concrete engine."""
    return evaluator.run(base, scn).weighted_summary.weighted_total_p50_gbp


def test_discrete_engine_satisfies_the_seam() -> None:
    assert isinstance(ScenarioEngine(), ScenarioEvaluator)


def test_monte_carlo_stub_drops_in_behind_the_same_seam() -> None:
    assert isinstance(_StubMonteCarloEvaluator(), ScenarioEvaluator)


def test_caller_is_unchanged_across_both_implementations() -> None:
    base, scn = _base(), _set()
    # The identical caller code runs against the real engine and the MC stub, and both return the
    # agreed ScenarioRunResult contract.
    assert isinstance(_consume(ScenarioEngine(), base, scn), float)
    assert isinstance(_consume(_StubMonteCarloEvaluator(), base, scn), float)
    assert isinstance(ScenarioEngine().run(base, scn), ScenarioRunResult)
    assert isinstance(_StubMonteCarloEvaluator().run(base, scn), ScenarioRunResult)


def test_non_conforming_object_is_not_an_evaluator() -> None:
    assert not isinstance(object(), ScenarioEvaluator)
