"""DFS scaled to the evidence: the OBSERVED period count, the SITE'S OWN ZONE, and THIS battery.

Owner instruction, 2026-08-19, verbatim: "ok scale the DFS then to the evidence, scaling based on
battery size and location to get a more accurate view."

DFS was the largest single stream in the published campaign and the softest number in it. Three
things were wrong with the way it was scaled, and this suite pins each one:

1. **The period count was an EXTRAPOLATION.** Zone 6's 407 accepted settlement periods, observed
   over 127 spring-and-summer days, were annualised by 365/127 to 1,170 called half-hours a year,
   so 65% of the modelled calls had never been seen. The observed count is now used as the annual
   count. DIRECTION OF EFFECT: DOWN, 2.87x on the count.
2. **Every site was priced in ONE zone**, the most favourable of the twelve (zone 6). DFS
   is zonal, the price and the call count move in OPPOSITE directions across the zones, and each
   campaign site's own zone is derivable by point-in-polygon from its postcode.
3. **The proxy path applied a REGISTRATION ROUNDING RULE to the money** (a 0.25 MW unit was paid on
   0.2 MW) and had no energy term at all, so a 0.5 h battery was ranked as though it could serve a
   five-half-hour evening window at full power.

Every figure here is measured, and none of it is a gate: D57 forbids suppressing DFS for any
eligibility reason, and nothing in this suite does. What is enforced is arithmetic - the right rate,
on the right volume, over the right number of periods, for the right zone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import MarketStream, SiteModel
from gridq.value_stack.dispatch import DispatchSummary
from gridq.value_stack.gb_streams import (
    _DFS_SUBSTITUTE_ZONE,
    _DFS_ZONE_BY_OUTWARD_CODE,
    _DFS_ZONE_OBSERVED,
    DemandFlexibilityServiceStream,
    dfs_service_period_mask,
    dfs_utilisation_rate_gbp_per_kwh,
    dfs_zone_basis,
    dfs_zone_for_site,
)

LIB = load_assumptions("gb_v1_12")
REPO = Path(__file__).resolve().parents[1]

#: Every campaign site's own DFS zone, resolved 2026-08-19 by point-in-polygon against NESO's
#: published 12-zone GeoJSON (zip sha256 ed3fa1cc8948..., the value the library already records),
#: with postcode centroids from postcodes.io over the ONS Postcode Directory. Reproducible:
#: ``python3 scripts/derive_dfs_zonal_basis.py --zones``. The full postcode each district was
#: checked against is in the comment, and all nine agreed with their district centroid.
CAMPAIGN_ZONES = {
    "GL52": 10,  # GL52 2RT Bishops Cleeve Ash Plant, GL52 7RT Bishops Cleeve MRF
    "OX10": 10,  # OX10 6PJ Ewelme (both records)
    "OX11": 10,  # OX11 0QX Harwell campus
    "RG10": 10,  # RG10 9XY Knowl Hill (both records)
    "SL3": 12,  # SL3 0EG Colnbrook and Tanhouse, one private wire
}


def _site_with_postcode(tmp_path: Path, postcode: str, *, days: int = 120) -> SiteModel:
    """The shared MILP fixture site, at ``postcode``, over a horizon long enough to be called in.

    The horizon matters: the mask allocates a zone's own annual period count PRO RATA to the
    horizon's length, so a 7-day window gives the thinnest zone (82 periods a year) a target below
    one and the comparison would be against an empty mask rather than against a different price.
    """
    path = tmp_path / f"load_{postcode.replace(' ', '_')}_{days}.csv"
    rows, ts = write_hh_csv(path, days=days)
    site = multi_rate_site(path, rows=rows, site_id=f"site_dfs_{postcode.replace(' ', '_')}")
    load = site.load_data.model_copy(
        update={
            "half_hourly_rows": rows,
            "start_date": ts[0].date(),
            "end_date": ts[-1].date(),
        }
    )
    return site.model_copy(update={"postcode": postcode, "load_data": load})


def _solve(site: SiteModel, *, power_kw: float, duration_h: float = 2.0):
    solver = MilpDispatchSolver()
    solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=LIB,
            power_kw=power_kw,
            energy_kwh=power_kw * duration_h,
            duration_hours=duration_h,
        )
    )
    schedule = solver.last_schedule()
    assert schedule is not None
    return schedule


# --- the EVIDENCE: the constants are the source's own numbers -----------------


def test_the_module_constants_reproduce_nesos_own_utilisation_report() -> None:
    """The 12-zone table is RECOMPUTED from the CSV, not trusted.

    The constants are transcribed rather than read at solve time because
    ``data/fetched/neso/dfs_util_cur.csv`` is a LIVE resource NESO grows daily and ``input_hash``
    pins no data content, so a re-fetch would move every DFS pound under an unchanged run id. This
    test is the other half of that bargain: it recomputes all four quantities per zone from the file
    itself, through the same script that generated them, and fails loudly if the file has moved off
    its recorded sha256 (a moved file is a deliberate re-derivation with a changelog entry, never a
    silent drift).

    Dies if any count, day count, price or histogram bucket in ``_DFS_ZONE_OBSERVED`` is edited by
    hand.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from derive_dfs_zonal_basis import zonal_basis

    basis = zonal_basis()
    assert sorted(basis) == list(range(1, 13))
    for zone, (periods, days, price, histogram) in basis.items():
        observed = _DFS_ZONE_OBSERVED[zone]
        assert observed.accepted_settlement_periods == periods, zone
        assert observed.event_days == days, zone
        assert observed.utilisation_price_gbp_per_mwh == price, zone
        assert observed.window_histogram == histogram, zone
        # The histogram IS the same measurement as the count, so it sums to it.
        assert sum(histogram.values()) == periods, zone
    # The campaign's own two zones, spelled out so a change to either is visible in the diff.
    assert (
        _DFS_ZONE_OBSERVED[10].accepted_settlement_periods,
        _DFS_ZONE_OBSERVED[10].event_days,
        _DFS_ZONE_OBSERVED[10].utilisation_price_gbp_per_mwh,
    ) == (370, 51, 163.979)
    assert (
        _DFS_ZONE_OBSERVED[12].accepted_settlement_periods,
        _DFS_ZONE_OBSERVED[12].event_days,
        _DFS_ZONE_OBSERVED[12].utilisation_price_gbp_per_mwh,
    ) == (366, 47, 162.533)


