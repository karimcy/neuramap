"""Tests for the portfolio requirements solver.

The load-bearing tests are the *integrity* ones: the kernel must be structurally incapable of the
error found in ``Sim Work/Model_v5.xlsx`` (asserting 60% gearing against a sub-1.0x DSCR, and
crediting interest expense as income), and the solver must refuse to return a number when the
answer would sit above defensible bounds.
"""

from __future__ import annotations

import pytest

from gridq.portfolio_requirements import (
    ARCHETYPES,
    REVENUE_STACK,
    Tag,
    central_set,
    evaluate,
    frontier,
    irr,
    required_revenue_for_target,
    solve_spec_card,
    verdict,
)
from gridq.portfolio_requirements.assumptions import (
    BOUNDS,
    CONTRACTED_TIER,
    MERCHANT_TIER,
    MODEL_V5_TIER,
    stack_total,
    worst_of,
)
from gridq.portfolio_requirements.kernel import annuity_factor
from gridq.portfolio_requirements.solver import (
    classify_revenue,
    load_coupled_capacity_mw,
    net_revenue_ceiling,
)


# --------------------------------------------------------------------------- IRR primitives
def test_irr_recovers_a_known_rate() -> None:
    # £100 out, £110 back in one year -> 10%.
    assert irr([-100.0, 110.0]) == pytest.approx(0.10, abs=1e-6)


def test_irr_returns_none_when_never_crosses_zero() -> None:
    # All-negative cashflows: NPV is negative at every rate, so no root exists in the bracket.
    # (Note [-100, 1, 1] *does* have a root near -89% — as the rate approaches -1 the discounted
    # tail diverges — so it is not a valid no-root case.)
    assert irr([-100.0, -1.0, -1.0]) is None


def test_annuity_factor_matches_closed_form() -> None:
    # 10 years at 7%: (1 - 1.07^-10)/0.07
    assert annuity_factor(0.07, 10) == pytest.approx(7.023581, abs=1e-5)


def test_annuity_factor_degenerates_to_years_at_zero_rate() -> None:
    assert annuity_factor(0.0, 10) == 10.0


# --------------------------------------------------------------------------- integrity guarantees
def test_debt_is_dscr_sized_never_exceeds_gearing_cap() -> None:
    r = evaluate(
        capex_per_mw=500_000.0,
        net_rev_yr1_per_mw=300_000.0,  # absurdly high -> cap must bind
        opex_per_mw=17_000.0,
        tier=CONTRACTED_TIER,
        fleet_mw=10.0,
        n_sites=4,
    )
    assert r.gearing_achieved <= CONTRACTED_TIER.gearing_cap + 1e-9
    assert r.binding_constraint == "gearing_cap"


def test_thin_cashflow_is_dscr_bound_not_gearing_bound() -> None:
    """The Model_v5 pathology: weak cashflow must NOT support 60% gearing."""
    r = evaluate(
        capex_per_mw=452_000.0,
        net_rev_yr1_per_mw=65_690.0,  # Model_v5's own Central
        opex_per_mw=17_000.0,
        tier=MODEL_V5_TIER,
        fleet_mw=100.0,
        n_sites=25,
    )
    assert r.binding_constraint == "dscr"
    assert r.gearing_achieved < 0.60, "Model_v5's asserted 60% gearing must not be reachable"


def test_dscr_never_falls_below_target_in_year_one() -> None:
    """Debt is sized off Year-1 P90 CFADS at the target DSCR, so Year-1 cover cannot be short."""
    for tier in (MERCHANT_TIER, CONTRACTED_TIER, MODEL_V5_TIER):
        r = evaluate(
            capex_per_mw=500_000.0,
            net_rev_yr1_per_mw=80_000.0,
            opex_per_mw=17_000.0,
            tier=tier,
            fleet_mw=10.0,
            n_sites=4,
        )
        if r.debt > 0 and r.binding_constraint == "dscr":
            assert r.dscr_path[0] >= tier.dscr_target - 1e-6


def test_leverage_is_dilutive_when_unlevered_below_cost_of_debt() -> None:
    """The structural finding: gearing subtracts when the asset return is under the debt rate."""
    r = evaluate(
        capex_per_mw=560_000.0,
        net_rev_yr1_per_mw=55_000.0,
        opex_per_mw=20_000.0,
        tier=MODEL_V5_TIER,  # 8.5%
        fleet_mw=5.0,
        n_sites=3,
    )
    assert r.unlevered_irr is not None and r.unlevered_irr < MODEL_V5_TIER.rate
    assert not r.leverage_accretive
    assert r.levered_minus_unlevered_pp is not None
    assert r.levered_minus_unlevered_pp < 0


def test_leverage_is_accretive_when_unlevered_above_cost_of_debt() -> None:
    r = evaluate(
        capex_per_mw=400_000.0,
        net_rev_yr1_per_mw=130_000.0,
        opex_per_mw=15_000.0,
        tier=CONTRACTED_TIER,  # 7.0%
        fleet_mw=20.0,
        n_sites=4,
    )
    assert r.unlevered_irr is not None and r.unlevered_irr > CONTRACTED_TIER.rate
    assert r.leverage_accretive


