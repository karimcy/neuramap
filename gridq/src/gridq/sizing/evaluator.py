"""Continuous-point sizing evaluator (issue 016 stage 3) — f(power, energy) → scored evaluation.

Wraps the existing single-spec pricing primitive (`gridq.sizing.sizer._evaluate_spec` — the exact
full-year dispatch + real finance path, never a heuristic) so the adaptive search can score an
arbitrary continuous ``(power_kw, energy_kwh)`` point from the `SizingDomain`. Stream and
constraint resolution happens once at construction (identical to `enumerate_sizing`), so each
call pays only the dispatch + finance cost — the invariant issue 016 protects: **the inner
evaluator is the same exact LP for every search method**.

Objective-metric honesty (north_star W5 / issue 016 scope addition): every scored point RECORDS
which metric it was scored on —

- ``levered_return_pct`` — the advertised bankable objective (equity IRR; needs injected
  `DebtTerms`, unavailable on the real path until B001/gearing lands);
- ``project_irr_pct`` — the unlevered, labelled fallback (never bankable);
- ``priced_floor_value_gbp`` — the last-resort label (NET FIRM/CONTRACTED £/yr — revenue minus
  that stack's own dispatch cost, N5) for a point carrying NO return metric at all. Since N4 it
  is no longer any selector's ranking: `MilpEngine._choose` ranks on `objective.rank_key`, and a
  point with no return metric is not `candidate_viable`, so it cannot be recommended —

so a refined surface can never silently claim to be the levered objective when it is not, and
the stage-9 bake-off can run both with and without injected debt and label each result.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from gridq.assumptions import AssumptionLibrary
from gridq.dispatch import DispatchSolver, dispatch_constraints_for
from gridq.finance import FinancialModelResult, run_financial_model
from gridq.finance.debt import DebtTerms
from gridq.schemas import Firmness, MarketStream, OptimisationResult, SiteModel
from gridq.sizing.objective import SizingObjective
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, _evaluate_spec
from gridq.value_stack.interface import DispatchConstraintDescriptor
from gridq.value_stack.registry import StreamRegistry, default_registry

# The metric names a ScoredPoint can carry (see module docstring for their meaning/order).
METRIC_LEVERED_RETURN = "levered_return_pct"
METRIC_PROJECT_IRR = "project_irr_pct"
METRIC_PRICED_FLOOR = "priced_floor_value_gbp"

_PRICED_FIRMNESS = frozenset({Firmness.FIRM, Firmness.CONTRACTED})


def priced_floor_value_gbp(result: OptimisationResult) -> float:
    """NET priced-floor (FIRM/CONTRACTED) £/yr of a result — the indicative fallback metric.

    Mirrors ``MilpEngine._floor_value`` exactly (kept in sync by a parity test; the shared home
    lands with the Wave-10 valuation-hook refactor). Gated upside is structurally excluded — it
    lives on ``gated_upside``, never in ``value_streams`` with priced firmness.

    N5 (2026-08-10 review): the sum is ``revenue − cost``, not gross revenue. ``_split_signed``
    deliberately routes the negative part of a signed dispatch component onto ``annual_cost_gbp``
    (a tariff-arbitrage stream is explicitly allowed to be a NET COST), and finance nets it in
    EBITDA — so ranking on gross revenue overstated every candidate by its own dispatch cost.
    That omission is size-correlated (charge-energy cost grows with power), which INVERTS the
    ranking between viable candidates: a big candidate whose extra revenue is mostly extra charge
    cost outranked a smaller one with the same EBITDA and a quarter of the capex.
    """
    return round(
        sum(
            v.annual_revenue_gbp - v.annual_cost_gbp
            for v in result.value_streams
            if v.firmness in _PRICED_FIRMNESS
        ),
        2,
    )


@dataclass(frozen=True)
class ScoredPoint:
    """One continuous point scored by the exact dispatch + finance path.

    ``objective_metric`` names the metric ``objective_value`` carries (never mix metrics when
    comparing points — a search must assert uniformity across its sample). ``feasible`` is the
    `SizingObjective` covenant/deliverability gate verdict; the full `CandidateEvaluation` rides
    along so nothing downstream needs re-solving.
    """

    power_kw: float
    energy_kwh: float
    duration_hours: float
    objective_value: float
    objective_metric: str
    feasible: bool
    evaluation: CandidateEvaluation


class ContinuousSizingEvaluator:
    """Score arbitrary continuous (power, energy) points through the exact dispatch+finance path.

    Construction mirrors `enumerate_sizing`'s per-run setup (streams resolved from the registry,
    constraints built once via `dispatch_constraints_for`); `evaluate` mirrors its per-candidate
    step exactly — same ``_evaluate_spec``, same solver, same finance runner — so a point scored
    here is indistinguishable from the same point scored by the grid path (issue 016's
    certification premise). Deterministic: identical construction + point → identical scores.
    """

    def __init__(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        *,
        dispatch_solver: DispatchSolver,
        objective: SizingObjective,
        requested_streams: list[MarketStream] | None = None,
        debt_terms: DebtTerms | None = None,
        horizon_years: int | None = None,
        scenario: object | None = None,
        registry: StreamRegistry | None = None,
        finance_runner: Callable[..., FinancialModelResult] = run_financial_model,
        extra_constraints: list[DispatchConstraintDescriptor] | None = None,
    ) -> None:
        self._site = site
        self._assumptions = assumptions
        self._solver = dispatch_solver
        self._objective = objective
        reg = registry if registry is not None else default_registry()
        # D61: `_evaluate_spec` re-resolves the award at each point's own power, through this.
        self._registry = reg
        self._streams = requested_streams if requested_streams is not None else reg.stream_ids()
        self._constraints = dispatch_constraints_for(self._streams, site, assumptions, registry=reg)
        # W7-3: additive seam commitments (C026 payloads) — default None changes nothing.
        if extra_constraints:
            self._constraints = [*self._constraints, *extra_constraints]
        self._debt_terms = debt_terms
        self._horizon_years = horizon_years
        self._scenario = scenario
        self._finance_runner = finance_runner
        # Stable model identifier when the solver declares one — keeps the sizing-vs-
        # underwriting fingerprint identity (F-008) on the continuous path too.
        self._model_version = (
            getattr(dispatch_solver, "MODEL_VERSION", None) or type(dispatch_solver).__name__
        )

    def evaluate(self, power_kw: float, energy_kwh: float) -> ScoredPoint:
        """Dispatch + price one continuous point (duration derived as energy/power)."""
        if power_kw <= 0 or energy_kwh <= 0:
            raise ValueError(
                f"evaluation point must be strictly positive, got ({power_kw} kW, {energy_kwh} kWh)"
            )
        candidate = SizingCandidate(power_kw=power_kw, duration_hours=energy_kwh / power_kw)
        evaluation = _evaluate_spec(
            self._site,
            self._assumptions,
            candidate,
            dispatch_solver=self._solver,
            streams=self._streams,
            constraints=self._constraints,
            debt_terms=self._debt_terms,
            horizon_years=self._horizon_years,
            scenario=self._scenario,
            finance_runner=self._finance_runner,
            model_version=self._model_version,
            registry=self._registry,
        )
        metric, value = self._score(evaluation)
        return ScoredPoint(
            power_kw=candidate.power_kw,
            energy_kwh=candidate.energy_kwh,
            duration_hours=candidate.duration_hours,
            objective_value=value,
            objective_metric=metric,
            feasible=self._objective.feasible(evaluation),
            evaluation=evaluation,
        )

    def evaluate_many(self, points: Iterable[tuple[float, float]]) -> list[ScoredPoint]:
        """Score points in deterministic input order (the seam stage 4's parallel harness wraps)."""
        return [self.evaluate(p, e) for p, e in points]

    @staticmethod
    def _score(evaluation: CandidateEvaluation) -> tuple[str, float]:
        """The honest (metric-name, value) for one evaluation — best available, always labelled.

        Mirrors the production preference order (`SizingObjective.rank_key`): the levered return
        when debt made it computable, the unlevered project IRR as the labelled fallback, else
        the priced-floor £ — a label for a point no selector may recommend (N4: it carries no
        return metric, so `candidate_viable` refuses it), 0.0 for an unpriceable candidate.
        """
        if evaluation.levered_return_pct is not None:
            return METRIC_LEVERED_RETURN, evaluation.levered_return_pct
        if evaluation.project_irr_pct is not None:
            return METRIC_PROJECT_IRR, evaluation.project_irr_pct
        return METRIC_PRICED_FLOOR, priced_floor_value_gbp(evaluation.optimisation_result)
