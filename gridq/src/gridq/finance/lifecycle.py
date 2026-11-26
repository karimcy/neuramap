"""Lifecycle finance (WP-UW-17): indexation, the CM indexation rule, construction and IDC on a
month-dated timeline, and terminal / decommissioning / replacement flows.

Pure functions over the typed `LifecycleInputs` and the assumption library. `cashflows` calls the
indexation and capital-flow helpers while it builds the series; `financial_model` calls the
construction helpers when it measures returns. Nothing here invents a figure: every rate, date and
£ is a library value with a source, a user input with provenance, or absent and labelled
(the data rule; D45). Decisions: D129 (indexation), -3 (construction), -4 (flows),
-5 (reconciliation).

**The Capacity Market rule, as published** (The Electricity Capacity Regulations 2014, SI 2014/2043,
Schedule 1 para 3, retrieved 2026-11-26 from
https://www.legislation.gov.uk/uksi/2014/2043/schedule/1):

* para 3(5): "If the relevant capacity auction was a T-4 auction, PE_ix is to be calculated in
  accordance with the formula— PE_ix = CCP_i × CPI_x / CPI_base";
* para 3(7): "CPI_base" is "the average of the monthly values of CPI for the months of the
  base period referred to in regulation 11 for the relevant capacity auction"; "CPI_x" is "the
  average of the monthly values of CPI for the months of the winter ending on the 30th April
  preceding the start of year X";
* para 3(6), as it stood until 22.7.2025: "If the relevant capacity auction was a T-1 auction or a
  DSR transitional auction, PE_ix is equal to CCP_i". Omitted from 23.7.2025 by SI 2025/917 Sch
  para 18(a)(ii) (https://www.legislation.gov.uk/uksi/2025/917); the current text states no T-1
  formula.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from gridq.assumptions import Assumption, AssumptionLibrary
from gridq.finance.returns import DatedFlow
from gridq.schemas.lifecycle import (
    CapacityAgreementIndexation,
    CashflowLineBasis,
    CmAuctionType,
    ConstructionPhase,
    LifecycleFlow,
    LifecycleFlowEntry,
    LifecycleFlowStatus,
    LifecycleInputs,
    PriceBasis,
    TerminalValueBasis,
)

__all__ = [
    "CM_RULE_SOURCE",
    "CPI_KEY",
    "INDEXED_COST_LINE_KEYS",
    "CmIndexation",
    "ConstructionFunding",
    "CpiReading",
    "LifecycleCapital",
    "LifecycleInputsError",
    "accumulation_factor",
    "construction_excluded_warning",
    "construction_funding",
    "cpi_null_warning",
    "dated_equity_flows",
    "dated_project_flows",
    "escalation_factors",
    "excluded_ledger",
    "lifecycle_capital_flows",
    "lifecycle_inputs_from_fields",
    "load_lifecycle_inputs",
    "operating_month",
    "parse_lifecycle_inputs",
    "parse_lifecycle_inputs_text",
    "read_cpi",
    "resolve_cm_indexation",
]

#: The one library key the escalation reads (N13/H-15). It has had no reader since gb_v1_8.
CPI_KEY = ("financing", "cpi_pct_per_year")

#: The cost lines the library's own notes name as CPI-indexed (gb_v1_12 financing.cpi_pct_per_year,
#: "LINES INDEXED (H-15)"). Every other line is an energy-price line on the same basis as the
#: un-indexed spreads it nets against, and stays flat-nominal (D129).
INDEXED_COST_LINE_KEYS: tuple[str, ...] = (
    "opex",
    "insurance",
    "metering_idno",
    "dno",
    "business_rates",
)

#: Where the CM rule was read, for the record and the warnings.
CM_RULE_SOURCE = (
    "SI 2014/2043 Sch 1 para 3(5) and 3(7), https://www.legislation.gov.uk/uksi/2014/2043/"
    "schedule/1 (retrieved 2026-11-26)"
)

_CM_TABLE_KEY = ("capacity_market", "clearing_price_by_auction_and_delivery_year")
_CM_PRICE_KEY = ("capacity_market", "clearing_price_gbp_per_kw_yr")


# --------------------------------------------------------------------------- CPI


@dataclass(frozen=True)
class CpiReading:
    """The state of `financing.cpi_pct_per_year` on a release.

    ``absent`` (gb_v0_1..gb_v1_6: no key) and ``null`` (gb_v1_7: the empty home) both leave the
    series un-escalated, but only ``null`` is warned: an absent key is a frozen release's declared
    flat basis, while a null one is a stated gap that must never read as 0% (D45).
    """

    state: str
    pct: float | None
    assumption: Assumption | None


def read_cpi(assumptions: AssumptionLibrary) -> CpiReading:
    """Read `financing.cpi_pct_per_year` without raising (absent / null / numeric)."""
    section = getattr(assumptions, CPI_KEY[0], None)
    a = section.get(CPI_KEY[1]) if isinstance(section, dict) else None
    if a is None:
        return CpiReading("absent", None, None)
    value = a.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return CpiReading("null", None, a)
    return CpiReading("numeric", float(value), a)


def cpi_null_warning() -> str:
    """D45: a null escalation key is a gap, not 0%."""
    return (
        "financing.cpi_pct_per_year is null: escalation NOT applied (not read as 0%). Every line "
        "stays on the flat basis below until the release states a rate; supply one (D45, N13)."
    )


def escalation_factors(cpi_pct: float, horizon_years: int) -> tuple[float, ...]:
    """``E_t = (1 + c)^(t − 1)`` for operating years ``t = 1..N``: year 1 is the price base."""
    growth = 1.0 + cpi_pct / 100.0
    return tuple(growth ** (t - 1) for t in range(1, horizon_years + 1))


# --------------------------------------------------------------------------- CM indexation


@dataclass(frozen=True)
class CmIndexation:
    """How a tenored Capacity Market payment is indexed on this run (D129)."""

    auction_type: str
    indexed: bool
    year1_uplift: float
    uplift_source: str
    delivery_year: str | None
    base_period_label: str | None


def _library_cm_delivery_year(assumptions: AssumptionLibrary) -> str | None:
    """The delivery year of the T-4 auction the library's CM price is the clear of.

    Read off the library, never assumed: the T-4 entry in the by-auction table whose price equals
    ``capacity_market.clearing_price_gbp_per_kw_yr``. None when the release carries no such pair.
    """
    price = _section_get(assumptions, *_CM_PRICE_KEY)
    table = _section_get(assumptions, *_CM_TABLE_KEY)
    if price is None or table is None or not isinstance(table.value, Mapping):
        return None
    t4 = table.value.get("T-4")
    if not isinstance(t4, Mapping) or isinstance(price.value, bool):
        return None
    matches = sorted(
        year
        for year, value in t4.items()
        if isinstance(value, (int, float)) and abs(float(value) - float(price.value)) < 1e-9
    )
    return matches[-1] if matches else None


def _library_base_period(assumptions: AssumptionLibrary, delivery_year: str | None) -> str | None:
    """The library's stated CPI base period for a T-4 delivery year (needs_review in gb_v1_12)."""
    if delivery_year is None:
        return None
    table = _section_get(assumptions, *_CM_TABLE_KEY)
    if table is None or not isinstance(table.value, Mapping):
        return None
    indexation = table.value.get("indexation")
    if not isinstance(indexation, Mapping):
        return None
    periods = indexation.get("t4_cpi_base_periods")
    if not isinstance(periods, Mapping):
        return None
    label = periods.get(delivery_year)
    return str(label) if label is not None else None


