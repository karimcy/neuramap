"""The site twin: one SYNTHETIC site's electrical, battery and cooling state on a simulated clock,
served over Modbus TCP (WP-TWIN-01, spec B7-01).

Each simulated second ``[s, s + 1)``:

1. the load model gives the minute's facility and IT watts (`gridq.adequacy.load`, one explicit
   draw: the twin never picks a draw for the caller);
2. the battery follows the setpoint in force, by kernel-segment replay (`electrical`);
3. the cooling plant follows any accepted IC-11 instruction, by the exact node step
   (`thermal`);
4. the site meter reads ``facility - cooling reduction + battery`` (load convention).

A register write takes effect from the next whole second (a write at exactly ``s`` from ``s``).
The registers at time ``t`` hold the average powers of the last whole second ``[t - 1, t)`` and the
state of energy and temperatures at the instant ``t``. The twin obeys its battery setpoint and does
not enforce the import limit; it counts the seconds the meter read above it
(``exceedance_s``).

The twin serves SYNTHETIC- sites only: its config refuses any other site, organisation or load.
"""

from __future__ import annotations

import functools
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np

from gridq.adequacy.load import LoadSource, map_scenario_minutes
from gridq.contracts.thermal_flex import FlexInstruction
from gridq.site_twin.electrical import BessSecond, BessSpec, BessTwin
from gridq.site_twin.modbus_server import RegisterBank, TwinModbusServer
from gridq.site_twin.registers import commissioning_document, points_for
from gridq.site_twin.thermal import FlexVerdict, ThermalSecond, ThermalSetup, ThermalTwin

__all__ = ["SiteTwin", "TwinConfig", "TwinSecond"]

_SYN = "SYNTHETIC-"


def _w(kw: float) -> int:
    """kW to integer W, round half up (the adequacy load's `quantise_w` rule)."""
    return math.floor(kw * 1000.0 + 0.5)


def _mdeg(c: float) -> int:
    return math.floor(c * 1000.0 + 0.5)


@dataclass(frozen=True)
class TwinConfig:
    org_id: str
    site_id: str
    #: Boot instant, on a UTC half-hour (the load is read per half-hour of the clock).
    boot_utc: datetime
    load: LoadSource
    #: Which load draw the twin replays. Required: there is no default draw.
    draw_index: int
    #: The load horizon's first local date, and the whole-day shift from the boot date to it.
    load_horizon_start: date
    shift_days: int
    bess: BessSpec
    thermal: ThermalSetup | None = None

    def __post_init__(self) -> None:
        if not (self.org_id.startswith(_SYN) and self.site_id.startswith(_SYN)):
            raise ValueError("the twin serves SYNTHETIC- organisations and sites only")
        ident = self.load.identity
        if ident.record_class != "synthetic_fixture" or ident.site_id != self.site_id:
            raise ValueError("the twin replays a synthetic load of its own site only")
        if not 0 <= self.draw_index < ident.draws:
            raise ValueError("draw_index names no draw of the load")
        t = self.boot_utc
        if t.tzinfo is None or t.utcoffset() != timedelta(0):
            raise ValueError("boot_utc is a UTC instant")
        if t.minute % 30 or t.second or t.microsecond:
            raise ValueError("the twin boots on a UTC half-hour")
        if self.thermal is not None and self.thermal.surface.site_id != self.site_id:
            raise ValueError("the thermal surface names another site")


@dataclass(frozen=True)
class TwinSecond:
    """What happened in one simulated second (W, load convention)."""

    s: int
    facility_w: int
    it_w: int
    setpoint_w: int
    bess: BessSecond
    thermal: ThermalSecond | None
    reduction_w: int
    import_w: int


@dataclass
class _Registers:
    bess_w: int = 0
    import_w: int = 0
    it_w: int = 0
    chiller_w: int = 0
    loop_mdeg: int = 0
    inlet_mdeg: int = 0
    ambient_mdeg: int = 0


