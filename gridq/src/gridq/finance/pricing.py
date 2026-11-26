"""Price recommendation (M5, part 4).

Supports the three commercial structures from the brief — fixed £/kWh (default), savings share,
capacity fee — but **only computes a number where the assumptions and inputs support it**. In v1
the fixed-£/kWh price-to-target needs an annual delivered-energy volume (a dispatch output the
current `OptimisationResult` does not carry) and a debt structure (absent — see ``debt.py``), so
the integration path returns the structure with an *unavailable* value and a warning. The pricing
*machinery* (capital-recovery factor → revenue requirement → £/kWh) is implemented and tested so
it activates as soon as those inputs exist.

Savings-share and capacity-fee are explicit, labelled placeholders (structure only, no value) —
never an unlabelled stand-in.

**WP-UW-17 (D128): the production price is levelised.** `financial_model` passes a
`Levelisation` — the run's own P50 pre-debt cashflows, the modelled delivered volume per year and a
closed-loop IRR measure — and the price is the flat £/kWh at which those flows plus price x volume
earn the target rate: ``P = max(0, -NPV_r(fcf)) / sum DF_t x E_t x I_t``. Volume fades and is
availability-scaled year by year, never flat year-1 volume (N12). ``hits_target_return`` then
re-measures the IRR WITH the price revenue added (closed loop). The CRF helpers below are the legacy
scalar path, kept for callers that supply no series.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus
from gridq.finance.returns import DatedFlow, xnpv
from gridq.schemas import PriceRecommendation, PriceStructure

TARGET_IRR_KEY = ("financing", "target_equity_irr_pct")

#: The closed-loop tolerance (percentage points): a measured IRR this close below the target is a
#: rounding artefact of the 6 dp price, not a miss.
HITS_TOLERANCE_PP = 0.01

#: Bisection steps for the equity-solved diagnostic price (bracket [0, 4P] halves to < 1e-12 £/kWh).
_EQUITY_SOLVE_ITERS = 40


@dataclass(frozen=True)
class Levelisation:
    """What the levelised price-to-target needs from one run (D128, D132 R3).

    ``priced_flows`` are the P50 PRE-DEBT flows (capex draws and operating FCF, every modelled
    lifecycle flow included, IDC excluded), month-dated: month ``12t`` on the annual basis, the
    construction draws and the delayed operating months on the dated basis. ``energy_kwh_by_year``
    is the modelled delivered volume of operating years ``1..N`` and ``operating_months`` their
    months. ``measure_irr`` returns the run's IRR with a per-year price revenue added (the equity
    IRR when debt is sized, else the project IRR — ``measured`` says which), or ``None``.
    """

    basis: str
    priced_flows: Sequence[DatedFlow]
    energy_kwh_by_year: Sequence[float]
    operating_months: Sequence[int]
    measure_irr: Callable[[Sequence[float]], float | None]
    measured: str
    price_index: Sequence[float] | None = None

    def energy_flows(self) -> list[DatedFlow]:
        index = self.price_index or [1.0] * len(self.energy_kwh_by_year)
        if not (len(index) == len(self.energy_kwh_by_year) == len(self.operating_months)):
            raise ValueError("energy, price index and operating months must align year by year")
        return [
            (m, e * i)
            for m, e, i in zip(self.operating_months, self.energy_kwh_by_year, index, strict=True)
        ]

    def revenue_by_year(self, price_gbp_per_kwh: float) -> list[float]:
        index = self.price_index or [1.0] * len(self.energy_kwh_by_year)
        return [
            price_gbp_per_kwh * e * i for e, i in zip(self.energy_kwh_by_year, index, strict=True)
        ]


def levelised_price(
    priced_flows: Sequence[DatedFlow],
    energy_flows: Sequence[DatedFlow],
    target_irr_pct: float,
) -> tuple[float, float]:
    """``(P, NPV)``: the flat £/kWh that lifts ``priced_flows`` to the target rate, and their NPV.

    Numerator and denominator are discounted to ONE shared anchor, the earliest month of either
    (R3), so a construction period moves both alike. ``P = round(max(0, -NPV) / PV(energy), 6)``:
    floored at zero (the stack alone may clear the target; a negative price is not an offer).
    """
    if not energy_flows:
        raise ValueError("no delivered-energy flows to levelise over")
    rate = target_irr_pct / 100.0
    anchor = min(m for m, _ in (*priced_flows, *energy_flows))
    npv_flows = xnpv(rate, priced_flows, anchor_month=anchor)
    denominator = xnpv(rate, energy_flows, anchor_month=anchor)
    if denominator <= 0:
        raise ValueError(f"discounted delivered energy must be positive, got {denominator}")
    return round(max(0.0, -npv_flows) / denominator, 6), npv_flows


def levelised_price_to_target(
    fcf_gbp: Sequence[float],
    energy_kwh_by_year: Sequence[float],
    target_irr_pct: float,
    price_index: Sequence[float] | None = None,
) -> tuple[float, float]:
    """The annual-basis levelised price: ``fcf`` and ``energy`` aligned on years ``0..N``.

    ``energy[0]`` is the year-0 volume (0.0). Equivalent to `levelised_price` with month ``12t``.
    """
    if len(fcf_gbp) != len(energy_kwh_by_year):
        raise ValueError(
            f"fcf ({len(fcf_gbp)}) and energy ({len(energy_kwh_by_year)}) must have equal length"
        )
    index = price_index if price_index is not None else [1.0] * len(energy_kwh_by_year)
    if len(index) != len(energy_kwh_by_year):
        raise ValueError("price index must align with the energy series")
    priced = [(12 * t, v) for t, v in enumerate(fcf_gbp)]
    energy = [
        (12 * t, e * i) for t, (e, i) in enumerate(zip(energy_kwh_by_year, index, strict=True))
    ]
    return levelised_price(priced, energy, target_irr_pct)


def equity_price_to_target(
    measure_irr: Callable[[Sequence[float]], float | None],
    revenue_per_unit_price: Callable[[float], Sequence[float]],
    target_irr_pct: float,
    base_price_gbp_per_kwh: float,
) -> float | None:
    """The DIAGNOSTIC price at which the measured (equity) IRR equals the target, or ``None``.

    Bisection on ``[0, 4 x base]`` with the debt held fixed. ``None`` when the IRR is unmeasurable
    at a bracket end or the target is not bracketed. Never the headline (D128: an
    equity-solved headline flips basis with the lender terms and is circular under a D77 sculpt).
    """
    low_irr = measure_irr(revenue_per_unit_price(0.0))
    if low_irr is not None and low_irr >= target_irr_pct:
        return 0.0
    high = 4.0 * base_price_gbp_per_kwh
    if high <= 0:
        return None
    high_irr = measure_irr(revenue_per_unit_price(high))
    if high_irr is None or high_irr < target_irr_pct:
        return None
    low = 0.0
    for _ in range(_EQUITY_SOLVE_ITERS):
        mid = (low + high) / 2.0
        mid_irr = measure_irr(revenue_per_unit_price(mid))
        if mid_irr is not None and mid_irr >= target_irr_pct:
            high = mid
        else:
            low = mid
    return round(high, 6)


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    sect = getattr(assumptions, section, None)
    if not isinstance(sect, dict):
        return None
    return sect.get(name)


def capital_recovery_factor(rate_pct: float, periods: int) -> float:
    """Capital recovery factor: the level annual payment per £1 of capex over ``periods`` years.

    CRF = i(1+i)^n / ((1+i)^n − 1); reduces to 1/n at a zero rate. Pure, deterministic.
    """
    if periods <= 0:
        raise ValueError(f"periods must be positive, got {periods}")
    if rate_pct < 0:
        raise ValueError(f"rate_pct must be non-negative, got {rate_pct}")
    i = rate_pct / 100.0
    if i == 0:
        return 1.0 / periods
    factor = (1.0 + i) ** periods
    return i * factor / (factor - 1.0)


def annual_revenue_requirement(
    capex_gbp: float,
    annual_opex_gbp: float,
    target_irr_pct: float,
    horizon_years: int,
    other_annual_revenue_gbp: float = 0.0,
) -> float:
    """The level annual revenue the CUSTOMER must contribute to hit the target return.

    ``capex × CRF(target_irr, horizon) + opex − other_annual_revenue``.

    **The subtraction is the point** (hostile review N3). This function previously took no revenue
    argument, so the customer price was built to recover 100% of capex and opex while the project
    *also* kept every pound of the value stack it had already booked — arbitrage, DUoS avoidance,
    Capacity Market, local flex. The stack was collected twice: once from the customer through the
    PPA rate, once as project revenue.

    The consequence ran in the damaging direction. An overstated price-to-target trips the
    ``exceeds_counterfactual`` test against the customer's current import cost, so the model
    declares a site **not commercially viable** when it is viable. On the worked example a site
    priced at £0.2465/kWh ("not viable") is £0.1265/kWh — comfortably viable — once the booked
    revenue is netted off.

    ``other_annual_revenue_gbp`` is the year-1 revenue the project earns from every source that is
    NOT the customer contract: the priced floor. It defaults to 0.0 so an explicit caller must
    supply it, and a caller that does not is simply back to the old, conservative-in-price
    behaviour rather than silently wrong in a new way.
    """
    if capex_gbp < 0:
        raise ValueError(f"capex must be non-negative, got {capex_gbp}")
    if annual_opex_gbp < 0:
        raise ValueError(f"annual opex must be non-negative, got {annual_opex_gbp}")
    if other_annual_revenue_gbp < 0:
        raise ValueError(
            f"other annual revenue must be non-negative, got {other_annual_revenue_gbp}"
        )
    crf = capital_recovery_factor(target_irr_pct, horizon_years)
    # Floored at zero: where the stack alone already clears the target, the customer contribution
    # is zero, not negative. A negative requirement would imply paying the customer to take the
    # asset, which is a different commercial proposition and not one this function may invent.
    return round(max(0.0, capex_gbp * crf + annual_opex_gbp - other_annual_revenue_gbp), 2)


def fixed_price_per_kwh(revenue_requirement_gbp: float, annual_energy_kwh: float) -> float:
    """The fixed £/kWh that collects the revenue requirement over the delivered energy volume."""
    if annual_energy_kwh <= 0:
        raise ValueError(f"annual_energy_kwh must be positive, got {annual_energy_kwh}")
    if revenue_requirement_gbp < 0:
        raise ValueError(f"revenue requirement must be non-negative, got {revenue_requirement_gbp}")
    return round(revenue_requirement_gbp / annual_energy_kwh, 6)


def recommend_price(
    structure: PriceStructure,
    assumptions: AssumptionLibrary,
    *,
    capex_gbp: float | None = None,
    annual_opex_gbp: float | None = None,
    annual_delivered_energy_kwh: float | None = None,
    horizon_years: int | None = None,
    counterfactual_import_gbp_per_kwh: float | None = None,
    other_annual_revenue_gbp: float = 0.0,
    achieved_equity_irr_pct: float | None = None,
    levelisation: Levelisation | None = None,
) -> tuple[PriceRecommendation, list[str]]:
    """Recommend a price for ``structure``, computing a value only where inputs support it.

    Returns the `PriceRecommendation` plus warnings. ``hits_target_return`` is MEASURED, never
    asserted (N12): it is the model's own equity IRR (``achieved_equity_irr_pct``, off the same
    run) compared against ``financing.target_equity_irr_pct``. Without a measured equity IRR it
    stays ``None`` (unknown) — the flag is never derived from an assumption's status.

    ``counterfactual_import_gbp_per_kwh`` grounds the recommendation in the customer's
    alternative (their current blended import cost): a price-to-target ABOVE the counterfactual
    is flagged ``exceeds_counterfactual`` and warned as not commercially viable — the number is
    still recorded for transparency (it shows exactly how far from viable the project is), but
    it must never be presented as an offer (red-team fix, 2026-07-07: a £1.06/kWh "price
    recommendation" was rendered without context on a site paying ~£0.21/kWh).

    ``levelisation`` (WP-UW-17, D128) selects the levelised path, which the production run
    always supplies once the delivered volume is known; the scalar CRF path below runs only without
    it, and its arguments are then ignored by the levelised path.
    """
    warnings: list[str] = []

    if structure == PriceStructure.SAVINGS_SHARE:
        warnings.append(
            "Price structure 'savings_share' is a labelled placeholder (structure only, no "
            "value): a savings-share contract REQUIRES an agreed M&V method — boundary Open Q4, "
            "owner UNRESOLVED (PE ships the M&V data contract only). No value computed."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings

    if structure == PriceStructure.CAPPED_BILL:
        warnings.append(
            "Price structure 'capped_bill' is a labelled placeholder (structure only, no value): "
            "a bill guarantee transfers usage/weather variance onto the seller, so pricing it "
            "needs (a) the agreed M&V method — boundary Open Q4, owner UNRESOLVED — and (b) "
            "sourced volume-variance keys (bill_products.volume_variance_pct / "
            "bill_products.weather_variance_pct — D30 homes; empty until sourced, never "
            "invented; this downside is NOT the P90 revenue haircut). No value computed."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings

    if structure == PriceStructure.CAPACITY_FEE:
        warnings.append(
            "Price structure 'capacity_fee' is a labelled placeholder in v1 (no £/kW/yr capacity-"
            "fee basis is sourced yet); no value computed."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings

    # Fixed £/kWh (default).
    target = _maybe(assumptions, *TARGET_IRR_KEY)
    if target is None:
        warnings.append(
            "Fixed £/kWh price not computed: no 'financing.target_equity_irr_pct' in the "
            "assumption library."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings
    if target.status != AssumptionStatus.CONFIRMED:
        warnings.append(
            f"Assumption '{'.'.join(TARGET_IRR_KEY)}' is '{target.status.value}'; any price "
            "recommendation is indicative, not bankable, until it is confirmed."
        )

    if levelisation is not None:
        return _levelised_recommendation(
            structure,
            target,
            levelisation,
            counterfactual_import_gbp_per_kwh,
            warnings,
        )

    if annual_delivered_energy_kwh is None or annual_delivered_energy_kwh <= 0:
        warnings.append(
            "Fixed £/kWh price unavailable: annual delivered-energy volume is not yet known "
            "(a dispatch output, M3). The price-to-target structure is returned without a value."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings
    if capex_gbp is None or annual_opex_gbp is None or horizon_years is None:
        warnings.append(
            "Fixed £/kWh price unavailable: capex / opex / horizon needed to compute the revenue "
            "requirement were not all supplied."
        )
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings

    target_irr_pct = float(target.value) if isinstance(target.value, (int, float)) else None
    if target_irr_pct is None:
        warnings.append("Target-return assumption is non-numeric; price not computed.")
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings

    # N3: net off the revenue the project already earns from the value stack, so the customer
    # is not charged for capex the stack is already recovering.
    requirement = annual_revenue_requirement(
        capex_gbp, annual_opex_gbp, target_irr_pct, horizon_years, other_annual_revenue_gbp
    )
    if other_annual_revenue_gbp > 0:
        warnings.append(
            f"Price-to-target nets off £{other_annual_revenue_gbp:,.0f}/yr of priced-floor "
            "revenue the project earns from sources other than the customer contract; the "
            "customer funds only the residual. Charging the full capex recovery AND keeping the "
            "stack would collect the same value twice."
        )
    price = fixed_price_per_kwh(requirement, annual_delivered_energy_kwh)
    # N12 (2026-08-10 review): `hits_target_return` is MEASURED against the run's own equity IRR,
    # not read off the assumption's STATUS. The old `hits = target.status == CONFIRMED` asserted
    # "this price hits the 12% equity target" on the strength of the target having been signed
    # off — and the construction behind the price cannot support that claim anyway: the CRF is
    # applied to TOTAL capex with no debt (an UNLEVERED recovery, while the key is an EQUITY
    # hurdle), it is pre-tax, and it levelises as if volume were flat when the delivered volume
    # fades at `degradation.annual_capacity_fade_pct`. The honest flag compares the equity IRR
    # the model actually computed for this case against the target.
    #
    # A measured MISS is always reportable (False — the conservative direction). A measured hit
    # is only asserted when the target itself is confirmed; on a placeholder target the claim is
    # unknown (None), never True.
    hits: bool | None
    if achieved_equity_irr_pct is None:
        hits = None
        warnings.append(
            "hits_target_return is UNKNOWN: no equity IRR was computed for this case (no debt "
            "structure resolved, or the equity series is non-conventional), so the price has not "
            "been verified against the target equity return. It is NOT asserted to hit target — "
            "the price-to-target construction is unlevered, pre-tax and assumes level annual "
            "volume, so 'meets target by construction' would be a false claim (N12)."
        )
    elif achieved_equity_irr_pct < target_irr_pct:
        hits = False
        warnings.append(
            f"Price-to-target MISSES the target return: the modelled equity IRR "
            f"{achieved_equity_irr_pct:.2f}% is below the {target_irr_pct:.2f}% target. The "
            "price recovers capex at the target rate on an unlevered, pre-tax, level-volume "
            "construction; leverage, tax and capacity fade move the achieved equity return away "
            "from it (N12)."
        )
    elif target.status != AssumptionStatus.CONFIRMED:
        hits = None
        warnings.append(
            f"Modelled equity IRR {achieved_equity_irr_pct:.2f}% clears the "
            f"{target_irr_pct:.2f}% target, but the target-return assumption is a placeholder — "
            "not asserting hits_target_return=True on placeholder inputs."
        )
    else:
        hits = True
    # Ground the price in the customer's alternative: a price-to-target above their current
    # blended import cost is not a commercial offer, it is a viability verdict.
    exceeds: bool | None = None
    if counterfactual_import_gbp_per_kwh is not None and counterfactual_import_gbp_per_kwh > 0:
        exceeds = price > counterfactual_import_gbp_per_kwh
        if exceeds:
            ratio = price / counterfactual_import_gbp_per_kwh
            warnings.append(
                f"Price-to-target £{price:.4f}/kWh EXCEEDS the customer's current blended "
                f"import cost £{counterfactual_import_gbp_per_kwh:.4f}/kWh ({ratio:.1f}x): a "
                "fixed PPA at this price is not commercially viable — recorded for "
                "transparency, never to be presented as an offer."
            )
    return (
        PriceRecommendation(
            structure=structure,
            value_gbp_per_kwh=price,
            hits_target_return=hits,
            counterfactual_import_gbp_per_kwh=counterfactual_import_gbp_per_kwh,
            exceeds_counterfactual=exceeds,
        ),
        warnings,
    )


def _exceeds(price: float, counterfactual: float | None, warnings: list[str]) -> bool | None:
    """Ground the price in the customer's alternative (red-team fix, 2026-07-07)."""
    if counterfactual is None or counterfactual <= 0:
        return None
    exceeds = price > counterfactual
    if exceeds:
        ratio = price / counterfactual
        warnings.append(
            f"Price-to-target £{price:.4f}/kWh EXCEEDS the customer's current blended "
            f"import cost £{counterfactual:.4f}/kWh ({ratio:.1f}x): a "
            "fixed PPA at this price is not commercially viable — recorded for "
            "transparency, never to be presented as an offer."
        )
    return exceeds


def _levelised_recommendation(
    structure: PriceStructure,
    target: Assumption,
    lev: Levelisation,
    counterfactual: float | None,
    warnings: list[str],
) -> tuple[PriceRecommendation, list[str]]:
    """The levelised price-to-target and its closed-loop verification (D128)."""
    if isinstance(target.value, bool) or not isinstance(target.value, (int, float)):
        warnings.append("Target-return assumption is non-numeric; price not computed.")
        return PriceRecommendation(
            structure=structure, value_gbp_per_kwh=None, hits_target_return=None
        ), warnings
    target_irr_pct = float(target.value)
    price, npv_at_target = levelised_price(lev.priced_flows, lev.energy_flows(), target_irr_pct)
    dated = (
        " on the dated monthly timeline (construction draws and any COD delay included; "
        "numerator and volume discounted to one anchor, the first draw)"
        if lev.basis == "dated"
        else ""
    )
    warnings.append(
        "Price-to-target is levelised over the modelled delivered volume (fade and availability "
        "applied each year) on the run's own P50 pre-debt cashflows at the target rate"
        f"{dated}; FLAT-NOMINAL £/kWh, not indexed, unlevered, pre-tax."
    )
    if npv_at_target >= 0:
        warnings.append(
            f"The value stack alone clears the target (NPV at target £{npv_at_target:,.0f}); "
            "customer price is £0, not negative."
        )
    warnings.append("DSCR and debt sizing exclude customer price revenue.")

    measured = lev.measure_irr(lev.revenue_by_year(price))
    equity_price: float | None = None
    if lev.measured == "equity":
        equity_price = equity_price_to_target(
            lev.measure_irr, lev.revenue_by_year, target_irr_pct, price
        )
    hits: bool | None
    if measured is None:
        hits = None
        warnings.append(
            f"hits_target_return is UNKNOWN: the {lev.measured} IRR with the recommended price "
            "could not be measured (no conventional series), so the price is not verified "
            "against the target (N12)."
        )
    elif measured < target_irr_pct - HITS_TOLERANCE_PP:
        hits = False
        solved = (
            f"£{equity_price:.4f}/kWh"
            if equity_price is not None
            else "unavailable (not bracketed)"
        )
        warnings.append(
            f"With the recommended price the measured {lev.measured} IRR {measured:.2f}% is below "
            f"the {target_irr_pct:.2f}% target; equity-solved price {solved}."
        )
    elif target.status != AssumptionStatus.CONFIRMED:
        hits = None
        warnings.append(
            f"With the recommended price the measured {lev.measured} IRR {measured:.2f}% clears "
            f"the {target_irr_pct:.2f}% target, but the target-return assumption is a placeholder "
            "— not asserting hits_target_return=True on placeholder inputs."
        )
    else:
        hits = True
    exceeds = _exceeds(price, counterfactual, warnings)
    return (
        PriceRecommendation(
            structure=structure,
            value_gbp_per_kwh=price,
            hits_target_return=hits,
            counterfactual_import_gbp_per_kwh=counterfactual,
            exceeds_counterfactual=exceeds,
            levelisation_basis=lev.basis,
            measured_irr_with_price_pct=measured,
            equity_closed_loop_gbp_per_kwh=equity_price,
        ),
        warnings,
    )
