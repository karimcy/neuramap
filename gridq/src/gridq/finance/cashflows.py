"""Deterministic cashflow model (M5, part 1).

Turns a `SiteModel` + an `OptimisationResult` (or a compatible result object) + a versioned
`AssumptionLibrary` into the year-by-year project cashflows the rest of the financial model
consumes: annual revenue by stream, annual cost by line, the capex estimate, an EBITDA-like
project cashflow and the free cashflow before debt — plus a P50 and a P90 view.

Hard rules honoured here (see SKILLS.md / docs/financial_model.md):
- **No private constants.** Every £/%/ratio is read from the assumption library via the
  passed-in `AssumptionLibrary` — there are no hardcoded capex/opex/revenue/haircut numbers.
  The only module-level number is the *structural projection horizon* default, which is a
  modelling parameter (not an underwriting £-assumption) and is always surfaced as a warning
  when it is defaulted.
- **No hallucinated inputs.** A missing input (no sizing, no dispatch energy accounting) is a
  warning and an *unavailable* line — never an invented number.
- **Placeholders stay labelled.** Every assumption whose status is not ``confirmed`` adds a
  warning naming it; a model built on any such assumption is not bankable.
- **No LLM arithmetic.** Pure, deterministic Python: identical inputs → identical output.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus
from gridq.finance.lifecycle import (
    CM_RULE_SOURCE,
    INDEXED_COST_LINE_KEYS,
    CmIndexation,
    cpi_null_warning,
    escalation_factors,
    lifecycle_capital_flows,
    read_cpi,
    resolve_cm_indexation,
)
from gridq.finance.returns import project_irr
from gridq.schemas import (
    AvailabilityCommitment,
    Firmness,
    MarketStream,
    SiteModel,
    ValueStreamResult,
    is_priced_floor,
)
from gridq.schemas.lifecycle import CashflowLineBasis, LifecycleFlowEntry, LifecycleInputs

# --- declared bases (truth-in-labelling; N1, N13/H-15, N14) ----------------
#
# These are LABELS, not numbers: none of them injects a £ or a % into any output. They exist
# because the 2026-08-10 hostile review found the pack silently reporting a pre-tax ratio against
# a post-tax covenant, flat-nominal cashflows discounted at nominal rates, and a horizon with no
# terminal flow — none of it disclosed anywhere (register C-7). Each label rides on the
# `CashflowModel` that carries the series, so the series cannot reach the debt/DSCR boundary or a
# report unlabelled, and each has a matching warning so it also reaches `FinancialCase.warnings`.
# The substantive fixes (a tax layer, an escalation index, a residual-value basis) are Tier B:
# they need a human input that is not derivable from code or from GB market mechanics, and NONE
# of them is invented here.

#: N1 — the basis of every cashflow series in this module, and of the DSCR measured on it.
PRE_TAX_CFADS_BASIS = (
    "PRE-TAX — no corporation tax is modelled; this ratio is not comparable to a post-tax covenant"
)

#: N13/H-15 — the escalation basis of every revenue and cost line in this module.
FLAT_NOMINAL_UNESCALATED_BASIS = (
    "FLAT-NOMINAL, UN-ESCALATED — no CPI/RPI index is modelled on any revenue or cost line; "
    "nominal debt rates and a nominal equity hurdle are applied to flat cashflows"
)

#: N13/H-15, WP-UW-17 (D129) — the basis whenever the release states a numeric
#: `financing.cpi_pct_per_year`: a nominal ledger with the fixed cost lines escalated, a tenored T-4
#: Capacity Market payment indexed per its Regulations, and every energy-price line held flat.
NOMINAL_CPI_ESCALATED_BASIS = (
    "NOMINAL, SELECTIVELY CPI-ESCALATED — the fixed cost lines (opex, insurance, metering/IDNO, "
    "DNO, business rates) escalate at financing.cpi_pct_per_year from year 1; a tenored T-4 "
    "Capacity Market payment is CPI-indexed in term per SI 2014/2043 Sch 1 para 3(5); every "
    "energy-price revenue and cost line is held flat-nominal"
)

#: N14 — the capital flows absent from the FCF series, named individually.
OMITTED_CAPITAL_FLOWS: tuple[str, ...] = (
    "terminal/residual value at horizon end",
    "decommissioning / site restoration",
    "inverter and balance-of-plant replacement",
)

# --- structural modelling parameters (NOT underwriting £-assumptions) ------

# The default projection horizon (years) used when a caller does not supply one. This is a
# *structural* modelling choice, not a priced number — it injects no £/% into any output. It
# cannot live in the assumption library yet (the library's 23 sections are frozen and none is
# a horizon), so it is an explicit parameter with a labelled default, and using the default
# always emits a warning. See OPEN_COMMENTS (finance) — it wants an assumption-library home.
DEFAULT_HORIZON_YEARS = 15

# Cost-line keys, in a fixed order so the cost dict is deterministic.
COST_LINE_KEYS: tuple[str, ...] = (
    "opex",
    "insurance",
    "metering_idno",
    "dno",
    "business_rates",
    "levies_on_charge",
    "windowed_levies_avoided",
    "parasitic_load",
)

#: The cost lines that may legitimately be NEGATIVE. ``windowed_levies_avoided`` is the model's
#: first: a levy charged only inside a peak window is AVOIDED by the discharge the optimiser makes
#: there, so its net effect on the site's bill is a credit, and a credit on the cost side is a
#: negative cost. It is not netted into ``levies_on_charge`` — that line's name says "on charge" and
#: means the flat levy on the round-trip losses, which stays a real positive cost.
SIGNED_COST_LINE_KEYS: frozenset[str] = frozenset({"windowed_levies_avoided"})

#: Release-gated parasitic (auxiliary) load cost. Absent from gb_v0_1/gb_v1_0 — those releases
#: omit the BESS's own continuous draw entirely (behaviour unchanged). From gb_v1_2 the key
#: exists (a labelled placeholder): the dispatch shrinks the connection charge headroom by
#: fraction x rated power, and finance prices the annual auxiliary energy here.
PARASITIC_LOAD_KEY = ("battery_opex", "parasitic_load_fraction_of_power")
_HOURS_PER_YEAR = 8760.0

#: H-17 — the ONE key that de-rates revenue for fleet availability: a published GB-fleet
#: availability figure, DISTINCT from degradation (degradation shrinks the MWh the asset can
#: deliver; unavailability removes the hours in which it can deliver anything, and the two are
#: separate lines in the same published decomposition). No percentage is hardcoded here — it is
#: read from the release, which sources it through `docs/bess_constants_register.md`.
AVAILABILITY_FACTOR_KEY = ("availability", "fleet_availability_pct")

#: The neighbouring key in the same section that is emphatically NOT a substitute for it. Per its
#: own library notes `expected_availability_pct` is the SITE-SPECIFIC firm-commitment factor and
#: stays an empty home per D30; it is a different quantity on a different basis, so this module
#: never falls back to it. It is named here only so that a release which populates it while
#: leaving the fleet key empty gets told the de-rate is still off, rather than the omission
#: passing silently — the "correct in module, wrong on the real path" failure mode.
_NOT_THE_FLEET_AVAILABILITY_KEY = ("availability", "expected_availability_pct")

#: D-130 — contracted streams whose P90 contribution is forced to ZERO once the award expires.
#: A local-flex award is typically a 1–2 year terminable DSO contract with no re-procurement
#: guarantee for that zone at all (whole NGED zones received zero dispatch in 2024/25), so the
#: cautious case cannot carry a re-contracting assumption for it. Other contracted streams keep a
#: post-expiry P90 contribution, but at the MERCHANT haircut — re-contracting is not a contract.
#:
#: DFS joins it (2026-08-13). DFS was added as the ONLY direct NESO revenue route below 1 MW, which
#: makes it the sub-1 MW sites' most load-bearing contracted line and therefore the one whose
#: post-expiry treatment matters most. Its re-contracting basis is strictly WEAKER than a DSO
#: tender's, not merely as weak: a DSO award at least has a named constraint zone and a repeating
#: procurement round, whereas DFS is a NESO service procured season by season with no guarantee the
#: service is run at all in a future winter. Carrying post-expiry DFS revenue in the cautious case
#: would assume both that the scheme continues to exist and that this unit is re-awarded inside it —
#: two assumptions, neither contracted. It bites only when a DFS tenor is actually supplied, so no
#: existing figure moves; see :func:`_warn_contract_tenor_basis` for the per-stream disclosure.
_NO_P90_AFTER_EXPIRY: frozenset[str] = frozenset(
    {MarketStream.LOCAL_FLEX_PICLO.value, MarketStream.DFS.value}
)

#: Why each member of :data:`_NO_P90_AFTER_EXPIRY` is there, in the stream's own terms. The
#: post-expiry warning used to hard-code "a terminable DSO award … for the zone", which is simply
#: not what DFS is — a wrong reason attached to a right treatment is still a defect in a pack a
#: lender reads. Keyed so a stream added to the set without a reason fails the disclosure test.
_NO_P90_REASON: dict[str, str] = {
    MarketStream.LOCAL_FLEX_PICLO.value: (
        "a terminable DSO award carries no re-procurement guarantee for the zone (whole NGED "
        "zones received zero dispatch in 2024/25)"
    ),
    MarketStream.DFS.value: (
        "DFS is procured season by season and NESO gives no guarantee the service runs at all in "
        "a future winter, so post-expiry revenue would assume both the scheme's continuation and "
        "a re-award inside it"
    ),
}

#: Contracted streams whose P90 contribution is ZERO **inside the award term too**, not only once
#: it has expired. Distinct from :data:`_NO_P90_AFTER_EXPIRY` (the D-130 post-expiry rule, already
#: live): this set governs the years the award is actually running, where the module previously
#: paid the single general CONTRACTED haircut. Because no release carries
#: ``p90_revenue_haircut_contracted_pct`` (gb_v1_9 adds it as a declared null), that general haircut
#: is in practice the MERCHANT 15% fallback, so 85% of a terminable DNO-flex award was entering P90
#: and the lender-basis DSCR. One scalar cannot serve a regulated CM availability payment and a
#: 90-day-terminable DSO award at once: for the CM payment 15% is conservative, for these two it is
#: OPTIMISTIC against requirements that are explicit about the cautious case.
#:
#: **This is not a hardcoded haircut and it does not breach the module docstring's no-hardcoded-
#: numbers rule.** It is a STRUCTURAL EXCLUSION mandated by MUST requirements ("never allow it to
#: carry the DSCR", D-139), of exactly the class :data:`_NO_P90_AFTER_EXPIRY` already applies as a
#: forced zero. The distinction the module holds to: a percentage a release could CALIBRATE is read
#: from the library and never invented here; an exclusion the requirements MANDATE is code. Nothing
#: is calibratable about "at or near zero" plus a published lower-tail utilisation rate of 0.00.
#:
#: P50 is untouched by membership — this is a cautious-case treatment, not a re-pricing — and the
#: effect is DISCLOSED per stream by :func:`_warn_in_term_p90_zeroed` whenever it bites.
_NO_P90_IN_TERM: frozenset[str] = frozenset(
    {MarketStream.LOCAL_FLEX_PICLO.value, MarketStream.DFS.value}
)

#: Why each member of :data:`_NO_P90_IN_TERM` is there, in the stream's own terms. **The two
#: members rest on DIFFERENT authorities and they must never be cross-cited**: D-139/D-1410 are
#: about DNO flexibility and say nothing about a NESO service, and attaching a DSO-zone reason to
#: DFS is the "wrong reason on a right treatment" defect :data:`_NO_P90_REASON` exists to prevent.
#: Keyed so a stream added to the set without a reason fails the disclosure test.
_NO_P90_IN_TERM_REASON: dict[str, str] = {
    MarketStream.LOCAL_FLEX_PICLO.value: (
        "the DNO flexibility line's P90 case must sit at or near zero and must never carry the "
        "DSCR (D-139). Its utilisation leg is paid only when the DNO calls, and D-1410 publishes "
        "the P90 utilisation rate as 0.00 to 0.02 against a P50 of 0.05 (NGED SAOU_DA) or 0.22 "
        "(UKPN SAOU). Its availability leg is a fixed fee for the award's term, but the ENA "
        "standard agreement lets either party end the award on 90 days' notice without cause "
        "(D-130), and the fee is priced gross with no sourced delivery ratio (D-116), so the "
        "cautious case does not count it either"
    ),
    MarketStream.DFS.value: (
        "no realised DFS clearing prices or acceptance rates exist for the post-9-April-2026 "
        "zonal, bi-directional design (docs/bess_modelling_requirements.md section 2, which "
        "requires DFS to be flagged as an emerging rather than an established revenue stream), and "
        "under E-06 acceptance is binary per submission against a Zonal Cap that 'may be zero (0) "
        "MW', so even inside a live registration the cautious case cannot assume any accepted "
        "volume"
    ),
}


class InsufficientInputsError(ValueError):
    """Raised when the inputs cannot support a cashflow model at all.

    Used when there is no sizing to build a capex/cashflow from (an unsized or infeasible
    result). The caller should turn this into a ranked data ask / not-bankable quote rather
    than the finance layer inventing a system to price.
    """


@runtime_checkable
class ResultLike(Protocol):
    """The subset of `OptimisationResult` the cashflow model reads.

    Declared as a Protocol so a *compatible* result object (e.g. a future real-engine result
    or a test double) works without importing the engine. The concrete `OptimisationResult`
    satisfies it. Optional dispatch energy accounting is read separately (see `DispatchEnergy`).
    """

    selected_power_kw: float | None
    selected_energy_kwh: float | None
    duration_hours: float | None
    value_streams: list[ValueStreamResult]
    warnings: list[str]


@dataclass(frozen=True)
class DispatchEnergy:
    """Annual energy accounting from dispatch — the forward seam for charge-volume costs.

    The current `OptimisationResult` schema does not carry per-year charge/discharge volumes
    (that is M3 dispatch's output). Until it does, levies-on-charge are *unavailable*. When a
    caller (or a future dispatch result) supplies this, the levy line activates.
    ``annual_charge_kwh`` is grid energy imported to charge (already includes round-trip losses
    by construction); if only ``annual_discharge_kwh`` is known it is grossed up by the
    round-trip-efficiency assumption.

    ``windowed_levy_avoidance_gbp`` is the £/yr the dispatch avoided on the site's **time-windowed**
    levy lines (in GB the Capacity Market supplier obligation, charged on 16:00–19:00 working-day
    November–February consumption only). It arrives as a £ rather than a volume because it is a sum
    over half-hours — the levy's own window calendar × the solved charge/discharge — and an ANNUAL
    net-charge volume cannot reconstruct it: it says nothing about which half-hours the flow fell
    in. Signed (normally positive, a credit; negative if the optimum charged inside the window) and
    ``None`` when the site declares no windowed levy, so the cost line is *unavailable* rather than
    a modelled £0.
    """

    annual_charge_kwh: float | None = None
    annual_discharge_kwh: float | None = None
    windowed_levy_avoidance_gbp: float | None = None


@dataclass(frozen=True)
class CapexEstimate:
    """The installed-capex estimate, traceable to the capex assumption(s).

    Two shapes (C025): the legacy all-in ``£/MW × MW`` (releases without an energy key —
    byte-identical behaviour), and the decomposed ``power component £/MW × MW + £/kWh × kWh``
    from gb_v1_3 on, which gives added duration a real marginal capital cost so a wide-duration
    sizing search stops pinning to the band edge. ``per_mw_gbp`` is always the EFFECTIVE all-in
    per MW (total ÷ MW, pre-multiplier) so the plausibility band check reads the same either
    way; the decomposition fields are ``None`` on the legacy shape.
    """

    total_gbp: float
    per_mw_gbp: float
    power_mw: float
    assumption_ref: str
    per_kwh_gbp: float | None = None
    power_component_per_mw_gbp: float | None = None
    energy_kwh: float | None = None
    #: The SIZE-INVARIANT one-off, pre-multiplier (2026-08-19). ``0.0`` on any release that does not
    #: publish ``battery_capex.dno_assessment_and_design_one_off_gbp_per_site``, so every such
    #: release stays byte-identical.
    fixed_gbp: float = 0.0


@dataclass(frozen=True)
class CashflowModel:
    """The deterministic year-by-year project cashflows for one sized system.

    Year 0 is capex (a negative free cashflow); years 1..N are operating. Monetary fields are
    rounded to whole pence. Revenue/cost are stated on the basis year (year-1 nameplate) and
    extended across the horizon (degradation on revenue; costs held flat — see docs).
    """

    horizon_years: int
    power_mw: float
    duration_hours: float | None
    capex: CapexEstimate
    # Year-1 basis figures. Per-stream revenue is the engine's own £ (RAW — it matches the
    # record's stream table everywhere); the perfect-foresight adjustment is a NAMED deduction,
    # never a silent shrink of a stream figure.
    annual_revenue_by_stream_gbp: dict[str, float]
    # W8 (north_star): the labelled interim deduction for the perfect-foresight premium on
    # dispatch-optimised revenue (year-1 £; decays with revenue). ``None`` = not applied (no
    # dispatch-optimised revenue, or the haircut key is unusable — warned as an upper bound).
    perfect_foresight_adjustment_gbp: float | None
    # W8 firmness split (raw year-1 £; firm + contracted == the stream total): the two classes
    # carry different P90 risk shapes, and the coming debt basis reads contracted separately.
    annual_revenue_firm_gbp: float
    annual_revenue_contracted_gbp: float
    # W8/N2: the non-delivery penalty line in the P90 series (year-1 £, post-multiplier and
    # post-bound; decays with revenue). It is the D30 annual cap scaled by the SAME delivery
    # shortfall the contracted P90 haircut applies — one event, one shortfall — and the combined
    # (forfeited revenue + penalty) deduction is bounded at the contracted revenue, so a
    # contracted stream can never make P90 EBITDA worse than not holding the contract at all.
    # ``None`` = un-modelled (no contracted revenue, or the D30 'penalties' section is
    # empty/unusable — a warned DATA-ASK, never zero).
    p90_penalty_exposure_gbp: float | None
    annual_dispatch_cost_gbp: float
    annual_cost_by_line_gbp: dict[str, float | None]
    # Multi-year series (length = horizon_years + 1; index 0 = year-0 capex).
    p50_fcf_pre_debt_gbp: list[float]
    p90_fcf_pre_debt_gbp: list[float]
    p50_ebitda_gbp: list[float]
    p90_ebitda_gbp: list[float]
    # W8: the CONTRACTED-basis P90 series (years 1..N) for lender-style debt measurement —
    # deliberately CONSERVATIVE: contracted P90 revenue minus the penalty line minus ALL
    # operating+dispatch costs (no cost allocation between bases is invented; the contracted
    # basis bears every cost). Negative on merchant-dominant runs by construction.
    p90_contracted_ebitda_gbp: list[float]
    # Provenance / status.
    assumption_version: str
    assumptions_used: list[str]
    uses_unconfirmed_assumptions: bool
    bankable: bool
    warnings: list[str]
    # --- declared bases (C-7: N1, N13/H-15, N14) — labels, never numbers -----
    # These carry the disclosure with the object that carries the series, so the series cannot
    # reach `assess_debt`, an IRR or a report without its basis attached. Defaulted so every
    # construction is labelled by default and a caller cannot forget.
    #: N1 — the tax basis of `p50/p90_fcf_pre_debt_gbp`, `p50/p90_ebitda_gbp` and any DSCR
    #: measured on them. There is no tax line anywhere in the tree; the DSCR covenant it is
    #: tested against is a post-tax lender covenant.
    cfads_basis: str = PRE_TAX_CFADS_BASIS
    #: N13/H-15 — the escalation basis of every revenue and cost line in the series.
    cashflow_basis: str = FLAT_NOMINAL_UNESCALATED_BASIS
    #: N14 — the capital flows the FCF series does NOT contain, named individually.
    omitted_capital_flows: tuple[str, ...] = OMITTED_CAPITAL_FLOWS
    #: H-17 — the fleet availability factor actually APPLIED to revenue (%). ``None`` = no
    #: availability de-rate was modelled (100% uptime assumed for the whole horizon) because no
    #: release carries a sourced figure — a warned data-ask, never a silently invented %.
    availability_factor_pct: float | None = None
    #: D-33/D-130 — the contract tenors actually applied, as ``(stream, years)`` pairs. Empty =
    #: no tenor evidence was supplied, so every contracted stream is projected to the full
    #: horizon (the pre-D-130 behaviour, warned).
    contracted_tenor_years: tuple[tuple[str, float], ...] = ()
    #: H-17, cost side — the year-1 VARIABLE cost the availability de-rate avoids (£, positive),
    #: i.e. ``(1 − availability_factor) × (dispatch charge cost + the levy line on net charge)``.
    #: It is a NAMED addition to the series in the same convention every other adjustment uses:
    #: ``annual_dispatch_cost_gbp`` and ``annual_cost_by_line_gbp`` stay the RAW year-1 figures the
    #: dispatch reported, so the cost table still reconciles to the engine and the price
    #: recommendation (which reads ``total_annual_operating_cost_gbp``) is untouched. ``None`` = no
    #: de-rate was applied at all; ``0.0`` = a de-rate applied but there was no variable cost to
    #: avoid (no dispatch cost and no levy line on this run).
    availability_avoided_variable_cost_gbp: float | None = None
    # --- WP-UW-17 (D128..D132) — all defaulted, so a legacy construction is unchanged ------
    #: The modelled delivered volume per year, aligned with `p50_fcf_pre_debt_gbp` (index 0 = 0.0,
    #: year k = discharge x (1 - fade)^(k-1) x availability). ``None`` = discharge unknown. The
    #: price-to-target levelises over this series, never over flat year-1 volume (N12).
    delivered_energy_kwh_by_year: list[float] | None = None
    #: Every cashflow line and the money it is stated in, as ``(line, CashflowLineBasis value)``.
    line_basis: tuple[tuple[str, str], ...] = ()
    #: The state of `financing.cpi_pct_per_year`: ``absent`` / ``null`` / ``numeric``.
    cpi_state: str = "absent"
    cpi_pct_per_year: float | None = None
    #: ``E_t = (1 + c)^(t-1)`` per operating year when the escalation is active, else ``None``.
    escalation_by_year: tuple[float, ...] | None = None
    #: The P50 pre-debt series deflated to year-1 money (``nominal_t / E_t``; year 0 undeflated).
    real_p50_fcf_gbp: list[float] | None = None
    real_project_irr_pct: float | None = None
    #: How a tenored CM payment was indexed (``None`` = not indexed on this run).
    cm_indexation: CmIndexation | None = None
    #: The lifecycle capital-flow ledger rows (empty = no lifecycle flow stated, legacy N14 path).
    lifecycle_flow_entries: tuple[LifecycleFlowEntry, ...] = ()
    #: The lifecycle capital flows in each FCF series (years 1..N), and the part that reduces
    #: CFADS (decommissioning + replacements; the terminal value never does). ``None`` = none.
    lifecycle_capital_p50_by_year: list[float] | None = None
    lifecycle_capital_p90_by_year: list[float] | None = None
    lifecycle_cfads_by_year: list[float] | None = None
    #: The COD delay this series was built under (a scenario row; 0 = planned COD).
    cod_delay_months: int = 0

    @property
    def total_annual_revenue_gbp(self) -> float:
        """Year-1 total priced revenue (raw stream sum; excludes gated upside) — BEFORE the
        named perfect-foresight adjustment AND before the H-17 fleet-availability de-rate, both
        of which the EBITDA/FCF series already net off.

        So this figure does NOT reconcile arithmetically to ``p50_ebitda_gbp[0] + costs`` whenever
        either adjustment is live; that is the module's established convention (the per-stream £
        stays the engine's own number so it matches the record's stream table everywhere, and every
        adjustment is a NAMED, separately reported deduction rather than a silent shrink). Read
        ``perfect_foresight_adjustment_gbp`` and ``availability_factor_pct`` alongside it.
        """
        return round(sum(self.annual_revenue_by_stream_gbp.values()), 2)

    @property
    def total_annual_operating_cost_gbp(self) -> float:
        """Year-1 total of the *available* operating COST lines (excludes capex).

        Excludes the signed lines in `SIGNED_COST_LINE_KEYS` — today only
        ``windowed_levies_avoided``, an avoided cost that is normally a CREDIT. Two reasons, and the
        first is not cosmetic:

        1. **A credit is not an operating cost.** `financial_model` reads this property as
           ``annual_opex`` and hands it to `recommend_price`, which **refuses a negative opex**
           (``pricing.py``: "annual opex must be non-negative"). A large enough credit therefore
           made the price recommendation raise rather than return a number — a real crash on a real
           site, not a theoretical one.
        2. The EBITDA series does **not** read this property (it sums the raw ``cost_lines`` dict in
           `build_cashflow_model`), so excluding the credit here changes no cashflow, no DSCR and no
           IRR. It changes only what is *labelled* "operating cost".

        **Known conservatism, stated rather than hidden.** The priced-floor streams are netted into
        the price-to-target through ``other_annual_revenue_gbp`` (N3), and this credit is the same
        kind of quantity — a customer cost the project removes — so for consistency it belongs there
        too. Adding it needs `financial_model.py`, which this change does not own. Until then the
        price-to-target ignores the credit entirely, which OVERSTATES the price the project needs,
        i.e. it errs against the project. See `total_annual_avoided_cost_gbp`.
        """
        return round(
            sum(
                v
                for k, v in self.annual_cost_by_line_gbp.items()
                if v is not None and k not in SIGNED_COST_LINE_KEYS
            ),
            2,
        )

    @property
    def total_annual_avoided_cost_gbp(self) -> float:
        """Year-1 total of the signed AVOIDED-cost lines, as a positive £ where they are credits.

        Today: the windowed-levy (GB Capacity Market supplier obligation) credit. Reported
        separately from `total_annual_operating_cost_gbp` so the two are never confused, and so the
        figure the price-to-target does not yet net (see above) is visible rather than lost.
        """
        return round(
            -sum(
                v
                for k, v in self.annual_cost_by_line_gbp.items()
                if v is not None and k in SIGNED_COST_LINE_KEYS
            ),
            2,
        )


# --- assumption access helpers --------------------------------------------


def _placeholder_warning(section: str, name: str, a: Assumption) -> str:
    return (
        f"Assumption '{section}.{name}' is '{a.status.value}' (confidence "
        f"{a.confidence.value}); output is not bankable until it is confirmed."
    )


def _use(
    assumptions: AssumptionLibrary,
    section: str,
    name: str,
    *,
    warnings: list[str],
    used: list[str],
) -> Assumption:
    """Fetch an assumption, recording its use and warning if it is not confirmed.

    Raises ``KeyError`` if the assumption is genuinely absent — callers that tolerate absence
    use :func:`_maybe` instead.
    """
    a = assumptions.get(section, name)
    ref = f"{section}.{name}"
    if ref not in used:
        used.append(ref)
    if a.status != AssumptionStatus.CONFIRMED:
        w = _placeholder_warning(section, name, a)
        if w not in warnings:
            warnings.append(w)
    return a


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    """Return an assumption if both the section and key exist, else ``None`` (no raise)."""
    sect = getattr(assumptions, section, None)
    if not isinstance(sect, dict):
        return None
    return sect.get(name)


def _as_float(value: object, *, where: str) -> float:
    """Coerce an assumption value to float, with a clear error if it is not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"assumption {where} must be a number, got {value!r}")
    return float(value)


