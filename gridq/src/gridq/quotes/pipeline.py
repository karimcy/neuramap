"""Quote generation orchestrator — the one-call run that produces a stamped quote.

Chains an (already-assembled, graded) `SiteModel` through an `OptimisationEngine` →
`run_financial_model` → `assemble_quote_record` → `emit_quote_telemetry`, returning a single
`QuoteRunResult`. The engine is dependency-injected (defaults to the integration-only
`BaselineEngine`) so the real MILP slots in behind the same interface unchanged.

File IO (loading the site / HH CSV / forecast) and presentation live in the CLI; this function
takes domain objects and returns domain objects, so it is testable without typer or the disk.
It **degrades honestly**: when finance has no positive sizing to price it raises
`InsufficientInputsError`, which we catch and turn into a `not_bankable` quote carrying the
outstanding data asks, rather than inventing a system.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from gridq.assumptions import AssumptionLibrary
from gridq.engine import BaselineEngine, OptimisationEngine
from gridq.finance.cashflows import CashflowModel, DispatchEnergy
from gridq.finance.financial_model import InsufficientInputsError, run_financial_model
from gridq.governance.dpa_guard import DpaEvidenceMissing, authorise_store, check_load
from gridq.ingestion.hh_mandate import HalfHourlyAdmission, admit_half_hourly
from gridq.ingestion.reconciliation import (
    BillReconciliationResult,
    InvoiceEvidence,
    ReconciliationResult,
    bill_reconciliation_records,
    invoice_evidence_from_mapping,
    reconcile_bill_against_invoice,
    reconcile_declared_against_measured,
    reconciliation_records,
)
from gridq.optimisation.tariff_calendar import (
    import_rate_assumptions,
    net_of_vat_stack,
    resolved_price_basis,
)
from gridq.quotes.assembly import assemble_quote_record
from gridq.quotes.confidence import ConfidenceAssessment, assess_confidence
from gridq.quotes.sensitivities import compute_scenario_sensitivities
from gridq.quotes.telemetry import emit_quote_telemetry
from gridq.schemas import (
    CustomerBill,
    DerivedAssumption,
    DispatchProfile,
    FinancialCase,
    OptimisationResult,
    QuoteMode,
    QuoteRecord,
    ScenarioSensitivity,
    SiteModel,
    SizingGrid,
    TelemetryEvent,
)
from gridq.value_stack.gb_streams import local_flex_gross_label

if TYPE_CHECKING:
    from gridq.finance import DebtTerms
    from gridq.ingestion.hh_data import HalfHourlySummary
    from gridq.scenarios.forecast_reference import ForecastReference
    from gridq.schemas import LenderTerms, LifecycleInputs


@dataclass(frozen=True)
class QuoteRunResult:
    """Everything one quote run produced: the stamped quote, its telemetry, and the detail."""

    quote: QuoteRecord
    telemetry: TelemetryEvent
    site: SiteModel
    result: OptimisationResult
    financial_case: FinancialCase | None
    bankable: bool
    forecast_ref: ForecastReference | None = None
    sizing_grid: SizingGrid | None = None
    dispatch_profile: DispatchProfile | None = None
    customer_bill: CustomerBill | None = None
    #: The evidential-strength band and, more usefully, what is holding it down. Also persisted
    #: on ``quote`` (`confidence_band` / `confidence_binding_factors`); carried here so a caller
    #: can render the full factor list without re-deriving it.
    confidence: ConfidenceAssessment | None = None
    #: Every assumption this run had to stand in for (D44) — the site's own recorded derived inputs
    #: plus whatever the ingestion gates recorded on the way through. The report's "Assumptions
    #: requiring confirmation" section is built from this; each record carries its own report
    #: language (`DerivedAssumption.report_label`), and a derived fact and a pending permission must
    #: never be merged into one sentence. Also stated verbatim in ``quote.warnings``.
    derived_assumptions: tuple[DerivedAssumption, ...] = ()
    #: Gate 3's verdict (declared vs measured), or None when no measured series could be read.
    reconciliation: ReconciliationResult | None = None
    #: Gate 3b's verdict (modelled delivered cost vs the site's own invoices), carried whole.
    #:
    #: Only the gate's `summary()` string reaches ``quote.warnings``, and that string is built from
    #: the NON-``ok`` findings only. So a renderer given the record alone can never show what the
    #: load-reconciliation section shows: the three limbs (volume / annual cost / monthly) each
    #: with their own severity, signed `variance_pct`, modelled and invoiced sides, and message —
    #: and on a cleanly reconciling site it sees nothing from Gate 3b at all. Carried here so the
    #: per-limb detail is available to a caller without re-running the gate.
    #:
    #: **Two different absences, and a reader must not merge them.** ``None`` HERE means the gate
    #: did not run — there was no `CustomerBill` to compare, so nothing was checked. A present
    #: result whose ``reconciles`` is ``None`` means the gate ran and found NO INVOICE here. Both
    #: cap the band and neither is a pass, but they are different facts about different things (our
    #: dispatch produced no bill / the customer has supplied no invoice) and carry different asks.
    bill_reconciliation: BillReconciliationResult | None = None


def _gate_2_warning(admission: HalfHourlyAdmission) -> str:
    """The Gate-2 verdict as one reader-facing line (D44: state it, never refuse on it)."""
    if admission.is_metered:
        return "Half-hourly basis: METERED"
    ceiling = admission.confidence_ceiling.value.upper()
    if admission.basis is not None:
        return (
            f"Half-hourly basis: {admission.basis.value.upper()} — reference profile "
            f"{admission.reference_profile or 'unrecorded'}, scaled to "
            f"{admission.scaled_to or 'unrecorded totals'}. Modelled load is a legitimate input "
            "basis but is evidence about a typical site of this type, not this site, so confidence "
            f"is capped at {ceiling} and the quote cannot advance past `indicative` without a "
            "recorded override."
        )
    if admission.has_series:
        return (
            "Half-hourly basis: NOT DECLARED — a half-hourly series is attached and is priced as "
            "supplied, but nothing records whether it is this site's own metered export or a "
            f"modelled shape. An undeclared basis is not read as metered, so confidence is capped "
            f"at {ceiling} and the quote cannot advance past `indicative` until the provenance is "
            "recorded in `load_data.half_hourly_basis`."
        )
    return (
        "Half-hourly basis: NONE — no half-hourly series is attached, so there was no shape to "
        f"dispatch against and no spec is priced. The run completes and confidence is capped at "
        f"{ceiling}; see the recorded assumption for the reference profile and the totals a shape "
        "would be modelled from. This is an honest empty result, not a refusal."
    )


def _derive_half_hourly_if_asked(
    site: SiteModel, out_dir: Path | None, assumptions: AssumptionLibrary
) -> SiteModel:
    """Opt-in D44 derivation: model the missing series so the run has a shape to dispatch.

    **Off by default, and deliberately so.** Derivation writes a file and rewrites the site (a new
    ``half_hourly_file_ref``, a MODELLED basis and a recorded `DerivedAssumption`), which
    legitimately moves ``input_hash`` — a run on a derived shape is not the same run as one on no
    shape at all. Keeping it opt-in leaves the default pipeline deterministic, offline and free of
    file IO, exactly as design §5 requires ("derivation runs as a separate step so the pipeline
    stays deterministic and offline in CI"). A caller that wants the derived shape names the
    directory to put it in.

    A site that already has a series is never touched: metered data is never displaced. A synthesis
    that cannot proceed without inventing a total is left alone too — Gate 2 then records
    ``not_derivable`` and the run continues honestly, which is the whole point of D44.
    """
    if out_dir is None or site.load_data.half_hourly_file_ref:
        return site
    # The derived series is this site's data, written to disk: the DPA guard decides first
    # (WP-GRC-05). Without a permit for the site nothing is written (`DpaEvidenceMissing`).
    authorise_store(site.site_id)
    # Lazy import: keeps the synthesis machinery (and its calendar/hashing work) off the default
    # quote path entirely, so the common case pays nothing for the opt-in feature.
    from gridq.ingestion.hh_synthesis import SynthesisError, derive_half_hourly

    try:
        return derive_half_hourly(site, out_dir=out_dir, release=assumptions.metadata.version).site
    except SynthesisError:
        # Nothing to scale to. Record-and-continue is the honest outcome, never a guessed total.
        return site


def _measured_summary(
    site: SiteModel, supplied: HalfHourlySummary | None
) -> HalfHourlySummary | None:
    """The measured half-hourly summary Gate 3 compares against, if one can be read.

    A caller that already summarised the series passes it in (the CLI and the web app both hold
    one); otherwise it is read from the site's own file ref. The read is fully guarded: an absent,
    unreadable or malformed file leaves Gate 3 honestly un-run rather than failing the quote — a
    reconciliation gate must never be the reason a run dies (D44).
    """
    if supplied is not None:
        return supplied
    ref = site.load_data.half_hourly_file_ref
    if not ref:
        return None
    try:
        # Lazy import: pandas and the HH loader stay off the quote layer's import path.
        from pathlib import Path as _Path

        from gridq.ingestion.hh_data import load_half_hourly_csv, summarise_half_hourly_data

        if not _Path(ref).exists():
            return None
        frame = load_half_hourly_csv(
            ref, site_id=site.site_id, is_personal_data=site.is_personal_data
        )
        return summarise_half_hourly_data(frame, file_ref=ref)
    except DpaEvidenceMissing:
        # A refusal is not an unreadable file: customer data without DPA evidence stops the run
        # rather than leaving Gate 3 quietly un-run (WP-GRC-05, criterion 3).
        raise
    except Exception:  # noqa: BLE001 — Gate 3 reports what it can read; it never breaks a run
        return None


#: Where the campaign's invoice evidence lives. Written by `scripts/extract_site_data.py` from the
#: per-meter tabs of the customer's own workbook; read-only here.
_INVOICE_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "sites" / ("grundon_invoices_2025.json")
)

#: Metadata key in that file, describing the extraction rather than a site. Never a site entry.
_INVOICE_BASIS_KEY = "_basis"


def _invoice_evidence_for(site_id: str) -> InvoiceEvidence:
    """This site's invoice evidence, or an explicit ABSENCE record. Never raises, never None.

    Absence is a first-class answer here, not an error: a site with no invoice on file has never had
    its modelled delivered cost compared to anything, and Gate 3b's own no-invoice branch says so
    and caps the band at LOW. Returning ``None`` instead would silently skip the gate, which is the
    "it reads as a pass" failure D45 exists to prevent — a site nobody checked would be
    indistinguishable from one that reconciled.

    Every failure mode (absent file, unreadable JSON, unknown site) resolves to the same absence
    record with the reason recorded, because a reconciliation gate must never be the reason a run
    dies.
    """
    reason: str
    try:
        if _INVOICE_EVIDENCE_PATH.exists():
            # Customer invoice data: the DPA guard decides before the file is read (WP-GRC-05).
            check_load(_INVOICE_EVIDENCE_PATH, site_id=site_id)
        raw = json.loads(_INVOICE_EVIDENCE_PATH.read_text(encoding="utf-8"))
        entry = raw.get(site_id) if isinstance(raw, dict) else None
        if isinstance(entry, Mapping) and site_id != _INVOICE_BASIS_KEY:
            return invoice_evidence_from_mapping(site_id, entry)
        reason = (
            f"no invoice evidence is recorded for site_id '{site_id}' in "
            f"{_INVOICE_EVIDENCE_PATH.name}."
        )
    except DpaEvidenceMissing:
        reason = _invoice_refusal_reason(site_id)
    except FileNotFoundError:
        reason = f"the invoice evidence file {_INVOICE_EVIDENCE_PATH.name} is not present."
    except (OSError, ValueError) as exc:  # unreadable / malformed JSON
        reason = f"the invoice evidence file could not be read ({type(exc).__name__})."
    return InvoiceEvidence(
        site_id=site_id,
        invoice_available=False,
        period="",
        absence_reason=reason,
        source=str(_INVOICE_EVIDENCE_PATH),
    )


def _invoice_refusal_reason(site_id: str) -> str:
    """The absence reason when the DPA guard refuses the invoice file (it was not read).

    A site the file is not attributed to gets the same reason it always had (the file holds no
    entry for it), so a run on any other site is unchanged. A site the file covers is told the
    truth: its invoices exist but are quarantined customer data without DPA evidence.
    """
    from gridq.governance.customer_data import load_manifest

    entry = load_manifest().match(_INVOICE_EVIDENCE_PATH)
    if entry is None or site_id not in entry.site_ids:
        return (
            f"no invoice evidence is recorded for site_id '{site_id}' in "
            f"{_INVOICE_EVIDENCE_PATH.name}."
        )
    return (
        f"the invoice evidence file {_INVOICE_EVIDENCE_PATH.name} is quarantined customer data "
        "with no valid DPA evidence object for this site (WP-GRC-05), so it was not read."
    )


def _net_of_vat_site(site: SiteModel) -> SiteModel:
    """The site with its tariff stack stated net of VAT; the same object when it already is."""
    priced = net_of_vat_stack(site.tariff_stack)
    if priced is site.tariff_stack:
        return site
    return site.model_copy(update={"tariff_stack": priced})


def _gate_3b(
    site: SiteModel,
    customer_bill: CustomerBill | None,
    supplied: InvoiceEvidence | None,
    *,
    release: str | None,
) -> BillReconciliationResult | None:
    """Gate 3b for one site: the modelled delivered cost against that site's own invoices.

    Returns ``None`` only when there is no modelled bill to compare — no dispatch, so the gate is
    honestly un-run rather than passed, and `_bill_reconciliation_factor` caps such a run at MEDIUM.
    Everything else, including a site with no invoice on file, produces a real result.

    ``supplied`` lets a caller inject evidence (tests, and any future non-campaign source) without
    the default file read; the production path resolves it from the site's own id.
    """
    if customer_bill is None:
        return None
    evidence = supplied if supplied is not None else _invoice_evidence_for(site.site_id)
    # Both are non-optional floats with ge=0, so no `or 0.0` guard: that idiom is what turns a
    # genuine absence into a silent zero, and there is no absence to hide here.
    fixed = customer_bill.standing_gbp + customer_bill.capacity_gbp
    return reconcile_bill_against_invoice(
        evidence,
        modelled_kwh=customer_bill.import_before_kwh,
        modelled_cost_gbp=customer_bill.annual_bill_before_gbp,
        modelled_fixed_gbp=fixed,
        # No monthly decomposition exists on a run record yet; the gate's own limb says so rather
        # than this caller inventing one.
        modelled_monthly_cost_gbp=None,
    )


def generate_quote(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    engine: OptimisationEngine | None = None,
    scenario_set: str | None = None,
    forecast_ref: ForecastReference | None = None,
    quote_mode: QuoteMode = QuoteMode.DRAFT,
    expiry_date: date | None = None,
    contributor: str | None = None,
    human_minutes: float | None = None,
    installer_iterations: int | None = None,
    extra_warnings: list[str] | None = None,
    dispatch_energy: DispatchEnergy | None = None,
    debt_terms: DebtTerms | None = None,
    lender_terms: LenderTerms | None = None,
    lifecycle: LifecycleInputs | None = None,
    progress: Callable[[str, int | None, int | None], None] | None = None,
    hh_summary: HalfHourlySummary | None = None,
    derived_hh_dir: Path | None = None,
    invoice_evidence: InvoiceEvidence | None = None,
) -> QuoteRunResult:
    """Run ``site`` through engine → finance → a stamped `QuoteRecord` + `TelemetryEvent`.

    ``engine`` defaults to `BaselineEngine` (integration-only; always ``not_bankable``). When
    finance cannot price the run (`InsufficientInputsError`), the quote is still produced with
    ``financial_case=None`` and an honest warning naming the missing inputs. ``extra_warnings``
    are caller-supplied notes (e.g. an HH/forecast file that could not be used) carried onto the
    quote verbatim. ``debt_terms`` are optional, explicitly-supplied debt assumptions forwarded to
    the financial model so DSCR / levered (equity) return / price-to-target populate; they are
    **never** invented from the library (the placeholder library carries no debt structure), so the
    default ``None`` keeps the honest real-mode path unchanged — only a deliberate, labelled caller
    (e.g. the opt-in demo) supplies them.

    ``lender_terms`` (WP-UW-03, D75) are a lender's stated terms, supplied by the CLI's
    ``--lender-terms`` or the app's lender-terms form. They reach the base case and every
    sensitivity row (so the Low/Central/High DSCRs measure the same structure), and the record
    carries them. Without them the case states ``dscr_summary.status = no_debt_terms`` and the
    reason, never a silent ``None``. They replace ``debt_terms``; passing both raises `ValueError`
    before any solve. They do NOT reach the engine's sizing search (D78: the sizer ranks on its own
    finance runner, which takes no debt terms; exposing the covenant to the sizer is WP-UW-09).

    ``lifecycle`` (WP-UW-17) are the stated construction programme, COD delay, CM agreement and
    terminal / decommissioning / replacement flows (the CLI's ``--lifecycle``, the app's
    ``lifecycle_json``). They reach the base case and every sensitivity row; a stated delay adds
    the zero-weight ``cod_delay`` row. Like lender terms they do not reach the sizing search, and
    the case says so ("SIZE SELECTED WITHOUT LIFECYCLE FLOWS").

    ``progress`` is an optional presentation-only side-channel sink (the web run screen), symmetric
    with the engine's own hook: when supplied it is duck-typed into the engine (if it exposes
    ``set_progress_sink``, as `MilpEngine`/`DemoEngine` do) so the sizing search narrates itself,
    and the three pipeline phases ``("optimise"|"finance"|"finalise", None, None)`` are emitted
    around the solve / finance / assembly steps. Every emit is guarded and the default ``None`` is
    byte-identical to omitting it — the side channel never touches a computed number.

    Every run is stamped with its validation Gate-2 basis and its **confidence band**
    (`gridq.quotes.confidence.assess_confidence`) — evidential strength, capped by the weakest
    input, with the binding factors recorded so a reader knows what to fix to raise it. The band
    is assessed from the assembled record and persisted on it; the lifecycle gate then reads the
    basis rather than a warning string (N21).

    **D44 — no missing input blocks a run.** Gates 2 and 3 record and continue: a missing series, an
    undeclared basis or a declared-vs-measured breach caps the band, states the confirmation ask on
    the quote, and the run completes. ``hh_summary`` is the measured summary Gate 3 compares the
    declared basis against; when omitted it is read from the site's own file ref (fully guarded — an
    unreadable file leaves Gate 3 honestly un-run, never fails the quote). ``derived_hh_dir`` opts
    into deriving the missing series itself (a reference profile scaled to the site's own totals,
    stamped MODELLED); it is off by default so the default path stays deterministic, offline and
    free of file IO, and because a derived shape legitimately changes ``input_hash``. Gate 1 (MILP
    or nothing) is untouched — it is an engine-quality gate, not a data gate.
    """

    if lender_terms is not None and debt_terms is not None:
        raise ValueError(
            "generate_quote takes lender_terms OR debt_terms, not both: one run measures one debt "
            "structure"
        )

    def _emit(phase: str) -> None:
        # Presentation-only side channel: guarded so it can never perturb the deterministic run.
        if progress is None:
            return
        try:
            progress(phase, None, None)
        except Exception:  # noqa: BLE001 — a progress sink must never break the run
            pass

    # Validation Gate 2 (docs/validation_requirements.md §1, D44): the half-hourly basis is
    # RECORDED, never a reason to block. This is the single choke point every QuoteRecord passes
    # through, so the policy is applied once here rather than per-caller. A missing series or an
    # undeclared basis caps the confidence band and states the confirmation ask; the run continues
    # and reports honestly (with no series there is no dispatch, so the engine prices nothing —
    # which is an auditable empty result, not a refusal). Gate 1 (MILP or nothing) is untouched:
    # it is an engine-quality gate and still refuses `BaselineEngine` numbers.
    #
    # N21: the gate's verdict is PERSISTED on the record (`half_hourly_basis`,
    # `confidence_ceiling`) below, not only narrated here. The prose stays because a human reads
    # it, but the ceiling is now a field `lifecycle.underwriting_blockers` reads — previously
    # `admission` appeared nowhere else in this file and "confidence is capped at MEDIUM" capped
    # precisely nothing: a modelled-basis run permitted every transition a metered one did.
    site = _derive_half_hourly_if_asked(site, derived_hh_dir, assumptions)
    admission = admit_half_hourly(site, release=assumptions.metadata.version)
    warnings_from_gate = [_gate_2_warning(admission)]

    # Validation Gate 3 (docs/validation_requirements.md §1 · D44 · contradiction C-6): does the
    # DECLARED load basis reconcile against the series actually being priced? The declared peak and
    # annual drive sizing, capex and Capacity Market revenue; the measured series drives every
    # revenue line. `reconcile_declared_against_measured` existed, was tested, and was called from
    # NOWHERE in src/ — so `assess_confidence` always took its "was not reconciled" branch and a
    # mandatory gate was unenforced on every production run. Wired here, in record-and-continue
    # mode: a breach caps the band and states the ask; it never blocks and never re-bases the run.
    release = assumptions.metadata.version
    summary = _measured_summary(site, hh_summary)
    reconciliation = None if summary is None else reconcile_declared_against_measured(site, summary)
    if reconciliation is not None and (not reconciliation.reconciles or reconciliation.warnings):
        warnings_from_gate.append(
            f"Load reconciliation (Gate 3): {reconciliation.summary()} "
            f"{reconciliation.basis_chosen}"
        )

    # Every assumption this run stood in for (D44). The site's own records were derived OFFLINE and
    # consumed by the run, so they are real inputs and are already inside `input_hash`; the gates'
    # records are observations ABOUT this run and are deliberately NOT written back onto the site —
    # doing so would move the identity of every existing site for no change in what was priced.
    derived_assumptions: list[DerivedAssumption] = [
        *site.assumptions_requiring_confirmation,
        *admission.derived,
    ]
    if reconciliation is not None:
        derived_assumptions.extend(reconciliation_records(reconciliation, release=release))
    # WP-UW-07 (D105): a band the stack did not quote, or band timing it did not state,
    # is priced on the declared no-spread envelope and recorded here, never a silent mean.
    derived_assumptions.extend(import_rate_assumptions(site.tariff_stack, release=release))
    warnings_from_gate.extend(
        f"ASSUMPTION REQUIRING CONFIRMATION [{record.report_label}] — {record.statement()}"
        for record in derived_assumptions
    )

    engine = engine or BaselineEngine()
    # Duck-typed progress hop (additive; C035), symmetric with the pipeline-phase emits: push the
    # sink into the engine when it offers the setter (MilpEngine/DemoEngine do; BaselineEngine does
    # not, so it simply reports the three pipeline phases). Guarded — wiring never breaks the run.
    if progress is not None:
        setter = getattr(engine, "set_progress_sink", None)
        if callable(setter):
            try:
                setter(progress)
            except Exception:  # noqa: BLE001 — a progress hook must never break the run
                pass
    # WP-UW-07 (D103): a VAT-inclusive stack is priced net of VAT, converted ONCE here so
    # the dispatch, the customer bill and finance read the same net figures. The site as supplied
    # (not the converted copy) is what the quote record hashes and carries.
    priced_site = _net_of_vat_site(site)
    price_basis_note = resolved_price_basis(site.tariff_stack).note
    if price_basis_note is not None:
        warnings_from_gate.append(f"Tariff basis (D103): {price_basis_note}")
    request = engine.build_optimisation_request(priced_site, assumptions, scenario_set)
    _emit("optimise")
    result = engine.solve(request)

    # Dispatch energy (annual charge/discharge volumes) activates finance's levies-on-charge +
    # price-to-target. It is not carried on the frozen OptimisationResult (D13), so a production
    # engine exposes it via the optional ``selected_dispatch_energy`` hook; an explicit
    # ``dispatch_energy`` argument overrides it. The placeholder engine has neither, so the lines
    # stay honestly unavailable — exactly as before (zero downstream change for the baseline path).
    if dispatch_energy is None:
        hook = getattr(engine, "selected_dispatch_energy", None)
        if callable(hook):
            dispatch_energy = hook()

    # Full evaluated sizing grid (every power×duration candidate + the chosen optimum) for the 3D
    # sizing surface. Read via the same duck-typed hook pattern as dispatch energy and persisted
    # onto the QuoteRecord — additively, never on the frozen OptimisationResult (D13). An engine
    # without the hook (e.g. BaselineEngine) leaves it ``None``: no downstream change.
    sizing_grid: SizingGrid | None = None
    grid_hook = getattr(engine, "last_sizing_run", None)
    if callable(grid_hook):
        sizing_run = grid_hook()
        if sizing_run is not None:
            sizing_grid = sizing_run.sizing_grid()

    # Representative half-hourly dispatch day (W5-2) for the dispatch chart — same duck-typed hook
    # pattern, persisted additively onto the QuoteRecord (never on the frozen OptimisationResult,
    # D13). An engine without the hook (e.g. BaselineEngine) leaves it ``None`` (no change).
    dispatch_profile: DispatchProfile | None = None
    profile_hook = getattr(engine, "last_dispatch_profile", None)
    if callable(profile_hook):
        dispatch_profile = profile_hook()

    # Customer bill before/after (W7-5, C017) for the proposal headline — same duck-typed hook
    # pattern, persisted additively onto the QuoteRecord. ``None`` (no hook / no dispatch / no
    # time-mappable energy rate) keeps the proposal's honest pending seam.
    customer_bill: CustomerBill | None = None
    provisioning_cost: float | None = None
    cost_hook = getattr(engine, "last_provisioning_opportunity_cost", None)
    if callable(cost_hook):
        provisioning_cost = cost_hook()
    bill_hook = getattr(engine, "last_customer_bill", None)
    if callable(bill_hook):
        customer_bill = bill_hook()

    # Validation Gate 3b — the modelled delivered cost against the site's OWN invoices.
    #
    # Gate 3 checks the modelled load against the measured series. It says nothing about whether
    # the tariff stack turns that load into the right number of pounds, and every avoided-cost
    # revenue line is a fraction of the delivered cost. This closes that gap. Like Gate 3 it was
    # built, tested and called from NOWHERE in `src/` — the seventh instance of that pattern in
    # this repo — so it is wired here, at a production call site, in record-and-continue mode.
    #
    # It runs AFTER the solve because it needs `CustomerBill`. The BEFORE side is used
    # (`annual_bill_before_gbp`), which is dispatch-invariant, so this tests the tariff stack and
    # not the battery. Per-site only: the module docstring's warning is load-bearing, since the
    # Grundon portfolio reconciles to +0.7% while no individual site is within +-7%.
    bill_reconciliation = _gate_3b(site, customer_bill, invoice_evidence, release=release)
    if bill_reconciliation is not None:
        bill_records = bill_reconciliation_records(bill_reconciliation, release=release)
        derived_assumptions.extend(bill_records)
        if not bill_reconciliation.reconciles or bill_reconciliation.warnings:
            warnings_from_gate.append(
                f"Bill reconciliation (Gate 3b): {bill_reconciliation.summary()} "
                f"{bill_reconciliation.basis_chosen}"
            )
        # The register disclosure loop above has already run, so records added here would reach the
        # quote record and the band while silently missing the report's "assumptions requiring
        # confirmation" section — a call path down which only part of the value flows. Re-emit for
        # the new records only, in the same format.
        warnings_from_gate.extend(
            f"ASSUMPTION REQUIRING CONFIRMATION [{record.report_label}] — {record.statement()}"
            for record in bill_records
        )

    financial_case: FinancialCase | None
    bankable: bool
    # The cashflow model is the only place that records whether the run consumed placeholder /
    # needs_review assumption values — the confidence assessment's `assumptions` factor reads it,
    # and `None` (finance refused) is honestly "not established", never a clean bill.
    cashflows: CashflowModel | None = None
    warnings: list[str] = [*warnings_from_gate, *(extra_warnings or [])]
    # D-116 (WP-UW-16): an awarded site's availability leg is priced GROSS while no sourced
    # utilisation performance ratio exists, and the quote says so here, where the reader looks,
    # not only in the stream notes. The same library read decides the price (valuation) and this
    # label, so they cannot disagree. `warnings` is not part of `input_hash`: quote_id is unmoved.
    gross_label = local_flex_gross_label(site, assumptions)
    if gross_label is not None:
        warnings.append(gross_label)
    _emit("finance")
    try:
        model = run_financial_model(
            priced_site,
            result,
            assumptions,
            dispatch_energy=dispatch_energy,
            debt_terms=debt_terms,
            lender_terms=lender_terms,
            lifecycle=lifecycle,
        )
        financial_case = model.case
        bankable = model.bankable
        cashflows = model.cashflows
    except InsufficientInputsError as exc:
        financial_case = None
        bankable = False
        warnings.append(
            f"Financial model skipped — no positive sizing to price: {exc}. "
            "Supply the missing inputs (see the ranked data asks) to underwrite."
        )

    # Output 2: Low/Central/High DSCR/return sensitivities (M6→M8). Only when there is a priced
    # base case; each row is the financial model under a scenario, against the same debt/dispatch.
    # The set-level warnings come back with the rows and are merged onto the quote (N17): before
    # that, the "every band is identity, so these three rows are the base case repeated" finding
    # was computed and dropped one hop from the record it needed to reach.
    scenario_sensitivities: list[ScenarioSensitivity] = []
    if financial_case is not None:
        sensitivities = compute_scenario_sensitivities(
            priced_site,
            result,
            assumptions,
            debt_terms=debt_terms,
            lender_terms=lender_terms,
            dispatch_energy=dispatch_energy,
            lifecycle=lifecycle,
        )
        scenario_sensitivities = sensitivities.rows
        warnings.extend(sensitivities.set_warnings)

    # N22: the forecast is documented as gated upside, but a consented WHOLESALE_P415 stream with
    # an agreed export tariff is priced into the FLOOR as CONTRACTED (D43). When that happened the
    # engine says so here, on the quote, so the reader is not left with the CLI's contract text.
    # Read through the same duck-typed hook pattern as the other engine outputs and merged into
    # `warnings` (which is NOT part of `input_hash`), so the quote_id is unmoved.
    floor_note_hook = getattr(engine, "last_forecast_priced_floor_note", None)
    if callable(floor_note_hook):
        floor_note = floor_note_hook()
        if floor_note:
            warnings.append(floor_note)

    # D46/D55: the named FEASIBILITY-STUDY basis the engine priced on, read through the same
    # duck-typed hook (D13) as the notes above — `MilpEngine.last_study_basis()`.
    #
    # Reading it off the ENGINE rather than taking it as a `generate_quote` parameter is deliberate
    # and is the safer of the two designs: the basis is a fact about the solve that actually
    # happened, so it cannot diverge from the basis that priced the numbers. A parameter would let a
    # caller declare one basis and price on another, which is precisely the mistake D46 exists to
    # make impossible. It also needs no change to any caller: every study run already constructs the
    # engine with `study_basis=`, so the basis now reaches the record with nothing new to remember.
    #
    # Two things this closes, both previously unenforced (`assembly.assemble_quote_record`'s own
    # warning said so, and `tests/test_all_streams_study_basis.py` now pins both):
    #
    # 1. **Identity becomes STRUCTURAL.** The basis was already hash-bearing in practice, but only
    #    via PROSE: `MilpEngine` writes `study_basis.note()` as the first entry of
    #    `OptimisationResult.warnings` (engine.py:2218) and `_canonical_result_dump` hashes the
    #    result, so two bases did already differ. Measured, not assumed. But that made `run_id`
    #    depend on the wording of a sentence — reword the note and every identity moves; and it left
    #    the canonical input with no `basis_id` field to read. Passing the record itself puts the
    #    structured basis in `input_hash` where a consumer can find it.
    # 2. **The band cap is enforced.** `StudyBasis.note()` asserts "this run's confidence band is
    #    capped at LOW", and until now nothing read `confidence_ceiling` off the basis, so the
    #    sentence was true only by coincidence of the Gate-2 admission. The ceiling handed to the
    #    record below is now the WEAKER of the two — a study basis can lower the band and can never
    #    raise it.
    #
    #    Read this with `quotes.confidence._study_basis_factor`, because the ceiling computed here
    #    is only half of it: `assess_confidence` builds the BAND from factors and reads neither this
    #    variable nor the record's `confidence_ceiling` field, so capping the field alone would have
    #    left the band uncapped and LOW only by the further coincidence that the shipped library has
    #    zero confirmed assumptions. The factor is what makes the cap bind, and it reads the basis
    #    off `QuoteRecord.study_basis_id` (persisted by `assemble_quote_record` from the
    #    `study_basis=` argument below) rather than off this local, so a consumer re-assessing a
    #    persisted record gets the same answer. Pinned end-to-end by
    #    `tests/test_study_basis_marker_and_band.py`.
    study_basis_hook = getattr(engine, "last_study_basis", None)
    study_basis = study_basis_hook() if callable(study_basis_hook) else None
    confidence_ceiling = admission.confidence_ceiling
    if study_basis is not None:
        if study_basis.confidence_ceiling.rank < confidence_ceiling.rank:
            confidence_ceiling = study_basis.confidence_ceiling
        # 3. **The label and the identity move in lockstep.** `outputs.viewmodel` detects the basis
        #    from the record's own warnings, so a run IDENTIFIED as a study run that does not SAY so
        #    on the record would render as an underwriting output — the precise failure D46's
        #    labelling exists to prevent. `MilpEngine` already writes this sentence as the first
        #    entry of `OptimisationResult.warnings` (engine.py:2218), so this is a DE-DUPLICATED
        #    backstop, never a second banner: any engine that reports a basis now also states it.
        note = study_basis.note()
        if note not in warnings and note not in result.warnings:
            warnings.append(note)

    # The confidence band (docs/validation_requirements.md §2), finally attached to the record.
    # It is assessed AFTER assembly because two of its six factors read the assembled record (the
    # priced-floor composition and the assumption provenance), then stamped onto a re-validated
    # copy — the band is evidence about the run, so it cannot be assessed before the run exists.
    _emit("finalise")
    quote = assemble_quote_record(
        site,
        result,
        financial_case,
        assumptions,
        quote_mode=quote_mode,
        forecast_ref=forecast_ref,
        expiry_date=expiry_date,
        extra_warnings=warnings,
        sizing_grid=sizing_grid,
        dispatch_profile=dispatch_profile,
        scenario_sensitivities=scenario_sensitivities,
        customer_bill=customer_bill,
        provisioning_opportunity_cost_gbp=provisioning_cost,
        half_hourly_basis=admission.basis,
        confidence_ceiling=confidence_ceiling,
        study_basis=study_basis,
    )
    assessment = assess_confidence(
        site,
        quote,
        admission=admission,
        # C-6: previously omitted, so the reconciliation factor permanently reported "was not
        # reconciled" and Gate 3 capped nothing. `None` still means genuinely un-run (no measured
        # series could be read), which is honestly weaker evidence than a reconciled basis.
        reconciliation=reconciliation,
        bill_reconciliation=bill_reconciliation,
        derived_assumptions=derived_assumptions,
        uses_unconfirmed_assumptions=(
            None if cashflows is None else cashflows.uses_unconfirmed_assumptions
        ),
    )
    # Stamp the assessment on. Re-validated rather than `model_copy`d so the record's own
    # invariants (the T0 acknowledgement guard) re-fire over the final field set — the same
    # reason `lifecycle.transition_quote` re-validates. The stamp touches no hashed field, so
    # `quote_id` / `input_hash` are byte-identical to the pre-stamp record.
    stamped = quote.model_dump(mode="json")
    stamped["confidence_band"] = assessment.band.value
    stamped["confidence_binding_factors"] = [
        f"{f.name} — {f.reason}" for f in assessment.binding_factors
    ]
    stamped["warnings"] = [*quote.warnings, assessment.explain()]
    quote = QuoteRecord.model_validate(stamped)
    telemetry = emit_quote_telemetry(
        quote,
        site,
        contributor=contributor,
        human_minutes=human_minutes,
        installer_iterations=installer_iterations,
    )
    return QuoteRunResult(
        quote=quote,
        telemetry=telemetry,
        site=site,
        result=result,
        financial_case=financial_case,
        bankable=bankable,
        forecast_ref=forecast_ref,
        sizing_grid=sizing_grid,
        dispatch_profile=dispatch_profile,
        customer_bill=customer_bill,
        confidence=assessment,
        derived_assumptions=tuple(derived_assumptions),
        reconciliation=reconciliation,
        bill_reconciliation=bill_reconciliation,
    )
