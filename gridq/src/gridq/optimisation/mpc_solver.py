"""Rolling-horizon MPC dispatch solver (``MpcDispatchSolver``).

Wraps the inner ``MilpDispatchSolver`` in a receding-horizon loop:

1. At the start of each application period (default: 1 day), re-solve a MILP
   over the full prediction horizon (default: 7 days) starting from the actual
   SoE as the initial condition.
2. Apply only the first application-period's actions to the battery model.
3. The ``ConstantEfficiencyBatteryModel`` propagates the real SoE — in simulation
   this is equivalent to the MILP's linear model (same RTE), so any deviation is
   zero.  When swapped for a nonlinear model the MPC loop corrects the SoE each
   day, recovering the accuracy lost by the one-off annual MILP.
4. Assemble the full-year accumulated schedule into a contract-valid
   ``DispatchResult`` via the same value-stream valuation as the annual MILP.

The loop starts at the usable SoE floor of the release's SoC window, and a window
that returns no optimal schedule ends the solve: the result carries that window's
status (``INFEASIBLE`` or ``INSUFFICIENT_DATA``), its reason, and no volumes. The
result is ``OPTIMAL`` only when every window solved (WP-SIZE-01; the regression
suite is ``tests/test_mpc_infeasibility_regression.py``).

The annual MILP and the MPC differ in dispatch (rolling ≠ global optimum) even
with identical data and a constant-efficiency battery model — the 7-day horizon
is myopic vs. the annual horizon and the binary MILP is a different model from
the LP used in the annual solve.  This is intentional: the MPC is the accurate
*simulation* mode; the annual MILP is the fast *sizing* mode.

Implements the ``DispatchSolver`` Protocol — nothing upstream (sizer, engine)
needs to change.
"""

from __future__ import annotations

import numpy as np

from gridq import constraint_library as _cl
from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.engine.errors import InvalidRequestError
from gridq.ingestion import HalfHourlyDataError
from gridq.optimisation._assumptions import maybe, numeric
from gridq.optimisation.battery_model import (
    BatteryModel,
    ConstantEfficiencyBatteryModel,
)
from gridq.optimisation.milp_solver import (
    _HOURS_PER_YEAR,
    _RTE_KEY,
    _WARRANTY_KEY,
    MilpDispatchSolver,
    ObjectiveWeights,
    _soc_window_fractions,
    duos_absence_warning,
)
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.tariff_calendar import (
    energy_objective_coefficients,
    energy_price_envelope,
    network_rate_series_with_absences,
    red_band_mask,
)
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import (
    BankableStatus,
    ImplementationLevel,
    SolverStatus,
)
from gridq.value_stack.registry import StreamRegistry, default_registry


