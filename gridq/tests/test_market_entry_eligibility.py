"""Market-entry eligibility, EAC/DFS settlement structure and the CM compiled-commitment rule.

Covers, with the sourced rule each test pins:

- **D-92** — consent alone opened the BM / DC-DM-DR gates with no eligibility check, so a 300 kW
  site could carry CONTRACTED Balancing Mechanism revenue into the P90 series the debt is sized on,
  for markets it cannot physically enter. Sourced minima: 1 MW per BM Unit, 1 MW per response unit,
  whole-MW sell orders.
- **E-06** — `MarketStream.DFS`, the only direct NESO route below 1 MW (0.1 MW minimum Registered
  Unit Capacity), including the piecewise performance factor K and the exclusivity rule that makes
  DFS an *alternative* to the streams D-92 removes rather than an addition to them.
- **D-158** — DC/DM/DR at EFA-block granularity with availability factor F and performance factor K,
  and the refusal to invent either.
- **D-116** — the ENA Open Networks local-flex taper (Grace Factor, Performance Multiplier,
  Payable-Over-Delivery cap) and the availability leg's data ask.
- **JSON-F-30** — the Capacity Market may not be CONTRACTED without a commitment that actually
  compiled into the dispatch.
- **MD-F-05 limb (b)** — contracted export is netted of the behind-the-meter value it cannibalised.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams, wholesale_contracted_stream_result
from gridq.schemas import Firmness, MarketStream, RateUnit
from gridq.value_stack import DispatchSummary, GateContext, default_registry, gates
from gridq.value_stack.gb_streams import (
    dfs_performance_factor,
    dfs_registered_unit_capacity_mw,
    eac_response_value,
    eligibility_threshold_mw,
    local_flex_availability_value,
    local_flex_utilisation_adjustment,
    market_entry_verdict,
    payable_delivery_fraction,
)

LIB = load_assumptions("gb_v0_1")
REG = default_registry()
SIZE_GATED = (MarketStream.BALANCING_MECHANISM, MarketStream.ANCILLARY_DC_DM_DR)


def _schedule(
    *,
    power_kw: float,
    deliverable_capacity_mw: float | None = None,
    tender_mwh: float | None = None,
) -> HalfHourlyDispatchSchedule:
    n = 48
    arr = lambda v: np.asarray(v, dtype=float)  # noqa: E731
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=arr([0.0] * n),
        discharge_kw=arr([0.0] * n),
        soe_kwh=arr([0.0] * n),
        site_load_kw=arr([100.0] * n),
        duos_rate_gbp_per_kwh=arr([0.1] * n),
        red_band_mask=np.zeros(n, dtype=bool),
        deliverable_capacity_mw=deliverable_capacity_mw,
        tender_window_discharge_mwh=tender_mwh,
    )


def _site(
    make_site: Callable[..., s.SiteModel],
    *,
    consents: tuple[s.RevenueStreamConsent, ...] = (),
    mec_kw: float | None = None,
) -> s.SiteModel:
    base = make_site()
    update: dict[str, object] = {"stream_consents": consents}
    if mec_kw is not None:
        update["connection"] = base.connection.model_copy(update={"mec_kw": mec_kw})
    return s.SiteModel.model_validate({**base.model_dump(mode="python"), **update})


def _consent(stream: MarketStream, rate: float) -> s.RevenueStreamConsent:
    return s.RevenueStreamConsent(
        stream=stream,
        consented=True,
        agreed_rate_gbp=rate,
        agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
    )


def _entry(streams: list[s.ValueStreamResult], sid: MarketStream) -> s.ValueStreamResult | None:
    return next((v for v in streams if v.stream == sid), None)


# --- D-92: the generic predicate ---------------------------------------------


def test_the_sourced_minima_are_1_mw_for_bm_and_response() -> None:
    # Register: "1 MW minimum BM Unit size"; "A minimum response capacity of 1 MW is required per
    # response unit". Both are the gate that excludes sub-1 MW BTM assets.
    assert eligibility_threshold_mw(MarketStream.BALANCING_MECHANISM) == 1.0
    assert eligibility_threshold_mw(MarketStream.ANCILLARY_DC_DM_DR) == 1.0


def test_the_capacity_market_prequalification_threshold_is_armed_at_one_mw() -> None:
    """The CM gate IS armed, on a sourced row — the earlier "unarmed" rationale was factually wrong.

    `docs/bess_constants_register.md` row "Minimum capacity threshold for prequalification": 1 MW of
    connection capacity (or DSR capacity), The Electricity Capacity Regulations 2014 (SI 2014/2043)
    regulation 15, "all CMU types" — "Directly excludes standalone BTM batteries below 1 MW from the
    CM unless aggregated into a multi-site DSR CMU". The code previously left this gate unarmed and
    justified it with the claim that no such row existed, which booked CM revenue on every sub-1 MW
    site in the live campaign for a market it cannot legally prequalify for.
    """
    assert eligibility_threshold_mw(MarketStream.CAPACITY_MARKET) == 1.0
    verdict = market_entry_verdict(MarketStream.CAPACITY_MARKET, unit_mw=0.2, portfolio_mw=None)
    assert verdict.route == "below_threshold"
    assert verdict.eligible is False


def test_the_cm_threshold_is_not_a_whole_mw_order_market() -> None:
    """CM is not truncated to integer MW — that rule is BM/EAC sell-order granularity, not CM."""
    verdict = market_entry_verdict(MarketStream.CAPACITY_MARKET, unit_mw=1.6, portfolio_mw=None)
    assert verdict.eligible is True
    assert verdict.route == "standalone"
    assert verdict.sellable_mw == 1.6  # NOT truncated to 1.0


def test_the_cm_aggregation_route_is_a_dsr_cmu_not_a_gsp_group() -> None:
    """The two GB aggregation routes have different rules; their caveats must not be swapped."""
    from gridq.value_stack.gb_streams import aggregation_route_notes

    verdict = market_entry_verdict(MarketStream.CAPACITY_MARKET, unit_mw=0.3, portfolio_mw=5.0)
    assert verdict.route == "aggregated"
    cm_notes = " ".join(aggregation_route_notes(verdict, MarketStream.CAPACITY_MARKET))
    assert "DSR CMU" in cm_notes
    assert "GSP Group" not in cm_notes
    bm_verdict = market_entry_verdict(
        MarketStream.BALANCING_MECHANISM, unit_mw=0.3, portfolio_mw=5.0
    )
    bm_notes = " ".join(aggregation_route_notes(bm_verdict, MarketStream.BALANCING_MECHANISM))
    assert "GSP GROUP" in bm_notes.upper()
    assert "DSR CMU" not in bm_notes


def test_a_sub_1_mw_site_cannot_prequalify_for_the_capacity_market(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Gate layer: the 300 kW default connection is barred, and says why, on both routes."""
    gate = REG.get(MarketStream.CAPACITY_MARKET).check_gates(
        _site(make_site), LIB, GateContext(deliverability_confirmed=True)
    )
    assert gate.gate_status.value == "conditional"
    assert gate.firmness != Firmness.FIRM
    blob = " ".join(gate.warnings)
    assert "NOT ELIGIBLE" in blob
    assert "SI 2014/2043" in blob
    # The bar must be described as prequalification, not as a de-rating haircut.
    assert "PREQUALIFICATION bar" in blob
    assert any("DSR CMU" in m for m in gate.missing_requirements)