def resolve_cm_indexation(
    assumptions: AssumptionLibrary,
    agreement: CapacityAgreementIndexation | None,
    cpi_pct: float,
    *,
    warnings: list[str],
) -> CmIndexation:
    """Resolve the auction type and the year-1 uplift ``U = CPI_x1 / CPI_base`` for a tenored CM
    payment on an indexed run, warning on every assumption it has to state."""
    if agreement is None:
        delivery_year = _library_cm_delivery_year(assumptions)
        base = _library_base_period(assumptions, delivery_year)
        warnings.append(
            "CM agreement auction type ASSUMED T-4: no capacity_agreement was supplied, and the "
            "library's CM price is the "
            f"{delivery_year or 'latest'} T-4 clear, so the payment is indexed per "
            f"{CM_RULE_SOURCE} (PE = CCP x CPI_x / CPI_base). Supply the agreement (auction type, "
            "delivery year) to confirm."
        )
        warnings.append(_uplift_not_applied(base, cpi_pct))
        return CmIndexation(
            auction_type=CmAuctionType.T_4.value,
            indexed=True,
            year1_uplift=1.0,
            uplift_source="not supplied: ratio 1.0 (data request)",
            delivery_year=delivery_year,
            base_period_label=base,
        )
    if agreement.auction_type is CmAuctionType.T_1:
        warnings.append(
            "T-1 CM held un-indexed: Sch 1 para 3(6) (PE=CCP) was omitted 23.7.2025 by SI "
            "2025/917 and the current text states no T-1 formula; pre-amendment rule applied "
            f"({agreement.reference}, delivery year {agreement.delivery_year})."
        )
        return CmIndexation(
            auction_type=CmAuctionType.T_1.value,
            indexed=False,
            year1_uplift=1.0,
            uplift_source="T-1: not indexed",
            delivery_year=agreement.delivery_year,
            base_period_label=None,
        )
    base = _library_base_period(assumptions, agreement.delivery_year)
    if agreement.cpi_base is None or agreement.cpi_x_year1 is None:
        warnings.append(_uplift_not_applied(base, cpi_pct))
        return CmIndexation(
            auction_type=CmAuctionType.T_4.value,
            indexed=True,
            year1_uplift=1.0,
            uplift_source="not supplied: ratio 1.0 (data request)",
            delivery_year=agreement.delivery_year,
            base_period_label=base,
        )
    uplift = agreement.cpi_x_year1 / agreement.cpi_base
    warnings.append(
        f"CM T-4 payment indexed per {CM_RULE_SOURCE}: year-1 uplift CPI_x/CPI_base = "
        f"{agreement.cpi_x_year1:g}/{agreement.cpi_base:g} = {uplift:.4f} ({agreement.reference}, "
        f"delivery year {agreement.delivery_year}); later in-term years escalate at the library "
        f"CPI forecast of {cpi_pct:g}%/yr, a projection of CPI_x rather than an observed index."
    )
    return CmIndexation(
        auction_type=CmAuctionType.T_4.value,
        indexed=True,
        year1_uplift=uplift,
        uplift_source=f"supplied: {agreement.reference}",
        delivery_year=agreement.delivery_year,
        base_period_label=base,
    )