def test_the_library_and_the_module_carry_the_same_twelve_zones() -> None:
    """gb_v1_12's own copy of the table equals the module's, so the £ can cite a key.

    Two copies of one number is a drift risk, and the alternative was worse: with no library key the
    D59 per-stream register prints a traceability gap for the largest stream in the pack. The key is
    therefore the citable copy and this test is the pin.

    Dies if either copy is edited without the other.
    """
    for zone, observed in _DFS_ZONE_OBSERVED.items():
        from_library, used, notes = dfs_zone_basis(LIB, zone=zone)
        assert used == ["ancillary_services.dfs_zonal_observed_2026_27"], zone
        assert notes == [], zone
        assert from_library.accepted_settlement_periods == observed.accepted_settlement_periods
        assert from_library.event_days == observed.event_days
        assert (
            from_library.utilisation_price_gbp_per_mwh == observed.utilisation_price_gbp_per_mwh
        ), zone


def test_a_release_without_the_key_falls_back_to_the_constants_and_says_so() -> None:
    """An older release must not read as zero, and must not read as sourced either."""
    older = load_assumptions("gb_v1_11")
    observed, used, notes = dfs_zone_basis(older, zone=10)
    assert observed == _DFS_ZONE_OBSERVED[10]
    assert used == []
    assert "READ FROM THE MODULE CONSTANTS, NOT THE LIBRARY" in " ".join(notes)


# --- LOCATION ----------------------------------------------------------------


@pytest.mark.parametrize(("outward", "zone"), sorted(CAMPAIGN_ZONES.items()))
def test_every_campaign_postcode_resolves_to_its_own_zone(
    tmp_path: Path, outward: str, zone: int
) -> None:
    """The campaign spans TWO zones, and the resolver finds them from the record's own postcode.

    Dies if the resolver is short-circuited to a fixed zone, and dies if the derived
    outward-code table is edited away from the point-in-polygon result.
    """
    site = _site_with_postcode(tmp_path, outward, days=7)
    resolved_zone, resolved, notes = dfs_zone_for_site(site)
    assert (resolved_zone, resolved) == (zone, True)
    assert _DFS_ZONE_BY_OUTWARD_CODE[outward] == zone
    text = " ".join(notes)
    assert f"DFS ZONE {zone}, THIS SITE'S OWN" in text
    assert "point-in-polygon" in text
    assert "geojson_sha256" in text
    # The residual ask is the FULL postcode, because a district centroid is what was used.
    assert "the site's FULL postcode" in text


