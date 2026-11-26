"""What a data-centre load synthesis is built from. Every number is the user's, or a cited preset.

**No field in this module has a numeric default** (the WP-SITE-01 data rule; the test
``test_no_input_field_has_a_numeric_default`` enforces it by introspection). A field whose honest
value may be "none" — a cooling capacity nobody has declared, a schedule that is always on — is
*required and nullable*: the caller writes ``None`` and so states it, rather than inheriting it.

Uncertain inputs are `Stated` values (`gridq.datacentre.distributions`): the user states a
distribution, or takes a cited preset from `gridq.datacentre.presets`. A `Fixed` value is certain
and adds nothing to the bands.

Time is naive local time with 48 half-hours and 1,440 minutes in every day, the same convention as
CORE's ``derive_half_hourly`` writes (`gridq.ingestion.hh_synthesis`). The horizon is
``[horizon_start, horizon_end)`` in whole days.

The structure the inputs fill follows decision D81 (Fable 5.2): facility power is
``L + P_ups_loss + P_dist + sum(P_aux) + P_cool``, the term structure of Zhang & Liu (2022) Eq. (3)
(`gridq.datacentre.sources.S2_EQ3`), with the cooling plant in WP-THERM-04's regression forms so a
plant later metered under that method is described in the same coefficients.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gridq.contracts.thermal_flex import CoolingTopology
from gridq.datacentre.distributions import Stated

__all__ = [
    "FORM_TERMS",
    "HALF_HOURS_PER_DAY",
    "MINUTES_PER_DAY",
    "AmbientSeries",
    "Auxiliary",
    "CoolingPlant",
    "CoolingRegime",
    "CoolingResponse",
    "DcLoadInputs",
    "Dips",
    "DistributionLoss",
    "DutyPoint",
    "RampPhase",
    "Ups",
    "UpsConstant",
    "UpsCurve",
    "UpsPoint",
    "Utilisation",
    "Variability",
]

HALF_HOURS_PER_DAY: Final = 48
MINUTES_PER_DAY: Final = 1440

#: WP-THERM-04 method v1 §6.4 plant forms and their number of coefficients
#: (C1: b0 + b1 x + b2 T; C2: + b3 x T; C3: + b4 x²).
FORM_TERMS: Final[dict[str, int]] = {"C1": 3, "C2": 4, "C3": 5}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_within(stated: Stated, name: str, low: float, high: float) -> None:
    lo, hi = stated.bounds()
    if lo < low or hi > high:
        raise ValueError(
            f"{name} must lie within [{low}, {high}]; its stated range is [{lo}, {hi}]"
        )


def _require_48(values: tuple[float, ...], name: str, low: float, high: float) -> None:
    if len(values) != HALF_HOURS_PER_DAY:
        raise ValueError(f"{name} needs {HALF_HOURS_PER_DAY} half-hourly values, got {len(values)}")
    if any(not (low <= v <= high) or math.isnan(v) for v in values):
        raise ValueError(f"{name} values must lie within [{low}, {high}]")


# --------------------------------------------------------------------------- IT ramp


class RampPhase(_Frozen):
    """One build phase: racks energised together, and how its IT capacity fills.

    The phase's rated IT capacity is ``racks * rack_rated_kw``. There is no density check against
    the cooling topology: no open source held gives a threshold (decision D81).

    From the (slipped) energisation date the installed fraction rises linearly to 1 over
    ``fill_days`` (0 = all at once). ``slip_increment_days`` is this phase's *increment* on the
    slip of the phase before it, so a phase's total slip is the sum of the increments up to and
    including it: phases keep their order whatever is drawn.
    """

    phase_id: str = Field(min_length=1)
    energised_on: date
    racks: int = Field(gt=0)
    rack_rated_kw: float = Field(gt=0)
    fill_days: Stated
    slip_increment_days: Stated
    #: UPS modules that carry this phase at N (the module count before redundancy).
    ups_modules: int = Field(ge=1)

    @model_validator(mode="after")
    def _non_negative(self) -> RampPhase:
        _require_within(self.fill_days, f"{self.phase_id}.fill_days", 0.0, math.inf)
        _require_within(
            self.slip_increment_days, f"{self.phase_id}.slip_increment_days", 0.0, math.inf
        )
        return self

    @property
    def rated_it_kw(self) -> float:
        return self.racks * self.rack_rated_kw


class Utilisation(_Frozen):
    """The IT load as a fraction of energised IT capacity, by half-hour of day and day type.

    The three shapes are required: no within-day shape for any workload is held from an open
    source, so the user states it (a data request until then). ``level_scale`` multiplies every
    shape value and carries the uncertainty on the overall level. A product above 1 is clipped to
    the energised capacity and flagged.
    """

    workload: str = Field(min_length=1)
    weekday: tuple[float, ...]
    saturday: tuple[float, ...]
    sunday: tuple[float, ...]
    level_scale: Stated

    @model_validator(mode="after")
    def _shapes(self) -> Utilisation:
        for name in ("weekday", "saturday", "sunday"):
            _require_48(getattr(self, name), f"utilisation.{name}", 0.0, 1.0)
        _require_within(self.level_scale, "utilisation.level_scale", 0.0, math.inf)
        return self

    def shape(self, weekday_index: int) -> tuple[float, ...]:
        if weekday_index == 5:
            return self.saturday
        if weekday_index == 6:
            return self.sunday
        return self.weekday


# --------------------------------------------------------------------------- power chain


class UpsPoint(_Frozen):
    """One datasheet point: efficiency (output / input) at a fraction of one module's rating."""

    load_fraction: float = Field(gt=0, le=1)
    efficiency: Stated

    @model_validator(mode="after")
    def _efficiency(self) -> UpsPoint:
        lo, hi = self.efficiency.bounds()
        if lo <= 0 or hi > 1:
            raise ValueError("UPS efficiency must lie in (0, 1]")
        return self


