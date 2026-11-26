"""The class-C stream zeros: a £0 that came from the MODEL, not from the site or the statute.

Every test here drives :func:`gridq.optimisation.valuation.build_value_streams` — the production
pricing path reached from ``cli.py`` → ``generate_quote`` → the MILP engine — and asserts on the
``ValueStreamResult`` it returns. Nothing loads a real run directory: ``runs/`` is gitignored, so a
test that read one would pass vacuously on a fresh checkout, which is precisely how this repo has
shipped a built-tested-never-called defect eleven times.

The three classes of zero this file separates, because they are the same £ and completely different
statements:

- **A structural zero** — the asset genuinely cannot earn it (a private wire has no CDCM DUoS).
  Correct, permanent, and must carry no data ask.
- **A refused £** — a term that *sets* the number is unsourced, so the figure is withheld rather
  than guessed. D45: "refuse the £, or derive it, never both-and-neither."
- **A modelling zero** — the defect class. The money is real and the model does not see it.

Sourced rules pinned here: NESO 'Demand Flexibility Service Procurement Rules' v5.0 (18 May 2026)
paras 4.2, 4.7.2, 4.7.6, 4.7.8(a), 4.7.9 and Schedule 1; NESO 'Demand Flexibility Service Service
Terms' v4.0 (9 April 2026) Schedule 1; decision D30 (no availability price without an award);
decision D45 (no null on a pricing path, and no both-and-neither).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.tariff_calendar import energy_price_series
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import Firmness, MarketStream
from gridq.value_stack.gb_streams import (
    _FLAT_TARIFF_NO_SPREAD_NOTE,
    LOCAL_FLEX_PRICE_CONTEXT_LABEL,
    MIC_ASC_UNPRICED_LEG_LABEL,
    dfs_seasonal_value_range_gbp,
    dfs_settlement_value_gbp,
)

LIB = load_assumptions("gb_v1_10")

#: Knowl Hill's actual recorded flat import rate (£/kWh) — the live site this defect was found on.
FLAT_RATE = 0.1541

_N = 48


def _site(
    *,
    import_rates: dict[str, float] | None = None,
    mic_kw: float = 325.0,
    dno_region: str | None = "SSEN_SEPD",
    duos_red_rate: float | None = 0.05471,
    capacity_charge: float | None = 0.1063,
    derived: tuple[s.DerivedAssumption, ...] = (),
) -> s.SiteModel:
    """A minimal valid site, built in-test so nothing depends on a gitignored run directory."""
    return s.SiteModel(
        site_id="site_class_c",
        postcode="OX11 0QX",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_file_ref="examples/demo_site/hh_2025.csv",
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=227.2,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"flat": FLAT_RATE} if import_rates is None else import_rates,
            duos_red_rate=duos_red_rate,
            capacity_charge_gbp_per_kva_day=capacity_charge,
        ),
        connection=s.ConnectionDetails(mic_kw=mic_kw, dno_region=dno_region),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
        derived_assumptions=list(derived) or None,
    )


def _schedule(
    *,
    energy_price: float | None = FLAT_RATE,
    duos_rate: float = 0.05471,
    charge_kw: float = 120.0,
    discharge_kw: float = 100.0,
) -> HalfHourlyDispatchSchedule:
    """A schedule whose round trip LOSES energy, so the flat-rate energy term is negative.

    ``charge_kw > discharge_kw`` is the round-trip loss: at a single flat price the energy term is
    ``flat × (Σdischarge − Σcharge) × Δt``, which is negative by exactly the cost of those losses.
    That negative is the £ this file exists to keep on the record.
    """
    red = np.zeros(_N, dtype=bool)
    red[16:24] = True
    return HalfHourlyDispatchSchedule(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=365.0,
        charge_kw=np.full(_N, charge_kw, dtype=float),
        discharge_kw=np.full(_N, discharge_kw, dtype=float),
        soe_kwh=np.zeros(_N, dtype=float),
        site_load_kw=np.full(_N, 200.0, dtype=float),
        duos_rate_gbp_per_kwh=np.full(_N, duos_rate, dtype=float),
        red_band_mask=red,
        energy_price_gbp_per_kwh=(
            None if energy_price is None else np.full(_N, energy_price, dtype=float)
        ),
    )


def _entry(streams: list[s.ValueStreamResult], sid: MarketStream) -> s.ValueStreamResult:
    match = next((v for v in streams if v.stream == sid), None)
    assert match is not None, f"{sid.value} absent from the result"
    return match


def _text(entry: s.ValueStreamResult) -> str:
    return " ".join(entry.warnings)


# ---------------------------------------------------------------------------
# TARIFF-1 / TARIFF-2 — the only class-C fix that moves the reported floor DOWN
# ---------------------------------------------------------------------------


def test_a_flat_tariff_opens_the_gate_so_the_round_trip_cost_reaches_the_floor() -> None:
    """A flat import tariff must OPEN this gate, because the round-trip energy cost is real.

    The gate used to CLOSE on ``len(distinct) < 2``. A closed gate is EXCLUDED firmness,
    ``valuation._priceable`` skips the pricing branch, and ``finance/cashflows.py`` sums
    ``annual_cost_gbp`` only over FIRM/CONTRACTED streams — so the negative energy term the MILP
    objective *did* carry never reached the reported floor, and the floor sat ABOVE the objective.

    Deleting the flat-tariff OPEN branch in ``gb_streams.TariffArbitrageStream.check_gates``
    reverts the stream to EXCLUDED and drops the cost, and this test goes red.
    """
    schedule = _schedule()
    assert schedule.arbitrage_value_gbp < 0, "fixture must exercise the negative-energy-term case"

    streams, _, _ = build_value_streams(schedule, _site(), LIB)
    arb = _entry(streams, MarketStream.TARIFF_ARBITRAGE)

    assert arb.gate_status == "open"
    assert arb.firmness is Firmness.FIRM, "only FIRM/CONTRACTED streams have their cost summed"
    assert arb.annual_revenue_gbp == 0.0, "a flat tariff has no spread, so revenue is genuinely £0"
    assert arb.annual_cost_gbp > 0.0, "the round-trip energy cost must be carried, not deleted"
    assert arb.annual_cost_gbp == pytest.approx(-schedule.arbitrage_value_gbp, abs=0.01)
    assert _FLAT_TARIFF_NO_SPREAD_NOTE in arb.warnings


def test_the_reported_floor_equals_the_milp_objective_on_a_flat_tariff_site() -> None:
    """The floor-equals-objective invariant, asserted on the STREAM SET rather than the schedule.

    ``tests/test_floor_equals_objective.py`` pins
    ``arbitrage_value_gbp + duos_avoidance_value_gbp == priced_floor_terms_gbp`` on the schedule
    OBJECT, and never calls ``build_value_streams`` — so it stayed green while the reported stream
    set broke the same invariant through the gate. This is that invariant one layer out, where the
    numbers a reader actually sees are assembled.
    """
    schedule = _schedule()
    streams, _, _ = build_value_streams(schedule, _site(), LIB)

    reported = sum(
        v.annual_revenue_gbp - v.annual_cost_gbp
        for v in streams
        if v.stream in (MarketStream.TARIFF_ARBITRAGE, MarketStream.DUOS_RED_BAND_AVOIDANCE)
    )
    assert reported == pytest.approx(schedule.priced_floor_terms_gbp, abs=0.02)


def test_an_empty_import_tariff_still_closes_the_gate() -> None:
    """Flat is not the same as absent, and only ABSENT may close the gate.

    An empty ``import_rates`` means the site's energy price is genuinely unknown — the MILP has no
    energy term either — so £0 is correct and the two layers agree. Opening this case would be the
    original F-01 defect in reverse.
    """
    streams, _, _ = build_value_streams(_schedule(energy_price=None), _site(import_rates={}), LIB)
    arb = _entry(streams, MarketStream.TARIFF_ARBITRAGE)

    assert arb.gate_status == "closed"
    assert arb.annual_revenue_gbp == 0.0
    assert arb.annual_cost_gbp == 0.0
    assert "genuinely UNKNOWN" in _text(arb)


@pytest.mark.parametrize(
    ("rates", "milp_prices_energy", "expect_open"),
    [
        ({}, False, False),
        ({"flat": FLAT_RATE}, True, True),
        ({"red": 0.30, "amber": 0.20, "green": 0.10}, True, True),
    ],
)
def test_the_gate_and_the_milp_energy_price_agree_on_every_tariff_state(
    rates: dict[str, float], milp_prices_energy: bool, expect_open: bool
) -> None:
    """The gate opens exactly when the objective carries an energy term — pinned to the source.

    The two layers are in different packages and cannot import one another (``value_stack`` must
    never depend on ``optimisation``), so the predicate is necessarily restated. This test is what
    stops the restatement drifting: it runs the REAL
    ``optimisation.tariff_calendar.energy_price_series`` and the REAL gate over the same tariff and
    demands they agree. Drift here is exactly how TARIFF-1 arose.
    """
    import pandas as pd

    prices, _note = energy_price_series(
        rates, pd.date_range("2025-01-01", periods=_N, freq="30min")
    )
    assert (prices is not None) is milp_prices_energy

    streams, _, _ = build_value_streams(
        _schedule(energy_price=FLAT_RATE if milp_prices_energy else None),
        _site(import_rates=rates),
        LIB,
    )
    assert (_entry(streams, MarketStream.TARIFF_ARBITRAGE).gate_status == "open") is expect_open


# ---------------------------------------------------------------------------
# DFS — the £ is REFUSED, never priced at zero
# ---------------------------------------------------------------------------


def test_dfs_shows_a_sourced_value_range_rather_than_a_bare_zero() -> None:
    """D45's forbidden outcome is both-and-neither: a data ask AND a £0.00 in the same record.

    DFS used to do exactly that, and the first fix withheld the annual £ on the grounds that the
    called-event count and realised K were unsourceable. A recorded search found them — NESO
    publishes the event count AND its own per-1-MW annual revenue scenarios — so the honest outcome
    is *derive and declare*. Deleting the ``dfs_unit_economics_notes`` call in
    ``DemandFlexibilityServiceStream.check_gates`` removes it all and this goes red.

    **WHAT CHANGED, 2026-08-19.** These notes used to say the record showed £0 "only because no
    engine branch prices this stream yet". That branch has existed since D57 item 4, so on a real
    MILP run the pack carried a measured DFS £ and, three notes later, the claim that nothing could
    measure it. The scenarios are now labelled for what they are - a published cross-check on the
    superseded winter 2024/25 pre-zonal basis, never added to the priced figure - and this test
    asserts the new framing so the contradiction cannot come back. The £0 HERE is a property of the
    fixture (a summary schedule carries no DFS price array), not of the stream.
    """
    dfs = _entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS)
    text = _text(dfs)

    assert dfs.annual_revenue_gbp == 0.0
    assert "NOT £0" in text
    assert "PUBLISHED CROSS-CHECK ON A SUPERSEDED BASIS, NOT THE £" in text
    assert "WHY THESE SCENARIOS ARE NOT THE £, AND WHAT IS" in text
    assert "THIS SITE'S OWN\nZONE" in text or "THIS SITE'S OWN ZONE" in text
    # The retired claim must not reappear: it contradicted the measured figure in the same pack.
    assert "no engine branch prices this stream yet" not in text
    assert "WHY THE RECORD STILL READS £0" not in text


def test_dfs_scales_nesos_published_per_mw_scenarios_to_this_units_capacity() -> None:
    """Settlement S = 0.5·P·Q·K is LINEAR in the offered volume Q, so £/MW scales to a sub-MW unit.

    Sourced: NESO, 'Demand Flexibility Service (DFS)' winter 2024/25 review, published 3 July 2025,
    pp. 17-18 — "The following scenarios assume a single DFS unit with 1 MW of flexibility". These
    are the source's own annual figures, not a re-derivation from P and K.
    """
    dfs = _entry(build_value_streams(_schedule(), _site(mic_kw=325.0), LIB)[0], MarketStream.DFS)
    text = _text(dfs)

    # MIC 325 kW → 0.325 MW → truncates to a 0.3 MW Registered Unit Capacity.
    low, central, high = dfs_seasonal_value_range_gbp(0.3)
    assert (low, central, high) == pytest.approx((942.6, 2267.4, 10068.0))
    assert f"£{low:,.0f}-£{high:,.0f} per season" in text
    assert f"£{central:,.0f}" in text
    assert "£7.56/kW/yr" in text, "the central case must be legible per kW"
    assert "£21.01/kW/yr" in text, "the full-acceptance ceiling must be stated too"
    # The volume basis is the CONNECTION bound, not the sized battery — the same class of
    # overstatement as the mic-approx-peak signature, so it must be labelled at the point of use.
    assert "NOTE THE VOLUME BASIS" in text
    assert "UPPER BOUND on the offered Service Volume" in text
    # The settlement formula stays on the record — it is what makes the scaling checkable.
    assert dfs_settlement_value_gbp(
        utilisation_price_gbp_per_mwh=210.0, service_volume_mw=0.3, k=1.0
    ) == pytest.approx(31.5)


def test_dfs_marks_the_4559_anchor_superseded_and_quantifies_how_wrong_it_would_be() -> None:
    """The £4,559/MWh anchor annualises to ~£538/kW/yr — 25.6× NESO's own published average.

    That is roughly 5× the entire grid-scale GB BESS revenue index, at a 93-750 kW site. The record
    must say so numerically, because a real citation attached to a 25× overstatement is the worst
    outcome available here: it looks better sourced than the correct figure.
    """
    text = _text(_entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS))

    # 0.5 × £4,559/MWh × 1 MW × 236 procured settlement periods = £537,962/MW/yr.
    assert 0.5 * 4559.0 * 236 == pytest.approx(537_962.0)
    assert 537_962.0 / 21_009.0 == pytest.approx(25.6, abs=0.05)

    assert "SUPERSEDED AND MUST NOT BE ANNUALISED" in text
    assert "25.6×" in text
    assert "DEFECT SIGNAL" in text
    assert "£210/MWh" in text, "the realised price must be on the record next to the anchor"
    assert "£1,290/MWh) is 3.5× below the old anchor" in text
    assert "CONFIDENCE CEILING: LOW" in text
    assert "Cornwall Insight" in text
    assert "a full delivery year of the 2026/27 zonal service" in text, (
        "the confirmation ask must name the data that would settle it"
    )


def test_dfs_carries_the_realised_event_count_that_makes_annualisation_possible() -> None:
    """The term that used to be refused, now sourced: 44 procured events over 236 SPs, one season.

    NESO, winter 2024/25 review, p. 3 and p. 16 verbatim: "56 Service Requirements were published,
    covering 306 settlement periods (SP), with volume procured on 44 events covering 236 settlement
    periods." The aggregate K must be labelled a FLEET statistic — the population is 90% domestic
    and 99.58% manually instructed, so it is not an automated battery's expected performance.
    """
    text = _text(_entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS))

    assert "44 events covering 236 settlement periods" in text
    assert "K ≈ 0.765" in text
    assert "FLEET statistic" in text
    assert "NOT to be multiplied into the scenarios above" in text, "guard against double-haircut"


def test_dfs_exclusivity_is_stated_as_stacking_list_conditional_not_automatic() -> None:
    """4.7.9 bars other services ONLY where the DFS Stacking List prohibits it — a conditional bar.

    NESO's own winter 2024/25 review records that stacking WAS facilitated with "all the core DNO
    Flexibility Services", with 10 providers stacking across CM and LCM. Reading 4.7.9 as an
    automatic exclusion overstates the exclusive set and therefore UNDERSTATES the stack, which is
    the mirror image of inflating revenue and just as wrong. 4.7.6 (BM) is the one hard bar.
    """
    streams, _, _ = build_value_streams(_schedule(), _site(), LIB)
    dfs_text = _text(_entry(streams, MarketStream.DFS))
    flex_text = _text(_entry(streams, MarketStream.LOCAL_FLEX_PICLO))

    for text in (dfs_text, flex_text):
        assert "STACKING LIST" in text.upper()
        assert "current DFS Stacking List" in text, "the governing document must be the data ask"
    assert "ONE HARD BAR AND ONE CONDITIONAL BAR" in dfs_text
    assert "4.7.6" in dfs_text
    assert "CONSERVATIVE MODELLING CHOICE" in dfs_text
    assert "UNDERSTATES the stack" in dfs_text
    assert "CONDITIONAL, NOT AUTOMATIC" in flex_text


def test_dfs_data_asks_point_at_key_paths_the_release_actually_carries() -> None:
    """DFS-3: the two stale asks claimed the library held NOTHING. Both were false on gb_v1_10.

    A stream declaring a data ask for a value the release already carries is the key-path-mismatch
    defect class — the path the code looks at and the path the value lives at differ — and it is
    what makes a report ask a customer for something already on file.
    """
    text = _text(_entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS))

    assert "no `dfs` section exists in any assumption release" not in text
    assert "the DFS Zone register" not in text or "SOURCED, NOT MISSING" in text
    assert "ancillary_services.dfs_performance_factor_k_piecewise" in text
    assert "ancillary_services.dfs_registered_unit_capacity_mw.zone_names" in text
    assert "ed3fa1cc89484faff47ff90fdb89c74d0fa860ff9818e729d25929b993daef88" in text


def test_dfs_surfaces_the_round_down_reading_that_is_the_only_thing_excluding_a_93_kw_site() -> (
    None
):
    """DFS-5: the sub-0.1 MW exclusion turns on an INTERPRETATION and must say so.

    NESO requires a Registered Unit Capacity "rounded to one decimal place" from 0.1 MW. GridQ
    reads that as round-DOWN so registered capacity never exceeds real capability — the right
    default — but nearest-rounding would take 0.093 MW to 0.1 MW and clear the bar. Bishops Cleeve
    Ash Plant is 7% from the boundary and this is the only thing excluding it.
    """
    dfs = _entry(build_value_streams(_schedule(), _site(mic_kw=93.0), LIB)[0], MarketStream.DFS)
    text = _text(dfs)

    # D57 item 8 (2026-08-18): the gate is now OPEN, because the 0.1 MW Registered Unit Capacity
    # floor is a PRODUCT MINIMUM SIZE and D57 limb 3 assumes every such threshold satisfied - limb
    # 5b
    # names "below a statutory or product minimum size (the 1 MW registered unit, the 0.1 MW DFS
    # RUC)"
    # among the conditions that may not zero, reduce, defer or hold a stream conditional. What this
    # test is actually FOR is unchanged and still asserted in full: the exclusion turned on an
    # INTERPRETATION of the rounding rule, and the reader is told so, told what nearest-rounding
    # would give, told the margin, and told what would clear it. D57 removed the restriction; it did
    # not remove the candour, and the surfacing is what this test defends.
    assert dfs.gate_status == "open"
    assert "PRICED ON A D57 ASSUMPTION, NOT ON A CLEARED REGISTERED UNIT CAPACITY" in text
    assert "INTERPRETATION, NOT A BARE RULE" in text
    assert "NEAREST-rounding would give 0.1 MW and CLEAR the bar" in text
    assert "7.0% below" in text
    assert "SAME DFS Zone" in text, "the exclusion must name what would clear it"


def test_dfs_states_the_metering_requirement_that_a_private_wire_may_not_meet() -> None:
    """Procurement Rules 4.7.2: an I&C Boundary Meter must be Half-Hourly Settled.

    A private-wire site's metering arrangement may not satisfy it, which would bar DFS for a
    METERING reason independent of size. Asked, never assumed either way.
    """
    text = _text(_entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS))

    assert "HALF-HOURLY SETTLED" in text
    assert "4.7.2" in text
    assert "Code of Practice 11" in text


# ---------------------------------------------------------------------------
# Exclusivity — DFS and DNO flex are ALTERNATIVES, and the CM is not in the set
# ---------------------------------------------------------------------------


def test_dfs_and_local_flex_are_modelled_as_alternatives_and_never_summed() -> None:
    """Both gates must state the non-additive modelling choice, whatever the Stacking List says.

    Below 1 MW these are the only two contracted markets available (BM, DC/DM/DR and the CM are all
    barred by their own 1 MW thresholds), so the choice is load-bearing. A report showing both as
    simultaneous revenue lines without the Stacking List in hand would inflate revenue.
    """
    streams, _, _ = build_value_streams(_schedule(), _site(), LIB)
    dfs_text = _text(_entry(streams, MarketStream.DFS))
    flex_text = _text(_entry(streams, MarketStream.LOCAL_FLEX_PICLO))

    assert "ALTERNATIVES" in dfs_text and "never sum" in dfs_text.lower()
    assert "ALTERNATIVES" in flex_text
    assert "4.7.9" in flex_text


def test_the_exclusive_set_does_not_swallow_the_capacity_market_or_the_bill_streams() -> None:
    """Rules 4.7.8(a) EXPRESSLY PERMITS a CM agreement alongside DFS.

    Overstating the exclusive set understates the stack, which is the same class of error as
    overstating revenue — just in the flattering-to-nobody direction.
    """
    text = _text(_entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.DFS))

    assert "4.7.8(a)" in text
    assert "EXPRESSLY PERMITS" in text
    assert "PER METER POINT, NOT PER SITE" in text


# ---------------------------------------------------------------------------
# LOCAL FLEX — an honest gate claim, and the per-DNO prices as CONTEXT not as £
# ---------------------------------------------------------------------------


def test_local_flex_no_longer_asserts_an_unchecked_fact_about_open_tenders() -> None:
    """LOCALFLEX-1: "no known open tender in the site's area" was never checked by anything.

    ``context.local_flex_tender_open`` is set in exactly one place, from a commitment the SITE
    supplies. No tender register, Piclo feed or DNO CMZ list is consulted anywhere in ``src/``, so
    the sentence fired on every run regardless of what any DNO was actually tendering — a hardcoded
    default dressed as a finding about the outside world.
    """
    flex = _entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.LOCAL_FLEX_PICLO)
    text = _text(flex)

    assert "no known open tender" not in text
    assert "NO AWARDED LOCAL-FLEX COMMITMENT IS RECORDED ON THIS SITE" in text
    assert "was NOT consulted" in text


def test_local_flex_publishes_this_sites_own_dno_prices_without_pricing_them() -> None:
    """LOCALFLEX-2: the refusal to AVERAGE was right; the refusal to SELECT was the defect.

    Every campaign site's DNO area is known, yet the reader was told only that the spread was too
    wide to quote. Now they see their own licensee's published figures — and the £ stays £0,
    because ``LOCAL_FLEX_PICLO`` is not gated upside and any non-zero here would land in the PRICED
    FLOOR with no award behind it (decision D30, and the F-19 error).
    """
    flex = _entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.LOCAL_FLEX_PICLO)
    text = _text(flex)

    assert LOCAL_FLEX_PRICE_CONTEXT_LABEL in text
    assert "SSEN_SEPD" in text
    assert "ssen_awarded_battery_vaou_week_ahead" in text
    assert "NOT A PRICE, AND NOT IN THE £" in text
    assert flex.annual_revenue_gbp == 0.0, "published context must never become booked revenue"
    assert flex.firmness is not Firmness.FIRM


def test_local_flex_never_substitutes_another_dnos_price_for_a_missing_one() -> None:
    """Borrowing a row from a different licensee is the averaging D30 forbids, wearing a hat.

    The published figures disagree by 20-30x for the nominally identical product, so a substituted
    row is not a conservative approximation — it is a number with no relationship to the site.
    """
    flex = _entry(
        build_value_streams(_schedule(), _site(dno_region="NPG_NORTHEAST"), LIB)[0],
        MarketStream.LOCAL_FLEX_PICLO,
    )
    text = _text(flex)

    assert "NO PUBLISHED AVAILABILITY PRICE FOR NPG_NORTHEAST" in text
    assert "deliberately NOT substituted" in text
    assert flex.annual_revenue_gbp == 0.0


# ---------------------------------------------------------------------------
# MIC/ASC — a £0 whose rationale covered only half the money
# ---------------------------------------------------------------------------


def test_mic_asc_points_at_the_stream_that_now_prices_the_capacity_and_tcr_money() -> None:
    """MICASC-1/2, UPDATED BY MICASC-3 (Olly, 2026-08-17): the disclosure became a signpost.

    **This assertion was deliberately changed, and the change is the point.** The original test
    pinned the sentence "neither of which any code path models" and the claim that the avoided
    agreed-capacity charge is "unavoidable BY CONSTRUCTION". Both were true when written. MICASC-3
    priced the two amounts on their own stream (``AGREED_CAPACITY_REDUCTION``), so continuing to
    assert them would have pinned a **false** statement into the pack — a disclosure insisting the
    engine cannot see money the engine is, on the same record, reporting.

    What is still true and still pinned: the MIC/ASC headroom stream's OWN £0 is correct, because
    it covers the ARBITRAGE reading of headroom and that value really is realised through the
    connection-bounded arbitrage. So the note must keep its £0, keep the label, and now ALSO name
    the stream a reader should look at instead. A £0 that does not say where the money went is the
    defect MICASC-1/2 existed to fix, and it would be reintroduced by dropping this test rather
    than updating it.
    """
    mic = _entry(build_value_streams(_schedule(), _site(), LIB)[0], MarketStream.MIC_ASC_HEADROOM)
    text = _text(mic)

    assert mic.annual_revenue_gbp == 0.0
    assert MIC_ASC_UNPRICED_LEG_LABEL in text
    # The two amounts are still NAMED — a reader must not have to know they exist.
    assert "AGREED-CAPACITY" in text
    assert "TCR FIXED-RESIDUAL" in text
    # ...and the note now routes the reader to the stream carrying the £, instead of asserting
    # that nothing models it.
    assert "AGREED_CAPACITY_REDUCTION" in text
    assert "tcr_band_thresholds_kva" in text
    assert "DIRECTION OF EFFECT: UP" in text
    # The direction claim is now qualified: UP, but OUTSIDE the priced floor, because the new
    # stream is CONDITIONAL. Pinning the qualifier stops the note being read as floor revenue.
    assert "CONDITIONAL" in text


# ---------------------------------------------------------------------------
# DUoS — a STRUCTURAL zero must not read as a missing input
# ---------------------------------------------------------------------------


def test_a_private_wire_duos_zero_reads_as_structural_and_makes_no_data_ask() -> None:
    """The GB DNOs' CDCM red/amber/green unit rates do not apply behind a private wire.

    So £0 is correct and PERMANENT, and asking that customer for a distribution rate no supplier
    could ever issue them is the defect. Deriving a published DNO red rate for such a meter point
    would invent a charge the site does not pay — the opposite of sourcing.
    """
    record = s.DerivedAssumption(
        kind="not_applicable",
        key="tariff_stack.duos_red_rate",
        missing_input="DUoS red-band unit rate",
        basis=(
            "PRIVATE-WIRE SITE: the GB grid network operator's CDCM unit rates do not apply behind "
            "the private wire, and the non-commodity cost the site does pay is already carried as "
            "a back-solved residual."
        ),
        derivation_method="not_derivable",
        derived_by="Olly",
        confidence_ceiling=s.ConfidenceBand.LOW,
        confirmation_ask="Confirm the private-wire arrangement and the residual's derivation.",
    )
    duos = _entry(
        build_value_streams(_schedule(), _site(duos_red_rate=None, derived=(record,)), LIB)[0],
        MarketStream.DUOS_RED_BAND_AVOIDANCE,
    )
    text = _text(duos)

    assert duos.annual_revenue_gbp == 0.0
    assert "STRUCTURAL reason, not a missing input" in text
    assert "PERMANENT" in text
    assert "NO DATA ASK IS MADE" in text
    assert duos.unlock_condition is None


def test_a_genuinely_missing_duos_rate_still_reads_as_a_data_absence() -> None:
    """The structural sentence must not leak onto a site that simply has not sent its tariff.

    Same £, opposite statement: one is "there is no rate to send", the other is "please send it".
    """
    duos = _entry(
        build_value_streams(_schedule(), _site(duos_red_rate=None), LIB)[0],
        MarketStream.DUOS_RED_BAND_AVOIDANCE,
    )
    text = _text(duos)

    assert "no DUoS rate data on the tariff" in text
    assert "STRUCTURAL reason" not in text
