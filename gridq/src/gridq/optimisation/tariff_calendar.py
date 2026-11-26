"""GB tariff band → half-hour time-window calendar (the one piece of GB *content* M3 adds).

The half-hourly dispatch MILP needs a **per-half-hour import price** to optimise against, but a
``SiteModel.tariff_stack`` records its prices keyed by *band label* (``import_rates`` like
``{"day": 0.30, "night": 0.10}`` and ``duos_red_rate``/``duos_amber_rate``/``duos_green_rate``),
not by clock time. This module supplies the missing **time-window calendar**: which half-hour of
the week falls in which DUoS / time-of-use band.

It is **content, never a price** — exactly the GB-specific bit the architecture allows to live
outside the core (like the assumption library). Every £/kWh comes from the site's own tariff; this
module only says *when* each band applies. The windows are a single, documented, *indicative*
post-TCR GB default; a future assumption version can override them. Unrecognised band labels are
**not guessed** — they degrade to "no time-mapped arbitrage" with a warning (never an invented
window or price). This is **not** a ``market == "GB"`` core branch: the core consumes the optimiser
generically; this calendar is GB content the GB engine reads, the same way ``gb_streams`` is.

Convention: each half-hour is labelled by its **period-start** local clock time. A naive index is
taken as the local clock already; a tz-aware index (UTC-labelled or fixed-offset) is converted to
**Europe/London** before banding — the windows are GB clock content, so the GB clock belongs here.

**Per-licence-area windows (LIT-F-05).** The generic fallback below follows the common post-TCR
pattern — red weekdays 16:00–19:00, amber weekdays 07:00–16:00 and 19:00–23:00, green the rest
(overnight and all weekend) — but *that pattern is not what most DNOs publish*, and for a
behind-the-meter site whose contractible floor is only tariff arbitrage plus DUoS red-band
avoidance, **the entire £ of the avoidance stream is set by when the band falls**. Using one
national window for 14 licence areas silently misprices a priced-floor stream, which is the defect
LIT-F-05 files. So a release may carry each area's own sourced windows in
``duos.band_windows_by_dno``, keyed on ``SiteModel.connection.dno_region``, and
:func:`duos_windows_from_assumptions` reads them when the caller supplies the site's region.

The published variation is material and cannot be proxied: red is 16:30–19:30 in SEPD and both SPEN
areas, 16:00–19:30 in both NPg areas, 17:00–19:00 in NGED South West, 17:00–19:30 in NGED South
Wales, and London has **two** red windows (11:00–14:00 and 16:00–19:00); seven areas charge **amber
at weekends** where the generic fallback says green. Two consequences for this module's shape:
windows need **half-hour, not whole-hour, resolution** (16:30, 19:30, 09:30, 07:30, 22:30 all
appear), and **weekends are a first-class day type**, not a green afterthought.

**Bank holidays are RED, and that is a no-op rather than a feature.** Every sourced 2026/27
statement prints its weekday row as "Monday to Friday (Including Bank Holidays)", so a bank holiday
is banded as the ordinary weekday it is. GB bank holidays always fall Monday–Friday, so
``dayofweek < 5`` already gives them the weekday windows and **no holiday-date calendar is needed**
— treating them as green is "a common modelling error" (the register's phrase) that this module has
never made and must never start making. The treatment is nevertheless read **per area** from the
release rather than assumed: an area declaring anything other than ``red`` falls back loudly,
because no GB holiday-date list is sourced anywhere in the release and this module will not invent
one (Scottish dates differ from England & Wales, and both SPEN and SHEPD are in the map).

**Fallbacks are loud, never silent.** An absent key, an untranscribed area, an unmapped region
string, a partially-filled area or a non-``Europe/London`` clock all return the documented generic
windows *with a note naming what was missing*. A silent fallback to one national window is
precisely what LIT-F-05 objects to, so the note is the point: the solver appends it to the run's
warnings, where a mispriced area is visible instead of merely wrong. ``connection.dno_region`` is a
free ``str | None`` with no enum, so an unrecognised value is **never** resolved to a neighbouring
area — it falls back and says so.

This is **content, never a price** — exactly the GB-specific bit the architecture allows outside
the core. Every £/kWh comes from the site's own tariff; this module only says *when* each band
applies, and unrecognised band labels are **not guessed** (they degrade to "no time-mapped
arbitrage" with a warning, never an invented window or price).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Final

import numpy as np
import pandas as pd

from gridq.schemas import (
    BandWindows,
    CdcmTariff,
    DerivedAssumption,
    TariffStack,
    WindowedLevy,
    parse_clock_minute,
)
from gridq.schemas.derived import ceiling_for_key
from gridq.schemas.tariff import OWN_COMPONENT_PROVENANCE
from gridq.tariffs.hv_tariff_model import band_windows_mask, network_unit_rate_series

# DUoS band labels (post-TCR; red is the distribution peak the asset discharges across).
RED = "red"
AMBER = "amber"
GREEN = "green"


@dataclass(frozen=True)
class DuosWindows:
    """The DUoS band calendar: which half-hours are red/amber, per day type; green = the rest.

    Two generations of field live here, and the split is deliberate rather than untidy.

    ``red``/``amber`` are the original **weekday whole-hour** windows (period-start hours, half-open
    ``[start, end)``), and they remain the fields the generic fallback and the legacy
    ``duos.band_windows_weekday`` release override populate. They are hours because that override's
    schema is hour pairs; keeping them first and unchanged keeps every existing construction,
    equality comparison and golden byte-identical.

    The ``*_min`` fields carry the **per-licence-area** windows as *minutes of day* (half-open,
    0–1440), because real published bands land on half-hours (16:30, 19:30, 09:30) that hour pairs
    cannot express. When ``weekday_red_min`` is ``None`` the weekday windows are derived from the
    hour fields (×60), so the two generations never disagree: :func:`_minute_windows` is the single
    normaliser every banding function goes through.

    Weekends are their own day type. ``weekend_*_min`` default to ``()`` — the legacy all-green
    weekend — but seven GB licence areas charge weekend amber, so an area's own weekend windows are
    honoured when the release supplies them.

    ``red_months`` is ``None`` for an all-year red band (every currently sourced area) or the months
    a seasonal red band applies to. ``dno_region``/``licence_area`` are provenance only: they never
    change a band, they let a caller state which area's calendar priced the result.
    """

    red: tuple[tuple[int, int], ...] = ((16, 19),)
    amber: tuple[tuple[int, int], ...] = ((7, 16), (19, 23))
    weekday_red_min: tuple[tuple[int, int], ...] | None = None
    weekday_amber_min: tuple[tuple[int, int], ...] | None = None
    weekend_red_min: tuple[tuple[int, int], ...] = ()
    weekend_amber_min: tuple[tuple[int, int], ...] = ()
    red_months: tuple[int, ...] | None = None
    dno_region: str | None = None
    licence_area: str | None = None


DEFAULT_DUOS_WINDOWS = DuosWindows()

# Day/night time-of-use windows (period-start local clock).
_NIGHT_START, _NIGHT_END = 23, 7  # 23:00–07:00 wraps midnight

_MINUTES_PER_DAY: Final = 24 * 60
# The only clock the published windows are quoted on. An area declaring anything else is not
# banded against this module's Europe/London conversion — it falls back loudly.
_PUBLISHED_TZ: Final = "Europe/London"
# The only bank-holiday treatment this module can apply: "banded as the weekday it is". See the
# module docstring — no GB holiday-date list is sourced in the release, so no other value is
# actionable and an area declaring one falls back loudly rather than being quietly mis-banded.
_WEEKDAY_BANK_HOLIDAY_BAND: Final = RED


class _Unset:
    """Type of the 'no ``dno_region`` argument was supplied at all' sentinel."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<unset>"


