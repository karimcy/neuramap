"""Half-hourly load synthesis (validation Gate 2, the modelled branch).

Where a site has no metered half-hourly export, generating one is **standard practice, not a
degradation** (`docs/validation_requirements.md` §1 Gate 2). A site is never refused for lacking a
meter — and under **D44** (Olly, 2026-08-12) it is not refused for having no defensible shape
either: `derive_half_hourly` raises `SynthesisError` only when it would have to invent the site's
energy, and Gate 2 then *records* that and the run continues with an honest empty result. The
refusal here is local to this module, never a verdict on the run.

## What this does, and what it refuses to do

It takes a **sourced reference shape** for the business type and scales it so the site's own
**measured totals** are reproduced exactly. The reference supplies only the *shape*; every unit of
energy comes from the site's own invoices or meter reads.

The scaling is deliberately layered, because the honesty of the result depends on which parts are
measured and which are assumed:

| Component | Monthly totals | Annual total only |
|---|---|---|
| Between-month (seasonal) | **MEASURED** per invoice | **MODELLED** from profile factors |
| Day-of-week variation | MODELLED | MODELLED |
| Within-day (half-hourly) shape | MODELLED | MODELLED |

Monthly invoice data is therefore worth far more than an annual figure: it makes the seasonal
swing — the part that drives peak-period value — an observation rather than an assumption. Grundon
has twelve monthly totals per meter, so its synthesised shapes are measured seasonally and modelled
only within the month.

## Hard rules

- **Never displaces metered data.** This module is only reached when no metered series exists; the
  mandate gate (`hh_mandate.py`) decides that, not this module.
- **Exact reconciliation.** Every scaling window reproduces its measured total to the penny, so the
  synthesised series passes the same Gate 3 reconciliation a metered one does.
- **Deterministic.** No clock, no randomness. The same inputs always produce the same file, so a
  synthesised quote is as reproducible as a metered one.
- **Honest provenance.** The reference profile, its source and its status travel onto the result
  and into the admission record, and a modelled basis caps the confidence band at Medium
  regardless of how good the rest of the case is.
- **Never invents a total.** With no measured total to scale to, this module raises rather than
  guessing a consumption figure.
"""

from __future__ import annotations

import calendar
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import gridq
from gridq.extensions import UnknownExtension, load_archetypes
from gridq.ingestion.hh_mandate import HalfHourlyAdmission, HalfHourlyBasis
from gridq.schemas import ConfidenceBand, DerivedAssumption, SiteModel

HALF_HOURS_PER_DAY = 48

#: Identity stamped on the records this module writes (`gridq.schemas.derived`).
_DERIVED_BY = "gridq.ingestion.hh_synthesis"


class ProfileSource(StrEnum):
    """Where a reference shape came from. This is the provenance that caps the confidence band."""

    #: Derived from Elexon profile-class / standard-settlement-configuration data.
    ELEXON_PROFILE_CLASS = "elexon_profile_class"
    #: Derived from metered half-hourly data at comparable sites.
    MEASURED_PEER_SITES = "measured_peer_sites"
    #: A documented engineering archetype awaiting replacement by a sourced curve. Defensible for
    #: shape, but it is an assumption and is labelled as one everywhere it is used.
    ARCHETYPE_PENDING_SOURCE = "archetype_pending_source"


@dataclass(frozen=True)
class ReferenceProfile:
    """A normalised load shape for one business type.

    ``weekday`` / ``saturday`` / ``sunday`` are each 48 half-hourly weights for one day. Only their
    *relative* magnitudes matter — the series is rescaled to the site's measured totals — but they
    are stored with a mean near 1.0 so they read naturally.

    ``month_factors`` are 12 relative monthly weights, used ONLY when no monthly totals are
    supplied. With monthly totals the seasonal shape is measured and these are ignored entirely.
    """

    profile_id: str
    label: str
    source: ProfileSource
    source_ref: str
    weekday: tuple[float, ...]
    saturday: tuple[float, ...]
    sunday: tuple[float, ...]
    month_factors: tuple[float, ...] = (1.0,) * 12
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("weekday", "saturday", "sunday"):
            day = getattr(self, name)
            if len(day) != HALF_HOURS_PER_DAY:
                raise ValueError(
                    f"{self.profile_id}.{name} has {len(day)} values, expected {HALF_HOURS_PER_DAY}"
                )
            if any(v < 0 for v in day):
                raise ValueError(f"{self.profile_id}.{name} contains a negative weight")
        if len(self.month_factors) != 12:
            raise ValueError(f"{self.profile_id}.month_factors must have 12 values")

    def day_shape(self, weekday_index: int) -> tuple[float, ...]:
        """The 48 weights for a day, by ``date.weekday()`` (0 = Monday)."""
        if weekday_index == 5:
            return self.saturday
        if weekday_index == 6:
            return self.sunday
        return self.weekday

    @property
    def is_sourced(self) -> bool:
        return self.source is not ProfileSource.ARCHETYPE_PENDING_SOURCE


