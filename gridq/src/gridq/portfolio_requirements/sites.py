"""Per-site Pessimistic / Central / Optimistic cases on the **merchant + PPA** model.

Revised 2026-08-05 after a fundamental reframe from the principal. The earlier version modelled the
battery as an *avoided-cost* asset, capping annual discharge at ``site load x 0.35``. That made site
consumption the ceiling on battery earnings. **It was the wrong model.**

The correct model:

* **The customer buys power at a fixed GBP/kWh PPA rate.** That is the PPA revenue leg.
* **Site load and its shape are a CONSTRAINT, not a cap** — they say when the battery must hold
  energy to meet the supply obligation.
* **The battery is otherwise a merchant asset**, trading as hard as its remaining cycles allow.
* **Sizing is therefore capped by CONNECTION CAPACITY and throughput (657 MWh/MW/yr)**, never by
  site consumption. A site consuming 550 MWh/yr can host a 1 MW battery cycling 657 MWh/yr.

Three mechanics that fall out of this and drive every number below:

1. **PPA-first throughput allocation.** PPA net margin (9.7-15p/kWh Central) beats merchant
   arbitrage (42,000/657 = 6.4p/kWh Central), so the PPA obligation is served first and merchant
   takes the residual. At sub-load-scale batteries the PPA consumes the *entire* throughput and the
   **merchant energy leg is zero** — only capacity streams survive. This is why small connections
   hurt so much.
2. **Energy streams scale with the merchant share; capacity streams do not.** Arbitrage, BM and
   DUoS consume throughput. CM, frequency response and local flex need export rights and
   state-of-charge headroom, not net cycles.
3. **Sleeved supply is a structural precondition.** The margins below assume *we* control the
   supply point and charge at off-peak wholesale plus non-commodity residue. If the battery must
   charge on the customer's existing flat tariff, the PPA leg dies at **every** site.

Design by the Fable advisor; the DCF is this module's. The advisor's earlier expected-IRR estimates
were retracted once reconciled against the arithmetic — a 15-year asset needs roughly **11.7%**
Year-1 gross yield (EBITDA/capex) to clear ~8% unlevered, and yields below ~9% produce *negative*
leverage at 7-9% debt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridq.portfolio_requirements.assumptions import (
    BOUNDS,
    CONTRACTED_TIER,
    MERCHANT_TIER,
    FinancingTier,
    Tag,
)
from gridq.portfolio_requirements.kernel import annuity_factor, irr

CASES = ("Pessimistic", "Central", "Optimistic")

SOLAR_DEGRADATION = 0.005

# Round-trip efficiency — cost per kWh discharged is charging cost / RTE.
ROUND_TRIP_EFFICIENCY = 0.88

# Per-site fixed capex applies to BATTERY projects only. A rooftop solar array does not need a
# new connection, ICP, protection or metering package; loading it with GBP250k destroys ~15
# percentage points of yield and would be a modelling artefact, not a cost.
SOLAR_ONLY_FIXED_CAPEX = 25_000.0

# The precondition that gates every PPA leg in this module.
SLEEVED_SUPPLY_PRECONDITION = (
    "STRUCTURAL PRECONDITION — sleeved supply. Every PPA margin here assumes Neura controls the "
    "supply point and charges the battery at off-peak wholesale plus non-commodity residue "
    "(12p Pessimistic / 10p Central / 8p Optimistic per kWh charged, i.e. 13.6 / 11.4 / 9.1p per "
    "kWh discharged at 88% round-trip). If the battery must instead charge on the customer's "
    "existing flat tariff (15-33p), the PPA leg is loss-making at EVERY site and none of these "
    "cases stand. This must be confirmed commercially before any site is proposed."
)

THROUGHPUT_SPLIT_NOTE = (
    "PPA-first throughput allocation. Total throughput is 657 MWh/MW/yr (365 cycles x 1.8 MWh "
    "usable). PPA net margin (9.7-15p/kWh Central) exceeds merchant arbitrage (6.4p/kWh Central), "
    "so the supply obligation is served first: T_PPA = min(serveable site volume, 657 x MW), and "
    "the merchant share m = (657 x MW - T_PPA) / (657 x MW). Energy streams (arbitrage, BM, DUoS) "
    "scale with m; capacity streams (Capacity Market, frequency response, local flex) do not, "
    "because they need export rights and SoC headroom rather than net cycles. Serveable fraction "
    "of site load f = 0.55 / 0.70 / 0.80 — load in the charging window passes through at ~zero "
    "margin."
)


@dataclass(frozen=True)
class SiteCase:
    """One site under one case. Revenue lines are absolute GBP/yr, year 1, pre-degradation."""

    site_key: str
    site_label: str
    case: str
    bess_mw: float
    bess_mwh: float
    solar_kwp: float
    connection_basis: str
    binding_constraint: str
    revenue_lines: dict[str, float]
    capex_gbp: float
    opex_gbp: float
    tier: FinancingTier
    driver: str
    battery_revenue_share: float
    merchant_share: float
    tag: Tag
    key_assumptions: tuple[str, ...] = field(default_factory=tuple)
    blocking: str | None = None
    proposed: bool = True

    @property
    def revenue_gbp(self) -> float:
        return sum(self.revenue_lines.values())

    @property
    def gross_yield(self) -> float:
        """Year-1 EBITDA / capex — the number that decides whether a 15-year asset works."""
        if not self.capex_gbp:
            return 0.0
        return (self.revenue_gbp - self.opex_gbp) / self.capex_gbp


@dataclass(frozen=True)
class SiteResult:
    """Evaluated site case."""

    case: SiteCase
    capex: float
    debt: float
    equity: float
    gearing: float
    debt_service: float
    unlevered_irr: float | None
    levered_irr: float | None
    dscr_min: float
    dscr_avg: float
    debt_bound_by: str
    payback_years: float | None
    npv_at_8pct: float
    leverage_accretive: bool

    @property
    def clears_yield_bar(self) -> bool:
        """A 15-year asset needs ~11.7% Year-1 gross yield to clear ~8% unlevered."""
        return self.case.gross_yield >= 0.117

    @property
    def suspect_high_return(self) -> bool:
        """Levered IRR above 25% — flag it rather than present it as a headline.

        A return this high in a pre-diligence assessment is far more likely to indicate an
        unverified input than a genuine opportunity. Harwell is the worked example: its solar
        return rests entirely on an ASSUMED avoided rate, because no tariff data was supplied.
        The flag exists so the caveat travels with the number automatically instead of depending
        on someone remembering to write it in the prose.
        """
        return self.levered_irr is not None and self.levered_irr > 0.25


def evaluate_site(case: SiteCase) -> SiteResult:
    """DSCR-sized debt applied at site level. Same integrity rule as the portfolio kernel.

    Debt is never asserted as a gearing percentage. Where the cashflow is thin the DSCR leg binds
    and gearing falls well below 60% — and where the unlevered return sits under the debt rate the
    result is flagged as value-destructive rather than quietly reported.
    """
    b = BOUNDS
    years = b.asset_life_years
    tier = case.tier
    p90 = (
        b.p90_factor_contracted
        if tier.firm_revenue_share_required >= 0.60
        else b.p90_factor_merchant
    )

    batt_share = case.battery_revenue_share
    ebitda: list[float] = []
    for y in range(1, years + 1):
        esc = (1.0 + b.cpi_escalation) ** (y - 1)
        batt_fade = (1.0 - b.degradation_per_year) ** (y - 1)
        solar_fade = (1.0 - SOLAR_DEGRADATION) ** (y - 1)
        revenue = (
            case.revenue_gbp * esc * (batt_share * batt_fade + (1.0 - batt_share) * solar_fade)
        )
        ebitda.append(revenue - case.opex_gbp * esc)

    cfads = ebitda[0] * p90
    debt_dscr = max(cfads / tier.dscr_target, 0.0) * annuity_factor(tier.rate, tier.tenor_years)
    debt_cap = tier.gearing_cap * case.capex_gbp
    debt = max(min(debt_dscr, debt_cap), 0.0)
    bound = "dscr" if debt_dscr <= debt_cap else "gearing_cap"
    service = debt / annuity_factor(tier.rate, tier.tenor_years) if debt > 0 else 0.0
    equity = case.capex_gbp - debt

    unlev = [-case.capex_gbp, *ebitda]
    lev = [-equity]
    dscr: list[float] = []
    for y in range(years):
        s = service if y < tier.tenor_years else 0.0
        lev.append(ebitda[y] - s)
        if s > 0:
            dscr.append(ebitda[y] / s)

    cum = -case.capex_gbp
    payback: float | None = None
    for idx, e in enumerate(ebitda):
        if payback is None and e > 0 and cum + e >= 0:
            payback = idx + (-cum / e)
        cum += e

    u = irr(unlev)
    lvd = irr(lev)
    return SiteResult(
        case=case,
        capex=case.capex_gbp,
        debt=debt,
        equity=equity,
        gearing=debt / case.capex_gbp if case.capex_gbp else 0.0,
        debt_service=service,
        unlevered_irr=u,
        levered_irr=lvd,
        dscr_min=min(dscr) if dscr else float("inf"),
        dscr_avg=(sum(dscr) / len(dscr)) if dscr else float("inf"),
        debt_bound_by=bound,
        payback_years=payback,
        npv_at_8pct=sum(cf / (1.0 + b.discount_rate_npv) ** i for i, cf in enumerate(unlev)),
        leverage_accretive=(lvd is not None and u is not None and lvd > u),
    )


def _c(
    site_key: str,
    site_label: str,
    case: str,
    *,
    bess_mw: float,
    solar_kwp: float,
    connection: str,
    binding: str,
    lines: dict[str, float],
    capex: float,
    opex: float,
    driver: str,
    batt_share: float,
    merchant_share: float,
    tag: Tag,
    tier: FinancingTier = MERCHANT_TIER,
    assumptions: tuple[str, ...] = (),
    blocking: str | None = None,
    proposed: bool = True,
) -> SiteCase:
    return SiteCase(
        site_key=site_key,
        site_label=site_label,
        case=case,
        bess_mw=bess_mw,
        bess_mwh=bess_mw * 2.0,
        solar_kwp=solar_kwp,
        connection_basis=connection,
        binding_constraint=binding,
        revenue_lines=lines,
        capex_gbp=capex,
        opex_gbp=opex,
        tier=tier,
        driver=driver,
        battery_revenue_share=batt_share,
        merchant_share=merchant_share,
        tag=tag,
        key_assumptions=assumptions,
        blocking=blocking,
        proposed=proposed,
    )


# ============================================================================================
# SITE 1 — BISHOPS CLEEVE. The reframe's big winner: the only >=1 MW export route in the estate.
# ============================================================================================
S1, S1L = "bishops_cleeve", "Grundon — Bishops Cleeve"
S1_BLOCK = (
    "THREE items, all export-related, and they carry the case: (1) export MEC and G99 headroom "
    "in writing; (2) the genset-charging arrangement — the battery charges from the LFG fleet at "
    "its export opportunity cost; (3) the uniform 0.49 p/kWh 'Feed in tariff' line — levy or "
    "export price? If it is the export price, charging cost collapses toward 0.49p and the case is "
    "transformed. Treated as a LEVY throughout; the upside is noted, never banked."
)
S1_ASM = (
    "Connection: 480 kVA import across two MPANs (MRF 250 kVA + Import 230 kVA) PLUS an existing "
    "grid EXPORT connection carrying 6,701,123 kWh in 2025 — the only >=1 MW market access in the "
    "estate. The battery co-locates behind the export meter and trades through a route already "
    "built",
    "LFG output fell 19.9% YoY (8,614 MWh 2024 -> 6,903 MWh 2025). Export headroom is therefore "
    "SMALL today and GROWS every year as the gas declines — an unusual case where the host asset's "
    "decline improves the battery's position",
    "Charging: 60% from gensets at ~6p/kWh export opportunity cost, 40% imported (Central)",
    "PPA reference is the MRF delivered rate of 26.58 p/kWh. Model_v5's flat 24p would be only a "
    "9.7% saving here — the PPA is set as delivered rate x (1 - guaranteed saving) instead",
    "Firm revenue share 65% in Central supports the contracted debt tier; Optimistic falls to 39% "
    "firm as merchant grows, so it is financed on the merchant tier",
)

# ============================================================================================
# SITE 2 — TANHOUSE & COLNBROOK. Solar-first. No grid connection: merchant needs one built.
# ============================================================================================
S2, S2L = "tanhouse_colnbrook", "Grundon — Tanhouse & Colnbrook"
S2_BLOCK = (
    "The EfW private-wire contract must be read: (a) does it permit solar displacement with no "
    "take-or-pay floor, and (b) does it prohibit a parallel DNO connection? Either answer can end "
    "the site. Also request a budget DNO connection offer now (~GBP1-2k) to price the merchant "
    "option properly — it is an option worth preserving, not a case to underwrite."
)
S2_ASM = (
    "NO GRID CONNECTION EXISTS — 100% EfW private wire plus solar. Merchant trading requires a "
    "grid connection to trade into, so a merchant battery is impossible here today. This is a "
    "physical fact, not a modelling assumption",
    "A new 2 MW connection does NOT clear the bar at Central: capex ~GBP1.52m (2x560k + 250k + "
    "2x~75k connection) against merchant revenue ~GBP142.6k = 9.4% gross yield, below the ~11.7% "
    "threshold — before a 1-3 year DNO queue. It only works at a <=GBP40k/MW connection offer or "
    "on the Stretch merchant stack",
    "COLNBROOK CANNOT TAKE A BATTERY PPA AT ALL: at 13.97 p/kWh delivered, a 20% saving implies "
    "11.2p against a cost floor of 13-15.6p per kWh discharged. The margin is NEGATIVE at any "
    "discount. Solar can undercut it (LCOE ~5-6p); a battery cannot",
    "Tanhouse battery PPA margin is ~2.2p/kWh Central charging off the 13.97p wire — GBP30-40k/yr "
    "at best, which cannot carry a battery",
    "Solar capex ~GBP650-700/kWp all-in for incremental rooftop. Roof/land area for 750 kWp is "
    "UNVERIFIED — survey item",
    "Per-site fixed capex reduced to GBP25k for solar-only: a rooftop array needs no new "
    "connection, ICP, protection or metering package. Loading the full GBP250k would cut yield "
    "from ~16% to ~6% and would be a modelling artefact, not a cost",
    "FINANCED ON THE MERCHANT TIER (9.0% / 8 yr / DSCR 1.40x): avoided-cost revenue behind an "
    "unverified private-wire contract is not contracted revenue",
)

# ============================================================================================
# SITE 3 — KNOWL HILL. 325 kVA binds everything. Marginal even on the favourable tariff read.
# ============================================================================================
S3, S3L = "knowl_hill", "Grundon — Knowl Hill"
S3_ASM = (
    "Connection 325 kVA — binds the sizing in every case",
    "TARIFF RESOLVED: the 15.41 p/kWh figure is the COMMODITY day rate from the estate tariff "
    "sheet; 33.02 p/kWh is the BLENDED DELIVERED cost from the invoices (cost / kWh). These are "
    "different quantities, not a contradiction. The PPA reference is the delivered 33.02p, because "
    "that is what the customer actually pays per kWh all-in",
    "No night rate exists on the commodity tariff, so there is no time-of-use differential to "
    "arbitrage against on the customer's own tariff — the merchant leg must trade wholesale",
    "LFG supplies 489 of 535 MWh/yr and is failing. Grid-served load therefore GROWS toward "
    "535 MWh/yr, which helps the PPA leg over time — but Year-1 economics still fail at Central",
    "At 0.3 MW the PPA obligation consumes the entire throughput, so the merchant ENERGY leg is "
    "zero and only capacity streams (CM / FR / flex) remain",
)

# ============================================================================================
# SITE 4 — EWELME. 750 kVA headroom is real, but the sub-1MW cost stack still bites at Central.
# ============================================================================================
S4, S4L = "ewelme", "Grundon — Ewelme"
S4_BLOCK = (
    "Confirm the connection agreement's MIC was not de-rated or terminated after the aerosol plant "
    "failed — operators routinely cut MIC to save availability charges. The Pessimistic case "
    "assumes it WAS de-rated (to ~300 kVA). The 750 kVA headroom is the entire investment case."
)
S4_ASM = (
    "Connection: 750 kVA live at 8.7% utilisation (~685 kW headroom) on the Workshop, plus "
    "200 kVA on the MRF. This is the estate's second market access and the sizing basis",
    "PPA reference is the Workshop delivered rate of 28.76 p/kWh. Model_v5's flat 24p is a 16.6% "
    "saving here, which is defensible",
    "At 0.6 MW (Central) the PPA obligation consumes the ENTIRE throughput — merchant share is "
    "zero and only capacity streams remain. Merchant energy revenue appears only at 1.0 MW "
    "(Optimistic), where 657 x MW finally exceeds the serveable load",
    "Central fails the ~11.7% gross-yield bar at 8.7%. Ewelme is an OPTIMISTIC-case project; the "
    "Central case as modelled does not return capital adequately",
    "Existing solar was connected within the last month — free upside to the pack but not "
    "quantifiable until the meter has history",
)

# ============================================================================================
# SITE 5 — HARWELL. Private campus network likely kills the merchant leg. Solar-led.
# ============================================================================================
S5, S5L = "harwell", "Harwell Science & Innovation Campus"
S5_BLOCK = (
    "Two documents decide whether a battery is possible at all: (1) the campus network agreement — "
    "does it permit export and market participation past the campus boundary? (2) one electricity "
    "invoice, to establish the tariff. Without both, the PPA leg is UNPRICEABLE and no battery IRR "
    "can be quoted. Solar does not depend on either."
)
S5_ASM = (
    "PRIVATE CAMPUS NETWORK. If export and market access are restricted at the campus boundary "
    "(the default assumption until the agreement says otherwise) then Capacity Market, frequency "
    "response, Balancing Mechanism and wholesale are ALL unavailable — roughly GBP71k/MW/yr of "
    "Central merchant value disappears",
    "NO TARIFF DATA SUPPLIED. The avoidable rate is ASSUMED at 18-20 p/kWh and classified STRETCH. "
    "A PPA-only battery serving ~60% of load (0.83 MW, GBP830k) yields ~6.5% even at an assumed "
    "11p margin — it fails. NO BATTERY IRR IS QUOTED for this site",
    "Real half-hourly data: 758 days, 2024-04-01 to 2026-05-01, ZERO missing intervals. The "
    "estate's best load evidence — used to price solar self-consumption precisely",
    "POWEN's 426,000 kWh consumption input is 2.1x below the metered 908,590 kWh. Re-based, "
    "Option 2 self-consumption is ~85%, not the 50% POWEN state. Option 2 is therefore correct",
    "POWEN's table mislabels kWp as MWp ('PV Capacity (MWp) 87.20 / 259.42')",
    "Roof-limited at 4,479 m2 — Option 2 (259.42 kWp) is the largest available",
    "Per-site fixed capex reduced to GBP25k for solar-only (see Tanhouse note)",
    "FINANCED ON THE MERCHANT TIER (9.0% / 8 yr / DSCR 1.40x): avoided-cost revenue behind an "
    "unverified tariff is not contracted revenue. The gate is explicit, not hidden in a haircut",
)


SITE_CASES: tuple[SiteCase, ...] = (
    # ---------------- SITE 1 — Bishops Cleeve ----------------
    _c(
        S1,
        S1L,
        "Pessimistic",
        bess_mw=0.5,
        solar_kwp=0,
        connection="480 kVA import; export rights NOT granted",
        binding="export rights",
        lines={"PPA leg (303 MWh @ 5.7p)": 17_300, "Capacity streams (haircut, no export)": 5_000},
        capex=600_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        driver="Import-charged battery with no export rights — capital is not returned.",
        assumptions=S1_ASM,
        blocking=S1_BLOCK,
    ),
    _c(
        S1,
        S1L,
        "Central",
        bess_mw=1.0,
        solar_kwp=0,
        connection="480 kVA import + existing >=1 MW export route (G99 amendment)",
        binding="export headroom + charging import",
        lines={
            "PPA leg (385 MWh @ 12.5p)": 48_100,
            "Merchant arbitrage": 22_800,
            "DUoS red-band": 2_500,
            "Capacity Market": 13_000,
            "Frequency response": 9_000,
            "Balancing Mechanism": 3_300,
            "Local flexibility": 3_000,
        },
        capex=850_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.41,
        tag=Tag.CENTRAL,
        tier=CONTRACTED_TIER,
        driver="Genset-charged battery trading through an already-built export route (65% firm).",
        assumptions=S1_ASM,
        blocking=S1_BLOCK,
    ),
    _c(
        S1,
        S1L,
        "Optimistic",
        bess_mw=1.5,
        solar_kwp=0,
        connection="export headroom confirmed and growing as LFG declines",
        binding="throughput / market depth",
        lines={
            "PPA leg (440 MWh @ 13.8p)": 60_700,
            "Merchant arbitrage": 65_600,
            "DUoS red-band": 5_000,
            "Capacity Market": 13_000,
            "Frequency response": 18_000,
            "Balancing Mechanism": 6_600,
            "Local flexibility": 3_000,
        },
        capex=1_140_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.55,
        tag=Tag.STRETCH,
        driver="1.5 MW trading the full stack through confirmed export headroom (39% firm).",
        assumptions=S1_ASM,
        blocking=S1_BLOCK,
    ),
    # ---------------- SITE 2 — Tanhouse & Colnbrook (solar-led) ----------------
    _c(
        S2,
        S2L,
        "Pessimistic",
        bess_mw=0,
        solar_kwp=250,
        connection="none — 100% EfW private wire",
        binding="no grid connection",
        lines={"Solar displacing EfW (238 MWh @ 11.9p)": 28_300},
        capex=170_000,
        opex=5_000,
        batt_share=0.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        tier=MERCHANT_TIER,
        driver="Colnbrook rooftop solar only — no battery is possible without a grid connection.",
        assumptions=S2_ASM,
        blocking=S2_BLOCK,
    ),
    _c(
        S2,
        S2L,
        "Central",
        bess_mw=0,
        solar_kwp=750,
        connection="none — 100% EfW private wire",
        binding="no grid connection",
        lines={"Solar displacing EfW (713 MWh @ ~12p)": 86_000},
        capex=490_000,
        opex=8_000,
        batt_share=0.0,
        merchant_share=0.0,
        tag=Tag.FAVOURABLE,
        tier=MERCHANT_TIER,
        driver="750 kWp across both sites, displacing EfW private-wire power.",
        assumptions=S2_ASM,
        blocking=S2_BLOCK,
    ),
    _c(
        S2,
        S2L,
        "Optimistic",
        bess_mw=2.0,
        solar_kwp=750,
        connection="NEW 2 MW DNO connection — only if the offer is <=GBP40k/MW",
        binding="connection cost + DNO queue",
        lines={"Solar displacing EfW": 86_000, "Merchant stack (2 MW, Stretch)": 283_000},
        capex=1_940_000,
        opex=44_000,
        batt_share=0.77,
        merchant_share=1.0,
        tag=Tag.STRETCH,
        tier=MERCHANT_TIER,
        driver=(
            "Contingent: build a grid connection and run a 2 MW merchant battery alongside solar."
        ),
        assumptions=S2_ASM,
        blocking=S2_BLOCK,
    ),
    # ---------------- SITE 3 — Knowl Hill ----------------
    _c(
        S3,
        S3L,
        "Pessimistic",
        bess_mw=0.25,
        solar_kwp=0,
        connection="325 kVA, no export rights",
        binding="connection + export rights",
        lines={"PPA leg (164 MWh @ 10.8p)": 17_700, "Capacity streams": 1_000},
        capex=425_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        driver="Sub-scale battery on a small connection — capital is not returned.",
        assumptions=S3_ASM,
    ),
    _c(
        S3,
        S3L,
        "Central",
        bess_mw=0.3,
        solar_kwp=0,
        connection="325 kVA with G99 export",
        binding="throughput (PPA consumes all cycles)",
        lines={"PPA leg (197 MWh @ 15.2p)": 29_900, "Capacity streams (CM + FR + flex)": 6_600},
        capex=460_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        tier=CONTRACTED_TIER,
        driver="PPA-led on the delivered 33.02p rate; merchant energy leg is zero at this size.",
        assumptions=S3_ASM,
    ),
    _c(
        S3,
        S3L,
        "Optimistic",
        bess_mw=0.5,
        solar_kwp=0,
        connection="325 kVA upgraded (+GBP60k)",
        binding="connection upgrade",
        lines={"PPA leg (328 MWh @ 19.6p)": 64_300, "Capacity streams": 15_600},
        capex=660_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.STRETCH,
        tier=CONTRACTED_TIER,
        driver="Upgraded connection, deeper PPA margin as LFG fails and grid-served load grows.",
        assumptions=S3_ASM,
    ),
    # ---------------- SITE 4 — Ewelme ----------------
    _c(
        S4,
        S4L,
        "Pessimistic",
        bess_mw=0.25,
        solar_kwp=0,
        connection="MIC de-rated to ~300 kVA, no export",
        binding="connection (MIC de-rated)",
        lines={"PPA leg (164 MWh @ 7.4p)": 12_100, "Capacity streams": 1_000},
        capex=425_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        driver="MIC assumed de-rated after the aerosol plant failed — the case collapses.",
        assumptions=S4_ASM,
        blocking=S4_BLOCK,
    ),
    _c(
        S4,
        S4L,
        "Central",
        bess_mw=0.6,
        solar_kwp=0,
        connection="750 kVA confirmed, G99 export",
        binding="throughput (PPA consumes all cycles)",
        lines={
            "PPA leg (394 MWh @ 11.5p)": 45_300,
            "Capacity Market": 7_800,
            "Frequency response": 5_400,
            "Local flexibility": 1_800,
        },
        capex=670_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.0,
        tag=Tag.CENTRAL,
        tier=CONTRACTED_TIER,
        driver="0.6 MW on the stranded connection; PPA takes the whole throughput (92% firm).",
        assumptions=S4_ASM,
        blocking=S4_BLOCK,
    ),
    _c(
        S4,
        S4L,
        "Optimistic",
        bess_mw=1.0,
        solar_kwp=0,
        connection="750 kVA + export works (+GBP40k)",
        binding="connection / export",
        lines={
            "PPA leg (555 MWh @ 15.8p)": 87_700,
            "Merchant arbitrage": 15_300,
            "DUoS red-band": 1_400,
            "Capacity Market": 13_000,
            "Frequency response": 18_000,
            "Balancing Mechanism": 1_900,
            "Local flexibility": 3_000,
        },
        capex=850_000,
        opex=6_000,
        batt_share=1.0,
        merchant_share=0.155,
        tag=Tag.STRETCH,
        tier=CONTRACTED_TIER,
        driver=(
            "1.0 MW finally exceeds serveable load, so a merchant energy leg appears (75% firm)."
        ),
        assumptions=S4_ASM,
        blocking=S4_BLOCK,
    ),
    # ---------------- SITE 5 — Harwell (solar only; no battery IRR quoted) ----------------
    _c(
        S5,
        S5L,
        "Pessimistic",
        bess_mw=0,
        solar_kwp=87.2,
        connection="private campus network — export/market access restricted",
        binding="campus network restrictions",
        lines={"Solar, ~100% self-consumed (79.9 MWh)": 14_400},
        capex=61_000,
        opex=3_000,
        batt_share=0.0,
        merchant_share=0.0,
        tag=Tag.STRETCH,
        tier=MERCHANT_TIER,
        driver=(
            "POWEN Option 1 rooftop solar. No battery — the campus network blocks the merchant leg."
        ),
        assumptions=S5_ASM,
        blocking=S5_BLOCK,
    ),
    _c(
        S5,
        S5L,
        "Central",
        bess_mw=0,
        solar_kwp=259.42,
        connection="private campus network — export/market access restricted",
        binding="campus network restrictions",
        lines={"Solar, ~85% self-consumed (228.5 MWh)": 38_800},
        capex=169_000,
        opex=4_000,
        batt_share=0.0,
        merchant_share=0.0,
        tag=Tag.STRETCH,
        tier=MERCHANT_TIER,
        driver="POWEN Option 2 at a re-based 85% self-consumption — roof-limited and solar-led.",
        assumptions=S5_ASM,
        blocking=S5_BLOCK,
    ),
    _c(
        S5,
        S5L,
        "Optimistic",
        bess_mw=0,
        solar_kwp=259.42,
        connection="campus rate >=30p confirmed",
        binding="roof area (4,479 m2)",
        lines={"Solar at a confirmed campus rate": 55_000},
        capex=169_000,
        opex=4_000,
        batt_share=0.0,
        merchant_share=0.0,
        tag=Tag.STRETCH,
        tier=MERCHANT_TIER,
        driver="Same array against a confirmed higher campus rate. Battery remains unpriceable.",
        assumptions=S5_ASM,
        blocking=S5_BLOCK,
    ),
)

SITE_ORDER: tuple[tuple[str, str], ...] = (
    (S1, S1L),
    (S2, S2L),
    (S3, S3L),
    (S4, S4L),
    (S5, S5L),
)

# Advisor's expected levered-IRR ranges on the REBUILT merchant + PPA model, after applying the
# DCF discipline (11.7% Year-1 gross yield needed for ~8% unlevered).
EXPECTED_LEVERED: dict[str, tuple[str, str, str]] = {
    S1: ("~-8%", "~7-9%", "~17-20%"),
    S2: ("~10%", "~13-16%", "~18-20% (contingent)"),
    S3: ("negative", "~0-2% fail", "~9-11%"),
    S4: ("negative", "~1-3% fail", "~14-17%"),
    S5: ("~12%", "~15-20%", "~20%+"),
}

SOLAR_LIFE_NOTE = (
    "All cases use a 15-year asset life, which is a battery convention. Solar arrays defensibly "
    "run 25 years with inverter replacement around year 13. Extending life for solar-attributable "
    "cashflows only would materially improve the solar-led sites (2 and 5). It is deliberately NOT "
    "applied here: it requires a versioned assumption change signed off upstream rather than being "
    "used as a tuning knob to reach a target return."
)

INVESTABILITY_RANKING: tuple[tuple[int, str, str], ...] = (
    (
        1,
        S5,
        "Harwell solar — ~15-20% levered Central, the estate's strongest return, and it depends on "
        "neither the campus network agreement nor a battery. The wide band is purely because the "
        "campus tariff is unverified: get one invoice. NO battery IRR is quotable here.",
    ),
    (
        2,
        S2,
        "Tanhouse & Colnbrook solar — ~13-16% levered Central. Strong, but gated on the EfW "
        "private-wire contract permitting displacement. Colnbrook at 13.97 p/kWh CANNOT take a "
        "battery PPA at any discount; a new grid connection for merchant trading does not clear "
        "the bar at Central and is an option to price, not a case to underwrite.",
    ),
    (
        3,
        S1,
        "Bishops Cleeve — ~7-9% levered Central, rising to ~17-20% Optimistic. THE REFRAME'S BIG "
        "WINNER: it holds the estate's only >=1 MW export route, already built and carrying "
        "6.7 GWh/yr. Under the old avoided-cost model this site looked worst; on the merchant + "
        "PPA "
        "model it is the only battery whose Central case approaches fundable.",
    ),
    (
        4,
        S4,
        "Ewelme — fails at Central (~1-3%), clears at Optimistic (~14-17%). The 750 kVA headroom "
        "is "
        "real, but at 0.6 MW the PPA obligation consumes the entire throughput so no merchant "
        "energy leg exists, and the sub-1MW cost stack still bites. An Optimistic-case project.",
    ),
    (
        5,
        S3,
        "Knowl Hill — ~0-2% levered Central, fails. 325 kVA binds everything and the PPA consumes "
        "all throughput. Clears only at Optimistic (~9-11%) with a connection upgrade. Hold.",
    ),
)

# The three levers that genuinely move the estate's Central case, in priority order.
VALUE_LEVERS: tuple[str, ...] = (
    "Bishops Cleeve export and genset-charging confirmation — it is the only >=1 MW market access "
    "in the estate and the only battery whose Central case approaches fundable.",
    "The Knowl Hill tariff basis and the Ewelme MIC status — two documents that decide whether "
    "either site has a project at all.",
    "Attacking the GBP250k per-site fixed capex plus the GBP700k/MW sub-1MW cost stack. This is "
    "the single biggest destroyer of value across the estate: every site except Tanhouse is "
    "sub-1MW, where fixed costs dominate. Portfolio procurement across all five sites is "
    "the lever.",
)


def evaluate_all() -> list[SiteResult]:
    """Every proposed site x case, in declaration order. Deterministic."""
    return [evaluate_site(c) for c in SITE_CASES if c.proposed]


def cases_for(site_key: str) -> list[SiteCase]:
    return [c for c in SITE_CASES if c.site_key == site_key]
