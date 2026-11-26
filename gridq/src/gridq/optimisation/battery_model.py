"""Battery model Protocol and ConstantEfficiencyBatteryModel for MPC dispatch.

The BatteryModel is the state accumulator in the MPC rolling loop.  It takes the
actually-executed charge/discharge at each half-hour and propagates the real SoE.

With constant efficiency this is a three-line calculation; the Protocol allows a
nonlinear battery model to be substituted without changing the MPC loop at all.

In live operation the BatteryModel can be replaced by a direct BMS read at the
end of each application period — the BMS reports the actual SoE and no model is
needed.  The Protocol is the seam between simulation and live operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class BatteryState:
    """Actual state after executing one half-hourly charge/discharge command.

    ``actual_charge_kw`` and ``actual_discharge_kw`` may differ from the commanded
    values when the battery hits an SOC bound (clipping).  The SoE accounting always
    uses the actual values — never the commanded ones.
    """

    actual_soe_kwh: float
    actual_charge_kw: float
    actual_discharge_kw: float
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class BatteryModel(Protocol):
    """State-propagation seam in the MPC rolling loop.

    In simulation: propagates SoE from the actually-executed charge/discharge.
    In live operation: replaced by a BMS read (no model step needed).
    """

    def step(
        self,
        charge_kw: float,
        discharge_kw: float,
        soe_kwh: float,
        dt_hours: float,
    ) -> BatteryState: ...


@dataclass
class ConstantEfficiencyBatteryModel:
    """Linear SoC propagation with SOC bound clipping.

    Adapted from the ``_nonlinear_validate`` pattern in the reference MPCSolver.
    Uses the same AC round-trip efficiency as the inner MILP so the simulation
    is self-consistent with the optimiser's assumptions.  Clips to SOC bounds and
    back-computes the actual achievable charge/discharge when a bound is hit.

    With perfect forecast and a BTM-only battery the SOC bounds never activate in
    simulation — this path is live-operation defensive clipping, not a physics fix.
    """

    rte: float
    """AC round-trip efficiency (same value used in the inner MILP)."""
    energy_kwh: float
    """Usable battery capacity [kWh]."""
    soc_min: float = 0.05
    """Minimum SOC fraction (default 5 %)."""
    soc_max: float = 1.0
    """Maximum SOC fraction (default 100 %, the pre-WP-SIZE-01 behaviour). The MPC sets it to the
    release's usable-window ceiling so the simulated state can never leave the window the next
    window's MILP requires its start state to sit in."""

    def step(
        self,
        charge_kw: float,
        discharge_kw: float,
        soe_kwh: float,
        dt_hours: float,
    ) -> BatteryState:
        """Propagate SoE by one interval; clip to SOC bounds; return actual state."""
        dc_charge_kwh = charge_kw * self.rte * dt_hours
        dc_discharge_kwh = discharge_kw * dt_hours
        raw_soe = soe_kwh + dc_charge_kwh - dc_discharge_kwh

        soe_min_kwh = self.soc_min * self.energy_kwh
        soe_max_kwh = self.soc_max * self.energy_kwh
        actual_soe = float(np.clip(raw_soe, soe_min_kwh, soe_max_kwh))

        delta = actual_soe - soe_kwh
        warnings: list[str] = []

        if delta >= 0.0:
            actual_charge_kw = (delta / (self.rte * dt_hours)) if dt_hours > 0 else 0.0
            actual_discharge_kw = 0.0
            if charge_kw > 0 and abs(actual_charge_kw - charge_kw) > 1e-6:
                warnings.append(
                    f"charge clipped {charge_kw:.3f} → {actual_charge_kw:.3f} kW "
                    "(SOC upper bound hit)."
                )
        else:
            actual_charge_kw = 0.0
            actual_discharge_kw = (-delta / dt_hours) if dt_hours > 0 else 0.0
            if discharge_kw > 0 and abs(actual_discharge_kw - discharge_kw) > 1e-6:
                warnings.append(
                    f"discharge clipped {discharge_kw:.3f} → {actual_discharge_kw:.3f} kW "
                    "(SOC lower bound hit)."
                )

        return BatteryState(actual_soe, actual_charge_kw, actual_discharge_kw, warnings)
