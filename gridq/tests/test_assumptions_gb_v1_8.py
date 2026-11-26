"""Tests for the gb_v1_8 assumption release (the NULL-CLOSURE release over gb_v1_7).

gb_v1_8 exists to answer decision D45 as amended 2026-08-12: a null on the pricing path is a defect,
not a state to report, because a missing rate does not read "unknown" downstream, it prices the
stream at GBP 0 and that zero enters P50, P90 and the DSCR indistinguishable from a measured result.
What it closes, from a primary published source in every case: the 13 unsourced DNO licence areas in
`duos.band_windows_by_dno` and `duos.unit_rates_p_per_kwh_by_dno`; the TCR residual band thresholds
that make the banded lookups readable; `financing.cpi_pct_per_year` from the OBR March 2026 EFO; the
Capacity Market 5h/5.5h de-rating bins, re-queried from the live NESO resource rather than
interpolated; the statutory CM penalty rate; the DFS zone identifiers and the Guaranteed Acceptance
Price definition; and the published TNUoS demand-residual band table.

The load-bearing invariants these tests lock:

*   SIX values drift from gb_v1_7 and no key is dropped.
    `test_gb_v1_8_priors_identical_to_gb_v1_7_except_the_enumerated_drift` is the mechanical proof
    of the release's own drift table: it fails if any other number moves, in either release. Value
    drift between releases is enumerated deliberately and visibly, never silent, and the CM penalty
    correction in particular is enumerated *because* it FLATTERS every quote carrying CM revenue.
*   Nothing is `confirmed` (0 confirmed repo-wide, by design). A fetched public tariff is
    `needs_review` until a human confirms it against the site's own bill, so no quote on this
    release may present as bankable.
*   Every entry carries a unit, a source that points at real evidence or says plainly that it is
    not sourced, and a vintage. That trio is the anti-fabrication guard, enforced strictly.
*   The DUoS closure is real PER AREA: all 14 licence areas carry a band-window set AND a unit-rate
    set AND their own citation. Asserted per area, so a half-filled area fails loudly rather than
    hiding inside a count.
*   Bank holidays are RED in every sourced area. Every statement prints the weekday row as "Monday
    to Friday (Including Bank Holidays)"; the register calls the green treatment "a common modelling
    error" and an earlier brief in this project made exactly that error.
*   Every remaining null records the search that failed and is never presented as zero.

Deliberate inversions of a gb_v1_7 assertion (both are the point of this release, not regressions):
gb_v1_7 asserted the 5h/5.5h de-rating bins were ABSENT, and that
`penalties.cm_penalty_rate_fraction_of_clearing` sat at 0.25 while `cm_penalty_rate_basis` implied
1/24. gb_v1_8 closes the first by re-query and resolves the second in favour of the statute.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from gridq.assumptions import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionStatus,
    load_assumptions,
)
from gridq.assumptions.loader import available_versions, latest_version

_ALL_SECTIONS = tuple(SECTION_NAMES) + tuple(OPTIONAL_SECTION_NAMES)

_RELEASED = date(2026, 8, 12)

# --- The drift table from gb_v1_7, enumerated ---------------------------------
#
# (1) VALUES that move. Six keys. Four are null-or-gap closures; one is a scalar closed from a
# forecast; one is a CORRECTION THAT FLATTERS EVERY QUOTE and is listed first for that reason.
_VALUE_DRIFT_SCALARS: dict[tuple[str, str], object] = {
    # FLATTERING: 0.25 (PE/4, research-grade) -> 0.0416667 (PE/24, the statute). Reduces the bounded
    # worst-case CM penalty line ~6x. Pinned here so it can never move again unannounced.
    ("penalties", "cm_penalty_rate_fraction_of_clearing"): 0.0416667,
    # null -> 2.0: OBR March 2026 EFO settled rate from 2027. Direction is per line, not netted.
    ("financing", "cpi_pct_per_year"): 2.0,
}
# The nested closures, asserted structurally in their own tests rather than by literal equality.
_VALUE_DRIFT_NESTED: frozenset[tuple[str, str]] = frozenset(
    {
        ("duos", "band_windows_by_dno"),
        ("duos", "unit_rates_p_per_kwh_by_dno"),
        ("capacity_market", "de_rating_by_auction_and_delivery_year"),
        ("ancillary_services", "dfs_registered_unit_capacity_mw"),
    }
)
_VALUE_DRIFT: frozenset[tuple[str, str]] = frozenset(_VALUE_DRIFT_SCALARS) | _VALUE_DRIFT_NESTED

# (2) VALUE FROZEN, basis re-sourced and status promoted placeholder -> needs_review. Nothing was
# tuned toward viability: the numbers are byte-identical, only the evidence behind them changed.
_PROMOTED: frozenset[tuple[str, str]] = frozenset(
    {
        ("battery_opex", "annual_fixed_opex"),
        ("penalties", "cm_monthly_cap_pct_of_revenue"),
        ("penalties", "cm_annual_cap_pct_of_revenue"),
    }
)

# (3) VALUE AND STATUS FROZEN, source/notes restated (a derivation route recorded, no number moved).
_RESOURCED: frozenset[tuple[str, str]] = frozenset({("tnuos", "demand_residual_gbp_per_year")})

# (4) VALUE, STATUS AND SOURCE FROZEN; only `notes` gained a cross-reference or a recorded search.
_NOTES_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        ("local_flex_piclo", "product_types"),
        ("balancing_mechanism", "aggregation_rule"),
        ("ancillary_services", "performance_factor_k_parameters"),
        ("ancillary_services", "dfs_performance_factor_k_piecewise"),
        ("availability", "availability_price_gbp_per_mw_h"),
        ("availability", "expected_availability_pct"),
        ("penalties", "cm_penalty_rate_basis"),
    }
)

# The three keys gb_v1_8 adds. Nothing is dropped.
_NEW_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        ("duos", "tcr_band_thresholds_kva"),
        ("ancillary_services", "dfs_guaranteed_acceptance_price_gbp_per_mwh"),
        ("tnuos", "demand_residual_bands_gbp_per_day"),
    }
)

# Locked entry mix (required + optional sections). 71 + 3 new = 74; 42 + 3 new + 4 promotions = 49
# needs_review (the fourth promotion rides on the CM penalty rate, which also changes value).
_TOTAL_ENTRIES = 74
_NEEDS_REVIEW = 49
_PLACEHOLDER = 25
_CONFIRMED = 0

# The 14 GB distribution licence areas. Both DUoS tables must key on exactly these, in this order.
_LICENCE_AREAS = (
    "NGED_EM",
    "NGED_WM",
    "NGED_SW",
    "NGED_SWALES",
    "UKPN_EPN",
    "UKPN_LPN",
    "UKPN_SPN",
    "SSEN_SEPD",
    "SSEN_SHEPD",
    "SPEN_SPD",
    "SPEN_SPM",
    "NPG_NORTHEAST",
    "NPG_YORKSHIRE",
    "ENWL",
)

# NGED_EM is carried byte-identical from gb_v1_7 and is the one area whose citation lives on the
# ENTRY's `source` (with its own statement URL) rather than in a per-area `source_url` + `sha256`
# block. Its inner shape is correspondingly thinner. Handled explicitly, never papered over.
_CARRIED_FORWARD_AREA = "NGED_EM"

# The three unit-rate shapes the DNOs actually publish. A consumer must branch on `rate_structure`.
_RATE_STRUCTURES = frozenset(
    {
        "band_invariant_units_with_banded_fixed",
        "banded_units",
        "banded_units_and_banded_fixed",
    }
)

_TCR_BANDS = ("no_residual", "band_1", "band_2", "band_3", "band_4")

# Evidence markers: a source either points at a fetchable document / an in-repo evidence file, or
# says in words that it is not sourced. Nothing in between is acceptable.
_EVIDENCE_MARKERS = ("http", "source_docs/", "docs/", "examples/", "data_contracts")
_UNSOURCED_MARKERS = (
    "NOT SOURCED",
    "NO CITABLE SOURCE",
    "NOT AGREED",
    "GATED ZERO",
    "SITE-DERIVED",
    "ILLUSTRATIVE",
    "PLACEHOLDER",
)
# Markers that make a null honest: the entry must say what is missing or why it is empty.
_MISSING_MARKERS = (
    "NOT SOURCED",
    "NOT AGREED",
    "DATA ASK",
    "REQUIRED",
    "EMPTY HOME",
    "SEARCH RECORDED",
    "NEVER ZERO",
    "NEVER READ AS ZERO",
    "NEVER A LIBRARY CONSTANT",
    "NOT PUBLISHED",
    "NO THRESHOLD PUBLISHED",
    "REMAIN NULL",
    "REMAINS NULL",
    "STAYS NULL",
    "WHAT WOULD SETTLE IT",
)
_VINTAGE_YEARS = tuple(str(y) for y in range(2014, 2027))


def _entries(lib: Any) -> list[tuple[str, str, Assumption]]:
    return [(sec, name, a) for sec in _ALL_SECTIONS for name, a in getattr(lib, sec).items()]


def _blob(a: Assumption) -> str:
    """Source + notes + the serialised value, for marker searches."""
    return f"{a.source} {a.notes} {json.dumps(a.value, default=str)}"


def _lv_red_rates(area: dict[str, Any]) -> dict[str, float]:
    """The LV site-specific red rate(s) for one area, whichever shape it publishes."""
    lv = area["lv_site_specific"]
    if "by_tcr_band" in lv:
        return {band: terms["red"] for band, terms in lv["by_tcr_band"].items()}
    return {"band_invariant": lv["red"]}


# --- Load / resolve / sections ----------------------------------------------


def test_gb_v1_8_loads_with_every_required_section_populated() -> None:
    lib = load_assumptions("gb_v1_8")
    assert lib.metadata.version == "gb_v1_8"
    assert lib.metadata.market == "GB"
    assert lib.metadata.released == _RELEASED
    assert lib.metadata.currency == "GBP"
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"required section {section} is empty"
        for name, a in getattr(lib, section).items():
            assert isinstance(a, Assumption), f"{section}.{name} is not an Assumption"


def test_gb_v1_8_is_resolvable_and_never_the_superseded_default() -> None:
    """MD-F-15: the CLI resolves ``latest_version()`` rather than pinning ``gb_v0_1``.

    This deliberately does NOT assert ``latest_version() == "gb_v1_8"``. That form encodes a
    POSITION ("this release is newest"), which stops being true the moment gb_v1_9 is cut and turns
    a release-specific file into a false alarm. The INVARIANT is that the resolved default is the
    newest release on disk and never the superseded gb_v0_1, whose CM de-rating is overstated
    ~2.3-4x. Ordering comes from ``available_versions()``, which sorts on the numeric parts, so this
    also survives a two-digit minor (a plain string compare would rank gb_v1_10 below gb_v1_8).
    """
    versions = available_versions()
    assert "gb_v1_8" in versions
    assert versions.index("gb_v1_8") > versions.index("gb_v1_7")
    resolved = latest_version()
    assert resolved != "gb_v0_1"
    assert resolved == versions[-1]
    assert versions.index(resolved) >= versions.index("gb_v1_8"), (
        f"the resolved default {resolved!r} is older than gb_v1_8; MD-F-15 has regressed"
    )
    # This release must stay loadable for provenance after being superseded.
    assert load_assumptions("gb_v1_8").metadata.version == "gb_v1_8"


def test_gb_v1_8_entry_and_status_counts_locked() -> None:
    entries = _entries(load_assumptions("gb_v1_8"))
    assert len(entries) == _TOTAL_ENTRIES
    mix = [a.status for _, _, a in entries]
    assert mix.count(AssumptionStatus.NEEDS_REVIEW) == _NEEDS_REVIEW
    assert mix.count(AssumptionStatus.PLACEHOLDER) == _PLACEHOLDER
    assert mix.count(AssumptionStatus.CONFIRMED) == _CONFIRMED
    assert _NEEDS_REVIEW + _PLACEHOLDER + _CONFIRMED == _TOTAL_ENTRIES


def test_gb_v1_8_confirms_nothing() -> None:
    # 0 confirmed repo-wide, by design: no quote on this release may present as bankable. A fetched
    # public tariff is evidence, not confirmation - a human must still check it against the bill.
    for section, name, a in _entries(load_assumptions("gb_v1_8")):
        assert a.status != AssumptionStatus.CONFIRMED, f"{section}.{name} must not be confirmed"


# --- The anti-fabrication guard ----------------------------------------------


def test_gb_v1_8_every_entry_fully_documented() -> None:
    """Unit, source, owner, notes, a sane review date - on all 74 entries, no exceptions."""
    for section, name, a in _entries(load_assumptions("gb_v1_8")):
        where = f"{section}.{name}"
        assert a.unit.strip(), f"{where} has no unit"
        assert a.source.strip(), f"{where} has no source"
        assert a.owner_or_reviewer.strip(), f"{where} has no owner"
        assert a.notes.strip(), f"{where} has no notes"
        assert a.last_reviewed <= _RELEASED, f"{where} reviewed after the release date"
        assert a.last_reviewed >= date(2026, 6, 24), f"{where} carries a pre-library review date"


def test_gb_v1_8_every_source_points_at_evidence_or_says_it_is_not_sourced() -> None:
    """The fabrication guard. A source is a URL, an in-repo evidence file, or a plain refusal.

    A confident-sounding sentence with nothing behind it is the failure mode this forbids: it is
    exactly how an invented number acquires the appearance of provenance.
    """
    for section, name, a in _entries(load_assumptions("gb_v1_8")):
        where = f"{section}.{name}"
        src = a.source
        assert len(src.strip()) >= 40, f"{where} source is too thin to audit: {src!r}"
        has_evidence = any(m in src for m in _EVIDENCE_MARKERS)
        says_unsourced = any(m in src.upper() for m in _UNSOURCED_MARKERS)
        assert has_evidence or says_unsourced, (
            f"{where} cites neither evidence nor a refusal: {src!r}"
        )


def test_gb_v1_8_every_entry_carries_a_vintage_or_effective_date() -> None:
    """Undated is unusable: a tariff, index or de-rating factor is only true for a stated period.

    Accepted: a year 2014-2026 in the source or inside the value (``charging_year``,
    ``effective_from``, a delivery year); a named in-repo evidence document; or an explicit
    statement that the entry is not sourced.
    """
    for section, name, a in _entries(load_assumptions("gb_v1_8")):
        where = f"{section}.{name}"
        blob = _blob(a)
        dated = any(y in blob for y in _VINTAGE_YEARS)
        in_repo_doc = "source_docs/" in a.source or "docs/" in a.source
        says_unsourced = any(m in a.source.upper() for m in _UNSOURCED_MARKERS)
        assert dated or in_repo_doc or says_unsourced, f"{where} carries no vintage"


# --- The drift table, proved mechanically ------------------------------------


def test_gb_v1_8_priors_identical_to_gb_v1_7_except_the_enumerated_drift() -> None:
    """The release's own drift claim, enforced. Silent value drift between releases is a defect."""
    v7, v8 = load_assumptions("gb_v1_7"), load_assumptions("gb_v1_8")
    observed_drift: set[tuple[str, str]] = set()
    for section in _ALL_SECTIONS:
        prior, current = getattr(v7, section), getattr(v8, section)
        for name, before in prior.items():
            key = (section, name)
            assert name in current, f"gb_v1_8 DROPPED {section}.{name} - that breaks callers"
            after = current[name]
            if before.value != after.value:
                observed_drift.add(key)
                assert key in _VALUE_DRIFT, f"{section}.{name} value drifted unannounced"
                continue
            # Value carried forward. Which OTHER fields may move is enumerated per key.
            if key in _PROMOTED:
                assert after.status == AssumptionStatus.NEEDS_REVIEW, f"{key} promotion missing"
                assert before.status == AssumptionStatus.PLACEHOLDER, f"{key} was not a placeholder"
                allowed = {"source", "status", "notes", "last_reviewed"}
            elif key in _RESOURCED:
                assert after.status == before.status, f"{key} status moved with the re-sourcing"
                allowed = {"source", "notes", "last_reviewed"}
            elif key in _NOTES_ONLY:
                allowed = {"notes"}
            else:
                allowed = set()
            moved = {
                field
                for field in (
                    "unit",
                    "source",
                    "status",
                    "owner_or_reviewer",
                    "confidence",
                    "notes",
                    "last_reviewed",
                )
                if getattr(before, field) != getattr(after, field)
            }
            assert moved <= allowed, f"{section}.{name} moved {sorted(moved - allowed)} unannounced"
    assert observed_drift == set(_VALUE_DRIFT)
    for key, expected in _VALUE_DRIFT_SCALARS.items():
        assert getattr(v8, key[0])[key[1]].value == expected