# Distinguishes a LEGACY one-argument call (no region concept — stay silent, the documented
# fallback is the intended behaviour) from an explicit ``dno_region=None`` (a site whose region was
# never recorded — that IS a mispricing and must be loud). Without the sentinel one of those two
# requirements has to give.
_UNSET: Final = _Unset()


def _minute_of_day(raw: object) -> int | None:
    """Parse a published ``"HH:MM"`` clock time to minutes after midnight (``"24:00"`` → 1440).

    Delegates to `gridq.schemas.parse_clock_minute` — the ONE parser for this convention, shared
    with `TariffStack.windowed_levies`' `ClockWindow`, so a window carried on a site's tariff and a
    window carried in a release's ``duos.band_windows_by_dno`` can never disagree about what
    ``"16:30"`` means. Behaviour is unchanged: the same lenient ``None`` on anything malformed.
    """
    return parse_clock_minute(raw)


def _minute_windows_from_release(raw: object) -> tuple[tuple[int, int], ...] | None:
    """Parse one band's ``[{start: "16:30", end: "19:30"}, …]`` list into minute pairs.

    Returns ``None`` for anything malformed — a partly-parsed band is never returned, because a
    half-applied window prices real half-hours against a calendar nobody published.
    """
    if raw is None:
        return ()  # the band is legitimately not charged on this day type
    if not isinstance(raw, list):
        return None
    out: list[tuple[int, int]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        start, end = _minute_of_day(item.get("start")), _minute_of_day(item.get("end"))
        if start is None or end is None or start >= end:
            return None
        out.append((start, end))
    return tuple(out)


def _covers_whole_day(*window_groups: tuple[tuple[int, int], ...]) -> bool:
    """True when the union of ``window_groups`` covers every minute of ``[00:00, 24:00)``.

    The partial-area guard. A published statement partitions the whole day across its bands, so a
    gap means the area was only partly transcribed — and an untranscribed hour would silently take
    the *code's* green default rather than the area's own band, which is the LIT-F-05 defect in
    miniature. Overlaps are benign (red beats amber beats green at banding time); gaps are not.
    """
    covered = np.zeros(_MINUTES_PER_DAY, dtype=bool)
    for windows in window_groups:
        for start, end in windows:
            covered[start : min(end, _MINUTES_PER_DAY)] = True
    return bool(covered.all())


def _fmt(windows: tuple[tuple[int, int], ...]) -> str:
    """Render minute windows back to published clock form, for the note the caller surfaces."""
    return (
        ", ".join(f"{s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}" for s, e in windows)
        or "none"
    )


def _red_months_from_release(raw: object) -> tuple[int, ...] | None | _Unset:
    """``"all"`` → ``None`` (all year); a month list → that tuple; anything else → ``_UNSET``."""
    if isinstance(raw, str) and raw.strip().lower() == "all":
        return None
    if isinstance(raw, list) and raw:
        if all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in raw):
            return tuple(sorted({int(m) for m in raw}))
    return _UNSET


