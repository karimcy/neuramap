"""Per-stream valuation of a solved dispatch schedule — the dispatch engine's own £ (D18).

This is where the **site-derived** per-stream value the value-stack docs always promised "lands
with the dispatch engine (M3)" actually lives. The value stack is consulted **only** for each
stream's gate (firmness / gate-status / unlock condition) and its constraint descriptors; the £ is
computed here from the *solved half-hourly schedule* and the site's own tariff (never the
``£/MW/yr × power`` proxy). No value-stack file is modified — the proxy formulas remain as a
labelled fallback for callers that do not run the MILP.

Attribution partitions the MILP objective with no double-counting:
- **tariff arbitrage** = the energy-band component of the bill reduction (site ``import_rates``),
- **DUoS red-band avoidance** = the DUoS component (dominated by red-band discharge),
- **MIC/ASC headroom** = £0 (its value is realised through the connection-bounded arbitrage; pricing
  it separately would triple-count the same MWh),
- **PPA / bill savings** = £0 (the customer-facing aggregate; the PPA £/kWh is finance's, not the
  stack's),
- **Capacity Market / local flex** = conditional upside from the assumption library × the schedule
  aggregates (held out of the priced floor until their gate context confirms),
- **wholesale / BM / ancillary** = gated upside, £0 until a route-to-market partner lands.

Every £ is an assumption-library or tariff number × a dispatched quantity; a placeholder /
needs_review assumption flows through as a loud not-bankable warning, never a silent figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from gridq.assumptions import AssumptionLibrary
from gridq.optimisation._assumptions import (
    duration_rescaled_rate,
    maybe,
    numeric,
    placeholder_note,
    value_for_duration,
)
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    DerivedAssumption,
    Firmness,
    MarketStream,
    SiteModel,
    StudyBasis,
    ValueStreamResult,
    ceiling_for_key,
)
from gridq.value_stack import gates
from gridq.value_stack.capacity_budget import (
    POWER_BUDGET_STREAMS,
    CapacityClaim,
    allocate_capacity,
)
from gridq.value_stack.gb_streams import (
    _DEFAULT_LOCAL_FLEX_CALL_RATE as _DEFAULT_CALL_RATE,
)
from gridq.value_stack.gb_streams import (
    _LOCAL_FLEX_CALL_RATE_KEY as _CALL_RATE_KEY,
)
from gridq.value_stack.gb_streams import (
    ELIGIBILITY_DATA_ASK,
    WHOLE_MW_ORDER_STREAMS,
    AgreedCapacityValue,
    ClampedLocalFlexAward,
    aggregation_route_notes,
    clamped_local_flex_awards,
    dfs_seasonal_value_range_gbp,
    dfs_utilisation_rate_gbp_per_kwh,
    dfs_zone_for_site,
    eac_response_value,
    eligibility_threshold_mw,
    local_flex_availability_value,
    local_flex_award_window_hours,
    local_flex_awarded_zone_clear,
    local_flex_call_rate,
    local_flex_utilisation_adjustment,
    market_entry_verdict,
    sellable_volume_mw,
    sourced_utilisation_performance_ratio,
)
from gridq.value_stack.interface import GateContext, GateResult, GateStatus
from gridq.value_stack.registry import StreamRegistry, default_registry

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecast

_RTE_KEY = "round_trip_efficiency.ac_round_trip"

#: The Capacity Market duration de-rating key, named ONCE because two things now key on it: the
#: provenance entry :func:`_derated_deliverable_mw` credits when it reads the factor, and the
#: volume-basis guard in :func:`_consented_gated_per_mw_yr_value`, which arms itself off exactly
#: that provenance entry. A second literal copy would let the guard silently stop firing the day the
#: key was renamed — the guard would read "the factor was not consulted" and publish the figure.
_CM_DERATE_KEY = "capacity_market.de_rating_factor_by_duration_h"

#: The ``gate_status`` a D46 study-basis export entry carries. Deliberately NOT
#: ``GateStatus.OPEN.value``: no gate opened, no consent was given, and a reader (or a template)
#: filtering on the gate status must be able to tell a study-basis floor entry from a real
#: consent-opened one without parsing prose.
_STUDY_BASIS_GATE_STATUS = "study_basis_export_in_floor"

# Streams priced (£/MW/yr) from a customer-agreed rate when consented (Revenue-Data Drive / D43).
_CONSENTED_PER_MW_YR_STREAMS = (
    MarketStream.BALANCING_MECHANISM,
    MarketStream.ANCILLARY_DC_DM_DR,
)

#: Greppable prefix on the refusal-to-price note emitted when a market-eligible BM / ancillary
#: stream would otherwise be priced on the Capacity Market de-rating basis. Consumers filter on the
#: PREFIX, never the prose (the house pattern — `outputs/consulting_report.py:374`,
#: `outputs/viewmodel.py:174`), so the explanation after it can be improved without breaking them.
#:
#: **Deliberately free of em and en dashes.** `outputs.formatting.no_dashes` rewrites both at render
#: time (an em dash becomes a comma), and stream notes pass through it on their way into the
#: underwriting memo (`outputs/underwriting.py:274`). A prefix carrying an em dash would therefore
#: be un-greppable in the very document the underwriter reads, which is the one place it must hold.
CM_DERATING_BASIS_REFUSAL = "REFUSED TO PRICE (WRONG VOLUME BASIS, D43 vs D-102):"

#: Greppable prefix on the note emitted when the D43 basis was used **with** an explicit
#: acknowledgement. Deliberately a DIFFERENT prefix from the refusal: a reader (or a report filter)
#: must be able to tell "this figure was withheld" from "this figure is present and understated"
#: without parsing prose, because the two demand opposite actions.
CM_DERATING_BASIS_ACKNOWLEDGED = "PRICED ON AN ACKNOWLEDGED WRONG VOLUME BASIS (D43 vs D-102):"


@dataclass(frozen=True)
class CapacityMarketDeratingBasisAcknowledgement:
    """The EXPLICIT, ATTRIBUTED opt-in that lets a ≥1 MW unit price BM / ancillary on D43's basis.

    **Why it exists.** D43 (locked) prices BM and the DC/DM/DR family as ``agreed £/MW/yr ×
    de-rated deliverable MW``, "parity with CM's volume basis" (`docs/changelog.md`, slice 2b). The
    parity is wrong, and `docs/bess_modelling_requirements.md:1272` (D-102) says what the basis
    should be: **BM offer/bid volumes bounded by the SoC-feasible envelope**, per settlement period.
    A CM de-rating factor answers a different physical question — it converts a duration-limited
    asset into equivalent FIRM CAPACITY ACROSS A MULTI-HOUR STRESS EVENT — whereas the BM and the
    six dynamic-response products pay for volume held or delivered in a **30-minute settlement
    period or EFA block**, which a duration-limited battery can serve at full nameplate power.

    The owner's decision (Olly) is to **keep D43's basis for now** and guarantee that no figure is
    priced on it *silently*.

    **Scope corrected 2026-08-18 (Olly).** "Cannot be priced silently" is a rule about the
    **basis**, and the guard originally armed on **size** — refusing every consented, rated
    ≥1 MW unit, including the ones whose volume never touched a de-rating factor (see
    :func:`_consented_gated_per_mw_yr_value` for the measurement). It therefore bites only on the
    branch that genuinely imports the CM factor: no solver-measured deliverable capability, so the
    volume is ``sized power × the CM duration factor``. That is a narrowing of the guard's reach and
    an INCREASE in reported revenue where a capability was measured; it is not a relaxation of the
    1 MW eligibility threshold, which is statutory, separately enforced by D-92, and untouched.

    **What it is NOT.** Not consent, not a rate, not a volume. It authorises nothing about the
    market and asserts nothing about the customer — it records that a **person** looked at a known-
    wrong volume basis and chose to publish a figure built on it anyway. Same shape as
    `gridq.optimisation.engine.ExportFeasibilityOverride` and `gridq.schemas.StudyBasis`: no
    default, no boolean, no environment variable, and the acknowledgement sentence must be quoted
    verbatim so it lands in the run record in words rather than as a truthy flag.

    **Direction of effect, stated because it is counter-intuitive.** Correcting the basis moves BM
    and ancillary revenue **UP** (≈9.1× on a 1-hour asset: gb_v1_8 de-rates 1 h storage to 0.1099).
    So a figure produced under this acknowledgement is **understated**, and the correction is an
    increase — which needs scrutiny rather than celebration, because a 9× uplift arriving in a
    priced floor on an unreviewed basis is how a floor stops being a floor.
    """

    #: The verbatim sentence `acknowledgement` must carry. Prose, deliberately: a boolean cannot be
    #: misread but it also cannot be *quoted*, and this string ends up in the run record.
    ACKNOWLEDGEMENT: ClassVar[str] = (
        "acknowledged: BM and ancillary are priced on the Capacity Market de-rating basis, "
        "which is not the SoC-feasible settlement-period basis these markets pay for, so this "
        "figure is understated"
    )

    #: Who directed the run to keep D43's basis at this size (a person — never "the system").
    authorised_by: str
    #: Why the known-wrong basis is being kept for this run, in the words of whoever authorised it.
    justification: str
    #: Must equal `ACKNOWLEDGEMENT` exactly.
    acknowledgement: str

    def __post_init__(self) -> None:
        if self.acknowledgement != self.ACKNOWLEDGEMENT:
            raise ValueError(
                "CapacityMarketDeratingBasisAcknowledgement.acknowledgement must be the verbatim "
                f"sentence {self.ACKNOWLEDGEMENT!r}. It exists so a known-wrong volume basis "
                "cannot be re-enabled by a truthy flag: whoever enables it states, in the record, "
                "which basis was used and that the resulting figure is understated."
            )
        if not self.authorised_by.strip() or not self.justification.strip():
            raise ValueError(
                "CapacityMarketDeratingBasisAcknowledgement requires a non-blank authorised_by and "
                "justification: an unattributed, unexplained decision to publish a figure on a "
                "known-wrong volume basis is exactly the silent default this input is shaped to "
                "make impossible."
            )

    def note(self, stream: MarketStream, threshold_mw: float | None) -> str:
        """The sentence stated beside every figure this acknowledgement permitted."""
        size = (
            f"at or above this market's sourced {threshold_mw:,.3f} MW minimum unit size"
            if threshold_mw is not None
            else "at a size where this market is enterable"
        )
        return (
            f"{CM_DERATING_BASIS_ACKNOWLEDGED} {stream.value} is priced {size} on D43's Capacity "
            "Market de-rating basis, which "
            f"{self.authorised_by.strip()} explicitly acknowledged as wrong for this market: "
            f"{self.justification.strip()} Acknowledged as: “{self.ACKNOWLEDGEMENT}”. THE FIGURE "
            "IS UNDERSTATED, not conservative-by-accident: the correct basis is the SoC-feasible "
            "envelope per settlement period (docs/bess_modelling_requirements.md:1272, D-102) and "
            "adopting it moves this revenue UP by roughly 9.1× on a 1-hour asset. Do not treat the "
            "eventual increase as upside discovered; treat it as a basis correction requiring "
            "scrutiny."
        )


def _cm_derating_basis_diagnosis() -> list[str]:
    """The diagnosis of D43's volume basis, in the words both paths must share.

    Path-NEUTRAL by construction. The refusal and the acknowledged path attach the same three
    sentences beneath their own verdict line, so one defect can never acquire two stories — and,
    concretely, so a PUBLISHED figure can never carry a sentence claiming it was withheld.

    Written to teach from the message alone: the correct basis, why the current one is wrong, the
    magnitude, the cliff, and the direction of the fix.
    """
    return [
        # The single most important sentence, and therefore the one both paths state IDENTICALLY: a
        # published-but-understated figure and a withheld one owe the reader the same correct basis.
        "THE CORRECT BASIS is the SoC-feasible envelope per settlement period — BM offer/bid "
        "volumes 'bounded by the SoC-feasible envelope' "
        "(docs/bess_modelling_requirements.md:1272, D-102). The conservatism of that bound against "
        "the volume basis NESO measures the rate on must be quantified, not left implicit, which "
        "the same requirement line also says.",
        "WHY THE CM PARITY IS WRONG: a Capacity Market de-rating factor converts a "
        "duration-limited asset into equivalent FIRM CAPACITY ACROSS A MULTI-HOUR STRESS EVENT. "
        "The Balancing Mechanism and the DC/DM/DR family pay for volume held or delivered in a "
        "30-MINUTE SETTLEMENT PERIOD or EFA BLOCK. Those are different physical questions, and the "
        "CM factor is calibrated for the first. MAGNITUDE: gb_v1_8 de-rates a 1-hour battery to "
        "0.1099, yet a 1-hour battery can deliver full nameplate power for a 30-minute settlement "
        "period — so D43's basis understates BM capability by roughly 9.1× on a short-duration "
        "asset.",
        "IT IS A CLIFF, NOT A HAIRCUT: the de-rated MW is then truncated to whole MW (this market "
        "accepts sell orders only in integer MW), so on D43's basis a genuinely eligible "
        "4 MW / 2 h unit books £0 because 4 × 0.2198 = 0.879 MW truncates to zero. A £0 from that "
        "route reads as an order-granularity limit and is really the imported CM de-rating.",
        "DIRECTION OF EFFECT: THE FIX MOVES THIS REVENUE UP, by roughly 9.1× on a 1-hour asset. An "
        "increase of that size landing in a priced floor deserves scrutiny, not celebration. THE "
        "SOURCED CORRECT DEFINITION ALREADY EXISTS — `value_stack.gb_streams.sellable_volume_mw` "
        "(plain deliverable MW in whole-MW orders, no CM de-rating, F and K applied separately for "
        "DC/DM/DR per D-158) — and the PROXY pricing path already consumes it, so the engine and "
        "proxy paths disagree by roughly 9× on this stream today.",
    ]


def _cm_derating_basis_refusal(
    stream: MarketStream, threshold_mw: float | None, route: str
) -> list[str]:
    """The refusal verdict for ``stream`` at a market-eligible size, plus the shared diagnosis."""
    size = (
        f"{threshold_mw:,.3f} MW (this market's sourced minimum unit size, "
        "`value_stack.gb_streams.eligibility_threshold_mw`)"
        if threshold_mw is not None
        else "the size at which this market becomes enterable"
    )
    entry = (
        "it clears that minimum standalone"
        if route == "standalone"
        else "it clears that minimum inside an aggregated portfolio"
        if route == "aggregated"
        else f"its entry route is {route!r}"
    )
    return [
        f"{CM_DERATING_BASIS_REFUSAL} {stream.value} is size-eligible ({entry}) at or above "
        f"{size}, and D43 would price it as `agreed £/MW/yr × CM-de-rated deliverable MW`. That "
        "volume basis is WRONG FOR THIS MARKET, so the £ is withheld rather than published: this "
        "stream stays £0, out of the priced floor, out of P50/P90 and out of the DSCR.",
        *_cm_derating_basis_diagnosis(),
        "TO PROCEED ANYWAY on D43's basis, pass a "
        "`CapacityMarketDeratingBasisAcknowledgement` (authorised_by + justification + the "
        "verbatim acknowledgement) to `build_value_streams`; the figure is then published and "
        "labelled UNDERSTATED. Never re-enable this basis with a boolean, and never by widening "
        "the eligibility gate.",
    ]


def _consent_agreed_rate(site: SiteModel, stream: MarketStream) -> float | None:
    """The customer-agreed rate for ``stream`` (£, unit stream-dependent) when consented, else None.

    Consent is *authorisation*, not value (D43): a captured agreed rate is honoured only when the
    customer explicitly consented to the stream at intake (estimation-ladder step 2 — real
    contracted > agreed rate > sourced/assumption > £0). No consent, or no captured rate, returns
    ``None`` and the caller falls back to the sourced/assumption number or £0. Nothing is invented.
    """
    if not gates.stream_consented(site, stream):
        return None
    return gates.agreed_rate(site, stream)


def _aggregator_cut(
    site: SiteModel, assumptions: AssumptionLibrary
) -> tuple[float, str, list[str]]:
    """Aggregator / route-to-market take on gross local-flex revenue: ``(cut, source, used)``.

    Mirrors the proxy path (``gb_streams._aggregator_cut``) so the engine and the fallback net the
    cut identically (fixes the long-standing engine-path cut-ignored bug, D43). Precedence: a
    per-site agreed rate (``export_arrangement.aggregator_cut_pct``) wins; else the assumption
    default (``local_flex_piclo.aggregator_cut_pct``); else ``0.0`` — no cut is ever invented, so
    gross == net when neither is supplied (byte-identical to any release without the key).
    """
    arrangement = getattr(site, "export_arrangement", None)
    if arrangement is not None and arrangement.aggregator_cut_pct is not None:
        return float(arrangement.aggregator_cut_pct), "site export arrangement", []
    a = maybe(assumptions, "local_flex_piclo", "aggregator_cut_pct")
    v = numeric(a)
    if a is not None and v is not None:
        return v, "assumption", ["local_flex_piclo.aggregator_cut_pct"]
    return 0.0, "none", []


def _derated_deliverable_mw(
    schedule: HalfHourlyDispatchSchedule, assumptions: AssumptionLibrary
) -> tuple[float | None, str, list[str]]:
    """De-rated deliverable MW — parity with CM (locked D43 volume basis for BM/ancillary).

    Prefers the engine-reserved capability the solver actually held (``deliverable_capacity_mw``);
    else sized power × the CM de-rating factor for the schedule duration. Returns
    ``(mw|None, note, assumptions_used)`` — ``None`` when the de-rating table is missing (never
    invents a volume).
    """
    if schedule.deliverable_capacity_mw is not None:
        mw = schedule.deliverable_capacity_mw
        # NOT "de-rated", which is what this note used to say. This branch returns the solver's own
        # committed reservation VERBATIM and reads no de-rating factor at all (`milp_solver.py:1888`
        # sums the committed kW of the compiled availability windows). The old wording was the
        # misnomer that made the volume-basis guard's size-keyed premise look right, and it cost BM
        # and DC/DM/DR every £ they could have earned — so the label states which quantity this is.
        return (
            mw,
            f"engine-reserved deliverable capability {round(mw, 4)} MW (the solver's own committed "
            "reservation; NO de-rating factor is applied at this step)",
            [],
        )
    derate_a = maybe(assumptions, "capacity_market", "de_rating_factor_by_duration_h")
    if derate_a is None:
        return None, "capacity_market.de_rating_factor_by_duration_h missing", []
    derate, warn = value_for_duration(derate_a.value, schedule.duration_h)
    if derate is None:
        return None, warn or "de-rating unavailable for the schedule duration", []
    mw = schedule.power_mw * derate
    note = (
        f"de-rated deliverable {round(mw, 4)} MW (sized power {schedule.power_mw} MW × de-rating "
        f"{derate})"
    )
    return mw, note, [_CM_DERATE_KEY]


def _result(
    stream: MarketStream,
    gate: GateResult,
    *,
    revenue: float = 0.0,
    cost: float = 0.0,
    assumptions_used: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> ValueStreamResult:
    """Assemble a `ValueStreamResult` carrying the gate-derived firmness + the engine-computed £."""
    return ValueStreamResult(
        stream=stream,
        annual_revenue_gbp=round(max(0.0, revenue), 2),
        annual_cost_gbp=round(max(0.0, cost), 2),
        firmness=gate.firmness,
        gate_status=gate.gate_status.value,
        unlock_condition=gate.unlock_condition,
        assumptions_used=assumptions_used or [],
        warnings=list(gate.warnings) + list(extra_warnings or []),
        consent_status=gate.consent_status,
    )


def _split_signed(value: float) -> tuple[float, float]:
    """Split a SIGNED £/yr component into ``(revenue, cost)``, both non-negative.

    The schema keeps revenue and cost on separate non-negative channels, and finance nets them
    (`dispatch_cost_total` is subtracted in EBITDA). So a negative dispatch component is carried
    as a cost rather than clamped to zero — the F-02 fix. ``revenue - cost`` always reproduces the
    input exactly, which is what keeps the reported floor equal to the MILP's objective.
    """
    return (round(max(0.0, value), 2), round(max(0.0, -value), 2))


def _priceable(gate: GateResult) -> bool:
    """A schedule is worth valuing only for an OPEN (floor) or CONDITIONAL (upside) gate."""
    return gate.gate_status in (GateStatus.OPEN, GateStatus.CONDITIONAL)


def _capacity_market_value(
    schedule: HalfHourlyDispatchSchedule,
    assumptions: AssumptionLibrary,
    site: SiteModel,
    context: GateContext | None = None,
) -> tuple[float, list[str], list[str]]:
    """CM value: clearing price (or the consented agreed £/kW/yr) × de-rated capability.

    Rate ladder (D43): a customer-agreed CM rate wins over the assumption clearing price when the
    customer consented; else the assumption clearing price; else £0. Consent NEVER bypasses the
    deliverability physics — the gate still holds CM out of the floor until deliverability is
    confirmed (that verdict lives on the gate, not here).
    """
    # D-92: prequalification eligibility at the SOLVED size is the binding test, and it is checked
    # BEFORE any rate or de-rating factor is read — a market the CMU cannot prequalify for is worth
    # £0, not a de-rated fraction of a clearing price. It also precedes the JSON-F-30
    # compiled-commitment logic: being barred from the market outranks an unconfirmed commitment.
    # Basis: connection/DSR capacity (SI 2014/2043 reg 15), so the sized power is the right input —
    # never `deliverable_capacity_mw`, which is nameplate × de-rating.
    verdict = _market_entry_verdict(MarketStream.CAPACITY_MARKET, schedule, context)
    elig_notes = list(verdict.reasons) + aggregation_route_notes(
        verdict, MarketStream.CAPACITY_MARKET
    )
    if not verdict.eligible:
        elig_notes.append(
            "£0: the Capacity Market cannot be PREQUALIFIED for at this unit's size (SI 2014/2043 "
            "reg 15 sets a 1 MW minimum on CONNECTION capacity), so it earns nothing at any "
            "clearing price or de-rating factor. CONSENT AUTHORISES PURSUIT, IT DOES NOT CREATE "
            "ELIGIBILITY — held out of the priced floor, the P50/P90 series and the DSCR."
        )
        return 0.0, [], elig_notes
    clearing = maybe(assumptions, "capacity_market", "clearing_price_gbp_per_kw_yr")
    derate_a = maybe(assumptions, "capacity_market", "de_rating_factor_by_duration_h")
    clearing_val = numeric(clearing)
    if derate_a is None:
        return 0.0, [], ["not priced: capacity_market clearing price / de-rating missing."]
    consented_rate = _consent_agreed_rate(site, MarketStream.CAPACITY_MARKET)
    if consented_rate is not None:
        # THE DE-RATING IS APPLIED EXACTLY ONCE, ON THE VOLUME, AT THE CANDIDATE'S OWN DURATION BIN
        # (2026-08-19). It used to be applied TWICE. The consented rate on these site records is
        # `capacity_market.study_basis_forward_gbp_per_kw_yr_by_duration.by_duration_h['2']` = GBP
        # 5.96/kW/yr, which is the T-4 2029-30 CLEARED price of GBP 27.10/kW/yr ALREADY MULTIPLIED
        # by that auction's published Storage Duration-Limited de-rating bin at 2 h (0.2198), less
        # the 20 pct aggregator cut - so it is a rate on DE-RATED capacity. The volume was then
        # de-rated AGAIN, at the 0.5 h bin (0.0557). The product 0.2198 x 0.0557 = 0.01225 where the
        # correct single factor for a 0.5 h asset is 0.0557, so the haircut was 4.55x too large. The
        # same run also mixed nodes: the 2 h bin built the rate while the 0.5 h bin cut the volume,
        # on a 0.5 h asset.
        #
        # THE FIX. The reference de-rating is divided back out, leaving a rate on RATED capacity, so
        # the ONE surviving factor is the volume's, at the candidate's OWN duration bin. The
        # reference factor is read from the SAME table the volume reads, so the two can never
        # disagree, and the volume branches below are untouched: whichever de-rated capability they
        # produce is now multiplied by an un-de-rated rate.
        #
        # DIRECTION OF EFFECT: CM revenue UP 4.55x at 0.5 h (GBP 0.2657 -> GBP 1.2085 per kW/yr).
        # NET EFFECT ON EBITDA APPROXIMATELY ZERO, and this must be said or a reader books a phantom
        # uplift: D58 limb 2 nets `min(CM revenue, windowed CM-peak levy credit)` off the REPORTED
        # credit, so raising CM revenue by GBP X removes the same GBP X from the avoided-cost
        # credit.
        ref_derate, ref_warn = value_for_duration(derate_a.value, _STUDY_BASIS_REFERENCE_DURATION_H)
        used: list[str] = []
        notes = [
            f"Capacity Market priced at the customer-agreed rate £{consented_rate:,.2f}/kW/yr "
            "(consent given) — ahead of the assumption clearing price (estimation ladder, D43); a "
            "commercially-agreed rate, not a market-cleared price."
        ]
        if ref_derate is None or ref_derate <= 0:
            rate_val = consented_rate
            notes.append(
                "RATE NOT RE-BASED TO RATED CAPACITY: capacity_market."
                "de_rating_factor_by_duration_h publishes no "
                f"{_STUDY_BASIS_REFERENCE_DURATION_H:g} h bin, so the de-rating already inside "
                "this agreed rate cannot be divided back out. DIRECTION OF THE RESIDUAL ERROR: "
                "the figure is UNDERSTATED, because the de-rating is applied twice - once inside "
                f"the rate and once on the volume below.{' ' + ref_warn if ref_warn else ''}"
            )
        else:
            rate_val = consented_rate / ref_derate
            used.append(_CM_DERATE_KEY)
            notes.append(
                f"RE-BASED TO RATED CAPACITY: the agreed £{consented_rate:,.2f}/kW/yr is quoted on "
                f"DE-RATED capacity at the {_STUDY_BASIS_REFERENCE_DURATION_H:g} h bin "
                f"({ref_derate}), so it is divided back out to £{rate_val:,.4f}/kW/yr of RATED "
                "capacity and the de-rating is applied ONCE, on the volume below, at this "
                "candidate's OWN duration bin. Applying it on both sides haircut this stream by "
                "4.55x at 0.5 h, which is what every run before 2026-08-19 did. CONFIRMATION ASK: "
                "whether the agreed rate is quoted on RATED or on DE-RATED capacity, so the "
                "re-basing stops being an assumption."
            )
    elif clearing is None or clearing_val is None:
        return 0.0, [], ["not priced: capacity_market clearing price / de-rating missing."]
    else:
        # The library clearing price is the auction headline and is already on RATED capacity, so
        # nothing is re-based here and the volume's de-rating is the only one applied.
        rate_val = clearing_val
        used = ["capacity_market.clearing_price_gbp_per_kw_yr"]
        notes = [placeholder_note("capacity_market", "clearing_price_gbp_per_kw_yr", clearing)]
    # Prefer the engine-reserved capability (W7-2: the solver held it through the committed
    # windows) over nameplate × de-rating. The provenance note only claims what actually
    # happened: "capability-checked" is asserted ONLY when the W7-3 end-of-tenor verdict
    # genuinely confirmed (adoption review W7-4 — never a fabricated check on a £ figure).
    if schedule.deliverable_capacity_mw is not None:
        capacity_mw = schedule.deliverable_capacity_mw
        checked = bool(context is not None and context.deliverability_confirmed)
        if checked:
            notes.append(
                f"committed capability {round(capacity_mw, 4)} MW — engine-reserved through the "
                "committed windows (net-injection headroom held in dispatch) and "
                "capability-checked to the final contract year (see the end-of-tenor "
                "deliverability notes)."
            )
        else:
            notes.append(
                f"committed capability {round(capacity_mw, 4)} MW — engine-reserved through the "
                "committed windows (net-injection headroom held in dispatch); end-of-tenor "
                "deliverability NOT confirmed (unchecked, or the check failed) — CONDITIONAL, "
                "held out of the priced floor."
            )
    else:
        derate, warn = value_for_duration(derate_a.value, schedule.duration_h)
        if derate is None:
            return 0.0, [], [f"not priced: {warn}."]
        capacity_mw = schedule.power_mw * derate
        # The same key can already be credited by the rate re-basing above; credit it once.
        if _CM_DERATE_KEY not in used:
            used.append(_CM_DERATE_KEY)
        notes.append(
            f"de-rated capability {round(capacity_mw, 4)} MW (sized power × duration de-rating "
            f"{derate}); deliverability to the final contract year at degraded capability is "
            "UNVERIFIED - no end-of-tenor capability check ran, because no CM commitment compiled "
            "into the dispatch. Under D57 that deliverability is ASSUMED and the figure is priced "
            "into the floor; the assumption is stated on the stream's gate warnings with its "
            "confirmation ask. It is an assumption, never a completed check (D45)."
        )
        if warn:
            notes.append(warn + ".")
    revenue = rate_val * (capacity_mw * 1000.0)  # £/kW/yr × kW
    # The eligibility working travels with the £ even when the verdict was favourable, so the reader
    # can see which route (standalone connection, or an aggregated DSR CMU) this figure rests on.
    notes.extend(elig_notes)
    return revenue, used, notes


#: The call-rate default and its resolver are defined ONCE, in `gridq.value_stack.gb_streams`, and
#: shared by both pricing paths (import direction: valuation → value_stack, never the reverse).
#: Re-exported here under the original private names because they are part of this module's
#: established surface (`tests/test_optimisation_commitments.py` imports `_local_flex_call_rate`).
#: Sourcing and the register citations live with the definition.
_DEFAULT_LOCAL_FLEX_CALL_RATE = _DEFAULT_CALL_RATE
_LOCAL_FLEX_CALL_RATE_KEY = _CALL_RATE_KEY
_local_flex_call_rate = local_flex_call_rate


def _local_flex_value(
    schedule: HalfHourlyDispatchSchedule, assumptions: AssumptionLibrary, site: SiteModel
) -> tuple[float, list[str], list[str]]:
    """Local-flex value: the AWARDED availability leg + a direction-correct utilisation leg.

    **What changed, D57 item 5b (Olly, 2026-08-18), and why both halves were defects.**

    1. **The availability leg was structurally £0 on every run.** This function called
       `local_flex_availability_value` with ``contracted_mw=None`` and ``window_hours=None``, so the
       helper took its missing-input branch and returned £0 with a data ask — on sites that DO carry
       an attested award recording both. The award's committed kW and its own service windows are
       read here now, and the hours are derived from the windows the contract declares
       (`gb_streams.local_flex_award_window_hours`), never defaulted. For SAOU/VAOU products
       availability is the LARGER half of the payment, so this was not a conservative rounding, it
       was a missing leg.
    2. **The utilisation leg measured the wrong DIRECTION.** It priced
       ``schedule.tender_window_discharge_mwh`` — in-window DISCHARGE — for every award. The awarded
       product at these sites is NGED's Port Ham SAOU_DA, whose recorded direction is *"Demand Turn
       Up / Generation Turn Down — the asset is paid to CHARGE"*. A turn-up obligation delivers by
       RAISING import, so a discharge-only measure reads £0 exactly where the real product is
       easiest to deliver. The direction is read off the awarded clear and the matching leg is
       priced.

    The rate ladder is otherwise unchanged (D43): a customer-agreed rate wins, else the site's own
    zone's AWARDED CLEAR (D57 limb 5b), else the library's GB-wide utilisation price. Gross revenue
    is netted by the aggregator / route-to-market cut, as before.

    **Parity with the proxy path** (`gb_streams.LocalFlexPicloStream._price`): both resolve the rate
    through the same consent ladder, apply the same shared
    :func:`gridq.value_stack.gb_streams.local_flex_call_rate`, and net the same aggregator cut.
    `tests/test_local_flex_parity.py` pins the two paths to the same £.
    """
    notes: list[str] = []
    used: list[str] = []
    # The site's own award: the source of the contracted MW, the contracted window hours and the
    # awarded product's direction. Absent on a site with no award, which is a real state.
    #
    # THE AWARDS ARE READ THROUGH THE CLAMP, not off the SiteModel (2026-08-19). This line read
    # `site.local_flex_awards` verbatim, which is the UNCLAMPED record: on the campaign's Harwell
    # run the dispatch reserved 10.372 kW through `local_flex_award_constraints` while this leg
    # invoiced the 213.810 kW the site record declares - the site's whole metered peak import, a
    # 20.6x mismatch between the volume the model commits and the volume it bills, inside one run.
    # `gb_streams.clamped_local_flex_awards` is now the single definition both consume, so the
    # invoiced MW IS the MW in the DispatchConstraintDescriptor at every candidate.
    #
    # DIRECTION OF EFFECT: DOWN, by declared kW / deliverable kW - 20.6x on Harwell (GBP 504.14 to
    # GBP 18.47 per kW/yr on that site's own numbers). The clamp is the asset's own physics, which
    # D57 preserves as arithmetic integrity and D58 limb 1 names as the half that is not waived; no
    # eligibility test is added and none of the removed gates comes back.
    # Bill THE RESERVATION THIS SOLVE COMPILED, read off the schedule (D61, 2026-11-22). Each
    # candidate's dispatch now reserves its OWN end-of-tenor capability (the seam rebinds the award
    # per candidate, `dispatch.contract.candidate_dispatch_constraints`), and the solver records
    # the kW it reserved on `schedule.local_flex_reserved_kw`. Billing that record is what makes
    # the invoiced MW the reserved MW at every size by construction, rather than by re-deriving the
    # bound here from a second source. This used to recompute the clamp from
    # `deliverable_capacity_mw`, which is the CAPACITY MARKET's reservation (0.05 MW on a 50 kW
    # candidate) and has nothing to do with local flex. A schedule with no record (``None``: no
    # award compiled, or not produced by a solve) is billed the seam's default reservation, the
    # smallest grid candidate's; an award the dispatch did not reserve is not billed, and says so.
    seam_awards, _clamp_notes = clamped_local_flex_awards(site, assumptions)
    reserved = schedule.local_flex_reserved_kw
    if reserved is None:
        clamped_awards = seam_awards
        reservation_basis = "seam default"
    else:
        by_id = dict(reserved)
        clamped_awards = [
            ClampedLocalFlexAward(
                award=entry.award.model_copy(
                    update={"committed_power_kw": by_id[entry.award.commitment_id]}
                ),
                declared_kw=entry.declared_kw,
            )
            for entry in seam_awards
            if entry.award.commitment_id in by_id
        ]
        reservation_basis = "solved"
        for entry in seam_awards:
            if entry.award.commitment_id not in by_id:
                notes.append(
                    f"LOCAL-FLEX AWARD '{entry.award.commitment_id}' WAS NOT RESERVED IN THIS "
                    "DISPATCH, so its availability leg is not billed: the invoiced MW is the MW in "
                    "the dispatch reservation, and here that is none."
                )
    clear, clear_key, clear_notes = local_flex_awarded_zone_clear(site, assumptions)
    notes.extend(clear_notes)

    util = maybe(assumptions, "local_flex_piclo", "utilisation_price_gbp_per_mwh")
    util_val = numeric(util)
    consented_rate = _consent_agreed_rate(site, MarketStream.LOCAL_FLEX_PICLO)
    # Two shapes exist in `awarded_zone_clears`: NGED records ONE awarded utilisation clear
    # (`utilisation_gbp_per_mwh`), SSEN records the FOUR awarded call-off prices in the zone
    # (`utilisation_gbp_per_mwh_awarded`). Where a list is recorded D56 takes the FAVOURABLE end
    # of the sourced range - every value in it is a real awarded price in this site's own zone -
    # and the note below states the whole list so the reader sees which end was taken.
    awarded_util = None if clear is None else clear.get("utilisation_gbp_per_mwh")
    awarded_util_range: list[float] = []
    if clear is not None and awarded_util is None:
        listed = clear.get("utilisation_gbp_per_mwh_awarded")
        if isinstance(listed, list | tuple):
            awarded_util_range = [float(v) for v in listed if isinstance(v, int | float)]
            if awarded_util_range:
                awarded_util = max(awarded_util_range)
    if consented_rate is not None:
        rate_val: float | None = consented_rate
        rate_used: list[str] = []
        notes.append(
            f"local flex priced at the customer-agreed rate £{consented_rate:,.2f}/MWh (consent "
            "given) - ahead of the assumption utilisation price (estimation ladder, D43)."
        )
    elif isinstance(awarded_util, int | float):
        # D57 limb 5b: this site's OWN zone's awarded utilisation clear, in full. It replaces the
        # GB-wide median the run record flagged as the wrong basis ("a zone ceiling is a hard cap on
        # that zone, so a GB median can exceed what the site could ever be paid").
        rate_val = float(awarded_util)
        field = (
            "utilisation_gbp_per_mwh_awarded" if awarded_util_range else "utilisation_gbp_per_mwh"
        )
        rate_used = [f"{clear_key}.{field}"]
        notes.append(
            f"utilisation priced at the AWARDED CLEAR £{rate_val:,.2f}/MWh for this site's own "
            f"zone ({clear_key}.{field}), not at the GB-wide median in "
            "local_flex_piclo.utilisation_price_gbp_per_mwh. A zone ceiling is a hard cap on that "
            "zone, so the GB median could exceed anything this site can be paid; the awarded clear "
            "cannot."
        )
        if awarded_util_range:
            listed = ", ".join(f"£{v:,.2f}" for v in awarded_util_range)
            notes.append(
                f"FAVOURABLE END OF A SOURCED RANGE TAKEN (D56): this zone records "
                f"{len(awarded_util_range)} AWARDED call-off utilisation prices - {listed}/MWh - "
                f"and the highest, £{rate_val:,.2f}/MWh, is the one priced. Every value in that "
                "list is a real price the DNO awarded in this zone, so what changes is which end "
                "of a cited range is taken and never whether it is cited. DIRECTION OF EFFECT: UP, "
                f"by {rate_val / min(awarded_util_range):,.1f}x against the lowest awarded price "
                "in the same zone. CONFIRMATION ASK: which call-off contract this site's own award "
                "corresponds to, so the price stops being the best of four and becomes its own."
            )
    else:
        rate_val = util_val
        rate_used = ["local_flex_piclo.utilisation_price_gbp_per_mwh"]
        if util is not None:
            notes.append(
                placeholder_note("local_flex_piclo", "utilisation_price_gbp_per_mwh", util)
            )
    if rate_val is None:
        return 0.0, [], [*notes, "not priced: no utilisation rate available from any source."]

    # ---- the utilisation leg, in the direction the AWARDED PRODUCT is delivered in ------------
    # ``direction`` is library prose and carries an em dash; no em/en dash may reach a rendered
    # pack, so it is normalised here rather than at every interpolation site.
    direction = "" if clear is None else str(clear.get("direction") or "")
    direction = " ".join(direction.replace("\u2014", "-").replace("\u2013", "-").split())
    # THE DIRECTION TEST READS THE "paid to ..." CLAUSE, NOT "TURN UP". Both awarded rows name BOTH
    # directions - NGED's is "Demand Turn Up / Generation Turn Down" and SSEN's is "Demand Turn Down
    # / Generation Turn Up" - so a substring test for "TURN UP" matches both and silently priced
    # SSEN's discharge product off the charge series. The unambiguous half of each row is the clause
    # that says which way the ASSET moves.
    paid_to = direction.upper().rsplit("PAID TO", 1)
    turn_up = len(paid_to) == 2 and paid_to[1].strip().startswith("CHARGE")
    if turn_up:
        mwh = schedule.tender_window_charge_mwh
        dir_note = (
            "DIRECTION: the awarded product is DEMAND TURN UP / GENERATION TURN DOWN "
            f"({direction}), "
            "so its utilisation volume is the in-window CHARGE the dispatch actually raised, not "
            "in-window discharge. Pricing this leg off discharge - which is what this function did "
            "before D57 item 5b - measures the opposite direction and reads GBP 0 whenever the "
            "optimum correctly declines to discharge into a summer daytime window."
        )
    else:
        mwh = schedule.tender_window_discharge_mwh
        dir_note = (
            "DIRECTION: the awarded product is delivered by DISCHARGING"
            + (f" ({direction})" if direction else ", the default where no direction is recorded")
            + ", so its utilisation volume is the in-window discharge the dispatch delivered."
            + (
                ""
                if direction
                else " NO AWARDED DIRECTION IS RECORDED FOR THIS SITE'S ZONE, so discharge is "
                "assumed. DATA ASK: the awarded product's direction, because a Demand Turn Up "
                "award is delivered by CHARGING and would be measured on the other series."
            )
        )
    notes.append(dir_note)
    call_rate, call_used, call_note = _local_flex_call_rate(assumptions)
    if call_note:
        notes.append(call_note)
    gross = 0.0
    payable_mwh = 0.0
    if mwh is None:
        notes.append(
            "UTILISATION LEG NOT MEASURED: no local-flex availability window compiled into this "
            "dispatch, so the in-window volume the tender pays for was never measured. The "
            "AVAILABILITY leg below is unaffected - it is paid for being available, not for being "
            "called - so this is a missing leg, never a finding that the award is worthless."
        )
    else:
        # F-19: availability is not utilisation. Paying every in-window movement across the whole
        # year as an instructed DNO call is an implicit 100% utilisation rate; the DNOs' own
        # published figures say otherwise by an order of magnitude, so a call rate is applied.
        called_mwh = mwh * call_rate
        # D-116 (overstatement half): the payable volume is not the delivered volume. Without the
        # award's contracted MWh no delivery ratio exists, so this passes through with a data ask.
        payable_mwh, perf_notes = local_flex_utilisation_adjustment(
            delivered_mwh=called_mwh, contracted_mwh=None
        )
        gross = rate_val * payable_mwh
        notes.append(
            f"utilisation £/MWh × {round(payable_mwh, 3)} payable MWh "
            f"({mwh} MWh delivered in the awarded windows × {call_rate:.0%} call rate) "
            f"(gross £{gross:,.0f})."
        )
        notes.extend(perf_notes)

    # ---- the availability leg, from the award's OWN contracted MW and window hours ------------
    avail_revenue = 0.0
    avail_used: list[str] = []
    awarded_avail = None if clear is None else clear.get("availability_gbp_per_mw_h")
    if not seam_awards:
        # No award record on this site, so there is no contracted MW and no contracted window hours
        # to price. The helper's own refusal note is still emitted, because the D-116 understatement
        # must stay visible: an unpriced availability leg is the LARGER half of an SAOU/VAOU payment
        # and a reader who is not told so reads the total as complete. Every site the owner supplies
        # carries an award (D54), so this branch is not reached on a campaign run.
        _leg, _leg_used, leg_notes = local_flex_availability_value(
            assumptions=assumptions, contracted_mw=None, window_hours=None, product_type=None
        )
        notes.extend(leg_notes)
    for award, declared_kw in clamped_awards:
        # The awarded zone row that supplies the RATE also publishes that service's own annual
        # availability-hour count, and where it does it governs (2026-08-19). This call passed the
        # award alone, so the hours were re-derived from its declared clock times: 180 h/yr at
        # Harwell against the SSEN Wallingford row's own published 136 h, a 1.32x overstatement, and
        # 136 h is the figure that reconciles to SSEN's published GBP 8,200 tranche budget.
        # DIRECTION OF EFFECT: DOWN, 1.32x, on SSEN SEPD sites; a no-op on NGED sites, whose row
        # publishes 1,098 h and whose derivation gives exactly 1,098.
        window_h, hours_basis = local_flex_award_window_hours(award, clear)
        # `award.committed_power_kw` is ALREADY the clamped figure - the same one the
        # DispatchConstraintDescriptor carries - so this is a unit conversion, not a second bound.
        contracted_mw = float(award.committed_power_kw) / 1000.0
        declared_mw = float(declared_kw) / 1000.0
        leg, leg_used, leg_notes = local_flex_availability_value(
            assumptions=assumptions,
            contracted_mw=contracted_mw or None,
            window_hours=window_h,
            # D-116: a SOURCED ratio only, never a default. None today on every release, so the
            # leg is priced gross and `quotes.pipeline` labels it gross in quote.warnings.
            utilisation_performance_ratio=sourced_utilisation_performance_ratio(assumptions),
            product_type=None,
            awarded_rate_gbp_per_mw_h=(
                float(awarded_avail) if isinstance(awarded_avail, int | float) else None
            ),
            awarded_rate_key=(
                f"{clear_key}.availability_gbp_per_mw_h" if awarded_avail is not None else None
            ),
        )
        avail_revenue += leg
        avail_used.extend(leg_used)
        if clear is not None and clear.get("availability_h_per_year") is not None:
            # The hours are now priced from the library, so the key is credited and renders on the
            # consolidated assumptions register (D56 limb 4) with its direction of effect.
            avail_used.append(f"{clear_key}.availability_h_per_year")
        cid = getattr(award, "commitment_id", "award")
        notes.append(
            f"AVAILABILITY LEG FOR AWARD '{cid}': {contracted_mw:,.3f} MW contracted"
            + (
                f" (the award declares {declared_mw:,.3f} MW; paid on the {contracted_mw:,.3f} MW "
                "the dispatch actually reserves, "
                + (
                    "read from the solved schedule's own reservation record and bounded by this "
                    "system's"
                    if reservation_basis == "solved"
                    else "which on a schedule carrying no solved reservation is the seam's "
                    "default reservation, the smallest grid candidate's"
                )
                + " end-of-tenor deliverable capability, because the asset cannot make available "
                "more power than it has. THE INVOICED MW IS THE MW IN THE DISPATCH RESERVATION: "
                "see the local-flex award constraint notes for the clamp's derivation)"
                if declared_mw > contracted_mw
                else ""
            )
            + " over "
            + (
                # The label follows the LADDER, it does not assert one rung of it. This read
                # "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS" unconditionally, which became false
                # the moment the awarded row's own published hour count took precedence: the run
                # then claimed a derivation in the same sentence whose parenthesis said PUBLISHED.
                # A note that states a figure must state the basis that produced THAT figure.
                f"{window_h:,.1f} contracted window hours/yr, {hours_basis}"
                if window_h is not None
                else f"UNKNOWN window hours ({hours_basis})"
            )
            + ". Before D57 item 5b this call site passed contracted_mw=None and "
            "window_hours=None, so the leg was GBP 0 on every run even where the award recorded "
            "both."
        )
        notes.extend(leg_notes)

    cut, cut_src, cut_used = _aggregator_cut(site, assumptions)
    revenue = (gross + avail_revenue) * (1.0 - cut)
    used = rate_used + cut_used + call_used + avail_used
    notes.append(
        "PRODUCT TYPE NOT CARRIED: the ENA product (SU / SAOU / VAOU / OU) sets the grace, "
        "multiplier and POD parameters and whether an availability leg exists at all. It has no "
        "home on the tender-award record yet, so the standardised NGED/UKPN parameters are used "
        "(grace 5%, multiplier 3, POD 0%) - a data ask, not a per-award fact."
    )
    if cut > 0.0:
        notes.append(
            f"aggregator cut {cut:.0%} ({cut_src}) applied: net £{revenue:,.0f} "
            f"= (utilisation £{gross:,.0f} + availability £{avail_revenue:,.0f}) × (1 − {cut:.2f})."
        )
    return revenue, used, [n for n in notes if n]


def _market_entry_verdict(
    stream: MarketStream,
    schedule: HalfHourlyDispatchSchedule,
    context: GateContext | None,
) -> gates.EligibilityVerdict:
    """Eligibility for ``stream`` at the SOLVED unit size — the authoritative test (D-92).

    The gate layer can only see the site's connection bound (a sizing-independent upper bound);
    here the sized power is known, so this is the binding test. Same predicate and same sourced
    thresholds as the proxy path, so the two cannot disagree about who may enter a market.
    """
    return market_entry_verdict(
        stream,
        unit_mw=schedule.power_mw,
        portfolio_mw=context.portfolio_mw if context is not None else None,
    )


#: The duration node every study-basis per-kW/yr rate on these site records was built at. The
#: consent notes say so in terms ("central_gbp_per_kw_yr_2h", "by_duration_h['2']"), and the
#: rescale below preserves whatever multiplier the quoted rate applied to that node rather than
#: replacing it.
_STUDY_BASIS_REFERENCE_DURATION_H = 2.0

#: stream -> (library key, path inside its value) of the per-duration table its study-basis rate was
#: built from. The rate is rescaled onto the CANDIDATE's own duration node through it.
#:
#: ANCILLARY_DC_DM_DR IS DELIBERATELY ABSENT AND THIS IS NOT AN OVERSIGHT. Its rate comes from
#: `ancillary_services.study_basis_availability_gbp_per_kw_yr_by_product`, which is keyed BY PRODUCT
#: (DCL/DCH/DML/DMH/DRL/DRH) and publishes no per-duration table for any of them, so there is no
#: sourced node to rescale onto and inventing one would be a fabricated factor - the one thing D57
#: limb 5b keeps forbidding. What the response products DO consume is warranty throughput, and that
#: is charged in the dispatch instead (`milp_solver` throughput_cap). DATA ASK: a per-duration
#: availability table for the EAC response products, or the Reserved Capacity limit as a function of
#: sustain duration, so the same correction can be made there.
_STREAM_DURATION_TABLE: dict[MarketStream, tuple[tuple[str, str], tuple[str, ...]]] = {
    MarketStream.BALANCING_MECHANISM: (
        ("balancing_mechanism", "study_basis_marginal_value_gbp_per_kw_yr"),
        ("method_b_bottom_up", "gbp_per_kw_yr"),
    ),
}


def _duration_rescaled_consent_rate(
    stream: MarketStream,
    rate: float,
    duration_h: float,
    assumptions: AssumptionLibrary,
) -> tuple[float, list[str], list[str]]:
    """A consented per-MW/yr rate moved onto the candidate's OWN duration node (2026-08-19).

    Returns ``(rate, assumption_keys_used, notes)``, and the rate is unchanged with a stated reason
    for any stream whose study basis publishes no per-duration table.

    **THE DEFECT.** `_consented_gated_per_mw_yr_value` applied ``rate x sellable MW`` with no
    duration term at all. The Balancing Mechanism rate on these records is GBP 13,980/MW/yr, i.e.
    ``balancing_mechanism.study_basis_marginal_value_gbp_per_kw_yr.central_gbp_per_kw_yr_2h``
    (GBP 17.47/kW/yr) less the 20 pct aggregator cut - the 2-HOUR row of a table the same key
    publishes as ``{1h: 10.25, 2h: 17.47, 4h: 28.74}`` - and it was paid in full to a 0.5-hour
    asset. Not an eligibility question: BM value for an energy-limited asset is the spread uplift
    times the MWh of throughput, and a 0.5 h asset moves a quarter of the MWh per cycle that a 2 h
    asset does.
    The table nearly halves from 2 h to 1 h and says so.

    DIRECTION OF EFFECT: DOWN, 1.70x at 0.5 h and at 1 h (GBP 13.98 -> GBP 8.20 per kW/yr); UP at
    4 h (GBP 23.00/kW/yr); unchanged at the 2 h reference. No stream is gated, zeroed or held
    conditional, and no threshold, consent test or stacking rule is added.
    """
    location = _STREAM_DURATION_TABLE.get(stream)
    if location is None:
        return (
            rate,
            [],
            [
                f"RATE NOT DURATION-ADJUSTED: the study basis for {stream.value} publishes no "
                "per-duration table, so there is no sourced node to move this rate onto and none "
                "is invented. DIRECTION OF THE RESIDUAL ERROR: OVERSTATED on an asset shorter than "
                "the basis the rate was built on. DATA ASK: a per-duration table for this "
                "product's availability value.",
            ],
        )
    (section, name), path = location
    entry = maybe(assumptions, section, name)
    table: object = None if entry is None else entry.value
    for step in path:
        table = table.get(step) if isinstance(table, dict) else None
    rescaled, note = duration_rescaled_rate(
        rate,
        table=table,
        duration_h=duration_h,
        reference_duration_h=_STUDY_BASIS_REFERENCE_DURATION_H,
        table_key=f"{section}.{name}.{'.'.join(path)}",
        rate_unit="GBP/MW/yr",
    )
    used = [f"{section}.{name}"] if rescaled != rate else []
    return rescaled, used, [note]


def _consented_gated_per_mw_yr_value(
    stream: MarketStream,
    schedule: HalfHourlyDispatchSchedule,
    site: SiteModel,
    assumptions: AssumptionLibrary,
    context: GateContext | None = None,
    basis_acknowledgement: CapacityMarketDeratingBasisAcknowledgement | None = None,
) -> tuple[float, list[str], list[str]]:
    """Price a consented BM / ancillary stream: agreed £/MW/yr × sellable de-rated MW (D43, D-92).

    Priced ONLY when **all** of these hold: the unit is size-eligible for the market (D-92), the
    customer consented, a customer-agreed £/MW/yr rate is recorded, **and** the CM de-rating volume
    basis is either not the basis actually used (the solver reserved a deliverable capability, so
    the volume is plain committed MW) or explicitly acknowledged. The volume is engine-measured,
    then truncated to the whole-MW quantity the market actually accepts as a sell order. Any missing
    leg ⇒ £0, keeping the stream's "consent given" label (consent is authorisation, not value).
    Never bankable — a commercially-agreed rate, not a market price.

    **The volume-basis guard (Olly, 2026-08-17), corrected 2026-08-18 (Olly).** D43's "parity with
    CM's volume basis" is **wrong for these two markets** — see
    `CapacityMarketDeratingBasisAcknowledgement` for the reasoning and
    `docs/bess_modelling_requirements.md:1272` (D-102) for the correct basis. Where that basis is
    the one in play the £ is **refused rather than published**, loudly labelled, with the run
    completing and the stream landing in ``gated_upside`` at £0 (D45: refuse the £ or derive it,
    never both-and-neither — a labelled refusal is sanctioned, a silent 9×-wrong number is not).
    Passing a `CapacityMarketDeratingBasisAcknowledgement` prices it anyway, attributed and
    labelled UNDERSTATED.

    **What was corrected, because the guard suppressed revenue it had no claim on.** The guard armed
    on **size**, on the premise that a size-eligible unit is necessarily priced on a CM-de-rated
    volume. That premise is false on the branch the MILP actually takes:
    :func:`_derated_deliverable_mw` returns ``schedule.deliverable_capacity_mw`` **verbatim** when
    the solver reserved a capability, consulting no de-rating factor at all — the quantity
    `milp_solver.py:1888` computes as plain committed MW, which is the whole-MW deliverable-power
    basis D-102 and `gb_streams.sellable_volume_mw` prescribe. (The note calling it "engine-reserved
    de-rated" is the misnomer that made the wrong premise look right.) So every consented, rated,
    size-eligible unit was refused — including units already priced on the CORRECT basis — which
    left BM and DC/DM/DR unable to reach a reported number at all and put the guard in direct
    conflict with D55's standing instruction that every stream be one the optimiser can pick from.
    The guard now arms off **which basis was used**, proved by provenance (:data:`_CM_DERATE_KEY`
    appearing in the volume's ``assumptions_used``), which is the fact it always meant to test.
    **Direction of effect: revenue moves UP** on a genuinely eligible consented unit whose
    capability the solver measured, and moves nowhere at all below the 1 MW threshold — D-92 refuses
    those first, and its refusal is untouched.

    **The guard belongs HERE and must not be generalised into**
    :func:`_derated_deliverable_mw`: :func:`_capacity_market_value` consumes the same helper, and
    the CM duration de-rating is the *correct* basis for the Capacity Market. It is only the import
    of that factor into BM / DC-DM-DR that is the defect.

    For DC/DM/DR the £ is additionally built at EFA-block granularity and netted by the
    availability factor F and performance factor K (D-158) — see
    :func:`gridq.value_stack.gb_streams.eac_response_value`.
    """
    # D-92 FIRST: eligibility is a precondition on the money, checked before consent and before any
    # rate is read. A market the asset cannot enter is worth £0, not a smaller number.
    verdict = _market_entry_verdict(stream, schedule, context)
    elig_notes = list(verdict.reasons) + aggregation_route_notes(verdict, stream)
    if verdict.route == "not_armed":
        elig_notes.append(ELIGIBILITY_DATA_ASK)
    if not verdict.eligible:
        elig_notes.append(
            f"£0: {stream.value} cannot be entered at this unit's size, so it earns nothing "
            "regardless of consent or of any agreed rate. CONSENT AUTHORISES PURSUIT, IT DOES NOT "
            "CREATE ELIGIBILITY — this stream is held out of the priced floor, the P50/P90 series "
            "and the DSCR."
        )
        return 0.0, [], elig_notes
    consented_rate = _consent_agreed_rate(site, stream)
    if consented_rate is None:
        return (
            0.0,
            [],
            [
                *elig_notes,
                "no consented agreed £/MW/yr rate — £0 (consent is authorisation, not value); "
                "the volume is never invented.",
            ],
        )
    # THE VOLUME BASIS, D57 (Olly, 2026-08-18). This read `_derated_deliverable_mw`, whose FALLBACK
    # branch (taken whenever no CAPACITY_MARKET availability-window commitment was compiled, i.e. on
    # every site in this campaign) multiplies sized power by
    # `capacity_market.de_rating_factor_by_duration_h`. That import of the CM factor is the wrong
    # basis for these two markets (D-102), and the 2026-08-17 guard below reacted to it by REFUSING
    # the £ -- so BM and DC/DM/DR reported £0 on every real run for want of a commitment nobody was
    # ever going to compile. D57 authorises retiring that mechanism: the volume is now
    # `gb_streams.sellable_volume_mw`, the single sourced definition the PROXY path already used,
    # which is plain deliverable ``power_mw`` route-aware-truncated to whole MW and reads no
    # de-rating factor at all. The two paths now call literally the same function, so the documented
    # engine-vs-proxy volume divergence is closed rather than restated.
    #
    # `used` is consequently empty here, which makes the `_CM_DERATE_KEY in used` guard below
    # unreachable from this call site. It is kept, not deleted: it is the correct refusal the day
    # any caller reintroduces a CM-de-rated volume on these streams.
    #
    # DIRECTION OF EFFECT: revenue UP. On a 2 h schedule the retired fallback applied a ~0.2094
    # de-rating and then truncated, so the same consented unit went from £0 to rate × its full
    # deliverable MW -- a ~4.8x volume increase before the refusal is even considered.
    mw, volume_notes = sellable_volume_mw(
        stream,
        unit_mw=schedule.power_mw,
        portfolio_mw=context.portfolio_mw if context is not None else None,
    )
    used: list[str] = []
    mw_note = (
        "not offerable at this size"
        if mw is None
        else (
            f"sellable deliverable capability {round(mw, 4)} MW (plain deliverable power on the "
            f"{verdict.route} route; NO Capacity Market de-rating factor is applied -- D-102/D57)"
        )
    )
    elig_notes = [*elig_notes, *volume_notes]
    # THE VOLUME-BASIS GUARD. Armed only where the CM duration de-rating actually priced the volume,
    # and only after consent + a rate are confirmed, so it never lands as boilerplate on a run it
    # cannot change. It still fires BEFORE the whole-MW truncation note is built, which is what
    # keeps the reader's stated reason the binding one: on D43's fallback basis a 4 MW / 2 h unit's
    # 0.879 de-rated MW truncates to 0, and blaming WHOLE-MW ORDER GRANULARITY for a £0 that is
    # really the imported CM de-rating is true and misleading at once. The threshold is READ from
    # the single sourced copy (`gb_streams.eligibility_threshold_mw`), never restated here.
    if _CM_DERATE_KEY in used:
        threshold_mw = eligibility_threshold_mw(stream)
        if basis_acknowledgement is None:
            # `[]`, not `used`: a WITHHELD figure credits no assumption. The key was read to decide
            # which basis was in play, and nothing was priced off it.
            return (
                0.0,
                [],
                [
                    *elig_notes,
                    *_cm_derating_basis_refusal(stream, threshold_mw, verdict.route),
                ],
            )
        # The acknowledged path publishes the figure, so it must NOT inherit the refusal's verdict
        # line ("the £ is withheld"). It carries the attributed acknowledgement instead, over the
        # SAME diagnosis — one defect, one story, two verdicts.
        elig_notes = [
            *elig_notes,
            basis_acknowledgement.note(stream, threshold_mw),
            *_cm_derating_basis_diagnosis(),
        ]
    if mw is None:
        return 0.0, used, [*elig_notes, f"not priced: {mw_note}."]
    # Whole-MW sell-order granularity: the OFFERED quantity, not the reserved capability, is what
    # the market accepts (register: "Sell orders may only be submitted in whole MWs"; "For BM, MW
    # values must be declared as integers"). A fractional remainder is simply unsellable —
    # **on the STANDALONE route**.
    #
    # The rule is ROUTE-DEPENDENT and this line used to ignore that, which barred the one route a
    # sub-1 MW site has into these markets. `gates.market_entry_eligibility` is the sourced
    # definition and says so in terms: "Fractional unit MW is offerable inside the aggregate (that
    # is what an aggregator is for), so ``whole_mw_orders`` does not truncate on this route"
    # (`value_stack/gates.py`, and it is why `EligibilityVerdict.sellable_mw` is NOT truncated on
    # the aggregated route). Truncating anyway made the engine contradict, in its very next note,
    # the verdict line it had just emitted — "the unit's own 0.915 MW is offerable inside the
    # aggregate" followed by "truncates to 0 MW as a sell order" — and made it disagree with the
    # proxy path, which consumes the route-aware `gb_streams.sellable_volume_mw`. An aggregator
    # pools fractional contributions into one whole-MW order; that pooling is the service.
    #
    # Mirrored, not restated: truncate exactly where the sourced predicate truncates. Direction of
    # effect: revenue UP on the aggregated route (a 0.915 MW unit inside a 12 MW portfolio goes from
    # £0 to its rate × 0.915). It does NOT widen eligibility — `verdict.eligible` is unchanged and a
    # standalone sub-1 MW unit was already refused above, before any volume was read.
    sellable = (
        gates.sellable_whole_mw(mw)
        if stream in WHOLE_MW_ORDER_STREAMS and verdict.route == "standalone"
        else mw
    )
    if sellable is None or sellable <= 0:
        return (
            0.0,
            used,
            [
                *elig_notes,
                f"£0: {mw_note} truncates to 0 MW as a sell order — this market accepts orders "
                "only in whole MW from a STANDALONE unit, so no part of a sub-1 MW capability is "
                "offerable. Aggregation is the route that makes a fractional capability sellable.",
            ],
        )
    # THE DURATION TERM (2026-08-19). The consented rate is quoted at the study basis's 2-hour node
    # and was applied to a candidate of ANY duration with no adjustment: GBP 17.47/kW/yr paid to a
    # 0.5-hour asset off a table that publishes {1h: 10.25, 2h: 17.47, 4h: 28.74}. It is rescaled
    # onto the candidate's own published node here, by floor lookup, never interpolation.
    priced_rate, duration_used, duration_notes = _duration_rescaled_consent_rate(
        stream, consented_rate, schedule.duration_h, assumptions
    )
    used = used + duration_used
    notes = [
        *elig_notes,
        f"{stream.value} priced at the customer-agreed £{consented_rate:,.2f}/MW/yr (consent "
        f"given) × {mw_note}.",
        *duration_notes,
        "not bankable: a commercially-agreed rate, not a market-cleared price.",
    ]
    if sellable != mw:
        notes.append(
            f"whole-MW sell orders: {sellable:,.0f} MW of the {mw:,.3f} MW capability is "
            f"offerable standalone; the {mw - sellable:,.3f} MW remainder is not sellable into "
            "this market by this unit on its own. Aggregation would make it sellable."
        )
    if stream == MarketStream.ANCILLARY_DC_DM_DR:
        # D-158: six products at EFA-block granularity, netted by F and K — not one flat £/MW/yr.
        revenue, eac_used, eac_notes = eac_response_value(
            rate_gbp_per_mw_yr=priced_rate,
            sellable_mw=sellable,
            assumptions=assumptions,
        )
        return revenue, used + eac_used, notes + eac_notes
    revenue = priced_rate * sellable
    return revenue, used, notes


def _dfs_value(
    schedule: HalfHourlyDispatchSchedule, assumptions: AssumptionLibrary, site: SiteModel
) -> tuple[float, list[str], list[str]]:
    """DFS utilisation value, MEASURED by the dispatch over the called settlement periods.

    **ZONAL, AND ON THE OBSERVED PERIOD COUNT (owner instruction, 2026-08-19).** The site's own DFS
    zone is resolved here from the same resolver the dispatch used, so the notes cannot describe a
    zone the objective did not price; the rate the notes quote is CHECKED against the rate that is
    actually on the schedule's own price array, and a mismatch is reported rather than smoothed.

    **The branch that did not exist (D57 item 4, 2026-08-18).** ``grep MarketStream.DFS
    src/gridq/optimisation/`` returned nothing: no objective term, no schedule field and no
    valuation
    branch, so the only direct NESO revenue route open to a sub-1 MW asset could not be earned by
    any
    dispatch at any price, and the stream reported £0 with a refusal note on every run.

    The £ is now the MILP's own: ``Σ (P·K/1000)·(discharge_t − charge_t)·Δt`` over the DFS winter
    season's modelled called half-hours, which is the settlement formula ``S = 0.5·P·Q·K`` summed
    over those periods (``0.5·Q`` being the MWh delivered in a half-hour). It is not an annualised
    availability figure and it is not NESO's per-MW scenario annuity — those are quoted BESIDE it as
    the published cross-check, and quoting one as the £ while the objective earned the other is the
    double-book this function is written to avoid.

    A negative figure is possible and is carried honestly: DFS volume is measured against a
    baseline,
    so charging inside a called period is negative delivery, and `gb_streams.dfs_performance_factor`
    already refuses to clip K at zero for exactly that reason.
    """
    measured = schedule.dfs_utilisation_value_gbp
    if schedule.dfs_price_gbp_per_kwh is None:
        return (
            0.0,
            [],
            [
                "DFS NOT MEASURED ON THIS RUN: no called DFS settlement period fell inside the "
                "priced horizon, so the dispatch had no DFS half-hour to deliver into. That is a "
                "CALENDAR fact about the metered series, not a finding that the site cannot earn "
                "DFS and not an eligibility refusal. DATA ASK: a half-hourly series covering the "
                "DFS winter season (27 November to 28 March)."
            ],
        )
    delivered = schedule.dfs_delivered_mwh
    zone, _resolved, zone_notes = dfs_zone_for_site(site)
    rate, rate_used, rate_notes = dfs_utilisation_rate_gbp_per_kwh(assumptions, zone=zone)
    called_mask = schedule.dfs_price_gbp_per_kwh > 0
    called = int(called_mask.sum())
    # THE NOTE MAY NOT DISAGREE WITH THE OBJECTIVE. The £ above is the dispatch's own; the rate
    # quoted below is re-resolved here, and if the two ever part company (a zone resolved one way in
    # the solver and another way here) the reader is told, because a note that silently describes a
    # different price than the one earned is worse than no note.
    priced_rate = float(schedule.dfs_price_gbp_per_kwh[called_mask].max()) if called else 0.0
    rate_check: list[str] = []
    if called and abs(priced_rate - rate) > 1e-9:
        rate_check = [
            f"RATE MISMATCH, REPORTED NOT RECONCILED: the dispatch priced DFS at "
            f"£{priced_rate:,.6f}/kWh and this stream re-resolves £{rate:,.6f}/kWh for zone "
            f"{zone}. The £ above is the DISPATCH's, which is the money the optimiser actually "
            "traded for. Two different zones or two different releases reached the same run; "
            "investigate before quoting either."
        ]
    # ANNUALISED, because `measured` is: `_net_value` multiplies by `annual_factor`, so a part-year
    # horizon would otherwise be compared against a part-year ceiling and read above 100%.
    power_ceiling = rate * schedule.power_mw * 1000.0 * 0.5 * called * schedule.annual_factor
    low, central, ceiling = dfs_seasonal_value_range_gbp(schedule.power_mw)
    notes = [
        f"DFS utilisation £{measured:,.2f}/yr, MEASURED BY THE DISPATCH: the settlement formula "
        f"S = 0.5 x P x Q x K summed over the {called} "
        f"modelled called half-hours, on {delivered} MWh/yr of NET delivery (discharge less any "
        "charge inside a called period, because DFS volume is measured against a baseline). This "
        "is the MILP's own objective term, not an annualisation of an availability rate.",
        *rate_check,
        *zone_notes,
        *rate_notes,
        "PUBLISHED CROSS-CHECK, NOT ADDED TO THE £: NESO's per-1-MW seasonal scenarios scale to "
        f"this unit's {schedule.power_mw:,.3f} MW as £{low:,.0f} (low) / £{central:,.0f} (central, "
        f"one hour per Service Requirement) / £{ceiling:,.0f} (ceiling, full participation across "
        "every published settlement period) per season. The measured figure above and these "
        "scenarios are TWO ESTIMATES OF THE SAME MONEY: only the measured one is in the £, because "
        "booking both would count the same pound twice, which is the one rule D57 does not relax.",
        "THROUGHPUT IS REAL AND IS PAID FOR: every DFS kWh is a kWh of warranty cycle life, and "
        "the same annual throughput cap and the same £21.3/MWh marginal degradation cost bind this "
        "delivery as bind arbitrage. The optimiser chose this dispatch WITH those costs in the "
        "objective, so a DFS pound here is a pound of profit, never a pound of revenue with its "
        "wear left out.",
        f"IT SCALES WITH THE BATTERY, AND HERE IS THE FUNCTION. DFS settles on the OFFERED "
        f"Service Volume Q, so the £ is bounded by what this asset can actually put into a called "
        f"half-hour: rate x SUM over called periods of min(rated power, the site's own in-window "
        f"import, the energy left in the pack) x 0.5 h. At {schedule.power_mw * 1000:,.3f} kW and "
        f"{schedule.duration_h:,.2f} h the POWER-bounded ceiling over {called} called half-hours "
        f"is £{power_ceiling:,.2f}/yr and the dispatch measured £{measured:,.2f}/yr, i.e. "
        f"{(measured / power_ceiling if power_ceiling else 0):.1%} of it; the gap is the state of "
        "energy, the site's own load and the half-hours another stream valued more highly. A "
        "LARGER OR LONGER BATTERY THEREFORE EARNS MORE DFS, which is the property a flat per-kW "
        "annuity does not have.",
    ]
    return measured, rate_used, notes


def _load_service_value(
    schedule: HalfHourlyDispatchSchedule, site: SiteModel
) -> tuple[float, list[str], list[str]]:
    """Contracted provisioning value: the customer's DECLARED counterfactual, never an estimate.

    The £ comes from the site's own requirement records (a DNO reinforcement quote, a genset,
    lost production — with the basis verbatim); the schedule contributes only the measured
    forced delivery as evidence. A requirement without a contract ``reference`` is a
    modelling input — its declared £ is still summed, but the stream's gate stays
    CONDITIONAL, which keeps it out of the priced floor (D14 discipline).
    """
    requirements = site.provisioning_requirements or []
    revenue = 0.0
    notes: list[str] = []
    for req in requirements:
        if req.declared_counterfactual_gbp_per_year is None:
            notes.append(
                f"requirement '{req.requirement_id}': no declared counterfactual — £0 priced; "
                "the engine-measured opportunity cost is reported separately, never invented "
                "as value."
            )
            continue
        revenue += req.declared_counterfactual_gbp_per_year
        notes.append(
            f"requirement '{req.requirement_id}': customer-declared counterfactual "
            f"£{req.declared_counterfactual_gbp_per_year:,.0f}/yr (basis: "
            f"{req.counterfactual_basis})."
        )
        if not req.reference:
            notes.append(
                f"requirement '{req.requirement_id}' has NO contract reference — a modelling "
                "input, not evidence (W7-4); conditional until the customer contract is "
                "referenced."
            )
    served = schedule.load_service_energy_mwh
    if served is not None:
        notes.append(
            f"measured forced delivery {served} MWh/yr against the obligation windows "
            "(in-model evidence; not priced as import displacement)."
        )
    return revenue, [], notes


def export_upside_stream_result(
    base: ValueStreamResult,
    *,
    delta_gbp: float,
    export_mwh: float,
    price_content_hash: str | None,
    price_source_ref: str | None,
    coverage_note: str | None = None,
) -> ValueStreamResult:
    """Rebuild the WHOLESALE_P415 gated-upside entry with the engine-measured export upside (A3).

    Takes the gate-derived ``base`` WHOLESALE_P415 entry (£0, CONDITIONAL) and returns a copy
    carrying the export overlay's £: ``annual_revenue_gbp = delta`` (the incremental total value of
    enabling wholesale export on the selected spec vs the no-export floor — overlay floor-terms +
    export revenue − floor-only floor-terms; ≥0, £0 when export never pays), firmness held at
    CONDITIONAL (the schema enforces gated streams are never FIRM), and notes that pin the basis,
    the exported MWh/yr, the price series ``sha256`` + coverage window verbatim, the
    perfect-foresight-indicative caveat, and the never-in-P50/P90/DSCR rule. Only ever applied by
    the engine when the overlay actually ran (consents cohered + a covering price series existed).
    """
    delta = round(max(0.0, delta_gbp), 2)
    notes = list(base.warnings)
    notes.append(
        f"export upside £{delta:,.2f}/yr — the INCREMENTAL total value of enabling wholesale "
        "export on the selected spec vs the no-export floor (overlay floor-terms + export revenue "
        "− floor-only floor-terms); ≥0 by construction, and auto-nets the retail energy bought to "
        "export plus any floor arbitrage cannibalised. £0 when export never pays."
    )
    notes.append(f"exported {export_mwh} MWh/yr in the overlay dispatch (perfect-foresight).")
    if price_content_hash:
        pin = f"priced against the supplied half-hourly wholesale series ({price_content_hash}"
        pin += f"; {coverage_note})." if coverage_note else ")."
        notes.append(pin)
    if price_source_ref:
        notes.append(f"price source: {price_source_ref}.")
    notes.append(
        "perfect-foresight over the historical/supplied series — INDICATIVE, not a forward "
        "guarantee; NEVER enters the priced floor, the P50/P90 cashflows or DSCR."
    )
    return base.model_copy(
        update={
            "annual_revenue_gbp": delta,
            "firmness": Firmness.CONDITIONAL,
            "warnings": notes,
        }
    )


def _enforce_engine_side_gates(
    sid: MarketStream,
    gate: GateResult,
    schedule: HalfHourlyDispatchSchedule,
    context: GateContext | None,
) -> GateResult:
    """Downgrade an OPEN gate the SOLVED schedule contradicts. Never upgrades one.

    Two facts only the engine holds can falsify a gate the value stack opened, and both put money
    into the priced floor that the asset cannot earn:

    - **D-92 — size eligibility.** ``check_gates`` sees only the site's connection bound, so a
      consented BM / DC-DM-DR stream can open on a site whose *sized* unit is below the market's
      1 MW minimum. Here the sized power is known, so the same predicate is re-run and binds. The
      **Capacity Market** is subject to the same test on its own sourced threshold (1 MW of
      connection capacity, SI 2014/2043 reg 15), and it is applied ahead of the JSON-F-30 check
      below: being barred from a market outranks holding an unconfirmed commitment in it.
    - **JSON-F-30 — Capacity Market with no compiled commitment. RETIRED BY D57 (2026-08-18).**
      ``deliverable_capacity_mw`` is the capability the solver actually reserved through the
      committed windows. When it is ``None`` the commitment never compiled into the dispatch
      (vacuous windows outside the metered horizon, or a held-state-of-energy-only commitment), so
      CM is priced at nameplate × de-rating — a volume larger than the awarded capacity, with zero
      headroom reserved for delivery, while the same full power keeps earning arbitrage. That was a
      downgrade to CONDITIONAL until D57, whose whole subject is that the CM agreement is **assumed
      approved** for the site and the battery size; on every campaign site the "missing commitment"
      simply *is* the missing agreement. The stream now stays OPEN and the entire finding is written
      onto its warnings with its confirmation ask, because D57 removes the restriction and not the
      candour (limb 4).

    The size test still downgrades to CONDITIONAL, which keeps the stream visible with its unlock
    condition but out of ``_PRICED_FIRMNESS`` — so it leaves the priced floor, the P50/P90 series
    and the DSCR. In practice D57 clears it too, through the assumed-approved aggregation route
    (`sizing.deliverability.d57_portfolio_aggregate_mw`), so it is reachable only where no
    portfolio MW is supplied at all.

    The asymmetry on the £ tracks *why* the gate fell, not which stream it was:

    - **Ineligible ⇒ £0.** A market the asset cannot enter or prequalify for earns nothing, at any
      price or de-rating factor. That now covers CM as well as BM/DC-DM-DR, and a non-zero figure
      would additionally let an ineligible claim contend for the shared power budget and rescale
      other streams. The £0 is applied in the pricing functions
      (:func:`_capacity_market_value`, :func:`_consented_gated_per_mw_yr_value`), so this gate only
      has to move the stream out of the floor.
    - **Eligible but unconfirmed ⇒ visible, non-zero, CONDITIONAL.** That is the JSON-F-30 case: an
      eligible CMU whose commitment did not compile has a realisable figure that is merely
      unverified, so it stays as conditional upside exactly as an unconsented CM stream does. CM is
      excluded from ``POWER_BUDGET_STREAMS``, so it perturbs nothing while it sits there.
    """
    if gate.gate_status != GateStatus.OPEN:
        return gate
    if sid in _CONSENTED_PER_MW_YR_STREAMS:
        verdict = _market_entry_verdict(sid, schedule, context)
        if verdict.eligible:
            return gate
        threshold = eligibility_threshold_mw(sid)
        floor_mw = 0.0 if threshold is None else threshold
        return gates.build_gate_result(
            sid,
            GateStatus.CONDITIONAL,
            missing_requirements=[
                f"a registered unit of at least {floor_mw:,.3f} MW, or aggregation to "
                f"{floor_mw:,.3f} MW within one GSP Group"
            ],
            unlock_condition=(
                f"a unit of at least {floor_mw:,.3f} MW, or aggregation into a portfolio of at "
                f"least {floor_mw:,.3f} MW within the same GSP Group via a route-to-market partner"
            ),
            warnings=[
                *gate.warnings,
                f"HELD OUT OF THE PRICED FLOOR (D-92): the sized unit is {schedule.power_mw:,.3f} "
                f"MW and this market requires at least {floor_mw:,.3f} MW, so the consented gate "
                "opened on a market the asset cannot enter. Consent authorises pursuit; it can "
                "never manufacture eligibility.",
                *verdict.reasons,
            ],
            consent_status=gate.consent_status,
        )
    if sid == MarketStream.CAPACITY_MARKET:
        cm_verdict = _market_entry_verdict(sid, schedule, context)
        if not cm_verdict.eligible:
            threshold = eligibility_threshold_mw(sid)
            floor_mw = 0.0 if threshold is None else threshold
            return gates.build_gate_result(
                sid,
                GateStatus.CONDITIONAL,
                missing_requirements=[
                    f"at least {floor_mw:,.3f} MW of connection capacity, or aggregation into a "
                    "multi-site DSR CMU"
                ],
                unlock_condition=(
                    f"a connection capacity of at least {floor_mw:,.3f} MW, or aggregation into a "
                    "multi-site DSR CMU (which brings its own Proven/Unproven DSR evidence, credit "
                    "cover and DSR de-rating basis)"
                ),
                warnings=[
                    *gate.warnings,
                    f"HELD OUT OF THE PRICED FLOOR (D-92): the sized unit is "
                    f"{schedule.power_mw:,.3f} MW and Capacity Market prequalification requires at "
                    f"least {floor_mw:,.3f} MW of connection capacity (The Electricity Capacity "
                    "Regulations 2014, SI 2014/2043, reg 15). The gate opened on a market this CMU "
                    "cannot prequalify for. Consent authorises pursuit; it can never manufacture "
                    "eligibility.",
                    *cm_verdict.reasons,
                ],
                consent_status=gate.consent_status,
            )
    if sid == MarketStream.CAPACITY_MARKET and schedule.deliverable_capacity_mw is None:
        # D57 (Olly, 2026-08-18), item 8. This DOWNGRADED the stream to CONDITIONAL, i.e. out of the
        # priced floor, whenever no CM availability-window commitment compiled into the dispatch —
        # which is every site in this campaign, because none carries a CM commitment record. The
        # blocker is therefore the ABSENCE OF A CM AGREEMENT, and D57 assumes the CM agreement
        # APPROVED for the site and for the battery size under evaluation. The downgrade is retired
        # and the gate stays OPEN; what the check would have said is stated on the stream instead,
        # because D57 removes the restriction, never the candour (limb 4).
        return gates.build_gate_result(
            sid,
            GateStatus.OPEN,
            open_firmness=gate.firmness,
            warnings=[
                *gate.warnings,
                "IN THE PRICED FLOOR ON A D57 ASSUMPTION, NOT ON A RESERVED COMMITMENT "
                "(JSON-F-30, retired 2026-08-18): no CM availability-window commitment compiled "
                "into this dispatch (deliverable_capacity_mw is unset), because this site holds no "
                "CM commitment record at all. The volume priced is therefore nameplate x de-rating "
                "rather than an awarded capacity, and NO net-injection headroom is reserved for "
                "delivery: the same full power keeps earning arbitrage in the same half-hours. "
                "Under D57 the CM agreement, its prequalification and its delivery obligation are "
                "ASSUMED APPROVED for this site and this battery size, so the stream is priced "
                "into the floor at that volume. DIRECTION OF EFFECT: UP - CONTRACTED rather than "
                "CONDITIONAL puts this figure into P50, P90, the DSCR, both IRRs and candidate "
                "selection. CONFIRMATION ASK: the CM agreement, its awarded de-rated capacity and "
                "its availability windows, so the volume becomes the awarded capacity and the "
                "solver reserves headroom for it instead of selling the same kW twice.",
            ],
            consent_status=gate.consent_status,
        )
    return gate


#: Stable prefix on the note that explains a priced-but-zero TARIFF_ARBITRAGE stream. Deliberately
#: a PREFIX of the sentence, matching the `D45_NULL_INPUT_LABEL` convention: a reader (or a pack
#: template) filtering `ValueStreamResult.warnings` needs one stable token, not a regex over prose.
ZERO_DISPATCH_LABEL = "GBP 0 IS A DISPATCH DECISION, NOT A MISSING INPUT"


def _zero_dispatch_note(
    schedule: HalfHourlyDispatchSchedule,
    site: SiteModel,
    assumptions: AssumptionLibrary,
) -> str | None:
    """Why a PRICED tariff-arbitrage stream earned exactly £0: the optimiser declined to cycle.

    ``None`` unless the solved schedule discharged nothing at all, so this is purely additive: a
    site whose battery ran keeps the note set it has today, byte-identically.

    **Why this exists.** ``DUOS_RED_BAND_AVOIDANCE`` reading £0 on a null ``duos_red_rate`` and
    ``TARIFF_ARBITRAGE`` reading £0 on a deliberately empty dispatch are two completely different
    facts that render as the same figure. D45 already separates the *null* case, at the gate. This
    separates the other one, at the price: the gate is OPEN, the spread came from the site's own
    rates, nothing is missing, and the zero is the model refusing a loss-making trade. Without it
    the only way to tell the two apart is to re-derive the dispatch economics by hand, which is
    exactly the investigation this note exists to make unnecessary.

    The arithmetic rides along because it is what makes the verdict CHECKABLE: the spread a cycle
    would need against the best spread the site actually offers, and the three terms that set the
    hurdle. A reader who sees "needs 0.04788, has 0.03525" can audit the conclusion; one who sees
    "£0" cannot. It is appended only when every term is genuinely available (a site with no
    time-mappable energy price, or a release missing the round-trip efficiency, gets the decision
    sentence without invented numbers).

    Deterministic: fixed-precision formatting, no clock, no set iteration, and every input is the
    solved schedule or a released assumption.

    **D62 limb 1 (2026-11-22): it keys on discharge OUTSIDE the DFS-called periods.** DFS is in
    every objective (D57), so on any site with called periods the battery delivers into them and
    total discharge is never zero; keyed on the total, this note was unreachable. Where the total
    is zero the text is byte-identical to before. Where only the DFS delivery is non-zero, the same
    decision is stated about the tariff, and the DFS delivery is stated separately, because it is
    priced on the DFS stream and not here.
    """
    if schedule.annual_discharge_outside_dfs_kwh > 0.0:
        return None
    dfs_only = schedule.annual_discharge_kwh > 0.0
    where = (
        "0 kWh discharged outside the DFS-called periods"
        if dfs_only
        else "0 kWh discharged across the priced horizon"
    )
    note = (
        f"{ZERO_DISPATCH_LABEL}: the optimiser dispatched NOTHING ({where}), so this stream "
        "earned exactly zero. The tariff gate is OPEN and the "
        "spread was read from the site's own rates, so nothing here is absent: the zero is the "
        "model declining a loss-making trade. Contrast the D45 null-input label, which marks a "
        "stream held out of the priced floor because a DECLARED input reads NULL."
    )
    if dfs_only:
        note += " " + _dfs_delivery_sentence(schedule)
    arithmetic = _round_trip_arithmetic(schedule, site, assumptions)
    return note if arithmetic is None else note + arithmetic


def _dfs_delivery_sentence(schedule: HalfHourlyDispatchSchedule) -> str:
    """D62 limb 1: the DFS delivery, stated separately from the tariff decision."""
    delivered = schedule.dfs_delivered_mwh or 0.0
    return (
        f"DFS DELIVERY IS SEPARATE: the battery delivered {delivered:,.3f} MWh/yr inside the "
        "DFS-called periods, priced on the DFS stream. Outside those periods it dispatched NOTHING "
        "on the tariff."
    )


def _round_trip_arithmetic(
    schedule: HalfHourlyDispatchSchedule,
    site: SiteModel,
    assumptions: AssumptionLibrary,
) -> str | None:
    """The per-kWh round-trip economics the optimiser decided on, as a checkable sentence.

    ``None`` when any term is genuinely unavailable (a tariff with no time-mappable energy price, a
    release with no round-trip efficiency), because a partial arithmetic statement is worse than
    none: a reader cannot audit a sum whose terms are missing, and inventing one is the failure this
    whole note exists to prevent.

    **Size-independent, and that is the point.** Every term is a £/kWh, so the verdict it supports
    holds for EVERY candidate size, not just the one measured. That is what lets a single measured
    candidate speak for the whole search box.
    """
    energy_price = schedule.energy_price_gbp_per_kwh
    eta = numeric(maybe(assumptions, "round_trip_efficiency", "ac_round_trip"))
    if energy_price is None or eta is None or eta <= 0.0:
        return None
    # Deferred import: `optimisation.milp_solver` imports this module, so a top-level import would
    # be circular. Reading the solver's own definitions keeps ONE definition of the levy stack and
    # the cycle cost, which matters because this note quotes them as the reason for the zero.
    from gridq.optimisation.milp_solver import (
        degradation_cost_gbp_per_kwh,
        levy_rate_gbp_per_kwh,
    )

    levy = levy_rate_gbp_per_kwh(site)
    c_deg, _deg_note = degradation_cost_gbp_per_kwh(assumptions)
    # The price the objective actually shaped its decision on: energy band + DUoS. The flat levy is
    # deliberately NOT in the spread (it is identical in every half-hour, so it cancels), but it is
    # in the hurdle below, because the round-trip LOSSES are bought at the full delivered price.
    priced = energy_price + schedule.duos_rate_gbp_per_kwh
    dearest, cheapest = float(priced.max()), float(priced.min())
    spread = dearest - cheapest
    needed = cheapest * (1.0 / eta - 1.0) + levy * (1.0 - eta) / eta + c_deg
    gain = dearest * eta - cheapest
    leakage = -levy * (1.0 - eta)
    wear = -c_deg * eta
    return (
        f" ARITHMETIC, per kWh charged: a cycle needs a spread of {needed:.5f} GBP/kWh and the "
        f"best this site's own priced series offers is {spread:.5f} GBP/kWh (dearest half-hour "
        f"{dearest:.5f}, cheapest {cheapest:.5f}, energy band plus DUoS). Terms: energy gain "
        f"{gain:+.6f} (dearest x RTE {eta:g} less cheapest), levy leakage {leakage:+.6f} (flat "
        f"levy {levy:.6f} GBP/kWh x (1 - RTE), the round-trip losses bought at the FULL delivered "
        f"price), cycle wear {wear:+.6f} (c_deg {c_deg:.6f} GBP/kWh discharged x RTE). Net "
        f"{gain + leakage + wear:+.6f} GBP/kWh, so no pair of half-hours repays the round trip. "
        "Every term is a GBP/kWh, so this holds at EVERY candidate size, not only the one measured."
    )


#: The machine-detectable marker for a value that is GENUINELY UNAVAILABLE, as opposed to a value
#: that was measured and came out at zero. `outputs.viewmodel.StreamRow.value_measured` keys off
#: exactly this token, so it is a cross-module contract and is defined HERE, with the producer that
#: decides when the condition holds, rather than being re-typed as a prose substring by the reader.
#:
#: It used to ride a sentence ("no dispatch solved - value unavailable") that a no-spec record wrote
#: on EVERY stream, and that sentence was false on all eight live campaign sites: dispatch was
#: solved (162 candidates, all optimal) and simply not SELECTED. The marker now travels only on the
#: branches where nothing was measured, so "unavailable" once again means unavailable.
VALUE_UNAVAILABLE_MARKER = "value unavailable (not invented)"


@dataclass(frozen=True)
class NoSpecDispatchEvidence:
    """What a run that selected NO system actually measured about dispatch.

    A no-spec record reports no candidate, so its streams are all £0. Until now it explained that
    £0 with "no dispatch solved - value unavailable (not invented)", which conflated three states a
    reader must be able to tell apart:

    - **nothing was ever solved** (no metered series, infeasible search) - genuinely no value;
    - **candidates were solved and priced, and every one dispatched nothing** - the value IS
      measured, it is £0, and it is a decision with a margin (Tanhouse, Colnbrook);
    - **candidates were solved and DISPATCHED, and none was selected** - dispatch produced real
      value; the run failed on economics, not for want of a dispatch (the other six sites).

    ``schedule`` is the re-solved dispatch of the candidate named in ``spec`` - the
    best-priced-floor candidate, the same one the no-viable verdict cites as evidence, so the
    record never quotes two different candidates at a reader. ``None`` when no representative solve
    could be obtained, which downgrades the note rather than inventing one.

    ``candidates_priced`` is the count the engine already computes for the verdict line, so the note
    and the verdict can never disagree about how much work was done.
    """

    candidates_priced: int
    schedule: HalfHourlyDispatchSchedule | None = None
    spec: str | None = None
    selection_reason: str | None = None


def _no_spec_notes(
    evidence: NoSpecDispatchEvidence | None,
    site: SiteModel,
    assumptions: AssumptionLibrary,
) -> list[str]:
    """What a no-spec record should say about a £0 stream, given what the run actually measured.

    The single place the three states above are turned into words. Every branch states only what
    the run can evidence: the count is the engine's own, the discharge and the arithmetic come from
    a real re-solved schedule, and where no measurement exists the note says so and carries
    :data:`VALUE_UNAVAILABLE_MARKER` rather than implying one.
    """
    if evidence is None or evidence.candidates_priced <= 0:
        # Nothing was solved, so nothing was measured. The original sentence, minus its em dash.
        return [f"no dispatch solved - {VALUE_UNAVAILABLE_MARKER}."]

    spec_txt = "" if evidence.spec is None else f" ({evidence.spec})"
    where = (
        " Measured on the best-priced-floor candidate confirmed on the full series"
        f"{spec_txt}, the same candidate the verdict cites as its evidence."
    )
    why = "" if evidence.selection_reason is None else f" Not selected: {evidence.selection_reason}"

    if evidence.schedule is None:
        return [
            f"DISPATCH WAS SOLVED, NOT SELECTED: {evidence.candidates_priced} candidate(s) were "
            "solved and priced on the full series and none was selected, so this record reports no "
            f"candidate's figures. That is why this line reads 0.00, and it is NOT a claim "
            f"that the "
            f"dispatch earned nothing. No representative dispatch could be re-measured for this "
            f"record, so the per-candidate value is {VALUE_UNAVAILABLE_MARKER} here; the sizing "
            f"grid holds every candidate that was evaluated.{why}"
        ]

    discharged = evidence.schedule.annual_discharge_kwh
    # D62 limb 1: the branch keys on discharge OUTSIDE the DFS-called periods. With DFS in every
    # objective (D57) the total is never zero on a site with called periods, so keyed on the total
    # the measured-zero branch below was unreachable.
    outside_dfs = evidence.schedule.annual_discharge_outside_dfs_kwh
    if outside_dfs > 0.0:
        return [
            f"DISPATCH WAS SOLVED AND RAN, BUT NO SYSTEM WAS SELECTED: "
            f"{evidence.candidates_priced} candidate(s) were solved and priced on the full series, "
            f"and the dispatch DISCHARGED {discharged:,.1f} kWh/yr, so the value was neither zero "
            f"nor unmeasurable. This record reports 0.00 because it reports NO CANDIDATE, not "
            f"because the dispatch earned nothing; the per-candidate figures are in the sizing "
            f"grid. The verdict is an economic one about the selected size, not an absence of "
            f"dispatch.{where}{why} Per-candidate value is {VALUE_UNAVAILABLE_MARKER} on this "
            "record, which reports no candidate."
        ]

    dfs_only = discharged > 0.0
    nothing = (
        "DISCHARGED NOTHING OUTSIDE THE DFS-CALLED PERIODS (0 kWh on the tariff)"
        if dfs_only
        else "DISCHARGED NOTHING AT ALL (0 kWh)"
    )
    note = (
        f"{ZERO_DISPATCH_LABEL}: {evidence.candidates_priced} candidate(s) were solved and priced "
        f"on the full series, none was selected, and the dispatch {nothing}"
        ". So this 0.00 is MEASURED, not missing: no half-hour's spread on this site covers "
        "the round trip, and the optimiser declined every cycle. Contrast the D45 null-input "
        "label, "
        f"which marks a stream held out of the priced floor because a DECLARED input reads NULL."
        f"{where}{why}"
    )
    if dfs_only:
        note += " " + _dfs_delivery_sentence(evidence.schedule)
    arithmetic = _round_trip_arithmetic(evidence.schedule, site, assumptions)
    return [note if arithmetic is None else note + arithmetic]


def build_value_streams(
    schedule: HalfHourlyDispatchSchedule | None,
    site: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    registry: StreamRegistry | None = None,
    context: GateContext | None = None,
    no_spec_evidence: NoSpecDispatchEvidence | None = None,
    cm_derating_basis_acknowledgement: CapacityMarketDeratingBasisAcknowledgement | None = None,
    capacity_reduction: AgreedCapacityValue | None = None,
) -> tuple[list[ValueStreamResult], list[ValueStreamResult], list[str]]:
    """Value every registry stream over the solved ``schedule`` (``None`` ⇒ unpriced, gate-only).

    Returns ``(value_streams, gated_upside, assumptions_used)``. Firmness/gating come from each
    stream's ``check_gates`` (the value stack); the £ is the engine's own, from the schedule + the
    site tariff + the assumption library. With no schedule, every stream is £0 with its real gate
    firmness (the honest degrade, mirroring the placeholder).

    ``cm_derating_basis_acknowledgement`` is the attributed opt-in that lets a **market-eligible**
    (≥1 MW, D-92) BM / ancillary stream price on D43's Capacity Market de-rating basis. Absent it —
    every ordinary run, and there is deliberately no default — such a stream **refuses to price**,
    stays £0 in ``gated_upside`` and carries the labelled reason. It bites only where that basis is
    the one actually used, i.e. where the solver reserved no deliverable capability and the volume
    fell back to ``sized power × the CM duration factor``; a unit whose capability the solver
    measured is priced on plain committed MW and needs no acknowledgement. See
    `CapacityMarketDeratingBasisAcknowledgement`.
    """
    reg = registry if registry is not None else default_registry()
    value_streams: list[ValueStreamResult] = []
    gated_upside: list[ValueStreamResult] = []
    used: set[str] = set()

    for stream in reg.all():
        sid = stream.stream_id
        gate = stream.check_gates(site, assumptions, context)
        if schedule is not None:
            gate = _enforce_engine_side_gates(sid, gate, schedule, context)
        revenue = 0.0
        cost = 0.0  # F-02: signed dispatch components carry their negative part here
        s_used: list[str] = []
        notes: list[str] = []

        if schedule is not None and _priceable(gate):
            if sid == MarketStream.TARIFF_ARBITRAGE:
                # F-02: the component is SIGNED. A negative value is a real cost (the optimum
                # bought energy at a loss to capture a larger DUoS spread) and is carried on the
                # cost channel, which finance subtracts in EBITDA — never clamped away, which
                # would report a floor above the MILP's own objective value.
                revenue, cost = _split_signed(schedule.arbitrage_value_gbp)
                s_used = [_RTE_KEY]
                notes = [
                    "site-derived from the half-hourly load × time-of-use tariff spread, "
                    "net of round-trip losses (round_trip_efficiency.ac_round_trip)."
                ]
                if cost:
                    notes.append(
                        f"NET COST £{cost}/yr: on this site the optimal dispatch pays more for "
                        "charge energy than the energy-band spread returns — the DUoS avoidance "
                        "carries the cycle. Carried as a cost, not clamped to zero (F-02)."
                    )
                # A zero here is a DECISION, not a missing input, and the two render identically
                # without this. Additive: `None` whenever the battery actually dispatched.
                zero_note = _zero_dispatch_note(schedule, site, assumptions)
                if zero_note is not None:
                    notes.append(zero_note)
            elif sid == MarketStream.DUOS_RED_BAND_AVOIDANCE:
                revenue, cost = _split_signed(schedule.duos_avoidance_value_gbp)
                notes = [
                    "site-derived: DUoS rate avoided by discharging across the red-band "
                    f"windows ({schedule.red_band_discharge_mwh} MWh red-band discharge)."
                ]
            elif sid == MarketStream.MIC_ASC_HEADROOM:
                notes = [
                    "£0: the connection-headroom value is realised through the "
                    "connection-bounded arbitrage; pricing it separately would double-count. "
                    "This covers the ARBITRAGE reading of headroom ONLY — the avoided "
                    "agreed-capacity charge and the TCR band drop are a separate, real amount and "
                    "are priced on AGREED_CAPACITY_REDUCTION (MICASC-3), not here."
                ]
            elif sid == MarketStream.AGREED_CAPACITY_REDUCTION:
                # MICASC-3. The £ is the dispatch engine's own, measured on the agreed-capacity
                # overlay solve; this branch only reports it. `capacity_reduction is None` means
                # the overlay could not run (no capacity rate derivable, or no optimum), which is
                # ABSENT, not £0 — and it says so rather than rendering as a measured nothing.
                if capacity_reduction is None:
                    notes = [
                        "NOT ASSESSED on this run: the agreed-capacity overlay did not produce a "
                        "result — either the site carries no £/kVA/day capacity charge and no "
                        "connection.dno_region to derive the published one from, or the overlay "
                        "solve reached no optimum. This £0 is ABSENT, not measured. Supply the "
                        "capacity line from the site's DUoS invoice, or the licence area, and the "
                        "stream prices. NO cross-area rate is substituted."
                    ]
                else:
                    revenue = capacity_reduction.annual_value_gbp
                    s_used = list(capacity_reduction.assumption_keys)
                    notes = list(capacity_reduction.notes)
            elif sid == MarketStream.PPA_OR_BILL_SAVINGS:
                notes = [
                    "£0 aggregate: the import-side floor is itemised across tariff arbitrage "
                    "+ DUoS red-band; the PPA £/kWh is set by the finance pricing layer."
                ]
            elif sid == MarketStream.CAPACITY_MARKET:
                revenue, s_used, notes = _capacity_market_value(
                    schedule, assumptions, site, context
                )
            elif sid == MarketStream.LOCAL_FLEX_PICLO:
                revenue, s_used, notes = _local_flex_value(schedule, assumptions, site)
            elif sid == MarketStream.DFS:
                revenue, s_used, notes = _dfs_value(schedule, assumptions, site)
            elif sid == MarketStream.LOAD_SERVICE:
                revenue, s_used, notes = _load_service_value(schedule, site)
            elif sid in _CONSENTED_PER_MW_YR_STREAMS:
                # BM / ancillary: priced only on explicit consent + a customer-agreed £/MW/yr rate
                # (D43). WHOLESALE_P415 is NOT here — the engine prices its contracted export mode
                # from the overlay export volume in `_final_result`, not from the floor schedule.
                revenue, s_used, notes = _consented_gated_per_mw_yr_value(
                    sid,
                    schedule,
                    site,
                    assumptions,
                    context,
                    basis_acknowledgement=cm_derating_basis_acknowledgement,
                )
        elif schedule is None:
            notes = _no_spec_notes(no_spec_evidence, site, assumptions)

        used.update(s_used)
        result = _result(
            sid, gate, revenue=revenue, cost=cost, assumptions_used=s_used, extra_warnings=notes
        )
        if sid in GATED_UPSIDE_STREAMS:
            # Placement (D43): a consented, priced (CONTRACTED) gated stream moves into the priced
            # floor (value_streams) so finance prices it with the contracted haircut. Everything
            # else — unconsented, or consented-but-unpriced (no agreed rate / no volume, incl.
            # WHOLESALE which is priced later via the export overlay) — stays gated upside.
            if gate.firmness == Firmness.CONTRACTED and result.annual_revenue_gbp > 0:
                value_streams.append(result)
            else:
                gated_upside.append(result)
        else:
            value_streams.append(result)

    # S-2 / F-11: one asset, one power budget. Each capacity-shaped stream above priced itself
    # against the SAME de-rated MW independently, so a site consenting to several of them sold the
    # same megawatt two or three times into the contracted floor. Allocate the rating across the
    # claims and scale each stream to what it actually won. Applied as a post-pass so every
    # stream's own pricing logic is untouched — only the double count is removed.
    if schedule is not None:
        value_streams, gated_upside = _apply_capacity_budget(value_streams, gated_upside, schedule)

    return value_streams, gated_upside, sorted(used)


def _apply_capacity_budget(
    value_streams: list[ValueStreamResult],
    gated_upside: list[ValueStreamResult],
    schedule: HalfHourlyDispatchSchedule,
) -> tuple[list[ValueStreamResult], list[ValueStreamResult]]:
    """Scale capacity-shaped streams to the share of the rating each actually won.

    Constraint and citations: `gridq.value_stack.capacity_budget`. A stream cut back keeps its
    entry — with the shortfall stated on it — so the reader sees that a claim was made and refused,
    never a silently smaller number.
    """
    rated_mw = (schedule.power_kw or 0.0) / 1000.0
    usable_mwh = (schedule.energy_kwh or 0.0) / 1000.0
    if rated_mw <= 0:
        return value_streams, gated_upside

    derated_mw = schedule.deliverable_capacity_mw
    if derated_mw is None or derated_mw <= 0:
        derated_mw = rated_mw

    claims = [
        CapacityClaim(v.stream, derated_mw)
        for v in (*value_streams, *gated_upside)
        if v.stream in POWER_BUDGET_STREAMS and v.annual_revenue_gbp > 0
    ]
    if len(claims) < 2:
        return value_streams, gated_upside  # nothing to contend for the budget

    budget = allocate_capacity(claims, rated_power_mw=rated_mw, usable_energy_mwh=usable_mwh)

    def _scaled(v: ValueStreamResult) -> ValueStreamResult:
        award = budget.awards.get(v.stream)
        if award is None:
            return v
        if award.was_cut:
            note = (
                f"CAPACITY BUDGET: this stream claimed {award.claimed_mw:,.3f} MW but the asset "
                f"could only award {award.awarded_mw:,.3f} MW (limited by {award.limited_by}) once "
                "every contracted service was allocated against the same rating. Revenue scaled by "
                f"{award.scale:.1%}. One megawatt cannot be sold to two markets at once."
            )
            return v.model_copy(
                update={
                    "annual_revenue_gbp": round(v.annual_revenue_gbp * award.scale, 2),
                    "annual_cost_gbp": round(v.annual_cost_gbp * award.scale, 2),
                    "warnings": [*v.warnings, note, *budget.warnings],
                }
            )
        # D57: the claim was paid IN FULL where the shared budget would have cut it. The £ is
        # unchanged; what is added is the MEASURED size of the assumption, on the stream it applies
        # to, because a bypass nobody can see on the record is not a stated assumption (CLAUDE.md
        # section 2.0 rule 4). `budget.awards` already holds the full claim, so the shortfall has to
        # be read from `constrained_awards`.
        uncut = budget.d57_uncut(v.stream)
        if uncut is None:
            return v
        note = (
            "D57 ASSUMED APPROVED - STACKING EXCLUSIVITY AND THE SHARED POWER/ENERGY BUDGET: "
            f"this stream claimed {uncut.claimed_mw:,.3f} MW and the shared budget would have "
            f"awarded it "
            f"only {uncut.awarded_mw:,.3f} MW (limited by {uncut.limited_by}), a cut to "
            f"{uncut.scale:.1%}. On this academic validation basis the claim is PAID IN FULL: the "
            "stacking rules, the shared inverter rating and the shared energy-sustainability "
            "budget are assumed satisfied for this site and battery size (D57 limb 3, "
            "owner-directed 2026-08-18). DIRECTION OF EFFECT: UP - this stream's GBP is "
            f"{(1.0 / uncut.scale if uncut.scale > 0 else float('inf')):,.2f}x what the constraint "
            "would have allowed. NO DOUBLE-BOOKING IS INTRODUCED: two counterparties paying "
            "availability on the same megawatt is two pounds from two payers, not one pound "
            "counted twice, and the two accounting-identity streams (PPA_OR_BILL_SAVINGS, "
            "MIC_ASC_HEADROOM) "
            "are untouched by this allocator and stay at GBP 0 on their stated carriers. "
            "CONFIRMATION ASK: the stacking permissions actually held on this meter point, per "
            "counterparty."
        )
        return v.model_copy(update={"warnings": [*v.warnings, note, *budget.warnings]})

    return [_scaled(v) for v in value_streams], [_scaled(v) for v in gated_upside]


def wholesale_contracted_stream_result(
    base: ValueStreamResult,
    *,
    agreed_tariff_gbp_per_kwh: float,
    export_mwh: float,
    price_content_hash: str | None = None,
    price_source_ref: str | None = None,
    coverage_note: str | None = None,
    cannibalised_floor_gbp: float | None = None,
) -> ValueStreamResult:
    """The CONTRACTED wholesale floor entry (D43): agreed export tariff × overlay export MWh, NET.

    Built ONLY when the customer consented to WHOLESALE_P415 AND a declared export tariff exists
    (``ExportArrangement.export_tariff_gbp_per_kwh``). The £ is the contracted tariff × the
    engine-measured overlay export volume — a real floor value finance prices with the contracted
    haircut; firmness is CONTRACTED (never FIRM — the four never-FIRM layers stand). The
    perfect-foresight Elexon-MID delta stays a SEPARATE indicative annotation on the gated_upside
    entry (:func:`export_upside_stream_result`), never contracted, never in P50/P90/DSCR.

    **``cannibalised_floor_gbp`` closes MD-F-05 limb (b).** The contracted export entry is appended
    to the *unchanged* no-export ``value_streams``, so pricing it gross claimed "full no-export
    arbitrage **plus** full export volume" — a physically impossible floor, because a MWh exported
    past the meter is a MWh not discharged behind it. This parameter is the floor value the export
    overlay **gave up**:

        ``cannibalised_floor_gbp = (floor-only floor terms) − (overlay floor terms)``, ``≥ 0``

    i.e. the arbitrage and DUoS-avoidance value the no-export dispatch earned and the exporting
    dispatch no longer does. It is subtracted here, and the arithmetic is printed on the note. The
    producer of the two floor-term figures is the engine (`optimisation.engine`); this is the
    valuation side of that contract, kept optional and defaulting to ``None`` so it is purely
    additive — a caller that does not yet supply it gets the previous number plus a loud GROSS
    warning, never a silently netted or silently gross one.
    """
    gross = round(max(0.0, agreed_tariff_gbp_per_kwh * export_mwh * 1000.0), 2)
    # Clamped at ≥0: netting can cancel the export value entirely (export never pays more than the
    # behind-the-meter dispatch it displaced) but can never make a contracted revenue line negative.
    cannibalised = 0.0 if cannibalised_floor_gbp is None else max(0.0, cannibalised_floor_gbp)
    revenue = round(max(0.0, gross - cannibalised), 2)
    notes = list(base.warnings)
    notes.append(
        f"wholesale export CONTRACTED £{revenue:,.2f}/yr — the customer-agreed export tariff "
        f"£{agreed_tariff_gbp_per_kwh:,.4f}/kWh (consent given) × {export_mwh} MWh exported in the "
        "engine's overlay dispatch. A contracted floor value; the Elexon-MID perfect-foresight "
        "delta is reported separately as indicative gated upside, never contracted."
    )
    if cannibalised_floor_gbp is None:
        notes.append(
            "GROSS — NOT NETTED (MD-F-05 limb b): no cannibalised-floor figure was supplied, so "
            f"this £{gross:,.2f}/yr is the full export tariff revenue while the priced floor still "
            "carries the FULL no-export arbitrage and DUoS-avoidance value of the same MWh. The "
            "two cannot both be earned — a MWh exported past the meter is a MWh not discharged "
            "behind it — so the floor is overstated by whatever behind-the-meter value the export "
            "overlay displaced. ASSUMPTION STATED: displaced behind-the-meter value is treated as "
            "zero here because it was not measured, which is the optimistic direction."
        )
    else:
        notes.append(
            f"NET of cannibalised behind-the-meter value (MD-F-05 limb b): £{gross:,.2f} gross "
            f"− £{cannibalised:,.2f} of arbitrage / DUoS-avoidance floor value the export overlay "
            f"gave up = £{revenue:,.2f}/yr. A MWh exported past the meter is a MWh not discharged "
            "behind it, so only the incremental value enters the priced floor."
        )
    if price_content_hash:
        pin = f"overlay export volume from the supplied half-hourly series ({price_content_hash}"
        pin += f"; {coverage_note})." if coverage_note else ")."
        notes.append(pin)
    if price_source_ref:
        notes.append(f"price source: {price_source_ref}.")
    return base.model_copy(
        update={
            "annual_revenue_gbp": revenue,
            "firmness": Firmness.CONTRACTED,
            "gate_status": GateStatus.OPEN.value,
            "warnings": notes,
        }
    )


# ---------------------------------------------------------------------------
# D46 — the named FEASIBILITY-STUDY basis: a derived, SOURCED export tariff, and the priced-floor
# entry it produces. Both halves live here because they are pricing, not engine state; the engine
# decides WHETHER a study basis applies, this decides WHAT it is worth and how it is labelled.
# ---------------------------------------------------------------------------

#: The `DerivedAssumption.derived_by` stamp for a tariff derived by this module. A deterministic
#: derivation names its module, not a model identity (`gridq.schemas.derived`).
STUDY_TARIFF_DERIVED_BY = "gridq.optimisation.valuation.derive_flat_export_tariff"


class ExportTariffUnsourced(ValueError):
    """Raised when no export tariff can be SOURCED from the supplied series.

    D46 requires the study basis's export tariff to be derived and **sourced**, and D45's no-null
    amendment applies to it exactly as to any other input: a £/kWh nobody can point at a dated
    document for is an invented one, and an invented export tariff multiplies into every £ on the
    floor of every report the study produces. So the failure mode is a **refused study basis whose
    message names exactly what is missing**, never a fabricated rate. A refused study basis is a
    fine outcome.
    """


def derive_flat_export_tariff(
    forecast: MarketForecast,
    *,
    derived_on_release: str | None = None,
) -> tuple[float, DerivedAssumption]:
    """Derive ONE flat export tariff (£/kWh) from a sourced wholesale price series, and record it.

    Returns ``(tariff_gbp_per_kwh, record)`` where ``record`` is the `DerivedAssumption`
    (``kind="derived_input"``) D46 requires: the source, the vintage, the direction of effect and
    the confirmation ask. Raises `ExportTariffUnsourced` — naming exactly what is missing — when the
    series cannot support a sourced figure.

    **Why FLAT, and why one scalar.** D46 asks for "a derived, sourced export tariff", singular, and
    `DerivedAssumption.value` is a scalar, so a shaped series is not recordable in the vehicle the
    decision mandates. It is also the only honest shape available: the alternative — mapping one
    sourced day's within-day shape onto every day of a metered year — is exactly the extrapolation
    `optimisation.export_prices.align_export_prices` and the ingestion discipline refuse
    (full-coverage-or-skip, never interpolated). And a flat £/kWh is what a real fixed-price export
    PPA looks like, which is the arrangement the study is asking about.

    **Direction of effect, both ways, because they do not point the same way.** The LEVEL is
    OPTIMISTIC: this is the raw market reference price with no route-to-market discount, no
    imbalance or shape risk cost and no supplier/aggregator margin deducted, all of which a real
    export offer carries. The SHAPE is PESSIMISTIC for a merchant route: a flat tariff deletes the
    intra-day wholesale spread, which is where most merchant export value lives, so a
    shape-following counterparty could beat it. Both statements ride the record.

    The mean is taken over the series' PRICED points only (a point with no price is never read as
    zero) and rounded to 6 dp for determinism.
    """
    priced = [
        float(p.price_gbp_per_mwh) for p in forecast.points if p.price_gbp_per_mwh is not None
    ]
    if not priced:
        raise ExportTariffUnsourced(
            "cannot derive an export tariff: the supplied wholesale series carries NO priced "
            f"points ({len(forecast.points)} point(s), none with a price). MISSING: a dated GB "
            "wholesale price series with at least one priced half-hour. The study basis is refused "
            "rather than priced at an invented £/kWh (D45, D46)."
        )
    source_ref = forecast.source or forecast.file_ref
    if not source_ref:
        raise ExportTariffUnsourced(
            "cannot derive an export tariff: the supplied wholesale series names no source and no "
            "file reference, so the resulting £/kWh would be uncitable. MISSING: "
            "`MarketForecast.source` or `.file_ref`. GB wholesale prices are public and dated — an "
            "uncited figure is an invented one, so the study basis is refused (D45, D46)."
        )
    pin = forecast.content_hash or forecast.file_sha256
    if not pin:
        raise ExportTariffUnsourced(
            "cannot derive an export tariff: the supplied wholesale series carries neither a "
            "content_hash nor a file_sha256, so the derived £/kWh could not be traced back to the "
            "exact bytes it came from. MISSING: a content/file hash on the series (see "
            "`gridq.ingestion.market_forecasts.hash_market_forecast`). The study basis is refused "
            "(D46 traceability; CLAUDE.md §5)."
        )
    mean_gbp_per_mwh = sum(priced) / len(priced)
    if not (mean_gbp_per_mwh > 0):
        raise ExportTariffUnsourced(
            f"cannot derive an export tariff: the mean of the {len(priced)} priced point(s) is "
            f"£{mean_gbp_per_mwh:,.6f}/MWh, which is not a positive export price. A non-positive "
            "tariff is not an export arrangement — the study basis is refused rather than priced "
            "at zero or negative (D45: a null on the pricing path is a defect, not a state)."
        )
    tariff = round(mean_gbp_per_mwh / 1000.0, 6)
    stamps = [p.timestamp for p in forecast.points if p.price_gbp_per_mwh is not None]
    vintage = f"{min(stamps).isoformat()}–{max(stamps).isoformat()}" if stamps else "unknown"
    record = DerivedAssumption(
        kind="derived_input",
        key=StudyBasis.TARIFF_KEY,
        missing_input=(
            "the export tariff the site would be paid for energy sent past the meter — no export "
            "arrangement, offer or PPA has been supplied for this site"
        ),
        value=tariff,
        unit="GBP/kWh",
        basis=(
            f"volume-unweighted arithmetic mean of the {len(priced)} PRICED half-hour(s) in the "
            f"supplied GB wholesale series (£{mean_gbp_per_mwh:,.6f}/MWh ÷ 1000). Vintage "
            f"{vintage}. Carried as ONE FLAT £/kWh, which is the shape a fixed-price export PPA "
            "takes and the only shape recordable as a single derived value; mapping the series' "
            "within-day shape onto other days would be extrapolation, which this repo refuses "
            "(full-coverage-or-skip). DIRECTION OF EFFECT — LEVEL: OPTIMISTIC, this is the raw "
            "market reference price with no route-to-market discount, imbalance/shape risk cost or "
            "counterparty margin deducted, all of which a real export offer carries. DIRECTION OF "
            "EFFECT — SHAPE: PESSIMISTIC for a merchant route, a flat tariff deletes the intra-day "
            "wholesale spread where most merchant export value sits. This is a market reference "
            "price, NOT this site's contracted export price, and no such contract exists."
        ),
        sources=[f"{source_ref} ({pin}); vintage {vintage}"],
        derivation_method="sourced_default",
        derived_by=STUDY_TARIFF_DERIVED_BY,
        derived_on_release=derived_on_release,
        confidence_ceiling=ceiling_for_key(StudyBasis.TARIFF_KEY),
        confirmation_ask=(
            "obtain a real export offer or PPA term sheet from a route-to-market counterparty (the "
            "£/kWh, its indexation and its term), together with the customer's consent to export. "
            "Until then this tariff is a market reference price standing in for a contract that "
            "does not exist."
        ),
    )
    return tariff, record


def study_basis_export_stream_result(
    base: ValueStreamResult,
    *,
    study: StudyBasis,
    export_mwh: float,
    overlay_floor_terms_gbp: float | None = None,
    measured_export_revenue_gbp: float | None = None,
) -> ValueStreamResult:
    """The D46 STUDY-BASIS export entry for the PRICED FLOOR: derived tariff × exported MWh, GROSS.

    Firmness is CONTRACTED, which is what carries the £ into the priced floor and therefore into
    P50, P90, the DSCR and both IRRs (``_PRICED_FIRMNESS`` is ``{FIRM, CONTRACTED}``; a gated-upside
    stream may never be FIRM, and the enum is a frozen contract). **CONTRACTED here is the pricing
    mechanism, not a claim that a contract exists**, so every channel a reader can reach says so:
    ``consent_status`` stays ``None`` (nothing was consented), ``gate_status`` names the study basis
    rather than an OPEN gate, ``unlock_condition`` states the commercial ask, ``assumptions_used``
    carries the register keys, and the warnings carry the basis banner, the derived tariff's own
    register statement and the outstanding-consent statement in D45's `pending_permission` language.

    **GROSS, and that is the single netting site (MD-F-05 limb (b)).** On the study basis the
    floor's dispatch-derived components (tariff arbitrage, DUoS red-band avoidance) are priced from
    the EXPORT dispatch itself — the same solve that produced the export volume — so the netting has
    already happened at source and there is nothing left to net here. ``overlay_floor_terms_gbp``,
    if supplied, is stated on the note purely so a reader can verify
    ``floor total == overlay floor terms + gross export`` by arithmetic. Subtracting a cannibalised
    figure here as well would deduct the same £ twice (see
    `optimisation.engine.MilpEngine._restate_export_netting_site`, which records why exactly one
    netting site is permitted).
    """
    # D57 (2026-08-18): ``measured_export_revenue_gbp`` is the schedule's OWN export term - the
    # Σ price_t·export_t·Δt the objective actually maximised - and it is what is reported whenever
    # the
    # caller has it. The flat ``tariff × volume`` product below is only correct while the export
    # price is a single number for the whole year; once the price is the real half-hourly series
    # (the
    # export decision cannot clear on an annual mean at all, so it now is) the product would report
    # the MEAN price on volume the optimiser only exported because the PEAK price cleared, which
    # understates the stream by the whole reason it exports. Reporting the objective's own term is
    # also the only version that keeps the reported floor equal to the solved objective (F-02).
    if measured_export_revenue_gbp is not None:
        revenue = round(max(0.0, measured_export_revenue_gbp), 2)
        realised = (revenue / (export_mwh * 1000.0)) if export_mwh > 0 else 0.0
        volume_note = (
            f"STUDY-BASIS EXPORT IN THE PRICED FLOOR £{revenue:,.2f}/yr = the MEASURED export term "
            f"of the solved objective, Σ price_t x export_t x Δt over {export_mwh:,.6f} MWh "
            "exported "
            f"by the study dispatch, at a realised average of £{realised:,.6f}/kWh. BOTH the "
            "volume "
            "AND the price are the model's own: it co-optimised export against the "
            "behind-the-meter "
            "floor on a REAL HALF-HOURLY price series, so it exported only in the half-hours where "
            "the price cleared the delivered cost of a charged kWh plus its marginal degradation. "
            "The realised average therefore sits ABOVE the annual mean by construction, and that "
            "is "
            "the point rather than an error: an annual mean of the same series never clears the "
            "bar "
            f"at all. The study basis's own flat tariff of £{study.export_tariff_gbp_per_kwh:,.6f}"
            "/kWh remains the fallback where no half-hourly series aligns to the horizon."
        )
    else:
        revenue = round(max(0.0, study.export_tariff_gbp_per_kwh * export_mwh * 1000.0), 2)
        volume_note = (
            f"STUDY-BASIS EXPORT IN THE PRICED FLOOR £{revenue:,.2f}/yr = the DERIVED, SOURCED "
            f"export tariff £{study.export_tariff_gbp_per_kwh:,.6f}/kWh × {export_mwh:,.6f} MWh "
            "exported by the study dispatch (which co-optimised export against the "
            "behind-the-meter "
            "floor, so the volume is the model's own choice and not an assumed utilisation). THE "
            "PRICE IS A FLAT ANNUAL MEAN on this run, because no half-hourly series aligned to the "
            "horizon - a weak basis for an export decision, since it removes exactly the "
            "volatility "
            "export value lives in."
        )
    notes = list(base.warnings)
    notes.append(volume_note)
    notes.append(study.floor_entry_note())
    notes.append(
        "GROSS BY DESIGN, AND NOT OVERSTATED (MD-F-05 limb b): the floor's dispatch-derived "
        "components (tariff arbitrage, DUoS red-band avoidance) are priced from THIS SAME export "
        "dispatch rather than from a separate no-export solve, so the behind-the-meter value the "
        "export displaced is already out of the floor. There is exactly ONE netting site and this "
        "is not it — netting here as well would deduct the same £ twice."
        + (
            ""
            if overlay_floor_terms_gbp is None
            else f" Check: floor total = £{overlay_floor_terms_gbp:,.2f} export-dispatch floor "
            f"terms + £{revenue:,.2f} gross export."
        )
    )
    notes.append(
        "FIRMNESS IS 'CONTRACTED' AS A PRICING MECHANISM ONLY — it is the firmness class finance "
        "prices into the floor, and it is NOT a claim that a contract exists. No export contract, "
        "consent, MEC, export MPAN or G99/G100 approval exists for this site; consent_status is "
        "null and remains so."
    )
    notes.append(study.tariff_record.statement())
    notes.append(study.consent_record.statement())
    return base.model_copy(
        update={
            "annual_revenue_gbp": revenue,
            "annual_cost_gbp": 0.0,
            "firmness": Firmness.CONTRACTED,
            "gate_status": _STUDY_BASIS_GATE_STATUS,
            "unlock_condition": (
                "requires customer consent to export (D45 pending_permission) PLUS a contracted "
                "route to market at or above the derived tariff — a commercial ask, not a "
                "modelling change. Priced here ONLY because the run declared the D46 study basis."
            ),
            "consent_status": None,
            "assumptions_used": [*base.assumptions_used, *study.assumption_keys()],
            "warnings": notes,
        }
    )


def study_basis_label_dispatch_component(
    entry: ValueStreamResult, *, study: StudyBasis
) -> ValueStreamResult:
    """Label a dispatch-derived priced-floor component that the study dispatch itself produced.

    D46 makes the labelling load-bearing and requires **every affected figure** to be traceable to
    its basis. Tariff arbitrage and DUoS red-band avoidance are affected: on a study-basis run they
    are priced from the dispatch that also exported, so they are the POST-export figures and are
    lower than the same site's underwriting-basis floor by the behind-the-meter value the export
    displaced. A reader rendering one of these entries on its own would otherwise see a figure that
    looks like an underwriting number and is not one.

    Same reasoning, and the same channel, as `MilpEngine._label_override` and the MD-F-05(b)
    sentence `_rebase_dispatch_floor_streams` puts on these two entries when D43's contracted route
    re-bases them: a per-stream entry can be read and rendered on its own, so it carries the label.
    """
    return entry.model_copy(
        update={
            "warnings": [
                *entry.warnings,
                f"{study.LABEL}: this dispatch-derived floor component is priced from the STUDY "
                "dispatch — the same solve that exported — so it is already NET of the "
                "behind-the-meter value the export displaced (MD-F-05 limb b, netted once, at "
                "source). It is therefore NOT the same figure this site would report on the "
                "underwriting basis, where no export exists and this component is higher.",
            ]
        }
    )