def test_equity_plus_debt_equals_capex() -> None:
    """No double-counting of the debt draw (Model_v5 defect D2)."""
    r = evaluate(
        capex_per_mw=500_000.0,
        net_rev_yr1_per_mw=90_000.0,
        opex_per_mw=17_000.0,
        tier=CONTRACTED_TIER,
        fleet_mw=10.0,
        n_sites=4,
    )
    assert r.debt + r.equity == pytest.approx(r.capex_total, rel=1e-12)
    assert r.levered_cashflows[0] == pytest.approx(-r.equity, rel=1e-12)


def test_interest_reduces_equity_cashflow_never_increases_it() -> None:
    """Model_v5 defect D1 in test form: levered CF must be below unlevered CF while debt serves."""
    r = evaluate(
        capex_per_mw=500_000.0,
        net_rev_yr1_per_mw=90_000.0,
        opex_per_mw=17_000.0,
        tier=CONTRACTED_TIER,
        fleet_mw=10.0,
        n_sites=4,
    )
    for y in range(1, CONTRACTED_TIER.tenor_years + 1):
        assert r.levered_cashflows[y] < r.unlevered_cashflows[y]


# --------------------------------------------------------------------------- determinism
def test_evaluate_is_deterministic() -> None:
    kw = dict(
        capex_per_mw=500_000.0,
        net_rev_yr1_per_mw=90_000.0,
        opex_per_mw=17_000.0,
        tier=CONTRACTED_TIER,
        fleet_mw=10.0,
        n_sites=4,
    )
    a, b = evaluate(**kw), evaluate(**kw)  # type: ignore[arg-type]
    assert a == b


def test_required_revenue_is_deterministic() -> None:
    arch = ARCHETYPES[2]
    a = required_revenue_for_target(archetype=arch, target_levered_irr=0.20)
    b = required_revenue_for_target(archetype=arch, target_levered_irr=0.20)
    assert a.required_net_rev_per_mw == b.required_net_rev_per_mw


# --------------------------------------------------------------------------- assumption tagging
def test_revenue_ceilings_are_ordered() -> None:
    assert (
        net_revenue_ceiling("central")
        < net_revenue_ceiling("favourable")
        < net_revenue_ceiling("stretch")
    )


def test_classify_revenue_boundaries() -> None:
    assert classify_revenue(net_revenue_ceiling("central")) == Tag.CENTRAL
    assert classify_revenue(net_revenue_ceiling("favourable")) == Tag.FAVOURABLE
    assert classify_revenue(net_revenue_ceiling("stretch")) == Tag.STRETCH
    assert classify_revenue(net_revenue_ceiling("stretch") + 1.0) == Tag.INDEFENSIBLE


def test_worst_of_picks_least_defensible() -> None:
    assert worst_of([Tag.CENTRAL, Tag.STRETCH, Tag.FAVOURABLE]) == Tag.STRETCH
    assert worst_of([Tag.CENTRAL]) == Tag.CENTRAL
    assert worst_of([]) == Tag.CENTRAL


def test_every_revenue_stream_is_ordered_and_sourced() -> None:
    for s in REVENUE_STACK:
        assert s.central <= s.favourable <= s.stretch, s.name
        assert s.note.strip(), s.name


def test_central_assumption_set_is_fully_tagged_and_sourced() -> None:
    s = central_set()
    assert s.version == "pr_central_v1"
    assert s.items
    for a in s.items:
        assert a.source.strip(), a.name
        assert isinstance(a.tag, Tag)
    assert s.tag() == Tag.CENTRAL


def test_stack_total_matches_sum_of_streams() -> None:
    assert stack_total("central") == sum(s.central for s in REVENUE_STACK)


# --------------------------------------------------------------------------- the headline findings
def test_twenty_percent_is_not_reachable_in_the_defensible_region() -> None:
    """THE finding: points that reach 20% levered exist, but none of them are defensible."""
    pts = frontier(tier=CONTRACTED_TIER, fleet_mw=10.0, n_sites=4, opex_per_mw=17_000.0)
    reaching = [p for p in pts if p.levered_irr is not None and p.levered_irr >= 0.20]
    assert reaching, "expected some points to reach 20% somewhere on the grid"
    assert not [p for p in reaching if p.defensible], (
        "no point reaching 20% levered may be inside the defensible region"
    )


def test_real_estate_is_infeasible_for_twenty_percent() -> None:
    arch = next(a for a in ARCHETYPES if a.key == "grundon_harwell_estate")
    q = required_revenue_for_target(archetype=arch, target_levered_irr=0.20)
    assert not q.feasible
    assert q.revenue_tag == Tag.INDEFENSIBLE
    assert q.required_net_rev_per_mw is not None
    assert q.required_net_rev_per_mw > net_revenue_ceiling("stretch")
    assert "INFEASIBLE" in q.reason