class MpcDispatchSolver:
    """Rolling-horizon MPC dispatch solver.  See module docstring."""

    MODEL_VERSION = "mpc-milp-v1"

    def __init__(
        self,
        *,
        settings: MpcSettings | None = None,
        battery_model: BatteryModel | None = None,
        registry: StreamRegistry | None = None,
        seed: int = 0,
    ) -> None:
        self._settings = settings if settings is not None else MpcSettings()
        self._registry = registry if registry is not None else default_registry()
        self._seed = int(seed)
        # None → built at solve time from the problem spec + assumption RTE.
        self._battery_model = battery_model
        self._inner = MilpDispatchSolver(registry=self._registry, seed=seed)
        self._last_schedule: HalfHourlyDispatchSchedule | None = None

    def last_schedule(self) -> HalfHourlyDispatchSchedule | None:
        """The full-year accumulated schedule from the last successful ``solve``."""
        return self._last_schedule

    # --- DispatchSolver Protocol -------------------------------------------

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        """Run the rolling-horizon MPC and return a contract-valid ``DispatchResult``."""
        if not isinstance(problem, DispatchProblem):
            raise InvalidRequestError(
                f"MpcDispatchSolver.solve expects a DispatchProblem, got {type(problem).__name__}"
            )
        self._last_schedule = None
        site = problem.site_model
        assumptions = problem.assumptions

        max_import = site.load_data.max_import_kw
        if max_import is None or max_import <= 0:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: load_data.max_import_kw is missing or non-positive.",
            )
        if not site.load_data.half_hourly_file_ref:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: no half-hourly series (load_data.half_hourly_file_ref) — "
                "the MPC needs the metered half-hourly load; a load shape is never invented.",
            )

        try:
            load, timestamps, interval_min, load_warn = self._inner._load_series(
                site.load_data.half_hourly_file_ref
            )
        except HalfHourlyDataError as exc:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                f"Insufficient data: half-hourly series unreadable — {exc}",
            )
        if load.size == 0:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: the half-hourly series has no usable (non-null) readings.",
            )

        rte_a = maybe(assumptions, "round_trip_efficiency", "ac_round_trip")
        rte = numeric(rte_a)
        if rte is None or not 0 < rte <= 1:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: round_trip_efficiency.ac_round_trip is missing or invalid.",
            )

        # --- usable SoC window: one resolver for the planner AND the simulator ---
        # The inner MILP plans inside the release's usable window and refuses a start state
        # outside it. The default simulator must therefore clip to that same window, never below
        # its floor or above its ceiling, or solver-tolerance drift (HiGHS returns a state a few
        # 1e-8 kWh past a bound) carries the next window's start out of the window and the MPC
        # refuses a feasible problem. ``max`` keeps a stricter ``MpcSettings.soc_min`` honoured
        # (on a release with no declared window the 0.05 default still applies, as before).
        soc_min_frac, soc_max_frac, _soc_note = _soc_window_fractions(assumptions)

        # --- battery model (built once per solve) ---------------------------
        battery_model = self._battery_model or ConstantEfficiencyBatteryModel(
            rte=rte,
            energy_kwh=float(problem.energy_kwh),
            soc_min=max(self._settings.soc_min, soc_min_frac),
            soc_max=soc_max_frac,
        )

        # --- step and horizon sizes in HH intervals -------------------------
        dt = interval_min / 60.0
        T = len(load)
        annual_factor = _HOURS_PER_YEAR / (T * dt)

        na_steps = max(1, round(self._settings.application_horizon_days * 24.0 / dt))
        nh_steps = max(na_steps, round(self._settings.prediction_horizon_days * 24.0 / dt))

        # --- connection limit (the inner MILP's, from the constraint library) -
        declared_limit = _cl.connection.import_limit_kw(
            site.connection.mic_kw, site.connection.asc_kw
        )
        conn_limit = declared_limit if declared_limit is not None else 0.0

        # --- objective aging weights from settings --------------------------
        weights = ObjectiveWeights(
            aging_alpha=self._settings.aging_alpha,
            aging_beta=self._settings.aging_beta,
        )

        # --- rolling MPC loop -----------------------------------------------
        actual_charge = np.zeros(T)
        actual_discharge = np.zeros(T)
        actual_soe = np.zeros(T)
        all_warnings: list[str] = [*load_warn]
        # Availability/held-SoE commitments (C026) are compiled by the ANNUAL MILP only — the
        # rolling-horizon windows do not carry them yet. Never silently: a commitment-bearing
        # problem dispatched via MPC is loudly disclosed as unconstrained-by-contract.
        if any(getattr(d, "parameters", None) is not None for d in problem.constraints):
            all_warnings.append(
                "availability/held-SoE commitments are NOT compiled in the MPC rolling-horizon "
                "mode yet — this dispatch is unconstrained by the contracted windows; use the "
                "annual MILP for commitment-bearing runs (C026 follow-up)."
            )
        # Start state (WP-SIZE-01, quarantine fault 1). The loop used to start at SoE 0 "fully
        # discharged", which lies OUTSIDE the usable window as soon as a release declares a SoC
        # floor, so window 1 was refused before it began. Start at the usable FLOOR instead: it is
        # still the emptiest state the pack can legally be in, so the start is as conservative as
        # before and credits the pack no energy it did not buy (the D-169 concern is crediting
        # energy ABOVE the floor; the floor itself is the pack's empty state). Same resolver as
        # the inner MILP, so the two can never disagree about where the floor is.
        current_soe = soc_min_frac * float(problem.energy_kwh)
        t_start = 0
        n_windows = -(-T // na_steps)

        while t_start < T:
            t_predict_end = min(t_start + nh_steps, T)
            t_apply_end = min(t_start + na_steps, T)
            n_apply = t_apply_end - t_start

            outcome = self._inner.solve_window_outcome(
                problem=problem,
                load=load[t_start:t_predict_end],
                timestamps=timestamps[t_start:t_predict_end],
                interval_min=interval_min,
                rte=rte,
                initial_soe_kwh=current_soe,
                weights=weights,
            )
            window_schedule = outcome.schedule

            if window_schedule is None:
                # A refused window ends the solve (WP-SIZE-01, quarantine faults 2-4). The old
                # branch held the battery and carried on, which made the refusal an absorbing
                # fixed point and then reported the assembled zeros as OPTIMAL. There is no honest
                # schedule past a refused window: "hold" is a control action nobody optimised, and
                # every later window would start from a state the problem never reached. So the
                # whole result degrades to the window's own status (INFEASIBLE or
                # INSUFFICIENT_DATA, as the annual solve reports it) with NO volumes, and the
                # solver's reason travels with it.
                refusal = outcome.refusal_status or SolverStatus.INFEASIBLE
                window_no = t_start // na_steps + 1
                return self._unsolved(
                    problem,
                    refusal,
                    f"MPC window {window_no} of {n_windows} (half-hours {t_start}-"
                    f"{t_predict_end - 1}, start SoE {current_soe:g} kWh) returned no optimal "
                    f"schedule ({refusal.value}); the rolling-horizon dispatch is not solved.",
                    extra_warnings=[*all_warnings, *outcome.reasons],
                )

            # Apply the application horizon (first na_steps of the solved window).
            planned_ch = window_schedule.charge_kw[:n_apply]
            planned_dc = window_schedule.discharge_kw[:n_apply]

            for i in range(n_apply):
                actual_load_i = float(load[t_start + i])
                # Clip to actual BTM + connection constraints.
                ch = min(float(planned_ch[i]), max(0.0, conn_limit - actual_load_i))
                dc = min(float(planned_dc[i]), actual_load_i)

                state = battery_model.step(ch, dc, current_soe, dt)
                actual_charge[t_start + i] = state.actual_charge_kw
                actual_discharge[t_start + i] = state.actual_discharge_kw
                actual_soe[t_start + i] = state.actual_soe_kwh
                all_warnings.extend(state.warnings)
                current_soe = state.actual_soe_kwh

            t_start += na_steps

        # --- assemble the full-year schedule --------------------------------
        envelope, price_warn = energy_price_envelope(
            site.tariff_stack.import_rates,
            timestamps,
            import_rate_windows=site.tariff_stack.import_rate_windows,
        )
        if price_warn:
            all_warnings.append(price_warn)
        energy_price = None if envelope is None else envelope.bill
        energy_buy = None if envelope is None else energy_objective_coefficients(envelope)[1]
        # D45, same discipline as `MilpDispatchSolver._solve_milp`: take the absence report rather
        # than the discarding wrapper, so an absent band rate is reported instead of entering this
        # schedule's reported DUoS avoidance as an indistinguishable £0. The rolling-horizon window
        # solves route through `_solve_milp`, so they already warn on their own objective price;
        # this is the assembly-time series the FULL-YEAR schedule reports against, and it must not
        # be the one place the absence goes quiet.
        duos, duos_absent = network_rate_series_with_absences(site.tariff_stack, timestamps)
        duos_note = duos_absence_warning(duos_absent)
        if duos_note:
            all_warnings.append(duos_note)
        red_mask = red_band_mask(timestamps)

        charge_sol = np.clip(np.round(actual_charge, 6), 0.0, None)
        discharge_sol = np.clip(np.round(actual_discharge, 6), 0.0, None)
        soe_sol = np.clip(np.round(actual_soe, 6), 0.0, None)

        full_schedule = HalfHourlyDispatchSchedule(
            power_kw=float(problem.power_kw),
            energy_kwh=float(problem.energy_kwh),
            duration_hours=float(problem.duration_hours),
            interval_hours=dt,
            annual_factor=annual_factor,
            charge_kw=charge_sol,
            discharge_kw=discharge_sol,
            soe_kwh=soe_sol,
            site_load_kw=load,
            duos_rate_gbp_per_kwh=duos,
            red_band_mask=red_mask,
            energy_price_gbp_per_kwh=energy_price,
            energy_buy_price_gbp_per_kwh=energy_buy,
        )
        self._last_schedule = full_schedule

        # Binding constraints against the full-year aggregate.
        throughput_cap_kwh = self._throughput_cap(problem, annual_factor)
        binding = full_schedule.binding_constraints(
            connection_limit_kw=conn_limit,
            throughput_cap_kwh=throughput_cap_kwh,
        )

        return self._solved(problem, full_schedule, binding, all_warnings)

    # --- helpers ------------------------------------------------------------

    def _throughput_cap(self, problem: DispatchProblem, annual_factor: float) -> float:
        """Annual throughput cap [kWh] from the assumption library; 0.0 if not set."""
        cap_table = maybe(
            problem.assumptions, "warranty_cycle_limits", "max_cycles_per_year_by_duration_h"
        )
        if cap_table is None:
            return 0.0
        # Ceiling lookup (D35) — same conservative warranty direction as the annual MILP.
        cap_cycles, _ = _cl.warranty.ceiling_value_for_duration(
            cap_table.value, float(problem.duration_hours)
        )
        if cap_cycles is None or cap_cycles <= 0:
            return 0.0
        return _cl.warranty.throughput_cap_kwh(cap_cycles, float(problem.energy_kwh), annual_factor)

    def _solved(
        self,
        problem: DispatchProblem,
        schedule: HalfHourlyDispatchSchedule,
        binding: list[str],
        warnings: list[str],
    ) -> DispatchResult:
        """The solved result. Reached only when EVERY window solved optimal (WP-SIZE-01): a
        refused window returns ``_unsolved`` from the loop, so OPTIMAL here is earned, not
        hard-coded over a refusal."""
        value_streams, gated_upside, used = build_value_streams(
            schedule,
            problem.site_model,
            problem.assumptions,
            registry=self._registry,
            context=None,
        )
        status = MilpDispatchSolver._bankable_status(
            problem.assumptions, set(used) | {_RTE_KEY, _WARRANTY_KEY}
        )
        return DispatchResult(
            power_kw=round(schedule.power_kw, 6),
            energy_kwh=round(schedule.energy_kwh, 6),
            duration_hours=round(schedule.duration_hours, 6),
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=status,
            solver_status=SolverStatus.OPTIMAL,
            dispatch_schedule_ref=schedule.ref(),
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=binding,
            annual_charge_kwh=schedule.annual_charge_kwh,
            annual_discharge_kwh=schedule.annual_discharge_kwh,
            annual_throughput_mwh=schedule.annual_throughput_mwh,
            equivalent_full_cycles=schedule.equivalent_full_cycles,
            warnings=[
                f"MPC dispatch ({self.MODEL_VERSION}): rolling "
                f"{self._settings.prediction_horizon_days}-day prediction horizon, "
                f"{self._settings.application_horizon_days}-day application, "
                "binary charge/discharge exclusivity.",
                *warnings,
            ],
        )

    def _unsolved(
        self,
        problem: DispatchProblem,
        status: SolverStatus,
        reason: str,
        *,
        extra_warnings: list[str] | None = None,
    ) -> DispatchResult:
        value_streams, gated_upside, _ = build_value_streams(
            None,
            problem.site_model,
            problem.assumptions,
            registry=self._registry,
            context=None,
        )
        return DispatchResult(
            power_kw=None,
            energy_kwh=None,
            duration_hours=None,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            solver_status=status,
            dispatch_schedule_ref=None,
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=[],
            annual_charge_kwh=None,
            annual_discharge_kwh=None,
            annual_throughput_mwh=None,
            equivalent_full_cycles=None,
            warnings=[
                reason,
                "MPC dispatch: no schedule solved; charge/discharge volume unavailable "
                "(None, never invented).",
                *(extra_warnings or []),
            ],
        )