def _uplift_not_applied(base_period: str | None, cpi_pct: float) -> str:
    return (
        "CM year-1 uplift CPI_x/CPI_base NOT applied (ratio 1.0): ONS CPI for the base period "
        f"{base_period or '(base period not stated in the library)'} and the pre-delivery winter "
        f"not supplied; in-term escalation at {cpi_pct:g}%/yr only. Understates CM if CPI rose. "
        "Data ask: the ONS CPI (All Items) monthly index for the auction's base period and for the "
        "winter before the first delivery year."
    )


# --------------------------------------------------------------------------- lifecycle flows


#: What to ask for when a flow is not stated (D131 data requests).
_DATA_REQUEST: dict[LifecycleFlow, str] = {
    LifecycleFlow.TERMINAL_VALUE: (
        "the residual-value basis at the horizon (resale, re-contract or second life), £ and price "
        "date with evidence, or a declared nil"
    ),
    LifecycleFlow.DECOMMISSIONING: (
        "the decommissioning / restoration obligation in the host-site agreement: cost, year, and "
        "whether it is net of salvage"
    ),
    LifecycleFlow.REPLACEMENT: (
        "the OEM or O&M replacement schedule by component, year and £, with the warranty terms"
    ),
}

_FLOW_TITLE = {
    LifecycleFlow.TERMINAL_VALUE: "TERMINAL VALUE",
    LifecycleFlow.DECOMMISSIONING: "DECOMMISSIONING",
    LifecycleFlow.REPLACEMENT: "REPLACEMENT",
}


