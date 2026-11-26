"""LIT-F-05: the DUoS band calendar is per licence area, and a fallback is LOUD.

Why this file exists. For a sub-1 MW behind-the-meter site the whole NESO/Capacity-Market stack
prices at £0 on sourced 1 MW thresholds, so the contractible floor is only two streams: tariff
arbitrage and **DUoS red-band avoidance**. The entire £ of the second is set by **when the band
falls** — so one national 16:00–19:00 weekday window standing in for 14 published licence areas is
not an approximation, it is a mispriced floor stream that no downstream figure reveals.

The three things pinned here, each of which was wrong before:

1. **The window is the area's own.** SEPD publishes red **16:30–19:30**, half an hour later than the
   generic default at both ends, so three of the six live sites (Ewelme, Knowl Hill, Harwell) had
   their avoidance computed in the wrong half-hours. Seven areas also charge **weekend amber**,
   which the generic default priced as green. Every one of the 14 areas is pinned below against its
   own 2026/27 statement, so an area that is only PARTLY transcribed fails this run rather than
   passing on a half-applied calendar.
2. **Bank holidays are RED, and the treatment is read per area, never assumed.** Every sourced
   statement bands its weekday row "Monday to Friday (Including Bank Holidays)"; GB bank holidays
   fall Monday–Friday, so they are already banded as the weekdays they are. Forcing them green is
   "a common modelling error" (register) — and forcing them red for an area that published
   something else would be the same error mirrored, so an unactionable treatment falls back loudly.
3. **A fallback says so.** Absent key, null area, unmapped region string, partial area, foreign
   clock and unusable season all return the documented generic windows *with a note*. A silent
   fallback to one national window is precisely the defect LIT-F-05 files.

`connection.dno_region` is a free `str | None` with no enum, so the region→key mapping is a hazard
in its own right: an unrecognised string must never be resolved to a neighbouring area.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests._dpa_support import dpa_released_sites

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation.tariff_calendar import (
    AMBER,
    DEFAULT_DUOS_WINDOWS,
    GREEN,
    RED,
    DuosWindows,
    duos_band_labels,
    duos_rate_series_with_absences,
    duos_windows_from_assumptions,
    red_band_mask,
)

# --------------------------------------------------------------------------------------------
# The per-area pin. Each row is that DNO's own 2026/27 Use of System Charging Statement as carried
# in `duos.band_windows_by_dno`, written in published clock form so the pin reads like the source.
# A partially-filled or re-transcribed area changes one of these and fails.
# --------------------------------------------------------------------------------------------
PUBLISHED_2026_27: dict[str, dict[str, tuple[str, ...]]] = {
    "NGED_EM": {"red": ("16:00-19:00",), "weekend_amber": ()},
    "NGED_WM": {"red": ("16:00-19:00",), "weekend_amber": ()},
    "NGED_SW": {"red": ("17:00-19:00",), "weekend_amber": ("16:30-19:30",)},
    "NGED_SWALES": {"red": ("17:00-19:30",), "weekend_amber": ("12:00-13:00", "16:00-21:00")},
    "UKPN_EPN": {"red": ("16:00-19:00",), "weekend_amber": ()},
    "UKPN_LPN": {"red": ("11:00-14:00", "16:00-19:00"), "weekend_amber": ()},
    "UKPN_SPN": {"red": ("16:00-19:00",), "weekend_amber": ()},
    "SSEN_SEPD": {"red": ("16:30-19:30",), "weekend_amber": ("09:30-21:30",)},
    "SSEN_SHEPD": {"red": ("16:00-19:00",), "weekend_amber": ("12:00-20:00",)},
    "SPEN_SPD": {"red": ("16:30-19:30",), "weekend_amber": ("16:00-20:00",)},
    "SPEN_SPM": {"red": ("16:30-19:30",), "weekend_amber": ("16:00-20:00",)},
    "NPG_NORTHEAST": {"red": ("16:00-19:30",), "weekend_amber": ()},
    "NPG_YORKSHIRE": {"red": ("16:00-19:30",), "weekend_amber": ()},
    "ENWL": {"red": ("16:00-19:00",), "weekend_amber": ("16:00-19:00",)},
}

# Weekdays covering a full week from Monday, and a Saturday, both in winter (no BST ambiguity).
_MON = pd.date_range("2026-01-05 00:00", periods=48 * 7, freq="30min")
_SAT = pd.date_range("2026-01-10 00:00", periods=48, freq="30min")


def _minutes(published: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    """Turn published ``"16:30-19:30"`` clock form into the module's minute-of-day pairs."""

    def m(text: str) -> int:
        hh, mm = text.split(":")
        return int(hh) * 60 + int(mm)

    return tuple((m(w.split("-")[0]), m(w.split("-")[1])) for w in published)


