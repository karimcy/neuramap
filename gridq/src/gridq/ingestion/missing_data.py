"""Missing-data detection (M0).

Inspects a validated `SiteModel` and reports which underwriting inputs are absent or
incomplete. This is the raw material the ranked data ask is built from
(`improvement_asks.build_ranked_data_asks`).

This module embodies the data-integrity rule: a missing input is **detected and reported**,
never filled. It assigns nothing and prices nothing — it only names what is missing and why
it matters, so the installer back-and-forth becomes a precise ask (System Brief M0).
"""

from __future__ import annotations

from pydantic import Field

from gridq.schemas.enums import ConsentStatus, CreditStatus, DataTier
from gridq.schemas.provenance import GridQModel
from gridq.schemas.site import SiteModel

# Stable base priorities (1 = highest impact on bankability / quote fidelity). These mirror
# the canonical ask order in docs/parallel_development_plan.md §5 and the task brief: data
# tier first, then the priced floor, then the gates, then credit, then gated upside.
PRIORITY_HALF_HOURLY = 1
PRIORITY_TARIFF = 2
PRIORITY_CONNECTION = 3
PRIORITY_DNO_REGION = 4
PRIORITY_EXPORT_CONSENTS = 5
PRIORITY_SUPPLIER_CONSENT = 6
PRIORITY_PHYSICAL_GATES = 7
PRIORITY_COUNTERPARTY = 8
PRIORITY_WHOLESALE_FORECAST = 9


class MissingDataItem(GridQModel):
    """One missing/incomplete input, with why it matters and what it blocks."""

    ask_id: str
    title: str
    why: str
    category: str  # load | tariff | connection | consents | physical | counterparty | market
    blocks: list[str] = Field(default_factory=list)
    priority: int
    detail: str | None = None


def _exporting(site: SiteModel) -> bool:
    return bool(site.connection.mec_kw and site.connection.mec_kw > 0)


def detect_missing_data(
    site: SiteModel,
    *,
    wholesale_requested: bool = False,
) -> list[MissingDataItem]:
    """Return the missing/incomplete inputs for ``site``.

    ``wholesale_requested`` should be True when wholesale / P415 analysis has been asked for;
    only then is the wholesale price-forecast file listed as a missing input (it is gated
    upside, not part of the bankable floor).
    """
    items: list[MissingDataItem] = []

    items += _check_half_hourly(site)
    items += _check_tariff(site)
    items += _check_connection(site)
    items += _check_dno_region(site)
    items += _check_export_consents(site)
    items += _check_supplier_consent(site)
    items += _check_physical_gates(site)
    items += _check_counterparty(site)
    if wholesale_requested:
        items += _check_wholesale_forecast(site)

    return items


def _check_half_hourly(site: SiteModel) -> list[MissingDataItem]:
    if (
        site.data_tier == DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
        and site.load_data.has_hh_coverage
    ):
        return []
    return [
        MissingDataItem(
            ask_id="hh_12_months",
            title="12 months of half-hourly meter data",
            why="Half-hourly load is the single biggest fidelity lever; ≥12 validated months "
            "unlock tier T2 and a firm, underwritten quote rather than a banded indicative one.",
            category="load",
            blocks=["data_tier:T2", "underwritten_quote"],
            priority=PRIORITY_HALF_HOURLY,
            detail=f"current tier: {site.data_tier.value}",
        )
    ]


def _check_tariff(site: SiteModel) -> list[MissingDataItem]:
    t = site.tariff_stack
    missing_parts: list[str] = []
    if t.standing_charge_per_day_gbp is None:
        missing_parts.append("standing charge")
    if not t.import_rates:
        missing_parts.append("import unit rates")
    if t.duos_red_rate is None and t.duos_amber_rate is None and t.duos_green_rate is None:
        missing_parts.append("DUoS bands")
    if not t.levies:
        missing_parts.append("levy lines")
    if not missing_parts:
        return []
    return [
        MissingDataItem(
            ask_id="tariff_stack",
            title="Full tariff: standing charge, unit rates, DUoS bands, levy lines",
            why="The import-side bankable floor (supply-rate arbitrage, DUoS red-band "
            "avoidance) is computed from the full tariff stack; missing lines mean the floor "
            "cannot be priced.",
            category="tariff",
            blocks=["bankable_floor"],
            priority=PRIORITY_TARIFF,
            detail="missing: " + ", ".join(missing_parts),
        )
    ]