def excluded_ledger() -> tuple[LifecycleFlowEntry, ...]:
    """The ledger of a run that stated no flow: all three EXCLUDED, each with its data request.

    The ledger is always populated (D131), so a reader sees the three omissions as rows
    even on the legacy path, where the one N14 warning names them in prose.
    """
    return tuple(
        LifecycleFlowEntry(
            flow=flow,
            status=LifecycleFlowStatus.EXCLUDED_NO_INPUT,
            basis_label="excluded: no input",
            data_request=_DATA_REQUEST[flow],
        )
        for flow in (
            LifecycleFlow.TERMINAL_VALUE,
            LifecycleFlow.DECOMMISSIONING,
            LifecycleFlow.REPLACEMENT,
        )
    )


@dataclass(frozen=True)
class LifecycleCapital:
    """The modelled lifecycle capital flows, per operating year ``1..N`` (signed £)."""

    p50_by_year: list[float]
    p90_by_year: list[float]
    #: The part that reduces CFADS: decommissioning and replacements, never the terminal value.
    cfads_by_year: list[float]
    entries: tuple[LifecycleFlowEntry, ...]
    excluded_labels: tuple[str, ...]
    warnings: list[str] = field(default_factory=list)


def _line_basis_for(
    price_basis: PriceBasis, escalation: Sequence[float] | None
) -> CashflowLineBasis:
    if price_basis is PriceBasis.NOMINAL_AS_ENTERED:
        return CashflowLineBasis.NOMINAL_FLAT
    return (
        CashflowLineBasis.NOMINAL_CPI_ESCALATED
        if escalation is not None
        else CashflowLineBasis.REAL_HELD_FLAT
    )


def _esc(price_basis: PriceBasis, escalation: Sequence[float] | None, year: int) -> float:
    """The escalation applied to a user £ in operating ``year`` (R2: (1+c)^(t−1), real only)."""
    if price_basis is PriceBasis.REAL_AT_AS_OF and escalation is not None:
        return escalation[year - 1]
    return 1.0