def _v1_8() -> object:
    return load_assumptions("gb_v1_8")


def _assumption(value: object) -> object:
    from gridq.assumptions import Assumption

    return Assumption(
        value=value,
        unit="UK clock time (Europe/London), per licence area",
        source="test",
        status="placeholder",
        owner_or_reviewer="test",
        confidence="low",
        last_reviewed=date(2026, 8, 13),
        notes="test-only",
    )


def _lib_with_areas(value: object) -> object:
    """A library whose ``duos.band_windows_by_dno`` is exactly ``value`` (synthetic areas)."""
    lib = copy.deepcopy(load_assumptions("gb_v0_1"))
    lib.duos["band_windows_by_dno"] = _assumption(value)
    return lib


def _area(**overrides: object) -> dict[str, object]:
    """A well-formed synthetic area (SEPD-shaped), so a test can break exactly one thing."""
    area: dict[str, object] = {
        "licence_area": "Testshire",
        "charging_year": "2026-27",
        "timezone": "Europe/London",
        "bank_holiday_band": "red",
        "red_months": "all",
        "weekday_windows": {
            "red": [{"start": "16:30", "end": "19:30"}],
            "amber": [{"start": "07:00", "end": "16:30"}, {"start": "19:30", "end": "22:00"}],
            "green": [{"start": "00:00", "end": "07:00"}, {"start": "22:00", "end": "24:00"}],
        },
        "weekend_windows": {
            "amber": [{"start": "09:30", "end": "21:30"}],
            "green": [{"start": "00:00", "end": "09:30"}, {"start": "21:30", "end": "24:00"}],
        },
    }
    area.update(overrides)
    return area


# --------------------------------------------------------------------------------------------
# 1. Every area wires, and its windows are its own.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("region", sorted(PUBLISHED_2026_27))
def test_every_licence_area_wires_to_its_own_published_windows(region: str) -> None:
    """All 14 GB areas resolve, and each carries the red/weekend-amber window IT publishes.

    Parametrised per area on purpose: a partially transcribed area fails its own case loudly
    instead of disappearing into an aggregate that still passes.
    """
    windows, note = duos_windows_from_assumptions(_v1_8(), region)
    expected = PUBLISHED_2026_27[region]
    assert windows.dno_region == region, "the resolved area must be the one asked for"
    assert windows.weekday_red_min == _minutes(expected["red"])
    assert windows.weekend_amber_min == _minutes(expected["weekend_amber"])
    assert windows.red_months is None, "every sourced 2026/27 area charges red all year"
    assert note is not None and f"band_windows_by_dno[{region}]" in note


def test_the_areas_genuinely_differ_so_no_national_window_could_serve_them() -> None:
    """The premise of LIT-F-05: one window cannot be right for 14 areas."""
    reds = {
        r: duos_windows_from_assumptions(_v1_8(), r)[0].weekday_red_min for r in PUBLISHED_2026_27
    }
    assert len({v for v in reds.values()}) >= 5, (
        "published red windows take several distinct shapes"
    )
    weekend_amber = {r for r in PUBLISHED_2026_27 if PUBLISHED_2026_27[r]["weekend_amber"]}
    assert len(weekend_amber) == 7, (
        "seven areas charge weekend amber; the generic default says green"
    )


def test_sepd_red_band_is_1630_to_1930_not_the_generic_1600_to_1900() -> None:
    """The correction that moves Ewelme, Knowl Hill and Harwell (all SSEN_SEPD)."""
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    generic = red_band_mask(_MON, DEFAULT_DUOS_WINDOWS)
    published = red_band_mask(_MON, sepd)
    by_ts = dict(zip(_MON, published, strict=True))
    generic_by_ts = dict(zip(_MON, generic, strict=True))
    # 16:00 and 16:30 are the half-hours the two calendars disagree about at the start of the peak…
    assert not by_ts[pd.Timestamp("2026-01-05 16:00")], "SEPD red starts at 16:30, not 16:00"
    assert generic_by_ts[pd.Timestamp("2026-01-05 16:00")], (
        "…and the generic default started at 16:00"
    )
    assert by_ts[pd.Timestamp("2026-01-05 16:30")]
    # …and 19:00/19:30 at the end.
    assert by_ts[pd.Timestamp("2026-01-05 19:00")], "SEPD red runs to 19:30, so 19:00 is still red"
    assert not generic_by_ts[pd.Timestamp("2026-01-05 19:00")]
    assert not by_ts[pd.Timestamp("2026-01-05 19:30")]
    # Same count of red half-hours, shifted by one — the £ moves because the LOAD in them differs.
    assert int(published.sum()) == int(generic.sum()) == 30


