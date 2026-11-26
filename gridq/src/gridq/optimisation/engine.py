"""`MilpEngine` — the production `OptimisationEngine`: the sizer composed with the real MILP.

This is the "one engine, called twice" composition the architecture always described (D17,
``docs/dispatch_engine.md``): for each candidate size it drives the `MilpDispatchSolver` through the
existing `gridq.sizing.enumerate_sizing` outer loop (which maps each `DispatchResult` onto the
frozen `OptimisationResult` and threads the dispatch-energy hook into finance), then returns the
candidate's result behind the frozen seam. It drops in wherever `BaselineEngine` is used with
**no downstream change**; the placeholder stays the explicit fallback.

It honours the conformance contract: it never raises on a valid site (insufficient data / no
dispatch degrade to ``INSUFFICIENT_DATA`` with no spec), it is deterministic, it echoes the request,
and on the all-placeholder ``gb_v0_1`` library every result stays ``not_bankable``. Because the
covenant gate cannot pass without injected debt terms (the library carries no debt structure), a
priceable run still surfaces the best **indicative** candidate as ``not_bankable`` — never silently
no-spec. That fallback ranks on the sizing objective's own `rank_key` (levered return where debt
resolved, else the labelled unlevered project IRR — N4), so a site with no credit assessment gets
the SAME metric the search optimised, one gate weaker. **"No viable configuration" is never a
financing-data verdict**: missing credit/debt data means underwriting unlevered, not refusing the
site (`CLAUDE.md` §2).

**The connection is a first-class input and an output.** The sizing search box is scaled from the
site's DECLARED import capacity where one is evidenced (`SizingBasis`), never from its load alone;
the capacity the recommended spec turns out to need is reported back
(`RequiredConnectionCapacity`), because an unknown MIC is a derived assumption plus a data ask and
never a blocker (D44). And **a data-shaped label is never attached to an economic outcome**: a run
whose candidates solved, priced and simply did not pay gets the economics verdict, with any
connection data ask stated alongside it and never in place of it.

**One netting site for contracted export (MD-F-05 limb (b)).** When an agreed export tariff enters
the priced floor, the floor's DISPATCH-DERIVED components are re-priced from the export OVERLAY
schedule and the export entry itself stays GROSS, so the floor totals ``overlay floor terms + gross
export`` rather than the physically impossible "full no-export arbitrage + full export volume".
`_rebase_dispatch_floor_streams` states the contract; `_restate_export_netting_site` records where
the netting happened and why the alternative (netting the export entry) is refused. Nothing here
fires on a run without both export consent and a supplied wholesale forecast — every run today.

**Export as a labelled feasibility study, never as fabricated consent.** The floor is
behind-the-meter by construction and the overlay's export cap is ``min(power, connection.mec_kw)``,
so a site with no recorded MEC had 2.1–2.6× of its measured discharge capability silently outside
the model. `ExportFeasibilityOverride` is the explicit, attributed, hard-to-construct scenario input
that lets a *study* permit export anyway: it carries the export limit itself, it is stated on every
result this engine returns, and it touches neither `export_dispatch_enabled` nor any consent field.
Because export value lives only in ``gated_upside``, an override can measure volume and can never
move a priced-floor figure — so it cannot make a candidate viable, by construction.

**Two bases, and which one a run is on is part of its identity (D46).** The paragraph above is the
UNDERWRITING basis and it is unchanged. A run may additionally declare the named
`gridq.schemas.StudyBasis`, which prices wholesale export INTO the priced floor at a derived,
sourced tariff — so on that basis export DOES move P50, P90, the DSCR and both IRRs, and can change
a viability verdict. It is applied by `_StudyBasisDispatchSolver`, so it reaches every candidate's
own dispatch and finance case rather than only the final label; it is refused alongside any second
export mechanism (forecast overlay, feasibility override, MPC, injected solver); it is stated first
in every result's ``warnings`` and suffixed onto ``SolverMetadata.model_version``; and it moves
nothing about consent — no consent, MEC, export MPAN or G99/G100 field is written anywhere, and
export at a study site still REQUIRES CUSTOMER CONSENT. **A study-basis figure may never be
presented as an underwriting output.**
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import pandas as pd

from gridq.assumptions import AssumptionError, AssumptionLibrary, load_assumptions
from gridq.dispatch import (
    DispatchProblem,
    DispatchResult,
    dispatch_constraints_for,
    to_finance_dispatch_energy,
)
from gridq.engine.errors import InvalidRequestError
from gridq.finance.cashflows import DispatchEnergy
from gridq.ingestion import HalfHourlyDataError
from gridq.optimisation._assumptions import maybe, numeric
from gridq.optimisation.milp_solver import (
    MilpDispatchSolver,
    duos_absence_warning,
    fixed_line_absence_warnings,
    levy_rate_gbp_per_kwh,
)
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.mpc_solver import MpcDispatchSolver
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.tariff_calendar import (
    duos_windows_from_assumptions,
    energy_price_series,
    network_rate_series_with_absences,
    red_band_mask,
    tariff_provenance_phrase,
)
from gridq.optimisation.two_stage import connection_power_limit_kw, two_stage_sizing
from gridq.optimisation.valuation import (
    NoSpecDispatchEvidence,
    _split_signed,
    build_value_streams,
    export_upside_stream_result,
    study_basis_export_stream_result,
    study_basis_label_dispatch_component,
    wholesale_contracted_stream_result,
)
from gridq.provenance import get_code_version
from gridq.schemas import (
    BankableStatus,
    ConfidenceBand,
    CustomerBill,
    DispatchProfile,
    Firmness,
    ImplementationLevel,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    SelectionStatus,
    SiteModel,
    SolveMode,
    SolverMetadata,
    SolverStatus,
    StudyBasis,
    ValueStreamResult,
    ceiling_for_key,
)
from gridq.sizing.objective import SizingObjective, candidate_viable
from gridq.sizing.provisioning import ProvisioningInfeasibleError
from gridq.sizing.search_space import DEFAULT_DURATION_HOURS, SearchSpace
from gridq.sizing.sizer import (
    CandidateEvaluation,
    ProgressSink,
    SizingRun,
    _emit_progress,
    enumerate_sizing,
)
from gridq.value_stack.gates import agreed_rate, export_dispatch_enabled, stream_consented
from gridq.value_stack.gb_streams import (
    AgreedCapacityValue,
    mid_half_hourly_export_price_series,
)
from gridq.value_stack.registry import StreamRegistry, default_registry

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecast

# Structural default sizing grid (labelled — injects no £/%, same precedent as
# BaselineEngine.BASELINE_POWER_FRACTION / SearchSpace.DEFAULT_DURATION_HOURS). Power candidates are
# fractions of the site's binding scale — the DECLARED CONNECTION LIMIT where one is evidenced,
# the observed peak only as the fallback (see `MilpEngine._sizing_basis`). Site-specific, never an
# invented absolute kW.
POWER_FRACTIONS: tuple[float, ...] = (0.25, 0.5, 1.0)

_PRICED_FIRMNESS = frozenset({Firmness.FIRM, Firmness.CONTRACTED})

#: Calendar constant for annualising a partial metered horizon, identical to the one
#: `milp_solver`/`schedule` use so a before-only bill annualises exactly as a solved one does.
_HOURS_PER_YEAR = 8760.0

#: Tolerance (relative to the observed peak) inside which a declared import capacity is treated as
#: the metered peak having been copied into MIC as a stand-in. Structural detector threshold, not a
#: price or a physical constant.
_PLACEHOLDER_MIC_TOLERANCE = 0.01

#: The priced-floor components whose £ is derived from the DISPATCH itself and therefore must be
#: re-priced from the export OVERLAY schedule whenever contracted export revenue enters the floor
#: (MD-F-05 limb (b) — see `_rebase_dispatch_floor_streams`). Every other stream's £ comes from
#: capability or a contract rate, not from the arbitrage the export cannibalises.
_OVERLAY_REBASED_FLOOR_STREAMS: dict[MarketStream, str] = {
    MarketStream.TARIFF_ARBITRAGE: "arbitrage_value_gbp",
    MarketStream.DUOS_RED_BAND_AVOIDANCE: "duos_avoidance_value_gbp",
}

#: Fragment of the sentence `valuation.wholesale_contracted_stream_result` appends when it is given
#: no ``cannibalised_floor_gbp`` — its own (ALTERNATIVE, mutually exclusive) implementation of
#: MD-F-05 limb (b). Under this engine's netting contract not passing that figure is the correct
#: call, which makes the sentence's CLAIM false; `MilpEngine._restate_export_netting_site` removes
#: it and states the netting site instead. Matched on this short fragment rather than the full
#: sentence so a wording or punctuation change on the valuation side cannot silently defeat the
#: strip, and its ABSENCE is tolerated (that half may legitimately stop emitting it).
_STALE_GROSS_CLAIM_MARKER = "NOT NETTED"

#: The D44/D45 register key an export-limit study override stands in for. It is in
#: `schemas.derived.STRUCTURAL_KEYS`, so `ceiling_for_key` returns **LOW** for it — the override
#: reads its confidence ceiling off the written policy instead of restating it, because there is one
#: ceiling policy and it lives in one place (`gridq.schemas.derived.ceiling_for_key`).
_EXPORT_LIMIT_CEILING_KEY = "export_arrangement.declared_export_limit_kw"


@dataclass(frozen=True)
class ExportFeasibilityOverride:
    """An EXPLICIT, LABELLED study input that permits export dispatch. **Never a consent record.**

    **Why it exists.** `milp_solver`'s floor is behind-the-meter by construction
    (``discharge ≤ min(power, load)``) and its export variable is capped at
    ``min(power, connection.mec_kw)``. Every site in the live validation campaign records
    ``mec_kw = None``, so the cap resolved to 0 kW and the export overlay was an unannounced no-op —
    which measured 2.1–2.6× of each site's physical discharge capability out of the model. This
    input lets a **feasibility study** ask "what if export were permitted?" and get a measured
    answer, with the assumption stated on the run.

    **What it is NOT, structurally rather than by promise.**

    * It is **not consent.** `value_stack.gates.export_dispatch_enabled` (G99 ∧ G100 approved ∧
      MEC > 0 ∧ an export MPAN) is untouched, and no field it reads is written anywhere: the MEC
      lives HERE, on the scenario input, and never on the `SiteModel`. D45 is explicit that a
      permission is not derivable and is modelled-and-labelled "requires customer consent", never
      recorded as granted — so this override changes what the *study* is allowed to model, and
      nothing about what the *customer* has agreed.
    * It is **not the priced floor.** Export value stays in ``gated_upside`` under
      ``WHOLESALE_P415`` (`CLAUDE.md` §6, D20/B002/D43). The override cannot move a P50, a P90, a
      DSCR or an IRR, and therefore cannot make a candidate viable. That is a deliberate structural
      property, not an omission: the *only* route from export to the floor is D43's CONTRACTED
      agreed-tariff entry, and that route requires real per-stream consent plus a declared export
      tariff, neither of which an override may manufacture.
    * It is **not enableable by accident.** Four fields are required, one of them the
      `ACKNOWLEDGEMENT` sentence verbatim; a non-positive, non-finite or unlabelled override raises
      on construction. There is no default, no environment variable and no truthy flag.

    **Confidence.** An export limit is a STRUCTURAL FEASIBILITY input under D45's ceiling policy, so
    ``confidence_ceiling`` is LOW and may not be raised (validated against `ceiling_for_key`).
    ``not_bankable`` is untouched — it is the "confirmation required" statement, not a restriction
    to remove.
    """

    #: The verbatim acknowledgement `acknowledgement` must carry. Prose, deliberately: a boolean
    #: cannot be misread but it also cannot be *quoted*, and this string ends up in the run record.
    ACKNOWLEDGEMENT: ClassVar[str] = (
        "study override, not customer consent; export requires customer consent"
    )

    #: The export capacity the study permits (kW). Comes from HERE, never from a guessed site field.
    mec_kw: float
    #: Who directed the study override (a person, in the record — never "the system").
    authorised_by: str
    #: Why export is being lifted for this run, in the words of whoever authorised it.
    justification: str
    #: Must equal `ACKNOWLEDGEMENT` exactly.
    acknowledgement: str
    #: The band cap this input imposes. LOW, and may not be raised above the D45 policy ceiling.
    confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

    def __post_init__(self) -> None:
        if self.acknowledgement != self.ACKNOWLEDGEMENT:
            raise ValueError(
                "ExportFeasibilityOverride.acknowledgement must be the verbatim sentence "
                f"{self.ACKNOWLEDGEMENT!r}. It exists so an export lift cannot be enabled by a "
                "truthy flag: whoever enables it states, in the run record, that this is a study "
                "input and not customer consent."
            )
        if not np.isfinite(self.mec_kw) or self.mec_kw <= 0:
            raise ValueError(
                f"ExportFeasibilityOverride.mec_kw must be a finite, positive kW (got "
                f"{self.mec_kw!r}). The export limit is the whole content of this input; an absent "
                "or zero limit is not an override, it is the unchanged no-export model."
            )
        if not self.authorised_by.strip() or not self.justification.strip():
            raise ValueError(
                "ExportFeasibilityOverride requires a non-blank authorised_by and justification: "
                "an unattributed, unexplained override is exactly the silent default this input is "
                "shaped to make impossible."
            )
        policy = ceiling_for_key(_EXPORT_LIMIT_CEILING_KEY)
        if self.confidence_ceiling.rank > policy.rank:
            raise ValueError(
                f"ExportFeasibilityOverride declares a {self.confidence_ceiling.value} confidence "
                f"ceiling but an export limit is a STRUCTURAL FEASIBILITY input, which the D45 "
                f"ceiling policy caps at {policy.value} "
                "(`gridq.schemas.derived.ceiling_for_key`). A producer may not raise its own "
                "ceiling."
            )

    def note(self, *, site_consent_coheres: bool) -> str:
        """The sentence stated on every artifact carrying a number derived from this override."""
        consent_txt = (
            "The site's own export consents DO cohere independently "
            "(`export_dispatch_enabled` is already true), so this override only replaces the "
            "export LIMIT — it is still not evidence of a route to market."
            if site_consent_coheres
            else "The site's own export consents DO NOT cohere: "
            "`export_dispatch_enabled` (G99 ∧ G100 approved ∧ MEC > 0 ∧ an export MPAN) is FALSE "
            "and is UNCHANGED by this override. No consent, MEC or export MPAN has been written to "
            "the site record; export at this site still REQUIRES CUSTOMER CONSENT (D45 "
            "`pending_permission`) and is modelled, not granted."
        )
        return (
            f"EXPORT FEASIBILITY OVERRIDE ACTIVE — A LABELLED STUDY INPUT, NOT CUSTOMER CONSENT. "
            f"Export dispatch was permitted for this run up to {self.mec_kw:,.3f} kW by an "
            "explicit "
            f"scenario override authorised by {self.authorised_by.strip()}: "
            f"{self.justification.strip()} Acknowledged as: “{self.ACKNOWLEDGEMENT}”. "
            f"{consent_txt} "
            "Export value is reported as GATED UPSIDE under WHOLESALE_P415 only and NEVER enters "
            "the priced floor, P50/P90, the DSCR or either IRR (CLAUDE.md §6, D20/B002/D43), "
            "so this override cannot change whether a candidate is economically viable. Per the "
            "D45 ceiling policy an export limit is a STRUCTURAL FEASIBILITY input, so this run's "
            f"confidence band is capped at {self.confidence_ceiling.value.upper()} and the result "
            "stays not_bankable. Confirm the real MEC and the export route with the DNO/supplier "
            "(data ask: connection_mic_mec_asc, and a route-to-market counterparty) before any "
            "figure derived from it is used outside a feasibility study."
        )

    @property
    def solver_basis_label(self) -> str:
        """The short basis label handed to `MilpDispatchSolver.solve_with_export`."""
        return (
            f"labelled EXPORT FEASIBILITY OVERRIDE (study input, NOT customer consent) authorised "
            f"by {self.authorised_by.strip()}"
        )


class _StudyBasisDispatchSolver(MilpDispatchSolver):
    """The D46 study-basis dispatch solver: export is priced INTO the floor, on every solve.

    **Why a solver subclass and not a post-processing step.** The verdict a study basis has to be
    able to change is per-candidate: ``candidate_viable`` reads the finance metrics that
    `sizing.sizer._evaluate_spec` builds from ``dispatch_solver.solve(problem)``, so export revenue
    has to be inside the candidate's own `DispatchResult` or it can move a label and nothing else —
    which is exactly the limitation D46 was written to remove. Overriding the two seams the sizing
    search actually funnels through (`_solve_milp` builds the model, `_solved` assembles the result)
    reaches every candidate on both stages, because `proxy_solver.PreloadedDispatchSolver` calls
    them on the instance it is handed (``self._base._solve_milp`` / ``._solved``) and Python
    dispatches on the instance. Subclassing also inherits `_load_series` and `_unsolved` unchanged,
    and keeps `isinstance(_, MilpDispatchSolver)` true, which is what `MilpEngine._run_search` and
    `two_stage.two_stage_sizing` require to run the real search at all.

    **What changes, and only this.** Every solve gets an ``export`` variable priced at the study
    basis's DERIVED, SOURCED flat tariff and bounded by its declared export limit, and the resulting
    export revenue is appended to ``value_streams`` as a heavily-labelled CONTRACTED entry. Nothing
    is written to the site: the export limit lives on the study basis, `export_dispatch_enabled` is
    untouched, and no consent, MEC, export MPAN or G99/G100 field is set anywhere.

    **MD-F-05 limb (b) — exactly ONE netting site, and this is it, by construction.** The floor's
    dispatch-derived components (tariff arbitrage, DUoS red-band avoidance) come from
    `valuation.build_value_streams` reading *this* schedule — the export dispatch's own — so they
    are already the post-export figures and the export entry is correctly GROSS. There is no second
    no-export solve to re-base *from*, which is what makes double-netting structurally impossible
    here rather than merely avoided: the physically impossible "full no-export arbitrage + full
    export volume" total cannot be constructed. `MilpEngine._study_netting_check` measures the
    displaced behind-the-meter value ONCE, after selection, on a fresh plain solver, and reports it
    as a CHECK on that arithmetic — never as a second deduction.
    """

    MODEL_VERSION = f"{MilpDispatchSolver.MODEL_VERSION}{StudyBasis.SOLVER_SUFFIX}"

    def __init__(
        self, study: StudyBasis, *, registry: StreamRegistry | None = None, seed: int = 0
    ) -> None:
        super().__init__(registry=registry, seed=seed)
        self._study = study

    @property
    def study(self) -> StudyBasis:
        """The named basis this solver prices on."""
        return self._study

    def _solve_milp(  # type: ignore[override]
        self,
        problem: DispatchProblem,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        rte: float,
        *,
        export_price_gbp_per_kwh: np.ndarray | None = None,
        export_limit_kw: float | None = None,
        export_limit_basis: str | None = None,
        **kwargs: object,
    ) -> tuple[HalfHourlyDispatchSchedule | None, list[str], list[str]]:
        """Add the study basis's flat export price + limit to every solve that has none already.

        A caller that already supplies an export price series (``solve_with_export``, i.e. the D43
        gated overlay) is passed through untouched — two export configurations must never be merged
        into one solve. The flat tariff needs no timestamp alignment by construction, so the proxy
        sweep's typical-period subset and the confirm stage's full metered year are handled by the
        same one line, and the price array can never be interpolated or extrapolated because there
        is no shape to align.
        """
        if export_price_gbp_per_kwh is not None:
            return super()._solve_milp(  # type: ignore[misc]
                problem,
                load,
                timestamps,
                interval_min,
                rte,
                export_price_gbp_per_kwh=export_price_gbp_per_kwh,
                export_limit_kw=export_limit_kw,
                export_limit_basis=export_limit_basis,
                **kwargs,  # type: ignore[arg-type]
            )
        # D57 (2026-08-18): THE EXPORT PRICE IS THE REAL HALF-HOURLY SERIES WHERE ONE ALIGNS.
        #
        # This was a FLAT array at the study basis's annual mean, and a flat price cannot clear an
        # export decision on these sites: the delivered cost of a charged kWh plus its marginal
        # degradation is roughly GBP 199/MWh, and the sourced annual mean is GBP 87. Priced flat the
        # optimiser rationally never exports, so WHOLESALE_P415 reported GBP 0 - a zero that reads
        # as
        # "export is worthless" when all it says is "the average of a volatile series is below
        # marginal cost". On the real series 64 half-hours a year clear that bar and 23 clear GBP
        # 250/MWh, so the optimiser exports in those and nowhere else. The volume becomes the
        # model's
        # own choice on real prices instead of an artefact of averaging.
        #
        # The flat tariff remains the FALLBACK and is used verbatim whenever the series cannot be
        # aligned to this solve's timestamps - never a part-filled mixture, and the run says which
        # basis it used either way.
        series, series_notes = mid_half_hourly_export_price_series(timestamps)
        export_price = (
            np.asarray(series, dtype=float)
            if series is not None
            else np.full(len(load), float(self._study.export_tariff_gbp_per_kwh), dtype=float)
        )
        if series is None:
            series_notes = [
                *series_notes,
                "EXPORT PRICED AT THE STUDY BASIS'S FLAT TARIFF of GBP "
                f"{self._study.export_tariff_gbp_per_kwh:,.6f}/kWh, because no half-hourly series "
                "aligned to this solve's horizon. A flat price is a WEAK basis for an export "
                "decision: it removes exactly the volatility export value lives in, and it "
                "understates the stream if the optimum would have exported into the peaks and "
                "overstates it if the mean sits above marginal cost.",
            ]
        schedule, notes, warns = super()._solve_milp(  # type: ignore[misc]
            problem,
            load,
            timestamps,
            interval_min,
            rte,
            export_price_gbp_per_kwh=export_price,
            export_limit_kw=float(self._study.export_limit_kw),
            export_limit_basis=self._study.solver_basis_label,
            **kwargs,  # type: ignore[arg-type]
        )
        return schedule, [*notes, *series_notes], warns

    def _solved(
        self,
        problem: DispatchProblem,
        schedule: HalfHourlyDispatchSchedule,
        binding: list[str],
        warnings: list[str],
        capacity_reduction: AgreedCapacityValue | None = None,
    ) -> DispatchResult:
        """Assemble the result, then price the study basis's export entry into ``value_streams``.

        ``capacity_reduction`` (MICASC-3) is forwarded unchanged: the study basis re-prices the
        EXPORT leg and has nothing to say about agreed capacity, but dropping the argument here
        would silently zero the stream on every study-basis run.
        """
        result = super()._solved(
            problem, schedule, binding, warnings, capacity_reduction=capacity_reduction
        )
        study = self._study
        export_mwh = schedule.annual_export_mwh
        if export_mwh is None:
            # Structurally unreachable (this solver always creates the export variable), so it is
            # reported rather than repaired: a study-basis result with no export measurement must
            # not look like a study-basis result with £0 of export.
            return result.model_copy(
                update={
                    "warnings": [
                        study.note(),
                        "STUDY BASIS NOT PRICED: this solve produced no export variable, so no "
                        "export volume was measured and no export £ entered the priced floor. The "
                        "floor below is the ordinary behind-the-meter floor.",
                        *result.warnings,
                    ]
                }
            )
        wholesale_base = next(
            (v for v in result.gated_upside if v.stream == MarketStream.WHOLESALE_P415), None
        )
        if wholesale_base is None:
            # No gate-derived WHOLESALE_P415 entry to build on (a registry that does not carry the
            # stream). Build the entry from scratch rather than dropping the study's whole point;
            # CONDITIONAL is the honest starting firmness and the helper sets CONTRACTED.
            wholesale_base = ValueStreamResult(
                stream=MarketStream.WHOLESALE_P415, firmness=Firmness.CONDITIONAL
            )
        entry = study_basis_export_stream_result(
            wholesale_base,
            study=study,
            export_mwh=float(export_mwh),
            overlay_floor_terms_gbp=schedule.priced_floor_terms_gbp,
            # The schedule's own export term, so the reported £ is the objective's and not a flat
            # tariff re-multiplied onto the volume it earned at the peaks (D57, 2026-08-18).
            measured_export_revenue_gbp=schedule.export_revenue_gbp,
        )
        # Every AFFECTED figure carries the basis, not just the export entry (D46: the labelling is
        # load-bearing). The two dispatch-derived floor components are affected — they are priced
        # from the dispatch that exported, so they are already net of the displaced behind-the-meter
        # value — and a per-stream entry can be rendered on its own.
        return result.model_copy(
            update={
                "value_streams": [
                    *(
                        study_basis_label_dispatch_component(v, study=study)
                        if v.stream in _OVERLAY_REBASED_FLOOR_STREAMS
                        and v.firmness in _PRICED_FIRMNESS
                        else v
                        for v in result.value_streams
                    ),
                    entry,
                ],
                # D57 (2026-08-18): THE GBP 0 WHOLESALE_P415 GATED PLACEHOLDER IS DROPPED, not
                # relabelled. It used to be kept and labelled, on the reasoning that removing
                # information a reader would otherwise see is the wrong direction. In practice it
                # MASKED the very thing it pointed at: two entries carry the same `stream` id, and
                # any consumer keying a lookup on the stream name - a report template, the D57
                # acceptance matrix, a spreadsheet - resolves WHOLESALE_P415 to whichever it reads
                # last, so a priced CONTRACTED floor entry could be shadowed by a GBP 0 placeholder
                # and the stream read as unearned. The label was correct prose that a keyed reader
                # never saw. The floor entry carries the whole story (the volume, the tariff, the
                # basis, the consent that is still pending), so nothing is lost by leaving exactly
                # one WHOLESALE_P415 entry on a study-basis run. On the UNDERWRITING basis this
                # branch does not run at all and the gated entry is untouched, which is where the
                # entire export value belongs.
                "gated_upside": [
                    v for v in result.gated_upside if v.stream != MarketStream.WHOLESALE_P415
                ],
                "warnings": [study.note(), *result.warnings],
            }
        )


@dataclass(frozen=True)
class SizingBasis:
    """WHICH physical quantity the sizing search box was scaled from, and why.

    The two-stage box spans 5–150% of ``capacity_kw`` (`two_stage.POWER_FRACTION_BOUNDS`), so this
    single number decides which system sizes are ever searched. It used to be the OBSERVED PEAK
    whenever one existed, with the connection limit as a mere fallback — which anchored the search
    on the LOAD and never on the wire. Measured consequence on a live site: a 325 kW connection with
    a 22.6 kW metered peak had its box capped at 33.9 kW, hiding 302 kW of evidenced headroom, and
    tripling another site's MIC produced an IDENTICAL candidate set because MIC never reached the
    box at all.

    So the basis is the DECLARED physical limit ``min(MIC, ASC)`` where one is evidenced — the same
    convention `two_stage.connection_power_limit_kw` and the solver's own charge cap use, so the
    box and the dispatch that fills it agree about what the connection supports (the N7 precedent,
    `two_stage.py:105-148`). ``basis`` names which of the three states produced it, and ``note`` is
    the sentence stated on the run and on the result so a reader never has to guess.
    """

    capacity_kw: float
    #: ``connection_limit`` · ``connection_limit_below_observed_peak`` · ``observed_peak``.
    basis: str
    connection_limit_kw: float | None
    observed_peak_kw: float | None
    note: str


@dataclass(frozen=True)
class RequiredConnectionCapacity:
    """The import capacity the RECOMMENDED spec actually needs — an OUTPUT, never a blocker (D44).

    ``required_kw`` is ``max(site load + battery charge) + parasitic auxiliary draw`` measured over
    the operative dispatch: exactly the quantity the solver's own connection constraint bounds
    (`milp_solver`'s ``charge_ub = min(power, max(0, limit − load − aux))``). Discharge is NOT
    credited as reducing it, because the solver's charge bound does not credit it either.

    An unknown MIC is a derived assumption plus a data ask, never a refusal (D44), and this is the
    number that makes the ask answerable: it tells the DNO conversation what capacity to confirm
    instead of asking for "the MIC".
    """

    required_kw: float
    observed_peak_load_kw: float
    battery_charge_peak_kw: float
    parasitic_kw: float
    declared_limit_kw: float | None
    #: ``declared_limit_kw − required_kw`` when a limit is declared, else ``None`` (never invented).
    spare_vs_declared_kw: float | None
    #: ``priced-floor dispatch`` or ``export overlay dispatch`` — which solve was measured.
    schedule_basis: str
    note: str


def _indicative_reason(chosen: CandidateEvaluation) -> str:
    """What `_choose`'s fallback actually ranked on, stated in one place so the persisted sizing
    grid and the quote say the same thing about how the reported size was picked (N16). It names
    the metric because "no candidate selected" was the alternative, and it was false.

    N4 (2026-08-10 review): the metric is now the objective's own `rank_key` — the levered
    (equity) return when debt terms resolved, else the unlevered project IRR as the labelled
    fallback `sizing/objective.py` has always documented. It used to be the NET priced-floor
    value, a gross-margin metric with NO capex term, so a candidate four times the size with a
    worse return outranked the one the search itself ranked first. Which of the two metrics
    actually selected is read off the chosen candidate and written into the reason, because the
    difference between "levered" and "unlevered" is the difference between a bankable optimum and
    an indicative one.

    2026-08-17: the opening clause used to read "no candidate met the P90-DSCR covenant (typically
    no debt structure is resolvable)". Both halves were asserted, neither was checked, and once
    `sizer._select` began selecting on a labelled UNLEVERED basis the second half became flatly
    wrong for this path: an indicative fallback is now reached mainly when the covenant gate DID
    run and rejected everything. The gate clause is therefore read off the CHOSEN CANDIDATE's own
    record — a candidate carrying no ``min_p90_dscr`` was never tested, and saying it "did not meet"
    a covenant would be the same unmeasured claim `sizer._covenant_account` exists to stop.
    """
    metric = (
        "the levered (equity) IRR"
        if chosen.levered_return_pct is not None
        else "the UNLEVERED project IRR (no debt structure resolved, so the objective's primary "
        "levered metric is unavailable — the labelled fallback)"
    )
    gate = (
        f"this candidate's own measured min P90 DSCR of {chosen.min_p90_dscr:.4g} did not meet the "
        "covenant"
        if chosen.min_p90_dscr is not None
        else "no P90 DSCR was computed for this candidate, so the covenant gate never ran on it "
        "and NOTHING HERE IS EVIDENCE THAT IT FAILED A COVENANT"
    )
    return (
        f"indicative fallback: {gate}, so the reported size is the viable, full-series candidate "
        f"{metric} ranks first on — the objective's own rank_key, smallest system as the "
        "tie-break. Selection on a return metric, but not a covenant-feasible optimum: it is a "
        "recommendation, not a bankable optimum, and it is reported as such."
    )


@dataclass(frozen=True)
class ExportUpside:
    """The consent-gated wholesale export overlay's result (issue 018 / D43), stashed per solve.

    ``delta_gbp`` is the incremental total value of enabling export on the selected spec vs the
    no-export floor (≥0, £0 when export never pays); ``export_mwh`` the annualised exported energy;
    the price fields pin which wholesale series produced it. Reported on the result's WHOLESALE_P415
    gated-upside entry (never the priced floor), and readable via ``last_export_upside`` for later
    persistence — no field is added to the frozen `OptimisationResult` (D13 discipline).

    ``cannibalised_floor_gbp`` is the MEASURED behind-the-meter value the export dispatch gave up:
    the no-export solve's priced-floor objective terms MINUS the overlay solve's (≥0 — the overlay
    maximises floor terms *plus* export revenue, so its floor terms alone cannot exceed the
    no-export optimum). It is the netting quantity MD-F-05 limb (b) needs: without it the floor
    reports the physically impossible "full no-export arbitrage + full export volume". £0 whenever
    no overlay ran.

    The three trailing fields are the MEASUREMENT the export-feasibility study needs and nothing
    else reads: the export limit the overlay was actually bounded by, and the annual discharge of
    the two solves so "how much of the behind-the-meter-suppressed volume did export recover" is
    answerable from the run rather than re-derived. Additive with defaults; ``None`` on any path
    that did not measure them.
    """

    delta_gbp: float
    export_mwh: float
    price_content_hash: str | None
    price_source_ref: str | None
    coverage_note: str | None
    cannibalised_floor_gbp: float = 0.0
    export_limit_kw: float | None = None
    floor_discharge_kwh: float | None = None
    overlay_discharge_kwh: float | None = None


class MilpEngine:
    """The production optimisation engine (sizer + MILP or MPC). See module docstring.

    Two-solver architecture:
    - ``_sizing_solver``: annual MILP — fast LP over the full year, used for every
      candidate in the search space (scanning 9+ specs in seconds).
    - ``_dispatch_solver``: the same annual MILP instance. This is the production path
      and the only supported one.

    **``use_mpc=True`` is QUARANTINED and raises** (2026-08-15). The four silent-failure faults
    that caused it are fixed in ``MpcDispatchSolver`` (WP-SIZE-01, 2026-11-16; regression suite
    ``tests/test_mpc_infeasibility_regression.py``); the engine entry point stays closed until
    the owner decides to open it. See the raise in ``__init__``; ``tests/test_mpc_quarantine.py``
    pins it. The default annual path is unaffected and is what every published run used.
    """

    MODEL_VERSION = MilpDispatchSolver.MODEL_VERSION

    def __init__(
        self,
        *,
        sizing_solver: MilpDispatchSolver | None = None,
        registry: StreamRegistry | None = None,
        seed: int = 0,
        use_mpc: bool = False,
        mpc_settings: MpcSettings | None = None,
        use_two_stage_search: bool = True,
        market_forecast: MarketForecast | None = None,
        export_feasibility_override: ExportFeasibilityOverride | None = None,
        study_basis: StudyBasis | None = None,
    ) -> None:
        self._registry = registry if registry is not None else default_registry()
        self._seed = int(seed)
        if use_mpc:
            # QUARANTINE (Olly, 2026-08-15). Unconditional, and deliberately not gated on the
            # assumption release: there is no release on which this path is currently trustworthy.
            #
            # The rolling-horizon path fails CLOSED-BUT-SILENT — it reports `status="optimal"` with
            # confident 0.0 volumes rather than refusing, which is the null-reads-as-zero defect
            # (D45) wearing a status field. Four faults, each verified by reading the code:
            #   1. `mpc_solver` seeds `current_soe = 0.0` while the model enforces a minimum
            #      state-of-energy floor, so window 1 is infeasible before it starts.
            #   2. The infeasible branch carries the unchanged state into the next window, making
            #      it an ABSORBING FIXED POINT: once one window refuses, all of them do (measured
            #      14/14).
            #   3. `mpc_solver.py:317` hard-codes `status="optimal"` on the assembled result, so
            #      the refusal never reaches the caller as a failure.
            #   4. `milp_solver.solve_window` (~:770) swallows the refusal text, so even a caller
            #      that checked would have nothing to read.
            #
            # Faults 3 and 4 are why this is a raise and not a warning: a path that cannot report
            # its own failure must not be reachable from a run that prices anything.
            #
            # NOT AFFECTED: the default annual path (`use_mpc=False`). Verified three ways — the
            # refusal is guarded by `initial_soe_kwh is not None` and the annual path passes
            # `None`; the annual solve is measured optimal and non-zero at 1/2/4/8h on gb_v1_8;
            # and a test asserts it. The six published site packs used the annual path.
            #
            # WP-SIZE-01 (2026-11-16): faults 1-4 are FIXED in `MpcDispatchSolver` — the loop starts
            # at the usable SoE floor, a refused window ends the solve with the window's own status
            # (INFEASIBLE / INSUFFICIENT_DATA) and reason and no volumes, and OPTIMAL is reported
            # only when every window solved (`tests/test_mpc_infeasibility_regression.py`). This
            # block is deliberately KEPT: lifting it makes the MPC reachable from priced runs, which
            # is the owner's decision, and the engine has not been exercised end to end on an MPC
            # dispatch result (see the study-basis refusal below, which is also still unreachable).
            raise InvalidRequestError(
                "MilpEngine refuses use_mpc=True: the MPC rolling-horizon dispatch path is "
                "QUARANTINED at the engine entry point. Its silent-failure mode (status='optimal' "
                "with 0.0 volumes after every window refused) is fixed in MpcDispatchSolver, but "
                "the path has not been opened to priced runs. Use the default annual MILP "
                "(use_mpc=False), which is unaffected and is what every published run used."
            )
        # The named D46 FEASIBILITY-STUDY BASIS. ``None`` on every ordinary run — the underwriting
        # basis — and then every line below that mentions it is inert, so an underwriting run is
        # byte-identical to the pre-D46 engine. Set, it prices export INTO the priced floor via
        # `_StudyBasisDispatchSolver`, and it is STATED on every result this engine returns. There
        # is no default, no environment variable and no truthy flag: the basis is a constructed,
        # attributed, acknowledgement-carrying record or it does not exist.
        self._study_basis = study_basis
        if study_basis is not None:
            # Three refusals, all of them structural double-counting or mixed-basis hazards rather
            # than style preferences. A study basis is a whole pricing basis, so it may not be
            # combined with a second mechanism for the same physics.
            if market_forecast is not None:
                raise InvalidRequestError(
                    "MilpEngine refuses study_basis together with market_forecast: the D43 gated "
                    "overlay would measure an export delta against a 'floor' solve that already "
                    "prices export, so the same exported energy would be reported twice — once in "
                    "the priced floor and once as gated upside. Run the study basis, or run the "
                    "underwriting basis with the forecast; never both."
                )
            if export_feasibility_override is not None:
                raise InvalidRequestError(
                    "MilpEngine refuses study_basis together with export_feasibility_override: "
                    "both supply an export limit for a study, and the override exists precisely "
                    "because export value could not reach the floor. The study basis carries its "
                    "own declared export limit (StudyBasis.export_limit_kw)."
                )
            # CURRENTLY UNREACHABLE — the engine-wide MPC quarantine above raises first. Kept, not
            # deleted, so the hazard is still guarded the moment the quarantine is lifted; its
            # assertion in `test_study_basis_engine` was removed rather than left to pass for the
            # wrong reason. Restore that assertion together with this block's reachability.
            if use_mpc:  # pragma: no cover - unreachable while the MPC quarantine stands
                raise InvalidRequestError(
                    "MilpEngine refuses study_basis together with use_mpc: the MPC rolling-horizon "
                    "solver is constructed independently of the study solver and its windows were "
                    "never designed to carry an export variable, so the dispatch and the priced "
                    "floor would be built on different bases."
                )
            if sizing_solver is not None:
                raise InvalidRequestError(
                    "MilpEngine refuses study_basis together with an injected sizing_solver: the "
                    "named basis is applied BY the solver, so an injected one would silently price "
                    "on the underwriting basis while the run claimed to be a study."
                )
        # Optional wholesale price series for the consent-gated EXPORT overlay (issue 018 / D43).
        # Engine config (like the progress sink), NOT a wire value: ``None`` (the default, and every
        # run today) means the export overlay never runs — byte-identical to the pre-018 engine, and
        # nothing enters the run identity. A supplied forecast only ever activates the overlay on a
        # site whose consents cohere (`export_dispatch_enabled`); the overlay then changes ONLY the
        # WHOLESALE_P415 gated-upside entry, which correctly gives that run a new identity.
        self._market_forecast = market_forecast
        # The labelled EXPORT FEASIBILITY OVERRIDE (see `ExportFeasibilityOverride`). Engine config
        # like the forecast above, and ``None`` on every ordinary run: absent, the site's own
        # `export_dispatch_enabled` predicate and its declared MEC are the only things that can open
        # the overlay, byte-identically to before. Present, it is STATED on every result this engine
        # returns — spec or no-spec — because a study assumption that is not on the record is a
        # silent default, which is the one thing this input exists to prevent.
        self._export_override = export_feasibility_override
        # Two-stage continuous sizing search (D32) — the DEFAULT since D34: C025 is
        # resolved (gb_v1_3 prices an energy capex component, so a wide-duration
        # recommendation is an economic trade-off, not a band-edge artifact) and the
        # D29-mandated bake-off re-run certified the two-stage confirm against the dense
        # exact-grid reference at ~4x less wall time. ``False`` forces the legacy 3x3 grid.
        self._use_two_stage = bool(use_two_stage_search)
        # Sizing solver: always the annual MILP (fast; scans every candidate in the search space).
        # On the D46 study basis it is the study subclass, so EVERY candidate's own dispatch and
        # finance case are built with export priced in the floor — which is what lets a study basis
        # change a viability verdict rather than only a label.
        self._sizing_solver = (
            _StudyBasisDispatchSolver(study_basis, registry=self._registry, seed=seed)
            if study_basis is not None
            else sizing_solver or MilpDispatchSolver(registry=self._registry, seed=seed)
        )
        # Dispatch solver: MPC on the chosen spec (accurate); falls back to the sizing solver.
        if use_mpc:
            self._dispatch_solver: MilpDispatchSolver | MpcDispatchSolver = MpcDispatchSolver(
                settings=mpc_settings, registry=self._registry, seed=seed
            )
        else:
            self._dispatch_solver = self._sizing_solver
        self._assumptions: AssumptionLibrary | None = None
        self._dispatch_energy: DispatchEnergy | None = None
        self._sizing_run: SizingRun | None = None
        self._dispatch_schedule: HalfHourlyDispatchSchedule | None = None
        self._customer_bill: CustomerBill | None = None
        self._provisioning_cost: float | None = None
        self._export_upside_result: ExportUpside | None = None
        # N23: the overlay solve's own schedule, and whether finance's dispatch volumes were
        # actually re-derived from it. The CONTRACTED export entry is priced ONLY when this is
        # True, so priced export revenue and the charge volume that produced it can never diverge.
        self._export_overlay_schedule: HalfHourlyDispatchSchedule | None = None
        self._export_volumes_reconciled: bool = False
        # N22: set only when the forecast-driven export overlay put an agreed-tariff CONTRACTED
        # entry into the priced floor. Per-solve state, reset in `solve` with the rest.
        self._forecast_floor_note: str | None = None
        # The export-feasibility study's measurement on a run that recommends NO system: the one
        # place the override produces a number without a selected candidate. Per-solve state.
        self._export_study_note: str | None = None
        # D46: the MD-F-05 limb (b) CHECK on a study-basis run — the behind-the-meter value the
        # export dispatch displaced, measured ONCE on a fresh plain solver after selection and
        # reported as arithmetic a reader can verify, never applied as a second deduction.
        # Per-solve state; ``None`` on every underwriting run.
        self._study_netting_check: str | None = None
        # Which physical quantity scaled the sizing search box, and the required import capacity the
        # recommended spec turned out to need (D44). Per-solve state, reset in `solve`; both are
        # STATED on the result's warnings (the channel that actually reaches the record) and also
        # exposed as duck-typed hooks for later structured persistence.
        self._sizing_basis_used: SizingBasis | None = None
        self._required_capacity: RequiredConnectionCapacity | None = None
        # Presentation-only progress side channel (the web run screen), attached via
        # ``set_progress_sink``. Engine config, NOT per-solve state — deliberately not reset in
        # ``solve`` (see there). ``None`` is byte-identical to the seam before this hook (C035).
        self._progress_sink: ProgressSink | None = None

    def set_progress_sink(self, sink: ProgressSink | None) -> None:
        """Attach a progress side channel (additive; C035) — ``sink(phase, done, total)``.

        Discovered duck-typed by `generate_quote` (``getattr(engine, "set_progress_sink", …)``).
        The sink is fired from inside the sizing search and once before the final dispatch; every
        emit is guarded so a raising/misbehaving sink can never perturb the deterministic solve.
        Stored as engine config (survives across solves until replaced); ``None`` disables it.
        """
        self._progress_sink = sink

    # --- OptimisationEngine Protocol ---------------------------------------

    def build_optimisation_request(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario_set: str | None = None,
    ) -> OptimisationRequest:
        """Assemble the request (full stream stack, production solve mode) and stash the library."""
        self._assumptions = assumptions  # used by solve() (falls back to load-by-version)
        return OptimisationRequest(
            request_id=f"req_{site_model.site_id}",
            site_model=site_model,
            assumption_version=assumptions.metadata.version,
            scenario_set_id=scenario_set,
            requested_streams=list(MarketStream),
            solve_mode=SolveMode.PRODUCTION,
        )

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        """Size + dispatch the request into a frozen `OptimisationResult` (never raises)."""
        if not isinstance(request, OptimisationRequest):
            raise InvalidRequestError(
                f"MilpEngine.solve expects an OptimisationRequest, got {type(request).__name__}"
            )
        self._dispatch_energy = None
        self._sizing_run = None
        self._dispatch_schedule = None
        self._customer_bill = None
        self._provisioning_cost = None
        self._export_upside_result = None
        self._export_overlay_schedule = None
        self._export_volumes_reconciled = False
        self._forecast_floor_note = None
        self._export_study_note = None
        self._study_netting_check = None
        self._sizing_basis_used = None
        self._required_capacity = None
        refusal = self._study_basis_refusal(request.site_model)
        if refusal is not None:
            # A REFUSED BASIS, not a refused site: the run stops because the basis it was asked to
            # price on cannot be applied coherently to THIS site, and saying so is the only honest
            # answer. Never a data-shaped verdict (D45) — the message names the basis conflict.
            return self._no_spec_result(request, None, SolverStatus.INSUFFICIENT_DATA, refusal)
        try:
            assumptions = self._resolve_assumptions(request)
        except AssumptionError as exc:
            return self._no_spec_result(
                request, None, SolverStatus.INSUFFICIENT_DATA, f"assumptions unavailable: {exc}"
            )

        site = request.site_model
        basis = self._sizing_basis(site)
        if basis is None:
            return self._no_spec_result(
                request,
                assumptions,
                SolverStatus.INSUFFICIENT_DATA,
                "no sizing basis: the site has no connection limit (connection.mic_kw / "
                "connection.asc_kw) and no observed peak (load_data.max_import_kw) to scale a "
                "candidate battery from.",
            )
        self._sizing_basis_used = basis
        base = basis.capacity_kw

        covenant = numeric(maybe(assumptions, "dscr_covenants", "min_p90_dscr"))
        objective = SizingObjective(min_p90_dscr_covenant=covenant)
        try:
            run = self._run_search(request, site, assumptions, objective, base)
        except ProvisioningInfeasibleError as exc:
            # Issue 017 / D42: a declared obligation no battery size can meet is a FINAL
            # verdict (D39) — never a fall-through to a manufactured optimum.
            return self._no_spec_result(
                request,
                assumptions,
                SolverStatus.INFEASIBLE,
                str(exc),
                selection_status=SelectionStatus.NO_VIABLE_CONFIGURATION,
                extra_warnings=[basis.note],
            )
        # The search box's anchor is part of the search's own record: a reader of the persisted
        # sizing surface must be able to see WHY the grid stops where it stops (a box scaled from
        # the load rather than from the wire is the difference between a 33.9 kW ceiling and a
        # 487.5 kW one on a real site).
        run = replace(run, warnings=[*run.warnings, basis.note])
        # Retain the full evaluated grid (every power×duration candidate with its levered return,
        # P90 DSCR and covenant feasibility) so the quote pipeline can persist it for the 3D sizing
        # surface — captured even when no candidate produces a spec (the surface still shows the
        # honestly-infeasible grid). Mirrors the _dispatch_energy stash; D13 stays honoured (no
        # field is added to the frozen OptimisationResult).
        self._sizing_run = run

        chosen = self._choose(run, objective)
        # N16: hand the indicative choice back to the run, so the grid the pipeline persists marks
        # the point the quote reports instead of contradicting it with "no candidate selected".
        # A run with a covenant-feasible `selected` is returned unchanged by this call.
        if chosen is not None:
            reason = _indicative_reason(chosen)
            self._sizing_run = run = run.with_indicative_selection(chosen, reason)
        if chosen is None or chosen.optimisation_result.selected_power_kw is None:
            # Distinguish the two honest no-spec verdicts: candidates that SOLVED and PRICED but
            # none of which is economically viable (an economics verdict — the dispatch worked),
            # versus candidates that never produced a dispatch at all (a data/feasibility issue).
            solved_and_priced = [
                e
                for e in run.candidates_evaluated
                if e.priceable and e.optimisation_result.selected_power_kw and not e.proxy
            ]
            if solved_and_priced:
                # A DATA-SHAPED LABEL MUST NEVER BE ATTACHED TO AN ECONOMIC OUTCOME, and the
                # converse: an ECONOMICS label must never be attached to a mechanically-forced £0.
                # Which of the two applies is decided by whether the dispatch model had a
                # connection limit to charge through AT ALL:
                #
                # * **No limit declared** → `milp_solver` resolves the connection limit to 0 kW
                #   (`conn_limit = min(MIC, ASC) if declared else 0.0`), so `milp_solver`'s
                #   `charge_ub = min(power, max(0, limit − load − aux))` is
                #   0 in every half-hour and every candidate prices at ~£0 MECHANICALLY. The
                #   economics of a battery here were never tested; this is a DATA verdict.
                # * **A limit IS declared** (even one that looks like the metered peak copied into
                #   MIC) → the dispatch charged, discharged and priced. That is an ECONOMIC result
                #   and it keeps the economics verdict. The placeholder-MIC signature is reported
                #   ALONGSIDE it as a data ask, with the MEASURED headroom and the run's own best
                #   priced floor, so nobody can read it as "there was no room to charge".
                #
                # The previous code returned INSUFFICIENT_DATA on the MIC≈peak metadata signature
                # alone and asserted the signature "leaves no charge headroom … every candidate
                # necessarily priced at or near zero". Its own run record refuted that on all three
                # live sites carrying the signature: 121,777 kWh/yr charged over 2,181 half-hours at
                # full rated power, median measured headroom 118.7 kW, priced floors up to
                # £18,510/yr (`validation_requirements.md` §3).
                best_floor_gbp = max(self._floor_value(e) for e in solved_and_priced)
                # The export-feasibility study, measured on the run that recommends nothing —
                # the only place a labelled override can produce a number, because the overlay
                # otherwise runs only on a SELECTED candidate and these sites select none.
                study_note = self._export_study_note = self._export_feasibility_study(
                    site, assumptions, solved_and_priced, list(request.requested_streams)
                )
                declared_limit = connection_power_limit_kw(site)
                capacity_note = self._capacity_headroom_note(
                    site,
                    measured=(
                        None
                        if declared_limit is None
                        else self._measured_charge_headroom(site, declared_limit)
                    ),
                    bound=self._connection_charge_limit_bound(run),
                    best_floor_gbp=best_floor_gbp,
                )
                if self._connection_capacity_undeclared(site):
                    return self._no_spec_result(
                        request,
                        assumptions,
                        SolverStatus.INSUFFICIENT_DATA,
                        capacity_note
                        or "connection capacity unknown: no import capacity is declared.",
                        selection_status=SelectionStatus.INSUFFICIENT_DATA,
                        extra_warnings=[basis.note, *([study_note] if study_note else [])],
                        candidate_disclosures=self._candidate_model_disclosures(solved_and_priced),
                        no_spec_evidence=self._no_spec_dispatch_evidence(
                            site,
                            assumptions,
                            solved_and_priced,
                            list(request.requested_streams),
                            "no import capacity is declared, so no candidate could be sized "
                            "against a real connection.",
                        ),
                    )
                # N15: the evidence count must be the count of the evidence. The verdict used to
                # format `len(run.candidates_evaluated)` — the WHOLE cloud, most of which is
                # typical-day proxy solves the filter three lines above deliberately excludes —
                # into the claim "dispatch solved and priced". A run confirming 10 candidates on
                # the metered year reported ~280. The proxy sweep is real work and is worth
                # stating, but it is a search-guidance count, not evidence that a system was
                # priced, so it is reported separately and labelled as what it is.
                proxy_swept = sum(1 for e in run.candidates_evaluated if e.proxy)
                # N6: the search re-solves a size-diverse cover of the whole box on the full
                # series before this verdict is allowed to stand. When it did, say so here —
                # the verdict's credibility rests on WHICH candidates were confirmed, not just
                # how many.
                cover_note = next(
                    (w for w in run.warnings if w.startswith("no-viable cover (N6)")), None
                )
                return self._no_spec_result(
                    request,
                    assumptions,
                    SolverStatus.OPTIMAL,
                    # CORRECTED 2026-08-15. This previously read "none is economically viable, no
                    # candidate produces a positive project IRR or levered return". THE RECORD DOES
                    # NOT EVIDENCE THAT: every candidate on the persisted sizing grid holds
                    # `project_irr_pct` and `levered_return_pct` NULL, so no candidate was ever
                    # compared against zero and nothing here establishes a POSITIVE-return
                    # comparison. The claim was read off this string and repeated into a commit
                    # message, a handoff entry and a client pack before the record was checked.
                    # State the measured fact instead: candidates were solved and priced on the
                    # full series, and none was SELECTED. `sizing_grid.selection_reason` carries
                    # why, in the terms the search actually established.
                    f"no viable configuration: {len(solved_and_priced)} candidate "
                    "system(s) were solved and priced on the full series and NONE WAS SELECTED. "
                    "See the sizing grid's selection reason for what the search established, and "
                    "what it did not: it is the only account of this verdict built from measured "
                    "values rather than asserted. "
                    f"A further {proxy_swept} candidate(s) were explored by typical-period PROXY "
                    "solves, which guide the search and are never priced evidence. No system is "
                    "recommended. The evaluated sizing surface is preserved as evidence; see "
                    "the ranked data asks for what could change this verdict (tariff structure, "
                    "export consents, route-to-market)." + (f" {cover_note}" if cover_note else ""),
                    selection_status=SelectionStatus.NO_VIABLE_CONFIGURATION,
                    extra_warnings=[
                        basis.note,
                        f"best candidate priced floor £{best_floor_gbp:,.2f}/yr (net of the signed "
                        "dispatch cost) — the verdict above is that this does not cover the "
                        "capex/opex basis at any searched size, NOT that the dispatch earned "
                        "nothing.",
                        *([capacity_note] if capacity_note is not None else []),
                        *([study_note] if study_note else []),
                    ],
                    candidate_disclosures=self._candidate_model_disclosures(solved_and_priced),
                    no_spec_evidence=self._no_spec_dispatch_evidence(
                        site,
                        assumptions,
                        solved_and_priced,
                        list(request.requested_streams),
                        "no candidate produces a positive project IRR or levered return at this "
                        "site on the priced value floor.",
                    ),
                )
            return self._no_spec_result(
                request,
                assumptions,
                self._aggregate_status(run),
                "no candidate produced a dispatch — insufficient data (e.g. no metered "
                "half-hourly series) or infeasible across the search space.",
                extra_warnings=[basis.note],
            )
        # Post-sizing full-year solves (the re-dispatch below + the provisioning strip) ride one
        # "dispatch" phase so the bar keeps moving past the sizing search instead of stalling.
        _emit_progress(self._progress_sink, "dispatch", None, None)
        # Stash the selected candidate's annual volumes so the quote pipeline can compute finance's
        # levies-on-charge + price-to-target (the D13 hook) without promoting fields onto the frozen
        # OptimisationResult. The volumes do not travel on the result, so they are produced here.
        self._dispatch_energy = self._dispatch_energy_for(
            request.site_model, assumptions, chosen, list(request.requested_streams)
        )
        # `_dispatch_energy_for` re-dispatched the chosen spec — it was the last solve, so the
        # solver now holds the chosen candidate's full half-hourly schedule. Retain it so the quote
        # pipeline can persist a representative-day projection for the dispatch chart (additive;
        # the frozen OptimisationResult is untouched — D13). The customer bill (W7-5, C017) rides
        # the same lifecycle: the chosen candidate's before/after decomposition, or None. Under
        # the two-solver split (MPC opt-in) the dispatch solver may not expose the bill hook —
        # the MPC rolling-horizon solver has none — so the bill honestly stays None there (the
        # documented pending seam on `QuoteRecord.customer_bill`), never a bill from the wrong
        # solve.
        self._dispatch_schedule = self._dispatch_solver.last_schedule()
        bill_hook = getattr(self._dispatch_solver, "last_customer_bill", None)
        self._customer_bill = bill_hook() if callable(bill_hook) else None
        self._provisioning_cost = self._provisioning_opportunity_cost(
            site, assumptions, chosen, list(request.requested_streams)
        )
        # Consent-gated wholesale EXPORT overlay (issue 018 / D43): a no-op unless the site's
        # consents cohere AND a covering price series was supplied to the engine — so every run
        # today (no forecast) is byte-identical here. Stashed for the WHOLESALE_P415 swap below.
        self._export_upside_result = self._export_upside(
            site, assumptions, chosen, list(request.requested_streams)
        )
        self._reconcile_export_volumes(site, chosen)
        # D46: the limb (b) CHECK on a study-basis run — one extra plain solve of the selected spec,
        # reported as arithmetic. ``None`` on every underwriting run.
        self._study_netting_check = self._study_netting_check_note(
            site, assumptions, chosen, list(request.requested_streams)
        )
        # D44: the import capacity the recommended spec actually needs, measured off whichever
        # dispatch the priced case is built on (after `_reconcile_export_volumes`, so an
        # export-priced run is measured on the OVERLAY dispatch it is actually paid for).
        self._required_capacity = self._required_connection_capacity(site, assumptions, chosen)
        return self._final_result(request, chosen)

    def _study_basis_refusal(self, site: SiteModel) -> str | None:
        """Why the declared study basis cannot be applied to ``site``, or ``None`` when it can.

        ``None`` on every underwriting run (no basis to check) and on every site whose own export
        route is closed — which is the case the study basis exists for.

        The one refusal: a site that ALREADY holds explicit WHOLESALE_P415 consent **and** declares
        an agreed export tariff has D43's real contracted route open, and `_final_result` prices
        that route into the floor on its own. Running the study basis on top would append a second
        priced export entry for the same exported energy, at a different (derived) tariff, on the
        same dispatch — the double count MD-F-05 limb (b) exists to prevent, and one that the
        idempotence of re-basing would NOT catch, because the defect is a duplicated revenue line
        rather than a doubly-netted cost. Such a site needs no study basis: it has the evidence.
        """
        study = self._study_basis
        if study is None:
            return None
        if not stream_consented(site, MarketStream.WHOLESALE_P415):
            return None
        if agreed_rate(site, MarketStream.WHOLESALE_P415) is None:
            return None
        return (
            f"STUDY BASIS REFUSED (D46) — not a data refusal: this site already holds explicit "
            "WHOLESALE_P415 consent AND declares an agreed export tariff, so D43's real CONTRACTED "
            "export route into the priced floor is already open and is priced from the site's own "
            "evidence. Applying the study basis "
            f"({study.basis_id!r}, derived tariff £{study.export_tariff_gbp_per_kwh:,.6f}/kWh) as "
            "well would put a SECOND priced export entry, at a different tariff, on the same "
            "exported energy. Re-run this site on the UNDERWRITING basis (no study_basis): it does "
            "not need a study, it has the consent and the contract. The study basis is for sites "
            "whose export route is closed."
        )

    def _study_netting_check_note(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> str | None:
        """MD-F-05 limb (b) CHECK for a study-basis run: measure the displaced BTM value ONCE.

        ``None`` on every underwriting run. On a study-basis run this solves the SELECTED spec once
        more on a **fresh plain** `MilpDispatchSolver` — no export variable, the ordinary no-export
        floor — and reports the difference in the priced-floor objective terms.

        It is a **check, not a deduction**, and the distinction is the whole point of limb (b). The
        study run's floor components already come from the export dispatch (there is no no-export
        solve behind them), so the netting has happened exactly once, at source. This number lets a
        reader verify that arithmetic — ``study floor terms ≈ no-export floor terms − displaced`` —
        rather than take it on trust. Subtracting it anywhere would net the same £ twice.

        A fresh solver is used deliberately (the `_export_upside` pattern): the engine's retained
        schedule / customer-bill hooks must keep describing the STUDY dispatch, which is the
        dispatch the priced case is built on.
        """
        study = self._study_basis
        if study is None:
            return None
        stream_ids = requested_streams or self._registry.stream_ids()
        constraints = dispatch_constraints_for(
            stream_ids,
            site,
            assumptions,
            registry=self._registry,
            candidate_power_kw=chosen.candidate.power_kw,  # D61: reserve what sizing reserved
        )
        problem = DispatchProblem(
            site_model=site,
            assumptions=assumptions,
            power_kw=chosen.candidate.power_kw,
            energy_kwh=chosen.candidate.energy_kwh,
            duration_hours=chosen.candidate.duration_hours,
            requested_streams=list(stream_ids),
            constraints=constraints,
            scenario=None,
        )
        plain_solver = MilpDispatchSolver(seed=self._seed)
        plain = plain_solver.solve(problem)
        plain_schedule = plain_solver.last_schedule()
        if plain.solver_status != SolverStatus.OPTIMAL or plain_schedule is None:
            return (
                "MD-F-05(b) CHECK NOT MEASURED on this study-basis run: the comparison no-export "
                "solve of the selected spec did not return an optimum, so the displaced "
                "behind-the-meter value is not reported. The priced floor is unaffected — its "
                "dispatch-derived components come from the study dispatch itself, so the netting "
                "already happened once, at source."
            )
        study_schedule = self._dispatch_schedule
        if study_schedule is None:
            return None
        displaced = round(
            plain_schedule.priced_floor_terms_gbp - study_schedule.priced_floor_terms_gbp, 2
        )
        return (
            "MD-F-05(b) CHECK (measured once, applied never): the no-export solve of the same spec "
            f"earns £{plain_schedule.priced_floor_terms_gbp:,.2f}/yr of priced-floor dispatch "
            f"terms against £{study_schedule.priced_floor_terms_gbp:,.2f}/yr on the study "
            f"dispatch, so the export displaced £{displaced:,.2f}/yr of behind-the-meter value. "
            "That displacement "
            "is ALREADY out of this run's floor: the floor's arbitrage and DUoS components are "
            "priced from the study dispatch, not from a no-export solve, so there is exactly ONE "
            "netting site and the CONTRACTED export entry is correctly GROSS. Subtracting this "
            "figure from the export entry as well would deduct the same £ twice. "
            f"Discharge behind the meter {study_schedule.annual_discharge_kwh:,.1f} kWh/yr on the "
            f"study dispatch vs {plain_schedule.annual_discharge_kwh:,.1f} kWh/yr with no export; "
            f"exported {study_schedule.annual_export_mwh or 0.0:,.3f} MWh/yr."
        )

    def _reconcile_export_volumes(self, site: SiteModel, chosen: CandidateEvaluation) -> None:
        """N23: keep finance's dispatch volumes and the priced floor on the SAME dispatch.

        `_dispatch_energy_for` above re-dispatches the chosen spec on the plain **no-export**
        problem, but when the site consents to WHOLESALE_P415 with a declared export tariff,
        `_final_result` prices ``tariff × the OVERLAY's export MWh`` into `value_streams` as
        CONTRACTED. Exporting means importing and storing energy the floor solve never charged, so
        the pre-N23 pipeline sent finance the floor's charge volume — the extra net import and its
        levies never reached the cost side while the revenue they produced entered P50, P90, the
        DSCR and the price-to-target's delivered-energy denominator.

        So: when that floor entry is in play, the volumes are re-derived from the overlay schedule
        `solve_with_export` already returned (BTM discharge only — exported kWh displace no site
        import, so they must not count as delivered energy or as levy-displacing discharge). If
        that schedule is unavailable the flag stays False and `_final_result` holds the contracted
        export OUT of the floor: the two are wired to the same switch, so priced export revenue
        without its own charge volume is structurally impossible, not merely unlikely.
        """
        if self._export_floor_tariff(site, chosen) is None:
            return
        overlay = self._export_overlay_schedule
        if overlay is None:
            return
        self._dispatch_energy = DispatchEnergy(
            annual_charge_kwh=overlay.annual_charge_kwh,
            annual_discharge_kwh=overlay.annual_discharge_kwh,
        )
        self._export_volumes_reconciled = True

    def _export_floor_tariff(self, site: SiteModel, chosen: CandidateEvaluation) -> float | None:
        """The agreed export tariff that would put CONTRACTED export revenue in the priced floor.

        ``None`` (the ordinary case) when no overlay ran, the site has no WHOLESALE_P415 gated
        entry to re-base, consent is absent, or no export tariff is declared. Stated once so
        `_reconcile_export_volumes` and `_final_result` cannot drift apart (N23).
        """
        if self._export_upside_result is None:
            return None
        if not any(
            v.stream == MarketStream.WHOLESALE_P415 for v in chosen.optimisation_result.gated_upside
        ):
            return None
        if not stream_consented(site, MarketStream.WHOLESALE_P415):
            return None
        return agreed_rate(site, MarketStream.WHOLESALE_P415)

    # --- composition helpers -----------------------------------------------

    def selected_dispatch_energy(self) -> DispatchEnergy | None:
        """The selected candidate's annual charge/discharge volumes from the last ``solve``.

        Duck-typed hook the quote pipeline reads to compute finance's levies-on-charge +
        price-to-target. ``None`` when the last solve produced no dispatch (no spec).
        """
        return self._dispatch_energy

    def last_sizing_run(self) -> SizingRun | None:
        """The full evaluated sizing grid from the last ``solve`` (every power×duration candidate
        with its levered return, P90 DSCR and covenant feasibility, plus the chosen optimum).

        Duck-typed hook mirroring `selected_dispatch_energy`: the quote pipeline reads it and
        persists ``last_sizing_run().sizing_grid()`` onto the `QuoteRecord` for the 3D sizing
        surface — additively, **without** promoting any field onto the frozen `OptimisationResult`
        (D13). ``None`` when the last solve evaluated no grid (assumptions/sizing basis unavailable
        before enumeration); a grid that produced no feasible spec is still returned (honest).
        """
        return self._sizing_run

    def last_dispatch_profile(self) -> DispatchProfile | None:
        """A representative day of the chosen candidate's dispatch from the last ``solve``.

        Duck-typed hook mirroring `last_sizing_run`: the quote pipeline reads it and persists it
        onto the `QuoteRecord` for the Wave-5 dispatch chart — additively, **without** promoting any
        field onto the frozen `OptimisationResult` (D13). ``None`` when the last solve produced no
        dispatch (no spec / insufficient data — e.g. no metered half-hourly series).
        """
        if self._dispatch_schedule is None:
            return None
        return self._dispatch_schedule.representative_day_profile()

    def last_customer_bill(self) -> CustomerBill | None:
        """The last ``solve``'s bill — before/after when a spec was chosen, else BEFORE-ONLY.

        Duck-typed hook mirroring `last_dispatch_profile` (W7-5, C017): the quote pipeline
        persists it onto the `QuoteRecord` so the proposal headline is a record-backed number, and
        Gate 3b reconciles its BEFORE side against the site's own invoices.

        **A no-spec run still returns a bill** (`_before_only_customer_bill`, produced in
        `_no_spec_result`). ``annual_bill_before_gbp`` is dispatch-invariant — the site's own load
        on the tariff stack its record states — so it exists whether or not a battery does; the
        AFTER side is wholly ``None`` there, never £0, because no dispatch was solved.

        ``None`` only when there is no bill to compute at all: no half-hourly series, an unreadable
        one, or a tariff with no time-mappable energy rate — the proposal keeps its honest pending
        seam and Gate 3b records itself un-run.
        """
        return self._customer_bill

    def _dispatch_energy_for(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> DispatchEnergy:
        """Re-dispatch the selected spec once to recover its annual charge/discharge volumes."""
        stream_ids = requested_streams or self._registry.stream_ids()
        constraints = dispatch_constraints_for(
            stream_ids,
            site,
            assumptions,
            registry=self._registry,
            candidate_power_kw=chosen.candidate.power_kw,  # D61: reserve what sizing reserved
        )
        problem = DispatchProblem(
            site_model=site,
            assumptions=assumptions,
            power_kw=chosen.candidate.power_kw,
            energy_kwh=chosen.candidate.energy_kwh,
            duration_hours=chosen.candidate.duration_hours,
            requested_streams=list(stream_ids),
            constraints=constraints,
            scenario=None,
            minimise_published_peak=True,  # D67: this schedule is the customer bill
        )
        return to_finance_dispatch_energy(self._dispatch_solver.solve(problem))

    def _provisioning_opportunity_cost(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> float | None:
        """The obligation's measured cost: one extra deterministic solve of the SELECTED spec
        with the obligations stripped; the priced-floor delta (excluding the LOAD_SERVICE
        stream's own declared value) is the foregone arbitrage/DUoS/CM — engine-measured,
        never estimated (issue 017 / D42). A FRESH solver instance is used so the retained
        dispatch/bill hooks keep the real (obligated) solve."""
        if not site.provisioning_requirements:
            return None
        if not isinstance(self._dispatch_solver, MilpDispatchSolver):
            return None  # only the real engine measures; never derived from a baseline
        stripped = site.model_copy(update={"provisioning_requirements": None})
        stream_ids = requested_streams or self._registry.stream_ids()
        constraints = dispatch_constraints_for(
            stream_ids,
            stripped,
            assumptions,
            registry=self._registry,
            candidate_power_kw=chosen.candidate.power_kw,  # D61: reserve what sizing reserved
        )
        problem = DispatchProblem(
            site_model=stripped,
            assumptions=assumptions,
            power_kw=chosen.candidate.power_kw,
            energy_kwh=chosen.candidate.energy_kwh,
            duration_hours=chosen.candidate.duration_hours,
            requested_streams=list(stream_ids),
            constraints=constraints,
            scenario=None,
        )
        unconstrained = MilpDispatchSolver(seed=self._seed).solve(problem)
        if unconstrained.solver_status != SolverStatus.OPTIMAL:
            return None  # no unconstrained dispatch — cost unavailable, never invented

        def _floor_excl_ls(result: OptimisationResult | DispatchResult) -> float:
            return sum(
                v.annual_revenue_gbp
                for v in result.value_streams
                if v.firmness in _PRICED_FIRMNESS and v.stream != MarketStream.LOAD_SERVICE
            )

        delta = _floor_excl_ls(unconstrained) - _floor_excl_ls(chosen.optimisation_result)
        return round(max(delta, 0.0), 2)

    def last_provisioning_opportunity_cost(self) -> float | None:
        """Duck-typed hook (D13 pattern): the selected spec's measured obligation cost."""
        return self._provisioning_cost

    @staticmethod
    def _parasitic_draw_kw(assumptions: AssumptionLibrary, power_kw: float) -> float:
        """The continuous auxiliary draw the MILP subtracts from the charge headroom (kW).

        Same assumption key and same arithmetic as the solver's own bound
        (``battery_opex.parasitic_load_fraction_of_power`` × rated power — `milp_solver`'s
        ``aux_kw``), read from the release rather than restated here: an absent key means zero, as
        it does in the solver. No number is introduced on the pricing path by this method.
        """
        fraction = numeric(maybe(assumptions, "battery_opex", "parasitic_load_fraction_of_power"))
        if fraction is None or fraction <= 0:
            return 0.0
        return float(fraction) * float(power_kw)

    def _required_connection_capacity(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
    ) -> RequiredConnectionCapacity | None:
        """The import capacity the RECOMMENDED spec needs — an OUTPUT, never a blocker (D44).

        ``max(site load + battery charge) + parasitic auxiliary draw`` over the operative dispatch:
        precisely the quantity the solver's own connection constraint bounds
        (`milp_solver`'s ``charge_ub = min(power, max(0, limit − load − aux))``, where ``limit`` is
        the declared ``min(MIC, ASC)`` de-rated by the release's assumed power factor if it declares
        one — unity, hence the declared limit itself, on every release today). Two deliberate
        consequences of
        using the solver's own inequality rather than a net-import calculation:

        * battery DISCHARGE is not credited as reducing the requirement, because the solver's
          charge bound does not credit it either (conservative, and consistent with what was
          solved);
        * EXPORT injection is excluded — it leaves through the connection, it does not consume
          import capacity.

        Measured on the OVERLAY dispatch when contracted export revenue is what the priced case is
        built on (N23 already re-bases finance's volumes there), else on the priced-floor dispatch;
        ``schedule_basis`` records which. Not measured (``None``) when no schedule survived the
        solve. It does not include a declared provisioning ADDITION drawn through the wire: that is
        compiled inside the solver and is not carried on the schedule — stated in the note rather
        than silently omitted.
        """
        overlay = self._export_overlay_schedule
        if self._export_volumes_reconciled and overlay is not None:
            schedule = overlay
            schedule_basis = "export overlay dispatch"
        elif self._dispatch_schedule is not None:
            schedule = self._dispatch_schedule
            schedule_basis = "priced-floor dispatch"
        else:
            return None
        aux_kw = self._parasitic_draw_kw(assumptions, chosen.candidate.power_kw)
        load = np.asarray(schedule.site_load_kw, dtype=float)
        charge = np.asarray(schedule.charge_kw, dtype=float)
        if load.size == 0:
            return None
        coincident_peak = round(float(np.max(load + charge)), 3)
        required = round(coincident_peak + aux_kw, 3)
        peak_load = round(float(load.max()), 3)
        charge_peak = round(float(charge.max()), 3)
        declared = connection_power_limit_kw(site)
        spare = None if declared is None else round(declared - required, 3)
        spec = (
            f"{chosen.candidate.power_kw:,.1f} kW / {chosen.candidate.energy_kwh:,.1f} kWh "
            f"({chosen.candidate.duration_hours:,.2f} h)"
        )
        note = (
            f"REQUIRED CONNECTION CAPACITY (output, D44): the recommended {spec} system needs at "
            f"least {required:,.1f} kW of import capacity — the maximum of (metered load + battery "
            f"charge) over the {schedule_basis}, {coincident_peak:,.1f} kW, plus a "
            f"{aux_kw:,.2f} kW continuous parasitic auxiliary draw "
            "(battery_opex.parasitic_load_fraction_of_power). For comparison the site's own peak "
            f"load is {peak_load:,.1f} kW and the battery's own charge peak is {charge_peak:,.1f} "
            "kW; the two do not simply add, because the dispatch already avoids charging into the "
            "peak where the connection binds. Measured against the solver's own constraint "
            "(charge <= limit - load - aux), so discharge is NOT credited as reducing the "
            "requirement and export injection is excluded (it does not consume import capacity). "
            "Excludes any declared provisioning ADDITION drawn through the wire, which is compiled "
            "inside the solver and is not carried on the schedule."
        )
        if declared is None:
            note += (
                " No import capacity is declared for this site (connection.mic_kw / "
                f"connection.asc_kw), so THIS FIGURE IS THE DATA ASK: confirm with the DNO that at "
                f"least {required:,.1f} kW is available (ask id: connection_mic_mec_asc). Per D44 "
                "an unknown MIC is a derived assumption plus a data ask, never a blocker."
            )
        elif spare is not None and spare < 0:
            note += (
                f" The declared limit min(MIC, ASC) = {declared:,.1f} kW is {-spare:,.1f} kW BELOW "
                "this requirement, so the recommended spec cannot be charged as dispatched within "
                "the declared capacity — confirm the real capacity with the DNO (data ask: "
                "connection_mic_mec_asc) before the spec is issued."
            )
        else:
            note += (
                f" The declared limit min(MIC, ASC) = {declared:,.1f} kW leaves {spare:,.1f} kW "
                "spare against this requirement."
            )
        return RequiredConnectionCapacity(
            required_kw=required,
            observed_peak_load_kw=peak_load,
            battery_charge_peak_kw=charge_peak,
            parasitic_kw=round(aux_kw, 3),
            declared_limit_kw=declared,
            spare_vs_declared_kw=spare,
            schedule_basis=schedule_basis,
            note=note,
        )

    def last_required_connection_capacity(self) -> RequiredConnectionCapacity | None:
        """Duck-typed hook (D13 pattern): the import capacity the recommended spec needs (D44).

        ``None`` when the last solve recommended no spec or produced no schedule. The figure is
        ALSO stated on the result's warnings — the channel that actually reaches the record today —
        so the output cannot be lost if no caller adopts this hook.
        """
        return self._required_capacity

    def last_sizing_basis(self) -> SizingBasis | None:
        """Duck-typed hook (D13 pattern): which physical quantity scaled the sizing search box.

        ``None`` before the first solve, or when the site had no sizing scale at all. Also stated on
        the result's warnings and on the persisted sizing run's warnings.
        """
        return self._sizing_basis_used

    def _export_upside(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> ExportUpside | None:
        """The consent-gated wholesale export upside (issue 018 / D43), mirroring
        `_provisioning_opportunity_cost`: two extra deterministic solves of the SELECTED spec on
        FRESH solver instances (so the retained dispatch/bill hooks keep the real floor solve). It
        runs ONLY when the site's declared consents cohere for export AND a covering wholesale price
        series was supplied to the engine AND the dispatch solver is the real MILP — otherwise
        ``None`` (byte-identical to the pre-018 engine). The upside is the INCREMENTAL total value
        of enabling export: ``max((overlay floor-terms + export revenue) − floor floor-terms, 0)``
        — ≥0 by construction (export=0 recovers the floor optimum), auto-nets the retail energy
        bought and any floor arbitrage cannibalised, £0 when export never pays.

        **The Elexon-MID delta is gated upside and never touches the priced floor. The agreed
        export TARIFF is a different thing and does** (N22): with explicit WHOLESALE_P415 consent
        AND a declared export tariff, `_final_result` prices ``tariff × overlay export MWh`` into
        `value_streams` as CONTRACTED (D43), which finance sums into P50/P90, the DSCR and both
        IRRs. Because this overlay is what measures the export volume, supplying a forecast is a
        necessary condition for that floor entry — so "a forecast never moves the floor" is false,
        and `last_forecast_priced_floor_note` states on the quote when it did.

        **The labelled study override** (`ExportFeasibilityOverride`) is the SECOND way this gate
        opens, and it opens ONLY this gate: it supplies the export LIMIT the overlay is bounded by
        and permits the overlay to run, while `export_dispatch_enabled` and every consent field it
        reads stay exactly as the site recorded them. Because the overlay only ever writes to
        ``gated_upside``, and the CONTRACTED floor entry below is gated on real per-stream consent
        plus a declared export tariff, an override can measure export volume and can NEVER put a
        pound of it in the priced floor."""
        override = self._export_override
        if self._study_basis is not None:
            # D46: unreachable via the constructor (study_basis refuses market_forecast, and the
            # gate below needs one), and refused a second time here on purpose. On the study basis
            # export is already in the priced floor, so a gated overlay delta measured against a
            # "floor" solve that itself exports would report the same energy twice.
            return None
        if not export_dispatch_enabled(site) and override is None:
            return None
        if self._market_forecast is None:
            return None
        if not isinstance(self._dispatch_solver, MilpDispatchSolver):
            return None  # only the real MILP measures; never derived from a baseline/MPC schedule
        stream_ids = requested_streams or self._registry.stream_ids()
        constraints = dispatch_constraints_for(
            stream_ids,
            site,
            assumptions,
            registry=self._registry,
            candidate_power_kw=chosen.candidate.power_kw,  # D61: reserve what sizing reserved
        )
        problem = DispatchProblem(
            site_model=site,
            assumptions=assumptions,
            power_kw=chosen.candidate.power_kw,
            energy_kwh=chosen.candidate.energy_kwh,
            duration_hours=chosen.candidate.duration_hours,
            requested_streams=list(stream_ids),
            constraints=constraints,
            scenario=None,
        )
        # Floor solve (export OFF) on a fresh solver — the exact no-export optimum to subtract.
        floor_solver = MilpDispatchSolver(seed=self._seed)
        floor_result = floor_solver.solve(problem)
        if floor_result.solver_status != SolverStatus.OPTIMAL:
            return None
        floor_schedule = floor_solver.last_schedule()
        if floor_schedule is None:
            return None
        # Overlay solve (export ON) on a second fresh solver — aligns the price series full-coverage
        # or returns None (never interpolated), and does not touch the reported dispatch hooks.
        overlay_schedule, _warn = MilpDispatchSolver(seed=self._seed).solve_with_export(
            problem,
            self._market_forecast,
            export_limit_kw=None if override is None else override.mec_kw,
            export_limit_basis=None if override is None else override.solver_basis_label,
        )
        if overlay_schedule is None:
            return None  # price series didn't fully cover the horizon (honest skip, no upside)
        # N23: retained so finance's charge/discharge volumes can be re-derived from the SAME
        # dispatch that produced any priced export revenue.
        self._export_overlay_schedule = overlay_schedule
        delta = round(
            max(
                (overlay_schedule.priced_floor_terms_gbp + overlay_schedule.export_revenue_gbp)
                - floor_schedule.priced_floor_terms_gbp,
                0.0,
            ),
            2,
        )
        # MD-F-05 limb (b): the behind-the-meter value the export dispatch GAVE UP. The floor solve
        # maximises the priced-floor objective terms with no export; the overlay maximises those
        # terms PLUS export revenue, so it will sacrifice arbitrage/DUoS value to free energy for
        # the wire. That sacrifice is measured here, once, and is what `_final_result` nets when
        # contracted export revenue enters the priced floor. Clamped at 0 for solver tolerance only.
        cannibalised = round(
            max(
                floor_schedule.priced_floor_terms_gbp - overlay_schedule.priced_floor_terms_gbp, 0.0
            ),
            2,
        )
        fc = self._market_forecast
        starts = [p.timestamp for p in fc.points]
        coverage = (
            f"coverage {min(starts).isoformat()}–{max(starts).isoformat()}" if starts else None
        )
        return ExportUpside(
            delta_gbp=delta,
            export_mwh=overlay_schedule.annual_export_mwh or 0.0,
            price_content_hash=fc.content_hash,
            price_source_ref=fc.source or fc.file_ref,
            coverage_note=coverage,
            cannibalised_floor_gbp=cannibalised,
            export_limit_kw=(
                min(float(chosen.candidate.power_kw), float(override.mec_kw))
                if override is not None
                else None
            ),
            floor_discharge_kwh=floor_schedule.annual_discharge_kwh,
            overlay_discharge_kwh=overlay_schedule.annual_discharge_kwh,
        )

    def _export_feasibility_study(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        solved_and_priced: list[CandidateEvaluation],
        requested_streams: list[MarketStream],
    ) -> str | None:
        """Measure the export overlay on a run that recommends NO system, under an active override.

        ``None`` whenever no override is active — every ordinary run, byte-identically.

        **Why this exists at all.** `_export_upside` runs only on a SELECTED candidate, and
        the whole
        point of the study is the six live sites that select none. Without this the override
        would be
        inert through the engine's own entry point and "with and without the override" would compare
        two identical runs. So the overlay is measured once, on the candidate with the best priced
        floor — the same candidate the no-viable verdict already reports as its evidence — and the
        measurement is stated as a WARNING.

        **It is evidence, never a priced entry.** No £ is written into the no-spec result's
        ``value_streams`` or ``gated_upside``: an upside measured on a candidate that was never
        selected must not be shaped like a stream on a system nobody recommended. The verdict,
        ``selection_status`` and ``not_bankable`` are all untouched — the study can only ever *add*
        a sentence, and it says so.
        """
        override = self._export_override
        if override is None:
            return None
        best = max(solved_and_priced, key=self._floor_value)
        spec = (
            f"{best.candidate.power_kw:,.3f} kW / {best.candidate.energy_kwh:,.3f} kWh "
            f"({best.candidate.duration_hours:,.2f} h)"
        )
        head = (
            "EXPORT FEASIBILITY STUDY MEASUREMENT (evidence on a run that recommends NO system, "
            "never a priced entry): measured on the best-priced-floor candidate confirmed on the "
            f"full series, {spec}. "
        )
        if self._market_forecast is None:
            return (
                head + "NOT MEASURED: the override is active but NO wholesale price series was "
                "supplied to the engine, and the overlay prices export against a supplied series "
                "only — a price is never invented. Supply a sourced half-hourly wholesale series "
                "covering the metered horizon to obtain the measurement."
            )
        upside = self._export_upside(site, assumptions, best, requested_streams)
        if upside is None:
            return (
                head + "NOT MEASURED: the export overlay did not run. Either the floor solve was "
                "not optimal, the dispatch solver is not the real MILP, or the supplied wholesale "
                "series does not FULLY cover the metered horizon (it is never interpolated or "
                "extrapolated). No export volume is reported rather than a partial one."
            )
        self._export_upside_result = upside
        floor_mwh = (upside.floor_discharge_kwh or 0.0) / 1000.0
        overlay_mwh = (upside.overlay_discharge_kwh or 0.0) / 1000.0
        limit_txt = (
            ""
            if upside.export_limit_kw is None
            else f"{upside.export_limit_kw:,.3f} kW export cap "
        )
        return (
            head + f"With export permitted ({limit_txt}= min(rated power, the override's "
            f"{override.mec_kw:,.3f} kW limit)) the overlay dispatch EXPORTED "
            f"{upside.export_mwh:,.1f} MWh/yr and discharged {overlay_mwh:,.1f} MWh/yr behind the "
            f"meter, against {floor_mwh:,.1f} MWh/yr behind the meter on the no-export floor solve "
            f"— total energy out {overlay_mwh + upside.export_mwh:,.1f} vs "
            f"{floor_mwh:,.1f} MWh/yr. "
            f"Incremental value £{upside.delta_gbp:,.2f}/yr; measured cannibalised "
            "behind-the-meter "
            f"value £{upside.cannibalised_floor_gbp:,.2f}/yr. ALL OF THIS IS GATED UPSIDE: not one "
            "pound of it entered the priced floor, P50/P90, the DSCR or either IRR, so the "
            "no-viable-configuration verdict above is measured WITH export permitted and is "
            "unchanged by it. Converting export capability into a bankable figure needs a "
            "contracted route to market (a counterparty and an agreed export tariff) plus customer "
            "consent — a commercial ask, not a modelling change."
        )

    def _no_spec_dispatch_evidence(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        solved_and_priced: list[CandidateEvaluation],
        requested_streams: list[MarketStream],
        selection_reason: str | None = None,
    ) -> NoSpecDispatchEvidence:
        """What this no-spec run MEASURED about dispatch, for the record's own £0 explanation.

        **The defect this closes.** A no-spec record wrote "no dispatch solved - value unavailable"
        on every stream, and on all eight live campaign sites that was false twice over: dispatch
        WAS solved (162 candidates, every one optimal) and, on six of them, it also RAN and produced
        value. What did not happen is SELECTION. A reader was told the value was unavailable when it
        was measured, which is the same null-versus-measured-zero confusion D45 exists to prevent,
        one layer up.

        **Why one candidate is enough.** The representative is the BEST-PRICED-FLOOR candidate,
        which is the candidate the no-viable verdict already cites as its evidence and the same one
        `_export_feasibility_study` measures, so a record can never quote two different candidates
        at a reader. For the dispatched/not-dispatched question that is sound in the direction it is
        used: a candidate that discharged proves dispatch ran. For the zero case the note's
        arithmetic is stated per kWh and is therefore SIZE-INDEPENDENT, so a single measurement
        settles the whole search box rather than merely one point on it.

        Re-solved on a FRESH `MilpDispatchSolver` - the same idiom, and for the same reason, as
        `_export_upside`: the retained dispatch and bill hooks must keep the real solve. Never
        raises and never blocks a verdict: any failure degrades to evidence with no schedule, which
        the note renders as an honest "could not be re-measured" rather than a claim.
        """
        count = len(solved_and_priced)
        if count == 0 or not isinstance(self._dispatch_solver, MilpDispatchSolver):
            return NoSpecDispatchEvidence(candidates_priced=count)
        best = max(solved_and_priced, key=self._floor_value)
        spec = (
            f"{best.candidate.power_kw:,.3f} kW / {best.candidate.energy_kwh:,.3f} kWh "
            f"({best.candidate.duration_hours:,.2f} h)"
        )
        try:
            stream_ids = requested_streams or self._registry.stream_ids()
            solver = MilpDispatchSolver(seed=self._seed)
            result = solver.solve(
                DispatchProblem(
                    site_model=site,
                    assumptions=assumptions,
                    power_kw=best.candidate.power_kw,
                    energy_kwh=best.candidate.energy_kwh,
                    duration_hours=best.candidate.duration_hours,
                    requested_streams=list(stream_ids),
                    constraints=dispatch_constraints_for(
                        stream_ids,
                        site,
                        assumptions,
                        registry=self._registry,
                        candidate_power_kw=best.candidate.power_kw,  # D61
                    ),
                    scenario=None,
                )
            )
            schedule = (
                solver.last_schedule() if result.solver_status == SolverStatus.OPTIMAL else None
            )
        except Exception:  # pragma: no cover - a measurement must never break a verdict
            schedule = None
        # D56/reporting (Olly, 2026-08-18): RETAIN the best candidate's schedule so the pack can
        # draw the dispatch day. `DispatchProfile` and its renderer (`charts.firming_diagram`, via
        # `consulting_report._dispatch_exhibits`) have existed since Wave 5, but on a no-spec run
        # `last_dispatch_profile()` returned None because `_dispatch_schedule` is only set on the
        # CHOSEN path - so every campaign pack showed the dispatch exhibit as unavailable while the
        # dispatch had in fact been solved, run, and measured here. That is the same
        # measured-versus-unavailable confusion this method exists to close, one exhibit further on.
        #
        # Safe by construction on this path only: the docstring's warning that "the retained
        # dispatch and bill hooks must keep the real solve" protects the CHOSEN candidate's
        # schedule, and on the no-spec path there is no chosen candidate, so `_dispatch_schedule`
        # is None and there is nothing to overwrite. Guarded anyway, so a future caller that reaches
        # here with a real solve retained cannot lose it.
        #
        # The profile is the BEST-PRICED-FLOOR candidate's - the same candidate this method's
        # evidence, `_export_feasibility_study` and the pack's "best evaluated candidate" exhibit
        # all quote, so a reader can never be shown two different candidates. It is NOT a
        # recommendation, and the pack labels it as a candidate measurement.
        if schedule is not None and self._dispatch_schedule is None:
            self._dispatch_schedule = schedule
        return NoSpecDispatchEvidence(
            candidates_priced=count,
            schedule=schedule,
            spec=spec,
            selection_reason=selection_reason,
        )

    def last_export_upside(self) -> ExportUpside | None:
        """Duck-typed hook (D13 pattern): the selected spec's measured wholesale export upside.

        ``None`` when the last solve ran no export overlay (no coherent consents, no supplied price
        series, no MILP, or the series did not cover the horizon). For optional later persistence —
        the upside already rides the result's WHOLESALE_P415 gated-upside entry."""
        return self._export_upside_result

    def _resolve_assumptions(self, request: OptimisationRequest) -> AssumptionLibrary:
        """Use the stashed library when its version matches the request; else load it by version."""
        if (
            self._assumptions is not None
            and self._assumptions.metadata.version == request.assumption_version
        ):
            return self._assumptions
        return load_assumptions(request.assumption_version)

    @staticmethod
    def _sizing_basis(site: SiteModel) -> SizingBasis | None:
        """The site's sizing scale: the DECLARED CONNECTION LIMIT, observed peak as the fallback.

        A battery is sized to the wire, not to the load. This used to return the observed peak
        whenever one existed and ``connection.mic_kw`` only as a fallback — so the two-stage box
        (5–150% of this number) never saw the connection. Measured on the live campaign: a 325 kW
        connection whose metered peak is 22.6 kW had its box capped at 33.9 kW, hiding 302 kW of
        evidenced headroom; and tripling another site's MIC produced an IDENTICAL candidate set,
        because MIC could not reach the box at all.

        Three states, each named on the returned basis and stated on the run:

        * a limit is declared and is at or above the observed peak → anchor on ``min(MIC, ASC)``,
          the same convention `two_stage.connection_power_limit_kw` (N7), `sizing.provisioning`
          and the solver's own charge cap use, so the box and the dispatch that fills it agree;
        * a limit is declared BELOW the observed peak → a contradiction the meter itself refutes
          (`ingestion.missing_data` D5: the observed peak is a FLOOR on MIC). The box is anchored on
          the larger of the two so it can never exclude a size either basis evidences, and the
          declaration is flagged rather than trusted;
        * no limit is declared → the observed peak, labelled, with the connection data ask named.
          An unknown MIC is a derived assumption plus a data ask, never a blocker (D44).

        ``None`` only when neither number exists — there is then no site-derived scale at all and
        nothing is invented.
        """
        peak_raw = site.load_data.max_import_kw
        peak = float(peak_raw) if peak_raw is not None and peak_raw > 0 else None
        limit = connection_power_limit_kw(site)
        if limit is None:
            if peak is None:
                return None
            return SizingBasis(
                capacity_kw=peak,
                basis="observed_peak",
                connection_limit_kw=None,
                observed_peak_kw=peak,
                note=(
                    f"sizing basis = OBSERVED PEAK {peak:,.1f} kW (fallback): no import capacity "
                    "(connection.mic_kw / connection.asc_kw) is declared, so the search box "
                    "(5–150% of the basis) is scaled from the metered load. The box therefore "
                    "stops at 1.5x the site's own peak and cannot see connection headroom that may "
                    "exist — data ask: connection_mic_mec_asc. Per D44 an unknown MIC is a derived "
                    "assumption plus a data ask, never a blocker: supply a labelled assumed "
                    "capacity to widen the box."
                ),
            )
        if peak is not None and peak > limit:
            return SizingBasis(
                capacity_kw=peak,
                basis="connection_limit_below_observed_peak",
                connection_limit_kw=limit,
                observed_peak_kw=peak,
                note=(
                    f"sizing basis = OBSERVED PEAK {peak:,.1f} kW: the declared import capacity "
                    f"min(MIC, ASC) = {limit:,.1f} kW is BELOW the site's own metered peak, which "
                    "the meter itself refutes (the observed peak is a FLOOR on MIC — "
                    "`ingestion.missing_data` D5). The search box (5–150% of the basis) is "
                    "anchored on the larger of the two so it cannot exclude a size either "
                    "basis evidences; "
                    "the declaration is FLAGGED, not trusted. Confirm MIC/ASC with the DNO (data "
                    "ask: connection_mic_mec_asc) — the dispatch's own charge bound still uses the "
                    "declared limit, so this contradiction suppresses charging."
                ),
            )
        peak_txt = f"{peak:,.1f} kW" if peak is not None else "not declared"
        return SizingBasis(
            capacity_kw=limit,
            basis="connection_limit",
            connection_limit_kw=limit,
            observed_peak_kw=peak,
            note=(
                f"sizing basis = DECLARED CONNECTION LIMIT min(MIC, ASC) = {limit:,.1f} kW; the "
                f"site's observed peak is {peak_txt}. The search box spans 5–150% of the basis, "
                "so it is scaled from the physical limit the battery must charge through, not "
                "from the load — anchoring on the load capped the box at 1.5x the peak and hid "
                "evidenced connection headroom (the same limit convention as the provisioning "
                "floor, N7)."
            ),
        )

    def _run_search(
        self,
        request: OptimisationRequest,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        objective: SizingObjective,
        base: float,
    ) -> SizingRun:
        """The sizing search: two-stage continuous by DEFAULT (D32/D34); legacy 3x3 fallback.

        C025 is resolved (gb_v1_3's capex energy component), so the wide-duration
        recommendation is a real economic trade-off and the D34 bake-off certified the
        two-stage method. The search needs the real MILP solver and a readable metered
        series; without either (an injected non-MILP solver in tests, a T0 site with no
        half-hourly file, an unreadable file) — or with ``use_two_stage_search=False`` — it
        falls back to the legacy 3x3 enumeration, whose per-candidate ``INSUFFICIENT_DATA``
        degrade is unchanged — never a fabricated search.
        """
        has_series = bool(site.load_data.half_hourly_file_ref)
        is_milp = isinstance(self._sizing_solver, MilpDispatchSolver)
        if self._use_two_stage and is_milp and has_series:
            try:
                return two_stage_sizing(
                    site,
                    assumptions,
                    base_solver=self._sizing_solver,
                    objective=objective,
                    binding_capacity_kw=base,
                    requested_streams=list(request.requested_streams) or None,
                    registry=self._registry,
                    seed=self._seed,
                    progress=self._progress_sink,
                )
            except HalfHourlyDataError:
                pass  # unreadable series — the legacy grid degrades per candidate, honestly
        return enumerate_sizing(
            site,
            assumptions,
            self._legacy_search_space(base),
            dispatch_solver=self._sizing_solver,
            objective=objective,
            requested_streams=list(request.requested_streams) or None,
            registry=self._registry,
            progress=self._progress_sink,
        )

    @staticmethod
    def _legacy_search_space(base: float) -> SearchSpace:
        """The legacy deterministic power × duration grid scaled to the sizing basis.

        ``base`` is `SizingBasis.capacity_kw` — the declared connection limit where one is
        evidenced, the observed peak otherwise — so the legacy grid and the two-stage box are
        anchored on the same physical quantity."""
        powers = sorted({round(f * base, 3) for f in POWER_FRACTIONS if f * base > 0})
        return SearchSpace(powers, DEFAULT_DURATION_HOURS)

    def _choose(self, run: SizingRun, objective: SizingObjective) -> CandidateEvaluation | None:
        """The search's own selection, else the best indicative (priceable AND viable) candidate.

        ``run.selected`` is now populated on TWO bases (`sizer.SELECTION_BASIS_*`): a
        covenant-feasible optimum, or — since 2026-08-17 — an UNLEVERED selection made when no debt
        structure was resolved and the covenant gate therefore never ran. Both are the search's own
        verdict and both are returned here unchanged; ``run.selection_basis`` is what tells them
        apart, and it rides the persisted grid so a reader is never left to guess.

        The indicative fallback below is gated on `candidate_viable` (2026-07-07): a candidate with
        no positive return metric is surface evidence, never a recommendation — without the gate an
        all-unviable site got the max-floor-£ grid candidate presented as "the selected system".
        ``None`` here means the caller must report a first-class verdict, not a spec.

        N4 (2026-08-10 review): the pool is ranked by ``objective.rank_key`` — the SAME key the
        search itself optimised and the same key `_select` uses on the covenant path — not by
        `_floor_value`. Ranking on priced-floor £ meant the default path (any site without a
        credit assessment has no `DebtTerms`, hence no DSCR, hence no covenant-feasible
        candidate) silently switched to a metric with no capex term, which is monotone-ish in
        size: the IRR-optimal small system the search ranked first lost to the biggest revenue
        earner in the cloud. `rank_key` carries the tie-breaks (smaller power, then smaller
        duration) itself, so the fallback and the covenant path now differ only in the gate.
        """
        if run.selected is not None:
            return run.selected
        # Indicative fallback: never a proxy (typical-period) evaluation — only full-series
        # results may be reported as the chosen size (D32) — and never an unviable candidate.
        priceable = [
            e
            for e in run.candidates_evaluated
            if e.priceable
            and e.optimisation_result.selected_power_kw
            and not e.proxy
            and candidate_viable(e)
        ]
        if not priceable:
            return None
        return max(priceable, key=objective.rank_key)

    @staticmethod
    def _connection_capacity_undeclared(site: SiteModel) -> bool:
        """True when the site declares NO import capacity at all (min(MIC, ASC) unavailable).

        This is the ONE state in which a £0 priced floor is mechanically forced rather than
        economically measured: `milp_solver` resolves an absent connection limit to 0 kW
        (``conn_limit = min(MIC, ASC) if declared else 0.0``), so its
        ``charge_ub = min(power, max(0, limit − load − aux))`` is 0 in every
        half-hour and no candidate can buy a kWh. Its verdict is therefore INSUFFICIENT_DATA — the
        economics were never tested. Every other state gets the economics verdict.
        """
        return connection_power_limit_kw(site) is None

    @staticmethod
    def _capacity_headroom_note(
        site: SiteModel,
        *,
        measured: str | None = None,
        bound: str | None = None,
        best_floor_gbp: float | None = None,
    ) -> str | None:
        """The connection-capacity note for a no-spec run, or ``None`` when there is nothing to say.

        Two states, and the distinction between them is the whole point:

        * **No limit declared at all** — the dispatch model had no limit to charge through, so the
          £0 floors are an artifact of the missing datum. A DATA verdict (see
          `_connection_capacity_undeclared`), and the caller returns INSUFFICIENT_DATA.
        * **The declared limit EQUALS the observed peak** (within
          ``_PLACEHOLDER_MIC_TOLERANCE``) — the signature of the metered peak having been copied
          into MIC as a stand-in. `ingestion.missing_data` refuses to auto-fill MIC for exactly this
          reason: MIC is a DNO fact, never derivable from the meter. This stays a DETECTOR and a
          DATA ASK; it is **not** a verdict and it is **not** evidence about headroom.

        The second branch used to assert that the signature "leaves no charge headroom for a
        battery to import through — so every candidate necessarily priced at or near zero", and to
        return INSUFFICIENT_DATA on that basis. The engine's own run record refuted it on all three
        live sites carrying the signature (121,777 kWh/yr charged over 2,181 half-hours at full
        rated power, median measured headroom 118.7 kW, priced floors up to £18,510/yr). It never
        measured anything: the claim was inferred from a metadata coincidence. So the note now
        carries the MEASUREMENTS the caller gathered — ``measured`` (headroom from the site's own
        half-hourly series, `_measured_charge_headroom`), ``bound`` (whether the connection charge
        limit actually bound, `_connection_charge_limit_bound`) and ``best_floor_gbp`` (the run's
        own best priced floor) — and says only what they support. Any of them absent is stated as
        absent, never inferred. A false causal sentence in a warning has already misled one session;
        the fix is that the sentence cannot be written without the numbers next to it.
        """
        peak = site.load_data.max_import_kw
        limit = connection_power_limit_kw(site)
        if limit is None:
            return (
                "connection capacity unknown: no import capacity (connection.mic_kw / "
                "connection.asc_kw) is declared. The dispatch model therefore had NO connection "
                "limit to charge through — an absent limit resolves to 0 kW and the MILP's charge "
                "bound min(power, limit − load − aux) is 0 in every half-hour — so every candidate "
                "priced at or near zero MECHANICALLY and the economics of a battery at this site "
                "were never tested. This is a DATA verdict, not an economics one (data ask: "
                "connection_mic_mec_asc). Per D44 an unknown MIC is a derived assumption plus a "
                "data ask, never a blocker: a labelled assumed capacity can be supplied to obtain "
                "an explicitly indicative result."
            )
        if peak is None or peak <= 0:
            return None
        if abs(limit - peak) > _PLACEHOLDER_MIC_TOLERANCE * peak:
            return None
        parts = [
            f"connection capacity unknown (PLACEHOLDER-MIC DETECTOR, a DATA ASK — not the reason "
            f"for the verdict above): the declared import capacity min(MIC, ASC) = {limit:,.1f} kW "
            f"equals the site's own observed peak {peak:,.1f} kW to within "
            f"{_PLACEHOLDER_MIC_TOLERANCE:.0%}, which is the signature of the metered peak "
            "standing in for the DNO's figure — `ingestion.missing_data` refuses to auto-fill MIC "
            "for exactly this reason. Confirm MIC/ASC with the DNO (ask: connection_mic_mec_asc).",
            measured
            or "MEASURED charge headroom is UNAVAILABLE here (the half-hourly series could not be "
            "re-read for this note), so no claim is made about it — in particular this note does "
            "NOT claim the connection left no room to charge.",
        ]
        if bound is not None:
            parts.append(bound)
        if best_floor_gbp is not None:
            parts.append(
                f"The best candidate confirmed on the full series priced a floor of "
                f"£{best_floor_gbp:,.2f}/yr, so the dispatch demonstrably charged and discharged "
                "against this connection. Confirming the real capacity would move these numbers; "
                "the verdict above was reached with the headroom stated here, not with none."
            )
        return " ".join(parts)

    def _measured_charge_headroom(self, site: SiteModel, limit_kw: float) -> str | None:
        """Measured ``limit − metered load`` statistics from the site's OWN half-hourly series.

        ``None`` when the series cannot be read (no file ref, an injected non-MILP sizing solver,
        an unreadable file) — the caller then states that headroom was not measured rather than
        inferring it. Deliberately reports no threshold-based figure (a "% of half-hours above
        50 kW" would need an invented threshold): min/median/mean/max, the share of half-hours with
        ANY headroom, and the annualised charge-energy envelope ``Σ max(0, headroom)·Δt``, which is
        a pure measurement. Excludes the parasitic auxiliary draw (candidate-dependent); says so.
        """
        ref = site.load_data.half_hourly_file_ref
        if not ref or not isinstance(self._sizing_solver, MilpDispatchSolver):
            return None
        try:
            # Same series accessor the search itself uses (`two_stage_sizing` calls it the same
            # way); a diagnostic note must never break a verdict, hence the broad guard.
            load, _timestamps, interval_min, _warn = self._sizing_solver._load_series(ref)
        except Exception:  # noqa: BLE001 — a measurement for a warning, never load-bearing
            return None
        if load.size == 0:
            return None
        headroom = limit_kw - np.asarray(load, dtype=float)
        dt = interval_min / 60.0
        positive = int((headroom > 0.0).sum())
        n = int(headroom.size)
        envelope_kwh = float(np.clip(headroom, 0.0, None).sum()) * dt * (8760.0 / (n * dt))
        return (
            f"MEASURED charge headroom (declared limit − metered load, before the parasitic "
            f"auxiliary draw) over the {n:,} half-hours priced: min "
            f"{float(headroom.min()):,.1f} kW, "
            f"median {float(np.median(headroom)):,.1f} kW, mean {float(headroom.mean()):,.1f} kW, "
            f"max {float(headroom.max()):,.1f} kW; positive in {positive:,} of {n:,} half-hours "
            f"({positive / n:.1%}), an annualised charge-energy envelope of "
            f"{envelope_kwh:,.0f} kWh/yr."
        )

    @staticmethod
    def _connection_charge_limit_bound(run: SizingRun | None) -> str | None:
        """Whether the connection charge limit ACTUALLY bound in any full-series candidate.

        The direct measured refutation of "there is no room to charge": the solver reports
        ``connection_charge_limit`` in `constraints_binding` when charge is pushed up to the
        load-reduced connection headroom (`schedule.binding_constraints`). ``None`` when no
        full-series candidate is available to read it from.
        """
        if run is None:
            return None
        confirmed = [e for e in run.candidates_evaluated if not e.proxy]
        if not confirmed:
            return None
        hits = sum(
            1
            for e in confirmed
            if "connection_charge_limit" in e.optimisation_result.constraints_binding
        )
        if hits:
            return (
                f"The connection charge limit BOUND in {hits} of {len(confirmed)} candidate(s) "
                "confirmed on the full series, so more capacity would buy more charge energy here."
            )
        return (
            f"The connection charge limit did NOT bind in any of the {len(confirmed)} candidate(s) "
            "confirmed on the full series: the dispatch was limited by economics or by the "
            "behind-the-meter discharge envelope, not by the connection."
        )

    @staticmethod
    def _floor_value(evaluation: CandidateEvaluation) -> float:
        """NET priced-floor (firm/contracted) value of a candidate.

        N4 (2026-08-10 review): this is NO LONGER the selection metric — `_choose` ranks on
        `objective.rank_key`. It is retained as the engine's half of the priced-floor parity
        contract with `sizing.evaluator.priced_floor_value_gbp` (one formula, two call sites,
        pinned by a parity test), which reporting and the floor/objective reconciliation read.

        N5 (2026-08-10 review): revenue MINUS ``annual_cost_gbp``. The signed dispatch components
        put their negative part on the cost field (`valuation._split_signed`), and finance nets it
        in EBITDA; ranking on gross revenue therefore credited each candidate with charge energy it
        never paid for — an error that grows with size, so it inverted the ranking between viable
        candidates. Kept identical to `sizing.evaluator.priced_floor_value_gbp` by a parity test.
        """
        return round(
            sum(
                v.annual_revenue_gbp - v.annual_cost_gbp
                for v in evaluation.optimisation_result.value_streams
                if v.firmness in _PRICED_FIRMNESS
            ),
            2,
        )

    def _candidate_model_disclosures(
        self, solved_and_priced: list[CandidateEvaluation]
    ) -> list[str]:
        """The dispatch model's own disclosures, carried onto a record that reports no candidate.

        **The defect this closes.** `MilpDispatchSolver._solve_milp` states, on every solve, what
        the half-hourly model did and did not represent: the usable SoC window it applied, the
        unity power factor it assumed, the marginal cycle cost it charged in the objective
        (DEG-01), the parasitic draw it took off the charge headroom, the warranty point it
        extrapolated from, the availability commitments it did NOT compile, the achieved MIP gap,
        and the tariff lines that were absent rather than zero. On a SOLVED run every one of them
        reaches the record: `sizing.sizer._to_optimisation_result` copies
        ``DispatchResult.warnings`` onto the candidate's `OptimisationResult`, and `_final_result`
        carries the chosen candidate's warnings onto the reported result. A no-spec record reports
        no candidate, so until now it carried NONE of them — and all six live campaign sites
        return ``no_viable_configuration``, i.e. the disclosures were absent from 100% of real
        runs. The verdict "no candidate is economically viable" is a claim about a priced floor,
        and a reader cannot weigh that floor without the physics basis it was priced on.

        80e12f1 closed the tariff-absence limb of this by RE-DERIVING the D45 lines beside the
        before-only bill (`_before_only_customer_bill`). This closes the rest by CARRYING the
        solver's own statements rather than restating them: a re-derivation can drift from what
        the solve actually did, and the disclosures that matter most here (the SoC window, the
        cycle cost in the objective) are properties of the solve, not of the site.

        **One candidate, not a union.** Most of these sentences embed spec-specific numbers, so a
        union over the ~24 confirmed candidates would put N near-identical variants of each on the
        record and bury the statement in its own repetitions. The candidate carried is the one the
        verdict already reports as its evidence: the best priced floor confirmed on the full
        series, the same choice `_export_feasibility_study` makes. Ties are broken on
        ``(floor, power, duration)`` — a total order over the de-duplicated search space — so the
        selection cannot depend on iteration order and two identical runs carry identical text.

        Verbatim, never reworded: the disclosure is the solver's sentence, and a re-worded copy is
        a second, silently-divergent account of the same fact (`quotes.disclosures` keeps the same
        rule). ``[]`` when no candidate was confirmed on the full series — there is then no solve
        to disclose, and inventing a block for one would be noise, not honesty.
        """
        if not solved_and_priced:
            return []
        best = max(
            solved_and_priced,
            key=lambda e: (
                self._floor_value(e),
                e.candidate.power_kw,
                e.candidate.duration_hours,
            ),
        )
        spec = (
            f"{best.candidate.power_kw:,.3f} kW / {best.candidate.energy_kwh:,.3f} kWh "
            f"({best.candidate.duration_hours:,.2f} h)"
        )
        header = (
            "DISPATCH MODEL DISCLOSURES OF THE CANDIDATE SOLVE THIS VERDICT RESTS ON — "
            f"{spec}, the best priced floor (£{self._floor_value(best):,.2f}/yr) among the "
            f"{len(solved_and_priced)} candidate(s) confirmed on the full metered series. The "
            "statements that follow are that solve's own disclosures, carried verbatim: they "
            "state what the half-hourly dispatch model did and did not represent, and they "
            "qualify the priced floor this verdict is measured against. Every candidate was "
            "solved against the same assumption release, so each statement applies to the search "
            "as a whole; the FIGURES inside them are this candidate's."
        )
        return [header, *best.optimisation_result.warnings]

    @staticmethod
    def _aggregate_status(run: SizingRun) -> SolverStatus:
        """The honest no-spec status (infeasible if any candidate was, else insufficient_data)."""
        statuses = {e.optimisation_result.solver_status for e in run.candidates_evaluated}
        if SolverStatus.INFEASIBLE in statuses:
            return SolverStatus.INFEASIBLE
        return SolverStatus.INSUFFICIENT_DATA

    # --- result assembly ----------------------------------------------------

    def _final_result(
        self, request: OptimisationRequest, chosen: CandidateEvaluation
    ) -> OptimisationResult:
        """Re-stamp the selected candidate's result to echo the request + engine provenance.

        When the consent-gated export overlay ran (issue 018 / D43), the WHOLESALE_P415
        gated-upside entry is swapped for the delta-bearing one, and when its agreed tariff enters
        the priced floor the dispatch-derived floor components are re-based onto the OVERLAY
        dispatch (MD-F-05 limb (b), `_rebase_dispatch_floor_streams`). Absent an overlay the value
        streams are byte-identical to the candidate's (the pre-018 path); the sizing-basis and
        required-connection-capacity sentences are appended to ``warnings`` on every run, which
        changes no £."""
        extra_warnings = [
            note
            for note in (
                self._sizing_basis_used.note if self._sizing_basis_used is not None else None,
                self._required_capacity.note if self._required_capacity is not None else None,
                self._override_note(request.site_model),
            )
            if note
        ]
        # D46: on a study basis the banner leads, and the limb (b) check follows the result's own
        # warnings. Requirement: a study-basis figure must be structurally hard to mistake for an
        # underwriting one, so the FIRST thing any reader of ``warnings`` sees is which basis
        # produced the numbers below it. Both are empty on an underwriting run.
        study_head = [self._study_basis.note()] if self._study_basis is not None else []
        study_tail = [self._study_netting_check] if self._study_netting_check else []
        update: dict[str, object] = {
            "result_id": f"res_{request.request_id}",
            "request_id": request.request_id,
            "selection_status": SelectionStatus.SELECTED,
            "solver_metadata": self._metadata(request),
            "warnings": [
                *study_head,
                *chosen.optimisation_result.warnings,
                *extra_warnings,
                *study_tail,
            ],
        }
        eu = self._export_upside_result
        if eu is not None:
            wholesale_base = next(
                (
                    v
                    for v in chosen.optimisation_result.gated_upside
                    if v.stream == MarketStream.WHOLESALE_P415
                ),
                None,
            )
            override_note = self._override_note(request.site_model)
            update["gated_upside"] = [
                self._label_override(
                    export_upside_stream_result(
                        v,
                        delta_gbp=eu.delta_gbp,
                        export_mwh=eu.export_mwh,
                        price_content_hash=eu.price_content_hash,
                        price_source_ref=eu.price_source_ref,
                        coverage_note=eu.coverage_note,
                    ),
                    override_note,
                )
                if v.stream == MarketStream.WHOLESALE_P415
                else v
                for v in chosen.optimisation_result.gated_upside
            ]
            # Contracted export-tariff mode (D43): with explicit consent to WHOLESALE_P415 AND a
            # declared export tariff, the agreed tariff × overlay export MWh is a CONTRACTED floor
            # value finance prices — moved into value_streams. The MID delta above stays indicative
            # gated upside, never contracted. Absent consent / tariff this is byte-identical.
            site = request.site_model
            tariff = self._export_floor_tariff(site, chosen)
            # N23: priced only when finance's dispatch volumes were actually re-derived from the
            # overlay schedule. Otherwise the revenue would ride a charge volume that never
            # bought it — the contracted entry is held out instead (the honest degrade: the MID
            # delta still reports as gated upside).
            if tariff is not None and wholesale_base is not None:
                if not self._export_volumes_reconciled:
                    self._forecast_floor_note = (
                        "The agreed export tariff was NOT priced into the floor (N23): the export "
                        "overlay's own dispatch volumes could not be recovered, so pricing its "
                        "export revenue would charge finance the no-export solve's import — "
                        "understating levies and overstating the price-to-target's delivered "
                        "energy. The Elexon-MID delta is still reported as gated upside."
                    )
                    return chosen.optimisation_result.model_copy(update=update)
                contracted = wholesale_contracted_stream_result(
                    wholesale_base,
                    agreed_tariff_gbp_per_kwh=tariff,
                    export_mwh=eu.export_mwh,
                    price_content_hash=eu.price_content_hash,
                    price_source_ref=eu.price_source_ref,
                    coverage_note=eu.coverage_note,
                )
                # MD-F-05 limb (b): the base streams are the NO-EXPORT solve's. Appending gross
                # export revenue to them reports "full no-export arbitrage + full export volume" —
                # physically impossible, because the exported kWh were charged instead of being
                # arbitraged behind the meter. Re-base the dispatch-derived components onto the
                # overlay dispatch that actually produced the export.
                rebased, rebase_note = self._rebase_dispatch_floor_streams(
                    chosen.optimisation_result.value_streams,
                    self._export_overlay_schedule,
                    cannibalised_gbp=eu.cannibalised_floor_gbp,
                )
                # ...and say so ON the export entry, whose own builder appends a (now false)
                # "still carries the FULL no-export arbitrage" sentence precisely BECAUSE this
                # engine correctly declines to net there as well.
                update["value_streams"] = [
                    *rebased,
                    self._restate_export_netting_site(
                        contracted,
                        cannibalised_gbp=eu.cannibalised_floor_gbp,
                        rebased=rebase_note is not None,
                    ),
                ]
                if rebase_note:
                    update["warnings"] = [
                        *study_head,
                        *chosen.optimisation_result.warnings,
                        *extra_warnings,
                        rebase_note,
                        *study_tail,
                    ]
                # N22: the CLI's `--forecast` help said "never priced into the floor" and this is
                # the line that prices it into the floor. Recording the fact here — where it is
                # measured — lets the quote say so, instead of the reader being left with the
                # flag's contract text. Read via the duck-typed hook below and merged into
                # QuoteRecord.warnings, which is NOT part of `input_hash`, so quote_id is unmoved.
                self._forecast_floor_note = (
                    "The supplied wholesale forecast CHANGED A PRICED-FLOOR FIGURE: the site "
                    "consents to WHOLESALE_P415 and declares an agreed export tariff of "
                    f"£{tariff:,.4f}/kWh, so £{contracted.annual_revenue_gbp:,.2f}/yr "
                    f"({eu.export_mwh:,.1f} MWh exported, measured by the forecast-driven export "
                    "overlay) is priced into the floor as CONTRACTED and is summed into P50/P90, "
                    "the DSCR and both IRRs. Only the Elexon-MID delta stays gated upside. "
                    "Without the forecast there is no measured export volume and this floor entry "
                    "does not exist — the forecast is a necessary condition for it. "
                    "Finance's charge/discharge volumes are taken from the SAME overlay dispatch "
                    "(N23), so the extra import this export required is levied on the cost side."
                )
        return chosen.optimisation_result.model_copy(update=update)

    @staticmethod
    def _rebase_dispatch_floor_streams(
        streams: list[ValueStreamResult],
        overlay: HalfHourlyDispatchSchedule | None,
        *,
        cannibalised_gbp: float,
    ) -> tuple[list[ValueStreamResult], str | None]:
        """MD-F-05 limb (b) — THE NETTING CONTRACT for contracted export in the priced floor.

        **The defect.** ``value_streams`` on the chosen candidate come from the NO-EXPORT floor
        solve. When the agreed export tariff is priced into the floor, gross export revenue used to
        be appended to those unchanged components, so the reported floor was "full no-export
        arbitrage + DUoS **and** full export volume" — two mutually exclusive uses of the same
        stored kWh. It goes live and material the moment export is enabled.

        **The contract, stated so the valuation half can be implemented against it.**

        1. Every priced-floor component whose £ is derived from the DISPATCH
           (`_OVERLAY_REBASED_FLOOR_STREAMS`: tariff arbitrage, DUoS red-band avoidance) MUST be
           re-priced from the OVERLAY schedule whenever a CONTRACTED export entry enters
           ``value_streams``. They are re-priced here, signed, through the same
           `valuation._split_signed` convention the floor path uses, so
           ``revenue − cost`` still reproduces the schedule's own signed component exactly and the
           floor still equals the solved objective (`tests/test_floor_equals_objective.py`).
        2. The export entry itself stays **GROSS** of the exported energy's own cost, and that is
           correct rather than an oversight: the overlay's `priced_floor_terms_gbp` already charges
           every charged kWh at energy + DUoS (`export_kw` is a separate variable from
           ``discharge_kw``), and the levies on that extra import ride the overlay charge volume
           finance receives (N23). So ``overlay floor terms + gross contracted export`` is the
           physically consistent total; no second deduction belongs on the export entry, and adding
           one would double-charge the energy.
        3. The measured netting quantity is `ExportUpside.cannibalised_floor_gbp` = no-export floor
           terms − overlay floor terms (≥0). It is reported, not applied twice: it is the *check* on
           step 1, since re-basing the two components moves the floor down by exactly this amount.
        4. **Residual, stated not hidden.** Capacity-market, local-flex and load-service entries
           are NOT re-based: their £ comes from capability or a contract rate rather than from the
           arbitrage the export cannibalises. If an overlay dispatch changes deliverable capability
           or tender-window energy, those entries still reflect the floor solve.
        5. **Exactly ONE netting site.** Netting the export entry INSTEAD (subtracting the
           cannibalised £ from it and leaving the base components alone) reaches the same total and
           is the REJECTED alternative — see `_restate_export_netting_site` for why, and for the
           consequence the two implementations coexisting in one tree would otherwise have. Applying
           both nets the same £ twice.

        ``overlay=None`` (structurally unreachable on the priced path — the contracted entry is
        gated on `_export_volumes_reconciled`, which requires the overlay schedule) returns the
        streams unchanged with no note rather than silently guessing.
        """
        if overlay is None:
            return list(streams), None
        rebased: list[ValueStreamResult] = []
        moved: list[str] = []
        for v in streams:
            attr = _OVERLAY_REBASED_FLOOR_STREAMS.get(v.stream)
            if attr is None or v.firmness not in _PRICED_FIRMNESS:
                rebased.append(v)
                continue
            signed = float(getattr(overlay, attr))
            revenue, cost = _split_signed(signed)
            was = round(v.annual_revenue_gbp - v.annual_cost_gbp, 2)
            moved.append(f"{v.stream.value} £{was:,.2f} -> £{round(revenue - cost, 2):,.2f}")
            rebased.append(
                v.model_copy(
                    update={
                        "annual_revenue_gbp": revenue,
                        "annual_cost_gbp": cost,
                        "warnings": [
                            *v.warnings,
                            "MD-F-05(b): re-priced from the EXPORT OVERLAY dispatch, not the "
                            f"no-export floor solve (was £{was:,.2f}/yr net). Exported kWh were "
                            "charged instead of being arbitraged behind the meter, so carrying "
                            "the no-export figure alongside gross export revenue would report "
                            "the same stored energy twice.",
                        ],
                    }
                )
            )
        if not moved:
            return rebased, None
        return rebased, (
            "MD-F-05(b) EXPORT-FLOOR NETTING APPLIED: the priced floor's dispatch-derived "
            "components are taken from the export OVERLAY dispatch, not the no-export floor solve "
            f"({'; '.join(moved)}). Measured cannibalised behind-the-meter value "
            f"£{cannibalised_gbp:,.2f}/yr. The contracted export entry stays GROSS because the "
            "exported energy's own charge cost is already inside the overlay's floor terms and its "
            "levies ride the overlay charge volume (N23) — netting it again would double-charge. "
            "Residual: capacity-market, local-flex and load-service entries are NOT re-based from "
            "the overlay dispatch."
        )

    @staticmethod
    def _restate_export_netting_site(
        contracted: ValueStreamResult, *, cannibalised_gbp: float, rebased: bool
    ) -> ValueStreamResult:
        """Make the contracted export entry say WHERE MD-F-05 limb (b) was netted — once.

        **Two implementations of limb (b) exist in this tree and they are ALTERNATIVES, not
        complements.** Applying both nets the same £ twice:

        * **this engine** re-prices the dispatch-derived floor components from the overlay schedule
          (`_rebase_dispatch_floor_streams`) and leaves the export entry GROSS;
        * `valuation.wholesale_contracted_stream_result` takes a ``cannibalised_floor_gbp`` and
          subtracts it from the export entry, leaving the base components untouched.

        The totals are algebraically identical — ``overlay terms + gross`` equals ``floor terms +
        (gross − cannibalised)`` — so arithmetic does not pick the winner. Two properties the
        valuation route lacks do:

        1. its subtraction is CLAMPED, ``max(0, gross − cannibalised)``. The overlay is chosen
           against the wholesale FORECAST, while this entry is priced at the AGREED TARIFF, so
           whenever the tariff underprices that forecast ``cannibalised`` exceeds ``gross``, the
           excess is deleted, and the reported floor rises ABOVE the overlay's own objective — the
           per-component-clamp defect class (F-02) this repo has already fixed once and pins with
           `tests/test_floor_equals_objective.py`. Re-basing cannot do this: the negative part
           lands on the cost channel through `valuation._split_signed`, so ``revenue − cost`` still
           reproduces the schedule exactly.
        2. it leaves the per-stream arbitrage and DUoS £ describing the NO-EXPORT dispatch while
           finance's charge/discharge volumes describe the OVERLAY dispatch (N23) — one record
           reporting two different dispatches.

        So the engine deliberately passes no ``cannibalised_floor_gbp``. That is the correct call
        AND it makes the valuation half append a "GROSS — NOT NETTED" sentence asserting that "the
        priced floor still carries the FULL no-export arbitrage and DUoS-avoidance value" — FALSE
        once the components have been re-based, and load-bearing prose rather than decoration. A
        record that states an overstatement and then corrects it further down is the contradiction
        class this review keeps re-raising (C-11), so the stale sentence is REMOVED and replaced
        with the netting site, never annotated underneath. ``rebased=False`` leaves it untouched —
        nothing was re-based, so it is then true.
        """
        if not rebased:
            return contracted
        kept = [w for w in contracted.warnings if _STALE_GROSS_CLAIM_MARKER not in w]
        return contracted.model_copy(
            update={
                "warnings": [
                    *kept,
                    "MD-F-05(b) NETTED ON THE DISPATCH COMPONENTS, NOT ON THIS ENTRY: this "
                    "contracted export revenue is GROSS "
                    f"(£{contracted.annual_revenue_gbp:,.2f}/yr) BY DESIGN, and the priced floor "
                    "is NOT overstated by it. The cannibalised behind-the-meter value "
                    f"(£{cannibalised_gbp:,.2f}/yr measured) has already been taken out of the "
                    "floor's dispatch-derived components, which are re-priced from the export "
                    "OVERLAY dispatch rather than the no-export floor solve, so the floor total "
                    "is 'overlay floor terms + gross export' — netting it here as well would "
                    "deduct the same £ twice. The exported energy's own charge cost is inside the "
                    "overlay's floor terms and its levies ride the overlay charge volume (N23).",
                ]
            }
        )

    def _override_note(self, site: SiteModel) -> str | None:
        """The active export-feasibility override's sentence, or ``None`` when none is active.

        Attached to the warnings of EVERY result this engine returns while an override is in force —
        selected spec or no-spec, overlay run or not. A study assumption a reader can only discover
        by inspecting the engine's constructor arguments is a silent default; this is the channel
        that actually reaches the run record (the sizing-basis and required-capacity sentences ride
        it for the same reason).
        """
        if self._export_override is None:
            return None
        return self._export_override.note(site_consent_coheres=export_dispatch_enabled(site))

    @staticmethod
    def _label_override(entry: ValueStreamResult, override_note: str | None) -> ValueStreamResult:
        """Stamp the override's label onto the gated-upside entry whose £ was measured under it.

        Requirement: the label is visible on every artifact carrying a number derived from the
        override. The result's ``warnings`` carry it for the run as a whole; a per-stream entry can
        be read (and rendered) on its own, so it carries it too.
        """
        if override_note is None:
            return entry
        return entry.model_copy(update={"warnings": [*entry.warnings, override_note]})

    def last_export_feasibility_override(self) -> ExportFeasibilityOverride | None:
        """Duck-typed hook (D13 pattern): the labelled export override this engine is running under.

        Engine config, so it is not per-solve state — ``None`` unless one was supplied. Its sentence
        is ALSO on every result's ``warnings``, so the disclosure cannot be lost if no caller adopts
        this hook.
        """
        return self._export_override

    def last_export_feasibility_study(self) -> str | None:
        """Duck-typed hook (D13 pattern): the no-spec run's export measurement under an override.

        ``None`` when no override was active, when a spec WAS selected (the measurement then rides
        the WHOLESALE_P415 gated-upside entry instead) or when no candidate priced at all.
        """
        return self._export_study_note

    def last_study_basis(self) -> StudyBasis | None:
        """Duck-typed hook (D13 pattern): the named D46 study basis this engine prices on.

        ``None`` on every underwriting run — which is what a consumer must check before presenting
        any figure from the run, because a study-basis figure may never be presented as an
        underwriting output. Engine config, so it is not per-solve state. The basis's own
        sentence is
        ALSO the first entry in every result's ``warnings`` and its suffix is on
        ``SolverMetadata.model_version``, so the disclosure survives a consumer that never adopts
        this hook.
        """
        return self._study_basis

    def last_study_netting_check(self) -> str | None:
        """Duck-typed hook (D13 pattern): the MD-F-05 limb (b) CHECK from a study-basis run.

        ``None`` on an underwriting run, and on a study run that selected no spec (there is no
        selected spec to re-solve). The sentence is also on the result's ``warnings``.
        """
        return self._study_netting_check

    def last_forecast_priced_floor_note(self) -> str | None:
        """Duck-typed hook (D13 pattern): how the supplied forecast moved the priced floor (N22).

        ``None`` when it did not — no forecast, no consent, no agreed export tariff, or no export
        volume — which is the ordinary case and byte-identical to the seam before this hook.
        """
        return self._forecast_floor_note

    def _before_only_customer_bill(
        self, site: SiteModel, assumptions: AssumptionLibrary
    ) -> tuple[CustomerBill | None, list[str]]:
        """The DISPATCH-INVARIANT bill for a run that recommends no system. Never raises.

        ``CustomerBill.annual_bill_before_gbp`` is the site's own metered load against the
        site's tariff stack, whoever's rates it holds (R8), with **no battery in it**. It therefore
        needs a half-hourly series and a tariff, not a viable battery — which is exactly why Gate 3b
        (`ingestion.reconciliation.reconcile_bill_against_invoice`) reads the BEFORE side: the
        check tests the TARIFF STACK, not the dispatch.

        Until this existed the engine emitted a bill only alongside a priced spec, so on every
        no-spec run `last_customer_bill()` was ``None``, Gate 3b returned ``None``, and the one
        gate that would catch "your tariff model is wrong" could not run on precisely the sites
        whose verdict most depends on the tariff model being right — a wrong tariff stack is a
        leading candidate for WHY a site prices as unviable. All six live campaign sites are in
        that state, so the gate was inert on 100% of real runs.

        Returns ``(bill, warnings)``. The bill is **before-only**: every AFTER-side field is
        ``None``, because with no dispatch there is no after-battery bill and a £0 saving would
        render as a computed one (`schemas.customer_bill` enforces the all-or-nothing rule). The
        warnings are the D45 absence declarations the solved path raises from inside `_solve_milp`
        — an absent DUoS band or fixed line still contributes £0 here, so it must be DECLARED on
        the same record, or the £0 reads as a measured rate to the gate that now consumes it.

        ``(None, …)`` — the gate stays honestly un-run rather than compared against a fabricated
        figure — when: the site has no half-hourly series; the series cannot be read; the sizing
        solver is an injected non-MILP double with no series accessor; or the tariff has no
        time-mappable energy rate (the same seam `schedule.customer_bill` keeps, and the same
        refusal `_solve_milp` makes rather than pricing energy at £0 — F-01).
        """
        ref = site.load_data.half_hourly_file_ref
        if not ref or not isinstance(self._sizing_solver, MilpDispatchSolver):
            return None, []
        try:
            # The same series accessor the search itself uses (`_measured_charge_headroom` and
            # `two_stage_sizing` read it the same way). The CLIPPED series is deliberate: it is
            # the documented reporting/billing basis (`_load_series_signed`), so this bill is
            # defined on exactly the array a solved run's `site_load_kw` carries.
            load, timestamps, interval_min, _warn = self._sizing_solver._load_series(ref)
        except Exception as exc:  # noqa: BLE001 — a bill must never break a verdict
            # Recorded, not swallowed: a silently-absent bill is how this defect stayed invisible.
            return None, [
                "BEFORE-BILL NOT COMPUTED: the site's half-hourly series could not be read "
                f"({type(exc).__name__}), so the dispatch-invariant before-bill is absent and "
                "the bill-vs-invoice reconciliation (Gate 3b) is un-run on this record."
            ]
        if load.size == 0:
            return None, [
                "BEFORE-BILL NOT COMPUTED: the half-hourly series has no usable (non-null) "
                "readings, so the bill-vs-invoice reconciliation (Gate 3b) is un-run."
            ]

        warnings: list[str] = []
        windows, windows_note = duos_windows_from_assumptions(
            assumptions, site.connection.dno_region
        )
        if windows_note:
            warnings.append(windows_note)
        energy_price, energy_warn = energy_price_series(
            site.tariff_stack.import_rates,
            timestamps,
            windows,
            site.tariff_stack.import_rate_windows,
        )
        if energy_warn:
            warnings.append(energy_warn)
        # D45: the absence REPORT, never the discarding wrapper — the array stays dense, so an
        # absent band contributes £0 to a bill a reconciliation gate now compares against an
        # invoice. Declaring it is what stops that £0 reading as a measured rate.
        duos, duos_absent = network_rate_series_with_absences(
            site.tariff_stack, timestamps, windows
        )
        duos_note = duos_absence_warning(duos_absent)
        if duos_note:
            warnings.append(duos_note)
        warnings.extend(fixed_line_absence_warnings(site))
        if energy_price is None:
            # F-01's seam, kept: an unknown energy price is not a £0 energy price. No bill.
            warnings.append(
                "BEFORE-BILL NOT COMPUTED: the site's half-hourly energy price could not be "
                f"determined ({energy_warn or 'no usable import tariff'}), so pricing the metered "
                "load would value energy at £0. The bill-vs-invoice reconciliation (Gate 3b) is "
                "un-run rather than compared against a fabricated total."
            )
            return None, warnings

        n = int(load.size)
        dt = interval_min / 60.0
        annual_factor = _HOURS_PER_YEAR / (n * dt)
        zeros = np.zeros(n, dtype=float)
        # A ZERO-DISPATCH schedule: charge = discharge = 0, so `customer_bill`'s net import is the
        # metered load itself and its BEFORE side is byte-identical to the one a solved run of this
        # site produces. Reusing that producer rather than restating the arithmetic is the point —
        # the number Gate 3b checks must be the number the proposal would quote. Its AFTER side is
        # arithmetically the BEFORE side (nothing is fabricated) and is discarded below; the
        # intermediate never escapes this method.
        no_battery = HalfHourlyDispatchSchedule(
            power_kw=0.0,
            energy_kwh=0.0,
            duration_hours=0.0,
            interval_hours=dt,
            annual_factor=annual_factor,
            charge_kw=zeros,
            discharge_kw=zeros,
            soe_kwh=zeros,
            site_load_kw=np.asarray(load, dtype=float),
            duos_rate_gbp_per_kwh=duos,
            red_band_mask=red_band_mask(timestamps, windows),
            energy_price_gbp_per_kwh=energy_price,
        )
        # The fixed battery-invariant lines, resolved exactly as `MilpDispatchSolver.solve` does —
        # `is None`, never `or`, so a declared 0.0 (a FACT) stays silent and an absent rate is the
        # labelled £0 `fixed_line_absence_warnings` above has already declared. The `or` chain on
        # `capacity_kva` is copied deliberately unchanged: it selects BETWEEN two capacity fields
        # rather than substituting a price, and milp_solver flags it as a schema question for the
        # owner of `connection`, not a null-as-zero defect.
        tariff = site.tariff_stack
        standing = (
            0.0
            if tariff.standing_charge_per_day_gbp is None
            else float(tariff.standing_charge_per_day_gbp) * 365.25
        )
        capacity_kva = site.connection.asc_kw or site.connection.mic_kw or 0.0
        capacity = (
            0.0
            if tariff.capacity_charge_gbp_per_kva_day is None
            else float(tariff.capacity_charge_gbp_per_kva_day) * capacity_kva * 365.25
        )
        both = no_battery.customer_bill(
            levy_rate_gbp_per_kwh=levy_rate_gbp_per_kwh(site),
            standing_gbp_per_year=standing,
            capacity_gbp_per_year=capacity,
        )
        if both is None:  # pragma: no cover — `energy_price is None` is handled above
            return None, warnings
        fixed_basis = (
            "Fixed lines are carried (battery-invariant): the standing charge and the "
            "capacity/availability charge (billed on the agreed supply capacity, kVA taken as kW "
            "at unity power factor). "
            if (both.standing_gbp > 0 or both.capacity_gbp > 0)
            else ""
        )
        return (
            CustomerBill(
                annual_bill_before_gbp=both.annual_bill_before_gbp,
                annual_bill_after_gbp=None,
                annual_saving_gbp=None,
                energy_before_gbp=both.energy_before_gbp,
                energy_after_gbp=None,
                duos_before_gbp=both.duos_before_gbp,
                duos_after_gbp=None,
                levies_before_gbp=both.levies_before_gbp,
                levies_after_gbp=None,
                standing_gbp=both.standing_gbp,
                capacity_gbp=both.capacity_gbp,
                import_before_kwh=both.import_before_kwh,
                import_after_kwh=None,
                levy_rate_gbp_per_kwh=both.levy_rate_gbp_per_kwh,
                peak_import_before_kw=both.peak_import_before_kw,
                peak_import_after_kw=None,
                basis=(
                    "BEFORE ONLY — NO SYSTEM IS RECOMMENDED ON THIS RUN, so there is no dispatch "
                    "and no after-battery bill: every AFTER figure and the annual saving are "
                    "ABSENT, never £0. The BEFORE side is dispatch-invariant: the site's "
                    "half-hourly load with no battery in it, priced on the stack the record "
                    # R8 (WP-UW-07 acceptance 5): whose rates these are is the record's statement,
                    # never asserted here. It used to say "priced on its own tariff stack"
                    # unconditionally, over Harwell's "Nothing in this stack is the site's own".
                    f"states; the site is {tariff_provenance_phrase(tariff)}. Modelled lines: "
                    "time-of-use energy (the site's import_rates), DUoS unit rates, and £/kWh levy "
                    f"lines on imported energy. {fixed_basis}NOT modelled (excluded, not zero): "
                    "the TNUoS residual, any levy not supplied as a £/kWh line on the tariff stack"
                    + (
                        ", and the standing/capacity fixed lines (not supplied on this tariff)."
                        if (both.standing_gbp == 0 and both.capacity_gbp == 0)
                        else "."
                    )
                ),
            ),
            warnings,
        )

    def _no_spec_result(
        self,
        request: OptimisationRequest,
        assumptions: AssumptionLibrary | None,
        status: SolverStatus,
        reason: str,
        *,
        selection_status: SelectionStatus | None = None,
        extra_warnings: list[str] | None = None,
        candidate_disclosures: list[str] | None = None,
        no_spec_evidence: NoSpecDispatchEvidence | None = None,
    ) -> OptimisationResult:
        """A contract-valid, no-spec result (honest degrade): gate-only streams, no volumes.

        ``selection_status`` defaults from the solver status (insufficient_data / infeasible) so
        every no-spec result carries an explicit verdict; the all-unviable economics verdict is
        passed explicitly as ``NO_VIABLE_CONFIGURATION`` by the caller.

        ``reason`` is always FIRST in ``warnings`` (callers and tests read it as the verdict);
        ``extra_warnings`` carries the supporting statements — the sizing basis, the measured
        evidence behind the verdict, the connection data ask — as separate entries, so a data ask
        can never be mistaken for the verdict itself.

        An active export-feasibility override is appended here rather than at each call site, so
        every no-spec exit discloses it — including the ones reached before any candidate is
        evaluated, where the override changed nothing and saying so is the honest statement. A
        declared D46 study basis rides the same channel for the same reason, and lands AFTER
        ``reason`` deliberately: the verdict must stay first (callers and tests read ``warnings[0]``
        as the verdict), so the basis banner cannot displace it.

        **The dispatch-invariant BEFORE bill is produced here** (`_before_only_customer_bill`), for
        the same reason the override note is: this is the single funnel every no-spec exit passes
        through, so no verdict can be added later that silently skips it. Without it
        `last_customer_bill()` was ``None`` on every no-spec run, which made Gate 3b
        (bill-vs-invoice) return ``None`` and left the tariff-stack check inert on all six live
        campaign sites — the sites whose verdict most depends on the tariff stack being right.
        ``assumptions is None`` (a refused study basis, an unloadable library) is the one exit with
        no DUoS window scheme to map the tariff onto, so the bill honestly stays ``None`` there.

        ``candidate_disclosures`` (`_candidate_model_disclosures`) carries the DISPATCH MODEL's own
        statements from the candidate solve the verdict rests on. Supplied only by the two exits
        that reached a full-series solve; the purely data-shaped exits leave it ``None``, because
        no dispatch ran there and a disclosure block about a solve that did not happen is noise.
        Spliced AFTER ``bill_warnings`` so ``warnings[0]`` stays the verdict (callers and tests
        read it as such) and the override note stays last, and de-duplicated against everything
        already on the record: 80e12f1's before-bill limb re-derives four of the same D45 lines,
        and saying the same thing twice on one record devalues both copies. The de-duplication
        keeps input order and uses the set for membership only, never for ordering, so the result
        is byte-stable across runs (``warnings`` is inside ``input_hash``).
        """
        override_note = self._override_note(request.site_model)
        study_note = self._study_basis.note() if self._study_basis is not None else None
        if selection_status is None:
            selection_status = (
                SelectionStatus.INFEASIBLE
                if status == SolverStatus.INFEASIBLE
                else SelectionStatus.INSUFFICIENT_DATA
            )
        bill_warnings: list[str] = []
        if assumptions is not None:
            value_streams, gated_upside, _ = build_value_streams(
                None,
                request.site_model,
                assumptions,
                registry=self._registry,
                no_spec_evidence=no_spec_evidence,
            )
            # Per-solve state, set exactly once per solve: every caller returns this result
            # immediately, and `solve` resets `_customer_bill` to None at the top.
            self._customer_bill, bill_warnings = self._before_only_customer_bill(
                request.site_model, assumptions
            )
        else:
            value_streams, gated_upside = [], []
        # Order-preserving de-duplication against the statements already on this record. `seen` is
        # a membership test only; the OUTPUT order is the solver's own, which is deterministic.
        prior = [
            reason,
            *([study_note] if study_note is not None else []),
            *(extra_warnings or []),
            *bill_warnings,
        ]
        seen = set(prior)
        carried: list[str] = []
        for note in candidate_disclosures or []:
            if note in seen:
                continue
            seen.add(note)
            carried.append(note)
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            selected_power_kw=None,
            selected_energy_kwh=None,
            duration_hours=None,
            dispatch_schedule_ref=None,
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=[],
            solver_status=status,
            selection_status=selection_status,
            solver_metadata=self._metadata(request),
            warnings=[
                reason,
                *([study_note] if study_note is not None else []),
                *(extra_warnings or []),
                # D45 parity with the solved path: the tariff-line absences `_solve_milp` raises
                # from inside a candidate solve never reach a no-spec record, because no candidate
                # solve is reported on one. The before-only bill is now compared against an
                # invoice, so an absent DUoS band or fixed line contributing £0 to it must be
                # declared on the same record or that £0 reads as a measured rate.
                *bill_warnings,
                # The rest of the same defect: every OTHER disclosure the candidate solves raised
                # (SoC window, power factor, cycle cost in the objective, parasitic draw, warranty
                # basis, uncompiled commitments, MIP gap, load-series notes) was dropped on a
                # no-spec record. Carried, not re-derived — see `_candidate_model_disclosures`.
                *carried,
                *([override_note] if override_note is not None else []),
            ],
        )

    def _metadata(self, request: OptimisationRequest) -> SolverMetadata:
        return SolverMetadata(
            seed=self._seed,
            assumption_version=request.assumption_version,
            code_version=get_code_version(),
            model_version=self._dispatch_solver.MODEL_VERSION,
        )
