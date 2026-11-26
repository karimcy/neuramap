"""MPC infeasibility regression suite (WP-SIZE-01 / spec A10-05, B2-02).

The permanent guard that the rolling-horizon MPC never reports ``optimal`` with zero volumes when
it did not solve. Any later dispatch code that reuses the MPC must keep this file green.

The failure mode it pins (measured on HEAD 55b168c before the fix, gb_v1_12, 7-day series):
``MpcDispatchSolver`` started at SoE 0 against the release's 15 % usable-SoE floor, so every window
was refused (7 of 7); the refusal branch held the battery and carried on, and the assembled result
was hard-coded ``SolverStatus.OPTIMAL`` with ``annual_discharge_kwh == 0.0``. The same happened on
a problem that is genuinely infeasible, so the two were indistinguishable.

Every infeasible problem here is genuinely infeasible, built from real model inputs; nothing is
stubbed. The annual MILP is solved on the same problem as an independent witness that it really is
infeasible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.optimisation.battery_model import BatteryState
from gridq.optimisation.milp_solver import MilpDispatchSolver, _soc_window_fractions
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.mpc_solver import MpcDispatchSolver
from gridq.schemas import AvailabilityWindow, ProvisioningRequirement, SolverStatus

#: The newest committed release. It declares a usable SoC window (0.15-0.95), which is what made
#: the zero-SoE start fall outside the window on every release since gb_v1_7.
_RELEASE = "gb_v1_12"
_SETTINGS = MpcSettings(prediction_horizon_days=2.0, application_horizon_days=1.0)


@pytest.fixture(scope="module")
def hh(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    path = tmp_path_factory.mktemp("mpc_infeasibility") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return path, rows


@pytest.fixture(scope="module")
def site(hh):
    path, rows = hh
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def library() -> AssumptionLibrary:
    return load_assumptions(_RELEASE)


def _problem(site, library, *, power: float, duration: float) -> DispatchProblem:
    return DispatchProblem(
        site_model=site,
        assumptions=library,
        power_kw=power,
        energy_kwh=power * duration,
        duration_hours=duration,
        constraints=[],
    )


def _over_power_obligation(site):
    """A load-service obligation the battery cannot physically serve.

    Fixture facts: weekday 17:00-19:00 load 320 kW on a 500 kW connection; an 800 kW addition
    leaves 620 kW unmet for 2 h every weekday. A 200 kW / 400 kWh pack can neither deliver 620 kW
    nor hold the ~1.3 MWh one occurrence drains, so no schedule satisfies the model.
    """
    requirement = ProvisioningRequirement(
        requirement_id="infeasible-obligation",
        windows=[
            AvailabilityWindow(days_of_week=[0, 1, 2, 3, 4], start_time=time(17), end_time=time(19))
        ],
        additional_load_kw=800.0,
        tenor_years=10.0,
    )
    return site.model_copy(update={"provisioning_requirements": [requirement]})


def _assert_no_volumes(result: DispatchResult) -> None:
    """An unsolved result carries no volume at all: None, never an invented 0.0."""
    assert result.power_kw is None
    assert result.energy_kwh is None
    assert result.annual_charge_kwh is None
    assert result.annual_discharge_kwh is None
    assert result.annual_throughput_mwh is None
    assert result.equivalent_full_cycles is None
    assert result.dispatch_schedule_ref is None


# --------------------------------------------------------------------------- genuinely infeasible


def test_infeasible_problem_returns_infeasible_never_optimal(site, library) -> None:
    """THE criterion: an infeasible problem returns an explicit INFEASIBLE status, no volumes."""
    problem = _problem(_over_power_obligation(site), library, power=200.0, duration=2.0)

    # Independent witness: the annual MILP on the same problem is infeasible too.
    annual = MilpDispatchSolver().solve(problem)
    assert annual.solver_status is SolverStatus.INFEASIBLE

    solver = MpcDispatchSolver(settings=_SETTINGS)
    result = solver.solve(problem)

    assert result.solver_status is SolverStatus.INFEASIBLE
    assert result.solver_status is not SolverStatus.OPTIMAL
    _assert_no_volumes(result)
    assert solver.last_schedule() is None


def test_the_refusal_names_the_window_and_carries_the_solver_reason(site, library) -> None:
    """Quarantine fault 4: the refusal text used to be swallowed. It now reaches the caller."""
    problem = _problem(_over_power_obligation(site), library, power=200.0, duration=2.0)
    result = MpcDispatchSolver(settings=_SETTINGS).solve(problem)
    joined = " | ".join(result.warnings)

    assert "MPC window 1 of 7" in joined
    assert "returned no optimal schedule (infeasible)" in joined
    # The solver's own words travel with the refusal: HiGHS's termination and the model-build
    # warning that explains it.
    assert "HiGHS termination 'infeasible'" in joined
    assert "infeasible by construction" in joined
    # And the old silent-hold text is gone.
    assert "battery held" not in joined


def test_window_outcome_reports_infeasible_with_the_highs_reason(site, library) -> None:
    """The window entry point returns the refusal status and reason, never a bare None."""
    problem = _problem(_over_power_obligation(site), library, power=200.0, duration=2.0)
    inner = MilpDispatchSolver()
    load, ts, interval, _w = inner._load_series(str(site.load_data.half_hourly_file_ref))
    floor = _soc_window_fractions(library)[0] * problem.energy_kwh

    outcome = inner.solve_window_outcome(
        problem, load[:96], ts[:96], interval, 0.88, initial_soe_kwh=floor
    )

    assert outcome.schedule is None
    assert outcome.refusal_status is SolverStatus.INFEASIBLE
    assert any("HiGHS termination 'infeasible'" in r for r in outcome.reasons)
    # The pinned bare-None contract of `solve_window` is unchanged.
    assert (
        inner.solve_window(problem, load[:96], ts[:96], interval, 0.88, initial_soe_kwh=floor)
        is None
    )


# --------------------------------------------------------------------------- the original fault


@pytest.mark.parametrize("duration", [1.0, 2.0, 4.0, 8.0])
def test_feasible_problem_on_a_windowed_release_solves_with_real_volumes(
    site, library, duration
) -> None:
    """Quarantine fault 1: the zero-SoE seed made every window refuse on every windowed release.

    4 h and 8 h are the durations where one half-hour of charging cannot lift SoE 0 to the 15 %
    floor, i.e. exactly where the old seed was infeasible on its own. The pack now starts at the
    usable floor, so a feasible problem solves and its volumes are real.
    """
    soc_min, soc_max, _ = _soc_window_fractions(library)
    assert soc_min > 0.0, "the release under test must declare a usable floor"

    solver = MpcDispatchSolver(settings=_SETTINGS)
    result = solver.solve(_problem(site, library, power=100.0, duration=duration))

    assert result.solver_status is SolverStatus.OPTIMAL, result.warnings[:3]
    assert result.annual_discharge_kwh is not None and result.annual_discharge_kwh > 0.0
    assert result.annual_charge_kwh is not None and result.annual_charge_kwh > 0.0
    schedule = solver.last_schedule()
    assert schedule is not None
    energy = 100.0 * duration
    # The simulated state never leaves the window the planner was given.
    assert float(schedule.soe_kwh.min()) >= soc_min * energy - 1e-6
    assert float(schedule.soe_kwh.max()) <= soc_max * energy + 1e-6


def test_optimal_is_never_reported_with_zero_volumes_across_both_outcomes(site, library) -> None:
    """The property, over a feasible and an infeasible problem: OPTIMAL implies real volumes, and
    anything without real volumes is not OPTIMAL."""
    feasible = _problem(site, library, power=100.0, duration=4.0)
    infeasible = _problem(_over_power_obligation(site), library, power=200.0, duration=2.0)
    for problem in (feasible, infeasible):
        result = MpcDispatchSolver(settings=_SETTINGS).solve(problem)
        if result.solver_status is SolverStatus.OPTIMAL:
            assert result.annual_discharge_kwh is not None and result.annual_discharge_kwh > 0.0
        else:
            _assert_no_volumes(result)


# --------------------------------------------------------------------------- a later window refuses


@dataclass
class _BmsReadsEmpty:
    """A state reading below the pack's usable floor after day 1, as a faulty BMS read would give.

    The ``BatteryModel`` seam is where a live BMS read replaces simulation. A reading outside the
    usable window is a genuine state the next window's MILP cannot start from.
    """

    calls: int = 0

    def step(self, charge_kw: float, discharge_kw: float, soe_kwh: float, dt_hours: float):
        self.calls += 1
        return BatteryState(actual_soe_kwh=0.0, actual_charge_kw=0.0, actual_discharge_kw=0.0)


def test_a_later_refused_window_discards_the_partial_schedule(site, library) -> None:
    """Quarantine fault 2: a refusal after solved windows used to become an absorbing hold.

    Window 1 solves from the floor; the state it hands on (SoE 0) is outside the usable window, so
    window 2 is refused. The whole result degrades with no volumes and the partial day-1 schedule
    is not reported.
    """
    bms = _BmsReadsEmpty()
    solver = MpcDispatchSolver(settings=_SETTINGS, battery_model=bms)
    result = solver.solve(_problem(site, library, power=100.0, duration=4.0))

    assert bms.calls == 48, "window 1 solved and its day was applied before the refusal"
    assert result.solver_status is SolverStatus.INSUFFICIENT_DATA
    assert result.solver_status is not SolverStatus.OPTIMAL
    _assert_no_volumes(result)
    assert solver.last_schedule() is None
    joined = " | ".join(result.warnings)
    assert "MPC window 2 of 7" in joined
    assert "outside the usable state-of-energy window" in joined
