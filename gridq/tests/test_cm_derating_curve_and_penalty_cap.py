"""gb_v1_10: the Capacity Market de-rating curve reaches the priced stream, and the annual
penalty cap stops claiming to be an unconditional bound.

WHAT WAS ACTUALLY WRONG (the brief's framing was wrong on both of its own hypotheses, and the
corrections matter more than the fix):

*   **The code never interpolates.** Both lookups FLOOR to the largest tabulated duration at or
    below the asset's, and refuse to invent a value between bins
    (`optimisation/_assumptions.value_for_duration`, `value_stack/gb_streams._derate_for_duration`).
    So "interpolation between table points is linear when the curve is not" describes nothing in
    this repo.
*   **The 1h/2h/4h table was not a linearised transcription.** The exact duration-proportionality
    of 0.1099 / 0.2198 / 0.4396 is a property of the published 2029-30 T-4 auction data, not an
    artefact: 5 x 0.1099 = 0.5495 against a published 5h of 0.5509, and 16 of the 18 auction x
    delivery-year combinations in `de_rating_by_auction_and_delivery_year` are NOT proportional at
    1/2/4h. gb_v1_9 already records that analysis on the key itself.

The real defect is a COVERAGE defect, and it runs the flattering way at exactly one point. The
production sizing search enumerates durations from 0.5h to 8h continuously
(`optimisation/two_stage.COARSE_DURATION_HOURS` = 0.5, 1, 1.5, 2, 3, 4, 6, 8;
`DURATION_BOUNDS_HOURS` = (0.5, 8.0); `NO_VIABLE_COVER_DURATION_HOURS` = 0.5, 2, 8; and the
adaptive refiner produces arbitrary durations in between), while the priced key carried three
bins. Flooring a 3h asset onto the 2h bin understates it by a third and an 8h asset onto the 4h
bin by half. Below the smallest bin the floor INVERTS: the lookup hands back the smallest bin's
own factor, so a 0.5h unit was priced at the 1h factor of 0.1099 where NESO publishes 0.0557,
nearly double the de-rated capacity it is entitled to, and the warning attached to that branch
calls it "conservatively".

gb_v1_10 closes it in the DATA, because the whole published curve was already in the same file
under `de_rating_by_auction_and_delivery_year`, from the same NESO CKAN fetch. No bin is derived.

THE LIVE PATH these tests assert against, named end to end:

    cli.py:278 generate_quote(engine=MilpEngine(...))
      -> optimisation/milp_solver.py:2064 build_value_streams(...)
      -> optimisation/valuation.py:900 build_value_streams
      -> optimisation/valuation.py:971 _capacity_market_value(...)
      -> optimisation/valuation.py:224 reads capacity_market.de_rating_factor_by_duration_h
      -> optimisation/valuation.py:265 value_for_duration(table, schedule.duration_h)

`build_value_streams` returns the exact `ValueStreamResult` objects serialised into quote.json, so
asserting there is asserting at the layer the reader reads, one call below the record.

NOT asserted here, because it is not true: that `value_stack/gb_streams.CapacityMarketStream._price`
matters to a quote. It does not. `value_function` has ZERO callers in `src/` (its only caller is
`streams.py:260`, inside `value_function` itself), so the whole `_price` layer of `gb_streams` is
dead on the pricing path; `valuation.py` reprices every stream itself and takes only `check_gates`
from the value stack. That is recorded in the report, not papered over here.

MUTATION TARGETS, both verified red-then-green:
  1. `assumptions/gb_v1_10.yaml` -> revert `de_rating_factor_by_duration_h` to the 3-bin table:
     `test_every_duration_the_search_can_produce_is_priced_at_its_published_bin` fails.
  2. `assumptions/gb_v1_10.yaml` -> restore the withdrawn unconditional sentence on
     `cm_annual_cap_pct_of_revenue`: `test_the_annual_cap_no_longer_claims_to_be_unconditional`
     fails.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    AssumptionStatus,
    load_assumptions,
)
from gridq.assumptions.loader import latest_version
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.two_stage import (
    COARSE_DURATION_HOURS,
    DURATION_BOUNDS_HOURS,
    NO_VIABLE_COVER_DURATION_HOURS,
)
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import MarketStream
from gridq.value_stack import GateContext

RELEASE = "gb_v1_10"
PRIOR = "gb_v1_9"
CM = MarketStream.CAPACITY_MARKET
ALL_SECTIONS = tuple(SECTION_NAMES) + tuple(OPTIONAL_SECTION_NAMES)

#: Deliverability confirmed, which is what lets the CM stream price at all. Production-reachable:
#: `sizing/deliverability.produce_gate_context` builds it, imported at `milp_solver.py:91`.
CONFIRMED = GateContext(deliverability_confirmed=True)

#: The published 2029-30 T-4 storage factors for the durations the search actually visits, read
#: off `de_rating_by_auction_and_delivery_year.full_curves["T-4"]["2029-30"]` in the SAME release
#: (so this table is not an independent transcription; it is the cross-check that the duration-only
#: key now agrees with the auction table it is a slice of).
PUBLISHED_BY_DURATION: dict[float, float] = {
    0.5: 0.0557,
    1.0: 0.1099,
    1.5: 0.1641,
    2.0: 0.2198,
    3.0: 0.3297,
    4.0: 0.4396,
    6.0: 0.6608,
    8.0: 0.8777,
}

#: What gb_v1_9 resolved for the same durations, by flooring onto its three bins. Kept explicit so
#: the DIRECTION OF EFFECT is measured by the tests rather than asserted in prose.
PRIOR_BY_DURATION: dict[float, float] = {
    0.5: 0.1099,  # below the smallest bin: the flattering inversion
    1.0: 0.1099,
    1.5: 0.1099,
    2.0: 0.2198,
    3.0: 0.2198,
    4.0: 0.4396,
    6.0: 0.4396,
    8.0: 0.4396,
}


def _site() -> s.SiteModel:
    """A site that can PREQUALIFY: 4 MW of evidenced export connection clears the reg 15 1 MW
    minimum on the generating route, so the eligibility gate does not short-circuit to GBP 0
    before any de-rating factor is read (SI 2014/2043 reg 15(4)(a))."""
    return s.SiteModel(
        site_id="site_cm_derating",
        site_name="CM de-rating curve site",
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
        connection=s.ConnectionDetails(mic_kw=300.0, mec_kw=4_000.0, asc_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _schedule(*, power_kw: float, duration_h: float) -> HalfHourlyDispatchSchedule:
    """A solved-shape schedule at ``duration_h``.

    ``deliverable_capacity_mw=None`` on purpose: when the solver has reserved capability against a
    compiled CM commitment, `valuation.py:247` uses that figure and the de-rating table is never
    consulted. The table branch (`valuation.py:265`) is the one under test, and it is the branch
    every site without a compiled CM commitment takes.
    """
    n = 48
    zeros = np.zeros(n, dtype=float)
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * duration_h,
        duration_hours=duration_h,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=zeros,
        discharge_kw=zeros,
        soe_kwh=zeros,
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=np.zeros(n, dtype=bool),
        deliverable_capacity_mw=None,
    )


def _priced_cm(power_kw: float, duration_h: float, version: str):
    """The CM entry as the reader receives it, through the production valuation function."""
    lib = load_assumptions(version)
    value_streams, gated, _used = build_value_streams(
        _schedule(power_kw=power_kw, duration_h=duration_h),
        _site(),
        lib,
        context=CONFIRMED,
    )
    entries = [v for v in (*value_streams, *gated) if v.stream == CM]
    assert len(entries) == 1, "the Capacity Market must be reported exactly once"
    return entries[0]


def _table(version: str) -> dict:
    """The duration-only de-rating table as the pricing path reads it."""
    value = load_assumptions(version).get("capacity_market", "de_rating_factor_by_duration_h").value
    assert isinstance(value, dict)
    return value


def _clearing(version: str) -> float:
    value = load_assumptions(version).get("capacity_market", "clearing_price_gbp_per_kw_yr").value
    assert isinstance(value, (int, float))
    return float(value)


# --- 1. release-cut discipline ------------------------------------------------


def test_gb_v1_10_carries_gb_v1_9_forward_byte_identical_except_three_enumerated_keys() -> None:
    """A release is never edited in place, and its drift is enumerated, never discovered.

    Three keys move and no key is added or dropped. `de_rating_factor_by_duration_h` gains bins;
    `cm_annual_cap_pct_of_revenue` keeps its VALUE and moves only its source and notes;
    `cm_penalty_rate_basis` gains additive fields. Everything else must compare equal, which is
    what makes the enumerated drift trustworthy.
    """
    old = load_assumptions(PRIOR).model_dump(mode="json")
    new = load_assumptions(RELEASE).model_dump(mode="json")
    assert old.pop("metadata")["version"] == PRIOR
    assert new.pop("metadata")["version"] == RELEASE

    drift = {
        ("capacity_market", "de_rating_factor_by_duration_h"),
        ("penalties", "cm_annual_cap_pct_of_revenue"),
        ("penalties", "cm_penalty_rate_basis"),
    }
    for section in ALL_SECTIONS:
        assert set(new[section]) == set(old[section]), f"{section}: keys added or dropped"
    for section, key in drift:
        assert new[section][key] != old[section][key], f"{section}.{key} was expected to move"
        new[section].pop(key)
        old[section].pop(key)

    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True), (
        "gb_v1_10 must differ from gb_v1_9 by exactly the three enumerated keys and the header"
    )


def test_gb_v1_10_keeps_the_annual_cap_value_and_status_frozen() -> None:
    """The 100% annual cap is statutory (reg 41(3A)(a)); correcting a false CLAIM about it must
    never move the number. A release that quietly retuned a statutory percentage while claiming
    to correct a label would be a worse defect than the one being fixed."""
    old = load_assumptions(PRIOR).get("penalties", "cm_annual_cap_pct_of_revenue")
    new = load_assumptions(RELEASE).get("penalties", "cm_annual_cap_pct_of_revenue")
    assert new.value == old.value == 100.0
    assert new.status == old.status == AssumptionStatus.NEEDS_REVIEW
    monthly_old = load_assumptions(PRIOR).get("penalties", "cm_monthly_cap_pct_of_revenue")
    monthly_new = load_assumptions(RELEASE).get("penalties", "cm_monthly_cap_pct_of_revenue")
    assert monthly_new.model_dump(mode="json") == monthly_old.model_dump(mode="json")


def test_nothing_on_gb_v1_10_is_confirmed_so_no_quote_becomes_bankable() -> None:
    """0 confirmed entries repo-wide, by design. Extending a table must not promote anything."""
    lib = load_assumptions(RELEASE)
    statuses = [a.status for sec in ALL_SECTIONS for a in getattr(lib, sec).values()]
    assert statuses, "no entries loaded"
    assert not any(status == AssumptionStatus.CONFIRMED for status in statuses)


def test_gb_v1_10_is_what_a_production_run_resolves() -> None:
    """MD-F-15's lesson: a release nobody resolves changes nothing. Deliberately not written as
    `latest_version() == RELEASE`, which would fail the day gb_v1_11 lands for a good reason."""
    resolved = latest_version()
    assert resolved != "gb_v0_1"
    assert (
        load_assumptions(resolved).get("capacity_market", "de_rating_factor_by_duration_h").value
        == load_assumptions(RELEASE).get("capacity_market", "de_rating_factor_by_duration_h").value
    ), "the newest release must carry the full published de-rating curve"


# --- 2. the curve reaches the priced stream (MUTATION TARGET: the table) -------


@pytest.mark.parametrize("duration_h", sorted(PUBLISHED_BY_DURATION))
def test_every_duration_the_search_can_produce_is_priced_at_its_published_bin(
    duration_h: float,
) -> None:
    """MUTATION TARGET. Revert `de_rating_factor_by_duration_h` in assumptions/gb_v1_10.yaml to
    the 3-bin {1, 2, 4} table and this test fails at 0.5h, 1.5h, 3h, 6h and 8h.

    Asserted through `build_value_streams`, which is what `milp_solver.py:2064` calls and what
    produces the `ValueStreamResult` the quote record serialises: the number a reader sees.
    """
    power_kw = 2_000.0
    entry = _priced_cm(power_kw, duration_h, RELEASE)
    factor = PUBLISHED_BY_DURATION[duration_h]
    expected = _clearing(RELEASE) * (power_kw / 1000.0 * factor) * 1000.0

    assert entry.annual_revenue_gbp == pytest.approx(round(expected, 2), abs=0.01)
    assert "capacity_market.de_rating_factor_by_duration_h" in entry.assumptions_used
    # The working travels with the money: a reader can see WHICH factor produced this figure.
    assert any(f"duration de-rating {factor}" in w for w in entry.warnings), entry.warnings
    # And no below-range warning fires for any duration the search can actually produce.
    assert not any("below the smallest tabulated point" in w for w in entry.warnings)


@pytest.mark.parametrize("duration_h", sorted(PUBLISHED_BY_DURATION))
def test_the_direction_of_effect_is_measured_release_against_release(duration_h: float) -> None:
    """The direction is measured, not asserted in prose: the same site and the same schedule
    priced on gb_v1_9 and on gb_v1_10.

    0.5h falls (the flattering inversion is corrected DOWN, halving that candidate's CM revenue);
    1h, 2h and 4h are byte-identical; 1.5h, 3h, 6h and 8h rise, because flooring onto a lower bin
    was understating them against the auction's own published figure for their duration.
    """
    power_kw = 2_000.0
    before = _priced_cm(power_kw, duration_h, PRIOR).annual_revenue_gbp
    after = _priced_cm(power_kw, duration_h, RELEASE).annual_revenue_gbp
    ratio = PUBLISHED_BY_DURATION[duration_h] / PRIOR_BY_DURATION[duration_h]

    assert before > 0.0, "the premise: CM prices on both releases for this site"
    assert after == pytest.approx(before * ratio, rel=1e-6)
    if duration_h in (1.0, 2.0, 4.0):
        assert after == before, "a tabulated bin must not move"
    elif duration_h == 0.5:
        assert after < before, "the half-hour candidate must lose the 1h de-rating it was given"
        assert after == pytest.approx(before * 0.0557 / 0.1099, rel=1e-6)
    else:
        assert after > before, "an off-grid duration must reach its own published bin"


def test_the_half_hour_candidate_is_no_longer_priced_as_a_one_hour_unit() -> None:
    """The single flattering branch, isolated.

    On gb_v1_9 a 0.5h candidate fell below the smallest bin, and the lookup returned the 1h factor
    while its own warning called that "conservatively". It is not conservative: it credits a
    half-hour unit with an hour unit's de-rated capacity. 0.5h is not a hypothetical corner, it is
    the FIRST entry of `COARSE_DURATION_HOURS`, the lower `DURATION_BOUNDS_HOURS` bound, and one of
    the three durations `NO_VIABLE_COVER_DURATION_HOURS` solves on every no-viable run.
    """
    assert min(COARSE_DURATION_HOURS) == 0.5
    assert DURATION_BOUNDS_HOURS[0] == 0.5
    assert 0.5 in NO_VIABLE_COVER_DURATION_HOURS

    before = _priced_cm(2_000.0, 0.5, PRIOR)
    after = _priced_cm(2_000.0, 0.5, RELEASE)
    assert any("below the smallest tabulated point" in w for w in before.warnings)
    assert any("conservatively" in w for w in before.warnings), (
        "the prior release's own warning claimed conservatism while overstating the factor"
    )
    assert not any("conservatively" in w for w in after.warnings)
    assert after.annual_revenue_gbp < before.annual_revenue_gbp
    assert after.annual_revenue_gbp == pytest.approx(before.annual_revenue_gbp * 0.50682, rel=1e-4)


# --- 3. the table cannot silently become unreadable ---------------------------


def test_every_duration_key_parses_as_a_number_or_the_whole_table_prices_cm_at_zero() -> None:
    """The trap that made this a data fix rather than a copy-paste.

    `de_rating_by_auction_and_delivery_year` writes its plateau as the range key "8.5_to_12".
    Both lookups build `sorted((float(k), float(v)) ...)` inside a try/except and return
    ``(None, "malformed")`` on failure, and `valuation.py:266` turns that into "not priced", i.e.
    GBP 0 for the Capacity Market at EVERY duration. Copying the auction curve verbatim would have
    silently zeroed the stream, so the plateau is written as discrete numeric bins.
    """
    table = _table(RELEASE)
    assert isinstance(table, dict)
    for key in table:
        float(key)  # raises, loudly, if a range key ever gets pasted in again
    values = [v for _, v in sorted((float(k), float(v)) for k, v in table.items())]
    assert values == sorted(values), "a de-rating curve is monotonic in duration"
    assert max(values) == 0.9216, "the published TCWAA plateau"


def test_the_published_curve_and_the_auction_table_agree_bin_for_bin() -> None:
    """The extension is a SLICE of data already in the file, not a new transcription.

    Every bin of the duration-only key must equal the 2029-30 T-4 bin of
    `de_rating_by_auction_and_delivery_year`, whose plateau key covers 8.5h to 12h. If the two ever
    disagree, one of them is wrong and neither can be trusted.
    """
    lib = load_assumptions(RELEASE)
    flat = lib.get("capacity_market", "de_rating_factor_by_duration_h").value
    auction = lib.get("capacity_market", "de_rating_by_auction_and_delivery_year").value
    curve = auction["full_curves"]["T-4"]["2029-30"]
    plateau = curve["8.5_to_12"]
    for key, value in flat.items():
        expected = curve.get(key, plateau if float(key) >= 8.5 else None)
        assert expected is not None, f"{key}h is not a published bin"
        assert value == expected, f"{key}h disagrees with the auction table"
    # And the auction table's own analysis is preserved: proportionality is a property of THIS
    # auction's data, never a modelling rule.
    assert 5 * flat["1"] != pytest.approx(flat["5"], abs=1e-9)
    assert "never interpolate" in auction["lookup_convention"].lower()


def test_the_search_bounds_stay_inside_the_published_curve() -> None:
    """The coupling that made the 3-bin table a defect, asserted rather than assumed.

    If someone widens the sizing duration bounds past the published curve, or below its first bin,
    the floor lookup starts approximating again and this fails before a quote does.
    """
    table = _table(RELEASE)
    bins = sorted(float(k) for k in table)
    lo, hi = DURATION_BOUNDS_HOURS
    assert lo >= min(bins), "the search can produce a duration below the smallest published bin"
    assert hi <= max(bins), "the search can produce a duration above the largest published bin"
    for duration in (*COARSE_DURATION_HOURS, *NO_VIABLE_COVER_DURATION_HOURS):
        assert float(duration) in bins, f"{duration}h has no published bin, so it floors onto one"


# --- 4. the annual penalty cap stops claiming to be unconditional -------------


def test_the_annual_cap_no_longer_claims_to_be_unconditional() -> None:
    """MUTATION TARGET. Restore the withdrawn sentence on `cm_annual_cap_pct_of_revenue` in
    assumptions/gb_v1_10.yaml and this test fails.

    gb_v1_9 asserted: "The annual cap means the worst-case annual outcome is losing the capacity
    payment plus paying an equal amount, not an unbounded loss." Under SI 2014/2043 Sch 1 para
    6(2)/(2A) the 100% annual cap enters the settlement calculation only where a penalty charge has
    been incurred in at least 48 relevant settlement periods AND those comprise at least 8 in each
    of at least 6 months of the delivery year. Below that threshold only the 200% MONTHLY cap
    applies, and 200% of the winter-weighted monthly payments can exceed 100% of the annual
    payment. So the sentence is false as written, and it is withdrawn rather than softened.

    The `source` string is the load-bearing half: it is what the memo's assumptions section and the
    HTML sources card render for every key in `assumptions_used`, and `cashflows.py` puts
    `penalties.cm_annual_cap_pct_of_revenue` in `used` whenever it prices the penalty line.
    """
    prior = load_assumptions(PRIOR).get("penalties", "cm_annual_cap_pct_of_revenue")
    entry = load_assumptions(RELEASE).get("penalties", "cm_annual_cap_pct_of_revenue")

    false_claim = "losing the capacity payment plus paying an equal amount"
    assert false_claim in prior.notes, "the premise: gb_v1_9 asserted this"
    # The claim is QUOTED so the correction is auditable, but it must appear only as a withdrawn
    # quotation. Restoring the gb_v1_9 notes verbatim removes the withdrawal and fails here.
    assert "THAT IS FALSE AS AN UNCONDITIONAL STATEMENT" in entry.notes
    assert "withdrawn" in entry.notes.lower()
    assert false_claim not in entry.source, "a withdrawn claim must not survive in the source"

    source = entry.source
    assert "6(2A)" in source or "6(2)" in source
    assert "48 relevant settlement periods" in source
    assert "6 months" in source
    assert "monthly" in source.lower()
    notes = entry.notes.lower()
    assert "conditional" in notes
    assert "understates" in notes  # the direction of the error, stated on the key
    assert "cashflows.py" in entry.notes  # the unfixed consumer, named where a reader will look


def test_the_penalty_regime_record_carries_the_statutory_trigger() -> None:
    """The regime record is where a corrected consumer should read the condition from, so the
    condition has to live there and not only in a review file."""
    basis = load_assumptions(RELEASE).get("penalties", "cm_penalty_rate_basis").value
    assert isinstance(basis, dict)
    assert basis["annual_cap_is_conditional"] is True
    assert "48 relevant settlement periods" in basis["annual_cap_condition"]
    assert "6 months" in basis["annual_cap_condition"]
    assert "200" in basis["cap_that_applies_below_threshold"]
    assert "NOT 100 pct" in basis["unconditional_annual_bound"]
    # Additive only: every field gb_v1_9 carried is unchanged.
    prior = load_assumptions(PRIOR).get("penalties", "cm_penalty_rate_basis").value
    for key, value in prior.items():
        assert basis[key] == value, f"{key} must not move"
    assert basis["penalty_rate_formula"].startswith("PR = PE / 24")
    assert basis["implied_fraction_of_clearing_per_mwh"] == 0.0416667


def test_no_rendered_string_added_by_this_release_carries_an_em_or_en_dash() -> None:
    """Sources and notes reach `report.html`, which deliberately does not sanitise dashes."""
    prior = load_assumptions(PRIOR)
    entry = load_assumptions(RELEASE)
    for section, key in (
        ("capacity_market", "de_rating_factor_by_duration_h"),
        ("penalties", "cm_annual_cap_pct_of_revenue"),
    ):
        new = entry.get(section, key)
        old = prior.get(section, key)
        for field in ("source", "notes"):
            added = getattr(new, field).replace(getattr(old, field), "")
            assert "—" not in added, f"{section}.{key}.{field} added an em dash"
            assert "–" not in added, f"{section}.{key}.{field} added an en dash"
