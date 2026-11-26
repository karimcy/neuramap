"""Closed enumerations shared across the GridQ schema layer.

All are ``(str, Enum)`` so JSON values serialise as stable strings. An unknown value
is a validation error, not a pass-through.
"""

from __future__ import annotations

from enum import StrEnum


class DataTier(StrEnum):
    """Data quality tier of a site's load information."""

    T0_ANNUAL_KWH_TARIFF_POSTCODE = "T0_ANNUAL_KWH_TARIFF_POSTCODE"
    T1_PARTIAL_HALF_HOURLY = "T1_PARTIAL_HALF_HOURLY"
    T2_VALIDATED_12_MONTH_HALF_HOURLY = "T2_VALIDATED_12_MONTH_HALF_HOURLY"


class HalfHourlyBasis(StrEnum):
    """How the half-hourly series underwriting priced was obtained (validation Gate 2).

    Lives in the schema layer, not in `gridq.ingestion.hh_mandate` where the gate is, because the
    basis has to be **persisted on the QuoteRecord** and read by downstream gates (N21). Before
    that it existed only as a word interpolated into a warning string, so no gate could branch on
    it: a modelled-basis run and a metered run were treated identically by every downstream check.
    `gridq.ingestion.hh_mandate` re-exports this name, so the gate's own vocabulary is unchanged.
    """

    #: The site's own metered half-hourly export. The only basis that can reach High confidence.
    METERED = "metered"
    #: A sourced reference profile scaled to the site's own reconciled totals. Legitimate and
    #: mandatory where no meter export exists; caps the confidence band at Medium.
    MODELLED = "modelled"
    #: Metered data covering part of the period, with the remainder modelled. The split is
    #: recorded; the modelled portion caps the band exactly as `MODELLED` does.
    MIXED = "mixed"


class ConfidenceBand(StrEnum):
    """How much weight a validation result can carry. Ordered worst to best.

    Evidential strength — never a probability, never P50/P90, never a hurdle
    (`docs/validation_requirements.md` §2). Defined here rather than in `gridq.quotes.confidence`
    so the band and the Gate-2 ceiling can be **persisted on the QuoteRecord** and enforced by the
    lifecycle gate (N21); `gridq.quotes.confidence` re-exports it, so its public name is unchanged.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]

    @classmethod
    def weakest(cls, bands: list[ConfidenceBand]) -> ConfidenceBand:
        return min(bands, key=lambda b: b.rank) if bands else cls.LOW


class MarketStream(StrEnum):
    """The value-stream registry (the vocabulary of revenue/cost streams)."""

    PPA_OR_BILL_SAVINGS = "PPA_OR_BILL_SAVINGS"
    TARIFF_ARBITRAGE = "TARIFF_ARBITRAGE"
    DUOS_RED_BAND_AVOIDANCE = "DUOS_RED_BAND_AVOIDANCE"
    MIC_ASC_HEADROOM = "MIC_ASC_HEADROOM"
    CAPACITY_MARKET = "CAPACITY_MARKET"
    LOAD_SERVICE = "LOAD_SERVICE"
    LOCAL_FLEX_PICLO = "LOCAL_FLEX_PICLO"
    WHOLESALE_P415 = "WHOLESALE_P415"
    BALANCING_MECHANISM = "BALANCING_MECHANISM"
    ANCILLARY_DC_DM_DR = "ANCILLARY_DC_DM_DR"
    #: NESO's Demand Flexibility Service (E-06). Added because it is the **only** direct NESO
    #: revenue route below 1 MW: the Balancing Mechanism, Balancing Reserve and all six DC/DM/DR
    #: products impose a 1 MW minimum unit size, so once those gates are correctly enforced (D-92)
    #: every sub-1 MW site — which is all five real sites in the live campaign — has no NESO route
    #: at all without this one. DFS's own minimum Registered Unit Capacity is 0.1 MW.
    DFS = "DFS"
    #: The avoided AGREED-CAPACITY (ASC/MIC) charge and the avoided TCR fixed residual band that
    #: follow a peak reduction (MICASC-3, Olly, 2026-08-17). Deliberately NOT folded into
    #: ``MIC_ASC_HEADROOM``: that stream is a `FloorStream` (OPEN ⇒ FIRM) and this money is
    #: CONDITIONAL — it needs the customer to re-negotiate agreed capacity with the DNO — so
    #: sharing the id would put a conditional amount inside the priced floor. The two are also
    #: gated on different facts: MIC/ASC headroom needs the connection to BIND, this needs a
    #: shaveable PEAK, and those are different sites.
    AGREED_CAPACITY_REDUCTION = "AGREED_CAPACITY_REDUCTION"


class Firmness(StrEnum):
    """How firm a value stream is in the priced case."""

    FIRM = "firm"
    CONTRACTED = "contracted"
    CONDITIONAL = "conditional"
    EXCLUDED = "excluded"


class QuoteMode(StrEnum):
    """Quote lifecycle state."""

    DRAFT = "draft"
    INDICATIVE = "indicative"
    UNDERWRITTEN = "underwritten"
    CONTRACTED = "contracted"
    OPERATING = "operating"
    LAPSED = "lapsed"


class ImplementationLevel(StrEnum):
    """How real the optimiser that produced a result is."""

    BASELINE = "baseline"
    IN_PROGRESS = "in_progress"
    PRODUCTION = "production"


class BankableStatus(StrEnum):
    """Whether a result is fit for a lender's review."""

    NOT_BANKABLE = "not_bankable"
    REVIEW_REQUIRED = "review_required"
    BANKABLE_CANDIDATE = "bankable_candidate"