def test_a_1_mw_connection_still_reaches_the_cm_deliverability_gate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The prequalification gate must not swallow the pre-existing deliverability logic."""
    site = _site(make_site, mec_kw=2_000.0)
    open_gate = REG.get(MarketStream.CAPACITY_MARKET).check_gates(
        site, LIB, GateContext(deliverability_confirmed=True)
    )
    assert open_gate.gate_status.value == "open"
    unconfirmed = REG.get(MarketStream.CAPACITY_MARKET).check_gates(site, LIB, GateContext())
    assert unconfirmed.gate_status.value == "conditional"
    assert any("deliverability" in w for w in unconfirmed.warnings)


def test_the_cm_money_is_zero_on_a_sub_1_mw_unit_on_both_paths(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Engine and proxy both price £0 — a barred market is worth nothing, not a de-rated fraction.

    This is the number that moves most on the live campaign: all five real sites are sub-1 MW, and
    CM was priced as clearing price x de-rated MW x 1000 on every one of them.
    """
    site = _site(make_site, consents=(_consent(MarketStream.CAPACITY_MARKET, 20.0),))
    ctx = GateContext(deliverability_confirmed=True)
    # Engine path: a COMPILED commitment is supplied, so JSON-F-30 cannot be what zeroes this.
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=300.0, deliverable_capacity_mw=0.066), site, LIB, context=ctx
    )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None
    assert cm.annual_revenue_gbp == 0.0
    assert cm.firmness != Firmness.CONTRACTED
    assert any("PREQUALIFIED" in w for w in cm.warnings)
    # Proxy path: same verdict, same £0.
    # `context=` by KEYWORD. `value_function`'s fourth positional slot is `scenario`, so passing
    # `ctx` positionally here left `context=None` and this proxy leg asserted £0 for the wrong
    # reason: it was exercising the no-GateContext path, not the deliverability-confirmed one the
    # test name claims. Both assertions still held, which is exactly why it went unnoticed.
    priced = REG.get(MarketStream.CAPACITY_MARKET).value_function(
        DispatchSummary(power_mw=0.3, duration_h=2.0, deliverable_capacity_mw=0.066),
        site,
        LIB,
        context=ctx,
    )
    assert priced.annual_revenue_gbp == 0.0
    assert any("PREQUALIFIED" in w for w in priced.warnings)


def test_an_eligible_cm_unit_still_prices(make_site: Callable[..., s.SiteModel]) -> None:
    """The gate removes only what is barred: a 2 MW unit prices exactly as before."""
    site = _site(make_site, mec_kw=4_000.0)
    ctx = GateContext(deliverability_confirmed=True)
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=2_000.0, deliverable_capacity_mw=0.44), site, LIB, context=ctx
    )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None
    clearing = LIB.get("capacity_market", "clearing_price_gbp_per_kw_yr").value
    assert cm.annual_revenue_gbp == pytest.approx(clearing * 0.44 * 1000.0, abs=0.01)
    assert not any("PREQUALIFIED" in w for w in cm.warnings)