def test_gb_v1_8_adds_exactly_three_keys_and_they_are_sourced_and_dated() -> None:
    v7, v8 = load_assumptions("gb_v1_7"), load_assumptions("gb_v1_8")
    new = {(sec, name) for sec, name, _ in _entries(v8) if name not in getattr(v7, sec)}
    assert new == _NEW_KEYS
    for section, name in sorted(new):
        a = v8.get(section, name)
        where = f"{section}.{name}"
        assert "http" in a.source, f"{where} is new but cites no fetchable source"
        assert any(y in a.source for y in _VINTAGE_YEARS), f"{where} cites no vintage"
        assert a.status == AssumptionStatus.NEEDS_REVIEW, where


def test_gb_v1_8_capex_and_opex_values_untouched() -> None:
    """Nothing tuned toward viability: every capex/opex VALUE is byte-identical to gb_v1_7."""
    v7, v8 = load_assumptions("gb_v1_7"), load_assumptions("gb_v1_8")
    for section in ("battery_capex", "battery_opex", "insurance", "business_rates", "dno_costs"):
        for name, before in getattr(v7, section).items():
            after = getattr(v8, section)[name]
            assert after.value == before.value, f"{section}.{name} value moved"
    assert v8.get("battery_capex", "installed_cost").value == 500000
    assert v8.get("battery_opex", "annual_fixed_opex").value == 12000


