"""Tests for the GB value *functions* — pricing a stream over a dispatch schedule.

These exercise W3-1: each GB stream values a supplied dispatch schedule from numbers in
``gb_v0_1`` × dispatched quantities, consuming the scenario price multiplier, with firmness
from the gate. The honesty invariants are pinned here:

- Numbers trace to ``gb_v0_1`` (asserted against the loaded assumption values).
- No dispatch schedule ⇒ nothing priced (£0); a closed gate ⇒ nothing priced.
- The generic ``GateContext`` opens & prices CM (deliverability) and Piclo (tender); it is
  additive (``context=None`` reproduces the data-ask-only behaviour).
- Gated upside (P415/BM/ancillary) prices £0 (placeholder zero) and is never FIRM.
- Priced placeholder numbers always carry a NOT-bankable warning naming the key.
- Outputs are deterministic.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.scenarios.models import Scenario, ScenarioName
from gridq.schemas import Firmness, MarketStream, RateUnit
from gridq.value_stack import DispatchSummary, GateContext, default_registry
from gridq.value_stack.gb_streams import local_flex_call_rate

LIB = load_assumptions("gb_v0_1")
REG = default_registry()

# Numbers pinned straight from gb_v0_1 (so a library edit that breaks tracing is caught).
ARB = LIB.get("tariff_arbitrage", "indicative_value_gbp_per_mw_yr").value  # 30000
DUOS = LIB.get("duos", "red_band_avoidance_gbp_per_mw_yr").value  # 18000
MIC = LIB.get("mic_asc_headroom", "value_gbp_per_mw_yr").value  # 12000
CM_PRICE = LIB.get("capacity_market", "clearing_price_gbp_per_kw_yr").value  # 30.0
CM_DERATE = LIB.get(
    "capacity_market", "de_rating_factor_by_duration_h"
).value  # {"1":..,"2":..,"4":..}
PICLO = LIB.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value  # 150.0
# F-19: the sourced DNO call rate both pricing paths apply (5% default; read from the library when a
# release carries local_flex_piclo.utilisation_call_rate, so this stays honest if one lands).
CALL_RATE, _, _ = local_flex_call_rate(LIB)


def _rich_site() -> s.SiteModel:
    """A well-populated T2 site: floor gates open, CM/Piclo conditional, gated conditional."""
    return s.SiteModel(
        site_id="site_vf",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"red": 0.42, "amber": 0.21, "green": 0.14},
            duos_red_rate=0.18,
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, dno_region="WPD_East_Midlands"),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _flat_site() -> s.SiteModel:
    """A site with a flat tariff (tariff-arbitrage gate CLOSED)."""
    site = _rich_site()
    return site.model_copy(
        update={
            "tariff_stack": s.TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="X",
                import_rates={"single": 0.24},
            )
        }
    )


def _dispatch(power_mw: float = 2.0, duration_h: float = 2.0, **extra: float) -> DispatchSummary:
    return DispatchSummary(power_mw=power_mw, duration_h=duration_h, **extra)


def _cm_site() -> s.SiteModel:
    """`_rich_site()` with a connection that can PREQUALIFY for the Capacity Market (D-92).

    CM prequalification requires at least 1 MW of connection capacity (The Electricity Capacity
    Regulations 2014, SI 2014/2043, reg 15); `_rich_site()`'s 300 kW connection is barred outright.
    The CM tests below are about de-rating and the deliverability gate, so they need a site that
    reaches those — the prequalification bar itself is pinned in test_market_entry_eligibility.py.
    """
    base = _rich_site()
    return base.model_copy(
        update={"connection": base.connection.model_copy(update={"mec_kw": 4_000.0})}
    )


def _value(stream_id: MarketStream, dispatch, *, scenario=None, context=None, site=None):  # type: ignore[no-untyped-def]
    return REG.get(stream_id).value_function(
        dispatch, site if site is not None else _rich_site(), LIB, scenario, context
    )


# --- floor streams: priced over a schedule, numbers trace to gb_v0_1 --------


def test_tariff_arbitrage_priced_traces_to_gb_v0_1() -> None:
    v = _value(MarketStream.TARIFF_ARBITRAGE, _dispatch(power_mw=2.0))
    assert v.annual_revenue_gbp == ARB * 2.0  # 30000 × 2 MW
    assert v.firmness == Firmness.FIRM
    assert v.gate_status == "open"
    assert "tariff_arbitrage.indicative_value_gbp_per_mw_yr" in v.assumptions_used
    assert any("not bankable" in w.lower() for w in v.warnings)


def test_duos_priced_traces_to_gb_v0_1() -> None:
    v = _value(MarketStream.DUOS_RED_BAND_AVOIDANCE, _dispatch(power_mw=3.0))
    assert v.annual_revenue_gbp == DUOS * 3.0
    assert v.firmness == Firmness.FIRM


def test_mic_asc_priced_traces_to_gb_v0_1() -> None:
    v = _value(MarketStream.MIC_ASC_HEADROOM, _dispatch(power_mw=2.0))
    assert v.annual_revenue_gbp == MIC * 2.0
    assert v.firmness == Firmness.FIRM


def test_ppa_floor_not_double_counted() -> None:
    # PPA/bill savings is the customer-facing aggregate; not separately priced (avoids
    # double-counting the itemised import-side streams).
    v = _value(MarketStream.PPA_OR_BILL_SAVINGS, _dispatch())
    assert v.annual_revenue_gbp == 0.0
    assert v.firmness == Firmness.FIRM
    assert any("double-count" in w.lower() for w in v.warnings)


# --- Capacity Market: deliverability profile via GateContext ----------------


def test_cm_conditional_without_context_is_upside_not_floor() -> None:
    v = _value(
        MarketStream.CAPACITY_MARKET, _dispatch(power_mw=2.0, duration_h=2.0), site=_cm_site()
    )
    # de-rated upside = 30 £/kW/yr × (2 MW × 0.65 × 1000 kW/MW)
    assert v.annual_revenue_gbp == CM_PRICE * (2.0 * CM_DERATE["2"] * 1000.0)
    assert v.firmness == Firmness.CONDITIONAL  # held OUT of the priced floor
    assert v.firmness != Firmness.CONTRACTED
    assert v.unlock_condition and "deliverab" in v.unlock_condition.lower()


def test_cm_contracted_and_priced_with_deliverability_context() -> None:
    ctx = GateContext(deliverability_confirmed=True)
    v = _value(
        MarketStream.CAPACITY_MARKET,
        _dispatch(power_mw=2.0, duration_h=4.0),
        context=ctx,
        site=_cm_site(),
    )
    assert v.annual_revenue_gbp == CM_PRICE * (2.0 * CM_DERATE["4"] * 1000.0)
    assert v.firmness == Firmness.CONTRACTED
    assert v.gate_status == "open"
    assert "capacity_market.de_rating_factor_by_duration_h" in v.assumptions_used


def test_cm_uses_engine_deliverable_capacity_when_supplied() -> None:
    ctx = GateContext(deliverability_confirmed=True)
    d = _dispatch(power_mw=5.0, duration_h=2.0, deliverable_capacity_mw=1.0)
    v = _value(MarketStream.CAPACITY_MARKET, d, context=ctx, site=_cm_site())
    # engine-verified de-rated capacity used directly (no further duration de-rating)
    assert v.annual_revenue_gbp == CM_PRICE * (1.0 * 1000.0)
    assert v.firmness == Firmness.CONTRACTED


# --- Local flex (Piclo): tender via GateContext + utilisation energy --------


def test_piclo_conditional_without_tender_is_upside() -> None:
    # F-19 parity: availability is NOT utilisation. This path used to pay every in-window MWh as an
    # instructed DNO call (an implicit 100% call rate) while the engine path applied the sourced 5%
    # rate — a 20x divergence on a CONTRACTED stream. Both now use the same resolver, so this
    # expectation is derived from it rather than hardcoded.
    d = _dispatch(tender_window_discharge_mwh=100.0)
    v = _value(MarketStream.LOCAL_FLEX_PICLO, d)
    assert v.annual_revenue_gbp == PICLO * 100.0 * CALL_RATE
    assert v.firmness == Firmness.CONDITIONAL


def test_piclo_contracted_with_tender_context() -> None:
    ctx = GateContext(local_flex_tender_open=True)
    d = _dispatch(tender_window_discharge_mwh=100.0)
    v = _value(MarketStream.LOCAL_FLEX_PICLO, d, context=ctx)
    assert v.annual_revenue_gbp == PICLO * 100.0 * CALL_RATE
    assert v.firmness == Firmness.CONTRACTED
    assert v.gate_status == "open"


def test_piclo_zero_without_delivered_tender_energy() -> None:
    ctx = GateContext(local_flex_tender_open=True)
    v = _value(MarketStream.LOCAL_FLEX_PICLO, _dispatch(), context=ctx)  # no tender MWh
    assert v.annual_revenue_gbp == 0.0
    assert any("tender-window" in w.lower() for w in v.warnings)


# --- gated upside: priced £0, never firm ------------------------------------


def test_gated_streams_price_zero_and_never_firm() -> None:
    for sid in s.GATED_UPSIDE_STREAMS:
        v = _value(sid, _dispatch(power_mw=2.0))
        assert v.annual_revenue_gbp == 0.0  # placeholder zero in gb_v0_1
        assert v.firmness == Firmness.CONDITIONAL
        assert v.firmness != Firmness.FIRM
        assert v.gate_status == "conditional"


def test_bm_ancillary_portfolio_context_still_conditional() -> None:
    # Even with the portfolio threshold met, the RTM gate (placeholder zero) keeps the
    # gated streams conditional — never firm. Portfolio drops off the missing list.
    ctx = GateContext(portfolio_mw=20.0, portfolio_threshold_mw=10.0)
    for sid in (MarketStream.BALANCING_MECHANISM, MarketStream.ANCILLARY_DC_DM_DR):
        v = _value(sid, _dispatch(power_mw=2.0), context=ctx)
        assert v.firmness == Firmness.CONDITIONAL
        g = REG.get(sid).check_gates(_rich_site(), LIB, ctx)
        assert not any("portfolio" in m.lower() for m in g.missing_requirements)
        assert any("route-to-market" in m.lower() for m in g.missing_requirements)


def test_ancillary_decay_machinery_present() -> None:
    # Value is £0 (placeholder), but the decay factor + degradation key are wired so the
    # machinery activates unchanged when a confirmed value lands.
    v = _value(MarketStream.ANCILLARY_DC_DM_DR, _dispatch(power_mw=2.0))
    assert v.annual_revenue_gbp == 0.0
    assert "degradation.annual_capacity_fade_pct" in v.assumptions_used
    assert any("decay" in w.lower() for w in v.warnings)


# --- scenario price multiplier (duck-typed; tolerates None) -----------------


def test_scenario_price_multiplier_applied_ducktyped() -> None:
    scen = SimpleNamespace(price_multiplier=1.2)
    v = _value(MarketStream.TARIFF_ARBITRAGE, _dispatch(power_mw=2.0), scenario=scen)
    assert v.annual_revenue_gbp == ARB * 2.0 * 1.2


def test_scenario_price_multiplier_applied_real_scenario() -> None:
    # Rename-guard for the finance/value-stack seam (decision D13): the real Scenario must
    # expose price_multiplier and be consumed by duck-typing.
    scen = Scenario(
        scenario_id="hi", name=ScenarioName.HIGH, probability_weight=0.25, price_multiplier=1.5
    )
    v = _value(MarketStream.DUOS_RED_BAND_AVOIDANCE, _dispatch(power_mw=2.0), scenario=scen)
    assert v.annual_revenue_gbp == DUOS * 2.0 * 1.5


def test_invalid_scenario_multiplier_defaults_to_one() -> None:
    for bad in (
        None,
        SimpleNamespace(price_multiplier=0.0),
        SimpleNamespace(price_multiplier=-1.0),
        SimpleNamespace(),
    ):
        v = _value(MarketStream.TARIFF_ARBITRAGE, _dispatch(power_mw=2.0), scenario=bad)
        assert v.annual_revenue_gbp == ARB * 2.0


# --- pricing only over a real, readable schedule ----------------------------


def test_no_dispatch_schedule_prices_nothing() -> None:
    for sid in MarketStream:
        v = REG.get(sid).value_function(None, _rich_site(), LIB, None)
        assert v.annual_revenue_gbp == 0.0
        assert v.annual_cost_gbp == 0.0


def test_a_flat_tariff_prices_no_revenue_even_though_the_gate_now_opens() -> None:
    """Flat tariff ⇒ £0 arbitrage REVENUE, but the gate is OPEN so the round-trip cost is carried.

    Amended 2026-08-17 (TARIFF-1). This previously asserted `firmness == EXCLUDED`, which was the
    defect: a CLOSED gate excluded the stream from the priced floor, and `annual_cost_gbp` is summed
    only over FIRM/CONTRACTED streams, so the NEGATIVE energy term the MILP objective carried on a
    flat tariff was dropped and the reported floor sat ABOVE the objective. "No spread to arbitrage"
    is a £0-revenue fact, not an absent input.

    What must stay true, and is what this test now pins: the £0 REVENUE. The engine path reads
    `schedule.arbitrage_value_gbp` (zero revenue, cost on the cost channel); the proxy path must not
    substitute the library's indicative £/MW/yr and invent a spread the site does not have.
    """
    v = REG.get(MarketStream.TARIFF_ARBITRAGE).value_function(
        _dispatch(power_mw=2.0), _flat_site(), LIB, None
    )
    assert v.annual_revenue_gbp == 0.0
    assert v.firmness == Firmness.FIRM
    assert v.gate_status == "open"
    assert any("NO PRICE SPREAD TO ARBITRAGE" in w for w in v.warnings)


def test_unreadable_dispatch_schedule_prices_nothing() -> None:
    bad = SimpleNamespace(foo=1)  # missing power_mw / duration_h
    v = REG.get(MarketStream.TARIFF_ARBITRAGE).value_function(bad, _rich_site(), LIB, None)
    assert v.annual_revenue_gbp == 0.0
    assert any("unreadable" in w.lower() for w in v.warnings)


# --- consent-driven pricing parity (proxy path, Revenue-Data Drive 2b / D43) ------


def _consented(
    consents: list[s.RevenueStreamConsent], *, mec_kw: float | None = None
) -> s.SiteModel:
    """A consented copy of the rich site; ``mec_kw`` makes it size-eligible for BM/DC-DM-DR (D-92).

    `_rich_site()` has a 300 kW connection, which cannot present the 1 MW unit the Balancing
    Mechanism and the six dynamic-response products require — consent does not change that.
    """
    update: dict[str, object] = {"stream_consents": tuple(consents)}
    if mec_kw is not None:
        update["connection"] = _rich_site().connection.model_copy(update={"mec_kw": mec_kw})
    return _rich_site().model_copy(update=update)


def test_proxy_cm_prefers_the_consented_agreed_rate() -> None:
    site = _consented(
        [
            s.RevenueStreamConsent(
                stream=MarketStream.CAPACITY_MARKET,
                consented=True,
                agreed_rate_gbp=50.0,
                agreed_rate_unit=RateUnit.GBP_PER_KW_YEAR,
            )
        ]
    )
    ctx = GateContext(deliverability_confirmed=True)
    d = _dispatch(power_mw=5.0, duration_h=2.0, deliverable_capacity_mw=1.0)
    v = REG.get(MarketStream.CAPACITY_MARKET).value_function(d, site, LIB, None, ctx)
    assert v.annual_revenue_gbp == 50.0 * 1.0 * 1000  # agreed £/kW/yr × 1.0 MW × 1000 (not the £30)
    assert any("customer-agreed" in w for w in v.warnings)
    assert any("not bankable" in w for w in v.warnings)


def test_proxy_bm_prefers_the_consented_agreed_rate() -> None:
    site = _consented(
        [
            s.RevenueStreamConsent(
                stream=MarketStream.BALANCING_MECHANISM,
                consented=True,
                agreed_rate_gbp=10_000.0,
                agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
            )
        ],
        mec_kw=2_500.0,  # D-92: size-eligible, else consent alone earns nothing
    )
    v = REG.get(MarketStream.BALANCING_MECHANISM).value_function(
        _dispatch(power_mw=2.0), site, LIB, None
    )
    assert v.annual_revenue_gbp == 10_000.0 * 2.0  # agreed £/MW/yr × sellable (whole) MW
    assert v.firmness == Firmness.CONTRACTED  # consent opens the gate to contracted, never firm
    assert any("customer-agreed" in w for w in v.warnings)


def test_proxy_ancillary_prefers_the_consented_agreed_rate() -> None:
    site = _consented(
        [
            s.RevenueStreamConsent(
                stream=MarketStream.ANCILLARY_DC_DM_DR,
                consented=True,
                agreed_rate_gbp=8_000.0,
                agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
            )
        ],
        mec_kw=2_500.0,  # D-92: size-eligible, else consent alone earns nothing
    )
    v = REG.get(MarketStream.ANCILLARY_DC_DM_DR).value_function(
        _dispatch(power_mw=2.0), site, LIB, None
    )
    assert v.annual_revenue_gbp > 0  # agreed rate × power × capability-decay factor
    assert v.firmness == Firmness.CONTRACTED
    assert any("customer-agreed" in w for w in v.warnings)


def test_proxy_gated_without_consent_is_unchanged() -> None:
    # No consent → the proxy stays on the assumption placeholder (£0 gated upside), never firm.
    for sid in (MarketStream.BALANCING_MECHANISM, MarketStream.ANCILLARY_DC_DM_DR):
        v = REG.get(sid).value_function(_dispatch(power_mw=2.0), _rich_site(), LIB, None)
        assert v.annual_revenue_gbp == 0.0
        assert v.firmness != Firmness.FIRM


# --- GateContext is additive + determinism ----------------------------------


def test_gate_context_additive_backward_compatible() -> None:
    site = _rich_site()
    for sid in MarketStream:
        a = REG.get(sid).check_gates(site, LIB)  # no context
        b = REG.get(sid).check_gates(site, LIB, None)  # explicit None
        assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_context_opens_cm_and_piclo_gates() -> None:
    site = _rich_site()
    # D-92: CM needs a connection that can prequalify before the deliverability context can open it.
    cm = REG.get(MarketStream.CAPACITY_MARKET).check_gates(
        _cm_site(), LIB, GateContext(deliverability_confirmed=True)
    )
    assert cm.gate_status.value == "open" and cm.firmness == Firmness.CONTRACTED
    piclo = REG.get(MarketStream.LOCAL_FLEX_PICLO).check_gates(
        site, LIB, GateContext(local_flex_tender_open=True)
    )
    assert piclo.gate_status.value == "open" and piclo.firmness == Firmness.CONTRACTED


def test_priced_paths_are_deterministic() -> None:
    ctx = GateContext(deliverability_confirmed=True, local_flex_tender_open=True)
    d = _dispatch(power_mw=2.0, duration_h=2.0, tender_window_discharge_mwh=50.0)
    scen = SimpleNamespace(price_multiplier=1.1)
    for sid in MarketStream:
        v1 = _value(sid, d, scenario=scen, context=ctx).model_dump(mode="json")
        v2 = _value(sid, d, scenario=scen, context=ctx).model_dump(mode="json")
        assert v1 == v2