def test_the_cm_gate_is_never_tested_against_de_rated_capacity(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The register row is explicit: the threshold is on connection capacity, NOT de-rated capacity.

    A 2 MW / 2 h unit de-rates to ~0.44 MW. Testing the threshold against that would bar an eligible
    CMU, so the predicate must read the sized power (and the connection bound at gate time).
    """
    site = _site(make_site, mec_kw=4_000.0)
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=2_000.0, deliverable_capacity_mw=0.44),
        site,
        LIB,
        context=GateContext(deliverability_confirmed=True),
    )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None and cm.annual_revenue_gbp > 0.0


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_a_sub_threshold_unit_is_ineligible(sid: MarketStream) -> None:
    verdict = market_entry_verdict(sid, unit_mw=0.3, portfolio_mw=None)
    assert verdict.eligible is False
    assert verdict.route == "below_threshold"
    assert verdict.sellable_mw == 0.0


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_an_unknown_unit_size_is_not_taken_as_eligible(sid: MarketStream) -> None:
    verdict = market_entry_verdict(sid, unit_mw=None, portfolio_mw=None)
    assert verdict.eligible is False
    assert verdict.route == "unproven"


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_aggregation_clears_the_threshold_for_a_small_unit(sid: MarketStream) -> None:
    # A fractional unit is offerable INSIDE an aggregate — that is what an aggregator is for — so
    # the whole-MW truncation does not apply on this route.
    verdict = market_entry_verdict(sid, unit_mw=0.3, portfolio_mw=12.0)
    assert verdict.eligible is True
    assert verdict.route == "aggregated"
    assert verdict.sellable_mw == 0.3


def test_whole_mw_sell_orders_truncate_the_offerable_quantity() -> None:
    # Register: "Sell orders may only be submitted in whole MWs" / "For BM, MW values must be
    # declared as integers" — so 1.6 MW may offer 1 MW and 0.9 MW may offer nothing.
    assert gates.sellable_whole_mw(1.6) == 1.0
    assert gates.sellable_whole_mw(0.9) == 0.0
    assert gates.sellable_whole_mw(3.0) == 3.0
    assert gates.sellable_whole_mw(None) is None
    verdict = market_entry_verdict(MarketStream.BALANCING_MECHANISM, unit_mw=1.6, portfolio_mw=None)
    assert verdict.sellable_mw == 1.0
    assert any("WHOLE MW" in r for r in verdict.reasons)


def test_the_connection_bound_is_the_generous_max_of_mic_and_mec(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # An upper bound by construction, so a refusal built on it can never wrongly block a site.
    assert gates.connection_bounded_response_mw(_site(make_site)) == 0.3  # MIC 300 kW
    assert gates.connection_bounded_response_mw(_site(make_site, mec_kw=2_000.0)) == 2.0


# --- D-92: the gate and the money -------------------------------------------


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_consent_cannot_open_a_market_a_small_site_cannot_enter(
    sid: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    site = _site(make_site, consents=(_consent(sid, 30_000.0),))  # 300 kW connection
    gate = REG.get(sid).check_gates(site, LIB)
    assert gate.gate_status.value == "conditional"
    assert gate.firmness == Firmness.CONDITIONAL
    assert gate.consent_status == "given"  # the authorisation label survives
    assert gate.unlock_condition  # the GateResult validator requires one
    assert any("NOT ELIGIBLE" in w for w in gate.warnings)
    assert any("CONSENT DOES NOT CREATE ELIGIBILITY" in w for w in gate.warnings)


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_a_sub_1_mw_site_earns_zero_and_leaves_the_priced_floor(
    sid: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    # The D-92 headline: this is the revenue removed from all five sub-1 MW real sites.
    site = _site(make_site, consents=(_consent(sid, 30_000.0),))
    value_streams, gated_upside, _ = build_value_streams(
        _schedule(power_kw=300.0, deliverable_capacity_mw=0.3), site, LIB
    )
    assert _entry(value_streams, sid) is None, "must not reach the priced floor"
    entry = _entry(gated_upside, sid)
    assert entry is not None
    assert entry.annual_revenue_gbp == 0.0
    assert entry.firmness == Firmness.CONDITIONAL
    assert entry.consent_status == "given"


@pytest.mark.parametrize("sid", SIZE_GATED)
def test_an_eligible_unit_still_prices_and_reaches_the_floor(
    sid: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    site = _site(make_site, consents=(_consent(sid, 30_000.0),), mec_kw=4_000.0)
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=4_000.0, deliverable_capacity_mw=2.0), site, LIB
    )
    entry = _entry(value_streams, sid)
    assert entry is not None
    assert entry.annual_revenue_gbp > 0
    assert entry.firmness == Firmness.CONTRACTED


def test_the_bm_volume_basis_is_sellable_power_not_the_cm_derated_capability(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D57 item 2 / D-102: the CM duration de-rating is NOT the BM or DC/DM/DR volume basis.

    **What this replaces.** The test used to assert £0 here: the basis was
    ``_derated_deliverable_mw``, so a 2 MW unit with 0.6 MW of *CM-de-rated* capability had a
    fractional volume that truncated to a zero whole-MW sell order. D-102 already recorded that
    importing the Capacity Market's duration de-rating into these two markets is the wrong basis,
    and
    D57 authorised retiring it (commit 7405d4b): the volume is now ``gb_streams.sellable_volume_mw``
    on the SIZED power, the same sourced function the proxy path uses, so engine and proxy can no
    longer disagree about the volume.

    It dies if ``valuation._consented_gated_per_mw_yr_value`` goes back to
    ``_derated_deliverable_mw``
    for these streams.
    """
    sid = MarketStream.BALANCING_MECHANISM
    site = _site(make_site, consents=(_consent(sid, 30_000.0),), mec_kw=2_000.0)
    value_streams, gated_upside, _ = build_value_streams(
        _schedule(power_kw=2_000.0, deliverable_capacity_mw=0.6), site, LIB
    )
    entry = _entry(value_streams, sid) or _entry(gated_upside, sid)
    assert entry is not None
    # 2.000 MW sized, whole-MW-offerable standalone => 2 MW x £30,000/MW/yr. The CM de-rating that
    # used to cut this to a fractional 0.6 MW is gone, and no note credits the CM de-rating key.
    assert entry.annual_revenue_gbp == pytest.approx(30_000.0 * 2.0)
    assert entry.firmness == Firmness.CONTRACTED
    joined = " ".join(entry.warnings)
    assert "NO Capacity Market de-rating factor is applied" in joined
    assert "de_rating_factor_by_duration_h" not in " ".join(entry.assumptions_used)


def test_the_whole_mw_order_rule_does_not_bite_on_the_d57_aggregated_route(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A sub-whole-MW unit sells its own MW inside the assumed-approved aggregate (D57 item 8).

    Production always supplies this context now — `sizing.deliverability.produce_gate_context`
    populates ``portfolio_mw`` on every run — so this is the path a real run takes. It dies if the
    aggregated route stops passing ``portfolio_mw`` through `_market_entry_verdict` into
    `sellable_volume_mw`, which is what would silently truncate every sub-MW site back to zero.
    """
    sid = MarketStream.BALANCING_MECHANISM
    site = _site(make_site, consents=(_consent(sid, 30_000.0),), mec_kw=2_000.0)
    ctx = GateContext(portfolio_mw=1.0, portfolio_threshold_mw=1.0)
    value_streams, gated_upside, _ = build_value_streams(
        _schedule(power_kw=1_500.0, deliverable_capacity_mw=0.33), site, LIB, context=ctx
    )
    entry = _entry(value_streams, sid) or _entry(gated_upside, sid)
    assert entry is not None
    # 1.500 MW is offerable IN FULL inside the aggregate: no truncation to 1 MW.
    assert entry.annual_revenue_gbp == pytest.approx(30_000.0 * 1.5)
    assert entry.firmness == Firmness.CONTRACTED
    assert any("AGGREGATION" in w for w in entry.warnings)


def test_the_engine_side_gate_only_ever_downgrades(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # A site whose CONNECTION clears 1 MW but whose SIZED unit does not: the gate layer cannot see
    # the sizing, so the engine path is what binds. It must not silently price the consented rate.
    sid = MarketStream.BALANCING_MECHANISM
    site = _site(make_site, consents=(_consent(sid, 30_000.0),), mec_kw=4_000.0)
    assert REG.get(sid).check_gates(site, LIB).gate_status.value == "open"
    value_streams, gated_upside, _ = build_value_streams(
        _schedule(power_kw=400.0, deliverable_capacity_mw=0.4), site, LIB
    )
    assert _entry(value_streams, sid) is None
    entry = _entry(gated_upside, sid)
    assert entry is not None and entry.annual_revenue_gbp == 0.0
    assert any("D-92" in w for w in entry.warnings)


def test_ineligible_streams_do_not_perturb_the_shared_capacity_budget(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # An ineligible stream is zeroed rather than kept at a conditional non-zero £, so it never
    # becomes a claim on the shared power budget and cannot rescale CM or local flex.
    #
    # The scenario needed here is BM/DC-DM-DR earning £0 while CM keeps its full value. Since CM's
    # own 1 MW prequalification gate was armed, a 300 kW unit no longer supplies that — it bars CM
    # too. A 1.5 MW unit does: it clears every 1 MW threshold, but its 0.33 MW de-rated capability
    # TRUNCATES TO ZERO as a whole-MW sell order, which is what zeroes BM and DC-DM-DR here. CM has
    # no whole-MW order rule, so it prices the full 0.33 MW.
    site = _site(
        make_site,
        mec_kw=2_000.0,
        consents=(
            _consent(MarketStream.BALANCING_MECHANISM, 30_000.0),
            _consent(MarketStream.ANCILLARY_DC_DM_DR, 60_000.0),
            s.RevenueStreamConsent(
                stream=MarketStream.CAPACITY_MARKET,
                consented=True,
                agreed_rate_gbp=50.0,
                agreed_rate_unit=RateUnit.GBP_PER_KW_YEAR,
            ),
        ),
    )
    schedule = _schedule(power_kw=1_500.0, deliverable_capacity_mw=0.33)
    value_streams, gated_upside, _ = build_value_streams(
        schedule, site, LIB, context=GateContext(deliverability_confirmed=True)
    )
    # D57: the whole-MW truncation still applies on the STANDALONE route (no portfolio supplied
    # here), so 1.500 MW sized sells as 1 MW. What the test is really about is downstream: no
    # stream is CUT BACK by the shared power budget any more (D57 limb 3 assumes stacking
    # exclusivity satisfied), and the oversell is DISCLOSED instead of enforced.
    for sid in SIZE_GATED:
        entry = _entry(value_streams, sid) or _entry(gated_upside, sid)
        assert entry is not None
        assert entry.annual_revenue_gbp > 0
        assert not any("Revenue scaled by" in w for w in entry.warnings), (
            "a stream was rescaled by the shared power budget; D57 limb 3 pays every claim in full"
        )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None
    # CM keeps the full agreed rate × its de-rated MW; CM is outside POWER_BUDGET_STREAMS.
    #
    # WP-CORE-01 triage (2026-11-16): re-pinned from 50.0 × 0.33 × 1000 = £16,500. FIX 5/7
    # (05133e4, 2026-08-19; changelog 2026-08-19 item 5) re-bases a consented CM rate from
    # DE-RATED to RATED capacity by dividing out the 2 h reference bin (gb_v0_1: 0.65), so the
    # de-rating lands ONCE, on the engine-reserved 0.33 MW:
    #   (£50 / 0.65) × 0.33 MW × 1000 = £25,384.62. Still unscaled by the power budget (the point
    #   of this test): no "CAPACITY BUDGET" note, asserted below.
    assert cm.annual_revenue_gbp == pytest.approx(50.0 / 0.65 * 0.33 * 1000.0)
    assert cm.annual_revenue_gbp == pytest.approx(25_384.62, abs=0.005)
    assert not any("CAPACITY BUDGET" in w for w in cm.warnings)


# --- JSON-F-30: RETIRED BY D57 - the CM agreement is ASSUMED APPROVED --------


def test_cm_is_contracted_without_a_compiled_commitment_under_d57(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D57 item 8: an uncompiled CM commitment no longer holds the stream out of the priced floor.

    **The assertion this replaces, and why the inversion is authorised.** It required
    ``firmness == CONDITIONAL`` whenever ``deliverable_capacity_mw is None``, on the reasoning that
    a
    commitment which never compiled leaves the volume at nameplate x de-rating with no headroom
    reserved for delivery. That reasoning is intact and is still published verbatim on the stream.
    But on every campaign site the "uncompiled commitment" simply IS the absent CM agreement, and
    D57
    (Olly, 2026-08-18) assumes the CM agreement APPROVED for the site and the battery size, its
    status line superseding every other instruction in this codebase on stream eligibility.

    So what is pinned now is the pair: the stream is CONTRACTED (in the floor, in P50/P90, in the
    DSCR, in both IRRs), AND the record still says the volume rests on an assumption and reserves no
    headroom. It dies if ``valuation._enforce_engine_side_gates``'s ``GateStatus.OPEN`` branch for
    ``deliverable_capacity_mw is None`` is reverted to CONDITIONAL, and it dies if the disclosure
    sentence is dropped.
    """
    # mec_kw clears the CM's own 1 MW prequalification gate, so JSON-F-30 is what this test
    # isolates — an ELIGIBLE CMU whose commitment simply did not compile into the dispatch.
    site = _site(make_site, mec_kw=2_000.0)
    ctx = GateContext(deliverability_confirmed=True)
    # deliverable_capacity_mw is None => the commitment never compiled into the dispatch.
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=1_000.0, deliverable_capacity_mw=None), site, LIB, context=ctx
    )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None
    assert cm.firmness == Firmness.CONTRACTED, "D57 requires CM in the priced floor"
    assert cm.gate_status == "open"
    assert cm.annual_revenue_gbp > 0
    blob = " ".join(cm.warnings)
    # The finding is still on the record, labelled as an assumption rather than a check.
    assert "IN THE PRICED FLOOR ON A D57 ASSUMPTION" in blob
    assert "JSON-F-30" in blob
    assert "NO net-injection headroom is reserved" in blob
    assert "CONFIRMATION ASK" in blob


