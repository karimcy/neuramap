"""The real optimisation engine (M2/M3/M4) — the half-hourly dispatch MILP + sizing composition.

`MilpDispatchSolver` is the production half-hourly dispatch solver (linopy/HiGHS) implementing the
inner `gridq.dispatch.DispatchSolver` Protocol; `MilpEngine` is the production
`gridq.engine.OptimisationEngine` — the sizing outer loop composed with the MILP, dropping in
behind the frozen platform↔engine seam with no downstream change. The integration-only
`SimpleSolver`/`BaselineEngine` remain the labelled fallbacks. See ``docs/dispatch_engine.md``.
"""

from __future__ import annotations

from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule

__all__ = [
    "MilpEngine",
    "MilpDispatchSolver",
    "HalfHourlyDispatchSchedule",
]