def _pos_number(value: object) -> float | None:
    """Return ``value`` as a positive float, or ``None`` if it is not one (bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if f > 0 else None


# --- capex -----------------------------------------------------------------

#: Where the installed-capex plausibility band lives in the library (£/MW low/high).
CAPEX_BENCHMARK_KEY = ("battery_capex", "installed_cost_benchmark_band")


def check_capex_plausibility(per_mw_gbp: float, assumptions: AssumptionLibrary) -> list[str]:
    """Sanity-check an installed capex (£/MW) against the library's benchmark band.

    The System Brief requires installer capex to be *sanity-checked against a benchmark library*.
    Returns a single warning when the figure falls outside
    ``battery_capex.installed_cost_benchmark_band`` (``{low, high}`` in £/MW); empty otherwise.
    This never blocks and never alters the figure — a benchmark band is a plausibility guard, not
    a price input. If no band is configured (or it is malformed) the check is skipped rather than
    guessing one.
    """
    band = _maybe(assumptions, *CAPEX_BENCHMARK_KEY)
    if band is None or not isinstance(band.value, dict):
        return []
    low = _pos_number(band.value.get("low"))
    high = _pos_number(band.value.get("high"))
    if low is None or high is None:
        return []  # malformed band — skip, never guess
    if per_mw_gbp < low or per_mw_gbp > high:
        return [
            f"installed capex £{per_mw_gbp:,.0f}/MW is outside the benchmark band "
            f"[£{low:,.0f}, £{high:,.0f}]/MW ({band.status.value}) — review the installer "
            "quote / assumption. Not blocking; no figure changed."
        ]
    return []


#: The size-invariant one-off capex key. Named once because both capex shapes read it and a second
#: literal copy would let one shape silently stop charging it.
_FIXED_CAPEX_KEY = "dno_assessment_and_design_one_off_gbp_per_site"


def _fixed_capex_gbp(
    assumptions: AssumptionLibrary, *, warnings: list[str], used: list[str]
) -> float:
    """The SIZE-INVARIANT one-off capex, or ``0.0`` where the release publishes none.

    **WHY THIS EXISTS, and the library says it verbatim (2026-08-19).** Capex was purely linear in
    kW and kWh - ``power_component x MW + per_kwh x kWh`` - with no fixed component at all, and
    `battery_capex.dno_assessment_and_design_one_off_gbp_per_site` self-documented the omission:
    "REFERENCE ONLY - NOT CONSUMED BY ANY CODE PATH TODAY, AND THE OMISSION FLATTERS EVERY RETURN
    ... DIRECTION OF THE OMISSION: capex is UNDERSTATED by GBP 1,557 per site". Combined with a
    revenue stack of which 83.9 pct is invariant to battery size, a purely linear cost function
    makes the sizing search walk to the bottom-left corner of its own grid: on the campaign's
    Harwell run it selected the SMALLEST candidate that exists (10.691 kW at 0.5 h) and reported a
    170.45 pct project IRR on GBP 3,554.76 of capex, i.e. GBP 332.50/kW installed for a sub-11 kW
    behind-the-meter battery against the same library's GBP 618/kW grid-scale 2-hour benchmark.

    The library also states the mechanism the linear form denies (`battery_capex` notes, citing
    DESNZ/Carbon Trust EINAS 2025 and Reonic): "fixed costs like the grid connection and controls do
    not grow with capacity, so they weigh far more on a small system", so "a sub-MW BTM system costs
    MORE per kW than grid scale". Charging the one sourced per-site one-off this library holds is
    the arithmetic minimum that respects that; it is NOT the whole fixed cost (see the direction
    note below).

    DIRECTION OF EFFECT: capex UP, returns DOWN, and hardest at the small end - +43.8 pct of capex
    on a 10.691 kW / 0.5 h candidate, +2.0 pct on a 320.7 kW / 2 h one. STILL UNDERSTATED: install
    labour, commissioning and any minimum job cost have no sourced figure in this library and none
    is invented here, so the fixed term remains a floor rather than an estimate.
    """
    entry = _maybe(assumptions, "battery_capex", _FIXED_CAPEX_KEY)
    if entry is None:
        return 0.0
    entry = _use(assumptions, "battery_capex", _FIXED_CAPEX_KEY, warnings=warnings, used=used)
    fixed = _as_float(entry.value, where=f"battery_capex.{_FIXED_CAPEX_KEY}")
    if fixed < 0:
        raise ValueError(
            f"battery_capex.{_FIXED_CAPEX_KEY} is negative ({fixed}); capex cannot be negative."
        )
    return fixed


def estimate_capex(
    power_mw: float,
    assumptions: AssumptionLibrary,
    *,
    warnings: list[str],
    used: list[str],
    cost_multiplier: float = 1.0,
    energy_kwh: float | None = None,
) -> CapexEstimate:
    """Estimate installed capex from the ``battery_capex`` assumption(s).

    Legacy shape: capex = ``installed_cost`` (£/MW) × power (MW) × ``cost_multiplier``.
    Decomposed shape (C025, from gb_v1_3): when ``installed_cost_gbp_per_kwh`` exists AND the
    caller supplies ``energy_kwh``, capex = (``installed_cost_gbp_per_mw_power_component`` ×
    MW + £/kWh × kWh) × multiplier — added duration now carries real marginal capital cost,
    which is what makes a wide-duration sizing recommendation meaningful. A release WITHOUT
    the energy key is byte-identical to before; a release WITH it but a caller without energy
    falls back to the all-in per-MW figure with a warning (never silently mispriced).

    Rejects negative inputs at every step — capex is never allowed to be negative (it is not
    a documented signed-quantity exception like connection headroom).
    """
    if power_mw < 0:
        raise InsufficientInputsError(f"power_mw must be non-negative, got {power_mw}")
    if cost_multiplier < 0:
        raise ValueError(f"scenario cost_multiplier must be non-negative, got {cost_multiplier}")

    per_kwh_a = _maybe(assumptions, "battery_capex", "installed_cost_gbp_per_kwh")
    power_comp_a = _maybe(assumptions, "battery_capex", "installed_cost_gbp_per_mw_power_component")
    if per_kwh_a is not None and power_comp_a is not None and energy_kwh is not None:
        if energy_kwh < 0:
            raise InsufficientInputsError(f"energy_kwh must be non-negative, got {energy_kwh}")
        # Re-read via _use so the keys are recorded and non-confirmed statuses warn as usual.
        per_kwh_a = _use(
            assumptions,
            "battery_capex",
            "installed_cost_gbp_per_kwh",
            warnings=warnings,
            used=used,
        )
        power_comp_a = _use(
            assumptions,
            "battery_capex",
            "installed_cost_gbp_per_mw_power_component",
            warnings=warnings,
            used=used,
        )
        per_kwh = _as_float(per_kwh_a.value, where="battery_capex.installed_cost_gbp_per_kwh")
        power_comp = _as_float(
            power_comp_a.value, where="battery_capex.installed_cost_gbp_per_mw_power_component"
        )
        if per_kwh < 0 or power_comp < 0:
            raise ValueError(
                "battery_capex decomposition components cannot be negative "
                f"(£/kWh={per_kwh}, power £/MW={power_comp})."
            )
        fixed = _fixed_capex_gbp(assumptions, warnings=warnings, used=used)
        base_total = power_comp * power_mw + per_kwh * energy_kwh + fixed
        effective_per_mw = base_total / power_mw if power_mw > 0 else power_comp
        # Sanity-check the EFFECTIVE unit capex against the £/MW benchmark band (flags only).
        warnings.extend(check_capex_plausibility(effective_per_mw, assumptions))
        total = round(base_total * cost_multiplier, 2)
        if total < 0:  # defensive — unreachable given the guards above
            raise ValueError(f"estimated capex is negative ({total}); refusing to price.")
        return CapexEstimate(
            total_gbp=total,
            per_mw_gbp=round(effective_per_mw, 2),
            power_mw=power_mw,
            assumption_ref=per_kwh_a.source,
            per_kwh_gbp=per_kwh,
            power_component_per_mw_gbp=power_comp,
            energy_kwh=energy_kwh,
            fixed_gbp=fixed,
        )
    if per_kwh_a is not None and energy_kwh is None:
        warnings.append(
            "battery_capex carries an energy (£/kWh) component but the result supplied no "
            "selected_energy_kwh — capex fell back to the all-in per-MW figure (duration is "
            "unpriced in this estimate; supply the sized energy for the decomposed shape)."
        )

    a = _use(assumptions, "battery_capex", "installed_cost", warnings=warnings, used=used)
    per_mw = _as_float(a.value, where="battery_capex.installed_cost")
    if per_mw < 0:
        raise ValueError(
            f"battery_capex.installed_cost is negative ({per_mw}); capex cannot be negative."
        )
    # The fixed term lands on BOTH shapes: a fixed cost that appears only on the decomposed shape
    # would make the same site's capex depend on which release priced it. ``0.0`` on any release
    # without the key, which is every release the legacy shape was written for, so those stay
    # byte-identical.
    fixed = _fixed_capex_gbp(assumptions, warnings=warnings, used=used)
    base_total = per_mw * power_mw + fixed
    effective_per_mw = base_total / power_mw if power_mw > 0 else per_mw
    # Sanity-check the EFFECTIVE unit capex against the library's benchmark band (flags only; never
    # blocks) - the same quantity the decomposed shape checks, and the same quantity this estimate
    # reports, so a figure outside the band can never be published without its warning.
    warnings.extend(check_capex_plausibility(effective_per_mw, assumptions))
    total = round(base_total * cost_multiplier, 2)
    if total < 0:  # defensive — unreachable given the guards above
        raise ValueError(f"estimated capex is negative ({total}); refusing to price.")
    return CapexEstimate(
        total_gbp=total,
        per_mw_gbp=round(effective_per_mw, 2),
        power_mw=power_mw,
        assumption_ref=a.source,
        fixed_gbp=fixed,
    )


# --- cost lines ------------------------------------------------------------


def _levy_rate_gbp_per_kwh(site: SiteModel) -> float:
    """Sum the site's tariff levy lines (£/kWh). These are *site data*, not an assumption."""
    return round(sum(site.tariff_stack.levies.values()), 6)


