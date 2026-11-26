"""Typed inputs to ``thermal_plant_model`` (WP-THERM-03; D163, D165, D167, D168).

**No defaults, no invented data.** Every plant parameter is a `Stated` value with its provenance
or an IC-11 `Unknown` with a reason. Provenance is one of:

- `SiteEvidenced`: the site's own document, as an IC-4 evidence reference (BMS history, a plant
  schedule, a commissioning record);
- `Declared`: a figure from a named published document (cited in ``source``);
- `StandIn`: a value typed in place of site data that is not held. A stand-in is never a site
  figure: any stand-in makes the output a ``synthetic_fixture`` surface with a ``SYNTHETIC-``
  site id derived from the input hash, names the stand-in parameters in the model string, and
  comes back with the `PlantDataRequest` that would replace it (D168 S-a/(3)). Every
  test fixture is a stand-in, labelled synthetic.

The modelled plant is the demo-scope chilled-water family (D163 T-a): air-cooled, or
water-cooled with cooling towers, chillers serving CRAH units, with optional free cooling. Other
topologies are WP-THERM-10 and are refused here.

Units: kW and kWth, kJ/K, kW/K, degrees Celsius, minutes. The driver series are half-hourly from
a UTC instant on a half-hour; each value bounds that half-hour (``*_lo`` and ``*_hi``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gridq.contracts._base import NotApplicable, Unknown
from gridq.contracts.evidence import EvidenceRef
from gridq.contracts.thermal_flex import (
    AshraeLimit,
    CoolingTopology,
    CustomerSlaLimit,
    ResponseTerms,
    Season,
    TimeBand,
    UnavailableReason,
)
from gridq.thermal.sources import AshraePreset

__all__ = [
    "FORM_TERMS",
    "SUPPORTED_TOPOLOGIES",
    "Capacitance",
    "CapacitanceParam",
    "CoefficientSet",
    "Declared",
    "DriverSeries",
    "HumiditySpec",
    "LimitInputs",
    "Param",
    "PlantInputs",
    "Provenance",
    "SiteEvidenced",
    "StandIn",
    "Stated",
    "SurfaceRequest",
    "UnavailableWindow",
    "VolumeTimesRhoCp",
    "stated",
]

#: WP-THERM-04 method v1 §6 power forms and their coefficient counts (b0..b4).
FORM_TERMS: Final = {"C1": 3, "C2": 4, "C3": 5}

#: The demo-scope topologies this model covers (D163 T-a).
SUPPORTED_TOPOLOGIES: Final = frozenset(
    {
        CoolingTopology.AIR_COOLED_CHILLERS,
        CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS,
        CoolingTopology.CRAH_CRAC,
        CoolingTopology.FREE_COOLING,
    }
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------------


class SiteEvidenced(_Frozen):
    """A site figure, from the site's own document registered as IC-4 evidence."""

    kind: Literal["site_evidenced"]
    evidence: EvidenceRef
    #: Where in the document the figure is (table, page, BMS point).
    locator: str = Field(min_length=1)


class Declared(_Frozen):
    """A figure from a named published document (not a site document)."""

    kind: Literal["declared"]
    source: str = Field(min_length=1)


class StandIn(_Frozen):
    """A value typed in place of site data that is not held. Never a site figure."""

    kind: Literal["stand_in"]
    reason: str = Field(min_length=1)
    source: str = Field(min_length=1)


Provenance = Annotated[SiteEvidenced | Declared | StandIn, Field(discriminator="kind")]


class Stated(_Frozen):
    kind: Literal["stated"]
    value: float
    provenance: Provenance


def stated(value: float, provenance: SiteEvidenced | Declared | StandIn) -> Stated:
    return Stated(kind="stated", value=float(value), provenance=provenance)


Param = Annotated[Stated | Unknown, Field(discriminator="kind")]


class CoefficientSet(_Frozen):
    """One regime's power-curve coefficients ``b0..b4`` (as many as the form has)."""

    kind: Literal["coefficients"]
    values: tuple[float, ...] = Field(min_length=3, max_length=5)
    provenance: Provenance


class Capacitance(_Frozen):
    """A heat capacity stated directly, kJ/K."""

    kind: Literal["capacitance"]
    kj_per_k: Stated


class VolumeTimesRhoCp(_Frozen):
    """A heat capacity as a volume times a volumetric heat capacity. No fluid property is held
    by this package: ``rho_cp`` is stated with its own provenance like any other figure."""

    kind: Literal["volume_rho_cp"]
    volume_m3: Stated
    rho_cp_kj_per_m3_k: Stated


CapacitanceParam = Annotated[Capacitance | VolumeTimesRhoCp | Unknown, Field(discriminator="kind")]


# --------------------------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------------------------