class UpsCurve(_Frozen):
    """A part-load efficiency curve, in ascending load fraction, ending at full load (1.0).

    The model works in *loss* space: each point becomes a loss in kW per module, and the loss is
    interpolated linearly between points. Below the lowest point the lowest point's loss is held,
    which overstates the loss there (a stated upper bound); above full load the last segment is
    extended and the interval is flagged as an overload. The points' uncertainties are sampled
    comonotonically (one hypercube dimension for the whole curve): a datasheet is optimistic or
    pessimistic as a whole.
    """

    kind: Literal["curve"] = "curve"
    points: tuple[UpsPoint, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _ascending(self) -> UpsCurve:
        fractions = [p.load_fraction for p in self.points]
        if any(b <= a for a, b in zip(fractions, fractions[1:], strict=False)):
            raise ValueError("UPS curve load fractions must strictly increase")
        if fractions[-1] != 1.0:
            raise ValueError(
                "the UPS curve must end at full load (load_fraction 1.0); a curve that stops short "
                "would have to be extrapolated across the range the plant mostly runs in"
            )
        return self


class UpsConstant(_Frozen):
    """One efficiency at every load: redundancy has no electrical effect, and the output says so."""

    kind: Literal["constant"] = "constant"
    efficiency: Stated

    @model_validator(mode="after")
    def _efficiency(self) -> UpsConstant:
        lo, hi = self.efficiency.bounds()
        if lo <= 0 or hi > 1:
            raise ValueError("UPS efficiency must lie in (0, 1]")
        return self


class Ups(_Frozen):
    """The UPS of each phase. Its output is taken to be the phase's IT load.

    Modules in service per phase: ``n`` at N, ``n + 1`` at N+1, ``2n`` at 2N, sharing the load
    equally, so the per-module load fraction is ``L / (modules * module_rating_kw)``.
    """

    redundancy: Literal["N", "N+1", "2N"]
    module_rating_kw: float = Field(gt=0)
    efficiency: Annotated[UpsCurve | UpsConstant, Field(discriminator="kind")]

    def modules_in_service(self, n: int) -> int:
        if self.redundancy == "N":
            return n
        if self.redundancy == "N+1":
            return n + 1
        return 2 * n


class DistributionLoss(_Frozen):
    """Power distribution loss as a stated fraction of a stated base (S2 Eq. (3)'s αPD·pPD term)."""

    fraction: Stated
    base: Literal["it_load", "ups_input"]

    @model_validator(mode="after")
    def _fraction(self) -> DistributionLoss:
        _require_within(self.fraction, "distribution_loss.fraction", 0.0, 1.0)
        return self


class Auxiliary(_Frozen):
    """A named non-IT, non-cooling load: lighting, BMS, security, offices, heaters.

    ``fixed_kw`` runs from the first energisation; the two fractions scale with energised IT
    capacity or IT load. ``schedule`` is 48 half-hourly multipliers in [0, 1] applied every day;
    ``None`` states that the load runs at its full value at all times. ``in_hall`` says whether its
    heat lands in the cooled space (it then enters the cooling load on the heat basis).
    """

    name: str = Field(min_length=1)
    basis: Literal["fixed_kw", "fraction_of_energised_capacity", "fraction_of_it_load"]
    value: Stated
    in_hall: bool
    schedule: tuple[float, ...] | None

    @model_validator(mode="after")
    def _checks(self) -> Auxiliary:
        _require_within(self.value, f"auxiliary {self.name!r}", 0.0, math.inf)
        if self.schedule is not None:
            _require_48(self.schedule, f"auxiliary {self.name!r} schedule", 0.0, 1.0)
        return self


# --------------------------------------------------------------------------- cooling


class DutyPoint(_Frozen):
    """A design duty point: the plant's electrical power at load term ``x_kw`` and ambient ``T``."""

    x_kw: float = Field(ge=0)
    ambient_c: float
    cooling_kw: Stated

    @model_validator(mode="after")
    def _non_negative(self) -> DutyPoint:
        _require_within(self.cooling_kw, "duty point cooling_kw", 0.0, math.inf)
        return self


class CoolingRegime(_Frozen):
    """The plant in one regime: stated coefficients, or design duty points to solve them from.

    Coefficients are ``b0, b1, ...`` in WP-THERM-04's order for the plant's form, each a `Stated`
    (independent hypercube dimensions). Duty points are solved per draw by least squares in the same
    form; their stated uncertainties move together (one dimension per regime: a design basis is
    optimistic or pessimistic as a whole). There must be at least as many points as coefficients,
    and the residuals of the solve are reported.
    """

    coefficients: tuple[Stated, ...] | None
    duty_points: tuple[DutyPoint, ...] | None

    @model_validator(mode="after")
    def _one_route(self) -> CoolingRegime:
        if (self.coefficients is None) == (self.duty_points is None):
            raise ValueError("a cooling regime states coefficients or duty points, exactly one")
        return self


class CoolingResponse(_Frozen):
    """How plant power follows a change of load inside a half-hour (one-minute series only)."""

    mode: Literal["instantaneous", "first_order"]
    time_constant_minutes: float | None

    @model_validator(mode="after")
    def _tau(self) -> CoolingResponse:
        if self.mode == "first_order":
            if self.time_constant_minutes is None or not self.time_constant_minutes > 0:
                raise ValueError("a first-order response needs a positive time constant")
        elif self.time_constant_minutes is not None:
            raise ValueError("an instantaneous response takes no time constant")
        return self


class CoolingPlant(_Frozen):
    """The cooling plant's electrical power, in WP-THERM-04 method v1 §6's forms.

    ``P_cool = b0 + b1 x + b2 T (+ b3 x T) (+ b4 x²)``, clipped at 0 and at ``capacity_kw``, and 0
    where no IT capacity is energised. ``T`` is the wet bulb where the topology includes
    ``water_cooled_chillers_with_towers`` or heat rejection is evaporative or adiabatic, else the
    dry bulb (method §6.2). With ``free_cooling`` in the topology there are two regimes,
    ``T < T*`` and ``T >= T*``, and ``switch_temperature_c`` states ``T*``.

    ``load_basis``: ``it_load`` uses ``x = L`` (method Path E). ``heat_to_cooled_space`` uses
    ``x = Q = L + f_ups·P_ups_loss + f_dist·P_dist + in-hall auxiliaries`` (Path T), where the two
    heat fractions say how much of each loss is released inside the cooled space.
    """

    topology: tuple[CoolingTopology, ...] = Field(min_length=1)
    evaporative_heat_rejection: bool
    load_basis: Literal["it_load", "heat_to_cooled_space"]
    ups_heat_fraction: Stated | None
    distribution_heat_fraction: Stated | None
    form: Literal["C1", "C2", "C3"]
    regimes: tuple[CoolingRegime, ...] = Field(min_length=1, max_length=2)
    switch_temperature_c: Stated | None
    capacity_kw: float | None = Field(gt=0)

    response: CoolingResponse

    @model_validator(mode="after")
    def _structure(self) -> CoolingPlant:
        free = CoolingTopology.FREE_COOLING in self.topology
        if free != (len(self.regimes) == 2):
            raise ValueError(
                "free_cooling in the topology means two regimes (below and at-or-above T*); "
                "without it there is one"
            )
        if free != (self.switch_temperature_c is not None):
            raise ValueError(
                "switch_temperature_c (T*) is stated exactly when there are two regimes"
            )
        heat = self.load_basis == "heat_to_cooled_space"
        for name in ("ups_heat_fraction", "distribution_heat_fraction"):
            value: Stated | None = getattr(self, name)
            if heat and value is None:
                raise ValueError(f"the heat load basis needs {name}")
            if not heat and value is not None:
                raise ValueError(f"{name} is used only on the heat load basis")
            if value is not None:
                _require_within(value, name, 0.0, 1.0)
        terms = FORM_TERMS[self.form]
        for i, regime in enumerate(self.regimes):
            if regime.coefficients is not None and len(regime.coefficients) != terms:
                raise ValueError(
                    f"regime {i}: form {self.form} has {terms} coefficients, "
                    f"{len(regime.coefficients)} stated"
                )
            if regime.duty_points is not None and len(regime.duty_points) < terms:
                raise ValueError(
                    f"regime {i}: form {self.form} needs at least {terms} duty points to solve "
                    f"its {terms} coefficients; {len(regime.duty_points)} stated"
                )
        return self

    @property
    def drives_on_wet_bulb(self) -> bool:
        """WP-THERM-04 method v1 §6.2: the driving ambient temperature."""
        return (
            CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS in self.topology
            or self.evaporative_heat_rejection
        )


# --------------------------------------------------------------------------- one-minute variability


class Dips(_Frozen):
    """A synchronous dip train (checkpoints, all-reduce stalls): a square wave on absolute minutes.

    Every ``period_minutes`` the IT load drops by ``depth_fraction`` for ``duration_minutes``. Each
    draw gets its own phase. The train is de-meaned within each half-hour, so it never moves a
    half-hourly value.
    """

    depth_fraction: Stated
    duration_minutes: int = Field(gt=0)
    period_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def _checks(self) -> Dips:
        _require_within(self.depth_fraction, "dips.depth_fraction", 0.0, 1.0)
        if self.duration_minutes >= self.period_minutes:
            raise ValueError("a dip must be shorter than its period")
        return self


class Variability(_Frozen):
    """How IT load moves inside a half-hour (decision D82).

    ``none``: the one-minute IT load is the half-hourly value held (a step). ``ar1``: a stationary
    AR(1) relative deviation with stated SD (before conditioning) and correlation time, conditioned
    so every half-hour's mean deviation is exactly zero. ``ar1_dips`` adds a dip train.
    """

    mode: Literal["none", "ar1", "ar1_dips"]
    sd_fraction: Stated | None
    correlation_minutes: Stated | None
    dips: Dips | None

    @model_validator(mode="after")
    def _by_mode(self) -> Variability:
        ar = self.mode in ("ar1", "ar1_dips")
        if ar != (self.sd_fraction is not None) or ar != (self.correlation_minutes is not None):
            raise ValueError(
                "sd_fraction and correlation_minutes are stated exactly in the AR(1) modes"
            )
        if (self.mode == "ar1_dips") != (self.dips is not None):
            raise ValueError("dips are stated exactly in mode ar1_dips")
        if self.sd_fraction is not None:
            _require_within(self.sd_fraction, "variability.sd_fraction", 0.0, math.inf)
        if self.correlation_minutes is not None:
            lo, _ = self.correlation_minutes.bounds()
            if not lo > 0:
                raise ValueError("variability.correlation_minutes must be positive")
        return self


# --------------------------------------------------------------------------- ambient


class AmbientSeries(_Frozen):
    """Outdoor temperature over the horizon, from a supplied series (no weather is fetched here).

    Hourly values are held across both half-hours. Nothing is interpolated to minutes: the
    one-minute series holds each half-hour's ambient (decision D82).
    """

    start: datetime
    interval_minutes: Literal[30, 60]
    dry_bulb_c: tuple[float, ...] = Field(min_length=1)
    wet_bulb_c: tuple[float, ...] | None
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self) -> AmbientSeries:
        if self.start.tzinfo is not None:
            raise ValueError("ambient start is naive local time, like the half-hourly series")
        if self.start.minute % self.interval_minutes or self.start.second or self.start.microsecond:
            raise ValueError("ambient start must sit on an interval boundary")
        if any(math.isnan(v) for v in self.dry_bulb_c):
            raise ValueError("dry bulb series contains NaN")
        if self.wet_bulb_c is not None:
            if len(self.wet_bulb_c) != len(self.dry_bulb_c):
                raise ValueError("wet bulb and dry bulb series differ in length")
            if any(math.isnan(v) for v in self.wet_bulb_c):
                raise ValueError("wet bulb series contains NaN")
            if any(w > d + 1e-9 for w, d in zip(self.wet_bulb_c, self.dry_bulb_c, strict=True)):
                raise ValueError("a wet bulb temperature exceeds its dry bulb")
        return self

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.interval_minutes * len(self.dry_bulb_c))