def test_cm_is_contracted_when_the_commitment_did_compile(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _site(make_site, mec_kw=2_000.0)  # size-eligible, so only JSON-F-30 is in play
    ctx = GateContext(deliverability_confirmed=True)
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=1_000.0, deliverable_capacity_mw=0.4), site, LIB, context=ctx
    )
    cm = _entry(value_streams, MarketStream.CAPACITY_MARKET)
    assert cm is not None
    assert cm.firmness == Firmness.CONTRACTED
    assert cm.annual_revenue_gbp == pytest.approx(30.0 * 0.4 * 1000.0)


# --- D-158: EFA-block structure, F and K -------------------------------------


def test_eac_value_is_the_rate_times_sellable_mw_when_f_and_k_are_unsourced() -> None:
    revenue, used, notes = eac_response_value(
        rate_gbp_per_mw_yr=28_296.9, sellable_mw=2.0, assumptions=LIB
    )
    assert revenue == pytest.approx(28_296.9 * 2.0)  # F = K = 1.0, an explicit upper bound
    assert used == []  # nothing claimed as sourced
    assert any("availability factor F" in n and "UPPER BOUND" in n for n in notes)
    assert any("performance factor K" in n and "UPPER BOUND" in n for n in notes)
    assert any("DATA ASK" in n for n in notes)