def lifecycle_capital_flows(
    lifecycle: LifecycleInputs | None,
    *,
    horizon_years: int,
    capex_total_gbp: float,
    escalation: Sequence[float] | None,
) -> LifecycleCapital | None:
    """Terminal value, decommissioning and replacements as series, a ledger row each, and warnings.

    Returns ``None`` when no flow is stated at all: the caller keeps the legacy N14 path (one
    warning naming all three omissions, byte-identical). Otherwise every flow is either MODELLED
    from its input or EXCLUDED with a labelled warning and a data request; a flow dated beyond the
    horizon is excluded and labelled, never clipped into the horizon (that would move a cost in
    time) and never refused.
    """
    if lifecycle is None or (
        lifecycle.terminal_value is None
        and lifecycle.decommissioning is None
        and lifecycle.replacements is None
    ):
        return None
    n = horizon_years
    source = f"{lifecycle.provenance.source.value}: {lifecycle.provenance.reference}"
    p50 = [0.0] * n
    p90 = [0.0] * n
    cfads = [0.0] * n
    entries: list[LifecycleFlowEntry] = []
    excluded: list[str] = []
    out: list[str] = []

    def _excluded(flow: LifecycleFlow, label: str) -> None:
        excluded.append(label)
        entries.append(
            LifecycleFlowEntry(
                flow=flow,
                status=LifecycleFlowStatus.EXCLUDED_NO_INPUT,
                basis_label="excluded: no input",
                data_request=_DATA_REQUEST[flow],
            )
        )
        out.append(
            f"LIFECYCLE — {_FLOW_TITLE[flow]} EXCLUDED (N14): no input supplied; DATA REQUEST: "
            f"{_DATA_REQUEST[flow]}."
        )

    # --- terminal value (year N only; never in CFADS) --------------------------------------
    tv = lifecycle.terminal_value
    if tv is None:
        _excluded(LifecycleFlow.TERMINAL_VALUE, "terminal/residual value at horizon end")
    else:
        if tv.basis is TerminalValueBasis.USER_GBP_AT_HORIZON:
            base = float(tv.amount_gbp or 0.0)
        elif tv.basis is TerminalValueBasis.RESIDUAL_FRACTION_OF_CAPEX:
            base = float(tv.fraction_of_capex or 0.0) * capex_total_gbp
        else:
            base = 0.0
        factor = _esc(tv.price_basis, escalation, n)
        tv50 = round(base * factor, 2)
        tv90 = round(float(tv.p90_amount_gbp) * factor, 2) if tv.p90_amount_gbp else 0.0
        p50[n - 1] += tv50
        p90[n - 1] += tv90
        p50_row = [0.0] * n
        p90_row = [0.0] * n
        p50_row[n - 1] = tv50
        p90_row[n - 1] = tv90
        entries.append(
            LifecycleFlowEntry(
                flow=LifecycleFlow.TERMINAL_VALUE,
                status=LifecycleFlowStatus.MODELLED,
                amounts_by_year_p50_gbp=p50_row,
                amounts_by_year_p90_gbp=p90_row,
                basis_label=_line_basis_for(tv.price_basis, escalation).value,
                price_basis=tv.price_basis,
                escalated=factor != 1.0,
                in_cfads=False,
                provenance=source,
            )
        )
        p90_txt = f"£{tv90:,.2f}" if tv.p90_amount_gbp else "nil — no P90 residual supplied"
        out.append(
            f"LIFECYCLE — TERMINAL VALUE MODELLED (N14): £{tv50:,.2f} at year {n}, basis "
            f"{tv.basis.value}, {tv.price_basis.value}, source {source}; in project/equity IRR, "
            f"EXCLUDED from CFADS/DSCR; P90 {p90_txt}."
        )

    # --- decommissioning (one year; reduces CFADS) ------------------------------------------
    dec = lifecycle.decommissioning
    if dec is None:
        _excluded(LifecycleFlow.DECOMMISSIONING, "decommissioning / site restoration")
    else:
        year = n if dec.year == "horizon_end" else int(dec.year)
        salvage = "net of salvage" if dec.net_of_salvage else "gross"
        if year > n:
            excluded.append("decommissioning / site restoration (beyond horizon)")
            entries.append(
                LifecycleFlowEntry(
                    flow=LifecycleFlow.DECOMMISSIONING,
                    status=LifecycleFlowStatus.EXCLUDED_BEYOND_HORIZON,
                    basis_label="excluded: beyond horizon",
                    price_basis=dec.price_basis,
                    in_cfads=True,
                    provenance=source,
                )
            )
            out.append(
                "LIFECYCLE — DECOMMISSIONING EXCLUDED — BEYOND HORIZON (N14): "
                f"£{dec.cost_gbp:,.2f} in year {year} > {n}; omission flatters the case."
            )
        else:
            factor = _esc(dec.price_basis, escalation, year)
            amount = round(-float(dec.cost_gbp) * factor, 2)
            p50[year - 1] += amount
            p90[year - 1] += amount
            cfads[year - 1] += amount
            row = [0.0] * n
            row[year - 1] = amount
            entries.append(
                LifecycleFlowEntry(
                    flow=LifecycleFlow.DECOMMISSIONING,
                    status=LifecycleFlowStatus.MODELLED,
                    amounts_by_year_p50_gbp=row,
                    amounts_by_year_p90_gbp=list(row),
                    basis_label=_line_basis_for(dec.price_basis, escalation).value,
                    price_basis=dec.price_basis,
                    escalated=factor != 1.0,
                    in_cfads=True,
                    provenance=source,
                )
            )
            out.append(
                f"LIFECYCLE — DECOMMISSIONING MODELLED (N14): £{-amount:,.2f} in year {year}, "
                f"{salvage}; deducted from CFADS."
            )

    # --- replacements (in-year; reduce CFADS; no reserve) -----------------------------------
    reps = lifecycle.replacements
    if reps is None:
        _excluded(LifecycleFlow.REPLACEMENT, "inverter and balance-of-plant replacement")
    else:
        inside = [r for r in reps if r.year <= n]
        beyond = [r for r in reps if r.year > n]
        row = [0.0] * n
        escalated = False
        bases = {_line_basis_for(r.price_basis, escalation).value for r in inside}
        for r in inside:
            factor = _esc(r.price_basis, escalation, r.year)
            escalated = escalated or factor != 1.0
            amount = round(-float(r.cost_gbp) * factor, 2)
            row[r.year - 1] += amount
        row = [round(v, 2) for v in row]
        for i, v in enumerate(row):
            p50[i] += v
            p90[i] += v
            cfads[i] += v
        status = (
            LifecycleFlowStatus.EXCLUDED_BEYOND_HORIZON
            if reps and not inside
            else LifecycleFlowStatus.MODELLED
        )
        entries.append(
            LifecycleFlowEntry(
                flow=LifecycleFlow.REPLACEMENT,
                status=status,
                amounts_by_year_p50_gbp=row if status is LifecycleFlowStatus.MODELLED else [],
                amounts_by_year_p90_gbp=list(row) if status is LifecycleFlowStatus.MODELLED else [],
                basis_label=(
                    "excluded: beyond horizon"
                    if status is LifecycleFlowStatus.EXCLUDED_BEYOND_HORIZON
                    else ("+".join(sorted(bases)) if bases else "declared none")
                ),
                price_basis=inside[0].price_basis if inside else None,
                escalated=escalated,
                in_cfads=True,
                provenance=source,
            )
        )
        if status is LifecycleFlowStatus.MODELLED:
            years = ", ".join(str(r.year) for r in sorted(inside, key=lambda r: r.year))
            total = -sum(row)
            out.append(
                f"LIFECYCLE — REPLACEMENT MODELLED (N14): {len(inside)} flow(s) totalling "
                f"£{total:,.2f} (years {years or 'none: declared no replacement'}); deducted from "
                "CFADS in-year; no maintenance reserve is modelled."
            )
        for r in beyond:
            excluded.append(f"{r.component.value} replacement in year {r.year} (beyond horizon)")
            out.append(
                f"LIFECYCLE — REPLACEMENT EXCLUDED — BEYOND HORIZON (N14): £{r.cost_gbp:,.2f} in "
                f"year {r.year} > {n} ({r.component.value}); omission flatters the case."
            )

    modelled = [e.flow.value for e in entries if e.status is LifecycleFlowStatus.MODELLED]
    not_levelised = "; ".join(excluded) if excluded else "none"
    out.append(
        "LIFECYCLE — PRICE BASIS (N14): the fixed-price recommendation levelises every modelled "
        f"lifecycle flow ({', '.join(modelled) or 'none'}), because they sit in the P50 series it "
        f"prices; NOT levelised (excluded): {not_levelised}."
    )
    if any(e.price_basis is PriceBasis.REAL_AT_AS_OF for e in entries if e.price_basis):
        out.append(
            "LIFECYCLE — PRICE DATE (R2): a real_at_as_of £ is taken to be in the model's year-1 "
            "price base; no escalation is applied between its as_of date and COD (a COD delay "
            "included)."
        )
    return LifecycleCapital(
        p50_by_year=[round(v, 2) for v in p50],
        p90_by_year=[round(v, 2) for v in p90],
        cfads_by_year=[round(v, 2) for v in cfads],
        entries=tuple(entries),
        excluded_labels=tuple(excluded),
        warnings=out,
    )