def test_gb_v1_8_prior_releases_untouched() -> None:
    # gb_v0_1..gb_v1_7 stay frozen for provenance; this is a new file, never an in-place edit.
    v7 = load_assumptions("gb_v1_7")
    assert v7.metadata.version == "gb_v1_7"
    assert v7.get("penalties", "cm_penalty_rate_fraction_of_clearing").value == 0.25
    assert v7.get("financing", "cpi_pct_per_year").value is None
    assert v7.get("duos", "band_windows_by_dno").value["ENWL"] is None
    assert load_assumptions("gb_v0_1").metadata.version == "gb_v0_1"


# --- The DUoS closure, per licence area -------------------------------------


def test_gb_v1_8_duos_both_tables_key_on_all_fourteen_areas_identically() -> None:
    # The GBP of red-band avoidance is the WINDOW times the RATE: a sourced window with no sourced
    # rate is half a constant, so the two tables must key identically, in the same order.
    lib = load_assumptions("gb_v1_8")
    bands = lib.get("duos", "band_windows_by_dno").value
    rates = lib.get("duos", "unit_rates_p_per_kwh_by_dno").value
    assert tuple(bands) == _LICENCE_AREAS
    assert tuple(rates) == _LICENCE_AREAS


@pytest.mark.parametrize("area", _LICENCE_AREAS)
def test_gb_v1_8_duos_area_carries_a_full_band_window_set(area: str) -> None:
    """Per area, not counted: a half-filled licence area must fail on its own name."""
    windows = load_assumptions("gb_v1_8").get("duos", "band_windows_by_dno").value[area]
    assert windows is not None, f"{area} band windows are still null"
    assert windows["charging_year"] == "2026-27"
    assert windows["effective_from"] == "2026-04-01"
    assert windows["timezone"] == "Europe/London"  # UK clock time, never UTC
    assert windows["red_months"] == "all"  # the LV/HV red band runs all year, unlike EDCM Super Red
    weekday = windows["weekday_windows"]
    assert set(weekday) == {"red", "amber", "green"}, area
    for band, spans in weekday.items():
        assert spans, f"{area} weekday {band} has no window"
        for span in spans:
            assert set(span) == {"start", "end"}, f"{area} {band} window is malformed"
            assert span["start"] < span["end"], f"{area} {band} window is inverted"
    assert windows["weekend_windows"], f"{area} has no weekend treatment"


