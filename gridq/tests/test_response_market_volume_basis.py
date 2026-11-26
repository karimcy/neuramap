"""N-2 / N-3 — one sourced volume basis for the BM and DC/DM/DR, and where the paths still differ.

**N-3 — the basis.** Both markets contract in **plain MW of deliverable power**, in whole-MW sell
orders (`docs/bess_constants_register.md`: "For BM, MW values must be declared as integers"; "Sell
orders may only be submitted in whole MWs"; "minimum 1 MW contracted capacity"). Neither carries a
Capacity Market duration de-rating: that factor is a scaled EFC used for capacity-adequacy
accounting in the CM alone, and no register row applies it to a BM offer or an EAC response order.
The de-ratings that DO apply to DC/DM/DR are the availability factor F and the performance factor K,
applied separately by `eac_response_value` (D-158). Applying the CM factor as well de-rates a volume
that was never de-rateable and then truncates the result to whole MW, which turns the wrong basis
into a cliff: measured, a genuinely eligible 4 MW unit books **£0** because 4 × 0.2198 = 0.879
truncates to zero.

**N-2 — the divergence.** `gb_streams` claimed the two pricing paths "cannot disagree"; that was
true of *entry* and false about *money*. Measured on a consented 4 MW unit at £30,000/MW/yr, with
one stream consented so the shared capacity budget is not contending:

| deliverable_capacity_mw | BM engine | BM proxy | DC/DM/DR engine | DC/DM/DR proxy |
|---|---|---|---|---|
| 4.0 (= the unit) | £120,000 | £120,000 | £120,000 | £117,000 |
| 2.0 | £60,000 | £120,000 | £60,000 | £117,000 |
| unset | £0 | £120,000 | £0 | £117,000 |

The volume half of that is `optimisation.valuation._derated_deliverable_mw`, which is not this
lane's file; the £3,000 ancillary gap is the capability-decay factor the proxy applies and the
engine does not. What is fixed here is the half that can be: the volume has **one definition**
(:func:`gridq.value_stack.gb_streams.sellable_volume_mw`), the proxy consumes it rather than
restating it, the basis travels with the £, and the one configuration where the two paths genuinely
agree is pinned so it cannot silently break.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import Firmness, MarketStream, RateUnit
from gridq.value_stack import DispatchSummary, GateContext, default_registry
from gridq.value_stack.gb_streams import (
    eac_response_value,
    sellable_volume_mw,
)

LIB = load_assumptions("gb_v1_8")
REG = default_registry()
BM = MarketStream.BALANCING_MECHANISM
EAC = MarketStream.ANCILLARY_DC_DM_DR
SIZE_GATED = (BM, EAC)
AGREED_RATE = 30_000.0
CTX = GateContext(deliverability_confirmed=True)


def _site(*, streams: tuple[MarketStream, ...]) -> s.SiteModel:
    """A consented site with a connection ample enough that only the volume basis is in play."""
    return s.SiteModel(
        site_id="site_n3",
        site_name="N-3 volume-basis site",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_file_ref="examples/demo_site/hh_2025.csv",
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
            import_rates={"day": 0.24, "night": 0.12},
        ),
        connection=s.ConnectionDetails(mic_kw=5_000.0, mec_kw=5_000.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
        stream_consents=[
            s.RevenueStreamConsent(
                stream=stream,
                consented=True,
                agreed_rate_gbp=AGREED_RATE,
                agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
            )
            for stream in streams
        ],
    )


def _schedule(*, power_kw: float, deliverable_mw: float | None) -> HalfHourlyDispatchSchedule:
    n = 48
    zeros = np.zeros(n, dtype=float)
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=zeros,
        discharge_kw=zeros,
        soe_kwh=zeros,
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=np.zeros(n, dtype=bool),
        deliverable_capacity_mw=deliverable_mw,
    )


def _engine(stream: MarketStream, *, power_kw: float, deliverable_mw: float | None) -> float:
    """The engine path's £ for ``stream``, with only that stream consented."""
    value_streams, gated, _ = build_value_streams(
        _schedule(power_kw=power_kw, deliverable_mw=deliverable_mw),
        _site(streams=(stream,)),
        LIB,
        context=CTX,
    )
    entries = [v for v in (*value_streams, *gated) if v.stream == stream]
    assert len(entries) == 1
    return entries[0].annual_revenue_gbp


def _proxy(stream: MarketStream, *, power_mw: float, deliverable_mw: float | None):
    return REG.get(stream).value_function(
        DispatchSummary(power_mw=power_mw, duration_h=2.0, deliverable_capacity_mw=deliverable_mw),
        _site(streams=(stream,)),
        LIB,
        context=CTX,
    )


def _decay_factor() -> float:
    """The proxy's capability-decay factor, read from the library rather than assumed."""
    fade = LIB.get("degradation", "annual_capacity_fade_pct").value
    return 1.0 - float(fade) / 100.0


# --- N-3: the volume basis ---------------------------------------------------