def _hourly_to_half_hourly(hourly: list[float]) -> tuple[float, ...]:
    """Expand 24 hourly weights to 48 half-hourly, interpolating between hour centres.

    Interpolation rather than duplication: a step-wise shape puts an artificial discontinuity on
    every hour boundary, which a peak-seeking optimiser will happily exploit.
    """
    out: list[float] = []
    for h in range(24):
        cur = hourly[h]
        nxt = hourly[(h + 1) % 24]
        out.append(cur)
        out.append((cur + nxt) / 2.0)
    return tuple(out)


def _flat(value: float = 1.0) -> tuple[float, ...]:
    return (value,) * HALF_HOURS_PER_DAY


# --------------------------------------------------------------------------- the profile library

# Shape sources. These are engineering archetypes for GB commercial/industrial operating patterns,
# documented and defensible for SHAPE, but they are assumptions and are labelled
# ARCHETYPE_PENDING_SOURCE so nothing downstream can mistake them for measured curves. The
# literature constants register (docs/bess_constants_register.md) replaces them with sourced
# Elexon profile-class curves; the seam is `ProfileSource`, and swapping a profile's source
# upgrades every site that uses it without touching this module's logic.

_SINGLE_SHIFT = _hourly_to_half_hourly(
    # Day-shift plant: overnight base load, ramp from 06:00, full load 08:00-17:00, ramp down.
    [
        0.30,
        0.28,
        0.28,
        0.28,
        0.30,
        0.40,
        0.75,
        1.15,
        1.55,
        1.65,
        1.65,
        1.60,
        1.50,
        1.60,
        1.65,
        1.60,
        1.45,
        1.05,
        0.70,
        0.50,
        0.40,
        0.35,
        0.32,
        0.30,
    ]
)
_DOUBLE_SHIFT = _hourly_to_half_hourly(
    # Two-shift operation: 06:00-14:00 and 14:00-22:00, reduced overnight.
    [
        0.45,
        0.42,
        0.40,
        0.40,
        0.45,
        0.70,
        1.20,
        1.40,
        1.45,
        1.45,
        1.42,
        1.40,
        1.38,
        1.40,
        1.42,
        1.40,
        1.38,
        1.35,
        1.30,
        1.15,
        0.95,
        0.70,
        0.55,
        0.48,
    ]
)
_OFFICE = _hourly_to_half_hourly(
    [
        0.25,
        0.24,
        0.24,
        0.24,
        0.25,
        0.30,
        0.45,
        0.80,
        1.35,
        1.60,
        1.70,
        1.70,
        1.60,
        1.65,
        1.70,
        1.65,
        1.45,
        1.05,
        0.65,
        0.45,
        0.35,
        0.30,
        0.27,
        0.25,
    ]
)