@pytest.mark.parametrize("area", _LICENCE_AREAS)
def test_gb_v1_8_duos_area_carries_a_full_unit_rate_set(area: str) -> None:
    rates = load_assumptions("gb_v1_8").get("duos", "unit_rates_p_per_kwh_by_dno").value[area]
    assert rates is not None, f"{area} unit rates are still null"
    assert rates["charging_year"] == "2026-27"
    for family in ("lv_site_specific", "lv_sub_site_specific", "hv_site_specific"):
        assert family in rates, f"{area} is missing {family}"
    reds = _lv_red_rates(rates)
    assert reds, f"{area} publishes no LV red rate"
    for band, red in reds.items():
        assert red > 0, f"{area} LV red rate for {band} is not positive"
    credits = rates["generation_export_credits"]
    for family in ("lv_site_specific", "lv_sub_site_specific", "hv_site_specific"):
        assert credits[family]["red"] < 0, f"{area} {family} export credit is not a credit"


@pytest.mark.parametrize("area", _LICENCE_AREAS)
def test_gb_v1_8_duos_area_carries_its_own_citation(area: str) -> None:
    """Fourteen areas, fourteen citations. One traceable area does not make a sourced table."""
    lib = load_assumptions("gb_v1_8")
    for key in ("band_windows_by_dno", "unit_rates_p_per_kwh_by_dno"):
        entry = lib.get("duos", key)
        block = entry.value[area]
        if area == _CARRIED_FORWARD_AREA:
            # Carried byte-identical from gb_v1_7: its statement URL is cited on the ENTRY source.
            assert area in entry.source, f"{key}: {area} is not cited on the entry source"
            assert "http" in entry.source
            continue
        assert "http" in block["source_url"], f"{key}: {area} has no statement URL"
        assert len(block["sha256"]) == 64, f"{key}: {area} sha256 is not a full digest"
        assert set(block["sha256"]) <= set("0123456789abcdef"), f"{key}: {area} sha256 is not hex"
    windows = lib.get("duos", "band_windows_by_dno").value[area]
    if area != _CARRIED_FORWARD_AREA:
        assert "2026" in windows["statement"], f"{area} statement names no charging year"


@pytest.mark.parametrize("area", _LICENCE_AREAS)
def test_gb_v1_8_duos_bank_holidays_are_red_never_green(area: str) -> None:
    """Pinned per area because this project already got it wrong once.

    Every 2026/27 statement prints its weekday row as "Monday to Friday (Including Bank Holidays)",
    so a bank holiday takes the WEEKDAY pattern and the red band applies. The sourced NGED statement
    calls treating them as green "a common modelling error". Treating them as green would credit
    red-band avoidance the site cannot earn on roughly eight weekdays a year and would mis-time
    dispatch on every one of them.
    """
    windows = load_assumptions("gb_v1_8").get("duos", "band_windows_by_dno").value[area]
    assert windows["bank_holiday_band"] == "red", f"{area} does not treat bank holidays as red"
    super_red = windows.get("edcm_super_red")
    if super_red is not None:
        assert super_red["days"] == "monday_to_friday_including_bank_holidays", area
        assert super_red["months"] == [11, 12, 1, 2]  # Nov-Feb only, unlike the all-year LV/HV red


