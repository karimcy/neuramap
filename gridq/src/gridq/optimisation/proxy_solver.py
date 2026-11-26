"""Preloaded dispatch solvers for the sizing search (M4 stage A/B/C, D32).

The two-stage sizing search evaluates hundreds of candidate sizes against the *same* metered
series. Re-reading and re-parsing the half-hourly CSV per candidate would be wasteful and would
flood the confidential-access log with hundreds of identical entries, so the search reads the
series **once** (one logged access, via the base solver) and evaluates every candidate through a
`PreloadedDispatchSolver` holding the parsed arrays.

With ``interval_weights`` set (the typical-period proxy) each solved interval stands for its
weight in real intervals: the weighted solve guides the search but its numbers are never the
reported selection — the confirm stage re-solves the winner on the full series (see
``gridq.optimisation.two_stage``). Weighted results carry a loud warning naming the proxy.

Implements the same `gridq.dispatch.DispatchSolver` Protocol as `MilpDispatchSolver`, so the
existing sizing loop (`gridq.sizing.sizer`) consumes it unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.engine.errors import InvalidRequestError
from gridq.optimisation._assumptions import maybe, numeric
from gridq.optimisation.milp_solver import MilpDispatchSolver, _UnpriceableDispatch
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.schemas import SolverStatus


class PreloadedDispatchSolver:
    """A `DispatchSolver` over an already-loaded (optionally weighted) half-hourly series.

    ``base`` supplies the MILP construction and result assembly (single implementation of the
    physics — this class adds no model of its own). ``interval_weights`` marks a typical-period
    proxy solve; ``extra_warnings`` are prepended to every result (e.g. the proxy provenance
    note), so downstream records always disclose how the numbers were produced.
    """

    def __init__(
        self,
        base: MilpDispatchSolver,
        *,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        interval_weights: np.ndarray | None = None,
        extra_warnings: tuple[str, ...] = (),
    ) -> None:
        if interval_weights is not None and len(interval_weights) != len(load):
            raise InvalidRequestError(
                f"interval_weights length {len(interval_weights)} != series length {len(load)}"
            )
        self._base = base
        self._load = load
        self._timestamps = timestamps
        self._interval_min = int(interval_min)
        self._weights = interval_weights
        self._extra_warnings = tuple(extra_warnings)
        self._last_schedule: HalfHourlyDispatchSchedule | None = None

    @property
    def MODEL_VERSION(self) -> str:  # noqa: N802 - mirrors the class-attr convention
        suffix = "-proxy" if self._weights is not None else ""
        return f"{self._base.MODEL_VERSION}{suffix}"

    def last_schedule(self) -> HalfHourlyDispatchSchedule | None:
        """The schedule from the last successful ``solve`` (``None`` otherwise)."""
        return self._last_schedule

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        """Solve one fixed spec against the preloaded series (contract-valid, never raises)."""
        if not isinstance(problem, DispatchProblem):
            raise InvalidRequestError(
                f"PreloadedDispatchSolver.solve expects a DispatchProblem, "
                f"got {type(problem).__name__}"
            )
        self._last_schedule = None
        rte = numeric(maybe(problem.assumptions, "round_trip_efficiency", "ac_round_trip"))
        if rte is None or not 0 < rte <= 1:
            return self._base._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: round_trip_efficiency.ac_round_trip is missing or invalid; "
                "round-trip losses cannot be modelled.",
            )
        try:
            schedule, binding, warn = self._base._solve_milp(
                problem,
                self._load,
                self._timestamps,
                self._interval_min,
                rte,
                interval_weights=self._weights,
            )
        except _UnpriceableDispatch as exc:
            # F-01: refuse rather than price charge energy at £0. This is the sizing search's
            # evaluation path, so an unhandled refusal here escapes as a 500 from every caller
            # instead of the honest INSUFFICIENT_DATA verdict + ranked data ask.
            return self._base._unsolved(problem, SolverStatus.INSUFFICIENT_DATA, str(exc))
        if schedule is None:
            return self._base._unsolved(
                problem, SolverStatus.INFEASIBLE, "Dispatch MILP returned no optimal solution."
            )
        self._last_schedule = schedule
        # MICASC-3: the agreed-capacity / TCR-band overlay. THIS is the path a real ``gridq run``
        # takes — the sizing search and the confirm stage both drive the engine through this
        # class, never through ``MilpDispatchSolver.solve`` — so an overlay wired only into that
        # method priced £0 on all 181 sizing-grid points of a live run while every direct-solve
        # test passed. Computed here and passed EXPLICITLY, never read off shared solver state.
        #
        # On a typical-period proxy solve (``self._weights is not None``) the helper refuses to
        # claim and returns the £0-with-reason record without solving: an annual worst-case peak
        # is not observable on a weighted subset of periods, and the refusal is cheap.
        capacity_reduction = self._base._capacity_reduction_overlay(
            problem,
            schedule,
            self._load,
            self._timestamps,
            self._interval_min,
            rte,
            None,
        )
        self._base._last_capacity_reduction = capacity_reduction
        return self._base._solved(
            problem,
            schedule,
            binding,
            [*self._extra_warnings, *warn],
            capacity_reduction=capacity_reduction,
        )