class HumiditySpec(_Frozen):
    """The full humidity limit the model enforces (tenths °C, per cent); ``None`` = no limit
    stated. Wider than IC-11 ``HumidityLimit``, which has no minimum RH."""

    min_dew_point_tenths_c: int | None
    max_dew_point_tenths_c: int | None
    min_rh_percent: int | None
    max_rh_percent: int | None


class LimitInputs(_Frozen):
    """The declared IT-safety limits: ASHRAE envelope (and its full humidity limit), customer
    SLA, and the storage media that select note f's rate of change."""

    ashrae: AshraeLimit
    ashrae_humidity: HumiditySpec
    customer_sla: Annotated[CustomerSlaLimit | NotApplicable | Unknown, Field(discriminator="kind")]
    media: Literal["tape", "disk"] | None

    @classmethod
    def from_preset(
        cls,
        preset: AshraePreset,
        *,
        media: Literal["tape", "disk"] | None,
        customer_sla: CustomerSlaLimit | NotApplicable | Unknown,
    ) -> LimitInputs:
        return cls(
            ashrae=preset.ic11(media),
            ashrae_humidity=HumiditySpec(
                min_dew_point_tenths_c=preset.min_dew_point_tenths_c,
                max_dew_point_tenths_c=preset.max_dew_point_tenths_c,
                min_rh_percent=preset.min_rh_percent,
                max_rh_percent=preset.max_rh_percent,
            ),
            customer_sla=customer_sla,
            media=media,
        )


# --------------------------------------------------------------------------------------------
# Plant
# --------------------------------------------------------------------------------------------


class PlantInputs(_Frozen):
    """One site's chilled-water plant, its thermal mass, setpoints and limits.

    Chillers: ``chiller_count`` identical units of ``unit_capacity_kwth``; a running unit cannot
    unload below ``min_part_load_ratio`` of its capacity; the plant's own staging runs
    ``ceil(load / unit) + standby_running`` units (capped at the count). ``min_run_min`` and
    ``min_off_min`` are the controller's anti-cycling times.

    Power: WP-THERM-04 method v1 §6 form (C1/C2/C3) on the heat-to-cooled-space basis (``x`` is
    the heat the chillers extract, kWth). ``T`` is the wet bulb with water-cooled chillers and
    towers or evaporative heat rejection, else the dry bulb. With free cooling, two regimes split
    at ``switch_temperature_c``.

    Thermal mass (D163 M-b): the chilled-water loop node (``loop_capacitance``) and the
    hall node (``hall_capacitance``: air, structure and IT thermal mass the inlet sees), coupled by
    ``hall_to_loop_ua_kw_per_k`` (the CRAH coils). Setpoints: the loop is held at
    ``chw_setpoint_c`` inside its band ``[loop_band_min_c, loop_band_max_c]``; in normal
    operation the IT inlet sits in ``[inlet_baseline_min_c, inlet_baseline_max_c]``.
    """

    site_id: str = Field(min_length=1)
    topology: tuple[CoolingTopology, ...] = Field(min_length=1)
    evaporative_heat_rejection: bool

    chiller_count: Param
    unit_capacity_kwth: Param
    min_part_load_ratio: Param
    standby_running: Param
    min_run_min: Param
    min_off_min: Param

    form: Literal["C1", "C2", "C3"]
    regimes: tuple[Annotated[CoefficientSet | Unknown, Field(discriminator="kind")], ...] = Field(
        min_length=1, max_length=2
    )
    switch_temperature_c: Param | None

    loop_capacitance: CapacitanceParam
    hall_capacitance: CapacitanceParam
    hall_to_loop_ua_kw_per_k: Param

    chw_setpoint_c: Param
    loop_band_min_c: Param
    loop_band_max_c: Param
    inlet_baseline_min_c: Param
    inlet_baseline_max_c: Param
    dew_point_min_c: Param
    dew_point_max_c: Param

    limits: LimitInputs
    response: Annotated[ResponseTerms | Unknown, Field(discriminator="kind")]

    @model_validator(mode="after")
    def _structure(self) -> PlantInputs:
        if list(self.topology) != sorted(set(self.topology)):
            raise ValueError("topology is sorted, without repeats")
        outside = set(self.topology) - SUPPORTED_TOPOLOGIES
        if outside:
            raise ValueError(
                f"topology {sorted(t.value for t in outside)} is outside the demo-scope "
                "chilled-water model (WP-THERM-10)"
            )
        chillers = {
            CoolingTopology.AIR_COOLED_CHILLERS,
            CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS,
        } & set(self.topology)
        if len(chillers) != 1 or CoolingTopology.CRAH_CRAC not in self.topology:
            raise ValueError("the model needs exactly one chiller type and CRAH units")
        free = CoolingTopology.FREE_COOLING in self.topology
        if free != (len(self.regimes) == 2):
            raise ValueError("free_cooling means two regimes (below and at-or-above T*)")
        if free != (self.switch_temperature_c is not None):
            raise ValueError("switch_temperature_c (T*) is stated exactly with free cooling")
        for i, r in enumerate(self.regimes):
            if isinstance(r, CoefficientSet) and len(r.values) != FORM_TERMS[self.form]:
                raise ValueError(f"regime {i}: form {self.form} has {FORM_TERMS[self.form]} terms")
        return self

    @property
    def drives_on_wet_bulb(self) -> bool:
        """WP-THERM-04 method v1 §6.2: the driving ambient temperature."""
        return (
            CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS in self.topology
            or self.evaporative_heat_rejection
        )