def test_gb_v1_8_duos_declares_a_rate_structure_so_a_consumer_can_branch() -> None:
    """The DNOs publish two different shapes and the entry says a consumer MUST check which.

    KNOWN GAP, reported not fixed: NGED_EM, the one area carried byte-identical from gb_v1_7, has no
    `rate_structure` key at all, so the documented read fails on precisely the area four in-repo
    sites reference. The test allows that one carry-forward and locks the contract for the other 13.
    """
    rates = load_assumptions("gb_v1_8").get("duos", "unit_rates_p_per_kwh_by_dno").value
    for area, block in rates.items():
        declared = block.get("rate_structure")
        if declared is None:
            assert area == _CARRIED_FORWARD_AREA, f"{area} publishes no rate_structure"
            continue
        assert declared in _RATE_STRUCTURES, f"{area} declares unknown structure {declared!r}"
        lv = block["lv_site_specific"]
        if declared == "band_invariant_units_with_banded_fixed":
            assert "by_tcr_band" not in lv, f"{area} says band-invariant but bands its unit rates"
            assert set(block["fixed_p_per_mpan_day"]["lv_site_specific"]) == set(_TCR_BANDS), area
        else:
            assert set(lv["by_tcr_band"]) == set(_TCR_BANDS), f"{area} bands are incomplete"


def test_gb_v1_8_duos_variation_is_structural_so_no_cross_region_proxy_is_safe() -> None:
    """gb_v1_7 priced every site off NGED_EM. These are the numbers that made that a defect."""
    bands = load_assumptions("gb_v1_8").get("duos", "band_windows_by_dno").value
    red_windows = {
        area: tuple((w["start"], w["end"]) for w in block["weekday_windows"]["red"])
        for area, block in bands.items()
    }
    # Six distinct red-window patterns across 14 areas, and London runs TWO red windows a day.
    assert len(set(red_windows.values())) == 6
    assert red_windows["UKPN_LPN"] == (("11:00", "14:00"), ("16:00", "19:00"))
    assert red_windows["NGED_SW"] == (("17:00", "19:00"),)
    assert red_windows["NGED_SWALES"] == (("17:00", "19:30"),)
    assert red_windows["NPG_NORTHEAST"] == (("16:00", "19:30"),)
    assert red_windows["SSEN_SEPD"] == (("16:30", "19:30"),)
    # Seven areas charge AMBER at weekends; the rest are all-green. Assuming green loses that cost.
    weekend_amber = {a for a, b in bands.items() if "amber" in b["weekend_windows"]}
    assert weekend_amber == {
        "NGED_SW",
        "NGED_SWALES",
        "SSEN_SEPD",
        "SSEN_SHEPD",
        "SPEN_SPD",
        "SPEN_SPM",
        "ENWL",
    }


def test_gb_v1_8_duos_red_rate_spread_is_the_size_of_the_proxy_error() -> None:
    """The cross-area LV red spread, measured from the closed table rather than asserted.

    NOTE-LEVEL DISCREPANCY, reported not fixed: the entry's notes give the span as "5.471 (SEPD LV
    band 4) to 14.833 (NGED SW LV) ... a 2.7x cross-area range". The real minimum is LPN band 4 at
    4.257, so the true spread is ~3.5x. The note understates the variation, which is the direction
    that makes the retired proxy look better than it was. Values are tested here, not prose.
    """
    rates = load_assumptions("gb_v1_8").get("duos", "unit_rates_p_per_kwh_by_dno").value
    observed = {
        (area, band): red for area, b in rates.items() for band, red in _lv_red_rates(b).items()
    }
    lowest = min(observed.items(), key=lambda kv: kv[1])
    highest = max(observed.items(), key=lambda kv: kv[1])
    assert lowest == (("UKPN_LPN", "band_4"), 4.257)
    assert highest == (("NGED_SW", "band_invariant"), 14.833)
    assert highest[1] / lowest[1] > 3.4
    # The retired proxy sat mid-range, so it was wrong in BOTH directions depending on the site.
    proxy = observed[("NGED_EM", "band_invariant")]
    assert proxy == 7.777
    assert lowest[1] < proxy < highest[1]
    # And the two areas the in-repo real sites actually sit in are BELOW the proxy: closing this
    # REDUCES their modelled red-band avoidance. The closure is unflattering, which is the point.
    assert observed[("NGED_WM", "band_invariant")] == 6.515
    assert observed[("SSEN_SEPD", "band_4")] == 5.471
    assert observed[("NGED_WM", "band_invariant")] < proxy
    assert observed[("SSEN_SEPD", "band_4")] < proxy


def test_gb_v1_8_duos_export_credit_hazard_survives_the_closure() -> None:
    """A MILP with no charge/discharge binary can book a cycle that never happened physically.

    In South Wales the red export CREDIT exceeds the red import CHARGE as printed, and in SEPD they
    are within 1%. gb_v1_7 flagged this for one area; it must stay visible now there are fourteen.
    """
    rates = load_assumptions("gb_v1_8").get("duos", "unit_rates_p_per_kwh_by_dno").value
    swales = rates["NGED_SWALES"]
    assert (
        -swales["generation_export_credits"]["lv_site_specific"]["red"]
        > (swales["lv_site_specific"]["red"])
    )
    sepd_import = rates["SSEN_SEPD"]["lv_site_specific"]["by_tcr_band"]["no_residual"]["red"]
    sepd_credit = -rates["SSEN_SEPD"]["generation_export_credits"]["lv_site_specific"]["red"]
    assert sepd_credit / sepd_import > 0.99
    # Fixed adders for the same TCR band differ by more than three orders of magnitude across areas,
    # so a fixed line must never be proxied either. (The entry's notes say "~50x", which is the
    # EPN-to-SPM comparison, not the LPN-to-SPM pair it cites. Reported, not corrected here.)
    lpn_band_4 = rates["UKPN_LPN"]["lv_site_specific"]["by_tcr_band"]["band_4"][
        "fixed_p_per_mpan_day"
    ]
    spm_band_4 = rates["SPEN_SPM"]["fixed_p_per_mpan_day"]["lv_site_specific"]["band_4"]
    assert lpn_band_4 == 2.35
    assert spm_band_4 == 3592.14
    assert spm_band_4 / lpn_band_4 > 1000