PROFILE_LIBRARY: dict[str, ReferenceProfile] = {
    "waste_mrf": ReferenceProfile(
        profile_id="waste_mrf",
        label="Materials recovery facility (waste sorting plant)",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="Engineering archetype: single-shift sorting plant with overnight base load.",
        weekday=_SINGLE_SHIFT,
        saturday=tuple(v * 0.55 for v in _SINGLE_SHIFT),
        sunday=tuple(v * 0.25 for v in _SINGLE_SHIFT),
        month_factors=(1.08, 1.06, 1.02, 0.97, 0.94, 0.93, 0.92, 0.92, 0.97, 1.02, 1.07, 1.10),
        notes="Sorting lines, balers, conveyors and extraction. Weekday-dominant with a partial "
        "Saturday shift; the overnight floor is standing plant, not production.",
    ),
    "industrial_continuous": ReferenceProfile(
        profile_id="industrial_continuous",
        label="Continuous / near-continuous industrial process",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="Engineering archetype: high load factor, weak diurnal and weekly variation.",
        weekday=_flat(1.0),
        saturday=_flat(0.95),
        sunday=_flat(0.92),
        month_factors=(1.04, 1.03, 1.01, 0.99, 0.98, 0.97, 0.97, 0.97, 0.99, 1.01, 1.03, 1.05),
        notes="Flat, high-baseload plant. The least favourable shape for peak shaving and the "
        "most favourable for availability-based revenue.",
    ),
    "workshop_depot": ReferenceProfile(
        profile_id="workshop_depot",
        label="Vehicle workshop / transport depot",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="Engineering archetype: day-shift maintenance, early-morning fleet activity.",
        weekday=_DOUBLE_SHIFT,
        saturday=tuple(v * 0.50 for v in _DOUBLE_SHIFT),
        sunday=tuple(v * 0.22 for v in _DOUBLE_SHIFT),
        month_factors=(1.10, 1.07, 1.02, 0.96, 0.92, 0.90, 0.90, 0.91, 0.96, 1.03, 1.09, 1.14),
        notes="Compressors, lifts, lighting and heating; winter-weighted through lighting and "
        "space heating.",
    ),
    "office": ReferenceProfile(
        profile_id="office",
        label="Office / administrative building",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="Engineering archetype: weekday office hours with a low out-of-hours floor.",
        weekday=_OFFICE,
        saturday=tuple(v * 0.30 for v in _OFFICE),
        sunday=tuple(v * 0.20 for v in _OFFICE),
        month_factors=(1.12, 1.08, 1.02, 0.95, 0.90, 0.90, 0.92, 0.92, 0.96, 1.03, 1.09, 1.11),
        notes="Strongly weekday- and daytime-weighted; the most peaky of the archetypes.",
    ),
    "non_domestic_unrestricted": ReferenceProfile(
        profile_id="non_domestic_unrestricted",
        label="Generic non-domestic unrestricted (fallback)",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="Generic non-domestic shape; stands in for Elexon Profile Class 3 pending the "
        "sourced curve.",
        weekday=_SINGLE_SHIFT,
        saturday=tuple(v * 0.60 for v in _SINGLE_SHIFT),
        sunday=tuple(v * 0.40 for v in _SINGLE_SHIFT),
        month_factors=(1.10, 1.07, 1.02, 0.96, 0.92, 0.90, 0.90, 0.92, 0.97, 1.03, 1.08, 1.13),
        notes="The deliberate fallback when the business type is unknown. Its use should always "
        "be accompanied by a data ask for the actual operating pattern.",
    ),
}


# The library above is the built-in content of the chassis archetype registry. Another workstream
# adds an archetype from its own package (`gridq.extensions`), not by editing this dict.
for _profile in PROFILE_LIBRARY.values():
    load_archetypes.register_builtin(_profile.profile_id, _profile, version=gridq.__version__)

#: The archetype used when the site's business type is unknown — a **deliberate, labelled choice**
#: (D44). `SiteModel` carries no business-type field by design ("the engine reasons from the site's
#: own data"), and `Counterparty.sector` is free text no mapping can be trusted to read, so an
#: unknown operating pattern gets the generic non-domestic unrestricted shape rather than a guessed
#: sector archetype: it is the least-committal of the library's shapes, stands in for Elexon Profile
#: Class 3, and is documented as the fallback whose use always carries a data ask for the real
#: operating pattern. It is ARCHETYPE_PENDING_SOURCE, so every use is labelled as an assumption.
DEFAULT_PROFILE_ID = "non_domestic_unrestricted"

#: The calendar year a derived series is laid down over when the site has no coverage window of its
#: own. Deliberate and labelled: it matches the existing `data/sites/hh/` convention (every
#: committed modelled series is `_2025_`) and is a fixed constant, never the clock — the same site
#: must synthesise to the same series on every machine, in every year. The year sets weekday/weekend
#: alignment and therefore the DUoS calendar, so it is recorded as a shape-like assumption.
DEFAULT_SYNTHESIS_YEAR = 2025


def default_reference_profile() -> ReferenceProfile:
    """The archetype for a site whose business type is unknown (`DEFAULT_PROFILE_ID`)."""
    return PROFILE_LIBRARY[DEFAULT_PROFILE_ID]


class SynthesisError(ValueError):
    """Synthesis could not proceed without inventing a number."""


