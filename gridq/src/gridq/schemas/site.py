"""SiteModel — the canonical, validated representation of one behind-the-meter site.

This is the frozen input contract every downstream module reads. There is deliberately
**no archetype field**: the engine reasons from the site's own data.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from gridq.schemas.commitment import AvailabilityCommitment
from gridq.schemas.confidentiality import Confidentiality
from gridq.schemas.connection import ConnectionDetails
from gridq.schemas.consents import ConsentState
from gridq.schemas.counterparty import Counterparty
from gridq.schemas.derived import DerivedAssumption
from gridq.schemas.enums import DataTier, LegalEntityType, MarketStream
from gridq.schemas.export_arrangement import ExportArrangement
from gridq.schemas.provenance import GridQModel
from gridq.schemas.provisioning import ProvisioningRequirement
from gridq.schemas.references import EvidenceLink, canonical_evidence_links, omit_unset
from gridq.schemas.stream_consent import RevenueStreamConsent
from gridq.schemas.supply import SupplyBoundary
from gridq.schemas.tariff import TariffStack


class LoadDataSummary(GridQModel):
    """Summary of a site's load data (annual figure and/or half-hourly coverage)."""

    annual_kwh: float | None = Field(default=None, ge=0)
    half_hourly_file_ref: str | None = None
    half_hourly_rows: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    missing_intervals: int | None = Field(default=None, ge=0)
    max_import_kw: float | None = Field(default=None, ge=0)
    max_export_kw: float | None = Field(default=None, ge=0)
    validation_warnings: list[str] = Field(default_factory=list)

    # --- how the half-hourly series was obtained (validation Gate 2) --------------------------
    # Without this, a MODELLED series is indistinguishable from a metered one the moment it is
    # written to disk and referenced: the mandate gate would see a file path and report METERED,
    # and the confidence ceiling would be wrong in the flattering direction. The basis is a
    # property of the data, so it belongs on the data.
    #: ``"metered"`` | ``"modelled"`` | ``"mixed"``. Defaults to metered ONLY when a file is
    #: present and nothing says otherwise; synthesis always sets it explicitly.
    half_hourly_basis: str | None = None
    #: The reference profile and its source, when the series is modelled.
    half_hourly_reference_profile: str | None = None
    #: What the modelled shape was scaled to (e.g. "12 monthly invoice totals, 2025").
    half_hourly_scaled_to: str | None = None

    @property
    def has_hh_coverage(self) -> bool:
        """True if the half-hourly coverage metadata (rows + window) is present."""
        return (
            self.half_hourly_rows is not None
            and self.start_date is not None
            and self.end_date is not None
        )


class PhysicalGateStatus(GridQModel):
    """Pass/fail status of the physical install gates."""

    footprint_available: bool | None = None
    fire_separation_ok: bool | None = None
    access_ok: bool | None = None
    planning_or_landlord_issue: bool | None = None
    notes: str | None = None