def blended_import_rate_gbp_per_kwh(site: SiteModel) -> float | None:
    """The site's blended delivered import cost (£/kWh): mean import rate + every £/kWh levy line.

    THE one blended-import-rate calculation (N26). Two callers priced imported grid energy two
    different ways before this existed: the price counterfactual in `financial_model` added the
    levy lines, the parasitic-load cost line here did not — while a comment claimed the two used
    "the same convention". Auxiliary draw is imported grid energy and attracts levies exactly as
    any other import does, which is the same reasoning the levies-on-charge line rests on, so the
    omission understated the parasitic cost by ``levy_rate × aux_kwh`` every year of the horizon.

    A disclosed blended-rate approximation over the tariff's own transcribed rates: unweighted by
    usage (the bill decomposition owns weighting) and built ONLY from the site's own numbers, never
    an external price. Returns ``None`` when the site carries no import rates, so the caller
    reports the line as unavailable rather than guessing one. Rounded to 6 dp — £/kWh site data,
    the same precision convention as :func:`_levy_rate_gbp_per_kwh`.

    **Excludes ``windowed_levies``, deliberately and conservatively.** A time-windowed levy has no
    single £/kWh: the auxiliary draw is continuous, so pricing it correctly needs the share of the
    8,760 h that falls inside the window, which is a calendar fact this unweighted helper does not
    have. Excluding it UNDERSTATES the parasitic cost (by the windowed rate × the auxiliary kWh
    drawn in-window — of the order of £40/yr at a 30 p/kWh Capacity Market rate on a 2.8%-of-hours
    window and a 200 kW unit), so it flatters the case by a small amount rather than inventing a
    time-weighting. Stated in the caller's warning; the fix is to price the auxiliary draw off the
    half-hourly series, which belongs with the dispatch, not here.
    """
    stack = site.tariff_stack
    rates = stack.import_rates if stack else {}
    if not rates:
        return None
    levies = sum((stack.levies or {}).values())
    return round(sum(rates.values()) / len(rates) + levies, 6)


def build_cost_lines(
    site: SiteModel,
    power_mw: float,
    capex_gbp: float,
    assumptions: AssumptionLibrary,
    *,
    dispatch_energy: DispatchEnergy | None,
    warnings: list[str],
    used: list[str],
) -> dict[str, float | None]:
    """Build the year-1 operating cost lines, each from a named assumption.

    A line is ``None`` (explicitly *unavailable*, with a warning) when its driving input is
    missing — never silently zero and never invented.
    """
    lines: dict[str, float | None] = {k: None for k in COST_LINE_KEYS}

    opex = _use(assumptions, "battery_opex", "annual_fixed_opex", warnings=warnings, used=used)
    lines["opex"] = round(
        _as_float(opex.value, where="battery_opex.annual_fixed_opex") * power_mw, 2
    )

    ins = _use(assumptions, "insurance", "annual_pct_of_capex", warnings=warnings, used=used)
    lines["insurance"] = round(
        _as_float(ins.value, where="insurance.annual_pct_of_capex") / 100.0 * capex_gbp, 2
    )

    metering = _use(
        assumptions, "metering_idno", "annual_gbp_per_site", warnings=warnings, used=used
    )
    lines["metering_idno"] = round(
        _as_float(metering.value, where="metering_idno.annual_gbp_per_site"), 2
    )

    dno = _use(assumptions, "dno_costs", "annual_gbp_per_site", warnings=warnings, used=used)
    lines["dno"] = round(_as_float(dno.value, where="dno_costs.annual_gbp_per_site"), 2)

    rates = _use(assumptions, "business_rates", "annual_gbp_per_mw", warnings=warnings, used=used)
    lines["business_rates"] = round(
        _as_float(rates.value, where="business_rates.annual_gbp_per_mw") * power_mw, 2
    )

    # Degradation (D36 — one physical fade, one price): the annual capacity fade is charged
    # ONCE, as the revenue decay the P50/P90 series already apply, with the warranty throughput
    # cap as the hard cycling limit. The former ``degradation_warranty_proxy`` (fade% × capex, a
    # replacement-reserve provision) and ``degradation_marginal_cost`` (£/MWh wear) lines were
    # REMOVED as double-counts: under the model's no-augmentation convention (day-one sizing
    # holds end-of-life capability; no replacement spend ever occurs in-model) a provision line
    # on top of the revenue decay charges the same fade twice, and the library's fade key is
    # explicitly a calendar+cycle proxy, so a separate per-MWh cycle-wear charge triple-counted
    # cycling. The library keys remain as unconsumed homes pending M7's calibrated SoH model.

    # Levies on the NET import increase (D36): on the no-export floor a discharge displaces
    # levied site import, so the battery's true levy cost is levy × (charge − discharge) — the
    # round-trip losses — exactly the symmetric convention the dispatch objective and the
    # customer bill use (net import = load + charge − discharge). Charging levies on GROSS
    # charge over-stated the cost by levy × discharge. Volumes are dispatch outputs; without
    # them this line is unavailable — not assumed.
    #
    # F-03 reconciliation (2026-08-10 review): the MILP objective now prices charging at
    # ``energy + DUoS + levy`` so the optimiser can SEE this leakage when it decides whether a
    # marginal cycle pays. That is not a double count and this line must NOT be deleted: the
    # reported value streams are built from the schedule's separate energy/DUoS arrays
    # (``arbitrage_value_gbp`` + ``duos_avoidance_value_gbp``), so no levy £ ever enters revenue.
    # This line remains the ONE place the levy is charged, on the same net-import convention the
    # objective's levy term is the exact negative of.
    levy_rate = _levy_rate_gbp_per_kwh(site)
    charge_kwh = _resolve_charge_kwh(dispatch_energy, assumptions, warnings=warnings, used=used)
    discharge_kwh = _resolve_discharge_kwh(
        dispatch_energy, assumptions, warnings=warnings, used=used
    )
    if charge_kwh is None or discharge_kwh is None:
        warnings.append(
            "Cost line 'levies_on_charge' is unavailable: dispatch charge/discharge volumes are "
            "not yet provided (M3 dispatch output). It is omitted, not estimated."
        )
    elif levy_rate <= 0:
        warnings.append(
            "Cost line 'levies_on_charge' is 0: the site's tariff_stack carries no levy lines "
            "(£/kWh). Confirm the site's levy stack before underwriting."
        )
        lines["levies_on_charge"] = 0.0
    else:
        net_charge_kwh = max(0.0, charge_kwh - discharge_kwh)
        lines["levies_on_charge"] = round(levy_rate * net_charge_kwh, 2)

    # TIME-WINDOWED levies (the GB Capacity Market supplier obligation): the same net-import
    # convention, but the rate is time-varying, so the £ must be summed over half-hours and cannot
    # be recovered from the annual volumes above. The dispatch computes it and it arrives on the
    # `DispatchEnergy` seam; see `HalfHourlyDispatchSchedule.windowed_levy_avoidance_gbp`.
    #
    # SIGN. The dispatch reports it as AVOIDED COST (positive = the site's bill fell), so it is
    # negated into a cost line: a credit is a negative cost. That makes this the first line in
    # `COST_LINE_KEYS` that can be negative, which is why `SIGNED_COST_LINE_KEYS` exists and why it
    # is NOT folded into `levies_on_charge` — that line means "the flat levy on the round-trip
    # losses" and stays positive.
    #
    # NO DOUBLE COUNT, on the same reconciliation as F-03. The windowed rate enters the MILP
    # objective (`milp_solver`, the ``+ windowed_levy`` term in ``price``) as a DECISION price so
    # the optimiser discharges into the window; it is deliberately absent from the reported value
    # streams, which are built from the schedule's separate energy/DUoS arrays, and absent from
    # ``priced_floor_terms_gbp``. This line is therefore the ONE place the windowed levy's £ is
    # booked, and it is the exact negative of the objective's windowed term.
    windowed_avoided = getattr(dispatch_energy, "windowed_levy_avoidance_gbp", None)
    if windowed_avoided is None:
        if site.tariff_stack.windowed_levies:
            warnings.append(
                "Cost line 'windowed_levies_avoided' is unavailable: the site declares windowed "
                f"levy line(s) {sorted(site.tariff_stack.windowed_levies)} but the dispatch did "
                "not "
                "report the £ avoided on them (no DispatchEnergy.windowed_levy_avoidance_gbp). It "
                "is omitted, not estimated — so this run UNDERSTATES the avoided cost, and the "
                "MILP objective still priced the levy, meaning the dispatch and the P&L disagree. "
                "Route the run through MilpDispatchSolver."
            )
    else:
        lines["windowed_levies_avoided"] = round(-float(windowed_avoided), 2)
        windowed_rates = {
            k: v.rate_gbp_per_kwh for k, v in sorted(site.tariff_stack.windowed_levies.items())
        }
        warnings.append(
            f"Cost line 'windowed_levies_avoided' = £{lines['windowed_levies_avoided']}/yr — the "
            f"site's TIME-WINDOWED levy line(s) {windowed_rates} (£/kWh, charged only inside their "
            "own window). A negative figure is a CREDIT: the levy is charged on in-window import, "
            "the "
            "dispatch discharges in the window and charges outside it, so in-window import falls. "
            "It is bounded by the site's own METERED in-window import (the MILP's behind-the-meter "
            "bound is discharge <= min(power, load), so net import never goes below zero and no "
            "export credit is assumed) — never a rated-power extrapolation. Charged ONCE here; the "
            "same rate is in the dispatch objective as a decision price only."
        )

    # Parasitic (auxiliary) load cost: RELEASE-GATED like the degradation line. Annual auxiliary
    # energy = fraction x rated power x 8760 h (the draw is continuous), priced at the site's
    # blended DELIVERED import cost — mean import rate + every £/kWh levy line — a disclosed
    # blended-rate approximation using only the site's numbers, never an external price. No import
    # rates ⇒ the line is unavailable with a warning — omitted, not estimated. The dispatch side
    # (connection headroom reduction) reads the same key in the MILP.
    #
    # N26 (2026-08-10 review): the levies used to be omitted here while the price counterfactual
    # in `financial_model` included them, behind a comment asserting the two shared a convention.
    # Auxiliary draw is imported grid energy: it attracts levies exactly as any other import does,
    # and the MILP already treats it as real continuous grid import consuming connection headroom
    # (`milp_solver.py` charge upper bound). Nothing else in the tree charges levies on it — the
    # levies_on_charge line above uses only charge − discharge from `DispatchEnergy`, which does
    # not include the parasitic draw — so omitting them here dropped the cost entirely. Both sites
    # now call ONE helper, :func:`blended_import_rate_gbp_per_kwh`.
    if _maybe(assumptions, *PARASITIC_LOAD_KEY) is not None:
        blended_rate = blended_import_rate_gbp_per_kwh(site)
        if blended_rate is None:
            warnings.append(
                "Cost line 'parasitic_load' is unavailable: the site has no import tariff rates "
                "to price the auxiliary draw against. It is omitted, not estimated."
            )
        else:
            pf = _use(assumptions, *PARASITIC_LOAD_KEY, warnings=warnings, used=used)
            fraction = _as_float(pf.value, where=".".join(PARASITIC_LOAD_KEY))
            if fraction < 0:
                raise ValueError(
                    f"{'.'.join(PARASITIC_LOAD_KEY)} is negative ({fraction}); an auxiliary "
                    "draw cannot be negative (that would flatter the case)."
                )
            aux_kwh = fraction * (power_mw * 1000.0) * _HOURS_PER_YEAR
            lines["parasitic_load"] = round(blended_rate * aux_kwh, 2)
            warnings.append(
                f"Cost line 'parasitic_load' prices the continuous auxiliary draw ({fraction} x "
                f"rated power = {round(aux_kwh, 2)} kWh/yr) at the site's blended DELIVERED import "
                f"cost £{blended_rate}/kWh — the MEAN of its import rates PLUS its £/kWh levy "
                f"lines (£{levy_rate}/kWh), since auxiliary draw is levied grid import like any "
                "other (N26). A blended-rate approximation, disclosed not exact; the dispatch "
                "separately reserves the same draw against the connection headroom."
                + (
                    ""
                    if not site.tariff_stack.windowed_levies
                    else " The site's TIME-WINDOWED levy line(s) "
                    f"{sorted(site.tariff_stack.windowed_levies)} are EXCLUDED from this blended "
                    "rate: a windowed levy has no single £/kWh and this helper is unweighted by "
                    "hour. The auxiliary draw is continuous, so it does pay the windowed levy on "
                    "the in-window fraction of its hours — that cost is UNDERSTATED here, which "
                    "flatters the case slightly rather than inventing a time-weighting."
                )
            )

    return lines


