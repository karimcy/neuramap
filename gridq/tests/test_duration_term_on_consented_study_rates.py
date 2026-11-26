"""A per-kW/yr study-basis rate is quoted at a DURATION, and two streams ignored it.

Two arithmetic defects, one shape. Both rates on the campaign site records are quoted at the
library's 2-hour node and were applied to a candidate of any duration:

*   **BALANCING_MECHANISM** was paid GBP 13,980/MW/yr - that is
    ``balancing_mechanism.study_basis_marginal_value_gbp_per_kw_yr.central_gbp_per_kw_yr_2h`` =
    GBP 17.47/kW/yr less the 20 pct aggregator cut - to a **0.5-hour** asset, off a table the same
    key publishes as ``{1h: 10.25, 2h: 17.47, 4h: 28.74}``. BM value for an energy-limited asset is
    the spread uplift times the MWh of throughput, and a 0.5 h asset moves a quarter of the MWh a
    2 h asset moves per cycle; the library's own table nearly halves from 2 h to 1 h. Overstated
    1.70x. FIXED HERE.
*   **CAPACITY_MARKET** was de-rated TWICE. Its consented GBP 4.77/kW/yr is
    ``capacity_market.study_basis_forward_gbp_per_kw_yr_by_duration.by_duration_h['2']`` = GBP
    5.96/kW/yr (the T-4 2029-30 cleared GBP 27.10/kW/yr x that auction's published Storage
    Duration-Limited de-rating bin at 2 h, 0.2198) less the cut - a rate on DE-RATED capacity - and
    the volume was then de-rated again at the candidate's own bin. 0.2198 x 0.0557 = 0.01225 against
    a correct single factor of 0.0557: the haircut was 4.55x too large. UNDERSTATED, so this is the
    one fix in the set whose stream moves UP - and its EBITDA effect is approximately ZERO, because
    D58 limb 2 nets ``min(CM revenue, windowed CM-peak levy credit)`` off the reported credit.
*   **ANCILLARY_DC_DM_DR is DELIBERATELY NOT FIXED** and this file pins the reason on the record:
    ``ancillary_services.study_basis_availability_gbp_per_kw_yr_by_product`` is keyed BY PRODUCT and
    publishes no per-duration table for any of the six, so there is no sourced node to rescale onto
    and inventing one would be the fabricated factor D57 limb 5b keeps forbidding.

NEITHER FIX IS AN ELIGIBILITY CONSTRAINT. No gate, threshold, minimum size, consent test, award
requirement or stacking rule is added or restored; every stream stays priced on every site. These
are a duration term and a de-rating applied once.
"""

from __future__ import annotations

import numpy as np
import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation._assumptions import duration_rescaled_rate
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import (
    _capacity_market_value,
    _consented_gated_per_mw_yr_value,
)
from gridq.schemas import MarketStream
from gridq.sizing.deliverability import d57_portfolio_aggregate_mw
from gridq.value_stack import GateContext

RELEASE = "gb_v1_12"
LIB = load_assumptions(RELEASE)


def _site() -> s.SiteModel:
    """A SYNTHETIC site carrying the campaign's study-basis consents, at the campaign's rates.

    The rates are library figures, not a customer's: GBP 13,980/MW/yr is the BM 2 h node (GBP
    17.47/kW/yr) less the 20 pct cut, GBP 4.77/kW/yr is the CM 2 h forward (GBP 5.96/kW/yr) less
    the cut, and GBP 16,600/MW/yr is the DCL availability net of the cut. This used to read a
    campaign record that WP-GRC-05 quarantines until its DPA is registered.
    """
    unit = s.RateUnit
    consents = [
        s.RevenueStreamConsent(
            stream=MarketStream.BALANCING_MECHANISM,
            consented=True,
            agreed_rate_gbp=13_980.0,
            agreed_rate_unit=unit.GBP_PER_MW_YEAR,
            notes="SYNTHETIC: BM study-basis 2 h node GBP 17.47/kW/yr less the 20 pct cut",
        ),
        s.RevenueStreamConsent(
            stream=MarketStream.CAPACITY_MARKET,
            consented=True,
            agreed_rate_gbp=4.77,
            agreed_rate_unit=unit.GBP_PER_KW_YEAR,
            notes="SYNTHETIC: CM study-basis 2 h forward GBP 5.96/kW/yr less the 20 pct cut",
        ),
        s.RevenueStreamConsent(
            stream=MarketStream.ANCILLARY_DC_DM_DR,
            consented=True,
            agreed_rate_gbp=16_600.0,
            agreed_rate_unit=unit.GBP_PER_MW_YEAR,
            notes="SYNTHETIC: DCL study-basis availability net of the 20 pct cut",
        ),
    ]
    return s.SiteModel(
        site_id="SYNTHETIC-duration-term",
        site_name="SYNTHETIC consented study-basis site (not a real site)",
        postcode="ZZ1 1AA",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=1_250_000.0),
        annual_consumption_kwh=1_250_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="SYNTHETIC",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(mic_kw=500.0, dno_region="SSEN_SEPD"),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="SYNTHETIC Operator A Ltd (not a real company)"),
        stream_consents=consents,
    )