# --------------------------------------------------------------------------- construction


def construction_excluded_warning() -> str:
    """The no-input construction disclosure (D130, R7): on every run without a programme."""
    return (
        "CONSTRUCTION PHASE EXCLUDED: no construction programme supplied; capex is booked at COD, "
        "no interest during construction is charged and returns are measured from COD. This "
        "flatters IRR. Supply construction months, drawdown profile and construction rate."
    )


def operating_month(year: int, delay_months: int) -> int:
    """The month (from planned COD) at which operating ``year`` is booked: ``D + 12·t``."""
    return delay_months + 12 * year


@dataclass(frozen=True)
class ConstructionFunding:
    """The dated draws and the IDC accumulation factor for one run (D130)."""

    #: ``(months_before_cod, capex_fraction)`` pairs; a single ``(0, 1.0)`` with no programme.
    draws: tuple[tuple[int, float], ...]
    delay_months: int
    #: ``A = Σ w_j·(1 + i)^(n_j + D)``; 1.0 when no construction rate applies (IDC excluded).
    accumulation_factor: float
    idc_rate_pct: float | None
    idc_rate_source: str | None


def accumulation_factor(
    draws: Sequence[tuple[int, float]], annual_rate_pct: float, delay_months: int
) -> float:
    """``A = Σ w_j·(1 + i)^(n_j + D)``, ``i = (1 + r/100)^(1/12) − 1``: the factor by which debt
    drawn pro-rata with the draws grows, with interest rolled up, to the actual COD."""
    monthly = (1.0 + annual_rate_pct / 100.0) ** (1.0 / 12.0) - 1.0
    return sum(w * (1.0 + monthly) ** (n + delay_months) for n, w in draws)