def _normalise_region(value: str) -> str:
    """Case/separator-insensitive form, so ``"ssen sepd"``/``"SSEN-SEPD"`` reach ``SSEN_SEPD``."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _resolve_area_key(region: str, available: dict[str, object]) -> str | None:
    """Map a free-text ``dno_region`` to a release key: exact, else normalised. Never fuzzy.

    ``connection.dno_region`` is a free ``str | None`` with no enum, so the only safe mapping is an
    exact identity (modulo case and separators). A partial or prefix match would resolve ``"NGED"``
    to whichever of four NGED areas sorted first and price the site against a neighbour's peak —
    an invented window wearing a sourced window's provenance. So there is deliberately no fuzzy
    branch here: an unrecognised string returns ``None`` and the caller falls back loudly.
    """
    if region in available:
        return region
    target = _normalise_region(region)
    if not target:
        return None
    return next((key for key in sorted(available) if _normalise_region(key) == target), None)


def duos_windows_for_dno(
    assumptions: object, dno_region: str | None
) -> tuple[DuosWindows, str | None]:
    """Resolve one licence area's own sourced band windows from ``duos.band_windows_by_dno``.

    Returns ``(windows, note)``. **Every path that does not return the area's own windows returns a
    note**, because the fallback is the mispricing LIT-F-05 files: red-band avoidance is one of only
    two contractible floor streams for a behind-the-meter site, its whole £ is set by when the band
    falls, and a site quietly banded on the generic national window is wrong in a way no downstream
    figure reveals. The loud paths are: no region recorded, no such key in the release, an area
    carried as ``null`` (transcribed for other areas but not this one), a malformed or partially
    filled area, a clock other than Europe/London, an unusable seasonal restriction, and a
    bank-holiday treatment this module cannot honour.

    A successfully resolved area also returns a note — it states which statement priced the site,
    which is the provenance an underwriting pack needs, not a warning about a defect.
    """
    if dno_region is None:
        return DEFAULT_DUOS_WINDOWS, (
            "DUoS band windows: the site records no connection.dno_region, so the generic "
            "indicative national windows were used (red weekdays 16:00–19:00, green weekends) "
            "instead of its licence area's own published bands. Published GB red windows range "
            "16:00–19:30 across areas and seven areas charge weekend amber, so the red-band "
            "avoidance £ on this site is INDICATIVE, not sourced (LIT-F-05)."
        )
    try:
        entry = assumptions.get("duos", "band_windows_by_dno")  # type: ignore[attr-defined]
    except (KeyError, AttributeError, TypeError):
        return DEFAULT_DUOS_WINDOWS, (
            f"DUoS band windows: this assumption release carries no duos.band_windows_by_dno, so "
            f"the site's licence area '{dno_region}' could not be priced on its own published "
            f"bands — the generic indicative national windows were used and the red-band "
            f"avoidance £ is INDICATIVE, not sourced (LIT-F-05)."
        )
    by_area = entry.value if hasattr(entry, "value") else None
    status = getattr(getattr(entry, "status", None), "value", "unknown")
    if not isinstance(by_area, dict) or not by_area:
        return DEFAULT_DUOS_WINDOWS, (
            f"DUoS band windows: duos.band_windows_by_dno is present but not a per-area mapping, "
            f"so licence area '{dno_region}' fell back to the generic indicative national windows "
            f"— red-band avoidance £ is INDICATIVE, not sourced (LIT-F-05)."
        )

    def _fallback(reason: str) -> tuple[DuosWindows, str | None]:
        return DEFAULT_DUOS_WINDOWS, (
            f"DUoS band windows: {reason} The generic indicative national windows were used (red "
            f"weekdays 16:00–19:00, green weekends), so this site's red-band avoidance £ is "
            f"INDICATIVE, not sourced (LIT-F-05). Areas the release does carry: "
            f"{sorted(k for k, v in by_area.items() if v is not None)}."
        )

    key = _resolve_area_key(dno_region, by_area)
    if key is None:
        return _fallback(
            f"the site's connection.dno_region '{dno_region}' matches no key in "
            f"duos.band_windows_by_dno, and a region string is NEVER resolved to a neighbouring "
            f"area (connection.dno_region is a free string with no enum, so only an exact "
            f"identity is safe)."
        )
    area = by_area[key]
    if area is None:
        return _fallback(
            f"licence area '{key}' is carried as null in duos.band_windows_by_dno — its windows "
            f"were not transcribed, which is 'unknown', not 'the national default'."
        )
    if not isinstance(area, dict):
        return _fallback(f"licence area '{key}' is not a mapping in duos.band_windows_by_dno.")

    timezone = area.get("timezone")
    if timezone is not None and timezone != _PUBLISHED_TZ:
        return _fallback(
            f"licence area '{key}' declares timezone '{timezone}', but this module bands on "
            f"{_PUBLISHED_TZ} clock time, so its windows cannot be applied without shifting every "
            f"band by the offset."
        )

    weekday_raw, weekend_raw = area.get("weekday_windows"), area.get("weekend_windows")
    if not isinstance(weekday_raw, dict) or not isinstance(weekend_raw, dict):
        return _fallback(
            f"licence area '{key}' does not carry both weekday_windows and weekend_windows as "
            f"mappings; weekends are a charged day type in seven GB areas, so an absent weekend "
            f"block is 'not transcribed', not 'all green'."
        )
    parsed: dict[str, tuple[tuple[int, int], ...]] = {}
    for day_type, raw in (("weekday", weekday_raw), ("weekend", weekend_raw)):
        for band in (RED, AMBER, GREEN):
            band_windows = _minute_windows_from_release(raw.get(band))
            if band_windows is None:
                return _fallback(
                    f"licence area '{key}' has a malformed {day_type} {band} window (expected a "
                    f"list of {{start: 'HH:MM', end: 'HH:MM'}} with start < end)."
                )
            parsed[f"{day_type}_{band}"] = band_windows

    red_months = _red_months_from_release(area.get("red_months"))
    if isinstance(red_months, _Unset):
        return _fallback(
            f"licence area '{key}' declares red_months={area.get('red_months')!r}, which is "
            f"neither 'all' nor a list of months 1–12, so the season the red band applies in is "
            f"unknown."
        )

    for day_type in ("weekday", "weekend"):
        if not _covers_whole_day(*(parsed[f"{day_type}_{b}"] for b in (RED, AMBER, GREEN))):
            return _fallback(
                f"licence area '{key}' has {day_type} windows that do not cover the whole day — "
                f"the area is only PARTLY transcribed, and an untranscribed hour would silently "
                f"take this module's green default instead of the area's own published band."
            )
    if red_months is not None:
        # A seasonal red band means the red hours are banded by something else out of season. Only
        # the statement knows what; deriving it would invent a band on real charged half-hours. So
        # the area's own amber+green must already cover the whole day WITHOUT its red windows —
        # checked per day type, because an area can restrict red seasonally on either or both.
        for day_type in ("weekday", "weekend"):
            if not parsed[f"{day_type}_{RED}"]:
                continue  # no red on this day type, so nothing is left unbanded out of season
            if not _covers_whole_day(parsed[f"{day_type}_{AMBER}"], parsed[f"{day_type}_{GREEN}"]):
                return _fallback(
                    f"licence area '{key}' restricts red to months {list(red_months)}, but its "
                    f"{day_type} amber and green windows do not cover the whole day on their own, "
                    f"so the band applying in the {day_type} red hours OUT of season is not stated "
                    f"and cannot be derived."
                )

    bank_holiday_band = area.get("bank_holiday_band")
    if bank_holiday_band != _WEEKDAY_BANK_HOLIDAY_BAND:
        return _fallback(
            f"licence area '{key}' declares bank_holiday_band={bank_holiday_band!r}. Only "
            f"'{_WEEKDAY_BANK_HOLIDAY_BAND}' is actionable: it means the statement's "
            f"'Monday to Friday (Including Bank Holidays)' weekday row, which the weekday windows "
            f"already deliver. Any other treatment needs a sourced GB bank-holiday DATE list, the "
            f"release carries none, and this module will not invent one (Scottish dates differ "
            f"from England & Wales)."
        )

    windows = DuosWindows(
        weekday_red_min=parsed["weekday_red"],
        weekday_amber_min=parsed["weekday_amber"],
        weekend_red_min=parsed["weekend_red"],
        weekend_amber_min=parsed["weekend_amber"],
        red_months=red_months,
        dno_region=key,
        licence_area=area.get("licence_area")
        if isinstance(area.get("licence_area"), str)
        else None,
    )
    season = "all year" if red_months is None else f"months {list(red_months)}"
    return windows, (
        f"DUoS band windows sourced per licence area from duos.band_windows_by_dno[{key}] "
        f"({area.get('licence_area', 'area not named')}, charging year "
        f"{area.get('charging_year', 'not stated')}, assumption status '{status}'): weekday red "
        f"{_fmt(parsed['weekday_red'])} ({season}), weekday amber {_fmt(parsed['weekday_amber'])}; "
        f"weekend red {_fmt(parsed['weekend_red'])}, weekend amber "
        f"{_fmt(parsed['weekend_amber'])}. "
        f"Bank holidays are banded as weekdays (red applies), per the statement's "
        f"'Monday to Friday (Including Bank Holidays)' row. Times are {_PUBLISHED_TZ} clock."
    )


def duos_windows_from_assumptions(
    assumptions: object, dno_region: str | None | _Unset = _UNSET
) -> tuple[DuosWindows, str | None]:
    """Resolve the DUoS windows for a site: its licence area's own bands where they can be read.

    ``dno_region`` decides which of three contracts applies, and the three are deliberately
    distinct because a silent fallback is the defect being fixed:

    * **omitted** — the legacy contract, unchanged and byte-identical: the release's
      ``duos.band_windows_weekday`` national override when present, else the documented code
      fallback, **silently** (every release predating the key behaves exactly as before, so no
      golden moves and no caller is retrofitted).
    * **a region string** — the per-area contract: that area's own sourced windows from
      ``duos.band_windows_by_dno`` (see :func:`duos_windows_for_dno`), or a LOUD fallback naming
      what was missing. A resolved area takes precedence over the national
      ``band_windows_weekday`` override, which cannot be right for 14 different areas at once.
    * **``None``** — a site whose ``connection.dno_region`` was never recorded: the generic windows
      **with a loud note**, because that site's red-band avoidance £ is indicative, not sourced.

    Returns ``(windows, note)``; the caller surfaces ``note`` (the MILP appends it to the run's
    warnings, which is what makes a fallback visible rather than merely wrong).
    """
    if not isinstance(dno_region, _Unset):
        windows, note = duos_windows_for_dno(assumptions, dno_region)
        if windows.weekday_red_min is not None:
            return windows, note  # the area's own bands win over any national override
        legacy, legacy_note = _windows_from_weekday_override(assumptions)
        return legacy, " ".join(n for n in (note, legacy_note) if n) or None
    return _windows_from_weekday_override(assumptions)


def _windows_from_weekday_override(assumptions: object) -> tuple[DuosWindows, str | None]:
    """The legacy national contract: ``duos.band_windows_weekday``, else the code fallback.

    Reads ``duos.band_windows_weekday`` (a structured value: ``{red: [[16,19]], amber: [...]}``
    — hour pairs, half-open, 0–24, no midnight wrap; overnight = two windows). Returns
    ``(windows, note)``: an applied override notes the assumption's status loudly; a malformed
    value falls back with a warning (never guessed); an ABSENT key returns the documented code
    fallback silently (byte-identical behaviour for every release that predates the key).
    """
    try:
        a = assumptions.get("duos", "band_windows_weekday")  # type: ignore[attr-defined]
    except (KeyError, AttributeError, TypeError):
        return DEFAULT_DUOS_WINDOWS, None  # documented fallback — no key, no noise

    def _pairs(raw: object) -> tuple[tuple[int, int], ...] | None:
        if not isinstance(raw, list) or not raw:
            return None
        out: list[tuple[int, int]] = []
        for item in raw:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or not all(isinstance(h, int) and not isinstance(h, bool) for h in item)
                or not 0 <= item[0] < item[1] <= 24
            ):
                return None
            out.append((item[0], item[1]))
        return tuple(out)

    value = a.value if hasattr(a, "value") else None
    red = _pairs(value.get("red")) if isinstance(value, dict) else None
    amber = _pairs(value.get("amber")) if isinstance(value, dict) else None
    if red is None or amber is None:
        return DEFAULT_DUOS_WINDOWS, (
            "duos.band_windows_weekday is present but malformed (expected {red: [[start, end]…], "
            "amber: […]}, hours 0–24, start<end) — the documented indicative fallback windows "
            "were used instead (never guessed)."
        )
    status = getattr(getattr(a, "status", None), "value", "unknown")
    return DuosWindows(red=red, amber=amber), (
        f"DUoS band windows overridden by duos.band_windows_weekday (status '{status}') — "
        f"red {list(red)}, amber {list(amber)}; weekends green."
    )


def _in_windows(
    minute: np.ndarray, day_mask: np.ndarray, windows: tuple[tuple[int, int], ...]
) -> np.ndarray:
    """Boolean mask: half-hours on ``day_mask`` days whose period-start minute is in any window.

    A window whose start is after its end wraps midnight (``23:30-01:00``), read on the day the
    half-hour falls on, as `gridq.tariffs.hv_tariff_model.band_windows_mask` reads it. Before
    WP-UW-07 such a window matched nothing here, so a wrapping levy window charged nothing without
    saying so; no sourced DUoS or levy window in any release wraps, so no banded series moves.
    """
    mask = np.zeros(minute.shape, dtype=bool)
    for start, end in windows:
        if start <= end:
            mask |= (minute >= start) & (minute < end)
        else:
            mask |= (minute >= start) | (minute < end)
    return day_mask & mask


@lru_cache(maxsize=64)
def _minute_windows(
    windows: DuosWindows,
) -> tuple[
    tuple[tuple[int, int], ...],
    tuple[tuple[int, int], ...],
    tuple[tuple[int, int], ...],
    tuple[tuple[int, int], ...],
]:
    """Normalise any :class:`DuosWindows` to ``(wd_red, wd_amber, we_red, we_amber)`` in minutes.

    The single place the two field generations are reconciled, so no banding function has to know
    whether its windows came from the hour-pair fallback/override or from a per-area release entry.
    Cached because the same frozen windows object is re-banded for every call in a solve.
    """
    hours_to_min = lambda pairs: tuple((s * 60, e * 60) for s, e in pairs)  # noqa: E731
    weekday_red = (
        windows.weekday_red_min
        if windows.weekday_red_min is not None
        else hours_to_min(windows.red)
    )
    weekday_amber = (
        windows.weekday_amber_min
        if windows.weekday_amber_min is not None
        else hours_to_min(windows.amber)
    )
    return weekday_red, weekday_amber, windows.weekend_red_min, windows.weekend_amber_min


# Recognised time-of-use label schemes (normalised, lower-case). Energy-band labels that do not
# match one of these have no known time-window schedule and are NOT guessed.
_DUOS_LABELS = frozenset({RED, AMBER, GREEN})
_DAY_NIGHT_LABELS = frozenset({"day", "night"})
# Peak/off-peak is deliberately NOT a scheme (JSON-F-39, D105): its timing is not the
# DUoS red band, so it stays unresolved until the stack states its own import_rate_windows.


def _clock_parts(timestamps: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (minute-of-day, is-weekday, month) arrays for ``timestamps`` (period-start).

    Band windows are defined on the **GB local clock**, so a tz-aware index is converted to
    Europe/London before its clock time/day-of-week are read (a UTC-labelled summer half-hour at
    15:30 lands in SEPD's 16:30 BST red band, as it physically should). A naive index is the
    site's local clock by documented convention and is read as-is.

    ``is_weekday`` is ``dayofweek < 5``, which is also **the bank-holiday treatment**: every sourced
    statement bands its bank holidays on the weekday row, and GB bank holidays fall Monday–Friday,
    so they are already banded as the weekdays they are — red applies, no holiday-date list needed.
    """
    idx = pd.DatetimeIndex(timestamps)
    if idx.tz is not None:
        idx = idx.tz_convert(_PUBLISHED_TZ)
    minute_of_day = idx.hour.to_numpy() * 60 + idx.minute.to_numpy()
    is_weekday = idx.dayofweek.to_numpy() < 5
    return minute_of_day, is_weekday, idx.month.to_numpy()