def test_gb_v1_8_tcr_band_thresholds_make_the_banded_lookups_readable() -> None:
    """The new key exists because `by_tcr_band` rates and the TNUoS bands are unreadable without
    it: neither can be looked up until the site's residual band is known."""
    thresholds = load_assumptions("gb_v1_8").get("duos", "tcr_band_thresholds_kva").value
    assert "inclusive of the upper threshold" in thresholds["boundary_rule"]
    for family, expected in (
        ("lv_site_specific", [80, 150, 231]),
        ("lv_sub_site_specific", [80, 150, 231]),
        ("hv_site_specific", [422, 1000, 1800]),
        ("edcm", [5000, 12000, 21500]),
    ):
        ladder = thresholds[family]
        assert [ladder[f"band_{i}"][1] for i in (1, 2, 3)] == expected, family
        # Band 4 is unbounded above: the null is structural, declared in the unit, never a zero.
        assert ladder["band_4"][1] is None, family
        assert ladder["band_4"][0] == expected[-1]
    # Upper-inclusive matters: exactly 231 kVA is band 3, and the 24-month MIC basis is recorded.
    assert thresholds["lv_site_specific"]["band_3"] == [150, 231]
    assert "Maximum Import Capacity" in thresholds["allocation_basis"]["hh_sites"]
    assert "null upper bound = unbounded" in (
        load_assumptions("gb_v1_8").get("duos", "tcr_band_thresholds_kva").unit
    )


# --- Capacity Market: the closed de-rating bins and the statutory penalty ----


def test_gb_v1_8_five_hour_de_rating_bins_are_closed_by_requery_not_interpolation() -> None:
    """The deliberate inversion of gb_v1_7's absent-bins assertion.

    gb_v1_7 asserted 5h and 5.5h were ABSENT and must never be interpolated. gb_v1_8 closes them by
    re-querying the same NESO CKAN resource, so the values are published, not derived. The published
    5h factor is NOT 5x the 1h factor, which is the proof that interpolation would have been wrong.
    """
    lib = load_assumptions("gb_v1_8")
    table = lib.get("capacity_market", "de_rating_by_auction_and_delivery_year").value
    t4 = table["full_curves"]["T-4"]["2029-30"]
    t1 = table["full_curves"]["T-1"]["2026-27"]
    assert t4["5"] == 0.5509
    assert t4["5.5"] == 0.6052
    assert t1["5"] == 0.6745
    assert t1["5.5"] == 0.7329
    # Non-proportionality, the reason a re-query beats an interpolation: 5 x 0.1099 = 0.5495.
    assert 5 * t4["1"] != pytest.approx(t4["5"], abs=1e-9)
    assert t4["5"] - 5 * t4["1"] == pytest.approx(0.0014, abs=1e-6)
    # Monotonic in duration, and the plateau is unchanged.
    for curve in (t4, t1):
        numeric = sorted((float(k), v) for k, v in curve.items() if "_to_" not in k)
        assert [v for _, v in numeric] == sorted(v for _, v in numeric)
    assert t4["8.5_to_12"] == 0.9216
    assert t1["7.5_to_12"] == 0.9216
    assert "re-queried" in table["missing_bins"].lower()
    assert "never interpolate" in table["lookup_convention"].lower()


def test_gb_v1_8_legacy_de_rating_and_clearing_price_keys_are_unchanged() -> None:
    # Existing callers read the duration-only key and the clearing-price table; neither moves.
    v7, v8 = load_assumptions("gb_v1_7"), load_assumptions("gb_v1_8")
    assert v8.get("capacity_market", "de_rating_factor_by_duration_h").value == {
        "1": 0.1099,
        "2": 0.2198,
        "4": 0.4396,
    }
    for name in (
        "clearing_price_gbp_per_kw_yr",
        "clearing_price_by_auction_and_delivery_year",
        "dsr_de_rating_by_auction_and_delivery_year",
    ):
        assert v8.get("capacity_market", name).value == v7.get("capacity_market", name).value
    # The history slice the new bins belong to is untouched: only the full curves gained bins.
    assert (
        v8.get("capacity_market", "de_rating_by_auction_and_delivery_year").value[
            "history_1h_2h_4h"
        ]
        == v7.get("capacity_market", "de_rating_by_auction_and_delivery_year").value[
            "history_1h_2h_4h"
        ]
    )


def test_gb_v1_8_cm_penalty_rate_is_the_statutory_pe_over_24_and_this_flatters_quotes() -> None:
    """THE FLATTERING CHANGE, pinned deliberately because it improves every CM quote.

    SI 2014/2043 Sch 1 para 5(3) reads PRN = PEzx x (1/24). gb_v1_7 carried 0.25, which encodes
    PE/4, while its own `cm_penalty_rate_basis` already implied 1/24: the release held the
    contradiction open on purpose. gb_v1_8 resolves it toward the statute, which cuts the bounded
    worst-case penalty line ~6x. A correction that helps the numbers gets MORE scrutiny, not less,
    so the value, the ratio, the disclosure and the unchanged caps are all locked here.
    """
    lib = load_assumptions("gb_v1_8")
    rate = lib.get("penalties", "cm_penalty_rate_fraction_of_clearing")
    assert rate.value == 0.0416667
    assert rate.value == pytest.approx(1 / 24, rel=1e-6)
    assert rate.status == AssumptionStatus.NEEDS_REVIEW  # statute read, human confirmation pending
    prior = load_assumptions("gb_v1_7").get("penalties", "cm_penalty_rate_fraction_of_clearing")
    assert prior.value / rate.value == pytest.approx(6.0, rel=1e-5)
    # The statute is cited by instrument, schedule and paragraph, and the direction is disclosed.
    assert "2014/2043" in rate.source and "5(3)" in rate.source
    assert "FLATTERS" in rate.notes.upper()
    # Internally consistent with the sourced regime entry, which gb_v1_7 contradicted.
    basis = lib.get("penalties", "cm_penalty_rate_basis").value
    assert basis["implied_fraction_of_clearing_per_mwh"] == rate.value
    assert "PE / 24" in basis["penalty_rate_formula"]
    # The caps bound the worst case and are NOT relaxed: same values, better evidence.
    assert lib.get("penalties", "cm_monthly_cap_pct_of_revenue").value == 200.0
    assert lib.get("penalties", "cm_annual_cap_pct_of_revenue").value == 100.0
    assert basis["monthly_cap_pct_of_monthly_capacity_payment"] == 200
    assert basis["annual_cap_pct_of_annual_capacity_payments"] == 100
    # And the base rate stays visible: no stress event has ever been declared.
    assert basis["stress_events_declared_to_date"] == 0


