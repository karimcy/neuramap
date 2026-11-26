"""QuoteRecord — binds a run (site + optimisation + finance) into a versioned quote."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from gridq.schemas.customer_bill import CustomerBill
from gridq.schemas.dispatch_profile import DispatchProfile
from gridq.schemas.enums import ConfidenceBand, DataTier, HalfHourlyBasis, QuoteMode
from gridq.schemas.finance import FinancialCase
from gridq.schemas.optimisation import OptimisationResult
from gridq.schemas.provenance import GridQModel
from gridq.schemas.references import (
    ConnectionAgreementRef,
    EvidenceLink,
    canonical_evidence_links,
    omit_unset,
)
from gridq.schemas.sizing import SizingGrid

#: The one string that lets an UNDERWRITTEN quote exist on a T0 site (N19).
#:
#: The guard below used to be satisfied by ANY non-empty ``warnings`` list — and three separate
#: routes append a warning to every quote unconditionally (the not-bankable note, the
#: zero-confirmed-assumptions note, the half-hourly-basis note), so it could never fire. A guard
#: that cannot fire is worse than no guard: it reads, in the schema, as a control that is not
#: there. The acknowledgement must therefore be a string a caller can only produce by deciding to
#: produce it. It is deliberately long and specific so no generic warning can collide with it.
T0_UNDERWRITING_ACKNOWLEDGEMENT = (
    "T0 UNDERWRITING ACKNOWLEDGED: this quote is underwritten from annual-kWh/tariff/postcode "
    "data with no half-hourly series behind it. The figures are not diligence-grade and must not "
    "be presented to a lender as underwritten."
)


class StatusHistoryEntry(GridQModel):
    """One prior lifecycle state, for the append-only history trail."""

    quote_mode: QuoteMode
    timestamp: datetime = Field(default_factory=datetime.now)
    note: str | None = None


class ScenarioSensitivity(GridQModel):
    """One scenario's DSCR/return outcome — a row of the Output-2 sensitivity table.

    Produced by re-running the financial model under a scenario (Low/Central/High). ``min_dscr``
    and ``irr_pct`` are ``None`` when their inputs are unavailable (e.g. no debt → no DSCR) — never
    guessed.
    """

    scenario_id: str
    label: str
    min_dscr: float | None = None
    irr_pct: float | None = None
    #: Time-to-energisation: months the scenario shifts commercial operations date (COD). 0 = on
    #: time. Reported so consenting-timeline scenarios are visible, not just applied to cashflows.
    cod_delay_months: int = Field(default=0, ge=0)
    note: str | None = None


class QuoteRecord(GridQModel):
    """A quote at a point in its lifecycle. Traceable via input/version hashes."""

    quote_id: str
    quote_mode: QuoteMode
    site_id: str
    input_hash: str | None = None
    assumption_version: str | None = None
    code_version: str | None = None
    optimisation_result: OptimisationResult | None = None
    financial_case: FinancialCase | None = None
    # Additive, optional (W5-0, D23): the full evaluated power×duration sizing grid the 3D surface
    # plots. Persisted from the engine's duck-typed `last_sizing_run()` hook; absent (``None``) for
    # engines that do not expose it (e.g. the integration `BaselineEngine`). Excluded from the
    # quote's `input_hash` (assembly hashes the run inputs, not the record), so adding it is
    # backward-compatible: existing quotes/fixtures without it validate unchanged.
    sizing_grid: SizingGrid | None = None
    # Additive, optional (W5-2, D25): a representative half-hourly dispatch day for the dispatch
    # chart. Persisted from the engine's duck-typed `last_dispatch_profile()` hook; ``None`` for
    # engines without it (e.g. the integration `BaselineEngine`) or runs with no metered HH series.
    # Excluded from the quote's `input_hash` (same as `sizing_grid`), so adding it is
    # backward-compatible and does not change `quote_id`.
    dispatch_profile: DispatchProfile | None = None
    # Additive, optional (L15): Low/Central/High DSCR/return sensitivities (Output 2), produced by
    # re-running the financial model per scenario. Excluded from the quote's `input_hash` (same as
    # `sizing_grid`), so adding it is backward-compatible and does not change `quote_id`.
    scenario_sensitivities: list[ScenarioSensitivity] = Field(default_factory=list)
    # Additive, optional (W7-5, C017): the customer bill before/after decomposition — the
    # proposal headline's persisted data source. Persisted from the engine's duck-typed
    # `last_customer_bill()` hook; ``None`` for engines without it, or a tariff with no
    # time-mappable energy rate (the honest pending seam). A run with NO dispatch is no longer
    # ``None``: since 2026-08-15 it carries a BEFORE-only bill (`after_side_priced` False, every
    # AFTER figure absent), so consumers must branch on that discriminator and never read a
    # persisted bill as proof of a saving. Excluded from the quote's `input_hash` (same as
    # `sizing_grid`/`dispatch_profile`) — backward-compatible.
    customer_bill: CustomerBill | None = None
    # Additive, hash-safe (issue 017 / D42): the selected spec's engine-MEASURED obligation
    # cost (the priced-floor delta of one extra solve without the obligations, excluding the
    # LOAD_SERVICE stream's own declared value). ``None`` = no obligation or no measurement —
    # never zero-filled, never estimated.
    provisioning_opportunity_cost_gbp: float | None = None
    # --- the PRICING BASIS, persisted machine-readably (D46/D55) --------------------------------
    #: Which named FEASIBILITY-STUDY basis priced this record, or ``None`` for the underwriting
    #: basis. Values are `StudyBasis.BASIS_ID` / `AllStreamsEnabledStudyBasis.BASIS_ID`
    #: (``"export_in_priced_floor_study_v1"`` / ``"all_streams_enabled_study_v1"``); the id → banner
    #: map is `gridq.schemas.study.STUDY_BASIS_LABELS`.
    #:
    #: **Why an id on the record and not a string in the prose.** D46 records that the labelling IS
    #: the entire mitigation for a second pricing basis, so *how a consumer detects the basis* is
    #: the load-bearing part. Until this field existed the only machine-readable basis signal on a
    #: finished record was the wording of `StudyBasis.note()`, written as the first entry of
    #: ``optimisation_result.warnings``, and every renderer sniffed it with a literal prefix
    #: (`gridq.outputs.viewmodel`). That is an identity resting on a sentence: the measured
    #: consequence was a D55 run whose banner vanished from all four artifacts because the detector
    #: matched ``"STUDY BASIS (D46)"``, and the customer proposal then called a study run an
    #: underwriting run. A `Literal` id cannot be reworded, so a consumer that branches on this
    #: field cannot be broken by an edit to a disclosure sentence.
    #:
    #: Additive and optional, and it moves NO identity: `_canonical_run_input` hashes the run's
    #: INPUTS (the whole `StudyBasis`, per D46), never the assembled record, so ``input_hash`` /
    #: ``quote_id`` are byte-identical across this growth. The global `SCHEMA_VERSION` is
    #: deliberately not bumped either — same reasoning, and the same precedent, as
    #: `gridq.schemas.sizing.SIZING_GRID_SCHEMA_VERSION`'s note on versioning additive projections
    #: locally rather than re-minting every identity in the repo.
    #:
    #: **Legacy records read ``None``.** A quote persisted before 2026-08-18 carries ``None`` even
    #: if a study basis priced it, because nothing wrote the field then. A consumer must therefore
    #: treat ``None`` as "this record does not say", not as "this was an underwriting run", and keep
    #: its warnings-prefix fallback for records that predate the field.
    study_basis_id: str | None = None
    # --- validation Gate 2 basis + confidence, persisted (N21) ---------------------------------
    # Before these fields the Gate-2 verdict existed only as prose interpolated into a warning
    # ("confidence is capped at MEDIUM"), and `admission` appeared nowhere else in the pipeline:
    # no branch in the lifecycle, the assembly or the financial model consulted the basis, so a
    # modelled-basis run and a metered run carried identical precision and permitted identical
    # transitions. Persisting them makes the ceiling a thing a gate can READ. All four are
    # additive and optional (``None`` on historical records) and none enters `input_hash`, so
    # adding them does not move any `quote_id`.
    #: How the half-hourly series underwriting priced was obtained (`admit_half_hourly`).
    half_hourly_basis: HalfHourlyBasis | None = None
    #: The best band this basis can support. A MODELLED/MIXED basis caps at MEDIUM however good
    #: the rest of the case is — evidence about a typical site of this type, not about this site.
    confidence_ceiling: ConfidenceBand | None = None
    #: The band actually assessed for this run (`gridq.quotes.confidence.assess_confidence`),
    #: capped by the weakest input. Evidential strength — never a probability, never a hurdle.
    confidence_band: ConfidenceBand | None = None
    #: The factors that set the band, ``"name — reason"``. What to fix to raise it.
    confidence_binding_factors: list[str] = Field(default_factory=list)
    #: A deliberate, recorded override permitting advance past ``indicative`` on a basis whose
    #: ceiling would otherwise refuse it (see `gridq.quotes.lifecycle`). Free text: who decided,
    #: and on what evidence. Never set by the pipeline — only by a caller taking the decision.
    basis_override_note: str | None = None
    # --- typed references to other frozen contracts (IC-12 2.0.0, WP-CORE-03) -------------------
    #: The IC-1 agreement the quoted site's connection terms come from: copied from
    #: ``SiteModel.connection.agreement`` at assembly, so a finished record names its agreement
    #: without the site file. ``None`` when the site names none; absent from the dump then.
    connection_agreement: ConnectionAgreementRef | None = None
    #: Every IC-4 evidence link the site and the engine result carried, merged and ordered at
    #: assembly. ``None`` when there is none; absent from the dump then. The record itself is
    #: never hashed into ``input_hash``; the links reach the hash through the site and the result.
    evidence_refs: tuple[EvidenceLink, ...] | None = None
    data_tier: DataTier
    expiry_date: date | None = None
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    @model_serializer(mode="wrap")
    def _dump_without_unset_growth(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(handler(self), ("connection_agreement", "evidence_refs"))

    @model_validator(mode="after")
    def _canonicalise_evidence_refs(self) -> QuoteRecord:
        """Order the evidence links, refuse duplicates, collapse empty to None (IC-12 2.0.0)."""
        self.evidence_refs = canonical_evidence_links(self.evidence_refs)
        return self

    @model_validator(mode="after")
    def _underwritten_from_t0_needs_acknowledgement(self) -> QuoteRecord:
        """Refuse UNDERWRITTEN on a T0 site without the specific acknowledgement (N19).

        The predecessor tested ``not self.warnings``. Every quote the pipeline builds carries at
        least one warning by construction — `assembly._not_bankable_note` is appended for any
        status other than `BANKABLE_CANDIDATE` (which no engine emits), the financial model
        appends a zero-confirmed-assumptions note, and `pipeline` always appends the half-hourly
        basis note — so the guard was satisfied on every possible input and never fired once.
        Testing for a sentinel the caller must add on purpose is the only version of this check
        that has any content.
        """
        if (
            self.quote_mode == QuoteMode.UNDERWRITTEN
            and self.data_tier == DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
            and T0_UNDERWRITING_ACKNOWLEDGEMENT not in self.warnings
        ):
            raise ValueError(
                "an underwritten quote from a T0 site is refused: T0 data (annual kWh + tariff + "
                "postcode, no half-hourly series) is insufficient to underwrite firmly. If a "
                "caller is deliberately overriding this, the exact string "
                "`gridq.schemas.quote.T0_UNDERWRITING_ACKNOWLEDGEMENT` must appear in `warnings` "
                "— a generic warning does not satisfy this guard."
            )
        return self