def test_sepd_weekend_is_amber_not_green() -> None:
    """SEPD charges amber 09:30–21:30 at weekends; the generic default charged green all weekend."""
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    bands = dict(zip(_SAT, duos_band_labels(_SAT, sepd), strict=True))
    generic = dict(zip(_SAT, duos_band_labels(_SAT, DEFAULT_DUOS_WINDOWS), strict=True))
    assert bands[pd.Timestamp("2026-01-10 09:30")] == AMBER
    assert bands[pd.Timestamp("2026-01-10 17:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-10 21:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-10 09:00")] == GREEN, "amber starts at 09:30"
    assert bands[pd.Timestamp("2026-01-10 21:30")] == GREEN, "amber ends at 21:30"
    assert all(v == GREEN for v in generic.values()), "the generic default priced the weekend green"
    # A weekend amber band is NOT a weekend red band — no avoidance stream is created here.
    assert not red_band_mask(_SAT, sepd).any()


def test_london_carries_two_red_windows_a_day() -> None:
    """LPN publishes 11:00–14:00 AND 16:00–19:00 — 6 red hours, double every other area."""
    lpn, _ = duos_windows_from_assumptions(_v1_8(), "UKPN_LPN")
    bands = dict(zip(_MON, duos_band_labels(_MON, lpn), strict=True))
    assert bands[pd.Timestamp("2026-01-05 11:00")] == RED
    assert bands[pd.Timestamp("2026-01-05 13:30")] == RED
    assert bands[pd.Timestamp("2026-01-05 14:00")] == AMBER, "the midday red window closes at 14:00"
    assert bands[pd.Timestamp("2026-01-05 17:00")] == RED
    assert int(red_band_mask(_MON, lpn).sum()) == 5 * 12, "6 red hours × 5 weekdays"


def test_south_wales_weekend_amber_has_a_green_gap_in_the_middle() -> None:
    """NGED South Wales publishes 12:00–13:00 and 16:00–21:00 weekend amber, green between."""
    swales, _ = duos_windows_from_assumptions(_v1_8(), "NGED_SWALES")
    bands = dict(zip(_SAT, duos_band_labels(_SAT, swales), strict=True))
    assert bands[pd.Timestamp("2026-01-10 12:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-10 13:00")] == GREEN, (
        "the gap is real, not a transcription hole"
    )
    assert bands[pd.Timestamp("2026-01-10 15:30")] == GREEN
    assert bands[pd.Timestamp("2026-01-10 16:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-10 21:00")] == GREEN


# --------------------------------------------------------------------------------------------
# 2. Bank holidays: red, because they are weekdays — and the treatment is read, not assumed.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("region", "red_hour"),
    [("SSEN_SEPD", "17:00"), ("NGED_EM", "17:00"), ("NGED_SW", "18:00"), ("UKPN_LPN", "12:00")],
)
@pytest.mark.parametrize(
    "holiday", ["2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31"]
)
def test_bank_holidays_are_red_not_green(region: str, red_hour: str, holiday: str) -> None:
    """Every statement bands its weekday row "Monday to Friday (Including Bank Holidays)".

    GB bank holidays fall Monday–Friday, so `dayofweek < 5` already gives them the weekday
    windows and the red band applies. Treating them as green is the register's "common modelling
    error"; this pins that the error is not present for any area, on real 2026 holiday dates
    (New Year, Good Friday, Easter Monday, both May holidays, the August holiday).
    """
    windows, _ = duos_windows_from_assumptions(_v1_8(), region)
    ts = pd.date_range(f"{holiday} 00:00", periods=48, freq="30min")
    assert ts.dayofweek[0] < 5, (
        "GB bank holidays fall on weekdays — that is why no calendar is needed"
    )
    bands = dict(zip(ts, duos_band_labels(ts, windows), strict=True))
    assert bands[pd.Timestamp(f"{holiday} {red_hour}")] == RED
    assert red_band_mask(ts, windows).any(), "a bank holiday must keep its red band"


def test_a_bank_holiday_treatment_this_module_cannot_honour_falls_back_loudly() -> None:
    """The treatment is per-DNO CONTENT, not a hard-coded choice in either direction.

    No GB bank-holiday DATE list is sourced anywhere in the release, and this module will not
    invent one (Scottish dates differ from England & Wales, and SPD/SHEPD are both in the map). So
    an area publishing anything other than the weekday row is refused loudly rather than being
    quietly banded as an ordinary weekday — which would be the same modelling error mirrored.
    """
    for declared in ("green", "amber", None):
        lib = _lib_with_areas({"TEST": _area(bank_holiday_band=declared)})
        windows, note = duos_windows_from_assumptions(lib, "TEST")
        assert windows == DEFAULT_DUOS_WINDOWS, "never half-apply an area we cannot band correctly"
        assert note is not None and "bank_holiday_band" in note
        assert "will not invent one" in note