# --- CPI, opex, TNUoS, DFS ---------------------------------------------------


def test_gb_v1_8_cpi_is_closed_from_the_obr_forecast_with_a_per_line_direction() -> None:
    """gb_v1_7 left this an empty home. A null escalation rate runs nominal revenue against real
    opex, so the null was not neutral: it silently drifted ~2%/yr, compounding."""
    cpi = load_assumptions("gb_v1_8").get("financing", "cpi_pct_per_year")
    assert cpi.value == 2.0
    assert cpi.status == AssumptionStatus.NEEDS_REVIEW
    assert "OBR" in cpi.source and "2026" in cpi.source
    assert "obr.uk" in cpi.source
    # The three questions gb_v1_7's empty home posed are answered on the key, not left implicit.
    notes = cpi.notes
    assert "CPIH" in notes  # named as NOT interchangeable with CPI
    assert "2.3" in notes  # the 2026 near-year path, and why 2.0 flat is used instead
    assert "WORSE" in notes.upper() and "BETTER" in notes.upper()  # direction stated per line
    # The corroborating long-run figure gb_v1_7 recorded is still carried, not dropped.
    assert "1.95" in cpi.source


def test_gb_v1_8_opex_reconciliation_is_reproducible_from_the_library_itself() -> None:
    """The opex basis replaced a bare placeholder. The arithmetic it claims is checkable here.

    NOTE-LEVEL DISCREPANCY, reported not fixed: the notes say 12,000 "sits between" a PNNL floor of
    ~GBP 2.5-4k and a decomposed Gresham residual of ~GBP 11.4k. It does not: 12,000 sits just ABOVE
    that residual. The error is in the conservative direction (a higher opex makes a site look
    worse), and the separately-booked-lines arithmetic the claim rests on does reconcile.
    """
    lib = load_assumptions("gb_v1_8")
    opex = lib.get("battery_opex", "annual_fixed_opex")
    assert opex.value == 12000
    assert opex.status == AssumptionStatus.NEEDS_REVIEW
    assert "PNNL" in opex.source and "Gresham" in opex.source
    # The lines this key must NOT include, summed at 1 MW from the library's own entries.
    separately_booked = (
        lib.get("business_rates", "annual_gbp_per_mw").value
        + lib.get("insurance", "annual_pct_of_capex").value
        / 100
        * lib.get("battery_capex", "installed_cost").value
        + lib.get("metering_idno", "annual_gbp_per_site").value
        + lib.get("dno_costs", "annual_gbp_per_site").value
    )
    assert separately_booked == pytest.approx(13_200.0)
    gresham = lib.get("battery_opex", "annual_fixed_opex_grid_scale_benchmark").value
    all_in = gresham["implied_gbp_per_mw_yr"]["2025"]
    assert all_in == 24_600
    # The library's own all-in at 1 MW lands within 2.5% of the published grid-scale figure.
    assert abs((opex.value + separately_booked) / all_in - 1) < 0.025
    # And the decomposed residual is slightly BELOW 12,000, not above it.
    assert all_in - separately_booked == pytest.approx(11_400.0)
    assert opex.value > all_in - separately_booked
    # The unquantified small-scale premium still points UP and must stay recorded.
    assert "BTM" in opex.notes and "above than below" in opex.notes


def test_gb_v1_8_tnuos_band_table_lands_and_the_per_site_line_stays_derived() -> None:
    lib = load_assumptions("gb_v1_8")
    bands = lib.get("tnuos", "demand_residual_bands_gbp_per_day").value
    assert bands["charging_year"] == "2026-27"
    assert bands["effective_from"] == "2026-04-01"
    table = bands["bands"]
    for prefix in ("LV", "HV", "EHV", "T_Demand"):
        ladder = [table[f"{prefix}{i}"] for i in range(1, 5)]
        assert ladder == sorted(ladder), f"{prefix} bands are not monotonic"
    assert table["LV4"] == 38.180103
    # The site stacks previously assumed LV2. The correction RAISES an unavoidable bill line.
    assert table["LV4"] / table["LV2"] == pytest.approx(3.317, rel=1e-3)
    assert table["LV4"] > bands["comparison_2025_26"]["LV4"]
    revenue = bands["total_residual_revenue_gbp_m"]
    assert revenue["2026-27"] / revenue["2025-26"] == pytest.approx(1.633, rel=1e-3)
    # The per-site GBP stays derived per site, never a library constant.
    per_site = lib.get("tnuos", "demand_residual_gbp_per_year")
    assert per_site.value is None
    assert "SITE-DERIVED BY CONSTRUCTION" in per_site.source
    assert "tcr_band_thresholds_kva" in per_site.source


def test_gb_v1_8_dfs_zones_are_numbered_because_no_names_are_published() -> None:
    dfs = load_assumptions("gb_v1_8").get("ancillary_services", "dfs_registered_unit_capacity_mw")
    zones = dfs.value["zone_names"]
    assert zones["published_names"] is False
    assert zones["zone_identifiers"] == list(range(1, 13))
    assert dfs.value["zone_count"] == len(zones["zone_identifiers"])
    assert zones["geojson_property"] == "Region"
    assert len(zones["geojson_sha256"]) == 64
    assert "point-in-polygon" in zones["allocation"]
    # The eligibility floor DFS exists to represent is unchanged.
    assert dfs.value["minimum"] == 0.1
    assert dfs.value["maximum"] == 100.0
    # The data ask moved from "what are the zones" to "what is each site's full address".
    assert "DATA ASK" in dfs.notes.upper()