class SiteModel(GridQModel):
    """One behind-the-meter site. The shared input contract (M1)."""

    site_id: str
    site_name: str | None = None
    postcode: str
    country: str = "GB"
    data_tier: DataTier
    load_data: LoadDataSummary
    annual_consumption_kwh: float | None = Field(default=None, ge=0)
    tariff_stack: TariffStack
    connection: ConnectionDetails
    consents: ConsentState
    physical_gates: PhysicalGateStatus
    counterparty: Counterparty
    # Additive, optional (L21): the supply boundary — the behind-the-meter metering/contractual
    # perimeter (import MPAN, behind-the-meter flag). The Brief's schema lists "supply boundary &
    # supplier consent"; supplier consent lives on `consents`, this is the boundary half. Defaults
    # to an all-unknown bundle so existing sites validate unchanged.
    supply_boundary: SupplyBoundary = Field(default_factory=SupplyBoundary)
    # Additive, optional (L03): how the site's confidential HH data is held (recorded NDA state +
    # personal-data treatment). Defaults to an all-unknown bundle so existing sites validate
    # unchanged. Access to the HH data itself is logged in `gridq.ingestion.access_log`.
    confidentiality: Confidentiality = Field(default_factory=Confidentiality)
    # Additive, optional (issue 017 / C033, authorized D42): declared load-serving
    # obligations — the battery as capacity for windows the connection cannot supply. Default
    # None so every existing site validates unchanged; `quotes.assembly._canonical_run_input`
    # POPS the field when None so existing input hashes / run ids are byte-stable (D42 hash
    # policy) — a SET requirement enters the hash, as a real input must.
    provisioning_requirements: list[ProvisioningRequirement] | None = None
    # Additive, optional (issue 018 / WS-A4): the site's export / route-to-market arrangement
    # (route, counterparty, aggregator cut, declared export tariff). Default None so every
    # existing site validates unchanged; `quotes.assembly._canonical_run_input` POPS the field
    # when None so existing input hashes / run ids are byte-stable (D42 hash policy) — a SET
    # value enters the hash, as a real input must. WS-C reads `aggregator_cut_pct` to net
    # local-flex revenue.
    export_arrangement: ExportArrangement | None = None
    # Additive, optional (Revenue-Data Drive / D43): per-stream customer consent captured at
    # intake — for each revenue stream, whether the customer authorised GridQ to pursue it and
    # any commercially agreed rate. When a stream is consented (with its rate where needed) its
    # gate may open to a contracted firmness and the proposal reports "consent given". Default
    # None so every existing site validates unchanged; `quotes.assembly._canonical_run_input`
    # POPS the field when None so existing input hashes / run ids are byte-stable (D42 hash
    # policy) — a SET list enters the hash, as a real input must.
    stream_consents: tuple[RevenueStreamConsent, ...] | None = None
    # Additive, optional (D54, Olly 2026-08-17 — briefed as "D53"; that number was already taken
    # in this working tree by the agreed-capacity decision, so this one takes the next free id):
    # local-flexibility AWARDS the site holds. The owner states, on good authority, that every site
    # currently being underwritten holds one, and instructs that it be assumed for any site he
    # supplies unless he says otherwise at upload.
    #
    # **This is the owner supplying an input, not the model inventing one.** D45 forbids GridQ
    # asserting a third party's agreement; it does not forbid the site's owner declaring one. The
    # distinction is carried in the record, not in the prose: each award names its `confirmed_by`
    # and `basis`, so an owner attestation can never be read as a transcribed DNO contract.
    #
    # **The mitigation is the labelling, and it is load-bearing.** The published registers were
    # checked, site by site, in the same session the owner gave this instruction, and they do NOT
    # agree with it: only Bishops Cleeve sits in a zone that is awarded AND open; Ewelme's zone is
    # awarded and 99.7% subscribed; five sites sit in no tendered zone at all. So an award recorded
    # here is an attestation awaiting its contract reference — never evidence of a register match —
    # and the value stack says exactly that on every run (`gb_streams.LocalFlexPicloStream`).
    #
    # Default None so every existing site validates unchanged;
    # `quotes.assembly._canonical_site_dump` POPS the field when None/empty so existing input
    # hashes / run ids are byte-stable (D42 hash policy) — a SET list enters the hash, as a real
    # input must.
    local_flex_awards: list[AvailabilityCommitment] | None = None
    # Additive, optional (D44, Olly 2026-08-12): the recorded stand-ins a run consumed where an
    # input was absent — each naming what was missing, the value used, its basis and sources, the
    # confidence ceiling it imposes and the exact confirmation ask. **No missing input blocks a
    # run**; it is recorded here instead and stated in the result and the report as requiring
    # confirmation. A `pending_permission` record is a permission, never a derived fact, and can
    # never be written as consent granted (`gridq.schemas.derived`).
    #
    # Only values that were actually DERIVED OFFLINE and consumed by the run belong here — they are
    # real inputs, so they enter `input_hash` and two runs on different derived capacities can never
    # share a `run_id`. A gate's transient observation ("this series' provenance is undeclared") is
    # NOT an input and must never be written here: it would move the identity of every existing
    # site. Default None so every existing site validates unchanged;
    # `quotes.assembly._canonical_run_input` POPS the field when None so existing input hashes /
    # run ids are byte-stable (D42 hash policy) — a SET list enters the hash, as a real input must.
    derived_assumptions: list[DerivedAssumption] | None = None
    # IC-12 2.0.0 (WP-CORE-03): IC-4 evidence objects this site's inputs rest on, each by content
    # hash with what it supports. A reference is a real input: a set tuple is dumped and enters
    # ``input_hash``. ``None`` (and empty, normalised to ``None``) is absent from every dump, so
    # every pre-2.0.0 site keeps its identity. Unlike the D42 fields above, the omission is done
    # by the model's own serialiser, so it holds for every hash site and not only for
    # `quotes.assembly._canonical_site_dump`.
    evidence_refs: tuple[EvidenceLink, ...] | None = None
    source_references: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    @model_serializer(mode="wrap")
    def _dump_without_unset_growth(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(handler(self), ("evidence_refs",))

    @model_validator(mode="after")
    def _canonicalise_evidence_refs(self) -> SiteModel:
        """Order the evidence links, refuse duplicates, collapse empty to None (IC-12 2.0.0)."""
        self.evidence_refs = canonical_evidence_links(self.evidence_refs)
        return self

    @model_validator(mode="after")
    def _canonicalise_stream_consents(self) -> SiteModel:
        """Reject duplicate-stream consents and canonicalise the ordering.

        A duplicate stream is a caller error (a ValueError, never a silent dedup). When the list
        is valid it is sorted deterministically by ``stream`` so the same consents in any input
        order produce a byte-identical canonical run input / hash.
        """
        if self.stream_consents:
            seen = [c.stream for c in self.stream_consents]
            if len(seen) != len(set(seen)):
                raise ValueError("stream_consents must carry at most one entry per stream.")
            self.stream_consents = tuple(sorted(self.stream_consents, key=lambda c: c.stream.value))
        return self

    @model_validator(mode="after")
    def _derived_vat_basis_is_recorded(self) -> SiteModel:
        """A VAT basis marked ``derived`` must have its `DerivedAssumption` on this site (D44/D45).

        `TariffBasis.vat_attestation` = ``"derived"`` says nobody stated whether the rates include
        VAT; the basis was inferred (H-01: a VAT-registered business is priced net of VAT). That
        inference is an assumption, so it is recorded where every assumption is, keyed
        ``tariff_stack.basis.vat``, and reaches the report's confirmation section. Without the
        record the derived basis would read as a stated one (WP-UW-07, D103).
        """
        if self.tariff_stack.basis.vat_attestation == "derived":
            keys = {record.key for record in self.derived_assumptions or ()}
            if "tariff_stack.basis.vat" not in keys:
                raise ValueError(
                    "tariff_stack.basis.vat_attestation is 'derived' but the site records no "
                    "DerivedAssumption on 'tariff_stack.basis.vat'; a derived VAT basis must be "
                    "declared, with its direction of error, or stated by the record."
                )
        return self

    @model_validator(mode="after")
    def _normalise_derived_assumptions(self) -> SiteModel:
        """Collapse an empty derived-assumption list to None (D44 + D42 hash policy).

        ``[]`` and absent carry identical information — no input was derived — but only ``None`` is
        popped from the canonical run input, so an empty list would shift ``input_hash`` for zero
        information. Normalising here closes that silent identity-drift path at the schema.
        """
        if self.derived_assumptions is not None and not self.derived_assumptions:
            self.derived_assumptions = None
        return self

    @property
    def assumptions_requiring_confirmation(self) -> tuple[DerivedAssumption, ...]:
        """Every recorded derived input / pending permission this site's run consumed.

        The report's "Assumptions requiring confirmation" section reads this; the two classes carry
        different report language (`DerivedAssumption.report_label`) and must never be merged into
        one sentence.
        """
        return tuple(self.derived_assumptions or ())

    @property
    def consented_streams(self) -> frozenset[MarketStream]:
        """The set of streams the customer explicitly consented to at intake."""
        return frozenset(c.stream for c in (self.stream_consents or ()) if c.consented)

    def agreed_rate_for(self, stream: MarketStream) -> float | None:
        """The generic agreed rate (£) recorded for ``stream``, or None if none was captured."""
        for c in self.stream_consents or ():
            if c.stream == stream:
                return c.agreed_rate_gbp
        return None

    @property
    def is_personal_data(self) -> bool:
        """Whether this site's data must be treated as personal data.

        Sole-trader sites are personal data by the System Brief's non-functional floor. Any other
        entity type (incl. the default ``UNKNOWN`` and ``PARTNERSHIP``) is not auto-classified —
        set ``confidentiality.personal_data_override=True`` to record those cases (e.g. an
        unincorporated partnership that is personal data under UK GDPR). The override wins either
        way, so a sole trader can also be recorded as explicitly not-personal.
        """
        override = self.confidentiality.personal_data_override
        if override is not None:
            return override
        return self.counterparty.legal_entity_type == LegalEntityType.SOLE_TRADER

    @model_validator(mode="after")
    def _check_tier_data(self) -> SiteModel:
        if self.data_tier == DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY:
            if not self.load_data.has_hh_coverage:
                raise ValueError(
                    "T2 requires half-hourly coverage metadata "
                    "(load_data.half_hourly_rows, start_date and end_date)."
                )
        if self.data_tier == DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE:
            if self.annual_consumption_kwh is None and self.load_data.annual_kwh is None:
                raise ValueError(
                    "T0 requires annual_consumption_kwh (or load_data.annual_kwh) to be set."
                )
        return self