# --------------------------------------------------------------------------- the whole


class DcLoadInputs(_Frozen):
    """Everything one synthesis needs. Its canonical JSON is the input half of the series hash."""

    site_id: str = Field(min_length=1)
    horizon_start: date
    horizon_end: date
    phases: tuple[RampPhase, ...] = Field(min_length=1)
    utilisation: Utilisation
    ups: Ups
    distribution_loss: DistributionLoss
    #: May be empty, which states that there are none.
    auxiliaries: tuple[Auxiliary, ...]
    cooling: CoolingPlant
    variability: Variability
    ambient: AmbientSeries

    @model_validator(mode="after")
    def _coherent(self) -> DcLoadInputs:
        if not self.horizon_end > self.horizon_start:
            raise ValueError("horizon_end must be after horizon_start")
        ids = [p.phase_id for p in self.phases]
        if len(set(ids)) != len(ids):
            raise ValueError("phase ids must be unique")
        dates = [p.energised_on for p in self.phases]
        if dates != sorted(dates):
            raise ValueError("phases must be listed in order of planned energisation")
        names = [a.name for a in self.auxiliaries]
        if len(set(names)) != len(names):
            raise ValueError("auxiliary names must be unique")
        start = datetime.combine(self.horizon_start, datetime.min.time())
        end = datetime.combine(self.horizon_end, datetime.min.time())
        if self.ambient.start > start or self.ambient.end < end:
            raise ValueError(
                f"the ambient series covers {self.ambient.start} to {self.ambient.end}; the "
                f"horizon needs {start} to {end}. Supply the series; none is fetched or filled."
            )
        if self.cooling.drives_on_wet_bulb and self.ambient.wet_bulb_c is None:
            raise ValueError(
                "this cooling topology is driven by the wet bulb (WP-THERM-04 method v1 §6.2); "
                "the ambient series must carry wet_bulb_c"
            )
        return self

    @property
    def days(self) -> int:
        return (self.horizon_end - self.horizon_start).days
