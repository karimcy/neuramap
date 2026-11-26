"""Tagged assumption set for the portfolio requirements solver.

Every number carries a ``(value, tag, source)`` triple. ``tag`` is the classification the drive
brief requires — ``CENTRAL`` / ``FAVOURABLE`` / ``STRETCH`` — plus ``INDEFENSIBLE`` for values that
exist only to be shown as *rejected* (Model_v5's frequency-response line is the worked example).

All bounds and stream values below are the Fable advisor's design values (2026-08-05), which
revise Model_v5's Central where that Central was not defensible for 2026 GB BTM. Where the advisor
recorded uncertainty it is carried in ``source`` rather than smoothed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Tag(StrEnum):
    """Assumption classification. Order matters — ``worst_of`` relies on it."""

    CENTRAL = "Central"
    FAVOURABLE = "Favourable"
    STRETCH = "Stretch"
    INDEFENSIBLE = "Indefensible"

    @property
    def rank(self) -> int:
        return {"Central": 0, "Favourable": 1, "Stretch": 2, "Indefensible": 3}[self.value]


def worst_of(tags: list[Tag]) -> Tag:
    """The least defensible tag in a set — an archetype is only as credible as its worst line."""
    return max(tags, key=lambda t: t.rank) if tags else Tag.CENTRAL


@dataclass(frozen=True)
class Assumption:
    """One traceable input: its value, how defensible it is, and where it came from."""

    name: str
    value: float
    unit: str
    tag: Tag
    source: str

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return f"{self.name} = {self.value:,.4g} {self.unit} [{self.tag.value}] — {self.source}"


# --------------------------------------------------------------------------------------------
# Revenue stack — GB 2026, 2-hour BTM battery, £/MW/yr GROSS (before the co-optimisation haircut)
#
# Each stream gives Central / Favourable / Stretch. Model_v5's Central is recorded alongside so the
# gap table can show where this module disagrees with the reference model, and why.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueStream:
    name: str
    central: float
    favourable: float
    stretch: float
    model_v5_central: float
    note: str


REVENUE_STACK: tuple[RevenueStream, ...] = (
    RevenueStream(
        name="Wholesale / ToU arbitrage + avoided import",
        central=29_500.0,
        favourable=42_000.0,
        stretch=99_000.0,
        model_v5_central=35_000.0,
        note=(
            "Spread-derived, not a constant: captured spread x load-coupled throughput. "
            "~GBP45/MWh Central for a decent grid-supplied host; GBP65/MWh Favourable; a "
            "high-differential host (32p peak / 12p off-peak, net of 88% round-trip) reaches "
            "GBP140-180/MWh = 77-99k Stretch. Behind a 13.97p private wire this line is only "
            "~GBP20k — which is why Tanhouse/Colnbrook are structurally poor hosts."
        ),
    ),
    RevenueStream(
        name="DUoS red-band avoidance",
        central=3_500.0,
        favourable=6_000.0,
        stretch=9_000.0,
        model_v5_central=6_000.0,
        note=(
            "Post-TCR the residual is fixed; only the red unit rate is avoidable. Model_v5's "
            "GBP6k is Favourable, not Central. Stretch requires HV on a high-red DNO area."
        ),
    ),
    RevenueStream(
        name="Capacity Market (de-rated, 2h)",
        central=9_500.0,
        favourable=13_000.0,
        stretch=13_000.0,
        model_v5_central=5_643.0,
        note=(
            "Recent T-4 clears GBP40-65/kW x 20.9% 2h de-rating. Model_v5 is the one line "
            "that is UNDERSTATED (it used GBP27.10/kW). Forward clearing is genuinely uncertain."
        ),
    ),
    RevenueStream(
        name="Frequency response (DC / DM / DR)",
        central=5_000.0,
        favourable=9_000.0,
        stretch=18_000.0,
        model_v5_central=18_000.0,
        note=(
            "Saturated since 2023; BTM availability conflicts with the peak-shaving window; "
            "aggregator fees apply. Model_v5's GBP18k is a 2021 number wearing a 2026 date — the "
            "single largest indefensible line in the reference model."
        ),
    ),
    RevenueStream(
        name="Balancing Mechanism",
        central=3_000.0,
        favourable=8_000.0,
        stretch=12_000.0,
        model_v5_central=8_000.0,
        note=(
            "Net of 15-20% aggregator share and skip-rate risk. Gated on route-to-market access "
            "(repo blocker B002) — must stay out of any firm/floor case."
        ),
    ),
    RevenueStream(
        name="Local flexibility (DSO / Piclo)",
        central=1_000.0,
        favourable=3_000.0,
        stretch=3_000.0,
        model_v5_central=2_000.0,
        note=(
            "Tender availability is a lottery by DNO area. GBP2k where actually contracted is "
            "fine; GBP2k assumed estate-wide is Favourable."
        ),
    ),
)


def stack_total(level: str) -> float:
    """Gross £/MW/yr at ``'central'`` | ``'favourable'`` | ``'stretch'``."""
    return sum(getattr(s, level) for s in REVENUE_STACK)


# --------------------------------------------------------------------------------------------
# Financing tiers — deterministic credit -> (rate, tenor, DSCR floor, gearing cap)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FinancingTier:
    """A debt tier. ``dscr_target`` sizes the debt; ``gearing_cap`` only ever binds downward."""

    name: str
    rate: float
    tenor_years: int
    dscr_target: float
    dscr_floor_min: float
    gearing_cap: float
    firm_revenue_share_required: float
    source: str


MERCHANT_TIER = FinancingTier(
    name="Merchant-heavy (<50% firm revenue)",
    rate=0.090,
    tenor_years=8,
    dscr_target=1.40,
    dscr_floor_min=1.15,
    gearing_cap=0.60,
    firm_revenue_share_required=0.0,
    source="Fable advisor design: merchant BTM 8.5-9.5%, tenor 7-10y, contracted-basis DSCR 1.40x",
)

CONTRACTED_TIER = FinancingTier(
    name="Contracted-heavy (>=60% firm revenue)",
    rate=0.070,
    tenor_years=10,
    dscr_target=1.30,
    dscr_floor_min=1.15,
    gearing_cap=0.60,
    firm_revenue_share_required=0.60,
    source="Fable advisor design: contracted-backed 6.5-7.5%, 10y, 1.30x avg P90 DSCR",
)

MODEL_V5_TIER = FinancingTier(
    name="Model_v5 as published (asserted 60% gearing)",
    rate=0.085,
    tenor_years=10,
    dscr_target=1.30,
    dscr_floor_min=1.15,
    gearing_cap=0.60,
    firm_revenue_share_required=0.0,
    source="Sim Work/Model_v5.xlsx Assumptions G37-G39 (SONIA 3.7% + 480bps)",
)


# --------------------------------------------------------------------------------------------
# Hard bounds the solver refuses to cross (Fable advisor constraint table)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """Numeric guardrails. Crossing one makes a result ``infeasible``, never a returned number."""

    # Throughput
    cycles_per_year: float = 365.0
    usable_mwh_per_mw: float = 1.8
    throughput_cap_mwh_per_mw: float = 657.0  # 365 x 1.8

    # BTM load coupling — a battery cannot cycle more than the host supports
    load_coverage_central: float = 0.35
    load_coverage_favourable: float = 0.50

    # Capex floors (£/MW), by fleet size
    capex_floor_sub_1mw: float = 700_000.0
    capex_floor_btm_1_5mw: float = 560_000.0
    capex_floor_ge_5mw: float = 480_000.0
    capex_floor_grid_scale: float = 400_000.0
    site_fixed_capex: float = 250_000.0  # connection, ICP, civils, metering (range 150-400k)

    # Opex floors (£/MW/yr)
    opex_floor_sub_5mw: float = 20_000.0
    opex_floor_ge_10mw: float = 15_000.0
    site_fixed_opex: float = 6_000.0

    # Asset / revenue path
    asset_life_years: int = 15
    degradation_per_year: float = 0.02
    cpi_escalation: float = 0.025
    cooptimisation_central: float = 0.80
    cooptimisation_favourable: float = 0.88
    cooptimisation_stretch: float = 0.92

    # P90 haircut on CFADS for debt sizing
    p90_factor_merchant: float = 0.85
    p90_factor_contracted: float = 0.92

    # NPV discount rate (Model_v5 G40)
    discount_rate_npv: float = 0.08

    def capex_floor(self, fleet_mw: float) -> float:
        if fleet_mw < 1.0:
            return self.capex_floor_sub_1mw
        if fleet_mw < 5.0:
            return self.capex_floor_btm_1_5mw
        return self.capex_floor_ge_5mw

    def opex_floor(self, fleet_mw: float) -> float:
        return self.opex_floor_ge_10mw if fleet_mw >= 10.0 else self.opex_floor_sub_5mw


BOUNDS = Bounds()


# --------------------------------------------------------------------------------------------
# Assumption sets and archetypes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AssumptionSet:
    """A named, versioned bundle of the inputs a run used."""

    version: str
    items: tuple[Assumption, ...] = field(default_factory=tuple)

    def tag(self) -> Tag:
        return worst_of([a.tag for a in self.items])

    def as_rows(self) -> list[tuple[str, str, str, str]]:
        return [(a.name, f"{a.value:,.4g} {a.unit}", a.tag.value, a.source) for a in self.items]


def central_set() -> AssumptionSet:
    """The defensible Central basis — what the module treats as *realistic numbers*."""
    b = BOUNDS
    items = [
        Assumption(
            "Gross revenue stack (Central)",
            stack_total("central"),
            "GBP/MW/yr",
            Tag.CENTRAL,
            "Fable advisor 2026 GB BTM per-stream Central (see REVENUE_STACK)",
        ),
        Assumption(
            "Co-optimisation factor",
            b.cooptimisation_central,
            "x gross",
            Tag.CENTRAL,
            "Stacking FR against peak-shaving costs more than Model_v5's 12%",
        ),
        Assumption(
            "Fixed opex",
            b.opex_floor_sub_5mw,
            "GBP/MW/yr",
            Tag.CENTRAL,
            "Insurance, comms, aggregator minimums at <5 MW",
        ),
        Assumption(
            "Per-site fixed opex",
            b.site_fixed_opex,
            "GBP/site/yr",
            Tag.CENTRAL,
            "Fable design",
        ),
        Assumption(
            "Per-site fixed capex",
            b.site_fixed_capex,
            "GBP/site",
            Tag.CENTRAL,
            "Connection, ICP, civils, metering (range 150-400k; +/-20% uncertain)",
        ),
        Assumption(
            "Degradation",
            b.degradation_per_year,
            "/yr",
            Tag.CENTRAL,
            "Model_v5 G27, 2%/yr",
        ),
        Assumption(
            "CPI escalation",
            b.cpi_escalation,
            "/yr",
            Tag.CENTRAL,
            "Model_v5 G21, lower of CPI/2.5%",
        ),
        Assumption(
            "Asset life",
            float(b.asset_life_years),
            "years",
            Tag.CENTRAL,
            "Model_v5 G16",
        ),
        Assumption(
            "Throughput cap",
            b.throughput_cap_mwh_per_mw,
            "MWh/MW/yr",
            Tag.CENTRAL,
            "365 cycles x 1.8 MWh usable (Model_v5 G26/G45)",
        ),
        Assumption(
            "BTM load coverage",
            b.load_coverage_central,
            "x site load",
            Tag.CENTRAL,
            "Battery serves peak windows, not baseload (+/-0.1 uncertain)",
        ),
    ]
    return AssumptionSet(version="pr_central_v1", items=tuple(items))


@dataclass(frozen=True)
class Archetype:
    """A named host/portfolio shape the solver can be asked about."""

    key: str
    label: str
    fleet_mw: float
    n_sites: int
    capex_per_mw: float
    opex_per_mw: float
    tier: FinancingTier
    site_load_gwh_per_site: float | None
    peak_tariff_p_per_kwh: float | None
    note: str


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        key="grundon_harwell_estate",
        label="Grundon + Harwell estate (the five real sites)",
        fleet_mw=3.0,
        n_sites=5,
        capex_per_mw=BOUNDS.capex_floor_btm_1_5mw,
        opex_per_mw=BOUNDS.opex_floor_sub_5mw,
        tier=MERCHANT_TIER,
        site_load_gwh_per_site=1.48,  # 7.40 GWh across 5 sites
        peak_tariff_p_per_kwh=None,
        note=(
            "Measured: 6.49 GWh/yr across four Grundon sites + 0.91 GWh Harwell. 73% of load sits "
            "behind a 13.97 p/kWh EfW private wire, two sites run on declining LFG, Ewelme is at "
            "8.7% connection utilisation, Harwell is flat at 43% load factor. Close to the "
            "anti-archetype for BTM battery value."
        ),
    ),
    Archetype(
        key="model_v5_central",
        label="Model_v5 Central (100 MW, as Sim modelled it)",
        fleet_mw=100.0,
        n_sites=25,
        capex_per_mw=452_000.0,
        opex_per_mw=17_000.0,
        tier=MODEL_V5_TIER,
        site_load_gwh_per_site=None,
        peak_tariff_p_per_kwh=None,
        note="Reference point. Capex is below this module's small-BTM floor of GBP560k/MW.",
    ),
    Archetype(
        key="top_decile_host",
        label="Top-decile grid-supplied industrial host portfolio",
        fleet_mw=10.0,
        n_sites=4,
        capex_per_mw=500_000.0,
        opex_per_mw=17_000.0,
        tier=CONTRACTED_TIER,
        site_load_gwh_per_site=4.0,
        peak_tariff_p_per_kwh=32.0,
        note=(
            "The archetype the spec card describes: grid-supplied industrial, >=20p peak/off-peak "
            "differential, evening-weighted load >=4 GWh/yr/site, HV with headroom, >=5 MW "
            "installs for procurement leverage, contracted-backed 7% debt."
        ),
    ),
)