@dataclass(frozen=True)
class SynthesisResult:
    """A generated half-hourly series and everything needed to judge it."""

    path: Path
    profile: ReferenceProfile
    year: int
    intervals: int
    total_kwh: float
    peak_kw: float
    #: "monthly invoice totals" or "annual total" — what the shape was scaled to.
    scaling_basis: str
    #: Which components are measured rather than modelled.
    measured_components: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def admission(self) -> HalfHourlyAdmission:
        """The Gate-2 admission record for this series."""
        return HalfHourlyAdmission(
            basis=HalfHourlyBasis.MODELLED,
            file_ref=str(self.path),
            scaled_to=self.scaling_basis,
            reference_profile=f"{self.profile.profile_id} ({self.profile.source.value})",
            notes=self.warnings,
        )


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def synthesise_half_hourly(
    *,
    site_id: str,
    profile: ReferenceProfile | str,
    year: int,
    out_dir: Path,
    monthly_kwh: Mapping[str, float] | None = None,
    annual_kwh: float | None = None,
    peak_kw_cap: float | None = None,
) -> SynthesisResult:
    """Generate a full-year half-hourly series scaled to the site's own measured totals.

    Exactly one of ``monthly_kwh`` (keys ``"YYYY-MM"``) or ``annual_kwh`` must be supplied.
    ``monthly_kwh`` is strongly preferred: it makes the seasonal shape measured rather than
    assumed, and each month reconciles to its own invoice exactly.

    ``peak_kw_cap`` (e.g. the site's MIC/ASC) clips the modelled peak and redistributes the
    clipped energy across the same month, so the monthly total still reconciles. It is a physical
    bound, not a shaping choice.
    """
    ref = _resolve_profile(profile)
    if (monthly_kwh is None) == (annual_kwh is None):
        raise SynthesisError(
            "Supply exactly one of monthly_kwh or annual_kwh. Monthly totals are strongly "
            "preferred — they make the seasonal shape measured rather than assumed."
        )

    warnings: list[str] = []
    if not ref.is_sourced:
        warnings.append(
            f"Reference profile '{ref.profile_id}' is an engineering archetype "
            f"({ref.source.value}), not a sourced curve. The SHAPE is an assumption; only the "
            "totals are measured. Confidence is capped at MEDIUM."
        )

    # --- build the month-by-month energy targets -------------------------------------------
    if monthly_kwh is not None:
        missing = [
            _month_key(year, m) for m in range(1, 13) if _month_key(year, m) not in monthly_kwh
        ]
        if missing:
            raise SynthesisError(
                f"monthly_kwh is missing {len(missing)} month(s) of {year}: {missing}. A partial "
                "year is not scaled up to a full one here — that would invent the missing "
                "months. Supply every month, or pass annual_kwh and accept a modelled season."
            )
        targets = {m: float(monthly_kwh[_month_key(year, m)]) for m in range(1, 13)}
        scaling_basis = f"{len(targets)} monthly invoice totals, {year}"
        measured: tuple[str, ...] = ("annual energy", "seasonal (month-to-month) variation")
    else:
        assert annual_kwh is not None
        weight_sum = sum(ref.month_factors)
        targets = {
            m: float(annual_kwh) * ref.month_factors[m - 1] / weight_sum for m in range(1, 13)
        }
        scaling_basis = f"annual total {annual_kwh:,.0f} kWh, {year}"
        measured = ("annual energy",)
        warnings.append(
            "Only an annual total was supplied, so the seasonal shape is MODELLED from the "
            "profile's month factors. Monthly invoice totals would make it measured — the single "
            "highest-value data ask for this site."
        )

    negative = {k: v for k, v in targets.items() if v < 0}
    if negative:
        raise SynthesisError(f"negative monthly energy target(s): {negative}")

    # --- lay the shape down, month by month --------------------------------------------------
    timestamps: list[datetime] = []
    values_kwh: list[float] = []
    interval_hours = 0.5

    for month in range(1, 13):
        days = calendar.monthrange(year, month)[1]
        month_ts: list[datetime] = []
        month_weights: list[float] = []
        for day in range(1, days + 1):
            start = datetime(year, month, day)
            shape = ref.day_shape(start.weekday())
            for i in range(HALF_HOURS_PER_DAY):
                month_ts.append(start + timedelta(minutes=30 * i))
                month_weights.append(shape[i])

        weight_total = sum(month_weights)
        target = targets[month]
        if weight_total <= 0:
            raise SynthesisError(f"profile {ref.profile_id} has zero weight for month {month}")
        # Scale so this month reproduces its measured total EXACTLY.
        month_kwh = [w * target / weight_total for w in month_weights]

        if peak_kw_cap is not None and peak_kw_cap > 0:
            month_kwh = _apply_peak_cap(month_kwh, peak_kw_cap, interval_hours, warnings, month)

        timestamps.extend(month_ts)
        values_kwh.extend(month_kwh)

    total = sum(values_kwh)
    peak_kw = max(values_kwh) / interval_hours if values_kwh else 0.0

    # --- write it, deterministically ---------------------------------------------------------
    digest = hashlib.sha256(
        "|".join(
            [
                site_id,
                ref.profile_id,
                ref.source.value,
                str(year),
                scaling_basis,
                f"{peak_kw_cap}",
                *(f"{m}:{targets[m]:.4f}" for m in sorted(targets)),
            ]
        ).encode()
    ).hexdigest()[:16]

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{site_id}_modelled_{year}_{digest}.csv"
    lines = ["timestamp,import_kwh"]
    lines.extend(
        f"{ts:%Y-%m-%d %H:%M:%S},{kwh:.6f}" for ts, kwh in zip(timestamps, values_kwh, strict=True)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return SynthesisResult(
        path=path,
        profile=ref,
        year=year,
        intervals=len(values_kwh),
        total_kwh=round(total, 3),
        peak_kw=round(peak_kw, 3),
        scaling_basis=scaling_basis,
        measured_components=measured,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class DerivedHalfHourly:
    """A derived series plus the site that now carries it, stamped and recorded (D44 · N9)."""

    #: The site with the generated series attached and its basis stamped **MODELLED**. This is the
    #: site the run must use — the original is left untouched.
    site: SiteModel
    synthesis: SynthesisResult
    #: The record of what was assumed, ready for the report's confirmation section. Also carried on
    #: ``site.derived_assumptions``, so it enters `input_hash` — a run on a derived shape is not the
    #: same run as one on no shape at all and must not share its identity.
    record: DerivedAssumption

    def admission(self) -> HalfHourlyAdmission:
        return self.synthesis.admission()


def derive_half_hourly(
    site: SiteModel,
    *,
    out_dir: Path,
    profile: ReferenceProfile | str | None = None,
    year: int | None = None,
    release: str | None = None,
) -> DerivedHalfHourly:
    """Derive the missing half-hourly series for ``site`` and stamp it onto the site (**D44**).

    The D44 derivation step for Gate 2: where a site has no metered export, model the shape from a
    reference profile scaled to the site's **own** measured totals, attach it, and record what was
    assumed. Every unit of energy comes from the site's own figures; the reference supplies only the
    shape.

    **This is what stops a modelled series being reported as metered (N9, route 2).** The returned
    site carries ``half_hourly_basis="modelled"``, the reference profile and the scaling basis, so
    the mandate gate reads MODELLED from the record and the confidence ceiling is MEDIUM. Before
    this, synthesis wrote a CSV that nothing stamped, and an unstamped CSV was read as metered.

    ``profile`` defaults to `DEFAULT_PROFILE_ID` — the deliberate, labelled archetype for an unknown
    business type. ``year`` defaults to the site's own coverage window when it has one, else
    `DEFAULT_SYNTHESIS_YEAR`; never the clock, so the same site always derives the same series.

    Raises `SynthesisError` when the site has no total to scale to: with no measured energy there is
    nothing to derive from, and inventing a consumption figure is the one thing this module will not
    do. Gate 2 then records ``not_derivable`` and the run continues honestly.
    """
    annual = site.load_data.annual_kwh or site.annual_consumption_kwh
    if not annual:
        raise SynthesisError(
            "Cannot derive a half-hourly series: this site states no annual or monthly consumption "
            "total to scale a shape to. The shape may be modelled; the site's energy may not be "
            "invented. Supply monthly invoice totals (preferred) or an annual total."
        )
    ref = default_reference_profile() if profile is None else _resolve_profile(profile)
    window = site.load_data.start_date or site.load_data.end_date
    resolved_year = (
        year if year is not None else (window.year if window else DEFAULT_SYNTHESIS_YEAR)
    )

    result = synthesise_half_hourly(
        site_id=site.site_id,
        profile=ref,
        year=resolved_year,
        out_dir=out_dir,
        annual_kwh=float(annual),
        peak_kw_cap=site.connection.mic_kw or site.connection.asc_kw,
    )
    record = DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_reference_profile",
        missing_input="the site's half-hourly load shape (no metered export exists)",
        value=ref.profile_id,
        unit=None,
        basis=(
            f"No metered half-hourly export exists for this site, so the shape is MODELLED from "
            f"reference profile '{ref.profile_id}' ({ref.source.value}: {ref.source_ref}) and "
            f"scaled so the site's own {result.scaling_basis} is reproduced exactly. Measured "
            f"components: {', '.join(result.measured_components)}; everything else "
            f"(day-of-week and within-day shape, and the {resolved_year} calendar alignment that "
            "sets the DUoS bands) is modelled. Generating a shape this way is standard practice "
            "here, not a degradation — but it is evidence about a typical site of this type, not "
            "about this site."
        ),
        # The archetype library is documented engineering shape, not a published curve
        # (ARCHETYPE_PENDING_SOURCE). An empty citation list is the honest record and stays visible.
        sources=[ref.source_ref] if ref.is_sourced else [],
        derivation_method="sourced_default",
        derived_by=_DERIVED_BY,
        derived_on_release=release,
        # Shape-like: the totals are the site's own, only the shape is assumed (`ceiling_for_key`).
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask=(
            "Supply the site's half-hourly meter export, or confirm the operating pattern "
            "(shift times, weekend working, seasonal variation) and the monthly consumption totals "
            "so the seasonal shape becomes measured rather than modelled."
        ),
    )
    load = site.load_data.model_copy(
        update={
            "half_hourly_file_ref": str(result.path),
            "half_hourly_rows": result.intervals,
            # N9, route 2: the basis is STAMPED here. A generated CSV is a file on disk exactly like
            # a metered one, so a series that does not carry its own basis gets read as metered.
            "half_hourly_basis": HalfHourlyBasis.MODELLED.value,
            "half_hourly_reference_profile": f"{ref.profile_id} ({ref.source.value})",
            "half_hourly_scaled_to": result.scaling_basis,
            "validation_warnings": [*site.load_data.validation_warnings, *result.warnings],
        }
    )
    derived = [*(site.derived_assumptions or []), record]
    return DerivedHalfHourly(
        site=site.model_copy(update={"load_data": load, "derived_assumptions": derived}),
        synthesis=result,
        record=record,
    )


def _resolve_profile(profile: ReferenceProfile | str) -> ReferenceProfile:
    if isinstance(profile, str):
        # IC-12 2.0.0: the id resolves through the chassis archetype registry, which holds this
        # module's library plus any archetype another workstream registered from its own package.
        try:
            return load_archetypes.get(profile)
        except UnknownExtension:
            raise SynthesisError(
                f"unknown reference profile {profile!r}; available: {list(load_archetypes.ids())}"
            ) from None
    return profile


def _apply_peak_cap(
    month_kwh: list[float],
    peak_kw_cap: float,
    interval_hours: float,
    warnings: list[str],
    month: int,
) -> list[float]:
    """Clip to a physical power limit, redistributing the clipped energy within the same month.

    The connection cannot pass more than its capacity, so a modelled peak above it is not
    physical. The energy is not deleted — it is pushed into the uncapped intervals, so the month
    still reconciles to its measured total exactly. Iterated because redistribution can push other
    intervals over the cap.
    """
    cap_kwh = peak_kw_cap * interval_hours
    values = list(month_kwh)
    for _ in range(50):
        over = [i for i, v in enumerate(values) if v > cap_kwh + 1e-12]
        if not over:
            return values
        excess = sum(values[i] - cap_kwh for i in over)
        for i in over:
            values[i] = cap_kwh
        headroom = [(i, cap_kwh - values[i]) for i in range(len(values)) if values[i] < cap_kwh]
        room = sum(h for _, h in headroom)
        if room <= 0:
            warnings.append(
                f"month {month}: the measured total cannot fit under a {peak_kw_cap:,.0f} kW "
                "cap — the connection limit and the invoice disagree. Energy left uncapped; "
                "verify the connection capacity."
            )
            return list(month_kwh)
        for i, h in headroom:
            values[i] += excess * (h / room)
    warnings.append(
        f"month {month}: peak-cap redistribution did not converge; cap applied loosely."
    )
    return values
