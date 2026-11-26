"""Sizing optimiser (M4) — the outer loop over the dispatch + finance contracts.

`SearchSpace` is the validated power×duration grid; `SizingObjective` is the bankable objective
(maximise levered return s.t. P90 DSCR ≥ covenant); `enumerate_sizing` is the "one engine, called
twice" loop that scores each candidate via an injected `DispatchSolver` + the finance model and
selects the best feasible size. `underwrite_spec` is the second call — it reprices the chosen spec
through the identical engine, and `financial_fingerprint` proves the two agree (consistency
contract #1). MILP-ready but never bankable on the placeholder solver.

See docs/dispatch_engine.md (the sizing outer loop section).
"""

from __future__ import annotations

from gridq.schemas import SizingGrid, SizingGridPoint
from gridq.sizing.domain import (
    DEFAULT_DURATION_BAND_H,
    DEFAULT_POWER_FRACTION_RANGE,
    SizingDomain,
)
from gridq.sizing.evaluator import (
    METRIC_LEVERED_RETURN,
    METRIC_PRICED_FLOOR,
    METRIC_PROJECT_IRR,
    ContinuousSizingEvaluator,
    ScoredPoint,
)
from gridq.sizing.objective import Evaluation, SizingObjective
from gridq.sizing.parallel import (
    SolveBenchmark,
    benchmark_parallel_evaluation,
    evaluate_points_parallel,
)
from gridq.sizing.provisioning import (
    DEFAULT_EXCEEDANCE_BASIS,
    ProvisioningAssessment,
    ProvisioningFloors,
    ProvisioningInfeasibleError,
    RechargeAssessment,
    SearchFloors,
    WindowOccurrence,
    assess_provisioning,
    daily_profile_series,
    end_of_tenor_floors,
    exceedance_levels,
    occurrences,
    recharge_scan,
    unmet_series,
    window_mask,
)
from gridq.sizing.search import (
    RefinementRound,
    SizingSearchResult,
    adaptive_refine_search,
    dense_grid_search,
    pso_search,
    search_result_to_sizing_grid,
)
from gridq.sizing.search_space import (
    DEFAULT_DURATION_HOURS,
    SearchSpace,
    SizingCandidate,
)
from gridq.sizing.sizer import (
    CandidateEvaluation,
    SizingRun,
    enumerate_sizing,
)
from gridq.sizing.underwrite import (
    UnderwriteResult,
    financial_fingerprint,
    underwrite_spec,
)

__all__ = [
    "DEFAULT_DURATION_BAND_H",
    "DEFAULT_DURATION_HOURS",
    "DEFAULT_EXCEEDANCE_BASIS",
    "DEFAULT_POWER_FRACTION_RANGE",
    "ProvisioningAssessment",
    "ProvisioningInfeasibleError",
    "SearchFloors",
    "ProvisioningFloors",
    "RechargeAssessment",
    "WindowOccurrence",
    "assess_provisioning",
    "daily_profile_series",
    "end_of_tenor_floors",
    "exceedance_levels",
    "occurrences",
    "recharge_scan",
    "search_floors",
    "unmet_series",
    "window_mask",
    "METRIC_LEVERED_RETURN",
    "METRIC_PRICED_FLOOR",
    "METRIC_PROJECT_IRR",
    "ContinuousSizingEvaluator",
    "ScoredPoint",
    "SearchSpace",
    "SizingCandidate",
    "SizingDomain",
    "SizingObjective",
    "Evaluation",
    "CandidateEvaluation",
    "RefinementRound",
    "SizingRun",
    "SizingGrid",
    "SizingGridPoint",
    "SizingSearchResult",
    "SolveBenchmark",
    "adaptive_refine_search",
    "benchmark_parallel_evaluation",
    "dense_grid_search",
    "enumerate_sizing",
    "evaluate_points_parallel",
    "pso_search",
    "search_result_to_sizing_grid",
    "UnderwriteResult",
    "underwrite_spec",
    "financial_fingerprint",
]