# --------------------------------------------------------------------------------------------
# 3. Fallbacks are loud, and a region is never guessed.
# --------------------------------------------------------------------------------------------


def test_an_unmapped_region_never_guesses_a_neighbouring_area() -> None:
    """`connection.dno_region` is a free string with no enum, so only an exact identity is safe.

    "NGED" is a prefix of four real keys. Resolving it to whichever sorted first would price the
    site against a neighbour's peak — an invented window wearing a sourced window's provenance.
    """
    for region in ("NGED", "SSEN", "Southern", "UKPN_XYZ", "", "  "):
        windows, note = duos_windows_from_assumptions(_v1_8(), region)
        assert windows == DEFAULT_DUOS_WINDOWS
        assert windows.dno_region is None
        assert note is not None and "matches no key" in note
        assert "NEVER resolved to a neighbouring area" in note
        assert "INDICATIVE, not sourced" in note


def test_a_site_with_no_dno_region_recorded_is_loud() -> None:
    """An unrecorded region is a mispriced floor stream, so it cannot be silent."""
    windows, note = duos_windows_from_assumptions(_v1_8(), None)
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is not None and "records no connection.dno_region" in note
    assert "INDICATIVE, not sourced" in note


def test_a_null_area_in_a_real_release_falls_back_loudly() -> None:
    """gb_v1_7 carries ENWL as null — "not transcribed" is unknown, not "the national default"."""
    windows, note = duos_windows_from_assumptions(load_assumptions("gb_v1_7"), "ENWL")
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is not None and "carried as null" in note
    # And the same area IS wired in gb_v1_8 — proof the loud path is about content, not code.
    later, later_note = duos_windows_from_assumptions(_v1_8(), "ENWL")
    assert later.dno_region == "ENWL" and later_note is not None


def test_a_release_without_the_key_at_all_falls_back_loudly_when_a_region_is_given() -> None:
    """gb_v0_1 predates `band_windows_by_dno`; asking for an area must say so, not pretend."""
    windows, note = duos_windows_from_assumptions(load_assumptions("gb_v0_1"), "SSEN_SEPD")
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is not None and "carries no duos.band_windows_by_dno" in note


@pytest.mark.parametrize(
    ("broken", "expected_phrase"),
    [
        ({"weekday_windows": {"red": [{"start": "16:30", "end": "19:30"}]}}, "do not cover"),
        ({"weekday_windows": "16:30-19:30"}, "as mappings"),
        ({"weekend_windows": None}, "as mappings"),
        ({"weekday_windows": {"red": [{"start": "19:30", "end": "16:30"}]}}, "malformed"),
        ({"weekday_windows": {"red": [{"start": "25:00", "end": "26:00"}]}}, "malformed"),
        ({"weekday_windows": {"red": "16:30-19:30"}}, "malformed"),
        ({"timezone": "UTC"}, "timezone"),
        ({"red_months": "winter"}, "red_months"),
        ({"red_months": [0, 13]}, "red_months"),
    ],
)
def test_a_partially_filled_or_malformed_area_fails_loudly_rather_than_half_applying(
    broken: dict[str, object], expected_phrase: str
) -> None:
    """A half-applied calendar prices real charged half-hours on a window nobody published.

    The coverage check is the partial-area guard: a published statement partitions the whole day
    across its bands, so a GAP means the area was only partly transcribed — and an untranscribed
    hour would silently take this module's green default instead of the area's own band.
    """
    lib = _lib_with_areas({"TEST": _area(**broken)})
    windows, note = duos_windows_from_assumptions(lib, "TEST")
    assert windows == DEFAULT_DUOS_WINDOWS, "partial data must never be partly applied"
    assert note is not None and expected_phrase in note
    assert "INDICATIVE, not sourced" in note


def test_a_non_mapping_by_dno_value_falls_back_loudly() -> None:
    for value in ([], "SSEN_SEPD", 42, {}):
        windows, note = duos_windows_from_assumptions(_lib_with_areas(value), "SSEN_SEPD")
        assert windows == DEFAULT_DUOS_WINDOWS
        assert note is not None and "INDICATIVE, not sourced" in note


def test_a_fallback_note_names_the_areas_the_release_does_carry() -> None:
    """So a reader can tell "we have no data for you" from "your region string is wrong"."""
    _windows, note = duos_windows_from_assumptions(_v1_8(), "NGED")
    assert note is not None and "SSEN_SEPD" in note and "UKPN_LPN" in note


# --------------------------------------------------------------------------------------------
# 4. Region-string normalisation: forgiving about form, strict about identity.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written", ["SSEN_SEPD", "ssen_sepd", "SSEN-SEPD", "ssen sepd", " SSEN_SEPD ", "SsenSepd"]
)
def test_region_strings_match_on_identity_modulo_case_and_separators(written: str) -> None:
    windows, _note = duos_windows_from_assumptions(_v1_8(), written)
    assert windows.dno_region == "SSEN_SEPD"
    assert windows.weekday_red_min == _minutes(("16:30-19:30",))