def _check_connection(site: SiteModel) -> list[MissingDataItem]:
    c = site.connection
    missing_parts: list[str] = []
    if c.mic_kw == 0:
        missing_parts.append("MIC (import capacity reads 0)")
    if c.mec_kw is None:
        missing_parts.append("MEC (export capacity)")
    if c.asc_kw is None:
        missing_parts.append("ASC band")
    if not missing_parts:
        return []
    detail = "missing: " + ", ".join(missing_parts)
    # D5 (issue 018): when MIC is unset, suggest the observed metered peak as a FLOOR for it - a
    # data-ask hint only. MIC is a DNO fact, never derivable from the meter, so it is NEVER
    # auto-populated; the suggestion just tells the user the value cannot be below observed load.
    observed_peak = site.load_data.max_import_kw if site.load_data else None
    if c.mic_kw == 0 and observed_peak:
        detail += (
            f". Suggested floor from metered load: MIC must be at least the observed peak "
            f"{observed_peak:.0f} kW; confirm the actual MIC/ASC with the DNO (never auto-filled)."
        )
    return [
        MissingDataItem(
            ask_id="connection_mic_mec_asc",
            title="Connection capacities: MIC / MEC / ASC",
            why="Charge load counts against MIC/ASC and export against MEC; without confirmed "
            "capacities the dispatch is unbounded and headroom value cannot be priced where "
            "it binds.",
            category="connection",
            blocks=["dispatch_bounds", "mic_asc_headroom_stream"],
            priority=PRIORITY_CONNECTION,
            detail=detail,
        )
    ]


def _check_dno_region(site: SiteModel) -> list[MissingDataItem]:
    if site.connection.dno_region:
        return []
    return [
        MissingDataItem(
            ask_id="dno_region",
            title="DNO region",
            why="The DNO region sets the DUoS band structure and whether a local-flexibility "
            "(Piclo) tender is open in the site's area; locational value cannot be placed "
            "without it.",
            category="connection",
            blocks=["duos_bands", "local_flex_piclo"],
            priority=PRIORITY_DNO_REGION,
            detail="can often be derived from postcode — confirm.",
        )
    ]


def _check_export_consents(site: SiteModel) -> list[MissingDataItem]:
    c = site.consents
    exporting = _exporting(site)
    if exporting:
        # An exporting site must confirm export consents + MPAN before they firm up - UNLESS all
        # three are already declared (G99 and G100 approved AND an export MPAN recorded), in which
        # case the export status is fully declared and there is nothing left to ask (issue 018/D8).
        fully_declared = (
            c.g99 == ConsentStatus.APPROVED
            and c.g100 == ConsentStatus.APPROVED
            and c.export_mpan is not None
        )
        relevant = not fully_declared
    else:
        # A non-exporting site only triggers the ask when export potential is genuinely
        # unknown — a definite state (e.g. not_started/not_required) is not a missing input.
        relevant = ConsentStatus.UNKNOWN in {c.g99, c.g100}
    if not relevant:
        return []
    return [
        MissingDataItem(
            ask_id="export_consents",
            title="Export status: G99 / G100 consent state and export MPAN",
            why="Export-side and gated-upside streams (wholesale/BM via P415) cannot be firmed "
            "without G99/G100 consent state and an export MPAN; export is gated on these.",
            category="consents",
            blocks=["export_streams", "wholesale_p415_gate"],
            priority=PRIORITY_EXPORT_CONSENTS,
            detail=(
                f"exporting={exporting}; g99={c.g99.value}; g100={c.g100.value}; "
                f"export_mpan={'set' if c.export_mpan else 'missing'}"
            ),
        )
    ]


