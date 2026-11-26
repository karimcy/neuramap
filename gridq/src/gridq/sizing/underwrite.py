"""The underwriting re-price seam (M4/M5) — "one engine, called twice".

System Brief consistency contract #1: *sizing searches specifications; underwriting prices the
chosen one. Both consume the same dispatch engine, the same value stack, the same scenario set.*
`underwrite_spec` is the second call: given the spec the sizer chose, it **independently**
reconstructs the dispatch + finance call path from the raw ``(site, assumptions, spec)`` inputs
(it is *not* handed the sizer's pre-built result) and prices that one fixed spec through the *same*
`_evaluate_spec` primitive the enumeration loop uses. Because both paths reduce to that one
deterministic primitive, the chosen spec reprices to identical numbers — so the optimiser cannot
select a spec underwriting would reject (contract #2), and sizing and underwriting can never drift.

`financial_fingerprint` is the timestamp-free projection a consistency check compares — never
object identity, never the wall-clock ``created_at``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gridq.assumptions import AssumptionLibrary
from gridq.dispatch import DispatchSolver, dispatch_constraints_for
from gridq.finance import FinancialModelResult, run_financial_model
from gridq.finance.debt import DebtTerms
from gridq.schemas import MarketStream, SiteModel
from gridq.sizing.objective import SizingObjective
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, _evaluate_spec
from gridq.value_stack.registry import StreamRegistry, default_registry


@dataclass(frozen=True)
class UnderwriteResult:
    """The outcome of underwriting one chosen spec (the second of the "called twice" calls).

    ``bankable`` follows the same rule the sizer's `_select` applies to the chosen candidate (a
    bankable underlying case **and** an available primary levered metric), so an underwrite and the
    sizing run agree on bankability for the same spec.
    """

    spec: SizingCandidate
    evaluation: CandidateEvaluation
    bankable: bool


def underwrite_spec(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    spec: SizingCandidate,
    *,
    dispatch_solver: DispatchSolver,
    objective: SizingObjective,
    requested_streams: list[MarketStream] | None = None,
    debt_terms: DebtTerms | None = None,
    horizon_years: int | None = None,
    scenario: object | None = None,
    registry: StreamRegistry | None = None,
    finance_runner: Callable[..., FinancialModelResult] = run_financial_model,
) -> UnderwriteResult:
    """Price ONE chosen spec through the same dispatch + finance engine (consistency contract #1).

    Reconstructs the stream/constraint set and the `DispatchProblem` independently from the raw
    inputs, then evaluates via the shared `_evaluate_spec`. Identical inputs ⇒ a re-price identical
    to the sizing evaluation of the same spec (proven by a `financial_fingerprint` comparison).
    """
    reg = registry if registry is not None else default_registry()
    streams = requested_streams if requested_streams is not None else reg.stream_ids()
    constraints = dispatch_constraints_for(streams, site, assumptions, registry=reg)
    # The solver's stable model identifier, not its class name: the two-stage search (D32)
    # evaluates through a preloaded wrapper whose MODEL_VERSION equals the underlying solver's,
    # so the sizing-vs-underwriting fingerprint identity (consistency contract #1) holds across
    # paths. Solvers without a MODEL_VERSION keep the class-name behaviour on both sides.
    model_version = (
        getattr(dispatch_solver, "MODEL_VERSION", None) or type(dispatch_solver).__name__
    )

    evaluation = _evaluate_spec(
        site,
        assumptions,
        spec,
        dispatch_solver=dispatch_solver,
        streams=streams,
        constraints=constraints,
        debt_terms=debt_terms,
        horizon_years=horizon_years,
        scenario=scenario,
        finance_runner=finance_runner,
        model_version=model_version,
        registry=reg,
    )
    bankable = evaluation.bankable and objective.primary_metric_available(evaluation)
    return UnderwriteResult(spec=spec, evaluation=evaluation, bankable=bankable)


def financial_fingerprint(evaluation: CandidateEvaluation) -> dict[str, object]:
    """A deterministic, timestamp-free projection of an evaluation's load-bearing finance numbers.

    The consistency check compares this between the sizing evaluation of the selected spec and a
    standalone underwriting re-price: equality proves the two reconstructed the *same canonical
    inputs* (``run_context.input_hash``) and recomputed the *same outputs*. It deliberately excludes
    the wall-clock ``created_at`` (metadata, never a computed value) and object identity, so it is a
    genuine cross-path determinism guarantee — not a comparison of the same object to itself.
    """
    fin = evaluation.financial
    return {
        "power_kw": evaluation.candidate.power_kw,
        "duration_hours": evaluation.candidate.duration_hours,
        "priceable": evaluation.priceable,
        "bankable": evaluation.bankable,
        "min_p90_dscr": evaluation.min_p90_dscr,
        "covenant": evaluation.covenant,
        "passes_covenant": evaluation.passes_covenant,
        "project_irr_pct": evaluation.project_irr_pct,
        "levered_return_pct": evaluation.levered_return_pct,
        "p50_cashflows": None if fin is None else list(fin.case.p50_cashflows),
        "p90_cashflows": None if fin is None else list(fin.case.p90_cashflows),
        "dscr_by_year": None if fin is None else list(fin.case.dscr_summary.by_year),
        "input_hash": None if fin is None else fin.run_context.input_hash,
    }