def _ctx() -> GateContext:
    """The D57 aggregation route, which is what lets a sub-1 MW unit reach these markets at all."""
    portfolio = d57_portfolio_aggregate_mw()
    return GateContext(portfolio_mw=portfolio, portfolio_threshold_mw=portfolio)


def _schedule(power_kw: float, duration_h: float) -> HalfHourlyDispatchSchedule:
    n = 48
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * duration_h,
        duration_hours=duration_h,
        interval_hours=0.5,
        annual_factor=365.0,
        charge_kw=np.zeros(n),
        discharge_kw=np.zeros(n),
        soe_kwh=np.zeros(n),
        site_load_kw=np.zeros(n),
        duos_rate_gbp_per_kwh=np.zeros(n),
        red_band_mask=np.zeros(n, dtype=bool),
    )


# ----------------------------------------------------------------- fix 4: the BM duration term


def test_bm_is_priced_at_the_tables_own_node_for_the_candidates_duration() -> None:
    """GBP 17.47/kW/yr is the 2 h row; a 0.5 h asset takes the 1 h row, the smallest published.

    It dies if `_duration_rescaled_consent_rate` stops being called from
    `_consented_gated_per_mw_yr_value`, and it dies if the BM entry is removed from
    `_STREAM_DURATION_TABLE`.
    """
    site, ctx = _site(), _ctx()
    agreed = float(site.agreed_rate_for(MarketStream.BALANCING_MECHANISM) or 0.0)
    assert agreed == 13_980.0
    table = LIB.get("balancing_mechanism", "study_basis_marginal_value_gbp_per_kw_yr").value
    nodes = table["method_b_bottom_up"]["gbp_per_kw_yr"]
    assert nodes == {"1h": 10.25, "2h": 17.47, "4h": 28.74}

    power_kw = 10.691
    per_kw: dict[float, float] = {}
    for duration_h in (0.5, 1.0, 2.0, 4.0):
        revenue, used, notes = _consented_gated_per_mw_yr_value(
            MarketStream.BALANCING_MECHANISM, _schedule(power_kw, duration_h), site, LIB, ctx
        )
        per_kw[duration_h] = revenue / power_kw
        assert "balancing_mechanism.study_basis_marginal_value_gbp_per_kw_yr" in used or (
            duration_h == 2.0
        )
        assert "DURATION-ADJUSTED" in " ".join(notes)

    # The audit's corrected figure: GBP 8.20/kW/yr at 0.5 h, against GBP 13.98 as run. 1.70x DOWN.
    assert per_kw[0.5] == pytest.approx(8.2023, abs=0.0002)
    assert per_kw[0.5] == pytest.approx(per_kw[1.0], rel=1e-9), "0.5 h floors onto the 1 h node"
    assert per_kw[2.0] == pytest.approx(agreed / 1000.0, rel=1e-9), "unchanged at the reference"
    assert per_kw[4.0] == pytest.approx(22.9986, abs=0.0002), "UP at 4 h: the table rises"
    assert per_kw[2.0] / per_kw[0.5] == pytest.approx(17.47 / 10.25, rel=1e-9)

    # The 0.5 h asset sits BELOW the table's smallest node and the record must say so, because the
    # figure it is given is the 1 h allowance and is therefore optimistic for a 0.5 h asset.
    _r, _u, notes = _consented_gated_per_mw_yr_value(
        MarketStream.BALANCING_MECHANISM, _schedule(power_kw, 0.5), site, LIB, ctx
    )
    assert "BELOW the smallest tabulated node" in " ".join(notes)


def test_ancillary_says_why_it_carries_no_duration_term_rather_than_inventing_one() -> None:
    """No per-duration table exists for the EAC response products, so none is fabricated.

    It dies if ANCILLARY_DC_DM_DR is quietly added to `_STREAM_DURATION_TABLE` against a table it
    does not have, and it dies if the stated reason is dropped from the record.
    """
    site, ctx = _site(), _ctx()
    products = LIB.get(
        "ancillary_services", "study_basis_availability_gbp_per_kw_yr_by_product"
    ).value
    assert "by_product" in products
    for row in products["by_product"].values():
        assert not any(str(k).endswith("h") and "throughput" not in str(k) for k in row), (
            "a per-duration table has appeared for a response product; wire the duration term"
        )
    revenue, _used, notes = _consented_gated_per_mw_yr_value(
        MarketStream.ANCILLARY_DC_DM_DR, _schedule(10.691, 0.5), site, LIB, ctx
    )
    assert revenue > 0.0, "the stream stays priced (D57): nothing is gated by this"
    text = " ".join(notes)
    assert "RATE NOT DURATION-ADJUSTED" in text
    assert "none is invented" in text
    assert "DATA ASK" in text


# ------------------------------------------------- fix 5: the CM de-rating is applied exactly once