# --------------------------------------------------------------------------------------------
# 5. The legacy contract is untouched (no golden may move from this change alone).
# --------------------------------------------------------------------------------------------


def test_omitting_the_region_is_byte_identical_and_silent() -> None:
    """The one-argument call is the pre-existing contract: documented fallback, no noise.

    This is what keeps every current golden safe: until a caller passes a region, banding is
    exactly what it was, including the legacy national `band_windows_weekday` override.
    """
    for release in ("gb_v0_1", "gb_v1_7", "gb_v1_8"):
        windows, note = duos_windows_from_assumptions(load_assumptions(release))
        assert windows == DEFAULT_DUOS_WINDOWS
        assert note is None, f"{release}: a one-argument call must stay silent"


def test_a_resolved_area_takes_precedence_over_the_national_override() -> None:
    """One national override cannot be right for 14 areas, so the area's own statement wins."""
    lib = copy.deepcopy(load_assumptions("gb_v1_8"))
    lib.duos["band_windows_weekday"] = _assumption({"red": [[3, 4]], "amber": [[4, 5]]})
    windows, note = duos_windows_from_assumptions(lib, "SSEN_SEPD")
    assert windows.weekday_red_min == _minutes(("16:30-19:30",))
    assert note is not None and "band_windows_weekday" not in note
    # …but with no resolvable area, the national override still applies, and both notes surface.
    fallback, fallback_note = duos_windows_from_assumptions(lib, "NGED")
    assert fallback == DuosWindows(red=((3, 4),), amber=((4, 5),))
    assert fallback_note is not None
    assert "matches no key" in fallback_note and "band_windows_weekday" in fallback_note


def test_the_default_windows_still_mean_what_they_always_meant() -> None:
    """The hour-pair fields and the minute fields must never disagree (one normaliser)."""
    bands = dict(zip(_MON, duos_band_labels(_MON, DEFAULT_DUOS_WINDOWS), strict=True))
    assert bands[pd.Timestamp("2026-01-05 16:00")] == RED
    assert bands[pd.Timestamp("2026-01-05 18:30")] == RED
    assert bands[pd.Timestamp("2026-01-05 19:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-05 10:00")] == AMBER
    assert bands[pd.Timestamp("2026-01-05 02:00")] == GREEN
    assert set(duos_band_labels(_SAT)) == {GREEN}, "the default weekend is green throughout"


# --------------------------------------------------------------------------------------------
# 6. Seasonal red bands, and the EHV super-red band that must NOT leak into CDCM banding.
# --------------------------------------------------------------------------------------------


def test_a_seasonal_red_band_applies_only_in_its_own_months() -> None:
    """`red_months` is supported as content: red in the listed months, the area's own band outside.

    Out of season the red hours must be covered by the area's OWN amber/green windows — this
    synthetic area covers the whole day with amber+green, so nothing is derived or invented.
    """
    lib = _lib_with_areas(
        {
            "WINTERY": _area(
                red_months=[11, 12, 1, 2],
                weekday_windows={
                    "red": [{"start": "16:30", "end": "19:30"}],
                    "amber": [{"start": "07:00", "end": "22:00"}],
                    "green": [
                        {"start": "00:00", "end": "07:00"},
                        {"start": "22:00", "end": "24:00"},
                    ],
                },
            )
        }
    )
    windows, note = duos_windows_from_assumptions(lib, "WINTERY")
    assert windows.red_months == (1, 2, 11, 12)
    assert note is not None and "months [1, 2, 11, 12]" in note
    january = pd.date_range("2026-01-05 16:30", periods=2, freq="30min")
    june = pd.date_range("2026-06-01 16:30", periods=2, freq="30min")
    assert list(duos_band_labels(january, windows)) == [RED, RED]
    assert list(duos_band_labels(june, windows)) == [AMBER, AMBER], (
        "out of season, the area's amber"
    )
    assert red_band_mask(june, windows).sum() == 0


