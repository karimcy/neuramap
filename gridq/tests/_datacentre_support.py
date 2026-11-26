"""Shared inputs for the ``gridq.datacentre`` tests.

Every number here is an **illustrative test input**, chosen to exercise the code paths (two phases,
a slip, a fill ramp, a UPS curve, both cooling routes, free cooling, a lag, AR(1) with dips). None
is a default, a sourced figure, or a claim about any real data centre. The ambient series is a
synthetic sine, not weather data.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest

import gridq.extensions as ext
from gridq.contracts.thermal_flex import CoolingTopology
from gridq.datacentre import (
    AmbientSeries,
    Auxiliary,
    CoolingPlant,
    CoolingRegime,
    CoolingResponse,
    DcLoadInputs,
    Dips,
    DistributionLoss,
    DutyPoint,
    Fixed,
    RampPhase,
    Stated,
    Uniform,
    Ups,
    UpsCurve,
    UpsPoint,
    Utilisation,
    Variability,
)

DRAWS = 80
SEED = 20261126
SITE_ID = "site_test"
HORIZON = (date(2027, 3, 1), date(2027, 3, 11))


def user(dist: Any) -> Stated:
    return Stated.user(dist)


def fixed(v: float) -> Stated:
    return user(Fixed(value=v))


def uniform(lo: float, hi: float) -> Stated:
    return user(Uniform(low=lo, high=hi))


def day_shape(base: float, swing: float) -> tuple[float, ...]:
    return tuple(base + swing * math.sin(2 * math.pi * (i - 12) / 48) for i in range(48))


def ambient(days: int = 12, *, wet: bool = False) -> AmbientSeries:
    hours = days * 24
    dry = tuple(10.0 + 6.0 * math.sin(2 * math.pi * (h - 9) / 24) for h in range(hours))
    return AmbientSeries(
        start=datetime(2027, 3, 1),
        interval_minutes=60,
        dry_bulb_c=dry,
        wet_bulb_c=tuple(d - 3.0 for d in dry) if wet else None,
        source="test fixture: synthetic sine, not weather data",
    )


def cooling(**over: Any) -> CoolingPlant:
    spec: dict[str, Any] = dict(
        topology=(
            CoolingTopology.AIR_COOLED_CHILLERS,
            CoolingTopology.FREE_COOLING,
            CoolingTopology.CRAH_CRAC,
        ),
        evaporative_heat_rejection=False,
        load_basis="heat_to_cooled_space",
        ups_heat_fraction=fixed(1.0),
        distribution_heat_fraction=uniform(0.5, 1.0),
        form="C2",
        regimes=(
            CoolingRegime(
                coefficients=(uniform(5.0, 10.0), uniform(0.05, 0.08), fixed(0.5), fixed(0.0)),
                duty_points=None,
            ),
            CoolingRegime(
                coefficients=None,
                duty_points=(
                    DutyPoint(x_kw=200.0, ambient_c=15.0, cooling_kw=uniform(40.0, 48.0)),
                    DutyPoint(x_kw=800.0, ambient_c=15.0, cooling_kw=uniform(150.0, 170.0)),
                    DutyPoint(x_kw=200.0, ambient_c=30.0, cooling_kw=uniform(60.0, 70.0)),
                    DutyPoint(x_kw=800.0, ambient_c=30.0, cooling_kw=uniform(230.0, 260.0)),
                    DutyPoint(x_kw=500.0, ambient_c=22.0, cooling_kw=uniform(120.0, 135.0)),
                ),
            ),
        ),
        switch_temperature_c=uniform(12.0, 16.0),
        capacity_kw=600.0,
        response=CoolingResponse(mode="first_order", time_constant_minutes=5.0),
    )
    spec.update(over)
    return CoolingPlant(**spec)


def make_inputs(**over: Any) -> DcLoadInputs:
    spec: dict[str, Any] = dict(
        site_id=SITE_ID,
        horizon_start=HORIZON[0],
        horizon_end=HORIZON[1],
        phases=(
            RampPhase(
                phase_id="P1",
                energised_on=date(2027, 3, 2),
                racks=20,
                rack_rated_kw=40.0,
                fill_days=uniform(2.0, 4.0),
                slip_increment_days=uniform(0.0, 1.0),
                ups_modules=2,
            ),
            RampPhase(
                phase_id="P2",
                energised_on=date(2027, 3, 6),
                racks=20,
                rack_rated_kw=40.0,
                fill_days=fixed(1.0),
                slip_increment_days=uniform(0.0, 2.0),
                ups_modules=2,
            ),
        ),
        utilisation=Utilisation(
            workload="test training mix",
            weekday=day_shape(0.7, 0.1),
            saturday=day_shape(0.65, 0.05),
            sunday=day_shape(0.6, 0.05),
            level_scale=uniform(0.9, 1.1),
        ),
        ups=Ups(
            redundancy="N+1",
            module_rating_kw=500.0,
            efficiency=UpsCurve(
                points=(
                    UpsPoint(load_fraction=0.25, efficiency=uniform(0.93, 0.95)),
                    UpsPoint(load_fraction=0.5, efficiency=uniform(0.95, 0.97)),
                    UpsPoint(load_fraction=0.75, efficiency=uniform(0.955, 0.975)),
                    UpsPoint(load_fraction=1.0, efficiency=uniform(0.95, 0.97)),
                )
            ),
        ),
        distribution_loss=DistributionLoss(fraction=uniform(0.01, 0.03), base="it_load"),
        auxiliaries=(
            Auxiliary(
                name="lighting_bms",
                basis="fixed_kw",
                value=uniform(10.0, 20.0),
                in_hall=True,
                schedule=None,
            ),
            Auxiliary(
                name="offices",
                basis="fraction_of_energised_capacity",
                value=fixed(0.005),
                in_hall=False,
                schedule=tuple(1.0 if 16 <= i < 36 else 0.3 for i in range(48)),
            ),
        ),
        cooling=cooling(),
        variability=Variability(
            mode="ar1_dips",
            sd_fraction=uniform(0.02, 0.05),
            correlation_minutes=fixed(10.0),
            dips=Dips(depth_fraction=uniform(0.1, 0.2), duration_minutes=2, period_minutes=15),
        ),
        ambient=ambient(),
    )
    spec.update(over)
    return DcLoadInputs(**spec)


@pytest.fixture
def isolated_registries() -> Iterator[None]:
    """Run a test with the chassis registries as CORE ships them, and put them back afterwards.

    The same snapshot-and-restore as ``tests/test_chassis_extension_registries.py``'s ``isolated``:
    without it a test that loads the plug-in would leave ``data_centre`` registered in its xdist
    worker, and CORE's ``test_the_built_ins_are_the_chassis_as_it_was`` would then fail there.
    """
    for registry in ext._ALL:
        registry._ensure_home()
    registries = [r._snapshot() for r in ext._ALL]
    loaded, discovered = dict(ext._loaded), ext._discovered
    try:
        yield
    finally:
        for registry, snapshot in zip(ext._ALL, registries, strict=True):
            registry._restore(snapshot)
        ext._loaded.clear()
        ext._loaded.update(loaded)
        ext._discovered = discovered
