"""FinancialCase — the underwriting output around an OptimisationResult.

P50/P90 cashflows and DSCR are mandatory; point estimates are not deliverables. No
financial maths is implemented here — only the shape the finance module will populate.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from gridq.schemas.enums import PriceStructure
from gridq.schemas.lender_terms import CashflowCase, DscrStatus, LenderTerms
from gridq.schemas.lifecycle import LifecycleInputs, LifecycleRecord
from gridq.schemas.provenance import GridQModel


class DscrSummary(GridQModel):
    """Debt Service Coverage Ratio summary (computed on the P90 case unless lender terms say not).

    ``status`` / ``unavailable_reason`` / ``cashflow_case`` are additive (WP-UW-03, D75). Every run
    the model produces sets ``status``, and sets ``unavailable_reason`` whenever ``min_dscr`` is
    ``None``, so a missing ratio always carries its cause on the record instead of reading as
    silence. They are ``None`` only on records written before D75. They are derived from inputs the
    quote identity already pins, so they are excluded from the canonical hash input (D78).
    """

    min_dscr: float | None = None
    by_year: list[float] = Field(default_factory=list)
    basis: str = "P90"
    status: DscrStatus | None = None
    unavailable_reason: str | None = None
    cashflow_case: CashflowCase | None = None


class DebtAssumptions(GridQModel):
    """Per-site debt assumptions (placeholder in v1; portfolio finance is Sim's).

    **Percent, not fraction.** Every ``*_pct`` field here is a percentage: 60% gearing is
    ``60.0``, not ``0.6``. The distinction is enforced below because the ``[0, 100]`` bound alone
    cannot catch it — ``0.6`` is a perfectly valid 0.6%, so a fraction written by mistake sails
    through validation and sizes the debt **100× too small**, silently taking the DSCR, the
    covenant verdict and the equity IRR with it (hostile review F-52).
    """

    gearing_pct: float | None = Field(default=None, ge=0, le=100)
    rate_pct: float | None = Field(default=None, ge=0)
    tenor_years: float | None = Field(default=None, ge=0)
    sculpting_basis: str | None = None
    #: WP-UW-03 (D77), additive: the principal actually lent and which bound set it, ``"gearing"``
    #: (capex x the gearing cap) or ``"dscr_target"`` (the case CFADS at the sculpting target).
    #: ``None`` when no debt was sized. Derived, so excluded from the quote hash (D78).
    principal_gbp: float | None = Field(default=None, ge=0)
    sized_by: str | None = None

    @model_validator(mode="after")
    def _percent_not_fraction(self) -> DebtAssumptions:
        """Reject values that are almost certainly fractions written into a percent field.

        A real project gearing between 0% and 1% does not exist, and neither does a cost of debt
        in that range — so the interval is safe to treat as a mis-entered fraction rather than a
        real, very small percentage. Exactly zero stays legal (all-equity / zero-rate).
        """
        for name in ("gearing_pct", "rate_pct"):
            value = getattr(self, name)
            if value is not None and 0.0 < value < 1.0:
                raise ValueError(
                    f"{name}={value} looks like a FRACTION written into a percent field. "
                    f"These fields are percentages: {value * 100:g}% is "
                    f"'{name}={value * 100:g}', not '{name}={value}'. A fraction here sizes debt "
                    "100x too small and silently corrupts the DSCR, the covenant verdict and the "
                    "equity IRR. If you genuinely mean a sub-1% value, state it explicitly in the "
                    "assumption record — it is not a value this schema will infer."
                )
        return self


class PriceRecommendation(GridQModel):
    """The recommended price/structure that hits the target return.

    ``counterfactual_import_gbp_per_kwh`` (additive, 2026-07-07) is the customer's current
    blended import energy cost (mean of the tariff's import rates plus £/kWh levy lines — a
    disclosed blended-rate approximation, the same convention the parasitic-load cost line
    uses). ``exceeds_counterfactual`` is True when the price required to hit the target return
    is ABOVE what the customer already pays per kWh — i.e. the project is not commercially
    viable as a fixed PPA and the number must never be presented as an offer. ``None`` on
    historical records and where either side is unavailable.
    """

    structure: PriceStructure = PriceStructure.FIXED_GBP_PER_KWH
    value_gbp_per_kwh: float | None = Field(default=None, ge=0)
    hits_target_return: bool | None = None
    counterfactual_import_gbp_per_kwh: float | None = Field(default=None, ge=0)
    exceeds_counterfactual: bool | None = None
    #: WP-UW-17 (D128), additive and derived (excluded from the quote hash, D78 rule): how
    #: the price was levelised — ``"annual"`` (the run's own P50 series at the target rate over the
    #: modelled delivered volume), ``"dated"`` (the same on the month-dated construction timeline),
    #: or ``"legacy_year1_flat"`` (a caller that supplied no series). None on historical records.
    levelisation_basis: str | None = None
    #: The IRR re-measured WITH the recommended price revenue added (closed loop): the equity IRR
    #: when a debt structure resolved, else the project IRR. What `hits_target_return` compares.
    measured_irr_with_price_pct: float | None = None
    #: Diagnostic only, never the headline: the price at which the EQUITY IRR (after the actual
    #: debt service) equals the target. None without debt or when it cannot be bracketed.
    equity_closed_loop_gbp_per_kwh: float | None = Field(default=None, ge=0)


class CovenantStatus(GridQModel):
    """DSCR covenant check (on the P90 case unless the lender terms name another).

    ``covenant`` is the default (event-of-default) threshold. The additive fields (WP-UW-03, D76;
    the tiers half of H-05) carry what one key could not: ``source`` says whether the thresholds
    came from the supplied lender terms or the assumption library; ``lock_up_dscr`` /
    ``lock_up_passes`` are the distribution lock-up tier; ``not_evaluated_reason`` says why
    ``passes`` is ``None``. All are derived from inputs the quote identity already pins, so
    excluded from its hash (D78).
    """

    covenant: float | None = None
    min_p90_dscr: float | None = None
    passes: bool | None = None
    source: str | None = None
    lock_up_dscr: float | None = None
    lock_up_passes: bool | None = None
    not_evaluated_reason: str | None = None


class CapitalRecoveryAccount(GridQModel):
    """The **always-defined** return account (D47/D50, Olly 2026-08-17).

    ``irr`` and ``equity_irr`` on :class:`FinancialCase` are legitimately nullable: an IRR does not
    exist for a series whose every cashflow is non-positive, and guessing one would be worse than
    refusing it. But a record whose ONLY return field is that null tells a reader "no value was
    found" when the truth is "the value was measured and it falls £N short of returning capital".
    This account is therefore **mandatory** on every ``FinancialCase`` and every field but
    ``capital_recovery_pct`` is non-nullable, so the two statements can never be confused.

    Undiscounted by design: the library carries no sourced project discount rate, and discounting at
    ``financing.target_equity_irr_pct`` (an equity *target*, withdrawn as a hurdle by CLAUDE.md §2)
    would be an invented input under D45. Computed by ``gridq.finance.returns.capital_recovery``;
    this schema only carries the shape, per the module docstring above.
    """

    annual_net_margin_gbp: float = Field(
        description="Mean operating-year net cashflow. Negative means the asset loses money yearly."
    )
    operating_cashflow_gbp: float = Field(
        description="Sum of years 1..N: the cash the asset generates over the horizon."
    )
    capital_outlay_gbp: float = Field(
        ge=0, description="Year-0 outlay as a positive number; 0.0 when year 0 is not an outlay."
    )
    lifetime_net_cashflow_gbp: float = Field(
        description="Sum of the whole series, outlay included. Undiscounted bottom line."
    )
    capital_recovery_gap_gbp: float = Field(
        description=(
            "Outlay minus operating cashflow. POSITIVE is a shortfall and is the figure that "
            "replaces a null IRR; negative means capital is returned with that much to spare."
        )
    )
    capital_recovery_pct: float | None = Field(
        default=None,
        description=(
            "Operating cashflow as a percentage of the outlay. None ONLY where there was no "
            "outlay to recover, which is the single legitimate null on this model."
        ),
    )
    every_operating_year_non_positive: bool = Field(
        description="True means no year turns cash-positive, which is why project IRR is refused."
    )
    status: str = Field(
        description=(
            "capital_never_returned | partial_capital_recovery | capital_returned | "
            "no_capital_outlay | insufficient_series"
        )
    )
    narrative: str = Field(
        min_length=1,
        description=(
            "One reader-facing sentence carrying the measured position and its direction. Reaches "
            "rendered packs, so it holds no em/en dash (house style is zero)."
        ),
    )


class FinancialCase(GridQModel):
    """The bankable result. Cashflows may be negative (year-0 capex) — a documented
    exception to the no-negative-GBP rule."""

    case_id: str
    p50_cashflows: list[float] = Field(
        default_factory=list, description="Annual cashflows (GBP); year 0 is typically negative."
    )
    p90_cashflows: list[float] = Field(
        default_factory=list, description="Annual cashflows (GBP); year 0 is typically negative."
    )
    dscr_summary: DscrSummary
    irr: float | None = Field(default=None, description="Project (unlevered) IRR (%).")
    equity_irr: float | None = Field(
        default=None,
        description=(
            "Levered (equity) IRR on the P50 base case (%); the P90 case governs the DSCR "
            "covenant. None when there is no debt or the equity series is non-conventional."
        ),
    )
    payback_years: float | None = Field(default=None, ge=0)
    capital_recovery: CapitalRecoveryAccount = Field(
        description=(
            "MANDATORY (D47): the always-defined return account. Deliberately has no default, so a "
            "FinancialCase cannot be constructed whose only return figure is a null IRR."
        )
    )
    debt_assumptions: DebtAssumptions
    price_recommendation: PriceRecommendation
    covenant_status: CovenantStatus
    warnings: list[str] = Field(default_factory=list)
    lender_terms: LenderTerms | None = Field(
        default=None,
        description=(
            "WP-UW-03 (D75), additive: the lender terms the DSCR was sized and measured against, "
            "as supplied. None when the run had none (then dscr_summary.status says why there is "
            "no DSCR). A real input, so it enters the quote hash when set (D78)."
        ),
    )
    lifecycle_inputs: LifecycleInputs | None = Field(
        default=None,
        description=(
            "WP-UW-17, additive: the lifecycle inputs as supplied (construction programme, COD "
            "delay to stress, CM agreement indexation facts, terminal / decommissioning / "
            "replacement flows). None when the run had none. A real input, so it enters the quote "
            "hash when set (pop-if-None, D78)."
        ),
    )
    lifecycle: LifecycleRecord | None = Field(
        default=None,
        description=(
            "WP-UW-17, additive and derived: what the run did with the lifecycle inputs and the "
            "library CPI key (indexation basis per line, construction / IDC / return basis, and "
            "the terminal, decommissioning and replacement ledger). Populated on every production "
            "run; None on historical records. Excluded from the quote hash (D132)."
        ),
    )

    @model_validator(mode="after")
    def _cashflows_present_and_aligned(self) -> FinancialCase:
        if not self.p50_cashflows or not self.p90_cashflows:
            raise ValueError("P50/P90 mandatory: both cashflow series are required.")
        if len(self.p50_cashflows) != len(self.p90_cashflows):
            raise ValueError("p50_cashflows and p90_cashflows must be the same length.")
        return self