def test_an_unresolved_postcode_substitutes_in_full_and_never_zeroes(tmp_path: Path) -> None:
    """D57 limb 5b: substitute the best-evidenced zone, USE IT IN FULL, and declare it.

    An unresolved location is the exact circumstance in which a previous generation of this codebase
    would have zeroed a stream. It may not: the substitution is declared, the donor zone's clear is
    never called the site's own, and the £ is priced.
    """
    site = _site_with_postcode(tmp_path, "ZZ99", days=7)
    zone, resolved, notes = dfs_zone_for_site(site)
    assert (zone, resolved) == (_DFS_SUBSTITUTE_ZONE, False)
    text = " ".join(notes)
    assert "SUBSTITUTED AND USED IN FULL (D57 limb 5b)" in text
    assert "BEST-EVIDENCED" in text
    assert "NOT zeroed, deferred or held conditional" in text
    assert "A DONOR ZONE'S CLEAR IS NOT THIS SITE'S OWN" in text
    # And it is priceable: a substituted zone still has a real rate.
    rate, _used, _notes = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=zone)
    assert rate > 0.0


def test_two_zones_at_one_size_earn_different_dfs_pounds(tmp_path: Path) -> None:
    """LOCATION DEPENDENCE, measured on two real solves of the same battery.

    Same site, same load, same size, same release: only the postcode differs. Zone 1 (Highland,
    82 accepted periods at £209.699/MWh) against zone 6 (Midlands and Yorkshire, 407 at
    £159.711/MWh). The thin zone pays MORE per MWh and is called FAR less often, which is the
    inverse relationship a national average destroys, so the deep zone must win overall.

    Dies if the zone stops reaching the objective - a fixed zone, or a national average price, makes
    these two figures equal.
    """
    thin = _solve(_site_with_postcode(tmp_path, "IV1"), power_kw=100.0)
    deep = _solve(_site_with_postcode(tmp_path, "LE17"), power_kw=100.0)
    assert _DFS_ZONE_BY_OUTWARD_CODE["IV1"] == 1
    assert _DFS_ZONE_BY_OUTWARD_CODE["LE17"] == 6
    thin_periods = int((thin.dfs_price_gbp_per_kwh > 0).sum())
    deep_periods = int((deep.dfs_price_gbp_per_kwh > 0).sum())
    assert thin_periods < deep_periods
    # The rate is the thin zone's own, and it is HIGHER per kWh.
    assert thin.dfs_price_gbp_per_kwh.max() > deep.dfs_price_gbp_per_kwh.max()
    # But the £ is lower, because the calls are what is scarce.
    assert 0.0 < thin.dfs_utilisation_value_gbp < deep.dfs_utilisation_value_gbp


# --- the OBSERVED COUNT ------------------------------------------------------


@pytest.mark.parametrize("zone", [1, 6, 10, 12])
def test_the_mask_is_each_zones_own_observed_count_and_is_never_annualised(zone: int) -> None:
    """The mask places the zone's OBSERVED periods, not 365/127 times as many.

    Dies the moment the extrapolation returns: the annualised counts are 236, 1170, 1063 and 1052
    for these four zones, and every one of them is a different number from the observed count.
    """
    idx = pd.date_range("2025-08-13", periods=17_520, freq="30min")
    mask, notes = dfs_service_period_mask(idx, zone=zone, assumptions=LIB)
    observed = _DFS_ZONE_OBSERVED[zone].accepted_settlement_periods
    annualised = round(observed * 365.0 / 127.0)
    assert int(mask.sum()) == observed
    assert int(mask.sum()) != annualised
    text = " ".join(notes)
    assert "SCALED TO THE OBSERVED PERIODS, AND THE DIRECTION OF EFFECT IS DOWN" in text
    assert f"{annualised} called half-hours a year instead of {observed}" in text
    assert "MEASURED FLOOR" in text


def test_the_observed_count_is_what_the_campaigns_own_zones_get() -> None:
    """The two campaign zones, and the size of the change on each.

    Zone 10 (seven of the nine site records) goes from 1,063 modelled called half-hours to 370, and
    zone 12 (Colnbrook and Tanhouse) from 1,052 to 366. Both were previously priced in zone 6 at
    1,170, so the count falls 3.16x and 3.20x respectively and the rate rises 2.7% and 1.8%.
    """
    for zone, expected_periods, expected_annualised in ((10, 370, 1_063), (12, 366, 1_052)):
        observed = _DFS_ZONE_OBSERVED[zone]
        assert observed.accepted_settlement_periods == expected_periods
        assert round(expected_periods * 365.0 / 127.0) == expected_annualised
    previous_basis_periods = round(407 * 365.0 / 127.0)
    assert previous_basis_periods == 1_170
    assert previous_basis_periods / _DFS_ZONE_OBSERVED[10].accepted_settlement_periods == (
        pytest.approx(3.162, abs=0.001)
    )
    rate_10, _u, _n = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=10)
    rate_6, _u6, _n6 = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=6)
    assert rate_10 > rate_6  # the zone the campaign actually sits in pays MORE per MWh