def construction_funding(
    phase: ConstructionPhase | None,
    *,
    delay_months: int,
    rate_pct: float | None,
    rate_source: str | None,
) -> ConstructionFunding:
    """Assemble the dated draws and the IDC factor. With no programme the whole capex is one draw
    at planned COD (a pure COD delay); with no rate the factor is 1.0 (IDC excluded, warned by the
    caller)."""
    draws = (
        tuple((p.months_before_cod, p.capex_fraction) for p in phase.drawdown)
        if phase is not None
        else ((0, 1.0),)
    )
    factor = accumulation_factor(draws, rate_pct, delay_months) if rate_pct is not None else 1.0
    return ConstructionFunding(
        draws=draws,
        delay_months=delay_months,
        accumulation_factor=factor,
        idc_rate_pct=rate_pct,
        idc_rate_source=rate_source,
    )


def dated_project_flows(
    capex_gbp: float,
    funding: ConstructionFunding,
    operating_fcf_by_year: Sequence[float],
    *,
    extra_by_year: Sequence[float] | None = None,
) -> list[DatedFlow]:
    """The unlevered project on the dated timeline: ``−w_j·C`` at ``−n_j``, FCF at ``D + 12t``.

    Pre-debt, so it carries no IDC (R3). ``extra_by_year`` adds a per-year amount (the customer
    price revenue in the closed loop) to the operating flows.
    """
    flows: list[DatedFlow] = [(-n, -w * capex_gbp) for n, w in funding.draws]
    for t, fcf in enumerate(operating_fcf_by_year, start=1):
        extra = extra_by_year[t - 1] if extra_by_year is not None else 0.0
        flows.append((operating_month(t, funding.delay_months), fcf + extra))
    return flows