@dataclass
class _Writes:
    pending: list[tuple[int, str, int]] = field(default_factory=list)
    echo: dict[str, int] = field(default_factory=dict)


class SiteTwin:
    """One synthetic site. `advance` moves its clock; `serve` puts it on the wire."""

    def __init__(self, config: TwinConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.ms = 0
        self.s = 0
        self.battery = BessTwin(config.bess)
        self.cooling = (
            ThermalTwin(config.thermal, config.boot_utc) if config.thermal is not None else None
        )
        self.setpoint_w = 0
        self.import_limit_w: int | None = None
        self.exceedance_s = 0
        self.seconds: list[TwinSecond] = []
        self._writes = _Writes()
        self._load_block: tuple[int, np.ndarray, np.ndarray] | None = None
        self._reg = _Registers()
        facility, it = self.load_at(0)
        self._reg.import_w, self._reg.it_w = facility, it
        if self.cooling is not None:
            self._thermal_registers(0, it, None, 0.0, 0.0)

    # ---------------------------------------------------------------- inputs

    @property
    def now_utc(self) -> datetime:
        return self.config.boot_utc + timedelta(milliseconds=self.ms)

    def load_at(self, minute: int) -> tuple[int, int]:
        """Facility and IT watts of the twin's ``minute`` since boot, from its load draw."""
        block = minute // 30
        if self._load_block is None or self._load_block[0] != block:
            c = self.config
            idx, _, _ = map_scenario_minutes(
                (c.boot_utc + timedelta(minutes=30 * block)).astimezone(UTC),
                30,
                horizon_start=c.load_horizon_start,
                shift_days=c.shift_days,
            )
            lo = int(idx.min())
            f, i = c.load.minutes(lo, int(idx.max()) + 1)
            row = c.draw_index
            self._load_block = (block, f[row, idx - lo].copy(), i[row, idx - lo].copy())
        _, f_w, i_w = self._load_block
        return int(f_w[minute % 30]), int(i_w[minute % 30])

    def offer_flex(self, instruction: FlexInstruction) -> FlexVerdict:
        """An IC-11 instruction for the cooling plant (scenario input: ThermaFlex's own interface
        is not yet agreed)."""
        if self.cooling is None:
            return FlexVerdict(False, ("no_thermal_plant",))
        with self.lock:
            return self.cooling.offer(instruction, now_s=-(-self.ms // 1000))

    # ---------------------------------------------------------------- writes

    def write(self, write_class: str, value: int) -> None:
        """A holding-register write in process (what a Modbus write to the point does)."""
        if write_class not in ("bess_setpoint", "import_limit"):
            raise ValueError(f"the twin has no writable {write_class} point")
        with self.lock:
            self._write(write_class, value)

    def _write(self, write_class: str, value: int) -> None:
        self._writes.echo[write_class] = value
        self._writes.pending.append((self.ms, write_class, value))

    def _latch(self, s: int) -> None:
        keep: list[tuple[int, str, int]] = []
        for t, write_class, value in self._writes.pending:
            if t <= s * 1000:
                if write_class == "bess_setpoint":
                    self.setpoint_w = value
                else:
                    self.import_limit_w = value
            else:
                keep.append((t, write_class, value))
        self._writes.pending = keep

    # ---------------------------------------------------------------- the clock

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("the simulated clock only moves forward")
        with self.lock:
            self.ms += ms
            while (self.s + 1) * 1000 <= self.ms:
                self._second(self.s)
                self.s += 1

    def _thermal_registers(
        self, minute: int, it_w: int, chiller_kw: float | None, w: float, a: float
    ) -> None:
        """Cooling registers; ``chiller_kw`` ``None`` reads the baseline curve (at boot)."""
        assert self.config.thermal is not None
        setup = self.config.thermal
        plant = setup.plant
        t = float(setup.ambient_c[min(minute, len(setup.ambient_c) - 1)])
        if chiller_kw is None:
            xx, tt = np.asarray([it_w / 1000.0]), np.asarray([t])
            chiller_kw = float(plant.curve.power(xx, tt, plant.curve.regime(tt))[0])
        self._reg.chiller_w = _w(chiller_kw)
        self._reg.loop_mdeg = _mdeg(plant.setpoint_c + w)
        self._reg.inlet_mdeg = _mdeg(plant.inlet_hi_c + a)
        self._reg.ambient_mdeg = _mdeg(t)

    def _second(self, s: int) -> None:
        self._latch(s)
        minute = s // 60
        facility, it = self.load_at(minute)
        sp = self.setpoint_w
        bess = self.battery.second(sp, facility, minute_start=s % 60 == 0)
        th: ThermalSecond | None = None
        reduction = 0
        if self.cooling is not None:
            th = self.cooling.second(s, it / 1000.0, (facility - it) / 1000.0)
            reduction = _w(th.reduction_kw)
        imp = facility - reduction + bess.power_w
        if self.import_limit_w is not None and imp > self.import_limit_w:
            self.exceedance_s += 1
        self.seconds.append(TwinSecond(s, facility, it, sp, bess, th, reduction, imp))
        r = self._reg
        r.bess_w, r.import_w, r.it_w = bess.power_w, imp, it
        if th is not None:
            self._thermal_registers((s + 1) // 60, it, th.chiller_kw, th.loop_dev_k, th.hall_dev_k)

    # ---------------------------------------------------------------- the wire

    def _readers(self) -> dict[str, Callable[[], int]]:
        usable = self.config.bess.usable_j
        r = self._reg
        return {
            "bess.bess-1.active_power.device_reported.whole": lambda: r.bess_w,
            "bess.bess-1.state_of_energy.device_reported.whole": lambda: self.battery.soe_j // 3600,
            "bess.bess-1.state_of_charge.device_reported.whole": lambda: (
                self.battery.soe_j * 10_000 // usable
            ),
            "bess.bess-1.active_power.setpoint_echo.whole": lambda: self._writes.echo.get(
                "bess_setpoint", 0
            ),
            "site_boundary.main.active_power.setpoint_echo.whole": lambda: self._writes.echo.get(
                "import_limit", 0
            ),
            "site_boundary.main.active_power.operational_meter.whole": lambda: r.import_w,
            "it_load.hall-1.active_power.scada.whole": lambda: r.it_w,
            "chiller.plant-1.active_power.device_reported.whole": lambda: r.chiller_w,
            "chiller.plant-1.temperature.device_reported.supply": lambda: r.loop_mdeg,
            "crah.hall-1.temperature.device_reported.supply": lambda: r.inlet_mdeg,
            "chiller.plant-1.temperature.device_reported.ambient": lambda: r.ambient_mdeg,
        }

    def bank(self) -> RegisterBank:
        readers_by_key = self._readers()
        readers: dict[tuple[int, str, int], Callable[[], int]] = {}
        writers: dict[tuple[int, int], Callable[[int], None]] = {}
        for p in points_for(self.cooling is not None):
            readers[(p.unit_id, p.table, p.address)] = readers_by_key[p.point_key]
            if p.write_class is not None:
                writers[(p.unit_id, p.address)] = functools.partial(self._write, p.write_class)
        return RegisterBank(readers, writers, self.lock)

    def serve(self, port: int = 0) -> TwinModbusServer:
        """Start serving the twin's registers on 127.0.0.1."""
        return TwinModbusServer(self.bank(), port).start()

    def commissioning(self, sender_id: str) -> str:
        """The twin's commissioning set (JSON) for a gateway."""
        return commissioning_document(
            org_id=self.config.org_id,
            site_id=self.config.site_id,
            sender_id=sender_id,
            usable_wh=self.config.bess.usable_j // 3600,
            thermal=self.cooling is not None,
        )