def test_a_seasonal_weekend_red_band_needs_its_own_off_season_coverage() -> None:
    """The seasonal guard is per DAY TYPE — a weekend red band can be seasonal on its own.

    Without the weekend arm of the check, this area's weekend red hours would fall to the module's
    green base out of season: the same "an untranscribed hour takes the code's default" defect the
    fallback message names, reappearing through the season dimension instead of the window one.
    """
    lib = _lib_with_areas(
        {
            "WEEKEND_SEASONAL": _area(
                red_months=[11, 12, 1, 2],
                # Weekday side is fully covered out of season, so only the WEEKEND can trip it.
                weekday_windows={
                    "red": [{"start": "16:30", "end": "19:30"}],
                    "amber": [{"start": "07:00", "end": "22:00"}],
                    "green": [
                        {"start": "00:00", "end": "07:00"},
                        {"start": "22:00", "end": "24:00"},
                    ],
                },
                weekend_windows={
                    "red": [{"start": "17:00", "end": "19:00"}],
                    "amber": [
                        {"start": "09:30", "end": "17:00"},
                        {"start": "19:00", "end": "21:30"},
                    ],
                    "green": [
                        {"start": "00:00", "end": "09:30"},
                        {"start": "21:30", "end": "24:00"},
                    ],
                },
            )
        }
    )
    windows, note = duos_windows_from_assumptions(lib, "WEEKEND_SEASONAL")
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is not None and "weekend amber and green windows do not cover" in note
    assert "OUT of season is not stated and cannot be derived" in note


def test_a_seasonal_area_whose_off_season_band_is_unstated_falls_back_loudly() -> None:
    """Deriving the out-of-season band would invent a band on real charged half-hours."""
    lib = _lib_with_areas(
        {"TEST": _area(red_months=[11, 12, 1, 2])}
    )  # amber/green leave a red hole
    windows, note = duos_windows_from_assumptions(lib, "TEST")
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is not None and "OUT of season is not stated and cannot be derived" in note


def test_the_ehv_super_red_band_does_not_touch_cdcm_banding() -> None:
    """`edcm_super_red` is an EHV/EDCM band; these sites are CDCM LV/HV, so it must not apply.

    Its absence on some areas means "not transcribed", not "none" (release note), so reading it
    here would be doubly wrong: wrong tariff basis, and unevenly available.
    """
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    november = pd.date_range("2026-11-02 00:00", periods=48, freq="30min")
    june = pd.date_range("2026-06-01 00:00", periods=48, freq="30min")
    # Same weekday shape in a super-red month and outside it: the CDCM red band is all-year.
    assert np.array_equal(red_band_mask(november, sepd), red_band_mask(june, sepd))
    assert int(red_band_mask(november, sepd).sum()) == 6


# --------------------------------------------------------------------------------------------
# 7. The GB clock, and the absence-vs-zero distinction (D45) under per-area windows.
# --------------------------------------------------------------------------------------------


def test_a_utc_labelled_summer_half_hour_lands_in_the_areas_bst_red_band() -> None:
    """Published windows are Europe/London clock, so a UTC index is converted before banding."""
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    utc = pd.date_range("2026-07-01 14:30", periods=6, freq="30min", tz="UTC")
    labels = dict(zip(utc.tz_convert("Europe/London"), duos_band_labels(utc, sepd), strict=True))
    # 15:30 UTC = 16:30 BST = the first red half-hour; 15:00 UTC = 16:00 BST = still amber.
    assert labels[pd.Timestamp("2026-07-01 16:00", tz="Europe/London")] == AMBER
    assert labels[pd.Timestamp("2026-07-01 16:30", tz="Europe/London")] == RED
    assert int(red_band_mask(utc, sepd).sum()) == 4, "15:30–17:30 UTC is 4 half-hours of BST red"


def test_absent_band_rates_are_still_reported_not_zeroed_under_per_area_windows() -> None:
    """D45 survives the rewiring: a null band rate is a MISSING INPUT, a 0.0 is a fact.

    Colnbrook and Tanhouse are SSEN_SEPD sites whose three band rates are ALL null and correctly
    so — they are 100% private-wire, so they genuinely pay no DUoS. Resolving their area's windows
    must not turn that null into a zero, nor a zero into an absence.
    """
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    _series, absent = duos_rate_series_with_absences(
        s.TariffStack(basis=SYNTHETIC_BASIS, provenance=SYNTHETIC_PROVENANCE), _MON, sepd
    )
    assert set(absent) == {RED, AMBER, GREEN}
    genuine_zero = s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        duos_red_rate=0.0,
        duos_amber_rate=0.0,
        duos_green_rate=0.0,
    )
    _zeros, none_absent = duos_rate_series_with_absences(genuine_zero, _MON, sepd)
    assert none_absent == (), "a declared 0.0 is a fact, never an absence"


