"""Layer 1 (frontier map) and Layer 2 (per-archetype inverse solve).

The integrity rule, per the Fable advisor's design: when the revenue required to hit the target
exceeds what the GB stack can defensibly pay, the solver returns **"infeasible above defensible
bounds"** rather than a number. It must be incapable of doing what Model_v5's `r34` did.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridq.portfolio_requirements.assumptions import (
    BOUNDS,
    REVENUE_STACK,
    Archetype,
    FinancingTier,
    Tag,
    stack_total,
    worst_of,
)
from gridq.portfolio_requirements.kernel import CashflowResult, evaluate


def net_revenue_ceiling(level: str) -> float:
    """Net £/MW/yr the GB stack can pay at ``'central'|'favourable'|'stretch'``.

    Net = gross x co-optimisation factor. The Stretch ceiling is the hard wall: above it the
    solver reports infeasible rather than returning a required-revenue figure.
    """
    b = BOUNDS
    factor = {
        "central": b.cooptimisation_central,
        "favourable": b.cooptimisation_favourable,
        "stretch": b.cooptimisation_stretch,
    }[level]
    return stack_total(level) * factor


def classify_revenue(net_rev_per_mw: float) -> Tag:
    """Tag a net £/MW/yr figure against the stack's Central/Favourable/Stretch ceilings."""
    if net_rev_per_mw <= net_revenue_ceiling("central") + 1e-9:
        return Tag.CENTRAL
    if net_rev_per_mw <= net_revenue_ceiling("favourable") + 1e-9:
        return Tag.FAVOURABLE
    if net_rev_per_mw <= net_revenue_ceiling("stretch") + 1e-9:
        return Tag.STRETCH
    return Tag.INDEFENSIBLE


def load_coupled_capacity_mw(site_load_gwh: float, n_sites: int, coverage: float) -> float:
    """Max fleet MW the host load can actually support.

    A BTM battery cannot cycle more than the site's load permits: annual discharge is capped at
    ``site load x coverage``, and each MW needs ``throughput_cap`` MWh/yr to earn its modelled
    revenue.
    """
    annual_discharge_mwh = site_load_gwh * 1000.0 * n_sites * coverage
    return annual_discharge_mwh / BOUNDS.throughput_cap_mwh_per_mw


# --------------------------------------------------------------------------------------------
# Layer 1 — frontier
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierPoint:
    capex_per_mw: float
    net_rev_per_mw: float
    unlevered_irr: float | None
    levered_irr: float | None
    dscr_avg: float
    gearing_achieved: float
    revenue_tag: Tag
    defensible: bool


def frontier(
    *,
    tier: FinancingTier,
    fleet_mw: float,
    n_sites: int,
    opex_per_mw: float,
    capex_lo: float = 300_000.0,
    capex_hi: float = 800_000.0,
    capex_steps: int = 11,
    rev_lo: float = 40_000.0,
    rev_hi: float = 140_000.0,
    rev_steps: int = 21,
) -> list[FrontierPoint]:
    """Grid-evaluate capex/MW x net revenue/MW. Deterministic; no search."""
    pts: list[FrontierPoint] = []
    for i in range(capex_steps):
        capex = capex_lo + (capex_hi - capex_lo) * i / (capex_steps - 1)
        for j in range(rev_steps):
            rev = rev_lo + (rev_hi - rev_lo) * j / (rev_steps - 1)
            r = evaluate(
                capex_per_mw=capex,
                net_rev_yr1_per_mw=rev,
                opex_per_mw=opex_per_mw,
                tier=tier,
                fleet_mw=fleet_mw,
                n_sites=n_sites,
            )
            tag = classify_revenue(rev)
            pts.append(
                FrontierPoint(
                    capex_per_mw=capex,
                    net_rev_per_mw=rev,
                    unlevered_irr=r.unlevered_irr,
                    levered_irr=r.levered_irr,
                    dscr_avg=r.dscr_avg,
                    gearing_achieved=r.gearing_achieved,
                    revenue_tag=tag,
                    defensible=(
                        tag.rank <= Tag.FAVOURABLE.rank and capex >= BOUNDS.capex_floor(fleet_mw)
                    ),
                )
            )
    return pts