@pytest.mark.parametrize("stream", SIZE_GATED)
@pytest.mark.parametrize(
    ("unit_mw", "expected"),
    [(4.0, 4.0), (2.0, 2.0), (1.6, 1.0), (1.0, 1.0), (0.9, None), (0.3, None)],
)
def test_the_volume_is_the_unit_in_whole_mw_never_a_de_rated_fraction(
    stream: MarketStream, unit_mw: float, expected: float | None
) -> None:
    """Plain deliverable MW, truncated to whole MW — the sourced order granularity, nothing else.

    Under a CM duration de-rating (0.2198 for a 2 h unit) every one of these rows would truncate to
    zero, including the 4 MW unit: 4 × 0.2198 = 0.879 ⇒ 0 MW offerable.
    """
    volume, _ = sellable_volume_mw(stream, unit_mw=unit_mw, portfolio_mw=None)
    assert volume == expected


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_volume_basis_cannot_depend_on_the_capacity_market_de_rating(
    stream: MarketStream,
) -> None:
    """A structural guarantee, not a coincidence: the definition takes no assumption library.

    Two releases carry materially different CM duration factors (gb_v0_1's unsourced 0.65 for 2 h vs
    gb_v1_8's published 0.2198), so a volume that consulted them would move between releases.
    """
    volume, notes = sellable_volume_mw(stream, unit_mw=4.0, portfolio_mw=None)
    assert volume == 4.0
    blob = " ".join(notes)
    assert "VOLUME BASIS" in blob
    assert "whole MW" in blob
    assert "Capacity Market duration de-rating" in blob


def test_each_market_states_the_basis_it_priced_and_its_source() -> None:
    """ "Say what you chose": the register row travels with the £, not just the number."""
    bm_notes = " ".join(sellable_volume_mw(BM, unit_mw=4.0, portfolio_mw=None)[1])
    assert "MW values must be declared as integers" in bm_notes
    assert "no sourced row applies it to a BM offer" in bm_notes
    eac_notes = " ".join(sellable_volume_mw(EAC, unit_mw=4.0, portfolio_mw=None)[1])
    assert "Sell orders may only be submitted in whole MWs" in eac_notes
    # The response product's own de-ratings are F and K, applied elsewhere — never a third haircut.
    assert "availability factor F" in eac_notes and "performance factor K" in eac_notes


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_a_fractional_unit_is_still_offerable_inside_an_aggregate(stream: MarketStream) -> None:
    """The route distinction survives the shared definition: an aggregator can sell 0.3 MW."""
    volume, notes = sellable_volume_mw(stream, unit_mw=0.3, portfolio_mw=12.0)
    assert volume == 0.3
    assert any("AGGREGATION" in n for n in notes)


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_an_ineligible_unit_has_no_offerable_volume_at_all(stream: MarketStream) -> None:
    """£0, not a smaller number: a market the asset cannot enter is worth nothing."""
    volume, notes = sellable_volume_mw(stream, unit_mw=0.3, portfolio_mw=None)
    assert volume is None
    assert any("INELIGIBLE" in n for n in notes)


# --- N-2: the proxy consumes the shared definition ---------------------------


def test_the_proxy_bm_price_is_the_agreed_rate_times_the_shared_volume() -> None:
    volume, _ = sellable_volume_mw(BM, unit_mw=4.0, portfolio_mw=None)
    assert volume is not None
    priced = _proxy(BM, power_mw=4.0, deliverable_mw=None)
    assert priced.annual_revenue_gbp == pytest.approx(AGREED_RATE * volume)
    assert priced.firmness == Firmness.CONTRACTED
    assert any("VOLUME BASIS" in w for w in priced.warnings)


def test_the_proxy_response_price_is_the_eac_structure_on_the_shared_volume() -> None:
    volume, _ = sellable_volume_mw(EAC, unit_mw=4.0, portfolio_mw=None)
    assert volume is not None
    expected, _, _ = eac_response_value(
        rate_gbp_per_mw_yr=AGREED_RATE, sellable_mw=volume, assumptions=LIB
    )
    priced = _proxy(EAC, power_mw=4.0, deliverable_mw=None)
    assert priced.annual_revenue_gbp == pytest.approx(expected * _decay_factor())
    assert any("VOLUME BASIS" in w for w in priced.warnings)


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_proxy_prices_zero_for_a_unit_that_cannot_enter(stream: MarketStream) -> None:
    priced = _proxy(stream, power_mw=0.3, deliverable_mw=None)
    assert priced.annual_revenue_gbp == 0.0
    assert any("CONSENT DOES NOT CREATE ELIGIBILITY" in w for w in priced.warnings)


# --- N-2: the parity that holds today, pinned so it cannot silently break ----