def test_the_weekend_amber_half_hours_are_priced_at_the_amber_rate() -> None:
    """The point of wiring weekend bands: those half-hours now carry the site's amber rate, not 0.

    Ewelme/Knowl Hill/Harwell rates (SEPD LV site-specific, TCR band 4, 2026/27).
    """
    sepd, _ = duos_windows_from_assumptions(_v1_8(), "SSEN_SEPD")
    tariff = s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        duos_red_rate=0.05471,
        duos_amber_rate=0.00509,
        duos_green_rate=0.00028,
    )
    rates, absent = duos_rate_series_with_absences(tariff, _SAT, sepd)
    assert absent == ()
    by_ts = dict(zip(_SAT, rates, strict=True))
    assert by_ts[pd.Timestamp("2026-01-10 17:00")] == pytest.approx(0.00509)
    assert by_ts[pd.Timestamp("2026-01-10 03:00")] == pytest.approx(0.00028)
    generic, _ = duos_rate_series_with_absences(tariff, _SAT, DEFAULT_DUOS_WINDOWS)
    assert generic.max() == pytest.approx(0.00028), "the generic calendar charged green all weekend"
    assert rates.sum() > generic.sum(), "weekend amber is a COST the site pays, priced now"


def test_resolution_and_banding_are_deterministic() -> None:
    for region in ("SSEN_SEPD", "UKPN_LPN", "NGED"):
        first = duos_windows_from_assumptions(_v1_8(), region)
        second = duos_windows_from_assumptions(_v1_8(), region)
        assert first == second
        assert np.array_equal(duos_band_labels(_MON, first[0]), duos_band_labels(_MON, second[0]))


# --------------------------------------------------------------------------------------------
# 8. The live sites, and the reader layer the area's calendar has to survive as far as.
# --------------------------------------------------------------------------------------------

#: Every real site model, DISCOVERED rather than named.
#:
#: This section used to hard-code ``("ewelme", "knowl_hill", "harwell")`` and call `pytest.skip`
#: on the first name whose file was absent, INSIDE the loop, so a single rename skipped the whole
#: test and left every remaining site unchecked. That is not a hypothetical: the same construction
#: in `test_gate3_declared_ref_resolution.py` named `bishops_cleeve` first, the metered rebuild
#: split that supply in two and deleted the original file, and the per-name skip then fired on
#: iteration one, checked NOTHING, and reported "not installed on this host" on a host where it
#: was installed, all while green. A missing file is now an ASSERTION.
#:
#: The name list was also simply out of date by 2026-08-15: seven of the nine real sites are
#: SSEN_SEPD (Colnbrook, Ewelme, Ewelme invoice-anchored, Harwell, Knowl Hill, Knowl Hill
#: invoice-anchored, Tanhouse) and two are NGED_WM (both Bishops Cleeve supplies), so naming three
#: checked three of nine and asserted nothing whatever about NGED_WM.
REAL_SITES = Path(__file__).resolve().parents[1] / "examples" / "real" / "sites"
REAL_SITE_FILES: list[Path] = sorted(REAL_SITES.glob("*.json"))

#: The static floor under the discovery. ``checked == len(discovered)`` on its own passes
#: VACUOUSLY at ``0 == 0``, which is the same green-on-nothing the skip produced, so the count
#: must also clear a number written down in advance. ``examples/real/`` has been tracked since
#: 2026-08-11, so a missing site file is a deleted site, never an uninstalled host. Nine is what
#: the metered rebuild landed (five Grundon metered supplies, two invoice-anchored companions,
#: Colnbrook, Harwell) and is the same floor `test_real_sites_milp_runs.py` writes down. Raise it
#: when sites are added; lowering it is a deliberate act that belongs in the diff.
MINIMUM_REAL_SITES = 9

#: Distinct licence areas the live set has to span. Two is load-bearing, not decoration: NGED_WM
#: publishes red 16:00-19:00, which is BYTE IDENTICAL to `DEFAULT_DUOS_WINDOWS`, so on the two
#: Bishops Cleeve sites matching window minutes cannot distinguish "resolved this area's own
#: published bands" from "fell back silently to the national default". A set that happened to
#: contain SEPD sites only would never exercise that collision at all.
MINIMUM_LICENCE_AREAS = 2


def test_the_real_site_set_is_discovered_and_clears_its_floor() -> None:
    """The floor under the resolution test below: an empty or shrunken glob FAILS, never skips."""
    assert len(REAL_SITE_FILES) >= MINIMUM_REAL_SITES, (
        f"only {len(REAL_SITE_FILES)} site models under {REAL_SITES} (expected at least "
        f"{MINIMUM_REAL_SITES}): {[p.stem for p in REAL_SITE_FILES]}. examples/real is TRACKED, "
        "so a missing site file is a deleted site, not an uninstalled host, and it must fail here "
        "rather than skip the licence-area checks."
    )