# --------------------------------------------------------------------------------------------
# Layer 2 — inverse solve
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementResult:
    """What one archetype would need to reach the target levered IRR."""

    archetype_key: str
    label: str
    target_levered_irr: float
    feasible: bool
    reason: str
    required_net_rev_per_mw: float | None
    required_unlevered_irr: float | None
    revenue_tag: Tag
    multiple_of_central: float | None
    result_at_required: CashflowResult | None
    central_case: CashflowResult
    load_cap_mw: float | None

    @property
    def verdict_tag(self) -> Tag:
        return self.revenue_tag


def required_revenue_for_target(
    *,
    archetype: Archetype,
    target_levered_irr: float = 0.20,
    search_hi: float = 400_000.0,
) -> RequirementResult:
    """Root-find the net £/MW/yr at which ``archetype`` reaches ``target_levered_irr``.

    Returns ``feasible=False`` with a reason when the required revenue exceeds the Stretch ceiling
    (or when no revenue in range reaches the target at all).
    """
    central_rev = net_revenue_ceiling("central")
    central_case = evaluate(
        capex_per_mw=archetype.capex_per_mw,
        net_rev_yr1_per_mw=central_rev,
        opex_per_mw=archetype.opex_per_mw,
        tier=archetype.tier,
        fleet_mw=archetype.fleet_mw,
        n_sites=archetype.n_sites,
    )

    load_cap = None
    if archetype.site_load_gwh_per_site is not None:
        load_cap = load_coupled_capacity_mw(
            archetype.site_load_gwh_per_site,
            archetype.n_sites,
            BOUNDS.load_coverage_central,
        )

    def lev_at(rev: float) -> float:
        r = evaluate(
            capex_per_mw=archetype.capex_per_mw,
            net_rev_yr1_per_mw=rev,
            opex_per_mw=archetype.opex_per_mw,
            tier=archetype.tier,
            fleet_mw=archetype.fleet_mw,
            n_sites=archetype.n_sites,
        )
        return r.levered_irr if r.levered_irr is not None else -1.0

    lo, hi = 1_000.0, search_hi
    if lev_at(hi) < target_levered_irr:
        return RequirementResult(
            archetype_key=archetype.key,
            label=archetype.label,
            target_levered_irr=target_levered_irr,
            feasible=False,
            reason=(
                f"target unreachable at any revenue up to GBP{search_hi:,.0f}/MW/yr — "
                "the capex/opex/financing structure cannot deliver it"
            ),
            required_net_rev_per_mw=None,
            required_unlevered_irr=None,
            revenue_tag=Tag.INDEFENSIBLE,
            multiple_of_central=None,
            result_at_required=None,
            central_case=central_case,
            load_cap_mw=load_cap,
        )

    for _ in range(200):
        mid = (lo + hi) / 2.0
        if lev_at(mid) < target_levered_irr:
            lo = mid
        else:
            hi = mid
    required = (lo + hi) / 2.0

    at_required = evaluate(
        capex_per_mw=archetype.capex_per_mw,
        net_rev_yr1_per_mw=required,
        opex_per_mw=archetype.opex_per_mw,
        tier=archetype.tier,
        fleet_mw=archetype.fleet_mw,
        n_sites=archetype.n_sites,
    )
    tag = classify_revenue(required)
    feasible = tag.rank <= Tag.STRETCH.rank
    reason = (
        "reachable within the defensible envelope"
        if tag.rank <= Tag.FAVOURABLE.rank
        else (
            "reachable only at the Stretch ceiling — every revenue line simultaneously at "
            "Favourable-to-Stretch"
            if feasible
            else (
                f"required revenue GBP{required:,.0f}/MW/yr exceeds the Stretch ceiling of "
                f"GBP{net_revenue_ceiling('stretch'):,.0f}/MW/yr — INFEASIBLE, no number is "
                "defensible here"
            )
        )
    )
    return RequirementResult(
        archetype_key=archetype.key,
        label=archetype.label,
        target_levered_irr=target_levered_irr,
        feasible=feasible,
        reason=reason,
        required_net_rev_per_mw=required,
        required_unlevered_irr=at_required.unlevered_irr,
        revenue_tag=tag,
        multiple_of_central=required / central_rev if central_rev else None,
        result_at_required=at_required,
        central_case=central_case,
        load_cap_mw=load_cap,
    )