def test_gb_v1_8_dfs_guaranteed_acceptance_price_carries_a_definition_not_a_price() -> None:
    gap = load_assumptions("gb_v1_8").get(
        "ancillary_services", "dfs_guaranteed_acceptance_price_gbp_per_mwh"
    )
    assert gap.value["applies_to"] == "DFS Tests only, never in-merit live events"
    # The current value is genuinely unpublished. A null here is a refusal, never GBP 0.
    assert gap.value["current_published_value"] is None
    assert gap.value["historic_anchor_winter_2022_23"] == 3000
    assert "SEARCH RECORDED" in gap.source.upper()
    assert "never read it as GBP 0" in gap.notes
    assert "NOT ON ANY PRICING PATH" in gap.notes


# --- Honest-missing discipline ----------------------------------------------


def test_gb_v1_8_null_valued_entries_state_what_would_settle_them() -> None:
    """A null is admissible only if the entry says what is missing. Enforced on every empty value.

    The marker set is deliberately wide (a data ask, a recorded search, an empty-home declaration, a
    "never zero" statement), because the point is that SOMETHING was written, not a phrasing style.
    """
    for section, name, a in _entries(load_assumptions("gb_v1_8")):
        if a.value is not None:
            continue
        blob = f"{a.source} {a.notes}".upper()
        assert any(m in blob for m in _MISSING_MARKERS), (
            f"{section}.{name} is empty but names no data ask"
        )


def test_gb_v1_8_every_remaining_null_is_enumerated_and_none_reads_as_zero() -> None:
    """The full null inventory, nested included, with the reason each one is empty.

    This is the guard against a null quietly becoming a zero on a priced path: any new null has to
    be added here with a recorded search, and every one of these entries must say in words that it
    is not zero, or name the document that would settle it.
    """
    lib = load_assumptions("gb_v1_8")

    def _null_paths(value: Any, prefix: str = "") -> list[str]:
        if isinstance(value, dict):
            return [p for k, v in value.items() for p in _null_paths(v, f"{prefix}/{k}")]
        if isinstance(value, list):
            return [p for i, v in enumerate(value) for p in _null_paths(v, f"{prefix}[{i}]")]
        return [prefix] if value is None else []

    observed = {
        f"{section}.{name}{path}"
        for section, name, a in _entries(lib)
        for path in _null_paths(a.value)
    }
    # Structural nulls: an unbounded upper bound on the top TCR band. Declared in the entry's unit.
    structural = {
        f"duos.tcr_band_thresholds_kva/{family}/band_4[1]"
        for family in ("lv_site_specific", "lv_sub_site_specific", "hv_site_specific", "edcm")
    }
    # Every other null, with why it is empty. Each is OFF the priced floor by construction.
    refused = {
        # D30 empty homes: a firmness promise and an availability price only a contract can assert.
        "availability.availability_price_gbp_per_mw_h",
        "availability.expected_availability_pct",
        # Site-derived by construction, now derivable from the two new keys.
        "tnuos.demand_residual_gbp_per_year",
        # Searched 2026-08-12 and not closed. No GB platform publishes a revenue share.
        "balancing_mechanism.aggregation_rule/aggregator_revenue_share_pct",
        # Searched 2026-08-12: NESO publishes the K methodology, not realised K.
        "ancillary_services.performance_factor_k_parameters/assumed_realised_k",
        # NESO has published no GAP for the post-April-2026 zonal service.
        "ancillary_services.dfs_guaranteed_acceptance_price_gbp_per_mwh/current_published_value",
        # No minimum offer size published; both DNOs now tender via Piclo.
        "local_flex_piclo.product_types/minimum_offer_size/SPEN",
        "local_flex_piclo.product_types/minimum_offer_size/NPG",
        # "No capacity threshold published", which is not the same as a threshold of zero.
        "local_flex_piclo.product_types/minimum_offer_size/NGED/min_capacity_kw",
        # NGED records availability grace as N/A for these two products.
        "local_flex_piclo.settlement_mechanics_by_product/by_product/NGED_SU/"
        "availability_grace_factor_pct",
        "local_flex_piclo.settlement_mechanics_by_product/by_product/NGED_OU/"
        "availability_grace_factor_pct",
    }
    assert observed == structural | refused
    # Each refusal's own entry must say it is not a zero, or say what would settle it.
    for path in sorted(refused):
        section, _, rest = path.partition(".")
        name = rest.split("/")[0]
        blob = f"{lib.get(section, name).source} {lib.get(section, name).notes}".upper()
        assert any(m in blob for m in _MISSING_MARKERS), f"{path} names no data ask"


def test_gb_v1_8_recorded_searches_name_the_date_and_the_route_that_failed() -> None:
    """A "we looked" claim is only auditable if it says when it was looked for and where."""
    lib = load_assumptions("gb_v1_8")
    searched = (
        ("balancing_mechanism", "aggregation_rule"),
        ("ancillary_services", "performance_factor_k_parameters"),
        ("local_flex_piclo", "product_types"),
    )
    for section, name in searched:
        a = lib.get(section, name)
        where = f"{section}.{name}"
        assert "RECORDED SEARCH" in a.notes.upper(), f"{where} claims no recorded search"
        assert "2026-08-12" in a.notes, f"{where} records no search date"
        assert "DATA ASK" in a.notes.upper(), f"{where} names no data ask"
    # And the gated-upside framing that makes each of those nulls safe is still explicit.
    assert "NOT ON THE PRICED FLOOR" in lib.get("balancing_mechanism", "aggregation_rule").notes
    assert (
        "NOT ON THE PRICED FLOOR"
        in lib.get("ancillary_services", "performance_factor_k_parameters").notes
    )