def dated_equity_flows(
    capex_gbp: float,
    funding: ConstructionFunding,
    principal_at_cod_gbp: float,
    debt_service_by_year_gbp: Sequence[float],
    operating_fcf_by_year: Sequence[float],
    *,
    extra_by_year: Sequence[float] | None = None,
) -> list[DatedFlow]:
    """The equity investor's dated flows (D130).

    Debt is drawn pro-rata with capex at share ``d = P_COD / (C·A)``, so equity funds
    ``(1 − d)·w_j·C`` at each draw. From actual COD equity receives FCF less debt service on
    ``P_COD`` (IDC included in it), to the LONGER of the horizon and the debt schedule (the N11
    rule: post-horizon service is a real negative flow), rounded to whole pence like `equity_irr`.
    """
    a = funding.accumulation_factor
    drawn_share = principal_at_cod_gbp / (capex_gbp * a) if capex_gbp > 0 and a > 0 else 0.0
    flows: list[DatedFlow] = [(-n, -(1.0 - drawn_share) * w * capex_gbp) for n, w in funding.draws]
    periods = max(len(operating_fcf_by_year), len(debt_service_by_year_gbp))
    for t in range(1, periods + 1):
        fcf = operating_fcf_by_year[t - 1] if t <= len(operating_fcf_by_year) else 0.0
        extra = (
            extra_by_year[t - 1] if extra_by_year is not None and t <= len(extra_by_year) else 0.0
        )
        service = debt_service_by_year_gbp[t - 1] if t <= len(debt_service_by_year_gbp) else 0.0
        flows.append((operating_month(t, funding.delay_months), round(fcf + extra - service, 2)))
    return flows


def _section_get(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    sect = getattr(assumptions, section, None)
    return sect.get(name) if isinstance(sect, dict) else None


# --------------------------------------------------------------------------- input loading


class LifecycleInputsError(ValueError):
    """Supplied lifecycle inputs could not be read or did not validate. Reader-facing message."""


def parse_lifecycle_inputs(data: object) -> LifecycleInputs:
    """Validate a mapping (already parsed from JSON/YAML/form) into `LifecycleInputs`."""
    if isinstance(data, LifecycleInputs):
        return data
    if not isinstance(data, Mapping):
        raise LifecycleInputsError(
            f"lifecycle inputs must be a mapping of fields, got {type(data).__name__}"
        )
    try:
        return LifecycleInputs.model_validate(dict(data))
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(p) for p in err.get('loc', ())) or '(inputs)'}: "
            f"{err.get('msg', 'invalid')}"
            for err in exc.errors()
        ]
        raise LifecycleInputsError(f"invalid lifecycle inputs: {'; '.join(lines)}") from exc


def parse_lifecycle_inputs_text(text: str, *, suffix: str = ".json") -> LifecycleInputs:
    """Parse JSON or YAML text (YAML for ``.yaml``/``.yml``) into `LifecycleInputs`."""
    try:
        data = yaml.safe_load(text) if suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise LifecycleInputsError(
            f"lifecycle inputs are not valid {suffix.lstrip('.')}: {exc}"
        ) from exc
    return parse_lifecycle_inputs(data)


def load_lifecycle_inputs(path: Path) -> LifecycleInputs:
    """Read a lifecycle-inputs file (the CLI's ``--lifecycle``; JSON, or YAML by extension)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise LifecycleInputsError(f"could not read lifecycle inputs file {path}: {exc}") from exc
    return parse_lifecycle_inputs_text(text, suffix=Path(path).suffix or ".json")


def lifecycle_inputs_from_fields(fields: Mapping[str, object]) -> LifecycleInputs | None:
    """The app form's ``lifecycle_json`` field as `LifecycleInputs`; ``None`` when blank."""
    raw = fields.get("lifecycle_json")
    if isinstance(raw, str) and raw.strip():
        return parse_lifecycle_inputs_text(raw, suffix=".json")
    return None
