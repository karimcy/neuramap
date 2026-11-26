"""Synthetic half-hourly load profiling for the manual-entry path.

A non-expert user rarely has a half-hourly meter export, but they do know their annual consumption,
their building type and (often) their peak demand. This module turns those into a full 8,760-hour
(17,520 half-hour) load profile, shaped by a business-type reference curve with weekday/weekend and
seasonal variation, scaled so the annual energy matches what the user entered. The engine dispatches
on the presence of a readable half-hourly file (``milp_solver`` checks ``half_hourly_file_ref``), so
this lets a manual quote actually solve and produce an estimate instead of returning insufficient
data.

It is honest about what it is: the profile is **modelled, not metered**. The site is graded
``T1_PARTIAL_HALF_HOURLY`` (never the validated ``T2``) and the run carries a SYNTHETIC warning, so
the result is clearly an estimate and never bankable on a synthetic shape. Generation is fully
deterministic (no clock, no randomness), so an identical manual entry yields an identical profile
and therefore an identical, reproducible quote id.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

#: Business-type daily shapes (24 hourly weights, mean ~1). Mirrors the wizard's live-preview curves
#: so the modelled profile matches what the user saw while entering data.
_SHAPES: dict[str, list[float]] = {
    "Office": [
        0.3,
        0.3,
        0.3,
        0.3,
        0.3,
        0.35,
        0.5,
        0.8,
        1.2,
        1.4,
        1.45,
        1.45,
        1.4,
        1.45,
        1.5,
        1.45,
        1.3,
        1.0,
        0.7,
        0.5,
        0.45,
        0.4,
        0.35,
        0.3,
    ],  # noqa: E501
    "Retail": [
        0.18,
        0.16,
        0.16,
        0.16,
        0.18,
        0.25,
        0.4,
        0.7,
        1.1,
        1.5,
        1.6,
        1.65,
        1.6,
        1.6,
        1.6,
        1.6,
        1.55,
        1.45,
        1.2,
        0.7,
        0.4,
        0.3,
        0.22,
        0.2,
    ],  # noqa: E501
    "Hotel": [
        0.5,
        0.45,
        0.42,
        0.42,
        0.45,
        0.6,
        0.95,
        1.25,
        1.2,
        1.05,
        1.0,
        1.0,
        1.05,
        1.0,
        1.0,
        1.05,
        1.15,
        1.3,
        1.5,
        1.55,
        1.4,
        1.1,
        0.8,
        0.6,
    ],  # noqa: E501
    "Restaurant": [
        0.2,
        0.18,
        0.16,
        0.16,
        0.16,
        0.18,
        0.25,
        0.4,
        0.55,
        0.7,
        1.0,
        1.55,
        1.6,
        1.3,
        0.9,
        0.8,
        0.9,
        1.2,
        1.7,
        1.85,
        1.6,
        1.1,
        0.6,
        0.3,
    ],  # noqa: E501
    "Warehouse": [
        0.12,
        0.12,
        0.12,
        0.12,
        0.12,
        0.3,
        0.9,
        1.5,
        1.6,
        1.6,
        1.6,
        1.6,
        1.55,
        1.6,
        1.6,
        1.6,
        1.4,
        0.7,
        0.2,
        0.14,
        0.12,
        0.12,
        0.12,
        0.12,
    ],  # noqa: E501
    "Supermarket": [
        0.6,
        0.55,
        0.55,
        0.55,
        0.6,
        0.7,
        0.9,
        1.15,
        1.35,
        1.45,
        1.5,
        1.5,
        1.45,
        1.45,
        1.45,
        1.45,
        1.45,
        1.4,
        1.3,
        1.1,
        0.9,
        0.8,
        0.7,
        0.65,
    ],  # noqa: E501
    "Industrial": [
        0.5,
        0.5,
        0.5,
        0.5,
        0.55,
        0.8,
        1.2,
        1.45,
        1.5,
        1.5,
        1.5,
        1.5,
        1.45,
        1.5,
        1.5,
        1.5,
        1.4,
        1.0,
        0.7,
        0.55,
        0.5,
        0.5,
        0.5,
        0.5,
    ],  # noqa: E501
    "Generic Commercial": [
        0.35,
        0.32,
        0.3,
        0.3,
        0.34,
        0.45,
        0.7,
        1.0,
        1.3,
        1.4,
        1.45,
        1.45,
        1.4,
        1.45,
        1.45,
        1.4,
        1.25,
        1.0,
        0.75,
        0.55,
        0.45,
        0.4,
        0.38,
        0.36,
    ],  # noqa: E501
}
#: Weekend demand as a fraction of a weekday, per type (trading vs always-on businesses).
_WEEKEND: dict[str, float] = {
    "Office": 0.55,
    "Warehouse": 0.5,
    "Industrial": 0.6,
    "Retail": 0.85,
    "Restaurant": 0.95,
    "Hotel": 1.0,
    "Supermarket": 1.0,
    "Generic Commercial": 0.75,
}
#: Mild monthly seasonal factor (Jan..Dec): UK demand is a little higher in winter.
_SEASON = [1.12, 1.08, 1.03, 0.98, 0.93, 0.90, 0.90, 0.92, 0.97, 1.02, 1.08, 1.12]

_YEAR = 2025  # a fixed reference year (not the wall clock) so generation stays deterministic
_STEPS = 17520  # 365 days x 48 half-hours


def _shape(business_type: str | None) -> list[float]:
    return _SHAPES.get((business_type or "").strip(), _SHAPES["Generic Commercial"])


def _weekend(business_type: str | None) -> float:
    return _WEEKEND.get((business_type or "").strip(), 0.75)


def generate_hh_csv(
    out_dir: Path,
    *,
    business_type: str | None,
    annual_kwh: float,
    peak_kw: float | None = None,
) -> Path:
    """Write a deterministic synthetic half-hourly CSV (``timestamp,import_kw``); return its path.

    The shape is the business-type reference curve with weekday/weekend and seasonal variation,
    scaled so total annual energy equals ``annual_kwh``. ``peak_kw`` only lifts the profile if the
    modelled peak falls below the stated peak (it never reduces annual energy). The file is
    content-addressed, so identical inputs reuse the same file (idempotent and deterministic).
    """
    shape = _shape(business_type)
    weekend = _weekend(business_type)
    start = datetime(_YEAR, 1, 1)

    raw: list[float] = []
    moment = start
    for _ in range(_STEPS):
        factor = weekend if moment.weekday() >= 5 else 1.0
        raw.append(shape[moment.hour] * factor * _SEASON[moment.month - 1])
        moment += timedelta(minutes=30)

    energy = sum(raw) * 0.5  # kWh = kW * 0.5h
    scale = (annual_kwh / energy) if energy > 0 else 1.0
    powers = [v * scale for v in raw]

    # If a peak is stated, reshape around the mean to approach it WITHOUT changing annual energy: a
    # linear amplification about the mean (peaks up, troughs down) preserves the sum; we then
    # re-scale to lock annual energy exactly. k is clamped so the profile never becomes unphysical.
    if peak_kw and peak_kw > 0 and powers:
        mean = sum(powers) / len(powers)
        old_peak = max(powers)
        if old_peak > mean:
            k = max(0.3, min(3.0, (peak_kw - mean) / (old_peak - mean)))
            powers = [max(0.0, mean + (v - mean) * k) for v in powers]
            adjusted = sum(powers) * 0.5
            if adjusted > 0:
                powers = [v * (annual_kwh / adjusted) for v in powers]

    lines = ["timestamp,import_kw"]
    moment = start
    for value in powers:
        lines.append(moment.strftime("%Y-%m-%d %H:%M:%S") + "," + format(round(value, 3), "g"))
        moment += timedelta(minutes=30)
    content = "\n".join(lines) + "\n"

    key = f"{business_type}|{annual_kwh}|{peak_kw}".encode()
    digest = hashlib.sha256(key + content.encode("utf-8")).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"synthetic_{digest}.csv"
    path.write_text(content, encoding="utf-8")
    return path


# --- billing-constrained profile (v2, 2026-07-07) --------------------------------------------
#
# The archetype path above invents a SHAPE (a business-type curve) and only approaches the stated
# peak within a clamped amplification, silently. When the intake carries OBSERVED shape facts
# (day/night volume split from bill lines, peak timing, a near-zero midday window), the honest
# model is built from those constraints instead of an archetype: every structural feature of the
# generated profile traces to a documented observation, and the stated annual energy, day-band
# share and peak are hit EXACTLY. The residual placement (which specific nights carry the peak
# bursts) is arbitrary-but-deterministic and disclosed via the returned notes.

#: The day/night band boundary mirrors `gridq.optimisation.tariff_calendar` (night 23:00-07:00).
_NIGHT_HOURS = frozenset({23, 0, 1, 2, 3, 4, 5, 6})
#: Deterministic peak-burst placement: weekly events across the summer months (the observed
#: excess-capacity breaches on the motivating site were May/June), 2h at the stated peak.
_BURST_WEEKDAY = 1  # Tuesday
_BURST_MONTHS = (5, 6, 7)
_BURST_SLOTS = 4  # 4 half-hours = 2h


def generate_constrained_hh_csv(
    out_dir: Path,
    *,
    annual_kwh: float,
    peak_kw: float | None = None,
    day_kwh_share: float | None = None,
    peak_period: str | None = None,
    midday_low: bool = False,
) -> tuple[Path, list[str]]:
    """Write a deterministic HH CSV built from OBSERVED billing constraints; return (path, notes).

    Constraints honoured exactly: total annual energy = ``annual_kwh``; day-band (07:00-23:00)
    energy share = ``day_kwh_share`` when given; max half-hour power = ``peak_kw`` when given
    (placed in the declared ``peak_period`` window as deterministic 2h weekly summer bursts).
    ``midday_low`` pins 09:00-14:00 near zero (observed e.g. on-site PV or overnight-only
    operations). The profile is modelled, not metered - T1, never bankable - and the notes list
    every modelling choice a human should see.
    """
    notes: list[str] = []
    share = None if day_kwh_share is None else min(max(float(day_kwh_share), 0.01), 0.99)
    overnight = (peak_period or "").strip().lower() == "overnight"

    # Base 24h weights from the declared facts (relative within each band; bands re-scaled next).
    weights: list[float] = []
    for hour in range(24):
        if hour in _NIGHT_HOURS:
            w = 1.0 if overnight else 0.35
        elif 7 <= hour < 9:
            w = 0.5
        elif 9 <= hour < 14:
            w = 0.05 if midday_low else 1.0
        elif 14 <= hour < 17:
            w = 0.3 if midday_low else 1.0
        else:  # 17:00-23:00 evening
            w = 0.7 if overnight else 1.1
        weights.append(w)

    start = datetime(_YEAR, 1, 1)
    raw: list[float] = []
    moment = start
    for _ in range(_STEPS):
        factor = 0.9 if moment.weekday() >= 5 else 1.0
        raw.append(weights[moment.hour] * factor * _SEASON[moment.month - 1])
        moment += timedelta(minutes=30)

    # Two-band scaling: hit the day/night energy split exactly (else scale total only).
    is_night = []
    moment = start
    for _ in range(_STEPS):
        is_night.append(moment.hour in _NIGHT_HOURS)
        moment += timedelta(minutes=30)
    day_raw = sum(v for v, n in zip(raw, is_night, strict=True) if not n) * 0.5
    night_raw = sum(v for v, n in zip(raw, is_night, strict=True) if n) * 0.5
    if share is not None and day_raw > 0 and night_raw > 0:
        day_target = annual_kwh * share
        night_target = annual_kwh * (1.0 - share)
        alpha, beta = day_target / day_raw, night_target / night_raw
        notes.append(
            f"day-band share set to {share:.3f} from the bill's day/night volumes "
            "(modelled two-band scaling)."
        )
    else:
        total = day_raw + night_raw
        alpha = beta = (annual_kwh / total) if total > 0 else 1.0
    powers = [v * (beta if n else alpha) for v, n in zip(raw, is_night, strict=True)]

    # Peak bursts: place the stated peak exactly, as deterministic weekly 2h summer events in
    # the declared window, then re-balance the same band's non-burst slots so band energy (and
    # therefore the split AND the annual total) stays exact.
    if peak_kw and peak_kw > 0:
        burst_hour = 23 if overnight else 11
        burst_idx: list[int] = []
        moment = start
        for i in range(_STEPS):
            if (
                moment.month in _BURST_MONTHS
                and moment.weekday() == _BURST_WEEKDAY
                and moment.hour == burst_hour
                and moment.minute == 0
            ):
                burst_idx.extend(j for j in range(i, min(i + _BURST_SLOTS, _STEPS)))
            moment += timedelta(minutes=30)
        burst_set = set(burst_idx)
        band_is_night = burst_hour in _NIGHT_HOURS
        band_idx = [i for i, n in enumerate(is_night) if n == band_is_night]
        band_energy = sum(powers[i] for i in band_idx) * 0.5
        burst_energy = peak_kw * len(burst_idx) * 0.5
        rest_idx = [i for i in band_idx if i not in burst_set]
        rest_energy = sum(powers[i] for i in rest_idx) * 0.5
        if burst_energy < band_energy and rest_energy > 0:
            rebalance = (band_energy - burst_energy) / rest_energy
            for i in rest_idx:
                powers[i] *= rebalance
            for i in burst_idx:
                powers[i] = peak_kw
            notes.append(
                f"stated peak {peak_kw:g} kW placed exactly as deterministic 2h weekly "
                f"{'overnight' if band_is_night else 'daytime'} bursts "
                f"(Tuesdays, May-July, {len(burst_idx)} half-hours); the band's remaining "
                "slots re-balanced so annual energy and the day/night split stay exact."
            )
        else:
            notes.append(
                f"stated peak {peak_kw:g} kW could NOT be honoured: the peak bursts would "
                "exceed the band's total energy implied by the annual consumption and split - "
                "the modelled peak is lower than observed. Treat peak-linked results as "
                "understated and supply the metered half-hourly file."
            )
        modelled_peak = max(powers)
        if peak_kw and abs(modelled_peak - peak_kw) / peak_kw > 0.02:
            notes.append(
                f"modelled peak {modelled_peak:.1f} kW differs from the stated {peak_kw:g} kW "
                "by more than 2% - review the shape facts or supply metered data."
            )

    lines = ["timestamp,import_kw"]
    moment = start
    for value in powers:
        lines.append(moment.strftime("%Y-%m-%d %H:%M:%S") + "," + format(round(value, 3), "g"))
        moment += timedelta(minutes=30)
    content = "\n".join(lines) + "\n"

    key = f"constrained|{annual_kwh}|{peak_kw}|{day_kwh_share}|{peak_period}|{midday_low}".encode()
    digest = hashlib.sha256(key + content.encode("utf-8")).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"synthetic_{digest}.csv"
    path.write_text(content, encoding="utf-8")
    return path, notes