class SolverStatus(StrEnum):
    """Outcome reported by the optimiser/mock."""

    BASELINE = "baseline"
    MOCK = "mock"
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    INSUFFICIENT_DATA = "insufficient_data"


class SelectionStatus(StrEnum):
    """What the sizing selection concluded (distinct from the solver's numerical status).

    A dispatch can solve OPTIMAL at every candidate size and the run can still end with no
    recommendable system: when no candidate produces any return metric (project IRR or levered
    return), the honest verdict is ``no_viable_configuration`` — a first-class economics verdict,
    never a smallest-grid-corner "recommendation" (the pre-2026-07-07 failure mode). Additive and
    optional on `OptimisationResult` so historical records (``None``) still load.
    """

    SELECTED = "selected"
    NO_VIABLE_CONFIGURATION = "no_viable_configuration"
    INSUFFICIENT_DATA = "insufficient_data"
    INFEASIBLE = "infeasible"


class SolveMode(StrEnum):
    """Which engine an OptimisationRequest is targeting."""

    BASELINE = "baseline"
    MOCK = "mock"
    PRODUCTION = "production"


class ConsentStatus(StrEnum):
    """State of a single grid/supplier consent.

    The per-site bundle of consents is the ``ConsentState`` *model* in ``consents.py``,
    whose fields use these values.
    """

    NOT_STARTED = "not_started"
    APPLIED = "applied"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"


class VoltageLevel(StrEnum):
    """Connection voltage level."""

    LV = "LV"
    HV = "HV"
    EHV = "EHV"


class ExportRoute(StrEnum):
    """How a site's exported / flexible energy reaches a market (route-to-market).

    Recorded on the ``ExportArrangement`` *model* (``export_arrangement.py``). ``UNKNOWN`` is
    the honest default until the arrangement is evidenced; ``NONE`` means the site deliberately
    has no export / route-to-market. This vocabulary prices nothing — it is a data contract only.
    """

    NONE = "none"
    PPA_EXPORT = "ppa_export"
    FLEXIBILITY_AGGREGATOR = "flexibility_aggregator"
    UNKNOWN = "unknown"


class CreditStatus(StrEnum):
    """Coarse counterparty credit standing (the deterministic mapping lives in finance)."""

    UNKNOWN = "unknown"
    NOT_ASSESSED = "not_assessed"
    STRONG = "strong"
    ADEQUATE = "adequate"
    WEAK = "weak"


class LegalEntityType(StrEnum):
    """The counterparty's legal form. ``SOLE_TRADER`` sites are treated as personal data."""

    UNKNOWN = "unknown"
    LIMITED_COMPANY = "limited_company"
    PLC = "plc"
    LLP = "llp"
    PARTNERSHIP = "partnership"
    SOLE_TRADER = "sole_trader"
    PUBLIC_SECTOR = "public_sector"
    CHARITY = "charity"
    OTHER = "other"


class NdaStatus(StrEnum):
    """Recorded state of the NDA under which a site's half-hourly data is held."""

    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"
    REQUESTED = "requested"
    SIGNED = "signed"
    EXPIRED = "expired"


class QuoteOutcome(StrEnum):
    """The commercial outcome of a quote (system or manual benchmark) — the telemetry hit-rate
    axis. ``PENDING`` until the deal resolves."""

    PENDING = "pending"
    SENT = "sent"
    WON = "won"
    LOST = "lost"
    ABANDONED = "abandoned"


class Confidence(StrEnum):
    """Confidence in a piece of input data."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PriceStructure(StrEnum):
    """Commercial structure of the price recommendation."""

    FIXED_GBP_PER_KWH = "fixed_gbp_per_kwh"
    SAVINGS_SHARE = "savings_share"
    CAPACITY_FEE = "capacity_fee"
    # W9: a bill-guarantee product ("your bill will not exceed X"). Structure-only until the
    # M&V method (boundary Open Q4) and the volume-variance keys are real — see finance.pricing.
    CAPPED_BILL = "capped_bill"
