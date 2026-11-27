"""SYNTHETIC fixtures for the WP-TWIN-01 site twin tests. Nothing here describes a real site,
battery, plant, load or measurement.

- The site is ``SYNTHETIC-SITE-HYBRID`` of ``SYNTHETIC-ORG-HYBRID`` (IC-5's and IC-14's fixture
  site), booted 2027-01-04 16:00Z.
- The load is an invented one-day, two-draw minute array (`load_array`): integer watts from an
  arithmetic pattern, IT load 1.2 to 1.6 MW, facility load 1.3 times IT plus 40 kW.
- The battery is the adequacy tests' synthetic battery (`tests/_adequacy_support.bess`) on this
  site.
- The thermal plant and its IC-11 surface are WP-THERM-03's SYNTHETIC fixture
  (`tests/thermal/_thermal_fixture.py`, site ``SYNTHETIC-THERM03-ALPHA``), with that fixture's
  invented driver year as the heat and dry bulb.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

TESTS = Path(__file__).resolve().parents[1]
for _p in (TESTS, TESTS / "thermal", TESTS / "contracts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _thermal_fixture as tfx  # noqa: E402
import _thermal_oracle as oracle  # noqa: E402, F401  (re-exported for the thermal tests)

import _adequacy_support as ads  # noqa: E402
from gridq.adequacy.load import MinuteLoadArray  # noqa: E402
from gridq.site_twin import BessSpec, SiteTwin, ThermalSetup, TwinConfig  # noqa: E402
from gridq.thermal.plant import NumericPlant, Resolved, resolve  # noqa: E402

ORG = "SYNTHETIC-ORG-HYBRID"
SITE = "SYNTHETIC-SITE-HYBRID"
SENDER = "SYNTHETIC-EDGE-TWIN01"
BOOT = datetime(2027, 1, 4, 16, 0, tzinfo=UTC)
DAY0 = date(2027, 1, 4)
SRC = Path(__file__).resolve().parents[2] / "src"


def load_array(site: str = SITE, day0: date = DAY0, days: int = 1) -> MinuteLoadArray:
    """An invented minute load: two draws, integer watts, never a measurement."""
    m = np.arange(days * 1440, dtype=np.int64)
    it = np.stack(
        [
            1_200_000 + (m * 7_919) % 400_001,
            1_250_000 + (m * 104_729) % 350_003,
        ]
    )
    facility = it * 13 // 10 + 40_000
    return MinuteLoadArray(
        site_id=site,
        record_class="synthetic_fixture",
        horizon_start=day0,
        facility_w=facility,
        it_w=it,
    )


def bess_spec(site: str = SITE, **kw: Any) -> BessSpec:
    defaults: dict[str, Any] = {
        "usable": 2000,
        "discharge_kw": 1000,
        "charge_kw": 500,
        "eta": Fraction(19, 20),
        "eta_charge": Fraction(24, 25),
        "initial": 1000,
        "operating": 1500,
    }
    defaults.update(kw)
    defaults["operating"] = min(defaults["operating"], defaults["usable"])
    return BessSpec.from_parameters(ads.bess(site=site, **defaults))


def twin(
    *,
    site: str = SITE,
    org: str = ORG,
    boot: datetime = BOOT,
    load: MinuteLoadArray | None = None,
    draw: int = 0,
    spec: BessSpec | None = None,
    thermal: ThermalSetup | None = None,
) -> SiteTwin:
    load = load if load is not None else load_array(site, boot.date())
    return SiteTwin(
        TwinConfig(
            org_id=org,
            site_id=site,
            boot_utc=boot,
            load=load,
            draw_index=draw,
            load_horizon_start=load.horizon_start,
            shift_days=(load.horizon_start - boot.date()).days,
            bess=spec if spec is not None else bess_spec(site),
            thermal=thermal,
        )
    )


@dataclass
class TwinClock:
    """The edge's clocks in simulation: wall and monotonic time follow the twin's simulated clock
    (the harness advances both together); synchronised to within 20 ms (synthetic)."""

    source: SiteTwin
    boot_id: str = "SYNTHETIC-BOOT-TWIN01"

    def wall(self) -> datetime:
        return self.source.now_utc

    def mono_ms(self) -> int:
        return self.source.ms

    def offset_bound_ms(self) -> int | None:
        return 20


# --------------------------------------------------------------------------- thermal


def numeric_plant() -> NumericPlant:
    res = resolve(tfx.plant())
    assert isinstance(res, Resolved)
    return res.plant


def driver_minutes(start: datetime, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """The fixture's invented driver year read per minute from ``start`` (UTC): the midpoint of
    each half-hour's heat (kWth) and dry-bulb (°C) bounds, so every value lies inside the envelope
    the surface was certified on."""
    d = tfx.drivers()
    j0 = int((start - d.start_utc) // timedelta(minutes=30))
    j = j0 + np.arange(minutes) // 30
    heat = (d.heat_lo_kwth[j] + d.heat_hi_kwth[j]) / 2.0
    assert d.temperature_lo_c is not None and d.temperature_hi_c is not None
    temp = (d.temperature_lo_c[j] + d.temperature_hi_c[j]) / 2.0
    return heat, temp


def thermal_twin(boot: datetime, history_start: datetime, minutes: int) -> SiteTwin:
    """A twin of the WP-THERM-03 fixture site booted at ``boot``, whose IT heat and dry bulb are
    the fixture's driver history from ``history_start`` (the surface is certified over that
    history, so replaying it keeps every input inside the certified envelope)."""
    heat_kw, temp_c = driver_minutes(history_start, minutes + 60)
    days = -(-(minutes + 60 + boot.hour * 60 + boot.minute) // 1440)
    it = np.zeros(days * 1440, dtype=np.int64)
    m0 = boot.hour * 60 + boot.minute
    it[m0 : m0 + heat_kw.size] = np.floor(heat_kw * 1000.0 + 0.5).astype(np.int64)
    facility = it * 13 // 10 + 40_000
    load = MinuteLoadArray(
        site_id=tfx.SITE_ID,
        record_class="synthetic_fixture",
        horizon_start=boot.date(),
        facility_w=facility[None, :],
        it_w=it[None, :],
    )
    setup = ThermalSetup(plant=numeric_plant(), surface=tfx.built("base").surface, ambient_c=temp_c)
    return twin(
        site=tfx.SITE_ID,
        org="SYNTHETIC-ORG-THERM03",
        boot=boot,
        load=load,
        spec=bess_spec(tfx.SITE_ID),
        thermal=setup,
    )
