"""The twin's cooling plant: the design-time thermal model stepped one second at a time
(WP-TWIN-01, D-NEW-TWIN01-3).

The twin re-implements nothing. The plant is the resolved `gridq.thermal.plant.NumericPlant` the
availability surface is certified on; the nodes move by `gridq.thermal.nodes.step`, the exact
constant-deficit solution the certificate inlines; the deficit a cut of ``K`` kW causes is
`PowerCurve.deficit`, the certificate's own root.

What drives it:

- **heat** ``x`` (kWth) is the IT load from the twin's load source (the ``it_load`` basis: every
  IT watt becomes heat the chillers extract);
- **dry bulb** ``T`` (°C) is a per-minute input to the twin;
- **an IC-11 `FlexInstruction`** is the only input that moves chiller power. It is accepted only
  when `check_instruction` passes against the surface it names and the plant is at rest (no event
  pending or running, no stored heat still being recovered). During its hold the deficit is
  ``F = deficit(x, T, regime(T), K)``; with no instruction ``F = 0``. A non-finite deficit (no
  extraction gives the cut) delivers nothing and is counted in ``infeasible_s``.

**Recovery is a twin assumption, labelled as such.** After the hold the plant extracts the
stored heat at ``u = min(spare, u_cap, E / dt)`` with ``spare = n x unit - x`` (every unit
available again), until ``E`` is zero. This is the policy the WP-THERM-03 oracle simulates
(``tests/thermal/_thermal_oracle.py``, unload strategy, once the minimum off time has passed); the
IC-11 surface certifies the hold, not a recovery controller.

**Electricity.** The load model is authoritative for the baseline: the site draws its facility
load. The chiller-power curve is used only for differences: the reduction is
``P(x, T) - P(x - F, T)``, which is ``K`` during a hold and the negative rebound
``P(x + u, T) - P(x, T)`` while recovering. A reduction is clamped to the facility load not
attributable to IT (the twin cannot cut more than the load model says is not IT) and the clamp is
counted in ``clamped_s``. ``P(x - F, T)`` itself is reported as a diagnostic chiller-power point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from gridq.contracts.thermal_flex import (
    FlexInstruction,
    ThermalFlexAvailability,
    check_instruction,
)
from gridq.thermal.nodes import deviations, step
from gridq.thermal.plant import NumericPlant

__all__ = ["FlexVerdict", "ThermalSecond", "ThermalSetup", "ThermalTwin"]


@dataclass(frozen=True)
class ThermalSetup:
    """The plant the surface was certified on, the surface itself, and the dry bulb (°C) for each
    minute since the twin's boot."""

    plant: NumericPlant
    surface: ThermalFlexAvailability
    ambient_c: np.ndarray

    def __post_init__(self) -> None:
        if self.ambient_c.ndim != 1 or not np.isfinite(self.ambient_c).all():
            raise ValueError("ambient_c is a finite per-minute series")


@dataclass(frozen=True)
class FlexVerdict:
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ThermalSecond:
    """One simulated second. Deviations in K from the baselines; powers in kW."""

    deficit_kw: float
    loop_dev_k: float
    hall_dev_k: float
    chiller_kw: float
    reduction_kw: float
    clamped: bool
    infeasible: bool


@dataclass
class _Event:
    instruction: FlexInstruction
    start_s: int
    end_s: int


@dataclass
class ThermalTwin:
    setup: ThermalSetup
    boot_utc: datetime
    e: float = 0.0
    d: float = 0.0
    event: _Event | None = None
    accepted: list[FlexInstruction] = field(default_factory=list)
    refused: list[tuple[FlexInstruction, tuple[str, ...]]] = field(default_factory=list)
    infeasible_s: int = 0
    clamped_s: int = 0

    def _at(self, t: datetime) -> int:
        return (t - self.boot_utc) // timedelta(seconds=1)

    def offer(self, instruction: FlexInstruction, now_s: int) -> FlexVerdict:
        """Accept ``instruction`` if its surface accepts it and the plant is at rest."""
        check = check_instruction(self.setup.surface, instruction)
        reasons = tuple(str(r) for r in check.reasons)
        start = self._at(instruction.start)
        if check.ok and start < now_s:
            reasons = ("starts_in_the_past",)
        elif check.ok and (self.event is not None or self.e > 0.0):
            reasons = ("plant_not_at_rest",)
        if reasons:
            self.refused.append((instruction, reasons))
            return FlexVerdict(False, reasons)
        self.event = _Event(instruction, start, self._at(instruction.end))
        self.accepted.append(instruction)
        return FlexVerdict(True, ())

    def second(self, s: int, it_kw: float, non_it_kw: float) -> ThermalSecond:
        """Advance from second ``s`` to ``s + 1`` with IT heat ``it_kw`` and the facility load
        not attributable to IT ``non_it_kw`` (the clamp)."""
        p = self.setup.plant
        minute = s // 60
        if minute >= len(self.setup.ambient_c):
            raise ValueError("the dry-bulb series does not cover this minute")
        t = float(self.setup.ambient_c[minute])
        tt, xx = np.asarray([t]), np.asarray([it_kw])
        reg = p.curve.regime(tt)
        ev = self.event
        infeasible = False
        if ev is not None and ev.start_s <= s < ev.end_s:
            k = np.asarray([float(ev.instruction.reduction_kw)])
            f = float(p.curve.deficit(xx, tt, reg, k)[0])
            if not math.isfinite(f):
                f, infeasible = 0.0, True
                self.infeasible_s += 1
        elif self.e > 0.0:
            spare = max(p.n_chillers * p.unit_kwth - it_kw, 0.0)
            f = -min(spare, p.u_cap, self.e / 1.0)
        else:
            f = 0.0
        if ev is not None and s + 1 >= ev.end_s:
            self.event = None
        self.e, self.d = step(p, self.e, self.d, f, 1.0)
        if 0.0 > self.e > -1e-9:
            self.e = 0.0
        w, a = deviations(p, self.e, self.d)
        base = float(p.curve.power(xx, tt, reg)[0])
        chiller = float(p.curve.power(xx - f, tt, reg)[0])
        reduction = base - chiller
        clamped = reduction > non_it_kw
        if clamped:
            reduction = non_it_kw
            self.clamped_s += 1
        return ThermalSecond(f, w, a, chiller, reduction, clamped, infeasible)