def test_required_unlevered_for_twenty_percent_is_about_fourteen_percent() -> None:
    """Cross-checks the Fable advisor's independent 14-15% estimate."""
    arch = next(a for a in ARCHETYPES if a.key == "top_decile_host")
    q = required_revenue_for_target(archetype=arch, target_levered_irr=0.20)
    assert q.required_unlevered_irr is not None
    assert 0.12 < q.required_unlevered_irr < 0.16


def test_spec_card_is_internally_consistent() -> None:
    arch = next(a for a in ARCHETYPES if a.key == "top_decile_host")
    c = solve_spec_card(archetype=arch, target_levered_irr=0.20)
    assert c.revenue_tag == Tag.STRETCH
    assert c.implied_captured_spread_gbp_per_mwh == pytest.approx(
        c.required_net_rev_per_mw / BOUNDS.throughput_cap_mwh_per_mw, rel=1e-9
    )
    assert c.multiple_of_central > 1.0


def test_load_coupling_caps_fleet_size() -> None:
    # 6.49 GWh across 4 Grundon sites at 35% coverage / 657 MWh per MW.
    cap = load_coupled_capacity_mw(1.62, 4, BOUNDS.load_coverage_central)
    assert cap == pytest.approx(1.62 * 1000 * 4 * 0.35 / 657.0, rel=1e-9)
    assert cap < 4.0


def test_verdict_reports_infeasible_archetypes() -> None:
    results = [
        required_revenue_for_target(archetype=a, target_levered_irr=0.20) for a in ARCHETYPES
    ]
    text = verdict(results)
    assert "INFEASIBLE" in text
    assert "10-14% levered" in text


# --------------------------------------------------------------------------- site cases
def test_every_site_case_is_sourced_and_classified() -> None:
    from gridq.portfolio_requirements.sites import SITE_CASES

    for c in SITE_CASES:
        assert c.driver.strip(), c.site_key
        assert c.connection_basis.strip(), c.site_key
        assert c.binding_constraint.strip(), c.site_key
        if c.proposed:
            assert c.key_assumptions, f"{c.site_key}/{c.case} has no stated assumptions"
            assert c.capex_gbp > 0
            assert 0.0 <= c.battery_revenue_share <= 1.0
            assert 0.0 <= c.merchant_share <= 1.0


def test_site_evaluation_is_deterministic() -> None:
    from gridq.portfolio_requirements.sites import evaluate_all

    a = [(r.case.site_key, r.case.case, r.levered_irr) for r in evaluate_all()]
    b = [(r.case.site_key, r.case.case, r.levered_irr) for r in evaluate_all()]
    assert a == b


def test_debt_plus_equity_equals_capex_for_every_site_case() -> None:
    from gridq.portfolio_requirements.sites import evaluate_all

    for r in evaluate_all():
        assert r.debt + r.equity == pytest.approx(r.capex, rel=1e-12)


def test_thin_yield_never_supports_full_gearing() -> None:
    """The Model_v5 pathology, at site level: weak cashflow must not reach the 60% cap."""
    from gridq.portfolio_requirements.sites import evaluate_all

    for r in evaluate_all():
        if not r.clears_yield_bar:
            assert r.gearing < 0.60, f"{r.case.site_key}/{r.case.case} geared {r.gearing:.2f}"


def test_high_returns_are_flagged_suspect() -> None:
    """Anything above 25% levered must self-flag — Harwell is the worked example."""
    from gridq.portfolio_requirements.sites import evaluate_all

    flagged = [r for r in evaluate_all() if r.suspect_high_return]
    assert flagged, "expected Harwell solar to trip the suspect-return flag"
    for r in flagged:
        assert r.levered_irr is not None and r.levered_irr > 0.25
        # A suspect return must carry a Stretch (or worse) classification.
        assert r.case.tag.rank >= Tag.STRETCH.rank, (
            f"{r.case.site_key}/{r.case.case} returns >25% but is only tagged {r.case.tag.value}"
        )


def test_no_battery_proposed_where_there_is_no_grid_connection() -> None:
    """Tanhouse & Colnbrook has no grid connection; a merchant battery needs one built."""
    from gridq.portfolio_requirements.sites import cases_for

    for c in cases_for("tanhouse_colnbrook"):
        if c.bess_mw > 0:
            assert "NEW" in c.connection_basis or "connection" in c.binding_constraint, (
                "a battery at Tanhouse must explicitly depend on building a connection"
            )


def test_expected_ranges_cover_every_site_and_case() -> None:
    from gridq.portfolio_requirements.sites import CASES, EXPECTED_LEVERED, SITE_ORDER

    for key, _label in SITE_ORDER:
        assert key in EXPECTED_LEVERED
        assert len(EXPECTED_LEVERED[key]) == len(CASES)