@pytest.mark.parametrize("power_mw", [4.0, 2.0, 1.0])
def test_engine_and_proxy_agree_on_bm_when_the_reserved_capability_is_the_whole_unit(
    power_mw: float,
) -> None:
    """The one configuration where the two volume bases coincide, so the £ must match exactly.

    It is a real regression guard rather than a tautology: it fails if either path changes its rate
    ladder, its truncation, or its treatment of the scenario multiplier — and it keeps holding once
    the engine adopts :func:`sellable_volume_mw`, because plain MW is what it then prices too.
    """
    engine = _engine(BM, power_kw=power_mw * 1_000.0, deliverable_mw=power_mw)
    proxy = _proxy(BM, power_mw=power_mw, deliverable_mw=power_mw).annual_revenue_gbp
    assert engine == pytest.approx(proxy)
    assert engine == pytest.approx(AGREED_RATE * power_mw)


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_engine_honours_the_aggregated_route_instead_of_truncating_it_away(
    stream: MarketStream,
) -> None:
    """A fractional unit inside an aggregate is SELLABLE, and the engine path must agree.

    The sourced predicate says it in terms — "Fractional unit MW is offerable inside the aggregate
    (that is what an aggregator is for), so ``whole_mw_orders`` does not truncate on this route"
    (`value_stack/gates.py`) — and `EligibilityVerdict.sellable_mw` is left untruncated there, which
    is why `sellable_volume_mw` returns 0.9 MW for the same input. The engine path applied the
    whole-MW rule route-blind, so it truncated 0.9 to 0 and reported £0 while its own preceding note
    said the 0.9 MW "is offerable inside the aggregate": one number, two contradictory statements.

    This matters beyond tidiness. Aggregation to the 1 MW registered-unit threshold is the ONLY
    route a sub-1 MW site has into the BM and the response family, every site in the live campaign
    is sub-1 MW, and truncation closed it — so no £ from either market could reach a reported
    number on any campaign site. Direction of effect: revenue UP on the aggregated route only.
    """
    unit_mw = 0.9  # below the 1 MW minimum standalone, and deliberately fractional
    ctx = GateContext(deliverability_confirmed=True, portfolio_mw=12.0)
    expected, _ = sellable_volume_mw(stream, unit_mw=unit_mw, portfolio_mw=12.0)
    assert expected == unit_mw, "the sourced definition does not truncate the aggregated route"

    value_streams, gated, _ = build_value_streams(
        _schedule(power_kw=unit_mw * 1_000.0, deliverable_mw=unit_mw),
        _site(streams=(stream,)),
        LIB,
        context=ctx,
    )
    entry = next(v for v in (*value_streams, *gated) if v.stream == stream)
    if stream is BM:
        assert entry.annual_revenue_gbp == pytest.approx(AGREED_RATE * unit_mw)
    else:
        # The response family nets F and K on the same volume (D-158); only the volume is at issue.
        anc_expected, _, _ = eac_response_value(
            rate_gbp_per_mw_yr=AGREED_RATE, sellable_mw=unit_mw, assumptions=LIB
        )
        assert entry.annual_revenue_gbp == pytest.approx(anc_expected)
    assert entry.firmness == Firmness.CONTRACTED
    assert any(v.stream == stream for v in value_streams), "it must reach the priced floor"
    # And the contradiction is gone: no truncation note against a volume that was never truncated.
    assert not any("truncates to 0 MW as a sell order" in w for w in entry.warnings)


def test_a_standalone_fractional_unit_is_still_truncated_away() -> None:
    """The other half of the route rule, so the fix above cannot be read as removing truncation.

    Same 1.6 MW unit, NO portfolio: eligible standalone, but only 1 whole MW is offerable. This is
    the guarantee that a sub-1 MW STANDALONE unit gains nothing — the statutory threshold and the
    integer-order rule both still bind, exactly as `sellable_volume_mw` reports them.
    """
    assert sellable_volume_mw(BM, unit_mw=1.6, portfolio_mw=None)[0] == 1.0
    value_streams, gated, _ = build_value_streams(
        _schedule(power_kw=1_600.0, deliverable_mw=1.6), _site(streams=(BM,)), LIB, context=CTX
    )
    entry = next(v for v in (*value_streams, *gated) if v.stream == BM)
    assert entry.annual_revenue_gbp == pytest.approx(AGREED_RATE * 1.0)  # 1 MW, not 1.6
    # WP-CORE-01 triage (2026-11-16): the truncation's stated reason is re-pinned to its new
    # source. D57 item 2 (7405d4b, 2026-08-18) moved the engine volume onto
    # `gb_streams.sellable_volume_mw`, which truncates on the standalone route and states why in
    # the verdict's own words (asserted verbatim above via the same function). The engine's
    # downstream "whole-MW sell orders: ..." note fires only when `sellable != mw`, which can no
    # longer happen because `mw` arrives already truncated, so that note is unreachable here. The
    # £ is unchanged (1 MW, asserted above); only which line of the record carries the reason moved.
    reason = (
        "sell orders are accepted only in WHOLE MW, so 1 MW of the unit's 1.600 MW is offerable"
    )
    assert any(reason in w for w in entry.warnings)


def test_both_paths_zero_a_unit_below_the_sourced_minimum() -> None:
    """Entry is the half the two paths DO agree on, and it is worth pinning that they still do."""
    for stream in SIZE_GATED:
        assert _engine(stream, power_kw=300.0, deliverable_mw=0.3) == 0.0
        assert _proxy(stream, power_mw=0.3, deliverable_mw=0.3).annual_revenue_gbp == 0.0
