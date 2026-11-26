"""Lifecycle inputs and the lifecycle record (WP-UW-17, `lifecycle_cashflow_model`).

Before WP-UW-17 the cashflow model was an annual series that started at the commercial operation
date (COD) with the whole capex in year 0 and ended at the horizon with nothing after it. Four
things a lender reads first were absent, and each was only disclosed (C-7):

* **Construction.** No build period and no interest during construction (IDC), so the debt at COD
  and the time value of the equity spent before COD were both missing (H-16).
* **COD delay.** `Scenario.cod_delay_months` existed but no production caller set it, and the
  sensitivity table withheld any delayed row because nothing applied the delay (N18).
* **Indexation.** `financing.cpi_pct_per_year` had no reader, and the T-4 Capacity Market payment,
  which the regulations index to CPI, was held flat (N13, H-15, D-33).
* **Terminal, decommissioning and replacement flows** were named as omitted, never modelled (N14).

`LifecycleInputs` is the one typed carrier for the facts those need. Like `LenderTerms` it is
frozen, carries provenance, and **defaults no figure**: a construction programme, a drawdown
profile, an IDC rate, a decommissioning cost or a replacement schedule is a user input with a
source, or it is absent and the record says what is missing (the data rule; D45). Each block is
optional and independent, so a run can supply a construction programme without a terminal-value
basis, and the flows it does not supply stay excluded with a labelled warning.

`LifecycleRecord` is the derived side: what the run actually did with those inputs and with the
assumption library's CPI key. Production populates it on every run, and it is excluded from the
quote's canonical hash because every figure in it is determined by inputs the hash already carries
(D132, the D78 rule). The input itself, `FinancialCase.lifecycle_inputs`, stays in the hash
under the pop-if-None rule, so two different programmes can never share a quote_id.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from gridq.schemas.provenance import GridQModel

__all__ = [
    "CapacityAgreementIndexation",
    "CashflowLineBasis",
    "CmAuctionType",
    "ConstructionPhase",
    "ConstructionRecord",
    "ConstructionStatus",
    "DatedFlowEntry",
    "Decommissioning",
    "DrawdownPoint",
    "IndexationRecord",
    "LifecycleFlow",
    "LifecycleFlowEntry",
    "LifecycleFlowStatus",
    "LifecycleInputs",
    "LifecycleProvenance",
    "LifecycleRecord",
    "LifecycleSource",
    "LineBasisEntry",
    "PriceBasis",
    "Replacement",
    "ReplacementComponent",
    "ReturnBasis",
    "TerminalValue",
    "TerminalValueBasis",
]

_FROZEN = ConfigDict(extra="forbid", frozen=True)

#: Tolerance on a drawdown profile summing to 1. A profile is a set of user-stated fractions; a
#: rounding residue in the last decimal is not a different programme.
DRAWDOWN_SUM_TOLERANCE = 1e-9


# --------------------------------------------------------------------------- inputs


class LifecycleSource(StrEnum):
    """Where the lifecycle facts came from."""

    #: The EPC / construction contract programme.
    CONTRACT_PROGRAMME = "contract_programme"
    #: An EPC or supplier quotation.
    EPC_QUOTE = "epc_quote"
    #: A lender's indication (a construction-facility rate, a reserve requirement).
    LENDER_INDICATION = "lender_indication"
    #: The owner's own statement (a host-site agreement obligation, a planned replacement).
    OWNER_STATEMENT = "owner_statement"
    #: Made up for a test or a demonstration; never a real programme, and labelled as such.
    SYNTHETIC = "synthetic"


class LifecycleProvenance(GridQModel):
    """Who stated the lifecycle facts, and on what. Travels with the inputs onto the record."""

    model_config = _FROZEN

    source: LifecycleSource
    reference: str = Field(
        min_length=1,
        description=(
            "Programme / quotation / agreement id and date, or what synthetic inputs are for."
        ),
    )
    as_of: date | None = Field(default=None, description="Date the facts were stated, if known.")
    evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the source document, when one is held.",
    )


class PriceBasis(StrEnum):
    """The money a user-stated £ is expressed in (D131, R2).

    ``real_at_as_of`` is money of the ``as_of`` date, which the model takes to be its year-1 price
    base (the base every library £ is stated on): on a CPI-indexed run it escalates by
    ``(1 + c)^(t − 1)`` in operating year ``t``, and no escalation is applied between ``as_of`` and
    COD (a delay included). ``nominal_as_entered`` is the money of the year the flow falls in and
    is never escalated.
    """

    REAL_AT_AS_OF = "real_at_as_of"
    NOMINAL_AS_ENTERED = "nominal_as_entered"


def _reject_fraction_rate(value: float | None) -> None:
    """The F-52 percent-not-fraction guard for a construction rate (same rule as `LenderTerms`)."""
    if value is not None and 0.0 < value < 1.0:
        raise ValueError(
            f"idc_rate_pct={value} looks like a FRACTION written into a percent field: "
            f"{value * 100:g}% is 'idc_rate_pct={value * 100:g}'. A fraction here prices the "
            "construction interest 100 times too cheaply."
        )


class DrawdownPoint(GridQModel):
    """One capex draw: a fraction of the capex spent a whole number of months before COD."""

    model_config = _FROZEN

    months_before_cod: int = Field(ge=0, description="Months before planned COD; 0 = at COD.")
    capex_fraction: float = Field(gt=0.0, le=1.0, description="Fraction of total capex drawn.")


class ConstructionPhase(GridQModel):
    """The construction programme: its length, the dated capex draws and the construction rate.

    The drawdown profile is REQUIRED and has no default shape: a uniform or S-curve profile would
    be an invented programme. ``idc_rate_pct`` may be omitted; the run then uses the supplied debt
    terms' rate with a labelled warning, or excludes IDC with a warning when there is none
    (D130, R4).
    """

    model_config = _FROZEN

    construction_months: int = Field(gt=0, description="Length of the build, in whole months.")
    drawdown: tuple[DrawdownPoint, ...] = Field(min_length=1)
    idc_rate_pct: float | None = Field(
        default=None, ge=0.0, description="Construction-facility interest rate, percent per year."
    )

    @model_validator(mode="after")
    def _coherent(self) -> ConstructionPhase:
        _reject_fraction_rate(self.idc_rate_pct)
        months = [p.months_before_cod for p in self.drawdown]
        if len(set(months)) != len(months):
            raise ValueError("drawdown months must be unique; merge draws in the same month")
        if max(months) > self.construction_months:
            raise ValueError(
                f"a draw at {max(months)} months before COD lies before the "
                f"{self.construction_months}-month construction period starts"
            )
        total = sum(p.capex_fraction for p in self.drawdown)
        if abs(total - 1.0) > DRAWDOWN_SUM_TOLERANCE:
            raise ValueError(
                f"drawdown capex fractions sum to {total:.12g}, not 1: the profile must account "
                "for the whole capex, and none of it is filled in here"
            )
        return self


class CmAuctionType(StrEnum):
    """The Capacity Market auction an agreement was won in (SI 2014/2043 Sch 1 para 3)."""

    #: T-4: PE = CCP x CPI_x / CPI_base (para 3(5)), CPI-indexed from the auction's base period.
    T_4 = "T-4"
    #: T-1: un-indexed under para 3(6) until 23.7.2025 (omitted by SI 2025/917; the current text
    #: states no T-1 formula). Held flat, with that stated.
    T_1 = "T-1"


class CapacityAgreementIndexation(GridQModel):
    """The facts para 3(5) needs to index a T-4 payment from year 1 (D129).

    ``cpi_base`` is the average monthly CPI over the auction's base period (reg 2, reg 11(1)(f));
    ``cpi_x_year1`` the average over the winter ending 30 April before the first delivery year.
    Both are ONS CPI (All Items) index values, stated by the user with a source: both or neither.
    Without them the year-1 uplift is 1.0 and labelled as such, never invented.
    """

    model_config = _FROZEN

    auction_type: CmAuctionType
    delivery_year: str = Field(
        pattern=r"^\d{4}-\d{2}$", description="First delivery year, e.g. '2029-30'."
    )
    cpi_base: float | None = Field(default=None, gt=0.0)
    cpi_x_year1: float | None = Field(default=None, gt=0.0)
    reference: str = Field(min_length=1, description="The agreement / register entry / ONS series.")

    @model_validator(mode="after")
    def _both_or_neither(self) -> CapacityAgreementIndexation:
        if (self.cpi_base is None) != (self.cpi_x_year1 is None):
            raise ValueError(
                "state cpi_base and cpi_x_year1 together (the year-1 uplift is their ratio), or "
                "neither"
            )
        return self


class TerminalValueBasis(StrEnum):
    """How the residual value at the horizon is stated. No perpetuity: it would invent a growth
    rate for a finite asset sized for its end of life (D131)."""

    USER_GBP_AT_HORIZON = "user_gbp_at_horizon"
    RESIDUAL_FRACTION_OF_CAPEX = "residual_fraction_of_capex"
    #: A stated nil residual: MODELLED as zero, which is a statement, not an omission.
    DECLARED_NIL = "declared_nil"


class TerminalValue(GridQModel):
    """The residual value at the horizon, received in the final operating year."""

    model_config = _FROZEN

    basis: TerminalValueBasis
    amount_gbp: float | None = Field(default=None, ge=0.0)
    fraction_of_capex: float | None = Field(default=None, ge=0.0, le=1.0)
    #: The cautious-case residual. Absent = nil in P90 (no downside residual is invented).
    p90_amount_gbp: float | None = Field(default=None, ge=0.0)
    price_basis: PriceBasis

    @model_validator(mode="after")
    def _exactly_what_the_basis_needs(self) -> TerminalValue:
        need = {
            TerminalValueBasis.USER_GBP_AT_HORIZON: ("amount_gbp",),
            TerminalValueBasis.RESIDUAL_FRACTION_OF_CAPEX: ("fraction_of_capex",),
            TerminalValueBasis.DECLARED_NIL: (),
        }[self.basis]
        for name in ("amount_gbp", "fraction_of_capex"):
            present = getattr(self, name) is not None
            if name in need and not present:
                raise ValueError(f"terminal value basis '{self.basis.value}' needs {name}")
            if name not in need and present:
                raise ValueError(f"terminal value basis '{self.basis.value}' does not take {name}")
        if self.basis is TerminalValueBasis.DECLARED_NIL and self.p90_amount_gbp:
            raise ValueError("a declared-nil residual cannot carry a P90 residual")
        return self


class Decommissioning(GridQModel):
    """The decommissioning / site-restoration obligation (from the host-site agreement)."""

    model_config = _FROZEN

    cost_gbp: float = Field(ge=0.0)
    year: int | Literal["horizon_end"] = Field(
        description="Operating year the cost falls in (1-based), or 'horizon_end'."
    )
    net_of_salvage: bool = Field(description="True when cost_gbp is already net of salvage.")
    price_basis: PriceBasis

    @model_validator(mode="after")
    def _year_positive(self) -> Decommissioning:
        if isinstance(self.year, int) and self.year < 1:
            raise ValueError("decommissioning year is an operating year, 1 or later")
        return self


class ReplacementComponent(StrEnum):
    """What is replaced. Battery modules are NOT a component here: replacing cells is
    augmentation, and the model sizes the asset on day one for end-of-life capability."""

    INVERTER_PCS = "inverter_pcs"
    BALANCE_OF_PLANT = "balance_of_plant"
    TRANSFORMER = "transformer"
    HVAC_THERMAL = "hvac_thermal"
    EMS_CONTROLS = "ems_controls"
    OTHER = "other"


class Replacement(GridQModel):
    """One scheduled component replacement."""

    model_config = _FROZEN

    year: int = Field(ge=1, description="Operating year the replacement falls in (1-based).")
    component: ReplacementComponent
    description: str | None = None
    cost_gbp: float = Field(gt=0.0)
    price_basis: PriceBasis

    @model_validator(mode="after")
    def _other_is_described(self) -> Replacement:
        if self.component is ReplacementComponent.OTHER and not (self.description or "").strip():
            raise ValueError("a replacement of component 'other' must say what it is")
        return self


class LifecycleInputs(GridQModel):
    """The lifecycle facts for one site, as stated. Every block is optional and independent; none
    is defaulted. See the module docstring.

    ``cod_delay_months`` is the delay to STRESS in the scenario table (a zero-weight ``cod_delay``
    row), not a delay applied to the base case. ``replacements=()`` is a declared "no replacement",
    which is modelled; ``None`` is "not stated", which is excluded and asked for.
    """

    model_config = _FROZEN

    provenance: LifecycleProvenance
    construction: ConstructionPhase | None = None
    cod_delay_months: int | None = Field(default=None, gt=0)
    capacity_agreement: CapacityAgreementIndexation | None = None
    terminal_value: TerminalValue | None = None
    decommissioning: Decommissioning | None = None
    replacements: tuple[Replacement, ...] | None = None

    @model_validator(mode="after")
    def _states_something(self) -> LifecycleInputs:
        blocks = (
            self.construction,
            self.cod_delay_months,
            self.capacity_agreement,
            self.terminal_value,
            self.decommissioning,
            self.replacements,
        )
        if all(b is None for b in blocks):
            raise ValueError(
                "lifecycle inputs state nothing: supply at least one of construction, "
                "cod_delay_months, capacity_agreement, terminal_value, decommissioning or "
                "replacements, or omit the inputs"
            )
        return self

    def label(self) -> str:
        """One line naming the inputs and their source, for run summaries."""
        parts = [
            name
            for name, block in (
                ("construction", self.construction),
                ("cod_delay", self.cod_delay_months),
                ("cm_agreement", self.capacity_agreement),
                ("terminal_value", self.terminal_value),
                ("decommissioning", self.decommissioning),
                ("replacements", self.replacements),
            )
            if block is not None
        ]
        return f"{', '.join(parts)} ({self.provenance.source.value}: {self.provenance.reference})"


# --------------------------------------------------------------------------- derived record


class CashflowLineBasis(StrEnum):
    """The money each cashflow line is stated in (D129): every line says real or nominal."""

    #: No CPI key on the release: every line flat, on the declared "neither real nor nominal" basis.
    FLAT_NOMINAL_UNESCALATED = "flat_nominal_unescalated"
    #: Nominal and deliberately NOT escalated (energy-price lines, merchant spreads, capex at COD).
    NOMINAL_FLAT = "nominal_flat"
    #: Nominal, escalated at the library CPI rate from year 1.
    NOMINAL_CPI_ESCALATED = "nominal_cpi_escalated"
    #: A T-4 Capacity Market payment in term, indexed per SI 2014/2043 Sch 1 para 3(5).
    NOMINAL_CM_CPI_INDEXED = "nominal_cm_cpi_indexed"
    #: A user £ stated in real money on a run with no CPI key: held flat, and labelled so.
    REAL_HELD_FLAT = "real_held_flat"


class LineBasisEntry(GridQModel):
    """One cashflow line and its basis."""

    line: str
    basis: CashflowLineBasis


class IndexationRecord(GridQModel):
    """What the run did with `financing.cpi_pct_per_year` and the CM indexation rule."""

    cashflow_basis: str
    cpi_state: Literal["absent", "null", "numeric"]
    cpi_pct_per_year: float | None = None
    line_basis: list[LineBasisEntry] = Field(default_factory=list)
    #: The P50 pre-debt series deflated to year-1 money (annual, COD-anchored). None when no index.
    real_p50_fcf_gbp: list[float] | None = None
    real_project_irr_pct: float | None = None
    real_return_basis: str | None = None
    cm_auction_type: str | None = None
    cm_year1_uplift: float | None = None
    cm_uplift_source: str | None = None
    cm_base_period_label: str | None = None


class ConstructionStatus(StrEnum):
    MODELLED = "modelled"
    EXCLUDED_NO_INPUT = "excluded_no_input"


class ReturnBasis(StrEnum):
    """The timeline the headline IRRs were measured on."""

    #: Annual, year 0 = COD, capex at COD (the historical basis; no construction, no delay).
    ANNUAL_COD_ANCHORED = "annual_cod_anchored"
    #: Month-dated: construction draws before COD, operations from the (possibly delayed) COD.
    DATED_MONTHLY = "dated_monthly"


class DatedFlowEntry(GridQModel):
    """One month-dated cashflow (month 0 = planned COD)."""

    month: int
    gbp: float


class ConstructionRecord(GridQModel):
    """What the run did about construction, IDC and the COD delay (D130)."""

    status: ConstructionStatus
    return_basis: ReturnBasis
    construction_months: int | None = None
    cod_delay_months: int = 0
    accumulation_factor: float | None = None
    idc_gbp: float | None = None
    idc_rate_pct: float | None = None
    idc_rate_source: str | None = None
    principal_at_cod_gbp: float | None = None
    cod_anchored_irr_pct: float | None = None
    cod_anchored_equity_irr_pct: float | None = None
    dated_p50_project_flows: list[DatedFlowEntry] | None = None
    dated_p50_equity_flows: list[DatedFlowEntry] | None = None


class LifecycleFlow(StrEnum):
    TERMINAL_VALUE = "terminal_value"
    DECOMMISSIONING = "decommissioning"
    REPLACEMENT = "replacement"


class LifecycleFlowStatus(StrEnum):
    MODELLED = "modelled"
    EXCLUDED_NO_INPUT = "excluded_no_input"
    EXCLUDED_BEYOND_HORIZON = "excluded_beyond_horizon"


class LifecycleFlowEntry(GridQModel):
    """One row of the lifecycle capital-flow ledger (D131)."""

    flow: LifecycleFlow
    status: LifecycleFlowStatus
    #: Signed £ per operating year 1..N (positive = inflow). Empty when the flow is excluded.
    amounts_by_year_p50_gbp: list[float] = Field(default_factory=list)
    amounts_by_year_p90_gbp: list[float] = Field(default_factory=list)
    basis_label: str
    price_basis: PriceBasis | None = None
    escalated: bool = False
    in_cfads: bool = False
    provenance: str | None = None
    data_request: str | None = None


class LifecycleRecord(GridQModel):
    """The derived lifecycle record on every `FinancialCase` (WP-UW-17)."""

    indexation: IndexationRecord
    construction: ConstructionRecord
    flows: list[LifecycleFlowEntry]