def _resolve_charge_kwh(
    dispatch_energy: DispatchEnergy | None,
    assumptions: AssumptionLibrary,
    *,
    warnings: list[str],
    used: list[str],
) -> float | None:
    """Resolve the annual charge volume (kWh) for the levy line, or ``None`` if unknown."""
    if dispatch_energy is None:
        return None
    if dispatch_energy.annual_charge_kwh is not None:
        if dispatch_energy.annual_charge_kwh < 0:
            raise ValueError("DispatchEnergy.annual_charge_kwh must be non-negative.")
        return float(dispatch_energy.annual_charge_kwh)
    if dispatch_energy.annual_discharge_kwh is not None:
        if dispatch_energy.annual_discharge_kwh < 0:
            raise ValueError("DispatchEnergy.annual_discharge_kwh must be non-negative.")
        rte = _use(
            assumptions, "round_trip_efficiency", "ac_round_trip", warnings=warnings, used=used
        )
        rte_val = _as_float(rte.value, where="round_trip_efficiency.ac_round_trip")
        if not 0 < rte_val <= 1:
            raise ValueError(
                f"round_trip_efficiency.ac_round_trip must be in (0, 1]; got {rte_val}."
            )
        warnings.append(
            "Levy charge volume derived from discharge ÷ round-trip efficiency (no metered "
            "charge volume supplied)."
        )
        return round(dispatch_energy.annual_discharge_kwh / rte_val, 2)
    return None


def _resolve_discharge_kwh(
    dispatch_energy: DispatchEnergy | None,
    assumptions: AssumptionLibrary,
    *,
    warnings: list[str],
    used: list[str],
) -> float | None:
    """Resolve the annual discharge volume (kWh), or ``None`` if unknown (mirror of charge).

    When only the charge volume is known, discharge = charge × round-trip efficiency (with a
    warning) — the exact inverse of the levy line's gross-up. Never assumed when neither volume
    is supplied.
    """
    if dispatch_energy is None:
        return None
    if dispatch_energy.annual_discharge_kwh is not None:
        if dispatch_energy.annual_discharge_kwh < 0:
            raise ValueError("DispatchEnergy.annual_discharge_kwh must be non-negative.")
        return float(dispatch_energy.annual_discharge_kwh)
    if dispatch_energy.annual_charge_kwh is not None:
        if dispatch_energy.annual_charge_kwh < 0:
            raise ValueError("DispatchEnergy.annual_charge_kwh must be non-negative.")
        rte = _use(
            assumptions, "round_trip_efficiency", "ac_round_trip", warnings=warnings, used=used
        )
        rte_val = _as_float(rte.value, where="round_trip_efficiency.ac_round_trip")
        if not 0 < rte_val <= 1:
            raise ValueError(
                f"round_trip_efficiency.ac_round_trip must be in (0, 1]; got {rte_val}."
            )
        warnings.append(
            "Degradation-cost discharge volume derived from charge × round-trip efficiency (no "
            "metered discharge volume supplied)."
        )
        return round(dispatch_energy.annual_charge_kwh * rte_val, 2)
    return None


# --- revenue ---------------------------------------------------------------


# Firmness levels that count toward the priced floor. The value-stack and the data contract
# define the floor as firmness ∈ {FIRM, CONTRACTED}; CONDITIONAL streams and gated upside (which
# the value-stack emits as at most CONDITIONAL) are upside, held out of the floor — never the
# priced case. Counting CONDITIONAL would over-state both revenue and dispatch cost the moment a
# real engine emits valued conditional streams. See OPEN_COMMENTS C014.
# The set itself now lives at `schemas.market.PRICED_FLOOR_FIRMNESS` — one definition, two readers.
def _is_priced(v: ValueStreamResult) -> bool:
    """A value stream is in the priced floor iff its firmness is FIRM or CONTRACTED.

    CONDITIONAL streams (and gated upside, which is at most CONDITIONAL) are upside, not the
    priced case, so they are excluded — as are explicitly priced-out (EXCLUDED) streams.

    Delegates to `schemas.market.is_priced_floor` rather than holding its own firmness set: the
    sizing grid now stamps per-candidate floor membership too (`SizingGridStreamRevenue`), and two
    copies of the set would be two definitions of the priced floor that could silently diverge —
    leaving the grid's floor breakdown and this module's floor total describing different streams.
    Behaviour is byte-identical; the set moved, it did not change."""
    return is_priced_floor(v)


def revenue_by_stream(result: ResultLike, *, warnings: list[str]) -> dict[str, float]:
    """Year-1 priced-floor revenue per stream, read straight from the result's value streams.

    Only floor streams — firmness ∈ {firm, contracted} — are summed. Conditional streams,
    priced-out (excluded) streams, and gated upside (the result's separate ``gated_upside`` list)
    are all held out of the floor. No revenue is invented here — zeros stay zeros.
    """
    out: dict[str, float] = {}
    for v in result.value_streams:
        if not _is_priced(v):
            continue
        out[v.stream.value] = round(out.get(v.stream.value, 0.0) + v.annual_revenue_gbp, 2)
    if not out or sum(out.values()) == 0:
        warnings.append(
            "No priced revenue in the optimisation result (all value streams are 0 or excluded). "
            "The financial case is a cost-only, not-bankable view until a real dispatch prices "
            "revenue."
        )
    return out


# The floor streams whose £ is a price×schedule integral under FULL FORESIGHT of the metered
# year (the MILP's own objective terms) — the premium applies to them and only them:
# availability/capacity payments (CM £/kW, flex availability) carry no foresight advantage.
_PERFECT_FORESIGHT_STREAMS: frozenset[str] = frozenset(
    {MarketStream.TARIFF_ARBITRAGE.value, MarketStream.DUOS_RED_BAND_AVOIDANCE.value}
)


def _perfect_foresight_adjustment(
    rev_by_stream: dict[str, float],
    assumptions: AssumptionLibrary,
    *,
    warnings: list[str],
    used: list[str],
) -> float | None:
    """The labelled interim deduction for the perfect-foresight premium (W8, north_star).

    The dispatch LP prices tariff arbitrage + DUoS avoidance with full knowledge of every
    half-hour of the metered year; a causal operator cannot achieve that optimum, so raw P50
    would be a systematic upper bound. Until the M7 backtest measures the true achievable/LP
    ratio per stream, the library's ``p50_p90_haircuts.perfect_foresight_haircut_pct`` is
    applied to those streams' year-1 revenue — as a NAMED deduction, loudly labelled. A
    missing/unusable key returns ``None`` with an explicit upper-bound warning (honest degrade
    — never a silent flatter, never an invented percentage).
    """
    affected = round(sum(v for k, v in rev_by_stream.items() if k in _PERFECT_FORESIGHT_STREAMS), 2)
    if affected <= 0:
        return None  # nothing dispatch-optimised is priced — no premium to adjust
    a = _maybe(assumptions, "p50_p90_haircuts", "perfect_foresight_haircut_pct")
    pct = None
    if a is not None and isinstance(a.value, (int, float)) and not isinstance(a.value, bool):
        pct = float(a.value)
    if a is None or pct is None or not 0 <= pct < 100:
        warnings.append(
            "P50 dispatch revenue is the RAW perfect-foresight LP optimum — an upper bound no "
            "causal operator achieves: p50_p90_haircuts.perfect_foresight_haircut_pct is "
            "missing or unusable and the M7 calibration has not run. Treat P50 as optimistic."
        )
        return None
    key = "p50_p90_haircuts.perfect_foresight_haircut_pct"
    if key not in used:
        used.append(key)
    warnings.append(
        f"Perfect-foresight adjustment applied: dispatch-optimised P50 revenue "
        f"({', '.join(sorted(k for k in rev_by_stream if k in _PERFECT_FORESIGHT_STREAMS))}) "
        f"reduced by {pct:g}% (£{round(affected * pct / 100.0, 2)} in year 1; "
        f"{key}, status '{a.status.value}') — an INTERIM, uncalibrated adjustment; the M7 "
        "backtest replaces it with a measured per-stream achievable/LP ratio."
    )
    return round(affected * pct / 100.0, 2)


def dispatch_cost_total(result: ResultLike) -> float:
    """Year-1 dispatch cost total (charge energy / losses / parasitic) over priced-floor streams.

    Mirrors :func:`revenue_by_stream`: only firmness ∈ {firm, contracted} counts, so a conditional
    stream's cost is held out of the floor exactly as its revenue is."""
    return round(sum(v.annual_cost_gbp for v in result.value_streams if _is_priced(v)), 2)


def revenue_by_firmness(result: ResultLike) -> tuple[float, float]:
    """Year-1 priced-floor revenue split by firmness class: ``(FIRM, CONTRACTED)``.

    The two classes carry different P90 risk shapes (W8, north_star W3): FIRM dispatch revenue
    is merchant-shaped (price/foresight risk), CONTRACTED availability revenue's downside is
    penalty/availability shortfall. Mirrors :func:`revenue_by_stream` exactly, so
    firm + contracted == the priced stream total.
    """
    firm = sum(v.annual_revenue_gbp for v in result.value_streams if v.firmness == Firmness.FIRM)
    contracted = sum(
        v.annual_revenue_gbp for v in result.value_streams if v.firmness == Firmness.CONTRACTED
    )
    return round(firm, 2), round(contracted, 2)


def contracted_revenue_by_stream(result: ResultLike) -> dict[str, float]:
    """Year-1 CONTRACTED-firmness revenue per stream — the per-contract view D-130 needs.

    :func:`revenue_by_firmness` gives the class total, which is enough for a haircut but not for a
    tenor: a 15-year CM agreement and a 1-year terminable local-flex award are both CONTRACTED and
    expire in different years. Sums to the ``contracted`` element of :func:`revenue_by_firmness`.
    """
    out: dict[str, float] = {}
    for v in result.value_streams:
        if v.firmness != Firmness.CONTRACTED:
            continue
        out[v.stream.value] = round(out.get(v.stream.value, 0.0) + v.annual_revenue_gbp, 2)
    return out


def _availability_factor(
    assumptions: AssumptionLibrary,
    *,
    warnings: list[str],
    used: list[str],
) -> tuple[float, float | None]:
    """The fleet availability factor applied to revenue (H-17): ``(factor, pct)``.

    Every revenue line in this module used to be earned at 100% availability for the whole
    horizon — no availability term existed anywhere in ``finance/``. Real GB BESS fleet uptime is
    below that, and on a contracted availability stream an outage does not merely forgo the
    payment, it also triggers the penalty/clawback regime, so unavailability hits contracted lines
    TWICE exactly as the service terms do (the caller folds the same shortfall into the penalty).

    This is NOT degradation: degradation shrinks the capability that is available, unavailability
    stops the capability earning at all. They multiply, and both are applied.

    Returns ``(1.0, None)`` — no de-rate, with a loud data-ask warning — when the release carries
    no usable figure (gb_v0_1 … gb_v1_6). A percentage is never invented here: it must be sourced
    into ``docs/bess_constants_register.md`` and then into an assumption release. Reading the key
    records it in ``used``, so a ``needs_review`` figure also blocks bankability through the normal
    gate.

    Only ``fleet_availability_pct`` is read. There is deliberately NO fallback to the neighbouring
    ``expected_availability_pct``: per that key's own library notes it is the site-specific
    firm-commitment factor and stays an empty home per D30 — a different quantity on a different
    basis. Silently substituting it would be the same class of error this finding is about.
    """
    section, name = AVAILABILITY_FACTOR_KEY
    a = _maybe(assumptions, section, name)
    pct = None if a is None else _pos_number(a.value)
    if a is not None and pct is not None and pct <= 100:
        _use(assumptions, section, name, warnings=warnings, used=used)
        warnings.append(
            f"Revenue is de-rated for fleet availability: {section}.{name} = {pct:g}% (status "
            f"'{a.status.value}') multiplies EVERY revenue line in both the P50 and the P90 series "
            "(H-17 — availability is an expected-value effect, not a downside scenario), and the "
            "same unavailability is folded into the contracted-stream penalty exposure, so it hits "
            "a contracted line twice as the service terms do. Distinct from degradation, which is "
            "applied on top: fade shrinks the MWh deliverable, unavailability removes the hours in "
            "which anything is deliverable. The cost side is now SPLIT rather than held whole: "
            "FIXED lines (opex, insurance, business rates, metering/DNO, and the continuous "
            "auxiliary draw, which a house load keeps taking while the unit is down) are NOT "
            "reduced, but the VARIABLE cost of cycling IS — the dispatch charge-energy cost and "
            "the levy line on net charge volume, because energy the battery does not cycle is "
            "energy it does not buy, and the MILP priced those volumes at 100% availability. "
            "Charging 100% of a volumetric cost against de-rated revenue was over-conservative "
            "in a direction nothing justified: an over-conservative constraint is still a wrong "
            "number. This is an ANNUAL SCALAR: it is not the frequency-response availability "
            "factor F, where "
            "seconds of unavailability zero a whole settlement period, and it carries no outage "
            "TIMING, so it cannot represent an outage landing on a CM Satisfactory Performance Day."
        )
        return pct / 100.0, pct
    warnings.append(
        "NO availability de-rate is modelled: every revenue line is earned at 100% availability "
        f"for the whole horizon (H-17). {section}.{name} is absent or unusable on this release, so "
        "no de-rate is applied and none is invented. Real GB fleet uptime is below 100%, and on a "
        "contracted availability stream an outage also triggers the penalty/clawback regime — so "
        "BOTH the revenue and the penalty line are optimistic on this axis. Source a published GB "
        "fleet availability % into the constants register and the next assumption release."
    )
    other_section, other_name = _NOT_THE_FLEET_AVAILABILITY_KEY
    other = _maybe(assumptions, other_section, other_name)
    if other is not None and _pos_number(other.value) is not None:
        warnings.append(
            f"{other_section}.{other_name} carries a value but is NOT used as the fleet "
            f"availability de-rate: it is the site-specific firm-commitment factor, a different "
            f"quantity on a different basis. The de-rate stays OFF until {section}.{name} is "
            "populated — this is stated so the omission cannot pass as if it had been applied."
        )
    return 1.0, None


