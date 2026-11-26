"""SimpleSolver — the labelled placeholder dispatch solver (integration only).

It proves the dispatch + sizing + finance loop runs end-to-end and conforms to the
`DispatchResult` contract before the real MILP exists. It **never** produces underwriting-grade
output: every result is ``implementation_level=baseline`` / ``bankable_status=not_bankable``,
prices nothing (every value stream 0), and — critically — **runs no dispatch, so it reports the
annual charge/discharge volume as unavailable (``None``), never an invented number.** The real
half-hourly MILP (linopy/HiGHS) will implement the same `DispatchSolver` Protocol and replace
this. See docs/dispatch_engine.md.

This is the dispatch-layer twin of `gridq.engine.BaselineEngine` (the outer-seam placeholder):
both are deterministic plumbing that stay honest about being non-bankable.
"""

from __future__ import annotations

from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.engine.errors import InvalidRequestError
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    BankableStatus,
    Firmness,
    ImplementationLevel,
    MarketStream,
    SolverStatus,
    ValueStreamResult,
)

_PLACEHOLDER_WARNINGS = (
    "SimpleSolver: integration plumbing only — implementation_level=baseline, NOT bankable.",
    "No dispatch is solved and no revenue is priced; every value stream is 0 pending the real "
    "MILP (linopy/HiGHS).",
    "Annual charge/discharge volume is unavailable: the placeholder runs no dispatch, so the "
    "volume is reported as None and never invented. Finance levies-on-charge and price-to-target "
    "stay unavailable until a real dispatch supplies it.",
)


class SimpleSolver:
    """Integration-only dispatch placeholder. See module docstring and docs/dispatch_engine.md."""

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        """Return a deterministic, never-bankable dispatch result for one fixed spec."""
        if not isinstance(problem, DispatchProblem):
            raise InvalidRequestError(
                f"SimpleSolver.solve expects a DispatchProblem, got {type(problem).__name__}"
            )

        warnings = list(_PLACEHOLDER_WARNINGS)

        # Data sufficiency: a real dispatch must count charge load against the connection, which
        # needs an observed peak / connection data. Mirror BaselineEngine's honest degrade.
        max_import = problem.site_model.load_data.max_import_kw
        if max_import is None or max_import <= 0:
            solver_status = SolverStatus.INSUFFICIENT_DATA
            warnings.append(
                "Insufficient data: load_data.max_import_kw is missing or non-positive — a real "
                "dispatch cannot bound charge load against the connection (MIC/ASC). Nothing is "
                "dispatched or priced."
            )
        else:
            solver_status = SolverStatus.BASELINE

        requested = problem.requested_streams or list(MarketStream)
        value_streams = [
            ValueStreamResult(
                stream=stream,
                annual_revenue_gbp=0.0,
                annual_cost_gbp=0.0,
                firmness=Firmness.EXCLUDED,
                gate_status="not_priced",
                warnings=["placeholder: not priced yet"],
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
                warnings=["placeholder: gated upside, not priced"],
            )
            for stream in requested
            if stream in GATED_UPSIDE_STREAMS
        ]

        return DispatchResult(
            # Echo the requested spec — it is an input, not a solver choice.
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=ImplementationLevel.BASELINE,
            bankable_status=BankableStatus.NOT_BANKABLE,
            solver_status=solver_status,
            dispatch_schedule_ref=None,
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=[],
            # No dispatch solved → charge/discharge volume genuinely unknown (never invented).
            annual_charge_kwh=None,
            annual_discharge_kwh=None,
            annual_throughput_mwh=None,
            equivalent_full_cycles=None,
            warnings=warnings,
        )