def test_eac_value_reports_the_efa_block_structure_and_refuses_to_stack_products() -> None:
    _, _, notes = eac_response_value(rate_gbp_per_mw_yr=28_296.9, sellable_mw=1.0, assumptions=LIB)
    joined = " ".join(notes)
    # 6 blocks x 4 h x 365 d = 8760 block-hours, matching the library key's own derivation.
    assert "8,760 block-hours/yr" in joined
    assert "ONE product per EFA block, never six added together" in joined
    for product in ("DCL", "DCH", "DML", "DMH", "DRL", "DRH"):
        assert product in joined


def test_eac_availability_and_performance_factors_apply_when_sourced() -> None:
    lib = load_assumptions("gb_v1_6")
    lib.availability["expected_availability_pct"].value = 95.0
    revenue, used, _ = eac_response_value(
        rate_gbp_per_mw_yr=10_000.0, sellable_mw=1.0, assumptions=lib
    )
    assert revenue == pytest.approx(10_000.0 * 0.95)
    assert "availability.expected_availability_pct" in used


def test_the_toll_exemption_keeps_the_flat_rate_and_names_its_contract() -> None:
    # The £/MW/yr shortcut survives ONLY for a toll whose contract reference puts availability risk
    # on the counterparty.
    lib = load_assumptions("gb_v1_6")
    lib.availability["expected_availability_pct"].value = 90.0
    revenue, used, notes = eac_response_value(
        rate_gbp_per_mw_yr=10_000.0,
        sellable_mw=1.0,
        assumptions=lib,
        toll_reference="TOLL-2026-004",
    )
    assert revenue == pytest.approx(10_000.0)  # F not deducted — it is the counterparty's risk
    assert used == []
    assert any("TOLL EXEMPTION" in n and "TOLL-2026-004" in n for n in notes)


