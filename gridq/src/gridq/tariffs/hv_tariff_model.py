"""``hv_tariff_model``: CDCM and EDCM network charges for LV, HV and EHV sites (WP-UW-07, A6-07).

One implementation of the published charging arithmetic, read by the published-example tests, the
customer bill and the dispatch model. It imports `gridq.schemas` only (D105), so the MILP
constraint library WP-SIZE-02 extracts can depend on it without a cycle.

**Two routes to one answer.**

- `cdcm_annual_charges` is the DNO's own model algebra over annual volumes. It is what ENWL's
  published CDCM model computes on its 'Net revenue summary' sheet (rows 95 to 101, quoted in
  ``tests/fixtures/dno_charging/enwl_cdcm_2026_27_site_specific.json``):

  - unit charges: MWh x 1000 x p/kWh / 100;
  - fixed charge: MPANs x p/MPAN/day x days / 100;
  - capacity and exceeded capacity: kVA x p/kVA/day x days / 100;
  - reactive power: MVArh x 1000 x p/kVArh / 100.

  The test reproduces ENWL's LV and HV Site Specific Band 1 net revenue and typical bill to the
  penny from the model's own volumes and rates.

- `billing_determinants_from_series` plus `cdcm_charges_from_series` / `edcm_charges_from_series`
  build the same lines from a half-hourly series, month by month, under the rules the ENWL LC14
  statement 2026/27 states for LV and HV designated properties:

  - **each time band is priced as itself**: a half-hour's kWh is charged at its own band's rate,
    and nothing else (no mean of bands exists anywhere in this module);
  - **exceeded capacity**: per half-hour ``max(2 x sqrt(AI^2 + max(RI, RE)^2) - MIC, 0)`` kVA,
    "the maximum value from the billing period is applied", and it is "charged for the full
    duration of the billing period in which the breach occurs";
  - **chargeable reactive power**: per half-hour ``max(max(RI, RE) - 0.33 x AI, 0)`` kVArh, where
    0.33 is ``sqrt(1 / 0.95^2 - 1)`` and "the square root calculation will be to two decimal
    places"; "only reactive import and reactive export values occurring at times of active import
    are used"; the values are "summated over the billing period".

  The billing period is the calendar month (half-hourly data is settled "in respect of each
  calendar month", ENWL LC14 statement).

**Pence and `Decimal`.** Rates are the statement's own numbers in pence (`gridq.schemas.CdcmTariff`
and `EdcmTariff`). Quantities are converted to `Decimal` through ``repr`` of the float, and every
amount is kept unrounded; a reported figure is rounded to the penny only where it is shown.

**Every line is tagged with how it can be avoided** (H-02, ``docs/bess_modelling_requirements.md``):
by reducing import in a half-hour, by not breaching the agreed capacity, only by re-negotiating the
agreed capacity, or not at all. `assert_unavoidable_lines_save_nothing` enforces that a dispatch
never "saves" a line it cannot move.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

import numpy as np
import pandas as pd

from gridq.schemas import BandWindows, CdcmTariff, ClockWindow, EdcmTariff

__all__ = [
    "CDCM_REACTIVE_THRESHOLD",
    "Avoidability",
    "BillingMonth",
    "CdcmVolumes",
    "ChargeBreakdown",
    "ChargeLine",
    "assert_unavoidable_lines_save_nothing",
    "band_windows_mask",
    "billing_determinants_from_series",
    "cdcm_annual_charges",
    "cdcm_band_labels_from_masks",
    "cdcm_charges_from_series",
    "chargeable_reactive_kvarh",
    "edcm_charges_from_series",
    "exceeded_capacity_kva",
    "network_unit_rate_series",
    "to_penny",
    "windows_from_statement_cell",
]

#: ``sqrt(1 / 0.95^2 - 1)`` "to two decimal places" (ENWL LC14 statement 2026/27, "Application of
#: charges for excess reactive power"; the same clause is para 2.52 of the LPN statement): the
#: reactive allowance per kWh of active import before excess reactive charges apply.
CDCM_REACTIVE_THRESHOLD = Decimal("0.33")

_HUNDRED = Decimal(100)
_THOUSAND = Decimal(1000)
_PUBLISHED_TZ = "Europe/London"
_MINUTES_PER_DAY = 24 * 60


def _dec(value: float | int | Decimal) -> Decimal:
    """A quantity as `Decimal` through its shortest round-trip repr (never binary float noise)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)