# --- SIZE --------------------------------------------------------------------


def test_two_sizes_in_one_zone_earn_different_dfs_pounds(tmp_path: Path) -> None:
    """SIZE DEPENDENCE, measured on two real solves in the same zone.

    DFS settles on the OFFERED Service Volume, so a bigger battery offers more into the same called
    half-hour and earns more. The £ is the dispatch's own objective term, so this is not an
    assumption about scaling - it is the scaling.

    Dies if the DFS £ is ever replaced by a per-kW annuity: an annuity is linear by construction and
    would still differ here, so the assertion is deliberately stronger - the RATIO must be below the
    power ratio, because the state of energy and the site's own load bind on the larger unit.
    """
    site = _site_with_postcode(tmp_path, "LE17")
    small = _solve(site, power_kw=50.0)
    large = _solve(site, power_kw=200.0)
    assert small.dfs_utilisation_value_gbp > 0.0
    assert large.dfs_utilisation_value_gbp > small.dfs_utilisation_value_gbp
    ratio = large.dfs_utilisation_value_gbp / small.dfs_utilisation_value_gbp
    assert 1.0 < ratio <= 4.0, ratio
    # Same zone, so the same called periods and the same rate: only the delivered volume moved.
    assert int((small.dfs_price_gbp_per_kwh > 0).sum()) == int(
        (large.dfs_price_gbp_per_kwh > 0).sum()
    )
    assert small.dfs_price_gbp_per_kwh.max() == large.dfs_price_gbp_per_kwh.max()
    assert large.dfs_delivered_mwh > small.dfs_delivered_mwh


def test_a_longer_duration_earns_more_dfs_at_the_same_power(tmp_path: Path) -> None:
    """The ENERGY bound is real: the DFS window runs five to eight half-hours an evening.

    A 0.5 h battery cannot serve it and a 2 h one can, at identical rated power - which is why a
    flat £/kW/yr was the wrong shape for this stream and why the proxy carries an energy term.
    """
    site = _site_with_postcode(tmp_path, "LE17")
    short = _solve(site, power_kw=100.0, duration_h=0.5)
    long_ = _solve(site, power_kw=100.0, duration_h=2.0)
    assert short.dfs_utilisation_value_gbp > 0.0
    assert long_.dfs_utilisation_value_gbp > short.dfs_utilisation_value_gbp


def test_the_published_size_function_bounds_the_measured_figure(tmp_path: Path) -> None:
    """The note publishes a POWER-BOUNDED ceiling, and the measured £ must sit under it.

    The ceiling is `rate x kW x 0.5 h x called periods`, annualised - the same annualisation the
    measured £ carries, because comparing an annualised figure against a part-year ceiling reads
    above 100% and invites the reader to think the dispatch beat its own physics.

    Dies if the ceiling stops being annualised, or if it is computed on anything other than the
    battery's own rating.
    """
    schedule = _solve(_site_with_postcode(tmp_path, "LE17", days=120), power_kw=100.0)
    site = _site_with_postcode(tmp_path, "LE17", days=120)
    streams, _gated, _used = build_value_streams(schedule, site, LIB)
    dfs = next(v for v in streams if v.stream == MarketStream.DFS)
    text = " ".join(dfs.warnings)
    assert "IT SCALES WITH THE BATTERY, AND HERE IS THE FUNCTION" in text
    called = int((schedule.dfs_price_gbp_per_kwh > 0).sum())
    rate, _u, _n = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=6)
    ceiling = rate * 100.0 * 0.5 * called * schedule.annual_factor
    assert 0.0 < dfs.annual_revenue_gbp <= ceiling + 1e-6
    assert f"£{ceiling:,.2f}/yr" in text