def test_ancillary_engine_path_carries_the_efa_and_factor_notes(
    make_site: Callable[..., s.SiteModel],
) -> None:
    sid = MarketStream.ANCILLARY_DC_DM_DR
    site = _site(make_site, consents=(_consent(sid, 60_000.0),), mec_kw=4_000.0)
    value_streams, _, _ = build_value_streams(
        _schedule(power_kw=4_000.0, deliverable_capacity_mw=2.0), site, LIB
    )
    entry = _entry(value_streams, sid)
    assert entry is not None
    # D57 item 2 / D-102: the volume is the SIZED 4.000 MW, not the CM-de-rated 2.0 MW.
    assert entry.annual_revenue_gbp == pytest.approx(60_000.0 * 4.0)
    joined = " ".join(entry.warnings)
    assert "EFA-block granularity" in joined
    assert "availability factor F" in joined
    assert "performance factor K" in joined


# --- D-116: the ENA taper, verified against the register's published table ----


@pytest.mark.parametrize(
    ("delivery", "expected"),
    [
        (1.00, 1.00),
        (0.95, 1.00),
        (0.94, 0.92),
        (0.90, 0.80),
        (0.85, 0.65),
        (0.80, 0.50),
        (0.76, 0.38),
    ],
)
def test_the_ena_payment_taper_matches_the_published_table(
    delivery: float, expected: float
) -> None:
    # Register row "Payment taper under 5% Grace Factor and Performance Multiplier of 3" — every
    # published point. The formula is verified against the source, not invented to fit it.
    assert payable_delivery_fraction(delivery) == pytest.approx(expected, abs=1e-9)


def test_the_taper_reaches_zero_at_the_published_63_percent() -> None:
    assert payable_delivery_fraction(0.64) > 0.0
    assert payable_delivery_fraction(0.63) == 0.0
    assert payable_delivery_fraction(0.50) == 0.0  # floored, never negative


def test_over_delivery_is_unpaid_under_the_zero_pod_cap() -> None:
    # "payable over-delivery 0%" for every GB product in the register.
    assert payable_delivery_fraction(1.30) == pytest.approx(1.0)
    assert payable_delivery_fraction(2.00) == pytest.approx(1.0)


def test_the_utilisation_adjustment_caps_over_delivery_and_penalises_shortfall() -> None:
    payable, notes = local_flex_utilisation_adjustment(delivered_mwh=12.0, contracted_mwh=10.0)
    assert payable == pytest.approx(10.0)  # 2 MWh over-delivery unpaid
    assert any("PAYABLE OVER-DELIVERY CAP" in n for n in notes)
    payable, _ = local_flex_utilisation_adjustment(delivered_mwh=8.0, contracted_mwh=10.0)
    assert payable == pytest.approx(10.0 * 0.50)  # 80% delivery -> 50% paid


def test_the_utilisation_adjustment_asks_for_data_rather_than_inventing_a_ratio() -> None:
    payable, notes = local_flex_utilisation_adjustment(delivered_mwh=7.0, contracted_mwh=None)
    assert payable == 7.0  # unchanged, and labelled as an upper bound
    assert any("DATA ASK" in n and "UPPER BOUND" in n for n in notes)