def test_every_real_sites_recorded_region_resolves_to_its_own_published_calendar() -> None:
    """Every live site's `connection.dno_region` string resolves to that area's OWN windows.

    Checked for all nine, not three, and the pass condition is the per-area pin at the top of this
    module rather than "SEPD". A region that resolved to nothing would be banded on the generic
    national window, which is the mispriced floor stream LIT-F-05 files.
    """
    # Each record is quarantined customer data (WP-GRC-05) and is read only through the guard.
    # The whole set is released before any site is read, so the test checks all of it or skips
    # as a whole, naming every blocker; it can never check a subset and pass.
    sites = dpa_released_sites(REAL_SITE_FILES, run_cross_checks=False)
    released = list(zip(REAL_SITE_FILES, sites, strict=True))
    checked = 0
    areas: set[str] = set()
    for path, site in released:
        region = site.connection.dno_region
        assert region in PUBLISHED_2026_27, (
            f"{path.name} records dno_region {region!r}, which is not one of the 14 pinned GB "
            "licence areas. An unrecognised region string is NEVER resolved to a neighbouring "
            "area, so this site would silently be banded on the generic national window."
        )
        windows, note = duos_windows_from_assumptions(_v1_8(), region)
        expected = PUBLISHED_2026_27[region]
        # A fallback returns `DEFAULT_DUOS_WINDOWS`, whose `weekday_red_min` and `dno_region` are
        # both None. Both are asserted BEFORE the minutes, because NGED_WM's published red window
        # is identical to the generic default's: on Bishops Cleeve, matching minutes alone would
        # be satisfied by exactly the silent fallback this file exists to forbid.
        assert windows.weekday_red_min is not None, (
            f"{path.name} ({region}): fell back to the generic national windows"
        )
        assert windows.dno_region == region, (
            f"{path.name} ({region}): resolved to {windows.dno_region!r}"
        )
        assert windows.weekday_red_min == _minutes(expected["red"]), path.name
        assert windows.weekend_amber_min == _minutes(expected["weekend_amber"]), path.name
        assert note is not None and f"band_windows_by_dno[{region}]" in note, path.name
        checked += 1
        areas.add(region)

    assert checked == len(REAL_SITE_FILES), (
        f"only {checked} of {len(REAL_SITE_FILES)} discovered sites were checked; a site whose "
        "region could not be read is a broken fixture, not a skip condition"
    )
    assert checked >= MINIMUM_REAL_SITES, (
        f"checked {checked} sites against a floor of {MINIMUM_REAL_SITES}: 'checked == discovered' "
        "is satisfied by an empty directory, so the count itself has to clear the floor"
    )
    assert len(areas) >= MINIMUM_LICENCE_AREAS, (
        f"the live set spans only {sorted(areas)}; at least {MINIMUM_LICENCE_AREAS} licence areas "
        "are needed for this test to exercise an area (NGED_WM) whose published red window is "
        "indistinguishable from the generic fallback on minutes alone"
    )


def test_the_areas_own_calendar_reaches_the_warnings_a_reader_reads(tmp_path: Path) -> None:
    """The reader layer: a solved run SAYS which licence area priced its red band.

    Every other test in this module asserts at `tariff_calendar`'s own layer, and that layer is
    reached from production at ONE place: `optimisation/milp_solver.py:1128` passes
    ``site.connection.dno_region`` into `duos_windows_from_assumptions` and appends the returned
    note to the run's warnings. Delete that call, or quietly drop its second argument, and every
    site reverts to the single national window while all fourteen per-area pins above stay green,
    because none of them runs the solver. The nearest existing solver-level coverage
    (`test_tariff_calendar_windows.py::test_solver_threads_the_override_end_to_end`) asserts the
    LEGACY national-override note, which the one-argument call still emits, so dropping the region
    argument passes there too.

    So this asserts the per-area provenance where an underwriter actually meets it: in the
    warnings carried on the dispatch result.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    from gridq.dispatch import DispatchProblem
    from gridq.optimisation.milp_solver import MilpDispatchSolver

    hh = tmp_path / "load.csv"
    rows, _ = write_hh_csv(hh)
    base = multi_rate_site(hh, rows=rows)
    site = base.model_copy(
        update={"connection": base.connection.model_copy(update={"dno_region": "SSEN_SEPD"})}
    )
    result = MilpDispatchSolver().solve(
        DispatchProblem(
            # `load_assumptions` rather than `_v1_8()` only because that helper is annotated
            # ``-> object`` for the malformed-library tests above; identical release, typed.
            site_model=site,
            assumptions=load_assumptions("gb_v1_8"),
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
        )
    )
    assert result.solver_status.value == "optimal"
    sourced = [w for w in result.warnings if "band_windows_by_dno[SSEN_SEPD]" in w]
    assert sourced, (
        "the run's warnings never name the licence area that priced its red band, so the site was "
        "banded on the generic national window (or the note was dropped): "
        f"{list(result.warnings)}"
    )
    assert "16:30" in sourced[0] and "19:30" in sourced[0], (
        "the note reached the reader but does not carry SEPD's published 16:30 to 19:30 red "
        f"window: {sourced[0]}"
    )