def to_penny(amount: Decimal) -> Decimal:
    """Round a £ amount to the penny, half up (the convention a bill line is shown in)."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Avoidability(StrEnum):
    """How a charge line can be reduced (H-02's three tags, with agreed capacity split out)."""

    #: Avoided by reducing import (or reactive import) in the specific half-hour it is charged in.
    HH_IMPORT = "hh_import"
    #: Avoided by not breaching the agreed capacity (exceeded capacity).
    PEAK_KVA = "peak_kva"
    #: Avoided only by re-negotiating the agreed capacity with the DNO, never by dispatch.
    AGREED_CAPACITY = "agreed_capacity"
    #: Not avoidable at all (fixed and residual charges).
    NOT_AVOIDABLE = "not_avoidable"


#: Lines a dispatch can never move: their saving must be exactly zero (H-02).
_DISPATCH_INVARIANT = frozenset({Avoidability.AGREED_CAPACITY, Avoidability.NOT_AVOIDABLE})


@dataclass(frozen=True)
class ChargeLine:
    """One line of a network bill, in the statement's units, with its H-02 tags."""

    component: str
    rate: Decimal
    rate_unit: str
    quantity: Decimal
    quantity_unit: str
    amount_gbp: Decimal
    avoidability: Avoidability
    #: Whether charging the battery from the grid adds to this line.
    charged_on_battery_charging: bool


@dataclass(frozen=True)
class ChargeBreakdown:
    """A network bill as charge lines; ``total_gbp`` is their unrounded sum."""

    methodology: str
    lines: tuple[ChargeLine, ...]
    notes: tuple[str, ...] = ()

    @property
    def total_gbp(self) -> Decimal:
        return sum((line.amount_gbp for line in self.lines), Decimal(0))

    def line(self, component: str) -> ChargeLine:
        """The line named ``component``; ``KeyError`` when the bill has none."""
        for item in self.lines:
            if item.component == component:
                return item
        raise KeyError(component)


@dataclass(frozen=True)
class CdcmVolumes:
    """Annual CDCM charging volumes, in the units of a DNO's CDCM model ('Net revenue summary')."""

    red_mwh: Decimal
    amber_mwh: Decimal
    green_mwh: Decimal
    mpans: Decimal
    import_capacity_kva: Decimal
    exceeded_capacity_kva: Decimal
    reactive_mvarh: Decimal


def _line(
    component: str,
    rate: Decimal,
    rate_unit: str,
    quantity: Decimal,
    quantity_unit: str,
    amount: Decimal,
    avoidability: Avoidability,
    *,
    on_charging: bool,
) -> ChargeLine:
    return ChargeLine(
        component=component,
        rate=rate,
        rate_unit=rate_unit,
        quantity=quantity,
        quantity_unit=quantity_unit,
        amount_gbp=amount,
        avoidability=avoidability,
        charged_on_battery_charging=on_charging,
    )


def cdcm_annual_charges(
    tariff: CdcmTariff, volumes: CdcmVolumes, days: int | Decimal
) -> ChargeBreakdown:
    """A CDCM tariff's annual charges over annual volumes: the DNO model's own algebra.

    The reactive line is omitted (with a note) when the tariff states no reactive rate; it is never
    charged at zero for an unstated rate.
    """
    d = _dec(days)
    lines = [
        _line(
            "red_unit",
            tariff.red_p_per_kwh,
            "p/kWh",
            volumes.red_mwh * _THOUSAND,
            "kWh",
            volumes.red_mwh * _THOUSAND * tariff.red_p_per_kwh / _HUNDRED,
            Avoidability.HH_IMPORT,
            on_charging=True,
        ),
        _line(
            "amber_unit",
            tariff.amber_p_per_kwh,
            "p/kWh",
            volumes.amber_mwh * _THOUSAND,
            "kWh",
            volumes.amber_mwh * _THOUSAND * tariff.amber_p_per_kwh / _HUNDRED,
            Avoidability.HH_IMPORT,
            on_charging=True,
        ),
        _line(
            "green_unit",
            tariff.green_p_per_kwh,
            "p/kWh",
            volumes.green_mwh * _THOUSAND,
            "kWh",
            volumes.green_mwh * _THOUSAND * tariff.green_p_per_kwh / _HUNDRED,
            Avoidability.HH_IMPORT,
            on_charging=True,
        ),
        _line(
            "fixed",
            tariff.fixed_p_per_day,
            "p/MPAN/day",
            volumes.mpans * d,
            "MPAN-days",
            volumes.mpans * tariff.fixed_p_per_day * d / _HUNDRED,
            Avoidability.NOT_AVOIDABLE,
            on_charging=False,
        ),
        _line(
            "capacity",
            tariff.capacity_p_per_kva_day,
            "p/kVA/day",
            volumes.import_capacity_kva * d,
            "kVA-days",
            volumes.import_capacity_kva * tariff.capacity_p_per_kva_day * d / _HUNDRED,
            Avoidability.AGREED_CAPACITY,
            on_charging=False,
        ),
        _line(
            "exceeded_capacity",
            tariff.exceeded_capacity_p_per_kva_day,
            "p/kVA/day",
            volumes.exceeded_capacity_kva * d,
            "kVA-days",
            volumes.exceeded_capacity_kva * tariff.exceeded_capacity_p_per_kva_day * d / _HUNDRED,
            Avoidability.PEAK_KVA,
            on_charging=True,
        ),
    ]
    notes: list[str] = []
    if tariff.reactive_p_per_kvarh is not None:
        lines.append(
            _line(
                "reactive",
                tariff.reactive_p_per_kvarh,
                "p/kVArh",
                volumes.reactive_mvarh * _THOUSAND,
                "kVArh",
                volumes.reactive_mvarh * _THOUSAND * tariff.reactive_p_per_kvarh / _HUNDRED,
                Avoidability.HH_IMPORT,
                on_charging=False,
            )
        )
    else:
        notes.append("no reactive power rate stated by the tariff record; no reactive line priced")
    return ChargeBreakdown(methodology="CDCM", lines=tuple(lines), notes=tuple(notes))


# ------------------------------------------------------------------ calendar (period-start clock)


def _clock(timestamps: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(timestamps)
    return idx.tz_convert(_PUBLISHED_TZ) if idx.tz is not None else idx


def band_windows_mask(windows: BandWindows, timestamps: pd.DatetimeIndex) -> np.ndarray:
    """The half-hours a `BandWindows` covers, on the period-start Europe/London clock.

    The same conventions `gridq.optimisation.tariff_calendar.windowed_levy_mask` applies to a
    levy window (half-open minute windows, weekday = ``dayofweek < 5``, optional months), held to
    agree with it by ``tests/test_hv_tariff_model.py``. A date in ``excluded_dates`` is banded on
    the weekend row, which is how a statement treats a non-working day. A residual `BandWindows`
    (no window at all) covers nothing here: "all other times" is the caller's complement.
    """
    idx = _clock(timestamps)
    minute = idx.hour.to_numpy() * 60 + idx.minute.to_numpy()
    weekday = idx.dayofweek.to_numpy() < 5
    if windows.excluded_dates:
        dates = np.asarray(idx.strftime("%Y-%m-%d"))
        weekday &= ~np.isin(dates, np.asarray(windows.excluded_dates))

    def _in(day: np.ndarray, spans: Iterable[tuple[int, int]]) -> np.ndarray:
        hit = np.zeros(minute.shape, dtype=bool)
        for start, end in spans:
            if start < end:
                hit |= (minute >= start) & (minute < end)
            elif start > end:  # wraps midnight
                hit |= (minute >= start) | (minute < end)
        return day & hit

    weekday_spans = [(w.start_minute, w.end_minute) for w in windows.weekday_windows]
    weekend_spans = [(w.start_minute, w.end_minute) for w in windows.weekend_windows]
    mask = _in(weekday, weekday_spans) | _in(~weekday, weekend_spans)
    if windows.months is not None:
        mask &= np.isin(idx.month.to_numpy(), np.asarray(windows.months))
    return mask


_STATEMENT_SPAN = re.compile(r"(\d{1,2})[.:](\d{2})\s*(?:to|-|–)\s*(\d{1,2})[.:](\d{2})")


def windows_from_statement_cell(text: str | None) -> tuple[ClockWindow, ...]:
    """The `ClockWindow` s a charging statement's time-band cell states, read from its own text.

    Statements write a band as one or more spans per cell, ``"09:00 to 16:00\\n19:00 to 20:30"``
    or ``"00.00 - 09.00\\n20.30 - 24.00"``; both separators and both ``:``/``.`` are read. An empty
    cell is no window. Text that holds no span at all is refused rather than read as "none".
    """
    if text is None or not text.strip():
        return ()
    spans = _STATEMENT_SPAN.findall(text)
    if not spans:
        raise ValueError(f"no 'HH:MM to HH:MM' span in time-band cell {text!r}")
    return tuple(
        ClockWindow(start=f"{int(h1):02d}:{m1}", end=f"{int(h2):02d}:{m2}")
        for h1, m1, h2, m2 in spans
    )


def cdcm_band_labels_from_masks(red: np.ndarray, amber: np.ndarray) -> np.ndarray:
    """``red``/``amber``/``green`` per half-hour from red and amber masks (green is the rest)."""
    labels = np.full(red.shape, "green", dtype=object)
    labels[amber] = "amber"
    labels[red] = "red"
    return labels


def network_unit_rate_series(
    tariff: CdcmTariff | EdcmTariff,
    timestamps: pd.DatetimeIndex,
    band_labels: np.ndarray | None = None,
) -> np.ndarray:
    """The network unit rate (£/kWh) each half-hour pays, every band at its own rate.

    CDCM: ``band_labels`` (``red``/``amber``/``green`` per half-hour, from the site's licence-area
    windows) is required, and each label is priced at its own band's rate. EDCM: the super red
    rate inside ``super_red_window`` and zero outside it (EDCM has no other unit charge).
    """
    n = len(timestamps)
    if isinstance(tariff, EdcmTariff):
        out = np.zeros(n, dtype=float)
        out[band_windows_mask(tariff.super_red_window, timestamps)] = float(
            tariff.super_red_p_per_kwh / _HUNDRED
        )
        return out
    if band_labels is None or len(band_labels) != n:
        raise ValueError("a CDCM unit-rate series needs one red/amber/green label per half-hour")
    rates = {
        "red": float(tariff.red_p_per_kwh / _HUNDRED),
        "amber": float(tariff.amber_p_per_kwh / _HUNDRED),
        "green": float(tariff.green_p_per_kwh / _HUNDRED),
    }
    unknown = set(np.unique(band_labels).tolist()) - set(rates)
    if unknown:
        raise ValueError(f"CDCM band labels must be red/amber/green; got {sorted(unknown)}")
    out = np.zeros(n, dtype=float)
    for band, rate in rates.items():
        out[band_labels == band] = rate
    return out


# ------------------------------------------------------------------ half-hourly determinants


def exceeded_capacity_kva(
    active_import_kwh: np.ndarray,
    reactive_import_kvarh: np.ndarray | None,
    reactive_export_kvarh: np.ndarray | None,
    mic_kva: float,
) -> np.ndarray:
    """Per half-hour ``max(2 x sqrt(AI^2 + max(RI, RE)^2) - MIC, 0)`` kVA (float, for the solver).

    Reactive values count only at times of active import, as the statement says.
    """
    ai = np.asarray(active_import_kwh, dtype=float)
    q = _reactive_at_import(ai, reactive_import_kvarh, reactive_export_kvarh)
    return np.maximum(2.0 * np.sqrt(ai * ai + q * q) - float(mic_kva), 0.0)


def chargeable_reactive_kvarh(
    active_import_kwh: np.ndarray,
    reactive_import_kvarh: np.ndarray | None,
    reactive_export_kvarh: np.ndarray | None,
) -> np.ndarray:
    """Per half-hour chargeable kVArh, ``max(max(RI, RE) - 0.33 x AI, 0)`` where AI > 0, else 0."""
    ai = np.asarray(active_import_kwh, dtype=float)
    q = _reactive_at_import(ai, reactive_import_kvarh, reactive_export_kvarh)
    return np.where(ai > 0.0, np.maximum(q - float(CDCM_REACTIVE_THRESHOLD) * ai, 0.0), 0.0)


def _reactive_at_import(ai: np.ndarray, ri: np.ndarray | None, re: np.ndarray | None) -> np.ndarray:
    zeros = np.zeros(ai.shape, dtype=float)
    q = np.maximum(
        np.asarray(ri, dtype=float) if ri is not None else zeros,
        np.asarray(re, dtype=float) if re is not None else zeros,
    )
    return np.where(ai > 0.0, q, 0.0)


@dataclass(frozen=True)
class BillingMonth:
    """The billing determinants of one calendar month of a half-hourly series."""

    month: str
    #: Days of the month the series covers (the days a per-day charge accrues on).
    days: int
    band_kwh: dict[str, Decimal] = field(default_factory=dict)
    #: The month's maximum half-hourly exceeded capacity (kVA).
    exceeded_kva: Decimal = Decimal(0)
    #: The month's summed chargeable reactive kVArh.
    chargeable_kvarh: Decimal = Decimal(0)


def billing_determinants_from_series(
    timestamps: pd.DatetimeIndex,
    active_import_kwh: np.ndarray,
    band_labels: np.ndarray,
    *,
    mic_kva: float,
    reactive_import_kvarh: np.ndarray | None = None,
    reactive_export_kvarh: np.ndarray | None = None,
) -> tuple[BillingMonth, ...]:
    """Per calendar month: kWh by band, maximum exceeded kVA and summed chargeable kVArh.

    ``band_labels`` names each half-hour's unit-rate band (``red``/``amber``/``green`` for CDCM,
    ``super_red``/``other`` for EDCM); it is the caller's calendar, so the arithmetic here never
    chooses one. Months follow the Europe/London clock.
    """
    idx = _clock(timestamps)
    ai = np.asarray(active_import_kwh, dtype=float)
    if not (len(idx) == len(ai) == len(band_labels)):
        raise ValueError("timestamps, active import and band labels must be the same length")
    exceeded = exceeded_capacity_kva(ai, reactive_import_kvarh, reactive_export_kvarh, mic_kva)
    reactive = chargeable_reactive_kvarh(ai, reactive_import_kvarh, reactive_export_kvarh)
    months = np.asarray(idx.strftime("%Y-%m"))
    dates = np.asarray(idx.strftime("%Y-%m-%d"))
    labels = np.asarray(band_labels, dtype=object)
    out: list[BillingMonth] = []
    for month in sorted(set(months.tolist())):
        sel = months == month
        band_kwh = {
            str(band): _dec(float(ai[sel & (labels == band)].sum()))
            for band in sorted(set(labels[sel].tolist()))
        }
        out.append(
            BillingMonth(
                month=month,
                days=len(set(dates[sel].tolist())),
                band_kwh=band_kwh,
                exceeded_kva=_dec(float(exceeded[sel].max())) if sel.any() else Decimal(0),
                chargeable_kvarh=_dec(float(reactive[sel].sum())),
            )
        )
    return tuple(out)


def _per_day_lines(
    months: Sequence[BillingMonth],
    *,
    fixed_p_per_day: Decimal,
    fixed_unit: str,
    capacity_p_per_kva_day: Decimal,
    exceeded_p_per_kva_day: Decimal,
    mic_kva: float,
) -> list[ChargeLine]:
    days = sum((Decimal(m.days) for m in months), Decimal(0))
    mic = _dec(float(mic_kva))
    # Exceeded capacity: each month's maximum, charged for every day of that month (the breach's
    # whole billing period), never the year's maximum for the year and never a sum of half-hours.
    exceeded_kva_days = sum((m.exceeded_kva * Decimal(m.days) for m in months), Decimal(0))
    return [
        _line(
            "fixed",
            fixed_p_per_day,
            fixed_unit,
            days,
            "days",
            fixed_p_per_day * days / _HUNDRED,
            Avoidability.NOT_AVOIDABLE,
            on_charging=False,
        ),
        _line(
            "capacity",
            capacity_p_per_kva_day,
            "p/kVA/day",
            mic * days,
            "kVA-days",
            mic * capacity_p_per_kva_day * days / _HUNDRED,
            Avoidability.AGREED_CAPACITY,
            on_charging=False,
        ),
        _line(
            "exceeded_capacity",
            exceeded_p_per_kva_day,
            "p/kVA/day",
            exceeded_kva_days,
            "kVA-days",
            exceeded_kva_days * exceeded_p_per_kva_day / _HUNDRED,
            Avoidability.PEAK_KVA,
            on_charging=True,
        ),
    ]


def cdcm_charges_from_series(
    tariff: CdcmTariff, months: Sequence[BillingMonth], *, mic_kva: float
) -> ChargeBreakdown:
    """A CDCM bill from monthly determinants: each band at its own rate, per-day lines by month."""
    lines: list[ChargeLine] = []
    for band, rate in (
        ("red", tariff.red_p_per_kwh),
        ("amber", tariff.amber_p_per_kwh),
        ("green", tariff.green_p_per_kwh),
    ):
        kwh = sum((m.band_kwh.get(band, Decimal(0)) for m in months), Decimal(0))
        lines.append(
            _line(
                f"{band}_unit",
                rate,
                "p/kWh",
                kwh,
                "kWh",
                kwh * rate / _HUNDRED,
                Avoidability.HH_IMPORT,
                on_charging=True,
            )
        )
    lines += _per_day_lines(
        months,
        fixed_p_per_day=tariff.fixed_p_per_day,
        fixed_unit="p/MPAN/day",
        capacity_p_per_kva_day=tariff.capacity_p_per_kva_day,
        exceeded_p_per_kva_day=tariff.exceeded_capacity_p_per_kva_day,
        mic_kva=mic_kva,
    )
    notes: list[str] = []
    kvarh = sum((m.chargeable_kvarh for m in months), Decimal(0))
    if tariff.reactive_p_per_kvarh is not None:
        lines.append(
            _line(
                "reactive",
                tariff.reactive_p_per_kvarh,
                "p/kVArh",
                kvarh,
                "kVArh",
                kvarh * tariff.reactive_p_per_kvarh / _HUNDRED,
                Avoidability.HH_IMPORT,
                on_charging=False,
            )
        )
    else:
        notes.append("no reactive power rate stated by the tariff record; no reactive line priced")
    return ChargeBreakdown(methodology="CDCM", lines=tuple(lines), notes=tuple(notes))


def edcm_charges_from_series(
    tariff: EdcmTariff, months: Sequence[BillingMonth], *, mic_kva: float
) -> ChargeBreakdown:
    """An EDCM import bill from monthly determinants (labels ``super_red`` / anything else).

    Super red kWh at the super red rate; fixed p/day; capacity on the MIC; exceeded capacity at
    each month's maximum for that month's days. EDCM has no reactive charge component.
    """
    kwh = sum((m.band_kwh.get("super_red", Decimal(0)) for m in months), Decimal(0))
    lines = [
        _line(
            "super_red_unit",
            tariff.super_red_p_per_kwh,
            "p/kWh",
            kwh,
            "kWh",
            kwh * tariff.super_red_p_per_kwh / _HUNDRED,
            Avoidability.HH_IMPORT,
            on_charging=True,
        )
    ]
    lines += _per_day_lines(
        months,
        fixed_p_per_day=tariff.fixed_p_per_day,
        fixed_unit="p/day",
        capacity_p_per_kva_day=tariff.capacity_p_per_kva_day,
        exceeded_p_per_kva_day=tariff.exceeded_capacity_p_per_kva_day,
        mic_kva=mic_kva,
    )
    return ChargeBreakdown(
        methodology="EDCM",
        lines=tuple(lines),
        notes=("EDCM has no separate reactive power charge component",),
    )


def assert_unavoidable_lines_save_nothing(before: ChargeBreakdown, after: ChargeBreakdown) -> None:
    """H-02: a line a dispatch cannot move (agreed capacity, fixed) must save exactly £0.

    Raises ``ValueError`` naming every such line whose amount differs before and after.
    """
    after_by_name = {line.component: line for line in after.lines}
    moved = [
        f"{line.component} ({line.avoidability.value}): £{line.amount_gbp} before, "
        f"£{after_by_name[line.component].amount_gbp} after"
        for line in before.lines
        if line.avoidability in _DISPATCH_INVARIANT
        and line.component in after_by_name
        and after_by_name[line.component].amount_gbp != line.amount_gbp
    ]
    missing = [
        line.component
        for line in before.lines
        if line.avoidability in _DISPATCH_INVARIANT and line.component not in after_by_name
    ]
    if moved or missing:
        raise ValueError(
            "H-02: a line no dispatch can avoid shows a saving: "
            + "; ".join(moved + [f"{name} missing after" for name in missing])
        )