def test_the_availability_leg_is_a_data_ask_not_a_default() -> None:
    # Decision D30: an availability price is asserted only from an awarded tender, never defaulted.
    revenue, used, notes = local_flex_availability_value(
        assumptions=LIB, contracted_mw=None, window_hours=None
    )
    assert revenue == 0.0
    assert used == []
    joined = " ".join(notes)
    assert "DATA ASK" in joined
    assert "UNDERSTATES local flex" in joined
    assert "40x" in joined  # the per-DNO spread that must not be averaged


def test_the_availability_leg_prices_when_an_award_supplies_everything() -> None:
    lib = load_assumptions("gb_v1_6")
    lib.availability["availability_price_gbp_per_mw_h"].value = 5.15  # NGED SAOU_DA realised median
    revenue, used, _ = local_flex_availability_value(
        assumptions=lib, contracted_mw=0.4, window_hours=270.0, product_type="SAOU"
    )
    assert revenue == pytest.approx(5.15 * 0.4 * 270.0)
    assert "availability.availability_price_gbp_per_mw_h" in used


def test_the_availability_leg_is_clawed_back_by_the_utilisation_performance_factor() -> None:
    lib = load_assumptions("gb_v1_6")
    lib.availability["availability_price_gbp_per_mw_h"].value = 10.0
    revenue, _, notes = local_flex_availability_value(
        assumptions=lib,
        contracted_mw=1.0,
        window_hours=100.0,
        utilisation_performance_ratio=0.80,
    )
    assert revenue == pytest.approx(1_000.0 * 0.50)
    assert any("Monthly Utilisation Performance Factor 0.5" in n for n in notes)


