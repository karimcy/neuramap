"""The sizing outer loop (M4) — enumerate the search space against the dispatch + finance contracts.

This is the "one engine, called twice" loop: for each candidate size in the `SearchSpace`, solve
the dispatch (via an injected `DispatchSolver`), wrap the result in the frozen `OptimisationResult`
contract, run the deterministic finance model, and score the candidate against the `SizingObjective`
(maximise levered return s.t. P90 DSCR ≥ covenant). The best feasible candidate is selected.

It is **MILP-ready but not bankable on the placeholder**: with `SimpleSolver` nothing is priced and
no charge/discharge volume exists, so the DSCR is unavailable (no injected debt) or fails, no
candidate is feasible, and the run is honestly ``bankable=False`` with no selection. Swap in the
real dispatch solver (same Protocol) and inject real debt terms and the loop produces a real
selection with **no change here**.

Determinism: identical inputs → identical selection. The per-candidate `OptimisationResult` carries
a wall-clock ``created_at`` (schema metadata, excluded from every finance hash); use
:meth:`SizingRun.fingerprint` for determinism comparisons rather than raw object equality.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from gridq.assumptions import AssumptionLibrary
from gridq.constraint_library import library_ref
from gridq.dispatch import (
    DispatchProblem,
    DispatchResult,
    DispatchSolver,
    candidate_dispatch_constraints,
    dispatch_constraints_for,
    to_finance_dispatch_energy,
)
from gridq.finance import FinancialModelResult, InsufficientInputsError, run_financial_model
from gridq.finance.debt import DebtTerms
from gridq.provenance import get_code_version
from gridq.schemas import (
    MarketStream,
    OptimisationResult,
    SiteModel,
    SizingGrid,
    SizingGridPoint,
    SizingGridStreamRevenue,
    SolverMetadata,
    is_priced_floor,
)
from gridq.sizing.deliverability import assess_end_of_life_deliverability
from gridq.sizing.objective import SizingObjective, candidate_viable
from gridq.sizing.search_space import SearchSpace, SizingCandidate
from gridq.value_stack.interface import DispatchConstraintDescriptor
from gridq.value_stack.registry import StreamRegistry, default_registry


@dataclass(frozen=True)
class CandidateEvaluation:
    """One evaluated candidate: its dispatch+finance outcome and the metrics the objective reads.

    ``priceable=False`` means finance could not build a cashflow model for this candidate (e.g. a
    dispatch that returned no positive sizing); it is recorded, not dropped, and is never selected.
    ``levered_return_pct`` is the finance model's **equity IRR** (``FinancialCase.equity_irr``,
    P50 basis) when debt is present, else ``None`` (no debt ⇒ no levered split; honest-degrade —
    never guessed). ``end_of_life_deliverable`` is the day-one-for-end-of-life capability verdict:
    ``None`` is an unassessed data-ask (the placeholder/test doubles supply no late-life signal —
    decision D12), ``False`` a known non-deliverable spec the objective rejects, ``True`` only when
    a real engine verifies it.
    """

    candidate: SizingCandidate
    optimisation_result: OptimisationResult
    priceable: bool
    bankable: bool
    min_p90_dscr: float | None
    covenant: float | None
    passes_covenant: bool | None
    levered_return_pct: float | None
    project_irr_pct: float | None
    financial: FinancialModelResult | None
    end_of_life_deliverable: bool | None = None
    warnings: list[str] = field(default_factory=list)
    # Two-stage search provenance (D32): which stage produced this evaluation and whether it was
    # a typical-period proxy solve (search guidance only — a proxy result is never the reported
    # selection; the confirm stage re-solves on the full series). Legacy enumeration = "grid".
    stage: str = "grid"
    proxy: bool = False


#: The bases on which `_select` may choose a size, and the only values `SizingRun.selection_basis`
#: ever carries. They are three different claims and must never be interchanged:
#:
#: * ``covenant`` — a P90 DSCR was measured for the chosen candidate and it MET the covenant. The
#:   only basis that is a covenant-feasible optimum.
#: * ``unlevered`` — no debt structure was resolved on the sizing path, so no covenant could be
#:   applied to any candidate and the size was ranked on the UNLEVERED project IRR
#:   (`docs/validation_requirements.md` §3: a financing-data absence underwrites unlevered with the
#:   band adjusted, never a refusal). The chosen size has neither passed nor failed a covenant.
#: * ``none`` — nothing was selected.
SELECTION_BASIS_COVENANT = "covenant"
SELECTION_BASIS_UNLEVERED = "unlevered"
SELECTION_BASIS_NONE = "none"


@dataclass(frozen=True)
class SizingRun:
    """The deterministic outcome of one sizing enumeration.

    ``selected`` is the chosen candidate (``None`` when none was selected on any basis) and
    ``selection_basis`` says WHICH basis chose it — read the two together, always. A
    ``covenant`` selection met the P90-DSCR covenant; an ``unlevered`` one never met it and never
    failed it, because no debt structure was resolved on the sizing path and the gate therefore
    never ran (`docs/validation_requirements.md` §3). ``bankable`` is ``True``
    only if the selected candidate is itself bankable **and** the primary objective metric (levered
    return) was actually computed — never on the placeholder, and never on an unlevered basis.
    ``rejected_by_covenant`` lists the
    candidates the P90-DSCR covenant explicitly rejected (proving the gate fires when DSCR is
    available), distinct from candidates whose DSCR was simply unavailable.

    ``indicative_selection`` (N16) is the candidate a CALLER chose from this run when ``selected``
    is ``None`` — the engine's indicative fallback. Without it the persisted grid and the quote
    disagreed about the most basic fact there is: `sizing_grid()` copied ``selected=None`` and the
    reason "no bankable candidate: no size meets the P90 DSCR covenant" onto the same
    `QuoteRecord` whose `OptimisationResult` said ``selection_status=SELECTED`` and named a kW.
    Both statements were persisted, side by side, and one of them was false. The run records what
    was actually chosen and by which metric; it never invents a choice of its own.
    """

    search_space_size: int
    candidates_evaluated: list[CandidateEvaluation]
    selected: CandidateEvaluation | None
    selection_reason: str
    rejected_by_covenant: list[SizingCandidate]
    bankable: bool
    assumption_version: str
    warnings: list[str] = field(default_factory=list)
    # Which search produced this run (D32): "enumerate-v1" (legacy grid) or "m4-two-stage-v2".
    search_version: str = "enumerate-v1"
    # N16: the candidate the caller indicatively chose when no candidate met the covenant, and the
    # metric that chose it. Additive and optional — a run nobody chose from carries None/None and
    # the grid is byte-identical to before.
    indicative_selection: CandidateEvaluation | None = None
    indicative_selection_reason: str | None = None
    #: Which basis chose ``selected`` (one of `SELECTION_BASIS_*`). ``_select`` is the only
    #: production writer and always sets it; the default reproduces the pre-2026-08-17 meaning of
    #: the field for any hand-built run, which is exactly what `sizing_grid()` used to infer from
    #: ``selected is not None`` alone. It is ignored entirely when ``selected`` is ``None``.
    selection_basis: str = SELECTION_BASIS_COVENANT

    def with_indicative_selection(
        self, chosen: CandidateEvaluation | None, reason: str
    ) -> SizingRun:
        """Return a copy recording the candidate a caller indicatively chose from this run.

        Frozen, so this returns a new run rather than mutating: the search's own verdict is a
        fact about the search and must not be edited after the fact. ``chosen=None`` (nothing was
        chosen) returns ``self`` unchanged, and a run that already has a covenant-feasible
        ``selected`` is likewise unchanged — an indicative fallback cannot overwrite a real
        selection.
        """
        if chosen is None or self.selected is not None:
            return self
        return replace(self, indicative_selection=chosen, indicative_selection_reason=reason)

    def fingerprint(self) -> dict[str, object]:
        """A deterministic, timestamp-free projection for determinism/golden tests."""
        return {
            "search_space_size": self.search_space_size,
            "search_version": self.search_version,
            "bankable": self.bankable,
            "selection_reason": self.selection_reason,
            # The basis rides the determinism projection because it is a CLAIM about the selection,
            # not decoration: a change from "covenant" to "unlevered" over the same kW is exactly
            # the kind of drift a golden must notice, and a projection that carried the size but
            # not the basis could not see it.
            "selection_basis": self.selection_basis if self.selected is not None else None,
            "selected": (
                None
                if self.selected is None
                else {
                    "power_kw": self.selected.candidate.power_kw,
                    "duration_hours": self.selected.candidate.duration_hours,
                    "energy_kwh": self.selected.candidate.energy_kwh,
                }
            ),
            "rejected_by_covenant": [
                (c.power_kw, c.duration_hours) for c in self.rejected_by_covenant
            ],
            "candidates": [
                {
                    "power_kw": e.candidate.power_kw,
                    "duration_hours": e.candidate.duration_hours,
                    "priceable": e.priceable,
                    "bankable": e.bankable,
                    "solver_status": e.optimisation_result.solver_status.value,
                    "min_p90_dscr": e.min_p90_dscr,
                    "passes_covenant": e.passes_covenant,
                    "project_irr_pct": e.project_irr_pct,
                    "levered_return_pct": e.levered_return_pct,
                    "end_of_life_deliverable": e.end_of_life_deliverable,
                    # The candidate's priced floor rides the projection for the same reason the
                    # ratios do: it is now a persisted claim about what the candidate earned, and a
                    # determinism projection that carried the IRR but not the revenue the IRR was
                    # computed FROM could not see a revenue drift that left the ratio unmoved.
                    # ``None`` (unpriceable candidate) is carried as None, never flattened to 0.0.
                    "annual_revenue_gbp": (
                        None
                        if e.financial is None
                        else e.financial.cashflows.total_annual_revenue_gbp
                    ),
                }
                for e in self.candidates_evaluated
            ],
        }

    def sizing_grid(self) -> SizingGrid:
        """Project the full evaluation into the serializable, persistable `SizingGrid` contract.

        Every evaluated candidate becomes a `SizingGridPoint` in the same deterministic
        ``(power, duration)`` order `SearchSpace.candidates()` enumerates; ``selected`` is the
        chosen optimum (``None`` when no candidate met the covenant — honest, not an error). This
        is the additive seam the Wave 5 3D sizing surface reads: it copies each candidate's
        plottable scalars straight off its `CandidateEvaluation`, inventing nothing. The
        grid-level ``covenant`` is the first available per-candidate covenant (constant across a
        run; ``None`` when no candidate was priced against one).

        Since grid contract ``0.2.0`` each point also carries the candidate's **priced floor, its
        recorded cost stack and its per-stream breakdown** (`_grid_point`, `_grid_streams`). That is
        what lets a run which selected NOTHING still state what its best candidate measured: before
        it, the persisted grid held every ratio the objective ranks on and not one £, so a pack with
        no specification in it read as "this site earns nothing" on sites whose own recorded verdict
        said the dispatch had discharged real energy.

        N16: when the run has no covenant-feasible ``selected`` but a caller indicatively chose a
        candidate, the grid marks THAT point and names the metric that chose it, so the surface
        and the quote agree about which system is being quoted. ``selection_basis`` says which of
        the two it is, so an indicative point is never read as a covenant-feasible optimum.

        **The basis is copied off the run, never inferred from ``selected is not None``.** It used
        to be inferred, and that hard-coded ``"covenant"`` was the mislabelling trap: the moment
        the search was allowed to select on an UNLEVERED basis (2026-08-17), a size that had never
        been tested against a covenant would have persisted as covenant-feasible into the pack.
        `_select` records which basis it actually used and this copies it.
        """
        covenant = next(
            (e.covenant for e in self.candidates_evaluated if e.covenant is not None), None
        )
        if self.selected is not None:
            selected = _grid_point(self.selected)
            basis = self.selection_basis
            reason = self.selection_reason
        elif self.indicative_selection is not None:
            selected = _grid_point(self.indicative_selection)
            basis = "indicative_fallback"
            reason = self.indicative_selection_reason or self.selection_reason
        else:
            selected = None
            basis = "none"
            reason = self.selection_reason
        return SizingGrid(
            search_space_size=self.search_space_size,
            covenant=covenant,
            bankable=self.bankable,
            selection_reason=reason,
            points=[_grid_point(e) for e in self.candidates_evaluated],
            selected=selected,
            selection_basis=basis,
            search_version=self.search_version,
        )


def _grid_streams(evaluation: CandidateEvaluation) -> list[SizingGridStreamRevenue]:
    """Project this candidate's own value streams onto the grid (copy, no maths, no filtering).

    Every stream the candidate's dispatch reported is carried, floor and held-out alike, because
    the held-out ones are exactly what makes a floor of £0 readable: a reader has to be able to see
    that ``DUOS_RED_BAND_AVOIDANCE`` sat outside the floor on a shut gate rather than inside it
    earning nothing (D45). Floor membership is stamped from `schemas.market.is_priced_floor`, the
    same predicate `finance.cashflows.revenue_by_stream` filters on, so this breakdown and
    ``SizingGridPoint.annual_revenue_gbp`` cannot disagree about which streams compose the floor.

    Gated upside (`OptimisationResult.gated_upside`) is deliberately NOT copied: it is never in the
    priced floor on any basis, it is reported per-run rather than per-candidate, and putting it
    beside a floor figure at candidate level is the one place a reader could add the two together.
    """
    return [
        SizingGridStreamRevenue(
            stream=v.stream.value,
            annual_revenue_gbp=v.annual_revenue_gbp,
            firmness=v.firmness.value,
            gate_status=v.gate_status,
            in_priced_floor=is_priced_floor(v),
        )
        for v in evaluation.optimisation_result.value_streams
    ]


def _grid_point(evaluation: CandidateEvaluation) -> SizingGridPoint:
    """Project one `CandidateEvaluation` onto a serializable `SizingGridPoint` (copy, no maths).

    The £ fields come off the evaluation's own `FinancialModelResult.cashflows` — one hop deeper
    than the ratios, still a verbatim copy: no total is re-summed here, so the grid's floor figure
    is the identical number the finance model built this candidate's cashflows on.

    **An unpriceable candidate carries ``None``, never ``0.0``.** ``financial is None`` means
    `InsufficientInputsError` — no cashflow model exists, so no floor revenue and no cost total was
    ever computed. Writing zero there would manufacture the precise defect this projection was
    extended to remove: a confidently wrong £0 that reads identically to a measured one and flows
    into any "best candidate earned…" sentence downstream (CLAUDE.md §2).
    """
    cashflows = None if evaluation.financial is None else evaluation.financial.cashflows
    return SizingGridPoint(
        power_kw=evaluation.candidate.power_kw,
        duration_hours=evaluation.candidate.duration_hours,
        energy_kwh=evaluation.candidate.energy_kwh,
        levered_return_pct=evaluation.levered_return_pct,
        project_irr_pct=evaluation.project_irr_pct,
        min_p90_dscr=evaluation.min_p90_dscr,
        passes_covenant=evaluation.passes_covenant,
        priceable=evaluation.priceable,
        bankable=evaluation.bankable,
        solver_status=evaluation.optimisation_result.solver_status,
        end_of_life_deliverable=evaluation.end_of_life_deliverable,
        stage=evaluation.stage,
        proxy=evaluation.proxy,
        annual_revenue_gbp=(None if cashflows is None else cashflows.total_annual_revenue_gbp),
        annual_operating_cost_gbp=(
            None if cashflows is None else cashflows.total_annual_operating_cost_gbp
        ),
        annual_dispatch_cost_gbp=(
            None if cashflows is None else cashflows.annual_dispatch_cost_gbp
        ),
        revenue_streams=_grid_streams(evaluation),
    )


def _to_optimisation_result(
    candidate: SizingCandidate,
    dispatch_result: DispatchResult,
    *,
    assumption_version: str,
    model_version: str,
) -> OptimisationResult:
    """Map a per-candidate `DispatchResult` onto the frozen `OptimisationResult` contract.

    The implementation level + bankable status are carried straight from the dispatch result (the
    placeholder is BASELINE / NOT_BANKABLE, which the schema validator hard-couples), and the
    value/gated streams are passed through already split — so the assembled result is schema-valid
    and downstream finance/outputs consume it unchanged when the real engine swaps in.
    """
    # Use lossless float repr in the ids so two distinct candidates (kept by SearchSpace's
    # exact-float dedup) can never collide on a result_id — keeps every result traceable.
    return OptimisationResult(
        result_id=f"res_size_{candidate.power_kw!r}kw_{candidate.duration_hours!r}h",
        request_id=f"req_size_{candidate.power_kw!r}kw_{candidate.duration_hours!r}h",
        implementation_level=dispatch_result.implementation_level,
        bankable_status=dispatch_result.bankable_status,
        selected_power_kw=dispatch_result.power_kw,
        selected_energy_kwh=dispatch_result.energy_kwh,
        duration_hours=dispatch_result.duration_hours,
        dispatch_schedule_ref=dispatch_result.dispatch_schedule_ref,
        value_streams=list(dispatch_result.value_streams),
        gated_upside=list(dispatch_result.gated_upside),
        constraints_binding=list(dispatch_result.constraints_binding),
        solver_status=dispatch_result.solver_status,
        solver_metadata=SolverMetadata(
            model_version=model_version,
            assumption_version=assumption_version,
            code_version=get_code_version(),
        ),
        warnings=list(dispatch_result.warnings),
        # IC-12 3.2.0 (WP-SIZE-02): the constraint library the candidate's dispatch compiled.
        constraint_library=library_ref(),
    )


# The end-of-life deliverability data-ask (D12): since W7-3 the verdict IS computed — the
# closed-form check in `gridq.sizing.deliverability` — whenever typed availability commitments
# (C026) ride the constraint seam with a tenor. Without them (or without a usable degradation
# fade) it remains an unassessed data-ask — never fabricated to a pass.
_EOL_DATA_ASK = (
    "End-of-life deliverability not assessed: no tenored availability commitment on the "
    "constraint seam (or degradation fade unusable) — recorded as a data-ask, not a pass (D12)."
)


def _evaluate_spec(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    candidate: SizingCandidate,
    *,
    dispatch_solver: DispatchSolver,
    streams: list[MarketStream],
    constraints: list[DispatchConstraintDescriptor],
    debt_terms: DebtTerms | None,
    horizon_years: int | None,
    scenario: object | None,
    finance_runner: Callable[..., FinancialModelResult],
    model_version: str,
    registry: StreamRegistry,
) -> CandidateEvaluation:
    """Dispatch + price ONE fixed spec into a `CandidateEvaluation`.

    The single per-spec pricing primitive shared by the sizing enumeration (called once per
    candidate) and the standalone underwriting re-price (`gridq.sizing.underwrite_spec`) — this is
    the "one engine, called twice" seam (System Brief consistency contract #1). It does no
    selection or ranking; it evaluates exactly one spec. An unpriceable result
    (`InsufficientInputsError`) is recorded as ``priceable=False``, never raised.

    ``levered_return_pct`` carries the finance model's equity IRR (``None`` when no debt is
    injected); ``end_of_life_deliverable`` is left ``None`` (a data-ask — see `_EOL_DATA_ASK`).

    **D61 (2026-11-22): the candidate reserves its OWN capability.** ``constraints`` arrives built
    once per search, at the candidate-less default (the local-flex award clamped to the search
    floor's capability). Its first act is to re-resolve every candidate-dependent descriptor at
    THIS candidate's power, so the solver and the end-of-tenor check below both test the volume
    this size can actually hold. ``registry`` is REQUIRED on purpose: a caller that is not updated
    raises ``TypeError`` instead of silently reserving the floor.
    """
    constraints = candidate_dispatch_constraints(
        constraints,
        streams,
        site,
        assumptions,
        candidate_power_kw=candidate.power_kw,
        registry=registry,
    )
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=candidate.power_kw,
        energy_kwh=candidate.energy_kwh,
        duration_hours=candidate.duration_hours,
        requested_streams=list(streams),
        constraints=constraints,
        scenario=scenario,
    )
    dispatch_result = dispatch_solver.solve(problem)
    optimisation_result = _to_optimisation_result(
        candidate,
        dispatch_result,
        assumption_version=assumptions.metadata.version,
        model_version=model_version,
    )
    # W7-3: the closed-form end-of-tenor check — real verdict when tenored commitments ride the
    # seam; honest None + data-ask otherwise (no extra solves, pure arithmetic).
    eol_verdict, eol_notes = assess_end_of_life_deliverability(
        power_kw=candidate.power_kw,
        energy_kwh=candidate.energy_kwh,
        duration_hours=candidate.duration_hours,
        constraints=constraints,
        assumptions=assumptions,
    )
    eol_warnings = eol_notes if eol_verdict is not None else [*eol_notes, _EOL_DATA_ASK]

    try:
        financial = finance_runner(
            site,
            optimisation_result,
            assumptions,
            horizon_years=horizon_years,
            scenario=scenario,
            debt_terms=debt_terms,
            dispatch_energy=to_finance_dispatch_energy(dispatch_result),
        )
    except InsufficientInputsError as exc:
        return CandidateEvaluation(
            candidate=candidate,
            optimisation_result=optimisation_result,
            priceable=False,
            bankable=False,
            min_p90_dscr=None,
            covenant=None,
            passes_covenant=None,
            levered_return_pct=None,
            project_irr_pct=None,
            financial=None,
            end_of_life_deliverable=eol_verdict,
            warnings=[f"unpriceable candidate (no cashflow model): {exc}", *eol_warnings],
        )

    return CandidateEvaluation(
        candidate=candidate,
        optimisation_result=optimisation_result,
        priceable=True,
        bankable=financial.bankable,
        min_p90_dscr=financial.dscr.min_dscr if financial.dscr.available else None,
        covenant=financial.case.covenant_status.covenant,
        passes_covenant=financial.case.covenant_status.passes,
        # Levered/equity return is the finance model's equity IRR (P50 basis); None when no debt.
        levered_return_pct=financial.case.equity_irr,
        project_irr_pct=financial.case.irr,
        financial=financial,
        end_of_life_deliverable=eol_verdict,
        warnings=[*financial.case.warnings, *eol_warnings],
    )


#: A progress side-channel sink: ``sink(phase, done, total)``. Presentation-only (the web run
#: screen). See `_emit_progress` for the determinism contract.
ProgressSink = Callable[[str, "int | None", "int | None"], None]


def _emit_progress(
    progress: ProgressSink | None,
    phase: str,
    done: int | None,
    total: int | None,
) -> None:
    """Fire a progress side-channel emit, swallowing anything it raises.

    Progress is a presentation-only side channel (the web app's live run screen). It MUST never
    perturb the deterministic solve, so a raising or misbehaving sink is caught and ignored, and
    ``None`` (the default at every hop) is a no-op — byte-identical to not wiring a sink at all.
    The sink only ever *reads* the loop position it is handed; it computes no number the solve uses.
    """
    if progress is None:
        return
    try:
        progress(phase, done, total)
    except Exception:  # noqa: BLE001 — a progress sink must never break the solve
        pass


def enumerate_sizing(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    search_space: SearchSpace,
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
    progress: ProgressSink | None = None,
) -> SizingRun:
    """Enumerate the search space against the dispatch + finance contracts and select the best size.

    Inputs:
      - ``dispatch_solver`` — any `DispatchSolver` (the placeholder `SimpleSolver` now, the real
        MILP later); injected so the real engine swaps in with no change here.
      - ``objective`` — the `SizingObjective` (covenant gate + ranking policy).
      - ``debt_terms`` — optional injected `DebtTerms`; without them the library has no debt
        structure in v1, so DSCR is unavailable and no candidate can be feasible (honest).
      - ``finance_runner`` — the finance entry point (injectable for testing; defaults to
        `run_financial_model`).
      - ``extra_constraints`` — additive descriptors appended after the streams' own (W7-3): the
        seam commitments carrying typed `AvailabilityCommitment` payloads (C026) ride here until
        the W7-4 producer threads real contract records; default ``None`` changes nothing.
      - ``progress`` — optional presentation-only side-channel sink (the web run screen). Fired
        once per candidate as ``("sizing", i, total)`` (1-based); guarded so it can never perturb
        the solve. Default ``None`` is byte-identical to omitting it (see `_emit_progress`).

    Never raises for a per-candidate unpriceable result — `InsufficientInputsError` is caught and
    recorded. Returns a `SizingRun`; ``bankable`` is never ``True`` on the placeholder.
    """
    reg = registry if registry is not None else default_registry()
    streams = requested_streams if requested_streams is not None else reg.stream_ids()
    constraints = dispatch_constraints_for(streams, site, assumptions, registry=reg)
    if extra_constraints:
        constraints = [*constraints, *extra_constraints]
    # Stable model identifier when the solver declares one (see underwrite_spec — keeps the
    # sizing-vs-underwriting fingerprint identity across the D32 preloaded-solver path).
    model_version = (
        getattr(dispatch_solver, "MODEL_VERSION", None) or type(dispatch_solver).__name__
    )

    candidates = search_space.candidates()
    warnings: list[str] = []
    if not candidates:
        warnings.append(
            "Sizing search space is empty (no candidate power×duration grid points) — nothing to "
            "evaluate. Supply at least one positive power candidate."
        )
        return SizingRun(
            search_space_size=0,
            candidates_evaluated=[],
            selected=None,
            selection_reason="empty search space — no candidates to evaluate",
            rejected_by_covenant=[],
            bankable=False,
            assumption_version=assumptions.metadata.version,
            warnings=warnings,
        )

    evaluations: list[CandidateEvaluation] = []
    rejected_by_covenant: list[SizingCandidate] = []

    total_candidates = len(candidates)
    for i, candidate in enumerate(candidates, start=1):
        _emit_progress(progress, "sizing", i, total_candidates)
        evaluation = _evaluate_spec(
            site,
            assumptions,
            candidate,
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
        evaluations.append(evaluation)
        if evaluation.passes_covenant is False:
            rejected_by_covenant.append(candidate)

    selected, bankable, reason, basis = _select(evaluations, objective, debt_terms=debt_terms)
    warnings.append(_bankability_warning(bankable, basis))

    return SizingRun(
        search_space_size=len(candidates),
        candidates_evaluated=evaluations,
        selected=selected,
        selection_reason=reason,
        rejected_by_covenant=rejected_by_covenant,
        bankable=bankable,
        assumption_version=assumptions.metadata.version,
        warnings=warnings,
        selection_basis=basis,
    )


def _no_debt_cause(debt_terms: DebtTerms | None) -> str:
    """WHY no P90 DSCR exists, stated from what THIS CALL knows and from nothing else.

    The sentence this replaces read "no injected DebtTerms, **and the assumption library carries no
    gearing**". The second limb was published into client packs and it is FALSE: every release from
    ``gb_v1_2`` to ``gb_v1_10`` carries ``financing.debt_gearing_pct`` (60.0, ``status:
    placeholder``), and `gridq.finance.credit` accepts a non-confirmed value with a warning rather
    than refusing it. The branch had never looked at a library at all — it was keyed only on "no
    candidate carries a DSCR" — so it asserted a fact about a file it never opened. That is a D45
    defect in its own right: an unchecked claim, stated in the voice of a measurement.

    What this function is handed is the ``DebtTerms`` the sizing search was actually given, so that
    is the whole of what it says. Nothing here claims anything about the assumption library, and
    nothing here derives, invents or infers a gearing.
    """
    if debt_terms is None:
        return (
            "NO DEBT STRUCTURE WAS RESOLVED ON THE SIZING PATH: the sizing search was handed no "
            "DebtTerms at all, so every candidate was priced with no debt service to cover and "
            "finance had no debt-service ratio to report for any of them. The sizing loop does not "
            "build a debt structure — `build_debt_terms_from_credit` is called only from the "
            "top-level finance entry point (`gridq/finance/financial_model.py`), which runs after "
            "sizing has already chosen. This states what the search was given and nothing else: no "
            "claim is made here about what any assumption release does or does not contain"
        )
    return (
        f"DebtTerms WERE handed to the sizing search ({debt_terms.gearing_pct:g}% gearing, "
        f"{debt_terms.annual_rate_pct:g}% for {debt_terms.tenor_years}y) and the finance model "
        "still returned no P90 DSCR for a single candidate. That is a finance-model outcome, not a "
        "missing input, and it should be diagnosed as one"
    )


def _covenant_account(
    evaluations: list[CandidateEvaluation],
    objective: SizingObjective,
    debt_terms: DebtTerms | None,
) -> str:
    """What the P90-DSCR covenant gate ACTUALLY did across ``evaluations`` — never an either/or.

    The no-selection reason used to hedge between two OPPOSITE verdicts in one sentence: "DSCR is
    unavailable without injected debt terms … **or** every candidate fails the covenant". One is a
    financing-DATA absence, which `validation_requirements.md` §3 requires be underwritten
    **unlevered** rather than refused; the other is an evidenced covenant failure. The record can
    always tell them apart — ``min_p90_dscr``/``passes_covenant`` are ``None`` when the gate never
    ran and populated when it did — so the hedge was never necessary, and it published a covenant
    failure that had not been measured on all eight sites of the 2026-08-15 campaign (162 grid
    points on Tanhouse, zero of them carrying a DSCR). This states which of the two happened.
    """
    total = len(evaluations)
    covenant = objective.min_p90_dscr_covenant
    if covenant is None:
        return (
            f"THE COVENANT GATE DID NOT RUN on any of the {total} candidate(s): the assumption "
            "library carries no 'dscr_covenants.min_p90_dscr' to test against. That is a missing "
            "covenant, NOT a covenant failure"
        )
    scored = [e.min_p90_dscr for e in evaluations if e.min_p90_dscr is not None]
    if not scored:
        return (
            f"THE COVENANT GATE DID NOT RUN on any of the {total} candidate(s): no P90 DSCR was "
            f"computed for a single one, because {_no_debt_cause(debt_terms)}. There was therefore "
            f"nothing to compare against the {covenant:g} covenant. NOTHING HERE IS EVIDENCE THAT "
            "ANY SIZE FAILED THE COVENANT — the test never happened. Per "
            "validation_requirements.md §3 the "
            "absence of a debt structure means this site underwrites on an UNLEVERED basis with "
            "the confidence band adjusted, never a refusal, so the unlevered project-IRR account "
            "below is the one that carries this verdict"
        )
    rejected = sum(1 for e in evaluations if e.passes_covenant is False)
    untested = total - len(scored)
    return (
        f"the covenant gate RAN on {len(scored)} of {total} candidate(s) and rejected {rejected}: "
        f"the best measured min P90 DSCR was {max(scored):.4g} against a {covenant:g} covenant"
        + (
            f"; the other {untested} candidate(s) carried no DSCR and were never tested"
            if untested
            else ""
        )
    )


def _return_metric_account(evaluations: list[CandidateEvaluation]) -> str:
    """Whether a return was ever COMPUTED here — so "no positive return" is never a default reading.

    A null ``project_irr_pct`` has two completely different meanings and the grid stores only the
    null: `finance.returns.project_irr` refuses the metric outright when the series is
    unconventional or never returns capital, so "no candidate produces a positive project IRR" can
    describe either a measured comparison against zero or a comparison that never took place. On
    the 2026-08-15 campaign it was the latter on every site, and the verdict prose asserted the
    former. The counts here are read straight off the evaluations, so the claim is evidenced by the
    record that carries it or it is not made at all.
    """
    total = len(evaluations)
    irrs = [e.project_irr_pct for e in evaluations if e.project_irr_pct is not None]
    levered = [e.levered_return_pct for e in evaluations if e.levered_return_pct is not None]
    if irrs or levered:
        measured = [
            f"best project IRR {max(irrs):.4g}% ({len(irrs)}/{total} computed)" if irrs else "",
            f"best levered return {max(levered):.4g}% ({len(levered)}/{total} computed)"
            if levered
            else "",
        ]
        positive = sum(1 for e in evaluations if candidate_viable(e))
        return (
            "return metrics WERE computed — "
            + "; ".join(p for p in measured if p)
            + f"; {positive} of {total} candidate(s) carry a strictly positive return."
        )
    return (
        f"NO RETURN METRIC WAS COMPUTED FOR ANY of the {total} candidate(s): every one records "
        "project_irr_pct = null and levered_return_pct = null, so no candidate's return was ever "
        f"compared against zero. {_return_absence_cause(evaluations)}"
    )


def _return_absence_cause(evaluations: list[CandidateEvaluation]) -> str:
    """The structural reason no return exists, read off the cashflows rather than inferred.

    Distinguishes "the asset loses money" from "the dispatch was handed no price to earn against",
    which is the distinction the `mic ≈ peak` heuristic got backwards (CLAUDE.md §2). Both are
    stated as facts about the series; neither is labelled a verdict here.
    """
    priced = [e for e in evaluations if e.financial is not None]
    if not priced:
        return (
            "No candidate reached the cashflow model at all, so there was nothing to measure a "
            "return on — this is an unpriceable-run state, not an economic finding."
        )
    never_positive = [e for e in priced if all(cf <= 0.0 for cf in e.financial.case.p50_cashflows)]  # type: ignore[union-attr]
    if len(never_positive) < len(priced):
        return (
            f"{len(priced) - len(never_positive)} of {len(priced)} priced candidate(s) DO turn "
            "cash-positive in at least one year, so the absent IRR is a non-conventional cashflow "
            "series the solver refuses to report a single root for — see the per-candidate finance "
            "warnings, and judge these on NPV/DSCR rather than on the missing IRR."
        )
    revenues = [e.financial.cashflows.total_annual_revenue_gbp for e in priced]  # type: ignore[union-attr]
    cause = (
        f"All {len(priced)} priced candidate(s) have a P50 pre-debt cashflow series that is "
        "non-positive in EVERY year, so the project IRR is UNDEFINED (capital is never returned) "
        "rather than negative — there is no number to compare with zero."
    )
    if max(revenues) <= 0.0:
        return (
            cause + " Every one of them also models ANNUAL REVENUE OF £0.00: the dispatch earned "
            "nothing at any searched size. A £0 revenue at every size is a statement about the "
            "PRICE INPUTS the dispatch was given — a tariff with no time structure, absent DUoS "
            "band rates, no capacity charge, no priced export — before it is a statement about "
            "the asset, and CLAUDE.md §2 requires that the absent inputs be derived and declared "
            "rather than read as a measured zero. Establish the tariff structure before this is "
            "reported as an economic verdict."
        )
    return (
        cause + f" Modelled annual revenue reaches £{max(revenues):,.2f} at best, against the "
        "capex and opex basis in the same cashflows."
    )


def _covenant_gate_ran(evaluations: list[CandidateEvaluation], objective: SizingObjective) -> bool:
    """Whether the P90-DSCR covenant was actually APPLIED to at least one candidate.

    This is the discrimination the unlevered basis turns on, and it is read off the record rather
    than inferred: a covenant to test against, and at least one candidate carrying a measured DSCR.
    False means the gate never ran, which is a financing-DATA absence; True means it ran, and
    whatever it decided is an evidenced covenant result that nothing below may weaken.
    """
    if objective.min_p90_dscr_covenant is None:
        return False
    return any(e.min_p90_dscr is not None for e in evaluations)


def _unlevered_pool(evaluations: list[CandidateEvaluation]) -> list[CandidateEvaluation]:
    """The candidates an UNLEVERED selection may choose between.

    Every non-financing condition `SizingObjective.feasible` imposes still applies. A spec with an
    explicit ``end_of_life_deliverable is False`` verdict is physically non-deliverable to its final
    contract year and is rejected here exactly as the covenant path rejects it; an *unassessed*
    ``None`` verdict is a data-ask and does not block (D12). Proxy (typical-period) evaluations are
    excluded because only a full-series result may ever be reported as the chosen size (D32). What
    is dropped, and all that is dropped, is the DSCR limb — the financing datum that does not exist
    on this path.

    `candidate_viable` is kept, and keeping it is the point: an unlevered basis is a basis for
    choosing among candidates that DO return capital, never a licence to recommend the least-bad
    loss-maker. That was the 2026-07-07 red-team failure (a 4 kW grid-corner "recommendation" on an
    unviable site) and this branch must not reopen it.
    """
    return [
        e
        for e in evaluations
        if e.priceable
        and e.optimisation_result.selected_power_kw
        and not e.proxy
        and e.end_of_life_deliverable is not False
        and candidate_viable(e)
    ]


def _unlevered_reason(
    chosen: CandidateEvaluation,
    pool: list[CandidateEvaluation],
    evaluations: list[CandidateEvaluation],
    objective: SizingObjective,
    debt_terms: DebtTerms | None,
) -> str:
    """The account of an UNLEVERED selection: what chose it, and what it is not.

    Every claim is either read off the chosen candidate or delegated to the two evidenced accounts
    (`_covenant_account`, `_return_metric_account`), so the sentence cannot outrun the record that
    carries it.
    """
    metric = (
        f"an unlevered project IRR of {chosen.project_irr_pct:.4g}%"
        if chosen.project_irr_pct is not None
        else "the objective's rank_key (this candidate carries no project IRR of its own)"
    )
    return (
        f"UNLEVERED SELECTION: {chosen.candidate.power_kw:g} kW x "
        f"{chosen.candidate.duration_hours:g} h, chosen from {len(pool)} eligible candidate(s) of "
        f"{len(evaluations)} evaluated, on {metric} — ranked by the SAME objective rank_key the "
        "covenant path uses, smallest system as the tie-break. "
        f"{_covenant_account(evaluations, objective, debt_terms)}. "
        "So this size has NEITHER PASSED NOR FAILED A COVENANT, and it is not a bankable optimum: "
        "it is the unlevered basis validation_requirements.md §3 requires when a debt structure is "
        "absent ('the absence of a credit assessment or debt structure means the site underwrites "
        "on an UNLEVERED basis with the confidence band adjusted, not that the site fails'). The "
        "confidence band is capped for it. Supplying a credit assessment / debt term sheet is the "
        "one thing that lets the covenant gate run and turns this into a levered result. "
        f"And on returns: {_return_metric_account(evaluations)}"
    )


def _bankability_warning(bankable: bool, basis: str) -> str:
    """The run-level bankability sentence, told in the terms of the basis that actually selected.

    Shared by both production searches so they cannot drift apart. The generic
    "selection is indicative only" line is wrong for an unlevered selection: an unlevered basis is a
    RECORDED basis mandated by §3, not an improvisation, and calling it indicative would hide the
    one fact a reader needs (that no covenant was ever applied).
    """
    if bankable:
        return "Sizing run produced a bankable-candidate selection."
    if basis == SELECTION_BASIS_UNLEVERED:
        return (
            "Sizing run is NOT bankable: no debt structure was resolved on the sizing path, so no "
            "P90 DSCR exists for any candidate and the size was selected on an UNLEVERED basis "
            "(validation_requirements.md §3). That is a recorded selection basis, not a covenant "
            "result — the selected size has neither passed nor failed a covenant — and the "
            "confidence band is capped accordingly. A credit assessment / debt structure is what "
            "lets the covenant gate run."
        )
    return (
        "Sizing run is NOT bankable: it runs on placeholder assumptions and/or a placeholder "
        "dispatch that prices nothing (or the levered return is unavailable without injected debt) "
        "— selection is indicative only. A real dispatch and confirmed assumptions are required "
        "before any size is presented as bankable."
    )


def _select(
    evaluations: list[CandidateEvaluation],
    objective: SizingObjective,
    *,
    debt_terms: DebtTerms | None,
) -> tuple[CandidateEvaluation | None, bool, str, str]:
    """Select the best candidate and say on WHICH BASIS; never assert bankability on an incomplete
    objective, and never let a financing-data absence wear an economic verdict.

    Three outcomes, in strict priority, returned as ``(selected, bankable, reason, basis)``:

    1. **covenant** — at least one candidate is `SizingObjective.feasible` (a measured P90 DSCR at
       or above the covenant, and not a known non-deliverable spec). Unchanged behaviour.
    2. **unlevered** — no candidate is covenant-feasible AND the covenant gate never ran on any of
       them (`_covenant_gate_ran`), i.e. no debt structure was resolved. `docs/
       validation_requirements.md` §3 and CLAUDE.md §2 both require this be underwritten on an
       UNLEVERED basis with the band adjusted — *"never return 'no viable configuration' for a data
       reason of any kind"*. Before 2026-08-17 this branch did not exist: the search returned no
       selection at all, and a financing-data absence was published as an economic refusal on eight
       client packs.
    3. **none** — nothing selectable. Reached when the covenant gate DID run and rejected
       everything (an evidenced covenant failure, which stays exactly as strict as it was), or when
       no candidate in the unlevered pool returns capital (the `candidate_viable` red-team gate).

    ``debt_terms`` is keyword-only and has NO default on purpose: a caller that forgets to thread it
    is a TypeError at import-time-adjacent test collection, not a silently wrong sentence about why
    no DSCR exists. Both production searches thread it (`enumerate_sizing`, `two_stage_sizing`).
    """
    feasible = [e for e in evaluations if objective.feasible(e)]
    if feasible:
        best = max(feasible, key=objective.rank_key)
        if best.bankable and objective.primary_metric_available(best):
            return (
                best,
                True,
                "selected the best feasible candidate (P90 DSCR ≥ covenant).",
                SELECTION_BASIS_COVENANT,
            )
        return (
            best,
            False,
            "best feasible candidate selected but NOT bankable: the run uses placeholder "
            "assumptions and/or the levered return is unavailable (no injected debt) — indicative "
            "only.",
            SELECTION_BASIS_COVENANT,
        )

    if not _covenant_gate_ran(evaluations, objective):
        pool = _unlevered_pool(evaluations)
        if pool:
            best = max(pool, key=objective.rank_key)
            return (
                best,
                False,
                _unlevered_reason(best, pool, evaluations, objective, debt_terms),
                SELECTION_BASIS_UNLEVERED,
            )

    return (
        None,
        False,
        "no candidate selected. What the search actually established, and no more: "
        f"{_covenant_account(evaluations, objective, debt_terms)}. And on returns: "
        f"{_return_metric_account(evaluations)}",
        SELECTION_BASIS_NONE,
    )