def _in_months(month: np.ndarray, months: tuple[int, ...] | None) -> np.ndarray:
    """Mask of half-hours falling in ``months`` (``None`` = every month).

    The generic month restriction both a seasonal DUoS red band and a seasonal levy window need.
    """
    if months is None:
        return np.ones(month.shape, dtype=bool)
    return np.isin(month, np.asarray(months))


def _red_season(month: np.ndarray, red_months: tuple[int, ...] | None) -> np.ndarray:
    """Mask of half-hours in a season the red band applies to (``None`` = all year)."""
    return _in_months(month, red_months)


def _local_dates(timestamps: pd.DatetimeIndex) -> np.ndarray:
    """The ``YYYY-MM-DD`` local calendar date of each half-hour (period-start, Europe/London).

    The same clock conversion `_clock_parts` applies, so a date and its half-hours are always read
    off one index. Needed only by the windowed-levy calendar, which excludes **non-working days** by
    date (GB bank holidays are Monday–Friday, so ``dayofweek < 5`` cannot see them).
    """
    idx = pd.DatetimeIndex(timestamps)
    if idx.tz is not None:
        idx = idx.tz_convert(_PUBLISHED_TZ)
    return np.asarray(idx.strftime("%Y-%m-%d"))


def duos_band_labels(
    timestamps: pd.DatetimeIndex, windows: DuosWindows = DEFAULT_DUOS_WINDOWS
) -> np.ndarray:
    """The DUoS band (``red``/``amber``/``green``) for each half-hour (period-start clock time).

    Green is the base, amber overrides it, red overrides amber — the published priority, and the
    reason overlapping windows in a release entry are harmless while gaps are not. Weekday and
    weekend windows are applied to their own day type, so an area with weekend amber (seven of the
    14 GB areas) no longer has its weekend priced as green.
    """
    minute, weekday, month = _clock_parts(timestamps)
    weekday_red, weekday_amber, weekend_red, weekend_amber = _minute_windows(windows)
    weekend = ~weekday
    bands = np.full(minute.shape, GREEN, dtype=object)
    bands[_in_windows(minute, weekday, weekday_amber)] = AMBER
    bands[_in_windows(minute, weekend, weekend_amber)] = AMBER
    # Red overrides the amber shoulders, and only in the season the red band applies to.
    in_season = _red_season(month, windows.red_months)
    bands[_in_windows(minute, weekday, weekday_red) & in_season] = RED
    bands[_in_windows(minute, weekend, weekend_red) & in_season] = RED
    return bands