def test_local_flex_engine_path_states_both_d116_gaps(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _site(make_site)
    site = s.SiteModel.model_validate(
        {
            **site.model_dump(mode="python"),
            "connection": site.connection.model_copy(update={"dno_region": "east_midlands"}),
        }
    )
    schedule = _schedule(power_kw=400.0, tender_mwh=40.0)
    value_streams, _, _ = build_value_streams(schedule, site, LIB)
    flex = _entry(value_streams, MarketStream.LOCAL_FLEX_PICLO)
    assert flex is not None
    joined = " ".join(flex.warnings)
    assert "availability leg NOT priced" in joined  # the understatement, stated
    assert "Payable-Over-Delivery" in joined  # the overstatement, stated
    assert "PRODUCT TYPE NOT CARRIED" in joined  # SU / SAOU / VAOU / OU never defaulted


# --- E-06: DFS ---------------------------------------------------------------


def test_dfs_is_registered_as_a_stream() -> None:
    assert MarketStream.DFS in REG
    assert REG.get(MarketStream.DFS).stream_id == MarketStream.DFS


@pytest.mark.parametrize(
    ("unit_mw", "expected"),
    [
        (0.05, None),  # below the 0.1 MW minimum Registered Unit Capacity
        (0.1, 0.1),
        (0.29, 0.2),  # truncated to one decimal place, never rounded up
        (5.0, 5.0),
        (100.0, 100.0),
        (150.0, None),  # above the 100 MW maximum
    ],
)
def test_dfs_registered_unit_capacity_bounds_and_rounding(
    unit_mw: float, expected: float | None
) -> None:
    assert dfs_registered_unit_capacity_mw(unit_mw) == expected


@pytest.mark.parametrize(
    ("rv", "expected"),
    [
        (150.0, 1.2),  # capped above at 1.2
        (120.0, 1.2),
        (100.0, 1.0),
        (60.0, 0.60),
        (40.0, 0.30),  # 0.01*(2*40-50)
        (25.0, 0.00),
        (10.0, 0.00),  # the dead zone: 0-25% earns exactly zero
        (-50.0, -0.50),  # NEGATIVE: the provider pays
        (-150.0, -1.0),  # floored at -1
    ],
)
def test_the_dfs_performance_factor_is_piecewise_and_not_clipped_at_zero(
    rv: float, expected: float
) -> None:
    assert dfs_performance_factor(rv) == pytest.approx(expected)


def test_dfs_clears_its_size_floor_on_a_site_the_1_mw_markets_exclude(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # The point of E-06: the same 300 kW site that D-92 bars from BM and DC/DM/DR does clear DFS.
    site = _site(make_site)
    for sid in SIZE_GATED:
        assert not market_entry_verdict(sid, unit_mw=0.3, portfolio_mw=None).eligible
    gate = REG.get(MarketStream.DFS).check_gates(site, LIB)
    # D57 item 4: OPEN, not conditional. Every requirement DFS carries and this site does not hold -
    # registration, the zone allocation, the baseline election, an accepted bid - is a contract,
    # consent, registration or award that D57 limb 2 assumes APPROVED. They stay on the record as
    # the
    # confirmation ask, which is what limb 4 requires, and the gate no longer holds the stream out.
    assert gate.gate_status.value == "open"
    assert any("clears the DFS minimum" in w for w in gate.warnings)
    assert any("ONLY direct NESO revenue route below 1 MW" in w for w in gate.warnings)
    assert any(
        "DFS IS PRICED ON D57 ASSUMPTIONS, NOT ON A HELD REGISTRATION" in w for w in gate.warnings
    )


def test_dfs_carries_the_exclusivity_rule_so_it_is_never_added_to_bm(
    make_site: Callable[..., s.SiteModel],
) -> None:
    gate = REG.get(MarketStream.DFS).check_gates(_site(make_site), LIB)
    joined = " ".join(gate.warnings)
    assert "EXCLUSIVITY" in joined
    assert "4.7.6" in joined and "4.7.9" in joined
    assert "ALTERNATIVES" in joined


def test_the_dfs_proxy_path_prices_the_measured_per_kw_yr_rate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D57 item 4: the PROXY path prices DFS too, because a GBP 0 here distorts the SEARCH.

    **What this replaces.** It required GBP 0 with data asks, which was right while nothing anywhere
    could price DFS. The engine path now prices it from the dispatch it measures over the called
    settlement periods - and the two paths run at DIFFERENT STAGES of one sizing search. A zero here
    ranks every coarse candidate as though DFS earned nothing, so the loop can discard the size that
    would have won on the full stack. The two cannot double-book: they are alternatives, never
    summed, and the selected candidate is always engine-priced.
    """
    v = REG.get(MarketStream.DFS).value_function(
        DispatchSummary(power_mw=0.3, duration_h=2.0), _site(make_site), LIB, None, None
    )
    assert v.annual_revenue_gbp > 0.0
    joined = " ".join(v.warnings)
    assert "S = 0.5 x P x Q x K" in joined
    assert "DATA ASK" in joined
    assert "THIS IS THE COARSE-SWEEP RANKING PATH ONLY" in joined
    assert "never summed" in joined
    assert "Registered Unit Capacity 0.3 MW" in joined


def test_dfs_with_an_agreed_rate_still_refuses_to_pay_uncalled_periods(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # The F-19 lesson, applied pre-emptively: availability is not utilisation.
    site = _site(
        make_site,
        consents=(
            s.RevenueStreamConsent(
                stream=MarketStream.DFS,
                consented=True,
                agreed_rate_gbp=300.0,
                agreed_rate_unit=RateUnit.GBP_PER_MWH,
            ),
        ),
    )
    v = REG.get(MarketStream.DFS).value_function(
        DispatchSummary(power_mw=0.4, duration_h=2.0), site, LIB, None, None
    )
    # D57: the £ is the MEASURED per-kW/yr rate, not the customer-agreed £/MWh - which still cannot
    # become an annual figure without a called-period count, and the record says so rather than
    # refusing the whole stream over it.
    assert v.annual_revenue_gbp > 0.0
    joined = " ".join(v.warnings)
    assert "needs a called-period count" in joined
    assert "volume-weighted accepted price" in joined


def test_dfs_enters_every_engine_run_priced_and_in_the_floor(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D57 limb 1: CONTRACTED and in the priced floor, from the dispatch's own measurement.

    Was `test_dfs_enters_every_engine_run_as_a_conditional_zero`. The zero was structural - there
    was
    no DFS objective term, no schedule field and no valuation branch anywhere in
    `src/gridq/optimisation/` - so it recorded the absence of an engine, not a fact about DFS.
    """
    value_streams, gated_upside, _ = build_value_streams(
        _schedule(power_kw=300.0), _site(make_site), LIB
    )
    entry = _entry(value_streams, MarketStream.DFS) or _entry(gated_upside, MarketStream.DFS)
    assert entry is not None
    assert entry.firmness == Firmness.CONTRACTED
    # This fixture schedule carries no DFS price array, so the £ is honestly absent and SAYS SO.
    assert any("DFS NOT MEASURED ON THIS RUN" in w for w in entry.warnings)
    assert any("CALENDAR fact" in w for w in entry.warnings)


# --- MD-F-05 limb (b): contracted export is netted ---------------------------


def _wholesale_base() -> s.ValueStreamResult:
    return s.ValueStreamResult(
        stream=MarketStream.WHOLESALE_P415,
        firmness=Firmness.CONDITIONAL,
        gate_status="conditional",
        warnings=["base note"],
    )


def test_contracted_export_is_netted_of_cannibalised_floor_value() -> None:
    r = wholesale_contracted_stream_result(
        _wholesale_base(),
        agreed_tariff_gbp_per_kwh=0.08,
        export_mwh=100.0,
        cannibalised_floor_gbp=3_000.0,
    )
    assert r.annual_revenue_gbp == pytest.approx(8_000.0 - 3_000.0)
    assert any("NET of cannibalised" in w for w in r.warnings)
    assert r.firmness == Firmness.CONTRACTED


def test_netting_cannot_drive_a_contracted_line_negative() -> None:
    r = wholesale_contracted_stream_result(
        _wholesale_base(),
        agreed_tariff_gbp_per_kwh=0.08,
        export_mwh=100.0,
        cannibalised_floor_gbp=20_000.0,  # export displaced more than it earned
    )
    assert r.annual_revenue_gbp == 0.0


def test_an_unsupplied_netting_figure_is_loudly_gross_not_silently_so() -> None:
    r = wholesale_contracted_stream_result(
        _wholesale_base(), agreed_tariff_gbp_per_kwh=0.08, export_mwh=100.0
    )
    assert r.annual_revenue_gbp == pytest.approx(8_000.0)  # additive: unchanged default
    joined = " ".join(r.warnings)
    assert "GROSS — NOT NETTED" in joined
    assert "ASSUMPTION STATED" in joined