def _check_supplier_consent(site: SiteModel) -> list[MissingDataItem]:
    state = site.consents.supplier_consent_state
    if state not in {ConsentStatus.NOT_STARTED, ConsentStatus.UNKNOWN}:
        return []
    return [
        MissingDataItem(
            ask_id="supplier_consent",
            title="Supplier consent (host supply-contract interaction)",
            why="The battery's import interacts with the host's supply contract; supplier "
            "consent state is a listed condition before an underwritten quote becomes firm.",
            category="consents",
            blocks=["underwriting_condition"],
            priority=PRIORITY_SUPPLIER_CONSENT,
            detail=f"supplier_consent_state={state.value}",
        )
    ]


def _check_physical_gates(site: SiteModel) -> list[MissingDataItem]:
    g = site.physical_gates
    missing_parts: list[str] = []
    if g.footprint_available is None:
        missing_parts.append("footprint")
    if g.fire_separation_ok is None:
        missing_parts.append("fire separation")
    if g.access_ok is None:
        missing_parts.append("access")
    if not missing_parts:
        return []
    return [
        MissingDataItem(
            ask_id="physical_gates",
            title="Physical gates: footprint / fire separation / access",
            why="Physical pass/fail gates are listed conditions on an underwritten quote; an "
            "unknown gate keeps the quote conditional.",
            category="physical",
            blocks=["underwriting_condition"],
            priority=PRIORITY_PHYSICAL_GATES,
            detail="unknown: " + ", ".join(missing_parts),
        )
    ]


def _check_counterparty(site: SiteModel) -> list[MissingDataItem]:
    cp = site.counterparty
    if not cp.company_number:
        # No identity yet — the single, highest-leverage counterparty ask. The credit-band ask
        # below is deliberately suppressed here so a site with no identity yields one ask, not two.
        return [
            MissingDataItem(
                ask_id="counterparty_identity",
                title="Counterparty legal entity and company number",
                why="The deterministic credit mapping (M9) keys off the registered legal entity / "
                "company number; without it the rate and tenor cannot be set and the case cannot "
                "bank.",
                category="counterparty",
                blocks=["credit_mapping"],
                priority=PRIORITY_COUNTERPARTY,
                detail=f"legal_name={cp.legal_name!r}; company_number missing",
            )
        ]
    if cp.credit_status in {CreditStatus.UNKNOWN, CreditStatus.NOT_ASSESSED}:
        # Identity is known but the counterparty has no credit band, so the deterministic
        # credit->rate/tenor mapping has nothing to map. Distinct from the identity ask; no band
        # is assumed.
        return [
            MissingDataItem(
                ask_id="counterparty_credit_status",
                title="Counterparty credit assessment (band)",
                why="The deterministic credit->rate/tenor mapping (M9) needs a credit band "
                "(strong/adequate/weak) to set the debt rate and tenor; without it the financing "
                "terms are unavailable and the case cannot bank. No band is assumed.",
                category="counterparty",
                blocks=["credit_mapping"],
                priority=PRIORITY_COUNTERPARTY,
                detail=(
                    f"company_number={cp.company_number!r}; credit_status={cp.credit_status.value}"
                ),
            )
        ]
    return []


def _check_wholesale_forecast(site: SiteModel) -> list[MissingDataItem]:
    return [
        MissingDataItem(
            ask_id="wholesale_forecast_file",
            title="Wholesale price forecast file (e.g. Modo export)",
            why="Wholesale / P415 analysis is gated upside priced from a supplied forecast "
            "curve — never generated. A Modo-style forecast file is required to model it at all.",
            category="market",
            blocks=["wholesale_p415_upside"],
            priority=PRIORITY_WHOLESALE_FORECAST,
            detail="gated upside — never in the priced floor.",
        )
    ]
