"""Return metrics: simple payback and project IRR (M5, part 3).

Both are deterministic, pure-Python and conservative: if a metric cannot be computed *reliably*
it is returned as unavailable with a warning, never a guessed or misleading number.

- **Simple payback** — the (interpolated) year at which cumulative undiscounted cashflow first
  turns non-negative. Returns ``None`` if the project never pays back over the horizon.
- **Project IRR** — the discount rate at which NPV = 0, found by bisection. It is computed only
  for a *conventional* cashflow (exactly one sign change: outflow(s) then inflows), which
  guarantees a unique root. Non-conventional or all-same-sign cashflows return unavailable —
  there is no single trustworthy IRR to report.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

# IRR search bounds and tolerance. These are numerical-method parameters (not £ assumptions):
# the discount rate is searched in (-99.99%, +100000%), which brackets any realistic project IRR.
_IRR_LOW = -0.9999
_IRR_HIGH = 1000.0
_IRR_TOL = 1e-7
_IRR_MAX_ITERS = 200


@dataclass(frozen=True)
class PaybackResult:
    """Simple-payback outcome. ``years=None`` ⇒ never pays back within the horizon."""

    available: bool
    years: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IrrResult:
    """Project-IRR outcome. ``irr_pct=None`` ⇒ no reliable single IRR exists."""

    available: bool
    irr_pct: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EquityIrrResult:
    """Levered (equity) IRR outcome. ``irr_pct=None`` ⇒ not computable.

    Unavailable (never a guessed number) when there is no debt principal (all-equity, or DSCR
    unavailable because no debt terms were resolved), the debt-service schedule is empty, the equity
    outlay is non-positive, or the equity cashflow is non-conventional (delegated to
    :func:`project_irr`, which refuses a non-unique IRR).
    """

    available: bool
    irr_pct: float | None = None
    equity_cashflows: tuple[float, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapitalRecovery:
    """An **always-defined** account of return, for the case where :func:`project_irr` refuses.

    D47/D50 (Olly, 2026-08-17). ``project_irr`` correctly returns unavailable when every cashflow is
    non-positive: capital is never returned, so no discount rate sets NPV to zero and there is
    no IRR to report. But a ``null`` return metric on the record is a **defect**, not a result —
    it reads to a client as "no value found" when the truth is "the model measured the value and
    it falls £N short". Every field here is therefore defined for **any** series; none is ever
    ``None`` except ``capital_recovery_pct``, undefined only when there was no outlay to recover.

    **Undiscounted, deliberately.** The library carries no sourced project discount rate:
    ``financing.target_equity_irr_pct`` is an equity *target*, not a project WACC, and CLAUDE.md §2
    withdrew the IRR target outright ("never a hurdle rate"). Discounting at an equity target would
    be both conceptually wrong and an invented input (D45), so this account discounts nothing. It is
    also the better client artifact: a capital-recovery gap is checkable, where an NPV at an assumed
    WACC invites the first question a lender asks, which is where the WACC came from.
    """

    annual_net_margin_gbp: float
    """Mean operating-year (year 1..N) net cashflow. Negative ⇒ the asset loses money every year."""

    operating_cashflow_gbp: float
    """Sum of years 1..N: the cash the asset actually generates over the horizon."""

    capital_outlay_gbp: float
    """The year-0 outlay as a positive number (0.0 when year 0 is not an outlay)."""

    lifetime_net_cashflow_gbp: float
    """Sum of the whole series, outlay included. The bottom line, undiscounted."""

    capital_recovery_gap_gbp: float
    """Outlay minus operating cashflow. **Positive ⇒ a shortfall**; this is the number that replaces
    a null IRR. Negative ⇒ capital is returned with that much to spare."""

    capital_recovery_pct: float | None
    """Operating cashflow as a percentage of the outlay. ``None`` only when there was no outlay."""

    every_operating_year_non_positive: bool
    """True ⇒ no year ever turns cash-positive, which is exactly why ``project_irr`` refuses."""

    status: str
    """Machine-readable: ``capital_never_returned`` | ``partial_capital_recovery`` |
    ``capital_returned`` | ``no_capital_outlay`` | ``insufficient_series``."""

    narrative: str
    """One reader-facing sentence stating the measured position and its direction. Kept free of
    em/en dashes because it reaches rendered packs, where the house style is zero."""


def capital_recovery(cashflows: list[float]) -> CapitalRecovery:
    """The always-defined return account for a year-0..N cashflow series (D47/D50).

    Never refuses and never returns a null headline: where an IRR does not exist this is the metric
    that carries the verdict. Pure, deterministic, undiscounted, and it reads no assumptions.
    """
    if len(cashflows) < 2:
        return CapitalRecovery(
            annual_net_margin_gbp=0.0,
            operating_cashflow_gbp=0.0,
            capital_outlay_gbp=0.0,
            lifetime_net_cashflow_gbp=round(sum(cashflows), 2),
            capital_recovery_gap_gbp=0.0,
            capital_recovery_pct=None,
            every_operating_year_non_positive=False,
            status="insufficient_series",
            narrative=(
                "No return account is possible: the cashflow series carries fewer than two "
                "periods, so there is no operating year to measure against the outlay."
            ),
        )

    operating = cashflows[1:]
    operating_total = round(sum(operating), 2)
    outlay = round(-cashflows[0], 2) if cashflows[0] < 0 else 0.0
    lifetime = round(sum(cashflows), 2)
    margin = round(operating_total / len(operating), 2)
    all_non_positive = all(cf <= 0.0 for cf in operating)
    gap = round(outlay - operating_total, 2)

    if outlay <= 0.0:
        return CapitalRecovery(
            annual_net_margin_gbp=margin,
            operating_cashflow_gbp=operating_total,
            capital_outlay_gbp=0.0,
            lifetime_net_cashflow_gbp=lifetime,
            capital_recovery_gap_gbp=0.0,
            capital_recovery_pct=None,
            every_operating_year_non_positive=all_non_positive,
            status="no_capital_outlay",
            narrative=(
                f"No year-0 capital outlay is recorded, so there is nothing to recover. The "
                f"asset's operating cashflow over {len(operating)} year(s) totals "
                f"GBP {operating_total:,.2f}, a mean of GBP {margin:,.2f} per year."
            ),
        )

    recovery_pct = round(operating_total / outlay * 100.0, 2)

    if all_non_positive:
        status = "capital_never_returned"
        narrative = (
            f"CAPITAL IS NEVER RETURNED, and this is why no IRR exists rather than a negative one: "
            f"every one of the {len(operating)} operating year(s) is cash-negative, at a mean of "
            f"GBP {margin:,.2f} per year, so the cumulative position moves FURTHER from recovery "
            f"each year. Against a GBP {outlay:,.2f} outlay the shortfall is "
            f"GBP {gap:,.2f}. The asset does not fail to be measured, it is measured and does "
            f"not pay."
        )
    elif gap > 0.0:
        status = "partial_capital_recovery"
        narrative = (
            f"Capital is PARTLY recovered: GBP {operating_total:,.2f} of operating cashflow "
            f"against a "
            f"GBP {outlay:,.2f} outlay, which is {recovery_pct:,.2f} percent of it, leaving a "
            f"GBP {gap:,.2f} shortfall over {len(operating)} year(s). At least one year is "
            f"cash-positive, so judge this on the shortfall rather than on a missing IRR."
        )
    else:
        status = "capital_returned"
        narrative = (
            f"Capital IS returned undiscounted: GBP {operating_total:,.2f} of operating cashflow "
            f"against a GBP {outlay:,.2f} outlay, {recovery_pct:,.2f} percent, a surplus of "
            f"GBP {abs(gap):,.2f} over {len(operating)} year(s)."
        )

    return CapitalRecovery(
        annual_net_margin_gbp=margin,
        operating_cashflow_gbp=operating_total,
        capital_outlay_gbp=outlay,
        lifetime_net_cashflow_gbp=lifetime,
        capital_recovery_gap_gbp=gap,
        capital_recovery_pct=recovery_pct,
        every_operating_year_non_positive=all_non_positive,
        status=status,
        narrative=narrative,
    )


def npv(rate: float, cashflows: list[float]) -> float:
    """Net present value of ``cashflows`` (year 0..N) at ``rate`` (decimal, e.g. 0.1 = 10%)."""
    if rate <= -1.0:
        raise ValueError(f"discount rate must be > -100%, got {rate}")
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cashflows))


def simple_payback(cashflows: list[float]) -> PaybackResult:
    """Undiscounted simple payback in years from a year-0..N cashflow series.

    Year 0 is typically the negative capex. The payback is the point where the running cumulative
    cashflow crosses zero, linearly interpolated within the crossing year.
    """
    if len(cashflows) < 2:
        return PaybackResult(False, warnings=("Need at least year 0 and one operating year.",))
    if cashflows[0] >= 0:
        return PaybackResult(
            False,
            warnings=(
                "Year-0 cashflow is non-negative — no upfront outlay to pay back; payback is "
                "not meaningful here.",
            ),
        )
    cumulative = cashflows[0]
    for year in range(1, len(cashflows)):
        prev = cumulative
        cumulative += cashflows[year]
        if cumulative >= 0:
            flow = cashflows[year]
            if flow <= 0:  # crossed zero on a non-positive flow — shouldn't happen, guard anyway
                return PaybackResult(True, years=float(year))
            fraction = -prev / flow
            return PaybackResult(True, years=round((year - 1) + fraction, 4))
    return PaybackResult(
        False,
        warnings=(
            "Project does not pay back within the projection horizon (cumulative cashflow stays "
            "negative).",
        ),
    )


def _count_sign_changes(cashflows: list[float]) -> int:
    """Count sign changes across the non-zero cashflows (zeros ignored)."""
    signs = [1 if cf > 0 else -1 for cf in cashflows if cf != 0]
    return sum(1 for a, b in zip(signs, signs[1:], strict=False) if a != b)


def project_irr(cashflows: list[float]) -> IrrResult:
    """Project IRR (%), computed only when a unique, reliable root exists.

    Reliability gate: the series must contain both an outflow and an inflow and have **exactly
    one** sign change (the conventional invest-then-earn shape). Otherwise IRR is unavailable —
    multiple or zero sign changes mean there is no single trustworthy IRR.
    """
    return _solve_irr(cashflows, lambda rate: npv(rate, cashflows))


def _solve_irr(values: Sequence[float], present_value: Callable[[float], float]) -> IrrResult:
    """The ONE IRR solver: the reliability gate on ``values`` plus bisection on ``present_value``.

    `project_irr` (annual, year-indexed) and `dated_irr` (month-dated, WP-UW-17) both call this,
    so the gate, the search bounds and the bisection are shared, never a second drifting solver.
    ``values`` are the cashflows in time order (for the sign-change gate); ``present_value`` is the
    NPV of the same flows at a decimal rate.
    """
    cashflows = list(values)
    if len(cashflows) < 2:
        return IrrResult(False, warnings=("Need at least two periods to compute IRR.",))
    if all(cf >= 0 for cf in cashflows):
        return IrrResult(False, warnings=("No negative cashflow — IRR is undefined (no outlay).",))
    if all(cf <= 0 for cf in cashflows):
        return IrrResult(
            False, warnings=("No positive cashflow — the project never returns capital; no IRR.",)
        )
    changes = _count_sign_changes(cashflows)
    if changes != 1:
        return IrrResult(
            False,
            warnings=(
                f"Cashflow has {changes} sign changes (not the conventional one); IRR may be "
                "non-unique, so it is not reported. Use NPV/DSCR to judge this profile.",
            ),
        )

    low, high = _IRR_LOW, _IRR_HIGH
    f_low = present_value(low)
    f_high = present_value(high)
    if f_low == 0:
        return IrrResult(True, irr_pct=round(low * 100.0, 4))
    if f_high == 0:
        return IrrResult(True, irr_pct=round(high * 100.0, 4))
    if (f_low > 0) == (f_high > 0):
        return IrrResult(
            False,
            warnings=("IRR is outside the searchable range (-99.99% … 100000%); not reported.",),
        )

    for _ in range(_IRR_MAX_ITERS):
        mid = (low + high) / 2.0
        f_mid = present_value(mid)
        if abs(f_mid) < _IRR_TOL or (high - low) / 2.0 < _IRR_TOL:
            return IrrResult(True, irr_pct=round(mid * 100.0, 4))
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return IrrResult(True, irr_pct=round((low + high) / 2.0 * 100.0, 4))


#: A month-dated cashflow: ``(month offset, £)``. Month 0 is the PLANNED commercial operation date
#: (COD); construction draws sit at negative offsets, and operating year ``t`` of a run delayed by
#: ``D`` months sits at ``D + 12·t`` (WP-UW-17, D130).
DatedFlow = tuple[int, float]


def consolidate_dated_flows(flows: Iterable[DatedFlow]) -> list[DatedFlow]:
    """Sum the flows that share a month and return them in month order.

    Two draws, or a draw and an operating year, landing in the same month are one cash movement;
    the sign-change gate must see them as one, never as an artificial pair of opposite signs.
    """
    by_month: dict[int, float] = {}
    for month, amount in flows:
        by_month[int(month)] = by_month.get(int(month), 0.0) + float(amount)
    return sorted(by_month.items())


def xnpv(rate: float, flows: Sequence[DatedFlow], *, anchor_month: int | None = None) -> float:
    """NPV of month-dated ``flows`` at an ANNUAL decimal ``rate``, discounted to the earliest month
    (or to ``anchor_month`` when given: the price numerator and denominator share one anchor, R3).

    Each flow is discounted by ``(1 + rate) ** ((month - first_month) / 12)``: a fractional-year
    exponent, so a four-month build or a three-month delay is priced at its true length rather than
    rounded to a year. On whole-year offsets the exponent is the same float as `npv`'s integer
    period, so the two agree exactly.
    """
    if rate <= -1.0:
        raise ValueError(f"discount rate must be > -100%, got {rate}")
    if not flows:
        return 0.0
    first = min(m for m, _ in flows) if anchor_month is None else anchor_month
    return sum(cf / (1.0 + rate) ** ((m - first) / 12.0) for m, cf in flows)


def dated_irr(flows: Sequence[DatedFlow]) -> IrrResult:
    """IRR (%) of month-dated ``flows``: the annual rate at which `xnpv` is zero.

    Same reliability gate, bounds and bisection as `project_irr` (via `_solve_irr`), applied to the
    flows in month order after same-month flows are summed.
    """
    ordered = consolidate_dated_flows(flows)
    return _solve_irr([cf for _, cf in ordered], lambda rate: xnpv(rate, ordered))


def equity_irr(
    capex_total_gbp: float,
    principal_gbp: float | None,
    debt_service_by_year_gbp: list[float],
    operating_fcf_pre_debt_by_year_gbp: list[float],
) -> EquityIrrResult:
    """Levered (equity) IRR (%) from the project's equity cashflows, or unavailable.

    The equity investor funds capex less the debt drawn at year 0 and receives the operating free
    cashflow net of debt service each year:

        equity_cf[0]   = -(capex_total - principal)
        equity_cf[t>=1] = operating_fcf[t] - debt_service[t]   (debt service is 0 after the tenor)

    The series runs to the LONGER of the operating horizon and the debt schedule (N11): debt
    service beyond the projected horizon is a real negative equity flow and is never dropped.

    Computed on the **P50 (base-case)** operating cashflows — the headline expected equity return —
    while the **P90** case governs the DSCR covenant (the constraint). This is the standard
    project-finance split: size debt to the downside, report the equity return on the base case.

    The root is found by the same :func:`project_irr`, so its one-sign-change reliability gate,
    search bounds and bisection are shared (never a second, drifting solver). The ``principal_gbp``
    and ``debt_service_by_year_gbp`` passed in must be the *same* values the DSCR was measured
    against (off the `DscrResult`), never recomputed, or the two would silently drift.

    Returns unavailable (``irr_pct=None``) — never a guessed number — when there is no debt
    principal, an empty debt schedule, a non-positive equity outlay, or a non-conventional series.
    """
    if principal_gbp is None or principal_gbp <= 0:
        return EquityIrrResult(
            available=False,
            warnings=(
                "Equity IRR unavailable: no debt principal (all-equity, or DSCR unavailable "
                "because no debt terms were resolved) — there is no levered split to measure; "
                "the project IRR is the return here.",
            ),
        )
    if not debt_service_by_year_gbp:
        return EquityIrrResult(
            available=False,
            warnings=("Equity IRR unavailable: the debt-service schedule is empty.",),
        )
    equity_outlay = round(capex_total_gbp - principal_gbp, 2)
    if equity_outlay <= 0:
        return EquityIrrResult(
            available=False,
            warnings=(
                f"Equity IRR unavailable: the equity outlay (capex {capex_total_gbp} - principal "
                f"{principal_gbp}) is non-positive.",
            ),
        )
    # Net each operating year by its debt service; years beyond the debt tenor carry none. Rounded
    # to whole pence, matching the debt-service schedule's own 2dp rounding; if a sub-penny year
    # ever made the series non-conventional, project_irr fails *safe* (unavailable, never a guess).
    #
    # N11 (2026-08-10 review): the series runs to the LONGER of the two, not to the operating
    # cashflows. It used to iterate the operating list, so when the projection horizon was
    # shorter than the tenor every debt-service payment past the horizon simply vanished: the
    # equity investor was credited with the full year-0 draw and never charged for the tail of
    # the loan that repays it. Worse, dropping those late negatives made a non-conventional
    # series look conventional, so `project_irr`'s sign-change gate — which would have refused
    # the metric — was bypassed and a flattered positive IRR was published. Post-horizon years
    # carry 0.0 operating cash (nothing was projected, so nothing may be assumed); the remaining
    # service therefore appears as the negative flow it is, and a series that is genuinely
    # non-conventional now fails the gate and reports unavailable.
    periods = max(len(operating_fcf_pre_debt_by_year_gbp), len(debt_service_by_year_gbp))
    equity_cf = [-equity_outlay]
    for t in range(periods):
        fcf = (
            operating_fcf_pre_debt_by_year_gbp[t]
            if t < len(operating_fcf_pre_debt_by_year_gbp)
            else 0.0
        )
        service = debt_service_by_year_gbp[t] if t < len(debt_service_by_year_gbp) else 0.0
        equity_cf.append(round(fcf - service, 2))
    inner = project_irr(equity_cf)
    return EquityIrrResult(
        available=inner.available,
        irr_pct=inner.irr_pct,
        equity_cashflows=tuple(equity_cf),
        warnings=inner.warnings,
    )