def test_a_consented_derated_cm_rate_is_re_based_so_the_derating_lands_once() -> None:
    """0.2198 x 0.0557 was applied where the correct single factor is 0.0557: a 4.55x haircut.

    It dies if the re-basing division is removed from `_capacity_market_value`.
    """
    site, ctx = _site(), _ctx()
    agreed = float(site.agreed_rate_for(MarketStream.CAPACITY_MARKET) or 0.0)
    assert agreed == 4.77
    derate = LIB.get("capacity_market", "de_rating_factor_by_duration_h").value
    ref, half = float(derate["2"]), float(derate["0.5"])
    assert (ref, half) == (0.2198, 0.0557)

    power_kw = 10.691
    revenue, used, notes = _capacity_market_value(_schedule(power_kw, 0.5), LIB, site, ctx)
    # ONE factor survives, and it is the candidate's OWN 0.5 h bin.
    assert revenue == pytest.approx((agreed / ref) * (power_kw * half), rel=1e-9)
    assert revenue / power_kw == pytest.approx(1.2088, abs=0.0002)
    # The double de-rating it replaces, pinned so it cannot return unnoticed.
    assert revenue / power_kw == pytest.approx(agreed * half / ref, rel=1e-9)
    assert revenue / power_kw > 4.5 * (agreed * half)
    text = " ".join(notes)
    assert "RE-BASED TO RATED CAPACITY" in text
    assert "de-rating is applied ONCE" in text
    assert "capacity_market.de_rating_factor_by_duration_h" in used

    # At the 2 h reference node the figure is UNCHANGED: re-base then de-rate is the identity.
    two_h, _u, _n = _capacity_market_value(_schedule(power_kw, 2.0), LIB, site, ctx)
    assert two_h / power_kw == pytest.approx(agreed, rel=1e-9)


def test_the_library_clearing_price_path_is_untouched_because_it_is_already_on_rated_capacity() -> (
    None
):
    """`clearing_price_gbp_per_kw_yr` is the auction headline and is NOT de-rated: no re-basing.

    Pinned because re-basing it too would multiply the stream by 1/0.2198 = 4.55x with no
    justification at all. It dies if the re-basing is hoisted out of the consented branch.
    """
    site = _site()
    stripped = site.model_copy(
        update={
            "stream_consents": [
                c for c in (site.stream_consents or ()) if c.stream != MarketStream.CAPACITY_MARKET
            ]
        }
    )
    clearing = float(LIB.get("capacity_market", "clearing_price_gbp_per_kw_yr").value)
    derate = float(LIB.get("capacity_market", "de_rating_factor_by_duration_h").value["0.5"])
    power_kw = 10.691
    revenue, _used, notes = _capacity_market_value(_schedule(power_kw, 0.5), LIB, stripped, _ctx())
    assert revenue == pytest.approx(clearing * power_kw * derate, rel=1e-9)
    assert "RE-BASED TO RATED CAPACITY" not in " ".join(notes)


# ------------------------------------------------------------- the shared helper's own arithmetic


def test_the_rescale_preserves_the_multiplier_and_never_interpolates() -> None:
    """The RATIO is applied, not the node: an aggregator cut inside the quoted rate survives.

    Reading ``table[duration]`` directly instead would silently discard whatever multiplier the
    quoted rate applied to the reference node - here a 20 pct route-to-market cut.
    """
    nodes = {"1h": 10.25, "2h": 17.47, "4h": 28.74}
    net_2h = 17.47 * 0.8
    for duration_h, node in ((1.0, 10.25), (2.0, 17.47), (4.0, 28.74)):
        rescaled, note = duration_rescaled_rate(
            net_2h,
            table=nodes,
            duration_h=duration_h,
            reference_duration_h=2.0,
            table_key="k",
        )
        assert rescaled == pytest.approx(node * 0.8, rel=1e-9), "the 20 pct cut survives"
        assert "DURATION-ADJUSTED" in note

    # No interpolation: 3 h floors onto the 2 h node rather than inventing a value between them.
    three_h, note = duration_rescaled_rate(
        net_2h, table=nodes, duration_h=3.0, reference_duration_h=2.0, table_key="k"
    )
    assert three_h == pytest.approx(net_2h, rel=1e-9)
    assert "never an interpolation" in note

    # An unreadable table NEVER fabricates a factor and never silently passes.
    passthrough, note = duration_rescaled_rate(
        net_2h, table=None, duration_h=0.5, reference_duration_h=2.0, table_key="k"
    )
    assert passthrough == net_2h
    assert "RATE NOT DURATION-ADJUSTED" in note
    assert "DIRECTION OF THE RESIDUAL ERROR" in note

    # A table with no reference node cannot preserve the multiplier, and says so.
    passthrough, note = duration_rescaled_rate(
        net_2h, table={"1h": 10.25}, duration_h=1.0, reference_duration_h=2.0, table_key="k"
    )
    assert passthrough == net_2h
    assert "publishes no 2 h node" in note