# --------------------------------------------------------------------------------------------
# The spec card — the headline deliverable
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecCard:
    """The 8 numbers that matter, for the minimal-stretch point on the target contour."""

    target_levered_irr: float
    required_unlevered_irr: float
    required_net_rev_per_mw: float
    implied_captured_spread_gbp_per_mwh: float
    max_capex_per_mw: float
    financing: str
    gearing_achieved: float
    dscr_avg: float
    min_fleet_mw: float
    n_sites: int
    host_archetype: str
    revenue_tag: Tag
    multiple_of_central: float
    feasible: bool


def solve_spec_card(*, archetype: Archetype, target_levered_irr: float = 0.20) -> SpecCard:
    """Build the required-portfolio spec card for ``archetype``."""
    req = required_revenue_for_target(archetype=archetype, target_levered_irr=target_levered_irr)
    rev = req.required_net_rev_per_mw or 0.0
    r = req.result_at_required
    spread = rev / BOUNDS.throughput_cap_mwh_per_mw if rev else 0.0
    return SpecCard(
        target_levered_irr=target_levered_irr,
        required_unlevered_irr=req.required_unlevered_irr or 0.0,
        required_net_rev_per_mw=rev,
        implied_captured_spread_gbp_per_mwh=spread,
        max_capex_per_mw=archetype.capex_per_mw,
        financing=(
            f"{archetype.tier.name}: {archetype.tier.rate * 100:.1f}%, "
            f"{archetype.tier.tenor_years}y, DSCR-sized at {archetype.tier.dscr_target:.2f}x"
        ),
        gearing_achieved=r.gearing_achieved if r else 0.0,
        dscr_avg=r.dscr_avg if r else 0.0,
        min_fleet_mw=archetype.fleet_mw,
        n_sites=archetype.n_sites,
        host_archetype=archetype.note,
        revenue_tag=req.revenue_tag,
        multiple_of_central=req.multiple_of_central or 0.0,
        feasible=req.feasible,
    )


def verdict(results: list[RequirementResult]) -> str:
    """One-paragraph honest verdict across archetypes."""
    reachable = [r for r in results if r.feasible and r.revenue_tag.rank <= Tag.FAVOURABLE.rank]
    stretch_only = [r for r in results if r.feasible and r.revenue_tag == Tag.STRETCH]
    infeasible = [r for r in results if not r.feasible]
    parts: list[str] = []
    if reachable:
        parts.append(
            "Reachable within the defensible envelope: "
            + ", ".join(r.label for r in reachable)
            + "."
        )
    if stretch_only:
        parts.append(
            "Reachable only at the Stretch ceiling (every revenue line simultaneously "
            "Favourable-to-Stretch): " + ", ".join(r.label for r in stretch_only) + "."
        )
    if infeasible:
        parts.append(
            "INFEASIBLE above defensible bounds: " + ", ".join(r.label for r in infeasible) + "."
        )
    if not reachable:
        parts.append(
            "No archetype reaches the target on Central inputs. The realistic own-and-operate "
            "ceiling for GB BTM in 2026 is roughly 10-14% levered; 20% is the property of a "
            "top-decile host archetype, not of portfolio scale (IRR is scale-invariant above the "
            "fixed-cost floor)."
        )
    return " ".join(parts)


def stack_gap_rows() -> list[tuple[str, float, float, float, float, str]]:
    """Per-stream gap table: (name, model_v5, central, favourable, stretch, note)."""
    return [
        (s.name, s.model_v5_central, s.central, s.favourable, s.stretch, s.note)
        for s in REVENUE_STACK
    ]


def worst_tag_across(tags: list[Tag]) -> Tag:
    return worst_of(tags)
