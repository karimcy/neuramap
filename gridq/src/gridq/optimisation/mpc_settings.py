"""Configuration dataclass for ``MpcDispatchSolver``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MpcSettings:
    """Tunable parameters for the MPC rolling-horizon dispatch solver.

    Horizons are specified in days and converted to half-hourly steps at solve
    time using the actual interval length from the loaded HH series — never a
    hardcoded step assumption.
    """

    prediction_horizon_days: float = 7.0
    """Look-ahead for each window solve [days].

    7 days captures a full weekly ToU price cycle so the MPC closely
    approximates the globally optimal annual dispatch.  Shorter horizons cause
    myopic dispatch against a partial cycle (e.g. 1 day sees only one overnight
    trough and misses the following day's red band).
    """

    application_horizon_days: float = 1.0
    """Control applied per loop iteration before re-solving [days].

    Daily re-optimisation: apply the first day's plan, then re-solve with the
    actual SoE as the initial condition.  Shorter application horizons improve
    tracking of the nonlinear battery but increase wall-clock time for a full
    year simulation.
    """

    aging_alpha: float = 0.005
    """Calendar-aging objective weight — soft penalty on mean SoC.

    Biases the schedule toward lower average SoC when it does not affect bill
    savings.  Adapted from the reference MPCSolver alpha parameter.  Set to
    0.0 to disable (recovers the _EPS tie-break behaviour of the annual MILP).
    """

    aging_beta: float = 0.0185
    """Cycle-aging objective weight — soft throughput penalty.

    Supplements the hard warranty throughput cap; does not replace it.  The
    hard cap still fires before this soft term would matter.  Adapted from the
    reference MPCSolver beta parameter.
    """

    soc_min: float = 0.05
    """Minimum SOC fraction passed to ``ConstantEfficiencyBatteryModel``."""
