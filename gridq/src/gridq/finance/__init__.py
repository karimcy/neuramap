"""Financial model (M5) + counterparty-credit mapping (M9, mapping only).

Builds the deterministic `FinancialCase` around an `OptimisationResult`: P50/P90 cashflows,
cost lines, capex, an EBITDA-like project cashflow and free cashflow before debt; DSCR + the
covenant check; payback + project IRR; and the price recommendation. Every number traces to an
input or a versioned assumption (`load_assumptions(version)`); no private constants, no
hallucinated capex/revenue/debt, no LLM arithmetic. Missing inputs become warnings and
*unavailable* fields — never guessed.

See docs/financial_model.md for what is implemented vs. placeholder, and how future dispatch
outputs feed this. Site-level only; the portfolio model is Sim's (Finance) workstream.
"""

from __future__ import annotations

from gridq.finance.cashflows import (
    DEFAULT_HORIZON_YEARS,
    CapexEstimate,
    CashflowModel,
    DispatchEnergy,
    InsufficientInputsError,
    ResultLike,
    blended_import_rate_gbp_per_kwh,
    build_cashflow_model,
    check_capex_plausibility,
    estimate_capex,
    resolve_contract_tenor_evidence,
    revenue_by_stream,
    tenors_from_commitments,
    tenors_from_provisioning_requirements,
)
from gridq.finance.credit import (
    CreditRateTenor,
    build_debt_terms_from_credit,
    map_credit_to_rate_tenor,
)
from gridq.finance.debt import (
    DSCR_BASIS_P90_PRE_TAX,
    SCULPTING_ANNUITY,
    SCULPTING_P90_DSCR,
    CovenantOutcome,
    DebtTerms,
    DscrResult,
    assess_debt,
    build_debt_terms,
    check_covenant,
    covenant_value,
    dscr_by_year,
    level_payment_schedule,
    sculpted_schedule,
    size_and_measure_dscr,
)
from gridq.finance.financial_model import (
    FinancialModelResult,
    build_financial_case,
    run_financial_model,
)
from gridq.finance.pricing import (
    annual_revenue_requirement,
    capital_recovery_factor,
    fixed_price_per_kwh,
    recommend_price,
)
from gridq.finance.returns import (
    EquityIrrResult,
    IrrResult,
    PaybackResult,
    equity_irr,
    npv,
    project_irr,
    simple_payback,
)

__all__ = [
    # cashflows
    "DEFAULT_HORIZON_YEARS",
    "CapexEstimate",
    "check_capex_plausibility",
    "CashflowModel",
    "DispatchEnergy",
    "InsufficientInputsError",
    "ResultLike",
    "blended_import_rate_gbp_per_kwh",
    "build_cashflow_model",
    "estimate_capex",
    "resolve_contract_tenor_evidence",
    "revenue_by_stream",
    "tenors_from_commitments",
    "tenors_from_provisioning_requirements",
    # debt / DSCR
    "DSCR_BASIS_P90_PRE_TAX",
    "SCULPTING_ANNUITY",
    "SCULPTING_P90_DSCR",
    "CovenantOutcome",
    "DebtTerms",
    "DscrResult",
    "assess_debt",
    "build_debt_terms",
    "check_covenant",
    "covenant_value",
    "dscr_by_year",
    "level_payment_schedule",
    "sculpted_schedule",
    "size_and_measure_dscr",
    # credit -> rate/tenor mapping (M9)
    "CreditRateTenor",
    "build_debt_terms_from_credit",
    "map_credit_to_rate_tenor",
    # returns
    "EquityIrrResult",
    "IrrResult",
    "PaybackResult",
    "equity_irr",
    "npv",
    "project_irr",
    "simple_payback",
    # pricing
    "annual_revenue_requirement",
    "capital_recovery_factor",
    "fixed_price_per_kwh",
    "recommend_price",
    # orchestrator
    "FinancialModelResult",
    "build_financial_case",
    "run_financial_model",
]
