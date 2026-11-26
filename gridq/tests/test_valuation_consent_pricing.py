"""Consent-driven pricing + placement in the engine valuation path (Revenue-Data Drive / 2b, D43).

These exercise `build_value_streams` directly over a synthetic schedule + a site carrying
per-stream consents, proving the locked estimation ladder (agreed rate > assumption > £0), the
de-rated deliverable MW volume basis for BM/ancillary (parity with CM), the local-flex
aggregator-cut netting bug-fix, and the placement rule (a consented + priced CONTRACTED gated
stream moves into `value_streams`; everything else stays `gated_upside`). Volumes are always
schedule-derived; a consented stream without its needed rate stays £0 with the "consent given"
label.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import RateUnit


def _schedule(
    *,
    deliverable_capacity_mw: float | None = 0.2,
    tender_mwh: float | None = None,
    power_kw: float = 200.0,
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
        energy_price_gbp_per_kwh=None,
        deliverable_capacity_mw=deliverable_capacity_mw,
        tender_window_discharge_mwh=tender_mwh,
    )


def _consented_site(
    base: s.SiteModel,
    *,
    consents: list[s.RevenueStreamConsent],
    export_arrangement: s.ExportArrangement | None = None,
    dno_region: str | None = None,
    mec_kw: float | None = None,
) -> s.SiteModel:
    """A consented site; ``mec_kw`` makes the connection able to present a ≥1 MW unit (D-92).

    `make_site()` has a 300 kW MIC, which cannot host the 1 MW unit the Balancing Mechanism and the
    six dynamic-response products require — so consent alone earns nothing there, by design.
    """
    update: dict[str, object] = {"stream_consents": tuple(consents)}
    if export_arrangement is not None:
        update["export_arrangement"] = export_arrangement
    conn_update: dict[str, object] = {}
    if dno_region is not None:
        conn_update["dno_region"] = dno_region
    if mec_kw is not None:
        conn_update["mec_kw"] = mec_kw
    if conn_update:
        update["connection"] = base.connection.model_copy(update=conn_update)
    return s.SiteModel.model_validate({**base.model_dump(mode="python"), **update})


def _stream(streams: list[s.ValueStreamResult], sid: s.MarketStream) -> s.ValueStreamResult | None:
    return next((v for v in streams if v.stream == sid), None)


# --- Capacity Market: agreed rate ahead of the assumption clearing price ------


def test_cm_consent_and_agreed_rate_prices_at_the_agreed_rate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[
            s.RevenueStreamConsent(
                stream=s.MarketStream.CAPACITY_MARKET,
                consented=True,
                agreed_rate_gbp=50.0,
                agreed_rate_unit=RateUnit.GBP_PER_KW_YEAR,
            )
        ],
        mec_kw=4_000.0,  # D-92: CM prequalification needs ≥1 MW of connection capacity
    )
    # D-92: a 4 MW unit clears the 1 MW prequalification threshold, while the RESERVED de-rated
    # capability stays 0.2 MW — which is exactly the point the register row makes, that the
    # threshold applies to connection capacity and NOT to de-rated capacity. So the £ below is
    # unchanged from before the gate was armed.
    value_streams, _, _ = build_value_streams(_schedule(power_kw=4_000.0), site, lib)
    cm = _stream(value_streams, s.MarketStream.CAPACITY_MARKET)
    assert cm is not None
    # WP-CORE-01 triage (2026-11-16): re-pinned. Was £50 × 0.2 MW × 1000 = £10,000. FIX 5/7
    # (05133e4, 2026-08-19; changelog 2026-08-19 item 5, "THE CM DE-RATING LANDS ONCE") treats a
    # consented CM rate as quoted on DE-RATED capacity at the 2 h reference bin, divides that bin
    # back out, and applies the de-rating once, on the volume. gb_v0_1's 2 h bin is 0.65, so:
    #   (£50 / 0.65) × 0.2 MW × 1000 = £76.923/kW × 200 kW = £15,384.62.
    # The code's own CONFIRMATION ASK (rated vs de-rated quoting) stays open on the record.
    assert cm.annual_revenue_gbp == pytest.approx(50.0 / 0.65 * 200.0, abs=0.005)
    assert cm.annual_revenue_gbp == 15_384.62
    assert cm.consent_status == "given"
    assert any("customer-agreed" in w for w in cm.warnings)


def test_cm_consent_without_a_rate_falls_back_to_the_assumption_clearing_price(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[s.RevenueStreamConsent(stream=s.MarketStream.CAPACITY_MARKET, consented=True)],
        mec_kw=4_000.0,  # D-92: CM prequalification needs ≥1 MW of connection capacity
    )
    value_streams, _, _ = build_value_streams(_schedule(power_kw=4_000.0), site, lib)
    cm = _stream(value_streams, s.MarketStream.CAPACITY_MARKET)
    assert cm is not None
    # £30 clearing × 0.2 MW × 1000 = £6,000 (identical to the no-consent number).
    assert cm.annual_revenue_gbp == 6_000.0
    assert cm.consent_status == "given"


# --- BM / ancillary: agreed £/MW/yr × de-rated deliverable MW, then placement --


def test_bm_consent_and_agreed_rate_prices_and_moves_into_the_priced_floor(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[
            s.RevenueStreamConsent(
                stream=s.MarketStream.BALANCING_MECHANISM,
                consented=True,
                agreed_rate_gbp=10_000.0,
                agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
            )
        ],
        mec_kw=4_000.0,  # D-92: the connection must be able to present a ≥1 MW BM Unit
    )
    # D-92: a 4 MW unit with 2 MW of de-rated deliverable capability — size-eligible, and the
    # de-rated MW is a whole number so nothing is lost to whole-MW sell-order granularity. The
    # previous version of this test used a 200 kW unit with 0.2 MW de-rated, which cannot enter the
    # Balancing Mechanism at all; it asserted £2,000/yr of contracted floor revenue for a market
    # the asset is barred from.
    schedule = _schedule(power_kw=4_000.0, deliverable_capacity_mw=2.0)
    value_streams, gated_upside, _ = build_value_streams(schedule, site, lib)
    bm = _stream(value_streams, s.MarketStream.BALANCING_MECHANISM)
    assert bm is not None, "a consented + priced gated stream must move into value_streams"
    # WP-CORE-01 triage (2026-11-16): re-pinned from £20,000 (£10,000 × 2.0 MW de-rated). D57 item
    # 2 (7405d4b, 2026-08-18; D57 limb 1 in docs/decisions.md) retired the de-rated volume for BM
    # and DC/DM/DR: the volume is now `gb_streams.sellable_volume_mw` at the unit's own power,
    # NOT de-rated (D-102). A 4.0 MW standalone unit sells 4 whole MW, so:
    #   £10,000/MW/yr × 4.0 MW = £40,000. `deliverable_capacity_mw=2.0` no longer enters BM.
    assert bm.annual_revenue_gbp == 40_000.0
    assert bm.firmness == s.Firmness.CONTRACTED
    assert bm.consent_status == "given"
    assert _stream(gated_upside, s.MarketStream.BALANCING_MECHANISM) is None


def test_bm_consent_without_a_rate_stays_zero_conditional_in_gated_upside(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[
            s.RevenueStreamConsent(stream=s.MarketStream.BALANCING_MECHANISM, consented=True)
        ],
    )
    value_streams, gated_upside, _ = build_value_streams(_schedule(), site, lib)
    assert _stream(value_streams, s.MarketStream.BALANCING_MECHANISM) is None
    bm = _stream(gated_upside, s.MarketStream.BALANCING_MECHANISM)
    assert bm is not None
    assert bm.annual_revenue_gbp == 0.0
    assert bm.consent_status == "given"  # authorisation label survives even with £0


def test_ancillary_consent_and_agreed_rate_prices_into_the_floor(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[
            s.RevenueStreamConsent(
                stream=s.MarketStream.ANCILLARY_DC_DM_DR,
                consented=True,
                agreed_rate_gbp=8_000.0,
                agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
            )
        ],
        mec_kw=4_000.0,  # D-92: 1 MW minimum per response unit for all six DC/DM/DR products
    )
    schedule = _schedule(power_kw=4_000.0, deliverable_capacity_mw=2.0)
    value_streams, _, _ = build_value_streams(schedule, site, lib)
    anc = _stream(value_streams, s.MarketStream.ANCILLARY_DC_DM_DR)
    assert anc is not None
    # D-158: £8,000/MW/yr × 2.0 MW sellable, netted by the availability factor F and performance
    # factor K. gb_v0_1 carries neither key, so both are held at 1.0 as a labelled UPPER BOUND —
    # the machinery is exercised here, and the data asks are asserted below.
    # WP-CORE-01 triage (2026-11-16): re-pinned from £16,000 (2.0 MW de-rated). Same cause as the
    # BM test above, D57 item 2 (7405d4b): the volume is the unit's own 4.0 MW, so the record's
    # own arithmetic reads "£8,000.00/MW/yr x 4 MW x F 1.0000 x K 1.0000 = £32,000.00/yr".
    assert anc.annual_revenue_gbp == 32_000.0
    assert anc.firmness == s.Firmness.CONTRACTED
    assert any("availability factor F" in w for w in anc.warnings)
    assert any("performance factor K" in w for w in anc.warnings)


# --- an unconsented gated stream is unchanged (stays gated upside, £0) ---------


def test_unconsented_gated_streams_stay_gated_upside_at_zero(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    value_streams, gated_upside, _ = build_value_streams(_schedule(), make_site(), lib)
    for sid in (
        s.MarketStream.WHOLESALE_P415,
        s.MarketStream.BALANCING_MECHANISM,
        s.MarketStream.ANCILLARY_DC_DM_DR,
    ):
        assert _stream(value_streams, sid) is None
        g = _stream(gated_upside, sid)
        assert g is not None and g.annual_revenue_gbp == 0.0 and g.consent_status is None


# --- local flex: the aggregator cut is netted (engine-path bug-fix) -----------


def test_local_flex_nets_the_aggregator_cut(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(
        make_site(),
        consents=[],
        export_arrangement=s.ExportArrangement(aggregator_cut_pct=0.2),
        dno_region="east_midlands",
    )
    value_streams, _, _ = build_value_streams(_schedule(tender_mwh=10.0), site, lib)
    flex = _stream(value_streams, s.MarketStream.LOCAL_FLEX_PICLO)
    assert flex is not None
    # £150/MWh × 10 MWh = £1,500 gross; net of the 20% cut = £1,200.
    # F-19: 10 MWh x 5% call rate x GBP150/MWh = GBP75 gross, less the 20% aggregator cut.
    assert flex.annual_revenue_gbp == 60.0
    assert any("aggregator cut" in w for w in flex.warnings)


def test_local_flex_without_a_cut_is_gross(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _consented_site(make_site(), consents=[], dno_region="east_midlands")
    value_streams, _, _ = build_value_streams(_schedule(tender_mwh=10.0), site, lib)
    flex = _stream(value_streams, s.MarketStream.LOCAL_FLEX_PICLO)
    assert flex is not None
    # F-19: availability is not utilisation. 10 MWh delivered in the awarded windows x a 5%
    # sourced call rate x GBP150/MWh = GBP75. This test previously asserted GBP1,500 — the
    # whole year's in-window discharge paid as instructed calls, i.e. a 100% call rate.
    assert flex.annual_revenue_gbp == 75.0  # no cut configured -> gross == net