def _resolve_contract_tenors(
    requested: Mapping[str, float] | None,
    contracted_by_stream: Mapping[str, float],
    *,
    warnings: list[str],
) -> dict[str, float]:
    """Validate the supplied per-stream contract tenors against the contracted revenue (D-130).

    Only a stream that actually carries CONTRACTED revenue this run can have its revenue tenored,
    and only a positive finite tenor is usable. Anything else is dropped with a warning rather
    than coerced — a mis-keyed tenor that silently did nothing would be worse than none at all.
    Keys may be `MarketStream` members or their string values.
    """
    out: dict[str, float] = {}
    if not requested:
        return out
    for raw_key, raw_tenor in requested.items():
        key = str(getattr(raw_key, "value", raw_key))
        if isinstance(raw_tenor, bool) or not isinstance(raw_tenor, (int, float)):
            warnings.append(
                f"Contract tenor for {key!r} is not a number ({raw_tenor!r}) — ignored; that "
                "stream is projected to the full horizon (D-130 exposure stands)."
            )
            continue
        tenor = float(raw_tenor)
        if not tenor > 0 or tenor != tenor or tenor in (float("inf"), float("-inf")):
            warnings.append(
                f"Contract tenor for {key!r} is not a positive finite number of years ({tenor}) — "
                "ignored; that stream is projected to the full horizon (D-130 exposure stands)."
            )
            continue
        if contracted_by_stream.get(key, 0.0) <= 0:
            warnings.append(
                f"Contract tenor supplied for {key!r} ({tenor:g}y) but that stream prices no "
                "CONTRACTED revenue this run — the tenor is recorded as unused, never applied to "
                "another stream's revenue."
            )
            continue
        out[key] = tenor
    return out


# --- contract tenor EVIDENCE (D-33 / D-130: the call path, not the arithmetic) --------------
#
# The arithmetic below has always worked; until 2026-08-13 nothing called it. `contract_tenor_years`
# had ZERO production callers — `run_financial_model` passed no tenor — so on every real run the
# defect behaviour persisted: a 1-2 year terminable award earned for the full 10-25 year horizon in
# P90 and in the lender basis, and a fixed CM availability payment was shrunk by battery capacity
# fade. That is the FOURTH instance of the same pattern in this repo (Gate 3 reconciliation, the
# F-19 local-flex call rate, the availability-reservation compiler): a fix is only as good as its
# narrowest call path. These helpers are that call path — they turn the contract records the model
# already receives into the mapping the projection has always been able to consume.
#
# Every tenor here comes from an AWARD, never from market practice and never from the assumption
# library: the register's "offtake contract tenors: merchant-only 1-3 years, tolls 4-7, floors up
# to 15" row is a market-practice range, not this site's contract, and substituting it would be
# exactly the invented number D-130's warning refuses to make.


def tenors_from_commitments(
    commitments: Iterable[AvailabilityCommitment] | None,
) -> tuple[dict[str, float], list[str]]:
    """Per-stream contract tenors read off `AvailabilityCommitment.tenor_years` (D-130).

    ``tenor_years`` on a commitment is the contract length from day one — the same field the W7-3
    end-of-tenor deliverability check reads, so the sizing gate and the cashflow projection now
    agree on how long the award runs instead of only one of them knowing.

    Two or more commitments on the SAME stream with different lengths collapse to the **minimum**,
    with a warning: the tenor is applied to that stream's whole revenue line, so the only length
    every part of it is contracted for is the shortest. Never averaged, never the longest. A
    commitment carrying no ``tenor_years`` is skipped with a warning (it is not evidence of a term).
    """
    out: dict[str, float] = {}
    warnings: list[str] = []
    if not commitments:
        return out, warnings
    seen: dict[str, list[tuple[str, float]]] = {}
    for c in commitments:
        stream = str(getattr(c.stream, "value", c.stream))
        tenor = c.tenor_years
        if tenor is None:
            warnings.append(
                f"Commitment {c.commitment_id!r} on {stream} carries no tenor_years, so it "
                "supplies NO contract length (D-130): that stream's revenue is projected to the "
                "full horizon unless another record tenors it. A commitment without a term is not "
                "evidence of one."
            )
            continue
        seen.setdefault(stream, []).append((c.commitment_id, float(tenor)))
    for stream, entries in sorted(seen.items()):
        shortest = min(entries, key=lambda e: (e[1], e[0]))
        out[stream] = shortest[1]
        if len({t for _, t in entries} | {shortest[1]}) > 1:
            listed = ", ".join(f"{cid}={t:g}y" for cid, t in sorted(entries))
            warnings.append(
                f"{stream} carries commitments of differing length ({listed}); the SHORTEST "
                f"({shortest[1]:g}y, {shortest[0]!r}) is applied to the whole stream, because the "
                "tenor applies to that stream's entire revenue line and the shortest term is the "
                "only one every part of it is contracted for. Bounded, never averaged; split the "
                "revenue by contract to price the longer terms separately."
            )
    return out, warnings


def tenors_from_provisioning_requirements(site: SiteModel) -> tuple[dict[str, float], list[str]]:
    """The LOAD_SERVICE contract tenor from the site's own declared obligations (D-130).

    `SiteModel.provisioning_requirements[].tenor_years` is the obligation length from day one, and a
    customer provisioning contract is exactly the "from the award itself" evidence class D-130 asks
    for. This is the ONE tenor path that is live from site data alone: it needs no caller to pass
    anything, so a site whose JSON declares a term gets the term applied.

    Only requirements that actually PRICE revenue count — `LoadServiceStream` prices the declared
    counterfactual and nothing else, so a requirement without one contributes no revenue to tenor.
    If ANY revenue-bearing requirement lacks a tenor the stream is left UN-tenored and warned:
    applying one requirement's term to the stream total would cut revenue that record says nothing
    about. Differing terms collapse to the minimum, warned, for the same reason as commitments.
    """
    warnings: list[str] = []
    requirements = site.provisioning_requirements or []
    priced = [r for r in requirements if r.declared_counterfactual_gbp_per_year is not None]
    if not priced:
        return {}, warnings
    missing = sorted(r.requirement_id for r in priced if r.tenor_years is None)
    if missing:
        warnings.append(
            f"Load-service requirement(s) {', '.join(missing)} price a declared counterfactual but "
            "carry no tenor_years, so LOAD_SERVICE is left UN-tenored (D-130) and projected to the "
            "full horizon. A term is not applied from the requirements that do have one: that "
            "would cut revenue those records say nothing about. Add tenor_years from the customer "
            "provisioning contract."
        )
        return {}, warnings
    tenors = sorted((float(r.tenor_years), r.requirement_id) for r in priced if r.tenor_years)
    shortest = tenors[0]
    if len({t for t, _ in tenors}) > 1:
        listed = ", ".join(f"{rid}={t:g}y" for t, rid in tenors)
        warnings.append(
            f"Load-service requirements carry differing terms ({listed}); the SHORTEST "
            f"({shortest[0]:g}y, {shortest[1]}) is applied to the whole LOAD_SERVICE line, because "
            "the tenor applies to that stream's entire revenue and the shortest term is the only "
            "one every part of it is contracted for. Bounded, never averaged."
        )
    return {MarketStream.LOAD_SERVICE.value: shortest[0]}, warnings