def red_band_mask(
    timestamps: pd.DatetimeIndex, windows: DuosWindows = DEFAULT_DUOS_WINDOWS
) -> np.ndarray:
    """Boolean mask of the half-hours in the DUoS red band (the distribution peak).

    The mask that sets the whole £ of ``DUOS_RED_BAND_AVOIDANCE``: it covers both day types and
    respects a seasonal red band, so it is the area's published peak, not a national assumption.
    """
    minute, weekday, month = _clock_parts(timestamps)
    weekday_red, _weekday_amber, weekend_red, _weekend_amber = _minute_windows(windows)
    in_season = _red_season(month, windows.red_months)
    return (
        _in_windows(minute, weekday, weekday_red) | _in_windows(minute, ~weekday, weekend_red)
    ) & in_season


def duos_rate_series_with_absences(
    tariff: TariffStack,
    timestamps: pd.DatetimeIndex,
    windows: DuosWindows = DEFAULT_DUOS_WINDOWS,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Per-half-hour DUoS rate (£/kWh), plus the bands whose rate is ABSENT rather than zero.

    D45 (2026-08-12): **a null is not a zero, and a null on the pricing path is a defect.** An
    absent band rate silently substituted with 0.0 does not read "unknown" downstream, it deletes
    that band's component from the objective's ``price_t`` and from the customer bill, so the
    optimiser is solved against a price the site does not face and `DUOS_RED_BAND_AVOIDANCE` prices
    at £0 — one of only two priced-floor streams, zeroed by an empty field and indistinguishable
    from a measured result.

    So this function separates the two cases the previous ``or 0.0`` conflated:

    - a band rate of **0.0** is a fact (the site genuinely faces no charge in that band), and
    - a band rate of **None** is a MISSING INPUT, returned in the second element so the caller can
      derive it (D45's preferred route), or refuse the affected £ and cap the confidence band.

    A zero still enters the array for an absent band, because the array must stay dense and as long
    as ``timestamps``. The difference is that the absence is no longer silent: it is reported, and a
    caller that ignores the second element is choosing to.

    Note ``or 0.0`` was doubly wrong here: it also swallowed a genuine 0.0. This uses ``is None``.
    """
    bands = duos_band_labels(timestamps, windows)
    declared = {
        RED: tariff.duos_red_rate,
        AMBER: tariff.duos_amber_rate,
        GREEN: tariff.duos_green_rate,
    }
    absent = tuple(band for band, rate in declared.items() if rate is None)
    out = np.zeros(bands.shape, dtype=float)
    for band, rate in declared.items():
        if rate is not None:
            out[bands == band] = float(rate)
    return out, absent


def windowed_levy_mask(levy: WindowedLevy, timestamps: pd.DatetimeIndex) -> np.ndarray:
    """Boolean mask of the half-hours a `WindowedLevy` is charged on (period-start local clock).

    Banded with exactly the machinery the DUoS calendar uses — `_clock_parts` for the
    Europe/London period-start clock and the weekday/weekend split, `_in_windows` for the
    half-open minute windows, `_in_months` for the seasonal restriction — so a levy window and a
    DUoS band window can never be banded on two different calendars.

    The one thing this adds that DUoS does not need: **non-working days are excluded by date.** The
    Capacity Market obligation window is "working days between November & February", GB bank
    holidays fall Monday–Friday, and ``dayofweek < 5`` therefore bands Christmas Day as a working
    day. For DUoS that is correct (every sourced statement prints "Monday to Friday (Including Bank
    Holidays)"); for a working-day levy it is an **overstatement of the avoided cost**, so
    ``levy.excluded_dates`` is honoured here. An empty list is permitted and is disclosed by
    :func:`windowed_levy_rate_series`, never treated as "there are none".
    """
    minute, weekday, month = _clock_parts(timestamps)
    weekday_windows = tuple((w.start_minute, w.end_minute) for w in levy.weekday_windows)
    weekend_windows = tuple((w.start_minute, w.end_minute) for w in levy.weekend_windows)
    mask = _in_windows(minute, weekday, weekday_windows) | _in_windows(
        minute, ~weekday, weekend_windows
    )
    mask &= _in_months(month, levy.months)
    if levy.excluded_dates:
        mask &= ~np.isin(_local_dates(timestamps), np.asarray(levy.excluded_dates))
    return mask


def windowed_levy_rate_series(
    tariff: TariffStack, timestamps: pd.DatetimeIndex
) -> tuple[np.ndarray, list[str]]:
    """Per-half-hour £/kWh from the site's **windowed** levy lines; zeros outside every window.

    Returns ``(series, notes)``. ``series`` is dense and as long as ``timestamps`` — zeros where no
    windowed levy applies, which is a **fact** here rather than a D45 null: a levy whose window this
    half-hour is outside genuinely costs nothing in this half-hour.

    Deliberately does NOT include the flat ``levies`` map. The flat total already has two callers
    with two rounding conventions (`milp_solver.levy_rate_gbp_per_kwh` and
    `finance.cashflows._levy_rate_gbp_per_kwh`), and adding a third definition here is how the
    objective and the cost line stop agreeing. The caller adds its own flat scalar to this array.

    **Why the split matters downstream.** The objective must see ``flat + windowed`` so the
    optimiser values in-window discharge correctly; finance must charge the two SEPARATELY, because
    the flat component's net effect over a year is a cost (levy × the round-trip losses, since
    ``charge ≥ discharge``) while the windowed component's is a **credit** (the battery discharges
    inside the window and charges outside it, so in-window net import falls). Summing them into one
    line would hide a signed credit inside a line named "levies on charge".

    Every note names the rate, the window, the number of half-hours it landed on and the
    non-working-day treatment, so a reader can audit the calendar without re-running the solve.
    """
    series = np.zeros(len(timestamps), dtype=float)
    notes: list[str] = []
    for label in sorted(tariff.windowed_levies):
        levy = tariff.windowed_levies[label]
        mask = windowed_levy_mask(levy, timestamps)
        series[mask] += float(levy.rate_gbp_per_kwh)
        weekday_txt = _fmt(tuple((w.start_minute, w.end_minute) for w in levy.weekday_windows))
        weekend_txt = _fmt(tuple((w.start_minute, w.end_minute) for w in levy.weekend_windows))
        months_txt = "all year" if levy.months is None else f"months {list(levy.months)}"
        excluded = (
            f"{len(levy.excluded_dates)} non-working day(s) excluded by date "
            f"({', '.join(levy.excluded_dates[:6])}"
            f"{', …' if len(levy.excluded_dates) > 6 else ''})"
            if levy.excluded_dates
            else "NO non-working days excluded, so any bank holiday inside the window is charged "
            "as a working day and the avoided cost is OVERSTATED by roughly one working day in 28 "
            "(three of ~85 in a November-February window). Supply excluded_dates to remove it"
        )
        notes.append(
            f"WINDOWED LEVY '{label}' £{levy.rate_gbp_per_kwh}/kWh applies to "
            f"{int(mask.sum())} of {len(timestamps)} half-hours "
            f"({100.0 * float(mask.mean()):.2f}%): weekday {weekday_txt}, weekend {weekend_txt}, "
            f"{months_txt}, Europe/London period-start clock; {excluded}. It is charged ONLY in "
            f"those half-hours — the flat `levies` map is unaffected, and outside the window this "
            f"levy's rate is a measured £0, not a missing input."
            + (f" Basis: {levy.basis}" if levy.basis else "")
        )
    return series, notes


def duos_rate_series(
    tariff: TariffStack,
    timestamps: pd.DatetimeIndex,
    windows: DuosWindows = DEFAULT_DUOS_WINDOWS,
) -> np.ndarray:
    """Per-half-hour DUoS rate (£/kWh) from the site's band rates.

    Thin wrapper over :func:`duos_rate_series_with_absences` that DISCARDS the absence report. Kept
    so existing callers keep working, but prefer the two-value form: under D45 an absent band rate
    is a defect to derive or to refuse, and this signature cannot tell you one is present. Callers
    on the pricing path should migrate.
    """
    series, _absent = duos_rate_series_with_absences(tariff, timestamps, windows)
    return series


def _normalise(label: str) -> str:
    """Lower-case + collapse separators so 'Off-Peak'/'off_peak'/'off peak' all match 'offpeak'."""
    return "".join(ch for ch in label.lower() if ch.isalnum())


def _band_labels_for_scheme(
    scheme: frozenset[str], timestamps: pd.DatetimeIndex, windows: DuosWindows
) -> np.ndarray:
    """Assign each half-hour the band label for a recognised time-of-use ``scheme``."""
    if scheme == _DUOS_LABELS:
        return duos_band_labels(timestamps, windows)
    if scheme == _DAY_NIGHT_LABELS:
        minute, _weekday, _month = _clock_parts(timestamps)
        night = (minute >= _NIGHT_START * 60) | (minute < _NIGHT_END * 60)
        return np.where(night, "night", "day")
    raise ValueError(f"no time-window scheme for {sorted(scheme)}")  # pragma: no cover


@dataclass(frozen=True)
class EnergyPriceEnvelope:
    """The per-half-hour energy import price, with the half-hours the stack does not price marked.

    D105 (WP-UW-07, MD-F-18 / JSON-F-38/F-41). A tariff band the site did not quote, or a
    label with no time window, used to be priced at **the mean of the bands it did quote**: an
    invented price on hours the site never priced, and a spread the dispatch could harvest. Here
    every half-hour whose band is quoted and time-resolved is priced **as itself**, and every other
    half-hour is *unresolved* and carries a **no-spread envelope** of the quoted rates instead:

    - ``sell`` (avoided import, i.e. discharge) is the **lowest** quoted rate there;
    - ``buy`` (added import, i.e. charge) is the **highest** quoted rate there.

    So no cycle touching an unresolved half-hour can earn energy spread from it, and the invented
    value is at most zero. On resolved half-hours ``buy == sell``. ``bill`` is ``sell``: on
    unresolved half-hours the energy line is a **lower bound** and is labelled so.
    """

    sell: np.ndarray
    buy: np.ndarray
    unresolved: np.ndarray
    #: Scheme labels the stack did not quote (each a ``tariff_stack.import_rates.<band>`` record).
    missing_bands: tuple[str, ...]
    #: Where the band timing came from: ``flat`` (no timing needed), ``stack`` (the stack's own
    #: ``import_rate_windows``), ``release_duos`` (the area's DUoS calendar), ``default_day_night``
    #: (23:00-07:00, declared), or ``none`` (no timing: every half-hour unresolved).
    window_source: str
    note: str | None = None

    @property
    def bill(self) -> np.ndarray:
        """The price the customer bill applies (a lower bound on unresolved half-hours)."""
        return self.sell

    @property
    def spread_neutral(self) -> bool:
        """True when every half-hour is resolved, so ``buy`` and ``sell`` are the same array."""
        return not bool(self.unresolved.any())


def _labels_from_stack_windows(
    import_rate_windows: dict[str, BandWindows], timestamps: pd.DatetimeIndex
) -> np.ndarray:
    """Each half-hour's normalised label from the stack's own windows (``""`` where uncovered)."""
    labels = np.full(len(timestamps), "", dtype=object)
    residual: str | None = None
    for label, windows in import_rate_windows.items():
        if windows.is_residual:
            residual = _normalise(label)
            continue
        labels[band_windows_mask(windows, timestamps)] = _normalise(label)
    if residual is not None:
        labels[labels == ""] = residual
    return labels


def energy_price_envelope(
    import_rates: dict[str, float],
    timestamps: pd.DatetimeIndex,
    windows: DuosWindows = DEFAULT_DUOS_WINDOWS,
    import_rate_windows: dict[str, BandWindows] | None = None,
) -> tuple[EnergyPriceEnvelope | None, str | None]:
    """The energy import price envelope for a stack's ``import_rates`` (see `EnergyPriceEnvelope`).

    Returns ``(envelope, note)``; ``envelope`` is ``None`` only when there are **no** import rates
    (the site's energy price is genuinely unknown: F-01, unchanged). Timing, in order: the stack's
    own ``import_rate_windows``; else a recognised scheme (red/amber/green on the area's DUoS
    calendar, day/night on the declared 23:00-07:00 default); else none. Peak/off-peak with no
    windows is **not** mapped onto the DUoS red band any more: inventing the timing invents the
    spread just as inventing the price does, so every half-hour is unresolved until windows arrive.
    A single distinct rate is flat and needs no timing.
    """
    if not import_rates:
        return None, "no import tariff rates — energy price unknown, arbitrage cannot be priced."
    n = len(timestamps)
    normalised = {_normalise(k): float(v) for k, v in import_rates.items()}
    distinct = {round(v, 10) for v in normalised.values()}
    if len(distinct) < 2:
        flat = next(iter(normalised.values()))
        series = np.full(n, flat, dtype=float)
        flat_note = (
            f"import tariff is a single flat rate (£{flat:.4f}/kWh) — no price spread to "
            "arbitrage, but charge energy is still costed at it, so round-trip losses are paid "
            "for and a cycle is only taken when another band (e.g. DUoS) covers them."
        )
        return (
            EnergyPriceEnvelope(
                sell=series,
                buy=series,
                unresolved=np.zeros(n, dtype=bool),
                missing_bands=(),
                window_source="flat",
                note=flat_note,
            ),
            flat_note,
        )

    missing: tuple[str, ...] = ()
    if import_rate_windows:
        labels = _labels_from_stack_windows(import_rate_windows, timestamps)
        source = "stack"
    else:
        keys = frozenset(normalised)
        scheme = next((s for s in (_DUOS_LABELS, _DAY_NIGHT_LABELS) if keys <= s), None)
        if scheme is None:
            labels = np.full(n, "", dtype=object)
            source = "none"
        else:
            labels = _band_labels_for_scheme(scheme, timestamps, windows)
            source = "release_duos" if scheme == _DUOS_LABELS else "default_day_night"
            missing = tuple(sorted(scheme - keys))

    low, high = min(normalised.values()), max(normalised.values())
    sell = np.full(n, low, dtype=float)
    buy = np.full(n, high, dtype=float)
    resolved = np.zeros(n, dtype=bool)
    for band, rate in normalised.items():
        hit = labels == band
        sell[hit] = rate
        buy[hit] = rate
        resolved |= hit
    unresolved = ~resolved
    note: str | None = None
    count = int(unresolved.sum())
    if count:
        if source == "none":
            why = (
                f"import tariff bands {sorted(import_rates)} have no time windows on the stack "
                "and are not red/amber/green or day/night"
            )
        elif missing:
            why = f"tariff band(s) {list(missing)} were not quoted by the site"
        else:
            why = "the stack's own import_rate_windows leave these half-hours uncovered"
        note = (
            f"{why}: {count} of {n} half-hours are UNRESOLVED and carry a no-spread envelope "
            f"(charging costed at £{high:.4f}/kWh, the highest quoted rate; discharge credited at "
            f"£{low:.4f}/kWh, the lowest), so no energy arbitrage is earned on them and no mean "
            "of bands is used. The energy bill on those half-hours is a LOWER BOUND."
        )
    # A day/night stack timed on the default 23:00-07:00 window raises no per-solve note: the
    # window is declared once, as a `DerivedAssumption` (`import_rate_assumptions`), on the run.
    return (
        EnergyPriceEnvelope(
            sell=sell,
            buy=buy,
            unresolved=unresolved,
            missing_bands=missing,
            window_source=source,
            note=note,
        ),
        note,
    )


def energy_price_series(
    import_rates: dict[str, float],
    timestamps: pd.DatetimeIndex,
    windows: DuosWindows = DEFAULT_DUOS_WINDOWS,
    import_rate_windows: dict[str, BandWindows] | None = None,
) -> tuple[np.ndarray | None, str | None]:
    """The per-half-hour energy **bill** price (£/kWh): `energy_price_envelope`'s ``bill`` array.

    Returns ``(prices, note)``; ``prices`` is ``None`` ONLY when the site's energy price is
    genuinely unknown (no import rates). **A flat tariff returns a CONSTANT array, not ``None``**
    (hostile review 2026-08-10, F-01): ``None`` conflated "no spread" with "charging is free".

    **Never an objective price on its own.** On unresolved half-hours it is the lowest quoted rate,
    which is right for a bill's lower bound and wrong as the price of charging; the MILP reads the
    envelope's ``buy`` and ``sell`` through `energy_objective_coefficients`. The mean-of-bands
    fallback this function used to apply is gone (WP-UW-07, acceptance 2).
    """
    envelope, note = energy_price_envelope(import_rates, timestamps, windows, import_rate_windows)
    return (None if envelope is None else envelope.bill), note


def energy_objective_coefficients(
    envelope: EnergyPriceEnvelope,
) -> tuple[np.ndarray, np.ndarray | None]:
    """``(sell, buy)`` for the MILP's energy term; ``buy`` is ``None`` when spread-neutral.

    ``None`` tells the caller to keep the single-price expression ``price·(discharge − charge)``
    unchanged, so a stack with every half-hour resolved builds a byte-identical model.
    """
    if envelope.spread_neutral:
        return envelope.sell, None
    return envelope.sell, envelope.buy


IMPORT_RATE_WINDOWS_KEY: Final = "tariff_stack.import_rate_windows"


def import_rate_assumptions(
    stack: TariffStack, *, release: str | None = None
) -> list[DerivedAssumption]:
    """The `DerivedAssumption` s the energy envelope stands on for this stack (D44, never silent).

    - a recognised scheme band the stack did not quote → ``tariff_stack.import_rates.<band>``;
    - day/night timed on the default 23:00-07:00 window → ``tariff_stack.import_rate_windows``;
    - labels with no windows and no recognised scheme (peak/off-peak included) →
      ``tariff_stack.import_rate_windows``, every half-hour unresolved.

    A flat stack, a stack with its own windows and a full red/amber/green stack (timed by the
    area's sourced DUoS calendar) need none. Pure in the stack: no timestamps are read.
    """
    rates = stack.import_rates
    if not rates or stack.import_rate_windows:
        return []
    normalised = {_normalise(k): float(v) for k, v in rates.items()}
    if len({round(v, 10) for v in normalised.values()}) < 2:
        return []
    keys = frozenset(normalised)
    scheme = next((s for s in (_DUOS_LABELS, _DAY_NIGHT_LABELS) if keys <= s), None)
    quoted = ", ".join(f"{k} £{v:g}/kWh" for k, v in sorted(normalised.items()))
    low, high = min(normalised.values()), max(normalised.values())
    envelope = (
        f"a no-spread envelope of the quoted rates ({quoted}): charging costed at £{high:g}/kWh, "
        f"discharge credited at £{low:g}/kWh, so no arbitrage is earned on those half-hours. "
        "Direction of error: the arbitrage value is understated if the band is really cheaper "
        "than the highest quoted rate at charging times; the energy bill on those half-hours is "
        "a lower bound."
    )
    records: list[DerivedAssumption] = []
    if scheme is None:
        records.append(
            DerivedAssumption(
                kind="derived_input",
                key=IMPORT_RATE_WINDOWS_KEY,
                missing_input=f"the times of day each import band ({sorted(rates)}) applies",
                value="unresolved: every half-hour priced on the no-spread envelope",
                basis=(
                    f"The stack quotes bands {sorted(rates)} but no time windows, and the labels "
                    "are not a scheme with a sourced calendar (red/amber/green or day/night). The "
                    "timing is not invented (peak/off-peak is no longer mapped onto the DUoS red "
                    f"band), so every half-hour is priced on {envelope}"
                ),
                derivation_method="sourced_default",
                derived_by="gridq.optimisation.tariff_calendar",
                derived_on_release=release,
                confidence_ceiling=ceiling_for_key(IMPORT_RATE_WINDOWS_KEY),
                confirmation_ask=(
                    f"Send the times each import band ({', '.join(sorted(rates))}) applies, from "
                    "the supply contract or a recent invoice."
                ),
            )
        )
        return records
    for band in sorted(scheme - keys):
        key = f"tariff_stack.import_rates.{band}"
        records.append(
            DerivedAssumption(
                kind="derived_input",
                key=key,
                missing_input=f"the {band} import rate",
                value="unresolved: no-spread envelope of the quoted bands",
                basis=(
                    f"The stack quotes {sorted(keys)} but not {band!r}; the {band} half-hours are "
                    f"priced on {envelope} The mean of the quoted bands is not used."
                ),
                derivation_method="sourced_default",
                derived_by="gridq.optimisation.tariff_calendar",
                derived_on_release=release,
                confidence_ceiling=ceiling_for_key(key),
                confirmation_ask=f"Send the {band} unit rate from the supply contract or invoice.",
            )
        )
    if scheme == _DAY_NIGHT_LABELS:
        records.append(
            DerivedAssumption(
                kind="derived_input",
                key=IMPORT_RATE_WINDOWS_KEY,
                missing_input="the hours the night rate applies",
                value="night 23:00-07:00, day 07:00-23:00, every day (Europe/London clock)",
                unit=None,
                basis=(
                    "The stack quotes day and night rates but not their hours. Economy-7 style "
                    "night windows vary by region and by meter, so the 23:00-07:00 window is a "
                    "default, declared rather than read as the site's own. Direction of error: if "
                    "the site's night window is shorter or later, the night-charging value is "
                    "overstated; if longer, understated."
                ),
                derivation_method="sourced_default",
                derived_by="gridq.optimisation.tariff_calendar",
                derived_on_release=release,
                confidence_ceiling=ceiling_for_key(IMPORT_RATE_WINDOWS_KEY),
                confirmation_ask=(
                    "Confirm the hours the night rate applies (from the supply contract or the "
                    "meter's time-switch code)."
                ),
            )
        )
    return records


def network_rate_series_with_absences(
    tariff: TariffStack,
    timestamps: pd.DatetimeIndex,
    windows: DuosWindows = DEFAULT_DUOS_WINDOWS,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Per-half-hour network unit rate (£/kWh) and the absent bands, from the stack's best record.

    A published `network_tariff` wins (WP-UW-07): a CDCM row prices each half-hour of the area's
    DUoS calendar at its own band's published rate, an EDCM row prices its super red window, and
    nothing is absent. Without one, the legacy flat ``duos_*`` lines are read exactly as
    `duos_rate_series_with_absences` reads them, so every existing stack is byte-identical.
    """
    nt = tariff.network_tariff
    if nt is None:
        return duos_rate_series_with_absences(tariff, timestamps, windows)
    if isinstance(nt, CdcmTariff):
        labels = duos_band_labels(timestamps, windows)
        return network_unit_rate_series(nt, timestamps, labels), ()
    return network_unit_rate_series(nt, timestamps), ()


def tariff_provenance_phrase(tariff: TariffStack) -> str:
    """The one sentence the report may use for where the stack's rates came from (R8, B-17).

    "priced on its own tariff stack" is rendered **only** when every component is the site's own
    (own invoice, published for the site, or not charged to it). Otherwise each component that is
    not its own is named, and the sentence says "not the site's own tariff". An own-invoice claim
    with no evidence object attached says so rather than reading as evidenced.
    """
    prov = tariff.provenance
    words = {
        "own_invoice": "its own invoiced rates",
        "published_for_site": "the DNO's published rates for its connection",
        "by_analogy": "rates taken by analogy from another tariff",
        "assumed": "assumed rates",
        "not_charged": "not charged to this site",
    }
    parts = (("commodity", prov.commodity), ("network", prov.network), ("levies", prov.levies))
    if prov.is_own:
        detail = "; ".join(f"{name}: {words[value]}" for name, value in parts)
        unevidenced = prov.commodity == "own_invoice" and not tariff.evidence
        evidence = (
            " (recorded as its own invoiced rates; no evidence object attached)"
            if unevidenced
            else ""
        )
        return f"priced on its own tariff stack ({detail}){evidence}"
    not_own = "; ".join(
        f"{name} {words[value]}" for name, value in parts if value not in OWN_COMPONENT_PROVENANCE
    )
    own = [f"{name} {words[value]}" for name, value in parts if value in OWN_COMPONENT_PROVENANCE]
    own_text = f" ({'; '.join(own)})" if own else ""
    return f"priced on a stack that is not the site's own tariff: {not_own}{own_text}"


@dataclass(frozen=True)
class ResolvedPriceBasis:
    """How every £/kWh and £/day line of a stack is turned into the net-of-VAT figure priced.

    ``divisor`` is ``1 + vat_rate_fraction`` for a VAT-inclusive stack and ``1.0`` otherwise; the
    one reader of `gridq.schemas.TariffBasis` on the pricing path (D103).
    """

    divisor: float
    note: str | None


def resolved_price_basis(tariff: TariffStack) -> ResolvedPriceBasis:
    """The stack's VAT basis as the divisor the pricing path applies, with its disclosure."""
    basis = tariff.basis
    if basis.vat == "included":
        divisor = basis.net_of_vat_divisor
        return ResolvedPriceBasis(
            divisor=divisor,
            note=(
                f"the tariff stack states its rates INCLUDE VAT at {basis.vat_rate_fraction:g}; "
                f"every £ line is priced net of VAT (divided by {divisor:g}), per H-01 for a "
                "VAT-registered business."
            ),
        )
    return ResolvedPriceBasis(divisor=1.0, note=None)


def net_of_vat_stack(tariff: TariffStack) -> TariffStack:
    """The stack with every £ rate stated net of VAT; the same object when it already is.

    The single place a VAT-inclusive stack is converted (`resolved_price_basis`), so the MILP, the
    customer bill and finance all read one net-of-VAT stack and never divide twice. The published
    `network_tariff` row is a DNO statement, which is net of VAT by construction, and is left as is.
    """
    resolved = resolved_price_basis(tariff)
    if resolved.divisor == 1.0:
        return tariff
    d = resolved.divisor

    def _net(value: float | None) -> float | None:
        return None if value is None else value / d

    return tariff.model_copy(
        update={
            "import_rates": {k: v / d for k, v in tariff.import_rates.items()},
            "export_rates": {k: v / d for k, v in tariff.export_rates.items()},
            "levies": {k: v / d for k, v in tariff.levies.items()},
            "windowed_levies": {
                k: w.model_copy(update={"rate_gbp_per_kwh": w.rate_gbp_per_kwh / d})
                for k, w in tariff.windowed_levies.items()
            },
            "duos_red_rate": _net(tariff.duos_red_rate),
            "duos_amber_rate": _net(tariff.duos_amber_rate),
            "duos_green_rate": _net(tariff.duos_green_rate),
            "standing_charge_per_day_gbp": _net(tariff.standing_charge_per_day_gbp),
            "capacity_charge_gbp_per_kva_day": _net(tariff.capacity_charge_gbp_per_kva_day),
            "basis": tariff.basis.model_copy(update={"vat": "excluded", "vat_rate_fraction": None}),
        }
    )