def test_the_proxy_path_scales_with_power_and_duration_and_never_rounds_the_money_down(
    tmp_path: Path,
) -> None:
    """The coarse sweep's own DFS £: zonal, size-bounded, and free of the registration rounding.

    Three properties, all of which the previous flat-rate proxy lacked:
      * it is bounded by usable ENERGY as well as by power, so duration changes the £;
      * it prices the unit's OWN MW, not the Registered Unit Capacity truncated to 0.1 MW - D57
        limb 3 forbids a product threshold or rounding rule reducing a figure;
      * it uses the site's own zone, so the sweep ranks on the basis the confirm stage prices.

    Dies if the truncated capacity comes back as the volume (the 0.25 MW assertion), if the energy
    term is dropped (the duration assertion), or if the zone stops being read (the zone assertion).
    """
    stream = DemandFlexibilityServiceStream()
    site = _site_with_postcode(tmp_path, "LE17", days=7)
    elsewhere = _site_with_postcode(tmp_path, "IV1", days=7)

    def priced(model: SiteModel, power_mw: float, duration_h: float) -> float:
        result = stream.value_function(
            dispatch_schedule=DispatchSummary(power_mw=power_mw, duration_h=duration_h),
            site_model=model,
            assumptions=LIB,
        )
        assert result.stream == MarketStream.DFS
        return result.annual_revenue_gbp

    # DURATION moves the £ at constant power: the energy bound binds at 0.5 h and not at 4 h.
    short = priced(site, 0.25, 0.5)
    long_ = priced(site, 0.25, 4.0)
    assert 0.0 < short < long_
    # POWER moves it too.
    assert priced(site, 0.5, 4.0) > long_
    # LOCATION moves it: zone 1 is called a fifth as often as zone 6.
    assert priced(elsewhere, 0.25, 4.0) < long_
    # THE ROUNDING RULE DOES NOT MOVE IT DOWN. A 0.25 MW unit truncates to a 0.2 MW Registered Unit
    # Capacity; pricing that would be a 20% haircut delivered by a registration formality.
    at_truncated = priced(site, 0.2, 4.0)
    assert long_ > at_truncated
    assert long_ == pytest.approx(at_truncated * 0.25 / 0.2, rel=1e-6)


def test_the_proxy_notes_state_the_zone_the_bound_and_the_direction(tmp_path: Path) -> None:
    """The reader is told which bound bound, which zone paid, and which way the change went."""
    stream = DemandFlexibilityServiceStream()
    site = _site_with_postcode(tmp_path, "GL52", days=7)
    result = stream.value_function(
        dispatch_schedule=DispatchSummary(power_mw=0.25, duration_h=0.5),
        site_model=site,
        assumptions=LIB,
    )
    text = " ".join(result.warnings)
    assert "PROXY PATH" in text and "A FUNCTION OF BOTH SIZE AND LOCATION" in text
    assert "THE ENERGY BOUND BINDS" in text
    assert "NOT annualised by 365/127" in text
    assert "DFS ZONE 10, THIS SITE'S OWN" in text
    assert "THE TRUNCATION IS NOT APPLIED TO THE £" in text
    assert "ancillary_services.dfs_zonal_observed_2026_27" in result.assumptions_used
    assert "warranty_cycle_limits.soc_min_fraction" in result.assumptions_used


def test_the_dfs_zone_geography_is_the_published_one(tmp_path: Path) -> None:
    """All twelve zones appear in the derived table, and the campaign's two are corroborated.

    The table carries 26 reference districts alongside the campaign's five, resolved in the same
    run by the same method. Bristol and Southampton land in zone 10 with Gloucestershire,
    Oxfordshire and Berkshire; London and Medway land in zone 12 with Slough. Six unrelated
    districts either side of the boundary agreeing with the campaign's own resolution is the check
    that the geography is being read and not asserted.
    """
    assert sorted(set(_DFS_ZONE_BY_OUTWARD_CODE.values())) == list(range(1, 13))
    for outward in ("BS1", "SO14"):
        assert _DFS_ZONE_BY_OUTWARD_CODE[outward] == _DFS_ZONE_BY_OUTWARD_CODE["GL52"] == 10
    for outward in ("SW1A", "ME1"):
        assert _DFS_ZONE_BY_OUTWARD_CODE[outward] == _DFS_ZONE_BY_OUTWARD_CODE["SL3"] == 12
    # A resolved site never takes the substitution branch, whatever the substitute happens to be.
    site = _site_with_postcode(tmp_path, "SL3", days=7)
    zone, resolved, _notes = dfs_zone_for_site(site)
    assert resolved and zone == 12 != _DFS_SUBSTITUTE_ZONE
