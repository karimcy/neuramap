"""The comfort/cost mode seam (WP-THERM-05; D-NEW-THERM05-1).

The comfort/cost mode is **whatever implements `ComfortCostPolicy`**: one plant command per
minute from the observation. The IT-safety objective mode is an overlay on that policy, not a
sibling optimiser. ThermaFlex's own comfort/cost mode is unseen IP (ADR-D7 option (c); research
note 34): an interface-level adapter to it (research note 35) would implement this protocol. Only
the clean-room `SetpointHoldingPolicy` ships, and it is a reference policy, not ThermaFlex.

Acceptance (2), "comfort/cost mode behaviour is unchanged", is proved as non-interference over
arbitrary policies: in comfort/cost mode the controller's command **is** the policy's command
(the same object), always; in the IT-safety mode with no event and no recovery active it is the
policy's command unless the safety filter has to change it (logged). Fidelity to ThermaFlex's
actual mode is a named ask under AWAIT-19, not something a test here can show.

**Baseline equivalence.** The WP-THERM-03 certificate is stated in deviations from the
setpoint-holding baseline (extraction equals the load, the plant's own staging). A flex cap is
certified only while the active policy is that baseline; a policy that is not says so
(``baseline_equivalent = False``) and the IT-safety mode then refuses every instruction until an
adapter supplies its own baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np

from gridq.thermal.plant import NumericPlant

__all__ = [
    "ComfortCostPolicy",
    "Observation",
    "PlantCommand",
    "SetpointHoldingPolicy",
]


@dataclass(frozen=True)
class PlantCommand:
    """One minute's command: how many chillers run, and the heat they extract (kWth)."""

    running_units: int
    extraction_kwth: float


@dataclass(frozen=True)
class Observation:
    """What the controller reads at the start of a minute.

    ``heat_kwth`` (the heat the chillers must extract to hold the setpoints) and ``ambient_c`` (the
    plant's driving temperature) are the policy's own inputs. ``loop_dev_c`` and ``inlet_dev_c`` are
    the loop and IT-inlet temperatures as deviations from the setpoint-holding baseline (the
    WP-THERM-03 state); NaN marks a lost reading. ``running``, ``startable`` (off and past its
    minimum off time) and ``stoppable`` (on and past its minimum run time) describe the units;
    ``faulted`` counts units out of service.
    """

    at: datetime
    heat_kwth: float
    ambient_c: float
    loop_dev_c: float
    inlet_dev_c: float
    running: int
    startable: int
    stoppable: int
    faulted: int

    @property
    def telemetry_ok(self) -> bool:
        return bool(np.isfinite(self.loop_dev_c) and np.isfinite(self.inlet_dev_c))


class ComfortCostPolicy(Protocol):
    """A comfort/cost mode: one command per minute. ``baseline_equivalent`` says whether its
    commands are the setpoint-holding baseline the WP-THERM-03 certificate is stated against."""

    @property
    def baseline_equivalent(self) -> bool: ...

    def command(self, obs: Observation) -> PlantCommand: ...


@dataclass(frozen=True)
class SetpointHoldingPolicy:
    """The clean-room reference comfort/cost policy: hold the setpoints (extract exactly the load)
    on the plant's own staging, ``ceil(x / q) + standby`` units (WP-THERM-03 `NumericPlant.n_on`).
    It is the baseline of D163, so it is baseline-equivalent."""

    plant: NumericPlant

    @property
    def baseline_equivalent(self) -> bool:
        return True

    def command(self, obs: Observation) -> PlantCommand:
        units = int(self.plant.n_on(np.array(obs.heat_kwth)))
        return PlantCommand(running_units=units, extraction_kwth=float(obs.heat_kwth))
