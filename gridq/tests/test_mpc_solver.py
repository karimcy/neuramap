"""Tests for MpcDispatchSolver and the extended milp_solver rolling-horizon path.

Covers: MPC solve produces a valid DispatchResult; physics invariants on the
accumulated schedule; determinism; honest degrade paths; and the milp_solver
extensions (solve_window, ObjectiveWeights, initial_soe_kwh, use_binary).
"""

from __future__ import annotations

import numpy as np
import pytest

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver, ObjectiveWeights
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.mpc_solver import MpcDispatchSolver
from gridq.schemas import BankableStatus, ImplementationLevel, SolverStatus

_RTE = 0.88


@pytest.fixture
def site(tmp_path):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=14)
    return multi_rate_site(path, rows=rows)


@pytest.fixture
def assumptions():
    return load_assumptions("gb_v0_1")


def _problem(site, *, power=100.0, duration=2.0, assumptions=None):
    return DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=power,
        energy_kwh=power * duration,
        duration_hours=duration,
    )


# ── MpcDispatchSolver integration ──────────────────────────────────────────


def test_mpc_solves_to_production_optimal_not_bankable(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    solver = MpcDispatchSolver(settings=settings)
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert result.implementation_level == ImplementationLevel.PRODUCTION
    assert result.bankable_status == BankableStatus.NOT_BANKABLE  # gb_v0_1 is placeholder


def test_mpc_schedule_has_no_simultaneous_charge_discharge(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    solver = MpcDispatchSolver(settings=settings)
    solver.solve(_problem(site, assumptions=assumptions))
    schedule = solver.last_schedule()
    assert schedule is not None
    # Binary constraints in each window guarantee no simultaneous charge/discharge.
    assert not np.any((schedule.charge_kw > 1e-6) & (schedule.discharge_kw > 1e-6))


def test_mpc_behind_the_meter_discharge_bound(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    solver = MpcDispatchSolver(settings=settings)
    solver.solve(_problem(site, assumptions=assumptions))
    schedule = solver.last_schedule()
    assert schedule is not None
    assert np.all(schedule.discharge_kw <= schedule.site_load_kw + 1e-6)


def test_mpc_round_trip_loss_grosses_charge_above_discharge(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    solver = MpcDispatchSolver(settings=settings)
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.annual_charge_kwh and result.annual_discharge_kwh
    assert result.annual_charge_kwh > result.annual_discharge_kwh
    assert result.annual_charge_kwh == pytest.approx(result.annual_discharge_kwh / _RTE, rel=5e-3)


def test_mpc_deterministic_same_problem_same_result(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    problem = _problem(site, assumptions=assumptions)
    r1 = MpcDispatchSolver(settings=settings).solve(problem)
    r2 = MpcDispatchSolver(settings=settings).solve(problem)
    assert r1.annual_charge_kwh == pytest.approx(r2.annual_charge_kwh, rel=1e-9)
    assert r1.annual_discharge_kwh == pytest.approx(r2.annual_discharge_kwh, rel=1e-9)


def test_mpc_degrade_no_half_hourly_series(site, assumptions):
    no_ref = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"half_hourly_file_ref": None})}
    )
    result = MpcDispatchSolver().solve(_problem(no_ref, assumptions=assumptions))
    assert result.solver_status == SolverStatus.INSUFFICIENT_DATA
    assert result.power_kw is None


def test_mpc_degrade_no_observed_peak(site, assumptions):
    no_peak = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"max_import_kw": None})}
    )
    result = MpcDispatchSolver().solve(_problem(no_peak, assumptions=assumptions))
    assert result.solver_status == SolverStatus.INSUFFICIENT_DATA


def test_mpc_last_schedule_none_after_failed_solve(site, assumptions):
    solver = MpcDispatchSolver()
    no_ref = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"half_hourly_file_ref": None})}
    )
    solver.solve(_problem(no_ref, assumptions=assumptions))
    assert solver.last_schedule() is None


def test_mpc_has_value_streams_and_priced_revenue(site, assumptions):
    settings = MpcSettings(prediction_horizon_days=3.0, application_horizon_days=1.0)
    result = MpcDispatchSolver(settings=settings).solve(_problem(site, assumptions=assumptions))
    assert result.value_streams
    assert result.priced_revenue_gbp > 0


# ── milp_solver extensions ─────────────────────────────────────────────────


def test_solve_window_returns_schedule(tmp_path, assumptions):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    load, timestamps, interval, _ = solver._load_series(str(path))
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
    )
    window_load = load[:48]  # first 24 h
    window_ts = timestamps[:48]
    schedule = solver.solve_window(
        problem=problem,
        load=window_load,
        timestamps=window_ts,
        interval_min=interval,
        rte=_RTE,
        initial_soe_kwh=50.0,
    )
    assert schedule is not None
    assert schedule.charge_kw.shape == (48,)
    assert schedule.discharge_kw.shape == (48,)
    # Binary constraint: no simultaneous charge and discharge.
    assert not np.any((schedule.charge_kw > 1e-6) & (schedule.discharge_kw > 1e-6))


def test_solve_window_soe_starts_at_initial(tmp_path, assumptions):
    """SoE at t=0 should equal initial_soe + net flow (no cyclic wrap)."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=3)
    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    load, timestamps, interval, _ = solver._load_series(str(path))
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
    )
    initial_soe = 80.0
    schedule = solver.solve_window(
        problem=problem,
        load=load,
        timestamps=timestamps,
        interval_min=interval,
        rte=_RTE,
        initial_soe_kwh=initial_soe,
    )
    assert schedule is not None
    dt = interval / 60.0
    # soe[0] = initial_soe + charge[0]*rte*dt - discharge[0]*dt
    expected_soe0 = initial_soe + schedule.charge_kw[0] * _RTE * dt - schedule.discharge_kw[0] * dt
    assert float(schedule.soe_kwh[0]) == pytest.approx(expected_soe0, abs=1e-3)


def test_objective_weights_default_preserves_annual_milp_behaviour(tmp_path, assumptions):
    """ObjectiveWeights() (zero) must give the same result as no weights at all."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
    )
    r1 = MilpDispatchSolver().solve(problem)
    load, timestamps, interval, _ = MilpDispatchSolver()._load_series(str(path))
    solver = MilpDispatchSolver()
    s2, _, _ = solver._solve_milp(
        problem, load, timestamps, interval, _RTE, weights=ObjectiveWeights()
    )
    assert s2 is not None
    assert s2.annual_discharge_kwh == pytest.approx(r1.annual_discharge_kwh, rel=1e-6)
