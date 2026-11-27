"""BaselineEngine — a deliberately limited engine for integration testing only.

It proves the pipeline runs end-to-end and conforms to the `OptimisationResult` contract.
It NEVER produces underwriting-grade output: every result is `implementation_level=
baseline` and `bankable_status=not_bankable`, with all revenues left at 0. The real MILP
will implement the same `OptimisationEngine` interface and replace this.
"""

from __future__ import annotations

from gridq.assumptions import AssumptionLibrary
from gridq.constraint_library import library_ref
from gridq.engine.errors import InvalidRequestError
from gridq.provenance import get_code_version
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    BankableStatus,
    Firmness,
    ImplementationLevel,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    SiteModel,
    SolveMode,
    SolverMetadata,
    SolverStatus,
    ValueStreamResult,
)

# Illustrative INTEGRATION heuristic — NOT an underwriting assumption and deliberately not
# in the assumption library. The baseline result is always not_bankable; this fraction only
# produces plumbing-test numbers and must never be read as a real sizing recommendation.
BASELINE_POWER_FRACTION = 0.1
BASELINE_DURATION_HOURS = 2.0

_BASELINE_WARNINGS = (
    "BaselineEngine: integration plumbing only — implementation_level=baseline, NOT bankable.",
    "No revenues are priced; all value streams are 0 pending the real engine.",
)


class BaselineEngine:
    """Integration-only engine. See module docstring and engine/README.md."""

    def build_optimisation_request(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario_set: str | None = None,
    ) -> OptimisationRequest:
        """Assemble a request from a site + assumption release (requests the full stream stack)."""
        return OptimisationRequest(
            request_id=f"req_{site_model.site_id}",
            site_model=site_model,
            assumption_version=assumptions.metadata.version,
            scenario_set_id=scenario_set,
            requested_streams=list(MarketStream),
            solve_mode=SolveMode.BASELINE,
        )

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        """Return a deterministic, never-bankable baseline result for plumbing."""
        if not isinstance(request, OptimisationRequest):
            raise InvalidRequestError(
                f"BaselineEngine.solve expects an OptimisationRequest, got {type(request).__name__}"
            )

        site = request.site_model
        max_import = site.load_data.max_import_kw
        warnings = list(_BASELINE_WARNINGS)

        if max_import is None or max_import <= 0:
            selected_power_kw: float | None = None
            selected_energy_kwh: float | None = None
            duration_hours: float | None = None
            solver_status = SolverStatus.INSUFFICIENT_DATA
            warnings.append(
                "Insufficient data: load_data.max_import_kw is missing or non-positive — "
                "cannot size even a baseline system."
            )
        else:
            selected_power_kw = round(max_import * BASELINE_POWER_FRACTION, 1)
            selected_energy_kwh = round(selected_power_kw * BASELINE_DURATION_HOURS, 1)
            duration_hours = BASELINE_DURATION_HOURS
            solver_status = SolverStatus.BASELINE

        requested = request.requested_streams or list(MarketStream)
        value_streams = [
            ValueStreamResult(
                stream=stream,
                annual_revenue_gbp=0.0,
                annual_cost_gbp=0.0,
                firmness=Firmness.EXCLUDED,
                gate_status="not_priced",
                warnings=["baseline: not priced yet"],
            )
            for stream in requested
            if stream not in GATED_UPSIDE_STREAMS
        ]
        gated_upside = [
            ValueStreamResult(
                stream=stream,
                annual_revenue_gbp=0.0,
                annual_cost_gbp=0.0,
                firmness=Firmness.CONDITIONAL,
                gate_status="gated",
                unlock_condition="route-to-market partner + export consents (placeholder)",
                warnings=["baseline: gated upside, not priced"],
            )
            for stream in requested
            if stream in GATED_UPSIDE_STREAMS
        ]

        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.BASELINE,
            bankable_status=BankableStatus.NOT_BANKABLE,
            selected_power_kw=selected_power_kw,
            selected_energy_kwh=selected_energy_kwh,
            duration_hours=duration_hours,
            dispatch_schedule_ref=None,
            value_streams=value_streams,
            constraints_binding=[],
            gated_upside=gated_upside,
            solver_status=solver_status,
            solver_metadata=SolverMetadata(
                model_version="baseline",
                assumption_version=request.assumption_version,
                code_version=get_code_version(),
            ),
            warnings=warnings,
            # IC-12 3.2.0 (WP-SIZE-02): the constraint library in force for this build. The
            # baseline solves nothing; the stamp still names the set any dispatch would apply.
            constraint_library=library_ref(),
        )
