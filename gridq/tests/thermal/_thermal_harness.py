"""The WP-THERM-05 test harness: the `DcController` in closed loop with the independent plant of
`_thermal_closed_loop` on the SYNTHETIC fixture (D208). Everything here is SYNTHETIC.

**Time.** The fixture's driver year starts 2025-01-01; the fixture surface is valid from
2026-12-01 to 2027-12-01. A run in 2027 reads the driver half-hour exactly 730 days earlier
(the same calendar date and, away from the clock-change weeks, the same local time), so a run
through a start slot sees load and ambient the certificate's envelope of that slot covers.
`SAFE_DAYS` keeps the starts off the clock-change weeks of both years.

**Driver paths.** ``random``: every minute's load and dry bulb uniform inside its half-hour
bounds. ``worst_low_t`` / ``worst_high_t``: the lowest load the half-hour allows while any
instruction's window is open (the largest deficit per kW, the least part-load headroom) and the
highest otherwise (the least spare for recovery), at the lowest or highest dry bulb.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import _thermal_fixture as fx
import numpy as np
from _thermal_closed_loop import Minute, Plant

from gridq.contracts._base import not_applicable
from gridq.contracts.thermal_flex import (
    FlexAcknowledgement,
    FlexInstruction,
    InstructionIssuer,
    ThermalFlexAvailability,
    availability_ref,
)
from gridq.thermal import thermaflex_dc_controller
from gridq.thermal.comfort_cost import ComfortCostPolicy, Observation
from gridq.thermal.controller import DcController, ObjectiveMode, StepRecord

SHIFT = timedelta(days=730)
#: Days of 2027 (day-of-year) a run may start on: inside the surface's validity, inside the
#: driver year, two days clear of either year's clock changes and of the series' end.
SAFE_DAYS = tuple(d for d in range(2, 330) if not (84 <= d <= 92 or 295 <= d <= 307))
PATHS = ("random", "worst_low_t", "worst_high_t")
MINUTE = timedelta(minutes=1)


def day_start(day_of_year: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2027, 1, 1, tzinfo=UTC) + timedelta(
        days=day_of_year - 1, hours=hour, minutes=minute
    )


def instruction(
    surface: ThermalFlexAvailability,
    start: datetime,
    duration: int,
    kw: int,
    ident: str,
    notice_min: int = 30,
    deadline_min: int = 10,
    site_id: str | None = None,
) -> FlexInstruction:
    return FlexInstruction(
        instruction_id=ident,
        site_id=site_id or surface.site_id,
        availability=availability_ref(surface),
        issued_at=start - timedelta(minutes=notice_min),
        issuer_role=InstructionIssuer.NEURA_DISPATCH,
        issuer_id="synthetic-test",
        on_behalf_of=not_applicable("synthetic test: the site's own cost"),
        stream=not_applicable("synthetic test: the site's own cost"),
        reduction_kw=kw,
        start=start,
        duration_min=duration,
        ack_deadline=start - timedelta(minutes=min(deadline_min, notice_min - 1)),
    )


@functools.cache
def _bounds(variant: str) -> list[tuple[float, float, float, float]]:
    drv = fx.variant_drivers(variant)
    assert drv.temperature_lo_c is not None and drv.temperature_hi_c is not None
    rows = zip(
        drv.heat_lo_kwth, drv.heat_hi_kwth, drv.temperature_lo_c, drv.temperature_hi_c, strict=True
    )
    return [(float(a), float(b), float(c), float(d)) for a, b, c, d in rows]


@dataclass
class Drive:
    """Load and dry bulb for each minute of a run, from the fixture's driver year."""

    variant: str
    mode: str
    rng: np.random.Generator
    windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    #: Optional override: ``(minute, x, t) -> (x, t)`` (out-of-envelope tests).
    bend: Callable[[int, float, float], tuple[float, float]] | None = None

    def at(self, i: int, at: datetime) -> tuple[float, float]:
        j = int((at - SHIFT - fx.START) // timedelta(minutes=30))
        xl, xh, tl, th = _bounds(self.variant)[j]
        if self.mode == "random":
            x, t = float(self.rng.uniform(xl, xh)), float(self.rng.uniform(tl, th))
        else:
            holding = any(s <= at < e for s, e in self.windows)
            x = xl if holding else xh
            t = tl if self.mode.endswith("low_t") else th
        if self.bend is not None:
            x, t = self.bend(i, x, t)
        return x, t


@dataclass
class Run:
    controller: DcController
    plant: Plant
    steps: list[StepRecord]
    acks: dict[str, FlexAcknowledgement]
    t0: datetime
    #: The load and dry bulb of each minute.
    xs: list[float]
    ts: list[float]

    @property
    def log(self) -> list[Minute]:
        return self.plant.log


Hook = Callable[[int, DcController, Plant], None]


def controller(
    variant: str,
    policy: ComfortCostPolicy | None = None,
    mode: ObjectiveMode = ObjectiveMode.IT_SAFETY_FLEX,
) -> DcController:
    return thermaflex_dc_controller(
        fx.built(variant),
        "SYNTHETIC-THERM05-CTRL",
        policy=policy,
        mode=mode,
        authority_granted=mode is ObjectiveMode.IT_SAFETY_FLEX,
    )


def simulate(
    variant: str,
    t0: datetime,
    minutes: int,
    mode: str,
    seed: int,
    sends: Sequence[tuple[datetime, FlexInstruction]] = (),
    ctrl: DcController | None = None,
    hooks: dict[int, Hook] | None = None,
    blind: Callable[[int], bool] | None = None,
    bend: Callable[[int, float, float], tuple[float, float]] | None = None,
    forced: Sequence[tuple[datetime, int, int]] = (),
) -> Run:
    """Run the closed loop for ``minutes`` from ``t0``. ``sends`` are delivered at their receipt
    instants (each before the step of the minute they fall in); ``forced`` books events without
    admission (``(start, duration, kW)``), the admission-bypass runs."""
    ctrl = ctrl or controller(variant)
    raw = fx.raw(variant)
    windows = [(i.start, i.end) for _, i in sends] + [
        (s, s + timedelta(minutes=d)) for s, d, _ in forced
    ]
    drive = Drive(variant, mode, np.random.default_rng(seed), windows, bend)
    x0, _ = drive.at(-1, t0)
    plant = Plant(raw, x0)
    for k, (s, d, kw) in enumerate(forced):
        ctrl.force_event(s, d, kw, f"SYNTHETIC-FORCED-{k}")
    queue = sorted(sends, key=lambda p: p[0])
    acks: dict[str, FlexAcknowledgement] = {}
    steps: list[StepRecord] = []
    xs: list[float] = []
    ts: list[float] = []
    q = 0
    for i in range(minutes):
        at = t0 + i * MINUTE
        if hooks and i in hooks:
            hooks[i](i, ctrl, plant)
        while q < len(queue) and queue[q][0] <= at:
            rec_at, ins = queue[q]
            acks[ins.instruction_id] = ctrl.receive(ins, rec_at)
            q += 1
        x, t = drive.at(i, at)
        xs.append(x)
        ts.append(t)
        lost = blind is not None and blind(i)
        obs = Observation(
            at=at,
            heat_kwth=x,
            ambient_c=t,
            loop_dev_c=float("nan") if lost else plant.w,
            inlet_dev_c=float("nan") if lost else plant.a,
            running=plant.running,
            startable=plant.startable(i),
            stoppable=plant.stoppable(i),
            faulted=len(plant.failed),
        )
        rec = ctrl.step(obs)
        steps.append(rec)
        plant.step(i, x, t, rec.command.running_units, rec.command.extraction_kwth)
    return Run(ctrl, plant, steps, acks, t0, xs, ts)


def cut_kw(run: Run, i: int) -> float:
    return run.log[i].cut_kw


def summary(run: Run) -> dict[str, Any]:
    return {
        "events": [(e.instruction_id, e.outcome, e.reason) for e in run.controller.events],
        "acks": {k: a.response.kind for k, a in run.acks.items()},
    }