# --------------------------------------------------------------------------------------------
# Drivers
# --------------------------------------------------------------------------------------------

HALF_HOUR_MIN: Final = 30


@dataclass(frozen=True, eq=False)
class DriverSeries:
    """Half-hourly bounds on the heat the chillers must extract and on the driving temperature.

    ``heat_*_kwth[j]`` and ``temperature_*_c[j]`` bound every minute of the half-hour starting
    ``start_utc + 30 j`` minutes. ``temperature_*_c`` is ``None`` when the series is not held (a
    missing wet bulb makes every cell unknown). NaN marks a missing half-hour.
    """

    start_utc: datetime
    heat_lo_kwth: np.ndarray
    heat_hi_kwth: np.ndarray
    temperature_kind: Literal["dry_bulb", "wet_bulb"]
    temperature_lo_c: np.ndarray | None
    temperature_hi_c: np.ndarray | None
    provenance: SiteEvidenced | Declared | StandIn
    _digest: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.start_utc.tzinfo is None or self.start_utc.utcoffset() != UTC.utcoffset(None):
            raise ValueError("start_utc must be an aware UTC instant")
        if self.start_utc.minute % HALF_HOUR_MIN or self.start_utc.second:
            raise ValueError("start_utc must fall on a half-hour")
        n = len(self.heat_lo_kwth)
        arrays = [self.heat_hi_kwth, self.temperature_lo_c, self.temperature_hi_c]
        for a in arrays:
            if a is not None and len(a) != n:
                raise ValueError("every driver series has the same length")
        if (self.temperature_lo_c is None) != (self.temperature_hi_c is None):
            raise ValueError("temperature bounds are both held or both missing")
        both = ~(np.isnan(self.heat_lo_kwth) | np.isnan(self.heat_hi_kwth))
        if np.any(self.heat_lo_kwth[both] > self.heat_hi_kwth[both]) or np.any(
            self.heat_lo_kwth[both] < 0
        ):
            raise ValueError("heat bounds must satisfy 0 <= lo <= hi")
        if self.temperature_lo_c is not None and self.temperature_hi_c is not None:
            t_ok = ~(np.isnan(self.temperature_lo_c) | np.isnan(self.temperature_hi_c))
            if np.any(self.temperature_lo_c[t_ok] > self.temperature_hi_c[t_ok]):
                raise ValueError("temperature bounds must satisfy lo <= hi")

    def __len__(self) -> int:
        return len(self.heat_lo_kwth)


# --------------------------------------------------------------------------------------------
# The surface request
# --------------------------------------------------------------------------------------------


class UnavailableWindow(_Frozen):
    """A caller-supplied authority or maintenance window: local minutes, in the given months."""

    months: tuple[int, ...] = Field(min_length=1)
    start_minute: int = Field(ge=0, le=1410)
    end_minute: int = Field(ge=30, le=1440)
    reason_code: UnavailableReason
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def _aligned(self) -> UnavailableWindow:
        if self.start_minute % HALF_HOUR_MIN or self.end_minute % HALF_HOUR_MIN:
            raise ValueError("unavailable windows are on half-hour boundaries")
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be after start_minute")
        return self


class SurfaceRequest(_Frozen):
    """What surface to build: identity, validity, seasons, an optional pinned band grid, the
    caller's minimum coverage, and any unavailable windows."""

    surface_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    seasons: tuple[Season, ...] = Field(min_length=1)
    #: ``None``: the greedy band proposer (D165 G-c). Otherwise a pinned grid on
    #: half-hour edges.
    bands: tuple[TimeBand, ...] | None
    #: A season-slot needs at least this many distinct local dates in **each** of its months.
    min_days_per_month: int = Field(ge=1, le=31)
    unavailable: tuple[UnavailableWindow, ...]

    @model_validator(mode="after")
    def _grid(self) -> SurfaceRequest:
        if self.bands is not None:
            for b in self.bands:
                if b.start_minute % HALF_HOUR_MIN or b.end_minute % HALF_HOUR_MIN:
                    raise ValueError("a pinned band grid is on half-hour edges")
        return self
