"""SYNTHETIC test fixture for WP-THERM-03: one invented chilled-water plant and one invented
year of drivers. Nothing here describes a real site, plant, document or measurement.

Every plant figure is a `StandIn` labelled "synthetic test fixture" (so the surface it gives is a
``synthetic_fixture`` with a ``SYNTHETIC-`` site id), and the raw numbers are also exposed as
plain constants (`RAW`) so the independent oracle (`_thermal_oracle.py`) can simulate the plant
without importing the model it checks. The only published figures used are the ASHRAE 2011 A1
recommended preset (opt-in, cited in `gridq.thermal.sources`) for the IT-inlet limits.
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from typing import Any, Final, Literal

import numpy as np

from gridq.contracts._base import unknown
from gridq.contracts.thermal_flex import (
    CoolingTopology,
    CustomerSlaLimit,
    MeasurementPoint,
    ResponseTerms,
    Season,
    TemperatureBand,
)
from gridq.thermal.inputs import (
    Capacitance,
    CoefficientSet,
    DriverSeries,
    LimitInputs,
    PlantInputs,
    StandIn,
    SurfaceRequest,
    stated,
)
from gridq.thermal.sources import a1_preset

SYN: Final = StandIn(
    kind="stand_in",
    reason="synthetic test fixture: no site data is held",
    source="tests/thermal/_thermal_fixture.py",
)
SITE_ID: Final = "SYNTHETIC-THERM03-ALPHA"

#: The invented plant, as plain numbers (kW, kWth, kJ/K, kW/K, °C, minutes).
RAW: Final[dict[str, Any]] = {
    "n": 4,
    "q_unit": 400.0,
    "min_plr": 0.3,
    "standby": 1,
    "min_run": 20,
    "min_off": 15,
    # C2 per regime: b0, b1, b2, b3. Regime 0 (free cooling assists) below T*, regime 1 at/above.
    "coeffs": ((4.0, 0.08, 0.5, 0.003), (5.0, 0.18, 1.2, 0.004)),
    "t_star": 10.0,
    "c_loop": 83_600.0,
    "c_hall": 30_000.0,
    "ua": 80.0,
    "setpoint": 10.0,
    "loop_min": 9.0,
    "loop_max": 14.0,
    "inlet_lo": 22.0,
    "inlet_hi": 24.0,
    "dew_lo": 8.0,
    "dew_hi": 12.0,
    # Limits: ASHRAE A1 recommended (cited preset) and a synthetic SLA band 18.0-26.0 °C.
    "binding_min": 18.0,
    "binding_max": 26.0,
    "max_rh": 60.0,
    "min_dp": 5.5,
    "max_dp": 15.0,
    "rate_c_per_h": 20.0,  # note f, disk drives
}

SLA: Final = CustomerSlaLimit(
    kind="customer_sla",
    temperature=TemperatureBand(
        point=MeasurementPoint.IT_INLET, min_tenths_c=180, max_tenths_c=260
    ),
    humidity=unknown("synthetic fixture: the SLA states no humidity term"),
    evidence=unknown("synthetic fixture: no SLA document exists"),
)


def plant(
    raw: dict[str, Any] = RAW, media: Literal["tape", "disk"] = "disk", **overrides: Any
) -> PlantInputs:
    r = raw
    fields: dict[str, Any] = {
        "site_id": SITE_ID,
        "topology": (
            CoolingTopology.AIR_COOLED_CHILLERS,
            CoolingTopology.FREE_COOLING,
            CoolingTopology.CRAH_CRAC,
        ),
        "evaporative_heat_rejection": False,
        "chiller_count": stated(r["n"], SYN),
        "unit_capacity_kwth": stated(r["q_unit"], SYN),
        "min_part_load_ratio": stated(r["min_plr"], SYN),
        "standby_running": stated(r["standby"], SYN),
        "min_run_min": stated(r["min_run"], SYN),
        "min_off_min": stated(r["min_off"], SYN),
        "form": "C2",
        "regimes": tuple(
            CoefficientSet(kind="coefficients", values=c, provenance=SYN) for c in r["coeffs"]
        ),
        "switch_temperature_c": stated(r["t_star"], SYN),
        "loop_capacitance": Capacitance(kind="capacitance", kj_per_k=stated(r["c_loop"], SYN)),
        "hall_capacitance": Capacitance(kind="capacitance", kj_per_k=stated(r["c_hall"], SYN)),
        "hall_to_loop_ua_kw_per_k": stated(r["ua"], SYN),
        "chw_setpoint_c": stated(r["setpoint"], SYN),
        "loop_band_min_c": stated(r["loop_min"], SYN),
        "loop_band_max_c": stated(r["loop_max"], SYN),
        "inlet_baseline_min_c": stated(r["inlet_lo"], SYN),
        "inlet_baseline_max_c": stated(r["inlet_hi"], SYN),
        "dew_point_min_c": stated(r["dew_lo"], SYN),
        "dew_point_max_c": stated(r["dew_hi"], SYN),
        "limits": LimitInputs.from_preset(a1_preset("recommended"), media=media, customer_sla=SLA),
        "response": ResponseTerms(kind="response_terms", min_notice_min=5, ramp_min=2),
    }
    fields["topology"] = tuple(sorted(fields["topology"]))
    fields.update(overrides)
    return PlantInputs(**fields)


START: Final = datetime(2025, 1, 1, tzinfo=UTC)
N_HALF_HOURS: Final = 366 * 48 + 96


def drivers(seed: int = 20261126, flat: bool = False) -> DriverSeries:
    """An invented year: heat to extract 430-760 kWth with a day shape, and a dry bulb with a
    seasonal and daily swing. Each half-hour carries a +-25 kWth and +-0.6 °C bound.

    ``flat``: the heat held near 590 kWth all day (565-625 kWth with its bound), for the tight
    variant whose chillers have almost no capacity above the peak load."""
    rng = np.random.default_rng(seed)
    j = np.arange(N_HALF_HOURS)
    hours = (j % 48) / 2.0
    day = j / 48.0
    if flat:
        heat = np.clip(590.0 + rng.normal(0, 4, j.size), 590.0, 600.0)
    else:
        heat = 590.0 + 110.0 * np.sin((hours - 9.0) / 24.0 * 2 * np.pi)
        heat = np.clip(heat + rng.normal(0, 15, j.size), 450.0, 740.0)
    temp = (
        11.0
        - 7.0 * np.cos((day - 15.0) / 365.0 * 2 * np.pi)
        + 4.0 * np.sin((hours - 9.0) / 24.0 * 2 * np.pi)
        + rng.normal(0, 0.8, j.size)
    )
    return DriverSeries(
        start_utc=START,
        heat_lo_kwth=heat - 25.0,
        heat_hi_kwth=heat + 25.0,
        temperature_kind="dry_bulb",
        temperature_lo_c=temp - 0.6,
        temperature_hi_c=temp + 0.6,
        provenance=SYN,
    )


SEASONS: Final = (
    Season(season_id="winter", months=(1, 2, 3, 11, 12)),
    Season(season_id="summer", months=(4, 5, 6, 7, 8, 9, 10)),
)


def request(**overrides: Any) -> SurfaceRequest:
    fields: dict[str, Any] = {
        "surface_id": "SYNTHETIC-THERM03-SURFACE",
        "issued_at": datetime(2026, 11, 26, tzinfo=UTC),
        "valid_from": datetime(2026, 12, 1, tzinfo=UTC),
        "valid_until": datetime(2027, 12, 1, tzinfo=UTC),
        "seasons": SEASONS,
        "bands": None,
        "min_days_per_month": 20,
        "unavailable": (),
    }
    fields.update(overrides)
    return SurfaceRequest(**fields)


#: The TIGHT variant (SYNTHETIC, invented like the rest): the same plant made to reach the three
#: checks the base fixture never reaches. A loop band 1.5 K above the setpoint (the loop binds
#: before the hall), the ASHRAE A1 tape-media rate (5 °C/h, the lowest held), a faster hall-loop
#: exchange (the inlet follows the stored heat closely), and chillers only 1 kWth above the peak
#: load of a flat day (`drivers(flat=True)`), so recovery is slow enough to bind on long holds.
RAW_TIGHT: Final[dict[str, Any]] = {
    **RAW,
    "q_unit": 156.5,
    "ua": 400.0,
    "loop_max": 11.5,
    "rate_c_per_h": 5.0,  # note f, tape drives
}
MEDIA: Final[dict[str, Literal["tape", "disk"]]] = {"base": "disk", "tight": "tape"}
VARIANTS: Final = ("base", "tight")


def raw(variant: str) -> dict[str, Any]:
    return RAW if variant == "base" else RAW_TIGHT


def variant_drivers(variant: str) -> DriverSeries:
    return drivers(flat=variant == "tight")


@functools.cache
def built(variant: str = "base") -> Any:
    """The fixture surface of one variant, built once per process."""
    from gridq.thermal import build_surface

    p = plant(raw(variant), MEDIA[variant])
    return build_surface(p, variant_drivers(variant), request())
