"""Dispatch solver contract (M3) + a labelled placeholder solver.

`DispatchSolver` is the shared inner contract a half-hourly dispatch solver implements;
`SimpleSolver` is the deliberately limited, integration-only placeholder (never bankable, prices
nothing, invents no charge/discharge volume). The real MILP (linopy/HiGHS — Olly, deferred)
implements the same Protocol and drops in with no downstream change. `to_finance_dispatch_energy`
is the producer seam that feeds finance's `DispatchEnergy` hook (C010 / D13).

See docs/dispatch_engine.md for what the placeholder does and does NOT do, and the MILP target.
"""

from __future__ import annotations

from gridq.dispatch.contract import (
    DispatchProblem,
    DispatchResult,
    DispatchSolver,
    candidate_dispatch_constraints,
    dispatch_constraints_for,
)
from gridq.dispatch.energy import to_finance_dispatch_energy
from gridq.dispatch.simple_solver import SimpleSolver

__all__ = [
    "DispatchProblem",
    "DispatchResult",
    "DispatchSolver",
    "SimpleSolver",
    "candidate_dispatch_constraints",
    "dispatch_constraints_for",
    "to_finance_dispatch_energy",
]
