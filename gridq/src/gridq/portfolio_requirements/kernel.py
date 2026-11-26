"""Deterministic cashflow kernel with **DSCR-sized debt**.

The one opinionated structural choice, per the Fable advisor's design: debt is *sized*, never
asserted. ``debt = min(gearing_cap x capex, PV(CFADS_P90 / DSCR_target, tenor, rate))``.

Why that matters: ``Sim Work/Model_v5.xlsx`` asserts 60% gearing against a Year-1 DSCR of 0.996x.
Sizing debt from cashflow instead makes that structure unreachable, and makes "leverage is only
accretive when the unlevered return exceeds the cost of debt" an *emergent* property rather than a
check bolted on afterwards. This kernel is, by construction, incapable of the error that produced
Model_v5's 20.88%.

Conventions (all stated, none hidden):

* Debt service is **level** (annuity), which is the natural pairing with DSCR sizing: the target
  DSCR fixes the annual service, and the service fixes the principal.
* Revenue path: ``rev_y = rev_yr1 x (1 + cpi)^(y-1) x (1 - degradation)^(y-1)``.
* Opex path: ``opex_y = opex_yr1 x (1 + cpi)^(y-1)``, plus per-site fixed opex.
* Capex is a single Year-0 outflow: ``fleet_mw x capex_per_mw + n_sites x site_fixed_capex``.
* Unlevered IRR is on ``(-capex, EBITDA_1..N)``; levered IRR on
  ``(-(capex - debt), (EBITDA - debt_service)_1..N)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridq.portfolio_requirements.assumptions import BOUNDS, FinancingTier


def irr(cashflows: list[float], lo: float = -0.9999, hi: float = 10.0) -> float | None:
    """Bisection IRR. ``None`` when the cashflow never crosses zero NPV in ``[lo, hi]``.

    Bisection (not Newton) so the result is deterministic and bracket-safe for the sign patterns a
    project cashflow produces.
    """

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** i for i, cf in enumerate(cashflows))

    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(400):
        mid = (lo + hi) / 2.0
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def annuity_factor(rate: float, years: int) -> float:
    """PV of 1/yr for ``years`` at ``rate``. Used to turn a DSCR-capped service into a principal."""
    if rate <= 0:
        return float(years)
    return (1.0 - (1.0 + rate) ** -years) / rate


@dataclass(frozen=True)
class CashflowResult:
    """One evaluated configuration."""

    capex_total: float
    debt: float
    equity: float
    gearing_achieved: float
    debt_service_annual: float
    unlevered_irr: float | None
    levered_irr: float | None
    dscr_min: float
    dscr_avg: float
    ebitda_year1: float
    cfads_p90_year1: float
    leverage_accretive: bool
    binding_constraint: str
    unlevered_cashflows: tuple[float, ...]
    levered_cashflows: tuple[float, ...]
    dscr_path: tuple[float, ...]

    @property
    def levered_minus_unlevered_pp(self) -> float | None:
        if self.levered_irr is None or self.unlevered_irr is None:
            return None
        return (self.levered_irr - self.unlevered_irr) * 100.0


def evaluate(
    *,
    capex_per_mw: float,
    net_rev_yr1_per_mw: float,
    opex_per_mw: float,
    tier: FinancingTier,
    fleet_mw: float,
    n_sites: int,
    contracted: bool | None = None,
) -> CashflowResult:
    """Evaluate one portfolio configuration with DSCR-sized debt.

    ``contracted`` selects the P90 haircut on CFADS used for debt sizing; when ``None`` it is
    inferred from the tier's ``firm_revenue_share_required``.
    """
    b = BOUNDS
    years = b.asset_life_years
    if contracted is None:
        contracted = tier.firm_revenue_share_required >= 0.60
    p90_factor = b.p90_factor_contracted if contracted else b.p90_factor_merchant

    capex_total = fleet_mw * capex_per_mw + n_sites * b.site_fixed_capex

    # Revenue / opex / EBITDA paths
    ebitda: list[float] = []
    for y in range(1, years + 1):
        esc = (1.0 + b.cpi_escalation) ** (y - 1)
        fade = (1.0 - b.degradation_per_year) ** (y - 1)
        revenue = fleet_mw * net_rev_yr1_per_mw * esc * fade
        opex = fleet_mw * opex_per_mw * esc + n_sites * b.site_fixed_opex * esc
        ebitda.append(revenue - opex)

    # --- Debt sizing: the integrity feature -------------------------------------------------
    cfads_p90_y1 = ebitda[0] * p90_factor
    service_cap = max(cfads_p90_y1 / tier.dscr_target, 0.0)
    debt_from_dscr = service_cap * annuity_factor(tier.rate, tier.tenor_years)
    debt_from_cap = tier.gearing_cap * capex_total
    debt = max(min(debt_from_dscr, debt_from_cap), 0.0)
    binding = "dscr" if debt_from_dscr <= debt_from_cap else "gearing_cap"

    debt_service = debt / annuity_factor(tier.rate, tier.tenor_years) if debt > 0 else 0.0
    equity = capex_total - debt

    # --- Cashflows ---------------------------------------------------------------------------
    unlev = [-capex_total, *ebitda]
    lev = [-equity]
    dscr_path: list[float] = []
    for y in range(years):
        service = debt_service if y < tier.tenor_years else 0.0
        lev.append(ebitda[y] - service)
        if service > 0:
            dscr_path.append(ebitda[y] / service)

    u = irr(unlev)
    lvd = irr(lev)
    return CashflowResult(
        capex_total=capex_total,
        debt=debt,
        equity=equity,
        gearing_achieved=(debt / capex_total) if capex_total else 0.0,
        debt_service_annual=debt_service,
        unlevered_irr=u,
        levered_irr=lvd,
        dscr_min=min(dscr_path) if dscr_path else float("inf"),
        dscr_avg=(sum(dscr_path) / len(dscr_path)) if dscr_path else float("inf"),
        ebitda_year1=ebitda[0],
        cfads_p90_year1=cfads_p90_y1,
        leverage_accretive=(lvd is not None and u is not None and lvd > u),
        binding_constraint=binding,
        unlevered_cashflows=tuple(unlev),
        levered_cashflows=tuple(lev),
        dscr_path=tuple(dscr_path),
    )