def resolve_contract_tenor_evidence(
    site: SiteModel,
    *,
    commitments: Iterable[AvailabilityCommitment] | None = None,
    explicit: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Assemble the per-stream contract tenors from every evidence source, explicit winning.

    Precedence, most specific first: an ``explicit`` mapping the caller passed (the CM agreement
    term, a DSO contract read off a record this module cannot see) → a typed
    `AvailabilityCommitment` payload → the site's own provisioning requirements. An explicit tenor
    that overrides derived evidence is warned, so a silent override cannot hide a contradiction
    between two records.

    Returns ``({}, [...])`` when no source carries a term: nothing is guessed, and
    :func:`_warn_contract_tenor_basis` then states D-33 and D-130 as the live exposures they are.
    """
    warnings: list[str] = []
    derived: dict[str, float] = {}
    #: Which source each derived tenor came from, so an override warning can NAME it. Attributing a
    #: commitment-payload tenor to "the site's own records" would be a wrong provenance claim in a
    #: warning a lender reads — the same defect class as a misdescribed disclosure.
    source_of: dict[str, str] = {}
    for label, (source_tenors, source_warnings) in (
        ("the site's own provisioning requirements", tenors_from_provisioning_requirements(site)),
        ("a typed AvailabilityCommitment payload", tenors_from_commitments(commitments)),
    ):
        warnings.extend(source_warnings)
        derived.update(source_tenors)
        source_of.update(dict.fromkeys(source_tenors, label))
    out = dict(derived)
    for raw_key, tenor in (explicit or {}).items():
        key = str(getattr(raw_key, "value", raw_key))
        if key in derived and derived[key] != tenor:
            warnings.append(
                f"Contract tenor for {key} supplied explicitly as {tenor!r}, overriding the "
                f"{derived[key]:g}y read from {source_of[key]}. The explicit value is used; "
                "reconcile the two records — one of them is wrong about the term."
            )
        out[key] = tenor  # validated (and dropped if unusable) by `_resolve_contract_tenors`
    return out, warnings


def _in_term_fraction(tenor_years: float, year: int) -> float:
    """The fraction of operating ``year`` (1-based) that falls inside a ``tenor_years`` contract.

    1.0 while the award is live for the whole year, 0.0 once it has expired, and the pro-rata
    fraction in the single boundary year — deterministic arithmetic on the supplied tenor, not an
    invented mid-year convention. A 1.5-year award earns a full year 1 and half of year 2.
    """
    return max(0.0, min(1.0, tenor_years - (year - 1)))


def _warn_contract_tenor_basis(
    contracted_by_stream: Mapping[str, float],
    tenors: Mapping[str, float],
    horizon_years: int,
    *,
    warnings: list[str],
    indexation_text: Mapping[str, str] | None = None,
) -> None:
    """Disclose the tenor basis of every CONTRACTED stream (D-33 + D-130), whichever way it lands.

    ``indexation_text`` (WP-UW-17) replaces the "NO CPI/RPI indexation" sentence for a stream that
    IS indexed on this run; ``None`` keeps every legacy sentence byte-identical.

    A tenored stream gets a warning naming the term and what changes at expiry. An un-tenored one
    gets the two findings stated as live exposures, because that is what they are: the revenue is
    projected to the horizon on no contract evidence (D-130, over-stating), and a fixed
    availability payment is being shrunk by battery capacity fade it is not exposed to (D-33,
    under-stating in term). Neither is quietly fixed by guessing a term.
    """
    if not contracted_by_stream:
        return
    for stream, tenor in sorted(tenors.items()):
        expiry = (
            "it never expires inside the projection"
            if tenor >= horizon_years
            else (
                "beyond that the revenue is priced as MERCHANT-class re-contracting (faded, "
                "merchant P90 haircut, excluded from the lender-basis contracted series)"
                + (
                    " and removed from P90 entirely — "
                    + _NO_P90_REASON.get(stream, "no re-procurement guarantee exists for it")
                    if stream in _NO_P90_AFTER_EXPIRY
                    else ""
                )
            )
        )
        index_sentence = (indexation_text or {}).get(
            stream,
            "NO CPI/RPI "
            "indexation is applied to it (a CM T-4 agreement escalates with CPI — see the "
            "flat-nominal basis warning; the index is a Tier B human input, never invented here).",
        )
        warnings.append(
            f"Contracted stream {stream} is tenored at {tenor:g} year(s) over a "
            f"{horizon_years}-year horizon: it is held FLAT on the awarded de-rated MW for the "
            f"term (capacity fade does not shrink a fixed payment — D-33) and {expiry}. "
            + index_sentence
        )
    untenored = sorted(k for k, v in contracted_by_stream.items() if v > 0 and k not in tenors)
    if not untenored:
        return
    warnings.append(
        "CONTRACTED revenue is projected to the FULL "
        f"{horizon_years}-year horizon with no contract tenor evidence for "
        f"{', '.join(untenored)} (D-130): a local-flex/DSO award is typically 1–2 years and "
        "terminable, and a CM agreement runs its awarded term, so multi-year DSCR and debt "
        "capacity built on this are OVER-stated. Supply `contract_tenor_years` from the award "
        "itself (an AvailabilityCommitment.tenor_years, the CM agreement term, the DSO contract) "
        "and the revenue ends at the term. No tenor is assumed here."
    )
    warnings.append(
        f"The battery's capacity fade is applied to the un-tenored contracted revenue "
        f"({', '.join(untenored)}) as well as to dispatch revenue (D-33): a FIXED availability "
        "payment is owed on the awarded de-rated MW and is not exposed to cell fade, so this "
        "UNDER-states it in term. The correct in-term flat treatment needs the contract length — "
        "supply `contract_tenor_years` and it is applied; nothing is guessed in its absence."
    )


def _declared_basis_warnings(
    horizon_years: int,
    contracted_by_stream: Mapping[str, float],
    *,
    indexation_warning: str | None = None,
    lifecycle_warnings: list[str] | None = None,
) -> list[str]:
    """The three declared-basis disclosures the pack never stated (C-7: N1, N13/H-15, N14).

    Each states an omission that is real, unquantified and Tier B — the substantive fix needs a
    human input (an SPV tax basis, an escalation index, a residual-value basis) that is not
    derivable from code or from GB market mechanics. None of them changes a computed number; all
    three exist so no reader can mistake what the numbers are. They are emitted on EVERY model, so
    a case cannot be read, or a DSCR quoted, without them.
    """
    out = [
        f"BASIS — {PRE_TAX_CFADS_BASIS} (N1). No tax line, tax rate, capital allowance or "
        "taxable-profit computation exists anywhere in the model: the same pre-tax EBITDA series "
        "is used as CFADS for the DSCR, as the project-IRR series and as the equity-IRR series, "
        "the DSCR covenant it is tested against is a lender's POST-tax covenant. Coverage is "
        "therefore overstated by an unquantified, BACK-LOADED amount — capital allowances push the "
        "charge into the late years, which are also the binding DSCR years. Adding the tax layer "
        "needs the SPV structure, the capital-allowance basis, WDA timing and loss carry-forward "
        "treatment: a human input, deliberately not invented here."
    ]
    cpi_note = (
        " Contracted lines make this worse in both directions: a CM T-4 agreement is CPI-linked "
        "and is modelled flat, while a merchant arbitrage spread is not indexed at all."
        if contracted_by_stream
        else ""
    )
    if indexation_warning is not None:
        out.append(indexation_warning)
    else:
        out.append(_flat_basis_warning(horizon_years, cpi_note))
    if lifecycle_warnings is not None:
        # WP-UW-17 (D131): each flow is modelled or excluded by its own labelled line.
        out.extend(lifecycle_warnings)
        return out
    out.append(
        f"OMITTED from the free-cashflow series across the {horizon_years}-year horizon (N14): "
        + "; ".join(OMITTED_CAPITAL_FLOWS)
        + ". The only capital flow in either series is the year-0 capex, and there is no funded "
        "reserve for any of the above. Two of the omissions flatter the case and one penalises it, "
        "so the net direction on the headline IRRs and on the late-life DSCR is unquantified — not "
        "small, unquantified. The residual-value basis (resale, re-contract, second life) and the "
        "decommissioning/restoration obligation in the host-site agreements are human inputs; "
        "neither a terminal value nor a replacement cost is invented here."
    )
    return out


def _flat_basis_warning(horizon_years: int, cpi_note: str) -> str:
    """The legacy N13/H-15 disclosure, byte-identical, for a run with no escalation."""
    return (
        f"BASIS — {FLAT_NOMINAL_UNESCALATED_BASIS} (N13/H-15). Revenue moves only with the "
        "degradation fade and every operating cost line is held constant for the whole "
        f"{horizon_years}-year horizon, while the debt rate, the equity hurdle and the discount "
        "rate applied to those cashflows are all nominal. The two errors do not cancel: "
        "flat-nominal opex understates cost by roughly 41% by year 15 at 2.5% CPI — in the year "
        "that binds the DSCR — while flat-nominal revenue understates revenue. The result is "
        f"neither a real nor a nominal number.{cpi_note} Choosing the index, the rate and which "
        "lines are indexed is a human input; no escalation rate is invented here."
    )


def _contracted_haircut_pct(
    assumptions: AssumptionLibrary,
    merchant_pct: float,
    contracted_revenue: float,
    *,
    warnings: list[str],
    used: list[str],
) -> float:
    """The CONTRACTED-firmness P90 haircut (W8): its own key when present, else the merchant
    default with a loud shares-the-merchant-haircut warning. Never an invented percentage."""
    if contracted_revenue <= 0:
        return merchant_pct  # nothing contracted priced — the distinction is moot this run
    a = _maybe(assumptions, "p50_p90_haircuts", "p90_revenue_haircut_contracted_pct")
    pct: float | None = None
    if a is not None and isinstance(a.value, (int, float)) and not isinstance(a.value, bool):
        pct = float(a.value)
    if a is None or pct is None or not 0 <= pct < 100:
        warnings.append(
            "Contracted revenue shares the merchant P90 haircut: "
            "p50_p90_haircuts.p90_revenue_haircut_contracted_pct is absent/unusable — the "
            "firmness-differentiated haircut lands with the next assumption release; an "
            "availability payment's downside is penalty-shaped, not price-shaped."
        )
        return merchant_pct
    key = "p50_p90_haircuts.p90_revenue_haircut_contracted_pct"
    if key not in used:
        used.append(key)
    if a.status != AssumptionStatus.CONFIRMED:
        w = _placeholder_warning("p50_p90_haircuts", "p90_revenue_haircut_contracted_pct", a)
        if w not in warnings:
            warnings.append(w)
    return pct


def _stream_contracted_haircut_pct(stream: str, contracted_pct: float) -> float:
    """The P90 haircut for ONE contracted stream, in term (the per-stream seam).

    ``_contracted_haircut_pct`` resolves a single CLASS-wide percentage; this resolves the one that
    actually applies to a named stream. The two are not the same question. A Capacity Market
    availability payment is a regulated fee with statutory penalties and a multi-year agreement; a
    DNO flexibility award is terminable on 90 days' notice by either party with no cause (ENA
    Standard Agreement §7.4, D-130) and is priced here as an availability fee plus utilisation
    revenue. Applying one scalar to both is conservative for the first and optimistic for the
    second, and the second is the one D-130/D-139 single out as least reliable.

    Only membership of :data:`_NO_P90_IN_TERM` differentiates today, and deliberately so: that
    membership is a requirements-mandated exclusion, whereas a per-stream *percentage* would need a
    calibration no GB authority publishes (the search is recorded on
    ``p50_p90_haircuts.p90_revenue_haircut_contracted_pct`` in ``assumptions/gb_v1_9.yaml`` and it
    FAILED). No percentage is invented here: every other stream keeps the class-wide value the
    library resolved, and the gap for those streams stays a declared ask on that key.
    """
    return 100.0 if stream in _NO_P90_IN_TERM else contracted_pct


def _warn_in_term_p90_zeroed(
    in_term_year_1_gbp: Mapping[str, float],
    contracted_haircut: float,
    *,
    warnings: list[str],
) -> None:
    """Disclose, per stream, every in-term contracted line whose P90 contribution is zero.

    Emitted only when such a stream is tenored AND earns in-term revenue, so a run without one
    carries no new text. Each stream states its OWN authority (:data:`_NO_P90_IN_TERM_REASON`) and
    the direction of the effect in £, because a treatment that lowers a P90 and a DSCR without
    saying so is the same defect class as one that raises them.

    **No requirement id appears in the shared prefix**, deliberately: D-139/D-130 are DNO-
    flexibility requirements and citing them at a DFS line would be a wrong authority in a pack a
    lender reads. Every citation is carried inside the stream's own reason.
    """
    for stream, gbp in sorted(in_term_year_1_gbp.items()):
        if gbp <= 0:
            continue
        removed = round(gbp * (1.0 - contracted_haircut / 100.0), 2)
        reason = _NO_P90_IN_TERM_REASON.get(stream, "no cautious-case basis exists for it")
        warnings.append(
            f"Contracted stream {stream} earns £{round(gbp, 2)} of IN-TERM revenue in year 1 and "
            "its P90 contribution is ZERO for the whole award term, not only after the award "
            f"expires: {reason}. "
            "It is therefore excluded from the P90 series and from the lender-basis contracted "
            "series in every year, so it can never carry the DSCR. DIRECTION: this LOWERS P90 "
            f"EBITDA and the minimum DSCR, by £{removed} in year 1 relative to the "
            f"{contracted_haircut:g}% class-wide contracted haircut this line used to take. P50 is "
            "UNCHANGED at its full value: the stream is not re-priced, only removed from the "
            "cautious case. Confirming the award's own delivery record is what would move it back."
        )


def _p90_penalty_exposure(
    result: ResultLike,
    assumptions: AssumptionLibrary,
    shortfall_fraction: float,
    *,
    warnings: list[str],
    used: list[str],
) -> float | None:
    """Year-1 non-delivery penalty line for the P90 downside (W8; D30 'penalties'), driven by
    the SAME shortfall the P90 revenue haircut applies.

    N2 (2026-08-10 review). This used to return the FULL annual cap (100% of CM revenue on the
    shipped library) and the caller subtracted it *on top of* the P90 revenue haircut on the same
    stream — two deductions for one event. The annual cap binds only if you failed to deliver, in
    which case you have already forfeited the availability revenue; summing them made a £50k/yr CM
    agreement worth −£7.5k/yr of P90 EBITDA, so of two identical sites the one that WON the
    contract priced £57.5k/yr worse and could fail a covenant the other passed.

    One shortfall drives both: ``shortfall_fraction`` is the contracted P90 haircut expressed as a
    delivery shortfall (P90 = you delivered ``1 − s`` of the obligation), the revenue loss is
    ``s × contracted revenue`` (applied by the caller as the haircut it already was), and the
    penalty is the annual cap scaled by the same ``s`` — zero at full delivery, the full cap only
    at total non-delivery. The caller then bounds the COMBINED deduction at the contracted revenue.

    Reads the optional ``penalties`` section: **empty = the release predates it** — when
    contracted revenue is priced, that absence is a loud DATA-ASK (the P90 downside is
    optimistic on this axis), never zero. Labelled as the shortfall-scaled bounded cap, not an
    expected value.
    """
    contracted = sum(
        v.annual_revenue_gbp for v in result.value_streams if v.firmness == Firmness.CONTRACTED
    )
    if contracted <= 0:
        return None  # no contracted commitment priced — no penalty exposure to model
    if not assumptions.penalties:
        warnings.append(
            "Contracted revenue carries UN-MODELLED non-delivery/penalty exposure: the "
            "'penalties' assumption section is empty (this release predates it — D30). Source "
            "the CM penalty regime (rate + monthly/annual caps) into the next release; until "
            "then the P90 downside excludes penalties and is optimistic on this axis."
        )
        return None
    a = assumptions.penalties.get("cm_annual_cap_pct_of_revenue")
    pct: float | None = None
    if a is not None and isinstance(a.value, (int, float)) and not isinstance(a.value, bool):
        pct = float(a.value)
    if a is None or pct is None or pct < 0:
        warnings.append(
            "'penalties' section is present but cm_annual_cap_pct_of_revenue is absent or "
            "unusable — penalty exposure stays un-modelled (a data-ask, never invented)."
        )
        return None
    cm_revenue = round(
        sum(
            v.annual_revenue_gbp
            for v in result.value_streams
            if _is_priced(v) and v.stream == MarketStream.CAPACITY_MARKET
        ),
        2,
    )
    if cm_revenue <= 0:
        return None
    key = "penalties.cm_annual_cap_pct_of_revenue"
    if key not in used:
        used.append(key)
    if a.status != AssumptionStatus.CONFIRMED:
        w = _placeholder_warning("penalties", "cm_annual_cap_pct_of_revenue", a)
        if w not in warnings:
            warnings.append(w)
    s = max(0.0, min(1.0, shortfall_fraction))
    exposure = round(cm_revenue * pct / 100.0 * s, 2)
    warnings.append(
        f"P90 includes a CM non-delivery penalty line of £{exposure} = the annual cap "
        f"(penalties.cm_annual_cap_pct_of_revenue {pct:g}% × CM revenue £{cm_revenue}) scaled by "
        f"the SAME {s * 100:g}% delivery shortfall the P90 contracted haircut applies (N2 — the "
        "penalty and the revenue loss are one event, never two deductions). The combined "
        "deduction is bounded at the contracted revenue: refine with SPD/EPT modelling later."
    )
    return exposure


# --- the cashflow model ----------------------------------------------------


def build_cashflow_model(
    site: SiteModel,
    result: ResultLike,
    assumptions: AssumptionLibrary,
    *,
    horizon_years: int | None = None,
    scenario: object | None = None,
    dispatch_energy: DispatchEnergy | None = None,
    contract_tenor_years: Mapping[str, float] | None = None,
    lifecycle: LifecycleInputs | None = None,
) -> CashflowModel:
    """Build the deterministic cashflow model for a sized system.

    Inputs:
      - ``site`` — the validated SiteModel (levy rates are read from its tariff stack).
      - ``result`` — an `OptimisationResult` or compatible object (carries sizing + value streams).
      - ``assumptions`` — the versioned `AssumptionLibrary` (the only source of £/%/ratio numbers).
      - ``horizon_years`` — projection horizon; defaults to ``DEFAULT_HORIZON_YEARS`` (+ a warning).
      - ``scenario`` — optional discrete scenario; ``price_multiplier`` scales revenue,
        ``cost_multiplier`` scales capex (read via duck-typing so finance stays decoupled).
      - ``dispatch_energy`` — optional dispatch energy accounting; activates the levy-on-charge.
      - ``contract_tenor_years`` — D-130: how long each CONTRACTED stream's award actually runs,
        keyed by `MarketStream` (or its value). Sourced from the award itself — an
        `AvailabilityCommitment.tenor_years`, a CM agreement term, a DSO contract. A stream with
        NO tenor supplied is projected to the full horizon exactly as before, with a warning
        naming that exposure: the correct in-term/post-term split is not computable without the
        contract length, and this module never invents one.
      - ``lifecycle`` — WP-UW-17: the stated terminal value, decommissioning and replacement flows
        (added to both FCF series; the CFADS part is reported separately) and the CM agreement
        that keys the indexation. ``None`` keeps the legacy N14 path byte-identical.

    WP-UW-17 escalation (D129): when the release states a NUMERIC
    `financing.cpi_pct_per_year`, the fixed cost lines escalate at it and a tenored T-4 CM payment
    is indexed per its Regulations; an absent key leaves every number bit-identical, and a null key
    leaves the arithmetic unchanged with one D45 warning. A scenario's ``cod_delay_months``
    consumes tenored contract months pro rata (D132).

    Raises `InsufficientInputsError` when there is no positive sizing to price.
    """
    warnings: list[str] = []
    used: list[str] = []

    power_kw = result.selected_power_kw
    if power_kw is None or power_kw <= 0:
        raise InsufficientInputsError(
            "Optimisation result has no positive selected_power_kw — cannot estimate capex or "
            "cashflows. Treat as a ranked data ask / not-bankable quote, not a priced result."
        )
    power_mw = power_kw / 1000.0

    if horizon_years is None:
        horizon_years = DEFAULT_HORIZON_YEARS
        warnings.append(
            f"Projection horizon defaulted to {DEFAULT_HORIZON_YEARS} years (a structural "
            "modelling parameter, not an underwriting assumption) — confirm the horizon."
        )
    if horizon_years <= 0:
        raise InsufficientInputsError(f"horizon_years must be positive, got {horizon_years}")

    price_multiplier = _scenario_multiplier(scenario, "price_multiplier", warnings=warnings)
    cost_multiplier = _scenario_multiplier(scenario, "cost_multiplier", warnings=warnings)

    capex = estimate_capex(
        power_mw,
        assumptions,
        warnings=warnings,
        used=used,
        cost_multiplier=cost_multiplier,
        energy_kwh=getattr(result, "selected_energy_kwh", None),
    )

    rev_by_stream = revenue_by_stream(result, warnings=warnings)
    # Firmness split (W8): FIRM dispatch revenue and CONTRACTED availability revenue carry
    # different P90 risk shapes; the perfect-foresight premium belongs to the FIRM side only.
    rev_firm, rev_contracted = revenue_by_firmness(result)
    # The perfect-foresight premium comes off as a NAMED deduction (per-stream £ stays raw and
    # matches the record's stream table); it scales with the same multiplier + decay as revenue.
    pf_adjustment_raw = _perfect_foresight_adjustment(
        rev_by_stream, assumptions, warnings=warnings, used=used
    )
    firm_base = (rev_firm - (pf_adjustment_raw or 0.0)) * price_multiplier
    # NOTE: the year-1 revenue aggregate that used to live here (`round(firm_base + contracted_base,
    # 2)`) is now built in the projection block below as `faded_p50_base`, over the UN-TENORED
    # contracted revenue only — the tenored part does not fade, so it cannot ride in a faded
    # aggregate (D-33). With no tenors supplied the two are the same float, by construction.
    pf_adjustment = (
        None if pf_adjustment_raw is None else round(pf_adjustment_raw * price_multiplier, 2)
    )
    dispatch_cost = dispatch_cost_total(result)

    cost_lines = build_cost_lines(
        site,
        power_mw,
        capex.total_gbp,
        assumptions,
        dispatch_energy=dispatch_energy,
        warnings=warnings,
        used=used,
    )
    operating_cost = sum(v for v in cost_lines.values() if v is not None)
    # The windowed-levy credit is pulled OUT of the flat operating-cost block and re-added per year
    # below, faded and availability-scaled, with the merchant haircut in P90.
    #
    # Why it cannot ride in the flat block: "costs held flat across the horizon" was documented as
    # CONSERVATIVE, and it was — while every cost line was positive. This is the model's first
    # NEGATIVE line, so held-flat flips direction and becomes optimistic. Worse, it would give an
    # avoided cost a better treatment than the revenue it substitutes for:
    # DUOS_RED_BAND_AVOIDANCE is the same quantity (a time-banded £/kWh avoided by the same
    # discharge) and it fades with capacity, scales with fleet availability and takes the P90
    # haircut. A credit that took none of the three would be worth more than an identical £ of
    # DUoS avoidance, which is not a fact about the site — it is an artefact of which side of
    # EBITDA the £ was written on.
    #
    # `x - 0.0 == x` in IEEE-754, so a site with no windowed levy keeps every figure bit-identical.
    windowed_credit_line = cost_lines.get("windowed_levies_avoided") or 0.0
    # ================================================================================
    # THE ONE DOUBLE-BOOK D57 DOES NOT RELAX: CM REVENUE vs THE CM PEAK LEVY AVOIDED.
    # ================================================================================
    # D57 (2026-08-18) opens every stream, and the ONLY rule it preserves is arithmetic integrity:
    # no pound may appear under two stream names, "nor in both a cost line and a revenue line".
    # This is that case, and it is the largest one in the model.
    #
    # The GBP 63.00/kW/yr T-4 2026/27 Capacity Market clearing price is BOTH:
    #   * the rate `CAPACITY_MARKET` earns for holding a de-rated capacity agreement (a REVENUE
    #     line), and
    #   * the reason the SUPPLIER's CM pass-through stepped to 0.30581 GBP/kWh in April 2026, which
    #     the battery avoids by discharging through the 16:00-19:00 working-day November-February
    #     window (`tariff_stack.windowed_levies.CM_peak`, a CREDIT on the COST side, booked in the
    #     `windowed_levies_avoided` line above).
    # They are the same auction's money flowing in opposite directions through the same capability.
    # `docs/bess_constants_register.md` line 683 carries the warning verbatim and measured the
    # overlap at roughly GBP 38/kW/yr on bishops_cleeve_mrf. Booking both is not optimism, it is an
    # accounting error, and it would corrupt the academic result on its own terms.
    #
    # THE FIX, and why it is netting rather than exclusion. Both streams stay ENABLED, which D57
    # requires: the CM revenue stream keeps its full figure and stays visible on `value_streams`,
    # and the levy stays in the MILP objective as a decision price (so the DISPATCH is unchanged -
    # discharging into the CM peak window is the right behaviour whichever way the money is booked).
    # What is netted is the REPORTED CREDIT: the overlap is removed from the avoided-cost side,
    # because the revenue side is the one the reader reads as the Capacity Market's value.
    #
    # DIRECTION OF EFFECT: DOWN, by the overlap, and that is correct. It is the only figure in this
    # change set that falls, and arithmetic integrity outranks "everything moves UP".
    cm_revenue = float(rev_by_stream.get(MarketStream.CAPACITY_MARKET.value, 0.0) or 0.0)
    if windowed_credit_line < 0.0 and cm_revenue > 0.0:
        # The credit is stored NEGATED (a credit is a negative cost), so the overlap is capped at
        # its magnitude: netting can zero the credit but can never turn it into a charge.
        overlap = min(cm_revenue, -windowed_credit_line)
        netted = round(windowed_credit_line + overlap, 2)
        warnings.append(
            "CM DOUBLE-BOOK NETTED (the one arithmetic rule D57 preserves): the "
            f"windowed CM-peak levy credit of GBP {-windowed_credit_line:,.2f}/yr and the "
            f"CAPACITY_MARKET revenue of GBP {cm_revenue:,.2f}/yr are THE SAME AUCTION'S MONEY "
            "flowing in opposite directions through the same capability - the GBP 63.00/kW/yr "
            "T-4 2026/27 clearing price is what the CMU earns AND what pushed the supplier's CM "
            "pass-through to 0.30581 GBP/kWh, which this dispatch avoids. Booking both counts one "
            f"pound twice. GBP {overlap:,.2f}/yr has therefore been REMOVED from the avoided-cost "
            f"credit, leaving GBP {-netted:,.2f}/yr, and the CAPACITY_MARKET revenue stream keeps "
            "its full figure - the revenue side is the one a reader reads as the Capacity Market's "
            "value. BOTH STREAMS REMAIN ENABLED (D57) and the MILP objective still prices the "
            "levy, so no dispatch decision changes; only the reported credit does. Source: "
            "docs/bess_constants_register.md line 683, which measured this overlap at roughly "
            "GBP 38/kW/yr on a real campaign site. DIRECTION OF EFFECT: DOWN by the overlap."
        )
        cost_lines["windowed_levies_avoided"] = netted
        windowed_credit_line = netted
        operating_cost = sum(v for v in cost_lines.values() if v is not None)
    operating_cost = operating_cost - windowed_credit_line

    # P50→P90 revenue haircuts (per firmness class — W8) + degradation decay on revenue
    # (costs held flat — documented). The merchant key remains the FIRM-class default.
    haircut_pct = _as_float(
        _use(
            assumptions, "p50_p90_haircuts", "p90_revenue_haircut_pct", warnings=warnings, used=used
        ).value,
        where="p50_p90_haircuts.p90_revenue_haircut_pct",
    )
    if haircut_pct < 0:
        # A negative haircut would put P90 ABOVE P50 — the cautious case beating the central one,
        # which violates the P90 ≤ P50 invariant. Clamp to zero and warn (the honest option:
        # visible, never a silently flattered downside case). Applies to the merchant key here;
        # the contracted key is range-validated inside `_contracted_haircut_pct`.
        warnings.append(
            f"Assumption 'p50_p90_haircuts.p90_revenue_haircut_pct' is negative "
            f"({haircut_pct}); clamped to 0 so P90 never exceeds P50 (the cautious case can "
            "never beat the central case). Review the assumption."
        )
        haircut_pct = 0.0
    contracted_haircut = _contracted_haircut_pct(
        assumptions, haircut_pct, rev_contracted, warnings=warnings, used=used
    )
    # H-17: fleet availability, applied to revenue in BOTH series (availability is an expected-value
    # effect, not a downside scenario) and folded into the contracted shortfall below so an outage
    # hits a contracted line twice — forgone payment AND penalty — exactly as the service terms do.
    availability_factor, availability_pct = _availability_factor(
        assumptions, warnings=warnings, used=used
    )
    # D-33 / D-130: which CONTRACTED streams have a known award length, and how much each earns.
    contracted_by_stream = contracted_revenue_by_stream(result)
    tenors = _resolve_contract_tenors(contract_tenor_years, contracted_by_stream, warnings=warnings)
    # --- WP-UW-17: the escalation key, the CM agreement and the COD delay ------------------
    # D129. `financing.cpi_pct_per_year` had no reader since gb_v1_8 (N13/H-15). It is read
    # here: numeric = the nominal, selectively escalated ledger; null = a stated gap (D45, one
    # warning, arithmetic unchanged); absent = a frozen release's flat basis, bit-identical.
    cpi = read_cpi(assumptions)
    escalation: tuple[float, ...] | None = None
    if cpi.state == "numeric":
        _use(assumptions, "financing", "cpi_pct_per_year", warnings=warnings, used=used)
        assert cpi.pct is not None
        escalation = escalation_factors(cpi.pct, horizon_years)
    elif cpi.state == "null":
        warnings.append(cpi_null_warning())
    indexed_cost_base = (
        sum(cost_lines[k] or 0.0 for k in INDEXED_COST_LINE_KEYS if k in cost_lines)
        if escalation is not None
        else 0.0
    )
    # D132 (missed point 1): a COD delay consumes contracted in-term months pro rata — an
    # award's delivery years are calendar-fixed, so the months before the late COD are lost.
    cod_delay = int(getattr(scenario, "cod_delay_months", 0) or 0) if scenario is not None else 0
    term_left = (
        {k: max(0.0, t - cod_delay / 12.0) for k, t in tenors.items()} if cod_delay else tenors
    )
    if cod_delay and tenors:
        warnings.append(
            f"COD DELAY vs CONTRACT TERM: the {cod_delay}-month delay consumes "
            f"{cod_delay / 12.0:g} year(s) of each tenored award ("
            + ", ".join(f"{k} {tenors[k]:g} -> {term_left[k]:g} yr" for k in sorted(tenors))
            + "): delivery years are calendar-fixed, so the remaining term is earned from the "
            "delayed COD. Termination or penalty risk from a late start is NOT modelled."
        )
    cm_stream_key = MarketStream.CAPACITY_MARKET.value
    cm_index: CmIndexation | None = None
    index_text: dict[str, str] | None = None
    if escalation is not None and cm_stream_key in tenors:
        cm_index = resolve_cm_indexation(
            assumptions,
            lifecycle.capacity_agreement if lifecycle is not None else None,
            cpi.pct or 0.0,
            warnings=warnings,
        )
        index_text = {
            cm_stream_key: (
                f"It is CPI-INDEXED in term per {CM_RULE_SOURCE}: year-1 uplift "
                f"{cm_index.year1_uplift:.4f} ({cm_index.uplift_source}), then the library CPI "
                f"of {cpi.pct:g}%/yr; re-contracting after expiry is flat-nominal."
                if cm_index.indexed
                else "It is held un-indexed: a T-1 agreement (see the T-1 warning)."
            )
        }
    elif (
        escalation is not None
        and contracted_by_stream.get(cm_stream_key, 0.0) > 0
        and cm_stream_key not in tenors
    ):
        warnings.append(
            "CM revenue is UN-TENORED, so it stays faded and un-indexed: CPI indexation per "
            f"{CM_RULE_SOURCE} is withheld until a tenor is supplied (the index applies to the "
            "agreement term, which is not known)."
        )

    _warn_contract_tenor_basis(
        contracted_by_stream,
        tenors,
        horizon_years,
        warnings=warnings,
        indexation_text=index_text,
    )
    # N2: ONE shortfall drives both contracted deductions. The contracted P90 haircut IS the
    # assumed delivery shortfall (P90 = the obligation was met on (1 − s) of it), so the revenue
    # loss is s × contracted revenue and the penalty is the annual cap scaled by the SAME s.
    # Summing an unscaled cap on top of the haircut (the pre-N2 behaviour) charged one event
    # twice and made winning a CM agreement reduce P90 EBITDA.
    # H-17 adds the unavailable fraction to that SAME shortfall: an outage forfeits the payment and
    # triggers the penalty regime, and it is the one shortfall number that drives both.
    # Clamped to [0, 1]: a shortfall is a fraction of an obligation, so a pathological library
    # haircut can never make the bound arithmetic below charge more than the revenue exists.
    shortfall_fraction = max(
        0.0, min(1.0, contracted_haircut / 100.0 + (1.0 - availability_factor))
    )
    # Non-delivery penalty (P90 only; None = un-modelled data-ask, never zero).
    penalty_exposure_raw = _p90_penalty_exposure(
        result, assumptions, shortfall_fraction, warnings=warnings, used=used
    )
    # The combined contracted deduction (forfeited revenue + penalty) is BOUNDED at the contracted
    # revenue itself: the cap is *defined* as a percentage of that revenue, so the bounded worst
    # case is losing it (net 0). You cannot lose 115% of a 100%-capped stream. The bound trims the
    # penalty only (the revenue loss is s × R ≤ R by construction), so the reported penalty line
    # stays the truthful post-bound figure.
    contracted_revenue_loss_raw = round(rev_contracted * shortfall_fraction, 2)
    penalty_effective_raw = min(
        penalty_exposure_raw or 0.0, max(0.0, rev_contracted - contracted_revenue_loss_raw)
    )
    if penalty_exposure_raw is not None and penalty_effective_raw < penalty_exposure_raw - 0.005:
        warnings.append(
            f"CM non-delivery penalty bounded (N2): the uncapped line £{penalty_exposure_raw} plus "
            f"the £{contracted_revenue_loss_raw} of forfeited contracted revenue would exceed the "
            f"£{rev_contracted} of contracted revenue itself, so the penalty is trimmed to "
            f"£{round(penalty_effective_raw, 2)} — the P90 contracted contribution floors at £0."
        )
    penalty_base = penalty_effective_raw * price_multiplier
    fade_pct = _as_float(
        _use(
            assumptions, "degradation", "annual_capacity_fade_pct", warnings=warnings, used=used
        ).value,
        where="degradation.annual_capacity_fade_pct",
    )

    # --- the projection ----------------------------------------------------
    #
    # Three revenue populations, because they do NOT share a shape (D-33 / D-130):
    #
    # 1. DISPATCH-DERIVED revenue (the FIRM class) — earned by moving energy, so it shrinks with
    #    the battery's capacity fade. Faded, merchant-haircut in P90. Unchanged behaviour.
    # 2. A FIXED AVAILABILITY PAYMENT inside its contract term (a CONTRACTED stream with a known
    #    tenor) — a CM agreement pays the awarded de-rated MW × the clearing price for the term
    #    whatever the cell chemistry does, so capacity fade must NOT shrink it. Held FLAT in term.
    #    The old code faded it, understating the single most bankable line in the stack.
    #    Its P90 haircut is now resolved PER STREAM (`_stream_contracted_haircut_pct`), not once
    #    for the whole class: a CM availability payment keeps the class-wide contracted haircut,
    #    while a DNO-flex / DFS award's in-term P90 is ZERO (`_NO_P90_IN_TERM`). P50 is untouched.
    # 3. Revenue BEYOND the contract term — not contracted revenue at all, it is re-contracting.
    #    Priced merchant-class: faded, and taking the MERCHANT haircut in P90 rather than the
    #    contracted one; for a local-flex award it is removed from P90 entirely (see
    #    ``_NO_P90_AFTER_EXPIRY``), and it never enters the lender-basis contracted series.
    #
    # A contracted stream with NO tenor supplied stays in population 1's shape — faded, contracted
    # haircut, full horizon — which is byte-identically the pre-D-130 behaviour, because the
    # in-term/post-term split is not computable without the contract length and this module does
    # not invent one. That dormancy is warned (see `_warn_contract_tenor_basis`) and regression-
    # tested, so no number here moves on evidence that was never supplied.
    #
    # `availability_factor` (H-17) multiplies all three, in both series. On the cost side it now
    # reduces the VARIABLE cost of cycling only (`avoided_variable_cost` below); fixed lines are
    # held whole, and nothing is escalated (N13/H-15) — all three deliberate, all three declared.
    tenored_base = {k: contracted_by_stream[k] * price_multiplier for k in tenors}
    untenored_contracted_base = (
        rev_contracted - sum(contracted_by_stream[k] for k in tenors)
    ) * price_multiplier
    # The aggregates that decay together, kept as single expressions so the no-tenor path is the
    # pre-existing arithmetic to the last bit, not an algebraically equal rearrangement of it.
    faded_p50_base = round(firm_base + untenored_contracted_base, 2)
    faded_p90_base = firm_base * (1.0 - haircut_pct / 100.0) + untenored_contracted_base * (
        1.0 - contracted_haircut / 100.0
    )
    faded_p90_contracted_base = untenored_contracted_base * (1.0 - contracted_haircut / 100.0)
    cm_stream = MarketStream.CAPACITY_MARKET.value
    # D-139/D-130 disclosure, stated once against the year-1 £ rather than 15 times in the loop.
    _warn_in_term_p90_zeroed(
        {
            k: tenored_base[k] * _in_term_fraction(term_left[k], 1)
            for k in tenored_base
            if k in _NO_P90_IN_TERM
        },
        contracted_haircut,
        warnings=warnings,
    )

    # H-17, cost side. The de-rate used to shrink revenue while charging 100% of every cost,
    # justified as "an unavailable battery still costs its fixed opex" — true of the fixed lines and
    # false of the variable ones. The dispatch charge-energy cost and the levy on net charge volume
    # are both VOLUMETRIC: the MILP produced those volumes assuming the unit was available all year,
    # so at 95% availability it cycles ~5% less, buys ~5% less charging energy and pays ~5% less
    # levy on it. Charging the full amount against 95% of the revenue over-stated the cost by
    # (1 − factor) × variable cost every year — conservative, disclosed, and still wrong, which is
    # the failure mode two earlier fixes in this module had to be corrected for.
    #
    # Held WHOLE, deliberately: opex, insurance, business rates, metering/DNO (contractual, not
    # volumetric) and `parasitic_load` — a continuous house load is the one line the original
    # justification genuinely covers, since auxiliary plant keeps drawing while the unit is down.
    #
    # Written as `+ avoided` on the pre-existing expression rather than as a rebuilt cost total: at
    # `availability_factor == 1.0` the term is exactly 0.0 and `x + 0.0 == x` in IEEE-754, so the
    # no-evidence path stays bit-identical. Re-associating the two subtractions into one bracket
    # would differ in the last bit and could flip a year that rounds on a half-penny boundary.
    levy_line = cost_lines.get("levies_on_charge") or 0.0
    variable_cost_base = dispatch_cost + levy_line
    avoided_variable_cost = variable_cost_base * (1.0 - availability_factor)
    if availability_pct is not None and variable_cost_base > 0:
        warnings.append(
            f"Availability also reduces the VARIABLE cost of cycling by £"
            f"{round(avoided_variable_cost, 2)}/yr in year 1 (H-17, cost side): "
            f"{100.0 - availability_pct:g}% of the £{round(variable_cost_base, 2)} the dispatch "
            f"priced as charge energy (£{dispatch_cost}) plus the levy on net charge volume "
            f"(£{round(levy_line, 2)}). Both are volumetric and the MILP produced those volumes at "
            "100% availability, so energy the unit does not cycle is energy it does not buy. FIXED "
            "lines (opex, insurance, business rates, metering/DNO and the continuous parasitic "
            "draw) are charged in full. Reported cost figures stay RAW year-1: this is a named "
            "series adjustment, not a re-stated cost line."
        )

    if windowed_credit_line:
        availability_txt = (
            "no factor supplied" if availability_pct is None else f"{availability_pct:g}%"
        )
        warnings.append(
            f"The windowed-levy line (£{round(windowed_credit_line, 2)}/yr, a CREDIT where "
            "negative) is treated exactly as the DUoS avoidance revenue it is the analogue of, not "
            "as a flat cost: it FADES with capacity ((1 - fade)^(year-1)), scales with fleet "
            f"availability ({availability_txt}), "
            f"and takes the {haircut_pct:g}% merchant P90 haircut in the P90 series. Holding "
            "it flat and un-haircut — the convention every POSITIVE cost line uses — would make an "
            "avoided £ worth more than an identical earned £, purely because of which side of "
            "EBITDA it was written on. It is EXCLUDED from the contracted (lender) series, where "
            "the identical DUOS_RED_BAND_AVOIDANCE £ is also excluded because that series carries "
            "contracted revenue only. Reported cost lines stay RAW year-1 (the H-17 precedent): "
            "this is a named series adjustment, not a re-stated cost line."
        )

    p50_ebitda: list[float] = []
    p90_ebitda: list[float] = []
    p90_contracted_ebitda: list[float] = []
    discharge_kwh = dispatch_energy.annual_discharge_kwh if dispatch_energy is not None else None
    energy_by_year: list[float] | None = (
        [0.0] if discharge_kwh is not None and discharge_kwh > 0 else None
    )
    for year in range(1, horizon_years + 1):
        decay = (1.0 - fade_pct / 100.0) ** (year - 1)
        if energy_by_year is not None and discharge_kwh is not None:
            energy_by_year.append(discharge_kwh * decay * availability_factor)
        in_term = {k: _in_term_fraction(t, year) for k, t in term_left.items()}
        # WP-UW-17 (D129): the escalation factor E_t and the in-term CM index U x E_t. Both
        # are exactly 1.0 / 0.0 on an un-escalated run, and `x * 1.0 == x`, `x - 0.0 == x`, so the
        # legacy arithmetic is bit-identical.
        e_t = escalation[year - 1] if escalation is not None else 1.0
        index = {
            k: (
                cm_index.year1_uplift * e_t
                if cm_index is not None and cm_index.indexed and k == cm_stream_key
                else 1.0
            )
            for k in in_term
        }
        indexed_cost_year = indexed_cost_base * (e_t - 1.0) if escalation is not None else 0.0
        # (2) fixed availability payments, held flat on the awarded MW for the part of the year the
        # award is live. NOT faded — D-33.
        flat_in_term = sum(tenored_base[k] * f * index[k] for k, f in in_term.items())
        # (2b) the same in-term payments in the CAUTIOUS case, haircut PER STREAM rather than by
        # one class-wide scalar (D-139/D-1410 for local flex, E-06 + the unsettled-literature note
        # for DFS; `_stream_contracted_haircut_pct`). With a single tenored stream this is
        # bit-identical to the old `flat_in_term * (1 - contracted_haircut/100)`, and with none it
        # is the same 0 — so the arithmetic moves only where a stream on `_NO_P90_IN_TERM` is
        # actually tenored, never on a run that supplied no such award.
        flat_in_term_p90 = sum(
            tenored_base[k]
            * f
            * index[k]
            * (1.0 - _stream_contracted_haircut_pct(k, contracted_haircut) / 100.0)
            for k, f in in_term.items()
        )
        # (3) post-expiry re-contracting: merchant-class, faded, merchant haircut in P90.
        recontract_p50 = sum(tenored_base[k] * (1.0 - f) * decay for k, f in in_term.items())
        recontract_p90 = sum(
            tenored_base[k] * (1.0 - f) * decay * (1.0 - haircut_pct / 100.0)
            for k, f in in_term.items()
            if k not in _NO_P90_AFTER_EXPIRY
        )
        p50_rev = (faded_p50_base * decay + flat_in_term + recontract_p50) * availability_factor
        p90_rev = (faded_p90_base * decay + flat_in_term_p90 + recontract_p90) * availability_factor
        # Conservative contracted basis: contracted P90 revenue bears the (shortfall-scaled,
        # bounded — N2) penalty AND all costs. In-term only — post-expiry revenue is a
        # re-contracting expectation, and a lender does not lend against one.
        p90_contracted_rev = (
            faded_p90_contracted_base * decay + flat_in_term_p90
        ) * availability_factor
        # The penalty follows the CM payment it is a percentage OF: flat in term when the agreement
        # term is known, otherwise the legacy faded shape. Zero once the agreement has expired —
        # there is no obligation left to fail.
        penalty_year = penalty_base * (
            in_term[cm_stream] * index[cm_stream] if cm_stream in in_term else decay
        )
        # N2's bound, re-tested per year: the combined (forfeited revenue + penalty) deduction can
        # never exceed the year's own contracted revenue, so the P90 contracted contribution floors
        # at £0. The 0.005 (half-penny) guard means only a MATERIAL breach trims — sub-penny
        # float noise never perturbs a figure that rounds to the same pence either way.
        if penalty_year > p90_contracted_rev + 0.005:
            penalty_year = p90_contracted_rev
        # The windowed-levy credit, given the same treatment as the DUoS avoidance it substitutes
        # for: faded, availability-scaled, and merchant-haircut in the MERCHANT cautious series. All
        # three terms are exactly 0.0 with no windowed levy, and `x - 0.0 == x`, so every existing
        # figure is bit-identical.
        windowed_year = windowed_credit_line * decay * availability_factor
        # The haircut applies only while the line is a CREDIT (negative). Haircutting a positive
        # windowed line — the case where the optimum net-CHARGED inside the window, so the levy is a
        # real cost — would shrink a cost in the cautious series and put P90 ABOVE P50, the exact
        # invariant the negative-haircut clamp above exists to protect.
        windowed_year_p90 = (
            windowed_year * (1.0 - haircut_pct / 100.0)
            if windowed_credit_line < 0
            else windowed_year
        )
        # NOT in the contracted (lender) series. That series carries CONTRACTED revenue only:
        # `faded_p90_contracted_base` excludes every FIRM dispatch-derived stream, so the
        # economically identical DUOS_RED_BAND_AVOIDANCE £ is absent from it. Admitting this
        # credit through the cost side would put a FIRM-class bill saving into the lender basis
        # while its twin stays out — an artefact of which side of EBITDA the £ was written on, in
        # the direction that flatters the covenant. The series' "bears ALL costs" convention was
        # conservative only while every cost line was positive; a credit is not a cost, so it does
        # not ride it.
        windowed_year_contracted = 0.0
        p50_ebitda.append(
            round(
                p50_rev
                - dispatch_cost
                - operating_cost
                + avoided_variable_cost
                - windowed_year
                - indexed_cost_year,
                2,
            )
        )
        p90_ebitda.append(
            round(
                p90_rev
                - penalty_year
                - dispatch_cost
                - operating_cost
                + avoided_variable_cost
                - windowed_year_p90
                - indexed_cost_year,
                2,
            )
        )
        p90_contracted_ebitda.append(
            round(
                p90_contracted_rev
                - penalty_year
                - dispatch_cost
                - operating_cost
                + avoided_variable_cost
                - windowed_year_contracted
                - indexed_cost_year,
                2,
            )
        )

    # WP-UW-17 (D131): the stated lifecycle capital flows ride in both FCF series; the
    # terminal value never reaches CFADS (reported separately for `financial_model`). No flow
    # stated = `None` and the series is the legacy `[capex, *ebitda]`, bit-identical.
    capital = lifecycle_capital_flows(
        lifecycle,
        horizon_years=horizon_years,
        capex_total_gbp=capex.total_gbp,
        escalation=escalation,
    )
    if capital is None:
        p50_fcf = [round(-capex.total_gbp, 2), *p50_ebitda]
        p90_fcf = [round(-capex.total_gbp, 2), *p90_ebitda]
    else:
        p50_fcf = [
            round(-capex.total_gbp, 2),
            *(round(e + c, 2) for e, c in zip(p50_ebitda, capital.p50_by_year, strict=True)),
        ]
        p90_fcf = [
            round(-capex.total_gbp, 2),
            *(round(e + c, 2) for e, c in zip(p90_ebitda, capital.p90_by_year, strict=True)),
        ]
    line_basis = _line_basis(rev_by_stream, cost_lines, escalation, cm_index, set(tenors))
    real_fcf: list[float] | None = None
    real_irr: float | None = None
    indexation_warning: str | None = None
    if escalation is not None:
        real_fcf = [
            p50_fcf[0],
            *(round(v / e, 2) for v, e in zip(p50_fcf[1:], escalation, strict=True)),
        ]
        real = project_irr(real_fcf)
        nominal = project_irr(p50_fcf)
        real_irr = real.irr_pct
        indexation_warning = _indexation_basis_warning(
            cpi.pct or 0.0,
            cpi.assumption.status.value if cpi.assumption is not None else "unknown",
            line_basis,
            nominal.irr_pct,
            real_irr,
        )
    # C-7 (N1, N13/H-15, N14): the three bases the pack never stated. Emitted here, at the point
    # the series are finished, so they travel with every FinancialCase (financial_model extends
    # `warnings` with these) and no series reaches the debt/DSCR boundary or an IRR unlabelled.
    warnings.extend(
        _declared_basis_warnings(
            horizon_years,
            contracted_by_stream,
            indexation_warning=indexation_warning,
            lifecycle_warnings=capital.warnings if capital is not None else None,
        )
    )

    # Bankability gate: a model is bankable only if the underlying result is a bankable candidate
    # AND no non-confirmed assumption was used. With gb_v0_1 (all placeholders) the second clause
    # is always False, so a placeholder-driven run can never be bankable — even if the result
    # claims bankable_candidate. (The covenant gate is added at the financial-case layer.)
    uses_unconfirmed = _uses_unconfirmed(assumptions, used)
    result_bankable = _result_is_bankable(result, warnings=warnings)
    if uses_unconfirmed and result_bankable:
        warnings.append(
            "Not bankable: the run uses non-confirmed (placeholder/needs_review) assumptions — "
            "confirm them and bump the assumption version before presenting as bankable."
        )

    return CashflowModel(
        horizon_years=horizon_years,
        power_mw=power_mw,
        duration_hours=result.duration_hours,
        capex=capex,
        annual_revenue_by_stream_gbp=rev_by_stream,
        perfect_foresight_adjustment_gbp=pf_adjustment,
        annual_revenue_firm_gbp=rev_firm,
        annual_revenue_contracted_gbp=rev_contracted,
        p90_penalty_exposure_gbp=(None if penalty_exposure_raw is None else round(penalty_base, 2)),
        annual_dispatch_cost_gbp=dispatch_cost,
        annual_cost_by_line_gbp=cost_lines,
        p50_fcf_pre_debt_gbp=p50_fcf,
        p90_fcf_pre_debt_gbp=p90_fcf,
        p50_ebitda_gbp=p50_ebitda,
        p90_ebitda_gbp=p90_ebitda,
        p90_contracted_ebitda_gbp=p90_contracted_ebitda,
        assumption_version=assumptions.metadata.version,
        assumptions_used=sorted(used),
        uses_unconfirmed_assumptions=uses_unconfirmed,
        bankable=result_bankable and not uses_unconfirmed,
        warnings=_dedupe(warnings),
        availability_factor_pct=availability_pct,
        contracted_tenor_years=tuple(sorted(tenors.items())),
        availability_avoided_variable_cost_gbp=(
            None if availability_pct is None else round(avoided_variable_cost, 2)
        ),
        cashflow_basis=(
            NOMINAL_CPI_ESCALATED_BASIS
            if escalation is not None
            else FLAT_NOMINAL_UNESCALATED_BASIS
        ),
        omitted_capital_flows=(
            OMITTED_CAPITAL_FLOWS if capital is None else capital.excluded_labels
        ),
        delivered_energy_kwh_by_year=energy_by_year,
        line_basis=line_basis,
        cpi_state=cpi.state,
        cpi_pct_per_year=cpi.pct,
        escalation_by_year=escalation,
        real_p50_fcf_gbp=real_fcf,
        real_project_irr_pct=real_irr,
        cm_indexation=cm_index,
        lifecycle_flow_entries=capital.entries if capital is not None else (),
        lifecycle_capital_p50_by_year=capital.p50_by_year if capital is not None else None,
        lifecycle_capital_p90_by_year=capital.p90_by_year if capital is not None else None,
        lifecycle_cfads_by_year=capital.cfads_by_year if capital is not None else None,
        cod_delay_months=cod_delay,
    )


def _line_basis(
    rev_by_stream: Mapping[str, float],
    cost_lines: Mapping[str, float | None],
    escalation: tuple[float, ...] | None,
    cm_index: CmIndexation | None,
    tenored: set[str],
) -> tuple[tuple[str, str], ...]:
    """Every cashflow line and the money it is stated in (D129), sorted.

    Un-escalated run: every line is ``flat_nominal_unescalated`` (the declared legacy basis).
    Escalated run: the five fixed cost lines are ``nominal_cpi_escalated``; a tenored T-4 CM
    payment is ``nominal_cm_cpi_indexed`` in term; everything else is ``nominal_flat``.
    """
    lines = [
        *(f"revenue:{k}" for k in rev_by_stream),
        "cost:dispatch_charging",
        *(f"cost:{k}" for k in cost_lines),
        "capex",
    ]
    out: dict[str, str] = {}
    for line in lines:
        if escalation is None:
            basis = CashflowLineBasis.FLAT_NOMINAL_UNESCALATED
        elif line.startswith("cost:") and line[5:] in INDEXED_COST_LINE_KEYS:
            basis = CashflowLineBasis.NOMINAL_CPI_ESCALATED
        elif (
            line == f"revenue:{MarketStream.CAPACITY_MARKET.value}"
            and cm_index is not None
            and cm_index.indexed
            and MarketStream.CAPACITY_MARKET.value in tenored
        ):
            basis = CashflowLineBasis.NOMINAL_CM_CPI_INDEXED
        else:
            basis = CashflowLineBasis.NOMINAL_FLAT
        out[line] = basis.value
    return tuple(sorted(out.items()))


def _indexation_basis_warning(
    cpi_pct: float,
    status: str,
    line_basis: tuple[tuple[str, str], ...],
    nominal_irr_pct: float | None,
    real_irr_pct: float | None,
) -> str:
    """The ONE "BASIS —" line of an escalated run: lines and tags, rate, status, both IRRs."""
    by_basis: dict[str, list[str]] = {}
    for line, basis in line_basis:
        by_basis.setdefault(basis, []).append(line)
    groups = "; ".join(f"{b}: {', '.join(lines)}" for b, lines in sorted(by_basis.items()))

    def _pct(v: float | None) -> str:
        return "unavailable" if v is None else f"{v:.2f}%"

    return (
        f"BASIS — {NOMINAL_CPI_ESCALATED_BASIS} (N13/H-15, D129). Rate: "
        f"financing.cpi_pct_per_year = {cpi_pct:g}%/yr ({status}), E_t = (1 + c)^(t-1) from "
        f"operating year 1. Lines: {groups}. Project IRR (annual, COD-anchored): nominal "
        f"{_pct(nominal_irr_pct)}, real (deflated to year-1 money) {_pct(real_irr_pct)}. Debt "
        "rates and the equity hurdle are nominal and now meet a nominal ledger; the energy-price "
        "lines are held flat because no cited escalation for them exists."
    )


def _scenario_multiplier(scenario: object | None, attr: str, *, warnings: list[str]) -> float:
    """Read a scenario multiplier by duck-typing (default 1.0); warn on an invalid value."""
    if scenario is None:
        return 1.0
    value = getattr(scenario, attr, None)
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        warnings.append(f"Scenario {attr}={value!r} is invalid; ignored (used 1.0).")
        return 1.0
    return float(value)


def _result_is_bankable(result: ResultLike, *, warnings: list[str]) -> bool:
    """Whether the underlying optimisation result is itself a bankable candidate.

    Read by duck-typing (a compatible result need not import the enum). Warns when it is not."""
    status = getattr(result, "bankable_status", None)
    bankable = getattr(status, "value", status) == "bankable_candidate"
    if not bankable:
        warnings.append(
            "Underlying optimisation result is not a bankable candidate — the financial case "
            "is indicative only, not bankable."
        )
    return bankable


def _uses_unconfirmed(assumptions: AssumptionLibrary, used: list[str]) -> bool:
    """True if any used assumption is not ``confirmed`` (placeholder/needs_review).

    Every ref in ``used`` was fetched via :func:`_use`, so it resolves in the library."""
    for ref in used:
        section, name = ref.split(".", 1)
        if assumptions.get(section, name).status != AssumptionStatus.CONFIRMED:
            return True
    return False


def _dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication (deterministic given deterministic generation order)."""
    return list(dict.fromkeys(items))
