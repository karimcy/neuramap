"""The opt-in **demo** path — the real engine on a clearly-labelled illustrative dataset.

The demo runs the *real* MILP dispatch solver and the *real* `gridq.sizing.enumerate_sizing` outer
loop over a committed **synthetic** 12-month half-hourly load (`examples/demo_site/`), with
**illustrative debt terms injected** so the sizing surface populates a levered-return z-axis, a
P90-DSCR-feasible region and a chosen optimum. Nothing is fabricated: every number is
produced by the deterministic engine/finance from labelled inputs, and the run still comes back
honestly **not bankable** (placeholder assumptions). The illustrative nature — synthetic load +
illustrative debt — is surfaced as a run warning and badged across the UI.

`DemoEngine` implements the `OptimisationEngine` Protocol using the sizing/dispatch API
(`enumerate_sizing`, `SearchSpace`, `SizingObjective`, `MilpDispatchSolver`, `DispatchProblem`,
`dispatch_constraints_for`, `to_finance_dispatch_energy`); it mirrors `MilpEngine`'s composition but
threads `debt_terms` into the sizer — so Olly's `MilpEngine` stays **unmodified**. Keep in sync with
`gridq.optimisation.engine.MilpEngine` if that composition changes.

**`DemoEngine` is not a mock, so "keep in sync" is a correctness requirement, not tidiness.** It
runs the real MILP and stamps `ImplementationLevel.PRODUCTION`, so a rule that lives here in an
older form than in `MilpEngine` is a *production* defect on the app's own path. Two such divergences
were found and closed by DELEGATING to the engine's own implementations rather than re-stating them,
because a second copy of a rule is exactly how the drift happened:

* **the sizing basis** — this module carried the deleted "observed peak, MIC only as a fallback"
  rule (twice), which meant the connection never reached the search box at all. It now calls
  `MilpEngine._sizing_basis`, so the box's UPPER edge is anchored on the declared connection limit
  and (inside `two_stage_sizing`, from `load_relevant_capacity_kw`) its LOWER edge on the site's own
  load-relevant scale. One basis, derived in one place, stated on the run via `SizingBasis.note`;
* **the export-floor netting (MD-F-05 limb (b))** — this module appended GROSS contracted export
  revenue to the unchanged NO-EXPORT value streams, reporting "full no-export arbitrage + full
  export volume". It now calls `MilpEngine._rebase_dispatch_floor_streams` /
  `_restate_export_netting_site` and mirrors the N23 volume reconciliation, so there is **exactly
  ONE netting site** (the dispatch-derived components, re-priced from the overlay schedule) and the
  export entry stays gross. `cannibalised_floor_gbp` is deliberately NOT passed to
  `valuation.wholesale_contracted_stream_result` — that is the rejected ALTERNATIVE implementation,
  and applying both would net the same £ twice.

The two engine statics are private on purpose; calling them is the deliberate trade. The
alternative — a faithful copy in this file — is what produced the drift these two fixes closed, and
it would leave a study-basis change (D46) landing in the engine and not here.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch import (
    DispatchProblem,
    dispatch_constraints_for,
    to_finance_dispatch_energy,
)
from gridq.finance import DebtTerms
from gridq.finance.cashflows import DispatchEnergy
from gridq.ingestion import HalfHourlyDataError, load_site_model_json
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastError,
    load_market_forecast_csv,
)
from gridq.optimisation._assumptions import maybe, numeric
from gridq.optimisation.engine import POWER_FRACTIONS, ExportUpside, MilpEngine, SizingBasis
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.two_stage import two_stage_sizing
from gridq.optimisation.valuation import (
    export_upside_stream_result,
    wholesale_contracted_stream_result,
)
from gridq.schemas import (
    BankableStatus,
    CustomerBill,
    DispatchProfile,
    Firmness,
    ImplementationLevel,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    SiteModel,
    SolveMode,
    SolverStatus,
)
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing
from gridq.sizing.search_space import DEFAULT_DURATION_HOURS
from gridq.sizing.sizer import CandidateEvaluation, ProgressSink, SizingRun, _emit_progress
from gridq.value_stack.gates import agreed_rate, export_dispatch_enabled, stream_consented
from gridq.value_stack.registry import StreamRegistry, default_registry

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = _REPO_ROOT / "examples" / "demo_site"
DEMO_SITE_JSON = DEMO_DIR / "sitemodel.json"
DEMO_HH_CSV = DEMO_DIR / "hh_2025.csv"
#: The demo's illustrative wholesale price series - **real** Elexon MID GB half-hourly prices for
#: 2025 (fetched off the pricing path via ``gridq.dev.fetch_prices``) mapped onto the demo's own
#: half-hourly grid. It feeds ONLY the consent-gated ``WHOLESALE_P415`` export overlay (gated
#: upside), so the priced floor / P50 / P90 / DSCR are untouched; absent ⟹ no export upside.
DEMO_FORECAST_CSV = DEMO_DIR / "wholesale_forecast_2025.csv"

#: Illustrative (NOT underwritten) debt terms for the demo, so the surface's levered-return axis,
#: feasible region and optimum populate. Labelled everywhere; never applied to a real-mode run.
#: Re-tuned 55%→25% gearing when the W8 perfect-foresight adjustment corrected P50 downward —
#: at 25% the demo shows the covenant machinery honestly: candidates are measured against the
#: release's P90-DSCR covenant, some are rejected by it, and a covenant-feasible optimum is
#: selected. (The old "1 passes, 8 are rejected" counts described the retired 9-point legacy grid;
#: the two-stage continuous search evaluates hundreds, so per-candidate counts are not fixed and
#: are read off the persisted surface rather than restated here.)
#: A display parameter, not an underwriting value.
DEMO_DEBT_TERMS = DebtTerms(gearing_pct=25.0, annual_rate_pct=7.5, tenor_years=12)

DEMO_LABEL = (
    "Illustrative demo - synthetic half-hourly load + illustrative (not underwritten) debt terms. "
    "Real engine, real maths, labelled synthetic inputs: not a real site and not bankable."
)

_PRICED_FIRMNESS = frozenset({Firmness.FIRM, Firmness.CONTRACTED})
_FALLBACK_COVENANT = 1.3  # labelled fallback if the library carries no covenant (injects no £/%)

#: Optional, **gitignored** real-site prefill for the real-mode intake form — a real C&I prospect
#: (Robert Thomas Farms, SmartestEnergy account EVY220LUQ) transcribed verbatim from the invoices in
#: ``examples/real/`` (private, never committed, never in the public image). It carries **no
#: half-hourly series** — the metered load is supplied by the visitor via the upload field — and
#: invents nothing (unknown fields are blank → honest data-asks). Absent on a clean checkout ⟹ the
#: example is simply not offered.
HAYWOOD_OAKS_PREFILL_JSON = _REPO_ROOT / "examples" / "real" / "haywood_oaks_prefill.json"


def load_haywood_oaks_prefill(path: Path = HAYWOOD_OAKS_PREFILL_JSON) -> dict[str, str] | None:
    """Return the real-mode form prefill for the Robert Thomas Farms site, or ``None`` when the
    private facts file isn't installed on this host.

    Values are real, transcribed invoice facts (no fabricated load shape, no computed numbers);
    the half-hourly series is the visitor's own upload. Keys mirror the ``real_form.html`` field
    names so they populate the form directly. A parse error degrades to ``None`` (example hidden,
    never a 500).

    The file is customer data under ``examples/real/``, so the DPA guard decides first (WP-GRC-05):
    without valid, verified DPA evidence for the customer it is not read and the example is hidden.
    """
    if not path.exists():
        return None
    from gridq.governance.dpa_guard import DpaEvidenceMissing, check_load

    try:
        check_load(path)
    except DpaEvidenceMissing:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Drop comment/meta keys (leading underscore) and coerce values to strings for the form.
    return {str(k): str(v) for k, v in data.items() if not str(k).startswith("_")}


def load_demo_site() -> SiteModel:
    """Load the committed demo `SiteModel`, resolving its half-hourly ref to the committed CSV."""
    site = load_site_model_json(DEMO_SITE_JSON)
    load = site.load_data.model_copy(update={"half_hourly_file_ref": str(DEMO_HH_CSV)})
    return site.model_copy(update={"load_data": load})


def load_demo_forecast() -> MarketForecast | None:
    """Load the demo's illustrative wholesale price series (real Elexon MID 2025 prices on the demo
    grid), or ``None`` if it isn't present. Feeds only the consent-gated ``WHOLESALE_P415`` export
    overlay - never the priced floor - so a missing file just means no export upside, not an error.
    """
    if not DEMO_FORECAST_CSV.exists():
        return None
    try:
        return load_market_forecast_csv(DEMO_FORECAST_CSV)
    except (MarketForecastError, ValueError, OSError):
        return None


class DemoEngine:
    """Real MILP + sizer over the synthetic load, with illustrative debt injected (labelled)."""

    MODEL_VERSION = MilpDispatchSolver.MODEL_VERSION

    def __init__(
        self,
        *,
        debt_terms: DebtTerms = DEMO_DEBT_TERMS,
        registry: StreamRegistry | None = None,
        seed: int = 0,
        market_forecast: MarketForecast | None = None,
    ) -> None:
        self._registry = registry if registry is not None else default_registry()
        self._seed = seed
        self._solver = MilpDispatchSolver(registry=self._registry, seed=seed)
        self._debt = debt_terms
        # The demo's illustrative wholesale forecast for the consent-gated export overlay (issue
        # 018 / D43) - defaults to the committed real-Elexon-2025 series so the demo showcases the
        # WHOLESALE_P415 export upside. ``None`` ⟹ no overlay (the priced floor is byte-identical).
        self._market_forecast = (
            market_forecast if market_forecast is not None else load_demo_forecast()
        )
        self._assumptions: AssumptionLibrary | None = None
        self._dispatch_energy: DispatchEnergy | None = None
        self._sizing_run: SizingRun | None = None
        self._dispatch_schedule: HalfHourlyDispatchSchedule | None = None
        self._export_upside_result: ExportUpside | None = None
        # MD-F-05 limb (b) + N23, mirroring MilpEngine: the export OVERLAY solve's own schedule and
        # whether finance's dispatch volumes were actually re-derived from it. The CONTRACTED export
        # entry is priced ONLY when the flag is True, so priced export revenue and the charge volume
        # that bought it can never describe two different dispatches.
        self._export_overlay_schedule: HalfHourlyDispatchSchedule | None = None
        self._export_volumes_reconciled: bool = False
        # N22: set only when the export overlay put an agreed-tariff CONTRACTED entry into the
        # priced floor (or was held out of it). Read by `last_forecast_priced_floor_note`.
        self._forecast_floor_note: str | None = None
        # D44: which physical quantity scaled the sizing search box (`MilpEngine._sizing_basis`).
        # STATED on the run and on the result, because a reader of the persisted sizing surface must
        # be able to see why the grid stops where it stops.
        self._sizing_basis_used: SizingBasis | None = None
        # Presentation-only progress side channel (the web run screen), attached via
        # ``set_progress_sink``. Config, not per-solve state; ``None`` is byte-identical to before.
        self._progress_sink: ProgressSink | None = None

    def set_progress_sink(self, sink: ProgressSink | None) -> None:
        """Attach a progress side channel (additive; C035) — mirrors `MilpEngine.set_progress_sink`.

        Discovered duck-typed by `generate_quote`; every emit is guarded so the sink can never
        perturb the deterministic solve. ``None`` disables it (the default).
        """
        self._progress_sink = sink

    def build_optimisation_request(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario_set: str | None = None,
    ) -> OptimisationRequest:
        self._assumptions = assumptions
        return OptimisationRequest(
            request_id=f"req_{site_model.site_id}",
            site_model=site_model,
            assumption_version=assumptions.metadata.version,
            scenario_set_id=scenario_set,
            requested_streams=list(MarketStream),
            solve_mode=SolveMode.PRODUCTION,
        )

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        self._dispatch_energy = None
        self._sizing_run = None
        self._dispatch_schedule = None
        self._export_upside_result = None
        self._export_overlay_schedule = None
        self._export_volumes_reconciled = False
        self._forecast_floor_note = None
        self._sizing_basis_used = None
        assumptions = self._resolve_assumptions(request)
        site = request.site_model
        # The sizing basis, from the engine's OWN derivation (`MilpEngine._sizing_basis`) rather
        # than a copy of it: the DECLARED CONNECTION LIMIT min(MIC, ASC) where evidenced, the
        # observed peak only as the fallback. This module previously carried the deleted
        # "peak-or-MIC" rule, under which MIC never reached the search box at all — measured on the
        # live campaign, a 325 kW connection with a 22.6 kW metered peak had its box capped at
        # 33.9 kW. ``None`` only when the site evidences NEITHER quantity, which is a real
        # insufficient-data verdict and never an `or 0.0` (D45: a null must never read as a number).
        basis = MilpEngine._sizing_basis(site)
        if basis is None:
            return self._no_spec(
                request,
                SolverStatus.INSUFFICIENT_DATA,
                "no sizing basis: the site has no connection limit (connection.mic_kw / "
                "connection.asc_kw) and no observed peak (load_data.max_import_kw) to scale a "
                "candidate battery from.",
            )
        self._sizing_basis_used = basis
        base = basis.capacity_kw
        covenant = (
            numeric(maybe(assumptions, "dscr_covenants", "min_p90_dscr")) or _FALLBACK_COVENANT
        )
        objective = SizingObjective(min_p90_dscr_covenant=covenant)
        # The two-stage continuous search (D32), the default since D34 — C025 is resolved so
        # the customer-visible surface now shows a real economic trade-off (mirrors
        # `MilpEngine._run_search`, with the demo's illustrative debt threaded so the surface
        # carries levered returns where computable). The legacy enumeration stays the honest
        # fallback for an unreadable series.
        #
        # ``binding_capacity_kw`` anchors the TOP of the power axis only. The LOWER edge is derived
        # INSIDE `two_stage_sizing` (`load_relevant_capacity_kw`, which also sees the metered series
        # peak) — deliberately not re-derived here, because raising the basis to the connection also
        # raised the SMALLEST searchable system, and one number cannot serve both edges of the box.
        # Passing the basis and letting the search derive its own floor is what mirrors the
        # TWO-ANCHOR result rather than only the upper edge.
        run: SizingRun | None = None
        if site.load_data.half_hourly_file_ref:
            try:
                run = two_stage_sizing(
                    site,
                    assumptions,
                    base_solver=self._solver,
                    objective=objective,
                    binding_capacity_kw=base,
                    requested_streams=list(request.requested_streams) or None,
                    debt_terms=self._debt,
                    registry=self._registry,
                    seed=self._seed,
                    progress=self._progress_sink,
                )
            except HalfHourlyDataError:
                run = None
        if run is None:
            run = enumerate_sizing(
                site,
                assumptions,
                self._legacy_search_space(basis),
                dispatch_solver=self._solver,
                objective=objective,
                requested_streams=list(request.requested_streams) or None,
                debt_terms=self._debt,
                registry=self._registry,
                progress=self._progress_sink,
            )
        # The search box's anchor is part of the search's own record (mirror MilpEngine): a
        # reader of the persisted surface must see WHY the grid stops where it stops.
        run = replace(run, warnings=[*run.warnings, basis.note])
        self._sizing_run = run
        chosen = self._choose(run)
        if chosen is None or chosen.optimisation_result.selected_power_kw is None:
            return self._no_spec(
                request, self._aggregate_status(run), "no candidate produced a dispatch"
            )
        # One "dispatch" phase for the post-sizing full-year re-dispatch (mirror MilpEngine).
        _emit_progress(self._progress_sink, "dispatch", None, None)
        self._dispatch_energy = self._dispatch_energy_for(
            site, assumptions, chosen, list(request.requested_streams)
        )
        # The chosen spec was re-dispatched last, so the solver holds its full schedule — retain it
        # for the dispatch chart's representative-day projection (mirror MilpEngine; D13 untouched).
        self._dispatch_schedule = self._solver.last_schedule()
        # Consent-gated wholesale EXPORT overlay (issue 018 / D43), mirroring MilpEngine: a no-op
        # unless the demo's consents cohere AND a covering price series is present; it changes ONLY
        # the WHOLESALE_P415 gated-upside entry, never the priced floor.
        self._export_upside_result = self._export_upside(
            site, assumptions, chosen, list(request.requested_streams)
        )
        self._reconcile_export_volumes(site, chosen)
        return self._final_demo_result(request, chosen)

    # duck-typed hooks the quote pipeline reads (mirror MilpEngine)
    def selected_dispatch_energy(self) -> DispatchEnergy | None:
        return self._dispatch_energy

    def last_sizing_run(self) -> SizingRun | None:
        return self._sizing_run

    def last_dispatch_profile(self) -> DispatchProfile | None:
        if self._dispatch_schedule is None:
            return None
        return self._dispatch_schedule.representative_day_profile()

    def last_customer_bill(self) -> CustomerBill | None:
        """The chosen candidate's bill before/after (W7-5, C017) — mirrors MilpEngine's hook,
        so the demo proposal shows the record-backed bill headline instead of the pending seam."""
        return self._solver.last_customer_bill()

    # --- helpers (mirror MilpEngine via public APIs) -----------------------
    def _resolve_assumptions(self, request: OptimisationRequest) -> AssumptionLibrary:
        if (
            self._assumptions is not None
            and self._assumptions.metadata.version == request.assumption_version
        ):
            return self._assumptions
        return load_assumptions(request.assumption_version)

    @staticmethod
    def _legacy_search_space(basis: SizingBasis) -> SearchSpace:
        """The legacy deterministic power × duration grid, scaled to the sizing basis.

        Mirrors `MilpEngine._legacy_search_space`. ``basis.capacity_kw`` is the declared connection
        limit where one is evidenced, the observed peak otherwise — so the legacy grid and the
        two-stage box are anchored on the same physical quantity. This method used to hold a SECOND
        copy of the deleted "observed peak, MIC only as a fallback" rule; the basis is now derived
        once, in `MilpEngine._sizing_basis`, and passed in. Single-anchor is correct here: the
        two-anchor power domain belongs to the continuous search, not to the 3x3 fallback grid.
        """
        base = basis.capacity_kw
        powers = sorted({round(f * base, 3) for f in POWER_FRACTIONS if f * base > 0})
        return SearchSpace(powers, DEFAULT_DURATION_HOURS)

    def last_sizing_basis(self) -> SizingBasis | None:
        """Duck-typed hook mirroring `MilpEngine.last_sizing_basis`: which physical quantity scaled
        the search box on the last ``solve``. ``None`` when no basis was derived (no spec)."""
        return self._sizing_basis_used

    def _choose(self, run: SizingRun) -> CandidateEvaluation | None:
        if run.selected is not None:
            return run.selected
        # Indicative fallback: never a proxy (typical-period) evaluation — only full-series
        # results may be reported as the chosen size (D32; mirrors MilpEngine._choose).
        priceable = [
            e
            for e in run.candidates_evaluated
            if e.priceable and e.optimisation_result.selected_power_kw and not e.proxy
        ]
        if not priceable:
            return None
        return max(
            priceable,
            key=lambda e: (
                round(
                    sum(
                        v.annual_revenue_gbp
                        for v in e.optimisation_result.value_streams
                        if v.firmness in _PRICED_FIRMNESS
                    ),
                    2,
                ),
                -e.candidate.power_kw,
                -e.candidate.duration_hours,
            ),
        )

    @staticmethod
    def _aggregate_status(run: SizingRun) -> SolverStatus:
        statuses = {e.optimisation_result.solver_status for e in run.candidates_evaluated}
        if SolverStatus.INFEASIBLE in statuses:
            return SolverStatus.INFEASIBLE
        return SolverStatus.INSUFFICIENT_DATA

    def _dispatch_energy_for(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> DispatchEnergy:
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
        return to_finance_dispatch_energy(self._solver.solve(problem))

    def last_export_upside(self) -> ExportUpside | None:
        """The selected spec's consent-gated wholesale export upside (issue 018 / D43), or ``None``
        when no overlay ran. Duck-typed hook mirroring `MilpEngine.last_export_upside`; the upside
        also rides the result's WHOLESALE_P415 gated-upside entry."""
        return self._export_upside_result

    def _export_upside(
        self,
        site: SiteModel,
        assumptions: AssumptionLibrary,
        chosen: CandidateEvaluation,
        requested_streams: list[MarketStream],
    ) -> ExportUpside | None:
        """The demo's consent-gated wholesale export upside - a faithful copy of
        `MilpEngine._export_upside` (kept in sync per this module's contract): two extra
        deterministic solves of the SELECTED spec on FRESH solver instances (the retained floor
        dispatch/bill hooks stay untouched), returning the incremental value of enabling export -
        ``max((overlay floor-terms + export revenue) − floor floor-terms, 0)`` - or ``None`` when
        export isn't consented, no price series covers the horizon, or export never pays. Gated
        upside only; wholesale £ never enters the priced floor, P50/P90 or DSCR."""
        if not export_dispatch_enabled(site):
            return None
        if self._market_forecast is None:
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
        floor_solver = MilpDispatchSolver(seed=self._seed)
        floor_result = floor_solver.solve(problem)
        if floor_result.solver_status != SolverStatus.OPTIMAL:
            return None
        floor_schedule = floor_solver.last_schedule()
        if floor_schedule is None:
            return None
        overlay_schedule, _warn = MilpDispatchSolver(seed=self._seed).solve_with_export(
            problem, self._market_forecast
        )
        if overlay_schedule is None:
            return None
        # N23: retained so finance's charge/discharge volumes, and the MD-F-05(b) re-base of the
        # floor's dispatch-derived components, can both come from the SAME dispatch that produced
        # any priced export revenue.
        self._export_overlay_schedule = overlay_schedule
        delta = round(
            max(
                (overlay_schedule.priced_floor_terms_gbp + overlay_schedule.export_revenue_gbp)
                - floor_schedule.priced_floor_terms_gbp,
                0.0,
            ),
            2,
        )
        # MD-F-05 limb (b): the behind-the-meter value the export dispatch GAVE UP. The floor
        # solve maximises the priced-floor objective terms with no export; the overlay
        # maximises those terms PLUS export revenue, so it will sacrifice arbitrage/DUoS
        # value to free energy for the wire. Measured HERE, exactly once — `_final_demo_result`
        # re-bases the floor's dispatch-derived components and this figure is the CHECK on that
        # re-base, never a second deduction. Clamped at 0 for solver tolerance only.
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
            floor_discharge_kwh=floor_schedule.annual_discharge_kwh,
            overlay_discharge_kwh=overlay_schedule.annual_discharge_kwh,
        )

    def _export_floor_tariff(self, site: SiteModel, chosen: CandidateEvaluation) -> float | None:
        """The agreed export tariff that would put CONTRACTED export revenue in the priced floor.

        Mirror of `MilpEngine._export_floor_tariff`. ``None`` when no overlay ran, the site has no
        WHOLESALE_P415 gated entry to re-base, consent is absent, or no export tariff is
        declared. Stated once so `_reconcile_export_volumes` and `_final_demo_result` cannot
        drift apart (N23).

        Note for readers who expect this to be dormant on the demo: it is **not**. The demo site
        consents to WHOLESALE_P415 and, although that consent records ``agreed_rate_gbp: null``,
        `value_stack.gates.agreed_rate` falls back to
        ``ExportArrangement.export_tariff_gbp_per_kwh`` (£0.075/kWh on the demo site), so this
        returns a rate and the contracted floor entry is LIVE on every demo run.
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

    def _reconcile_export_volumes(self, site: SiteModel, chosen: CandidateEvaluation) -> None:
        """N23 (mirror of `MilpEngine._reconcile_export_volumes`): keep finance's dispatch volumes
        and the priced floor on the SAME dispatch.

        `_dispatch_energy_for` re-dispatches the chosen spec on the plain **no-export** problem, but
        when the agreed export tariff prices ``tariff × the OVERLAY's export MWh`` into
        `value_streams` as CONTRACTED, exporting means importing and storing energy the floor
        solve never charged. Sending finance the floor's charge volume would keep that extra net
        import and its levies off the cost side while the revenue they produced entered P50, P90
        and the DSCR.

        This is also what makes the gross export entry sound: MD-F-05(b) leaves it gross *because*
        the exported energy's own charge cost rides the overlay charge volume finance receives.
        Where the overlay schedule is unavailable the flag stays False and `_final_demo_result`
        holds the contracted entry OUT of the floor — the two read the same switch, so priced
        export revenue without its own charge volume is structurally impossible, not just unlikely.
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

    def last_forecast_priced_floor_note(self) -> str | None:
        """Duck-typed hook mirroring `MilpEngine.last_forecast_priced_floor_note` (N22): how the
        supplied forecast moved the priced floor, or why it was held out of it.

        ``None`` when it did not move it — no overlay, no consent, no agreed export tariff, or no
        export volume."""
        return self._forecast_floor_note

    def _final_demo_result(
        self, request: OptimisationRequest, chosen: CandidateEvaluation
    ) -> OptimisationResult:
        """Re-stamp the chosen candidate's result; when the export overlay ran, swap its
        WHOLESALE_P415 gated-upside entry for the delta-bearing one, and when its agreed tariff
        enters the priced floor, re-base the dispatch-derived floor components onto the OVERLAY
        dispatch (MD-F-05 limb (b)) — mirror of `MilpEngine._final_result`. The sizing-basis
        sentence is appended to ``warnings`` on every run, which changes no £."""
        extra_warnings = [
            note
            for note in (
                self._sizing_basis_used.note if self._sizing_basis_used is not None else None,
            )
            if note
        ]
        update: dict[str, object] = {
            "result_id": f"res_{request.request_id}",
            "request_id": request.request_id,
            "warnings": [*chosen.optimisation_result.warnings, *extra_warnings],
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
            update["gated_upside"] = [
                export_upside_stream_result(
                    v,
                    delta_gbp=eu.delta_gbp,
                    export_mwh=eu.export_mwh,
                    price_content_hash=eu.price_content_hash,
                    price_source_ref=eu.price_source_ref,
                    coverage_note=eu.coverage_note,
                )
                if v.stream == MarketStream.WHOLESALE_P415
                else v
                for v in chosen.optimisation_result.gated_upside
            ]
            # Contracted export-tariff mode (D43), mirror of MilpEngine: consent to WHOLESALE_P415
            # + a declared export tariff ⇒ agreed tariff × overlay export MWh is a CONTRACTED floor
            # value. LIVE on the demo (see `_export_floor_tariff`), not the dormant path it
            # reads as.
            site = request.site_model
            tariff = self._export_floor_tariff(site, chosen)
            if tariff is not None and wholesale_base is not None:
                # N23: priced only when finance's dispatch volumes were actually re-derived from the
                # overlay schedule. Otherwise the revenue would ride a charge volume that never
                # bought it — the contracted entry is held out instead (the honest degrade: the MID
                # delta still reports as gated upside).
                if not self._export_volumes_reconciled:
                    self._forecast_floor_note = (
                        "The agreed export tariff was NOT priced into the floor (N23): the export "
                        "overlay's own dispatch volumes could not be recovered, so pricing its "
                        "export revenue would charge finance the no-export solve's import — "
                        "understating levies and overstating the price-to-target's delivered "
                        "energy. The Elexon-MID delta is still reported as gated upside."
                    )
                    return chosen.optimisation_result.model_copy(update=update)
                # NOTE: ``cannibalised_floor_gbp`` is deliberately NOT passed. That parameter is the
                # ALTERNATIVE implementation of MD-F-05 limb (b) — it nets the export entry and
                # leaves the base components alone. This path nets the base components instead, so
                # passing it as well would deduct the same £ twice (`_rebase_dispatch_floor_streams`
                # step 5, `_restate_export_netting_site`).
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
                # arbitraged behind the meter. THE ONE netting site, delegated to the engine's own
                # implementation so the demo and the engine can never disagree about where it is.
                rebased, rebase_note = MilpEngine._rebase_dispatch_floor_streams(
                    chosen.optimisation_result.value_streams,
                    self._export_overlay_schedule,
                    cannibalised_gbp=eu.cannibalised_floor_gbp,
                )
                update["value_streams"] = [
                    *rebased,
                    MilpEngine._restate_export_netting_site(
                        contracted,
                        cannibalised_gbp=eu.cannibalised_floor_gbp,
                        rebased=rebase_note is not None,
                    ),
                ]
                if rebase_note:
                    update["warnings"] = [
                        *chosen.optimisation_result.warnings,
                        *extra_warnings,
                        rebase_note,
                    ]
                # N22: this is the line that prices a forecast-measured figure into the demo's
                # priced floor, so it says so on the record rather than leaving the reader to infer
                # it from the CLI flag's contract text.
                self._forecast_floor_note = (
                    "The wholesale forecast CHANGED A PRICED-FLOOR FIGURE on the demo: the site "
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

    def _no_spec(
        self, request: OptimisationRequest, status: SolverStatus, reason: str
    ) -> OptimisationResult:
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            solver_status=status,
            warnings=[reason],
        )
