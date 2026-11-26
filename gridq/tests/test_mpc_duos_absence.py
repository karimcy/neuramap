"""The MPC assembly path must report an absent DUoS band rate too (D45).

`MpcDispatchSolver.solve` builds its own DUoS rate series at assembly time — the series the
FULL-YEAR accumulated schedule reports its `duos_avoidance_value_gbp` against. The rolling-horizon
window solves route through `MilpDispatchSolver._solve_milp` and so warn on their own objective
price, but this assembly-time series was a second, independent consumer of the discarding wrapper.
Left unmigrated it would have been the one place an absent band rate went quiet, which is the whole
defect D45's amendment is about: an absent input must not read as a measured £0.

Kept separate from `test_duos_null_consumers.py` because the MPC loop is slow (a MILP per window),
so this is deliberately one short horizon rather than a matrix.
"""

from __future__ import annotations

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.mpc_solver import MpcDispatchSolver
from gridq.schemas import SolverStatus

_LABEL = "DUoS BAND RATE ABSENT, NOT ZERO (D45)"


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("mpc_duos") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=3)
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v0_1")


def _solve(site, assumptions, tariff_update: dict[str, object]):
    patched = site.model_copy(
        update={"tariff_stack": site.tariff_stack.model_copy(update=tariff_update)}
    )
    solver = MpcDispatchSolver(
        settings=MpcSettings(prediction_horizon_days=2.0, application_horizon_days=1.0)
    )
    return solver.solve(
        DispatchProblem(
            site_model=patched,
            assumptions=assumptions,
            power_kw=100.0,
            energy_kwh=200.0,
            duration_hours=2.0,
        )
    )


def test_mpc_reports_all_null_duos_band_rates(site, assumptions) -> None:
    """The Colnbrook/Tanhouse shape, dispatched through the MPC rather than the annual MILP."""
    result = _solve(
        site,
        assumptions,
        {"duos_red_rate": None, "duos_amber_rate": None, "duos_green_rate": None},
    )
    assert result.solver_status == SolverStatus.OPTIMAL
    notes = [w for w in result.warnings if w.startswith(_LABEL)]
    assert notes != [], "the MPC assembly series must report the absence, not price it at £0"
    assert "red, amber, green" in notes[0]


def test_mpc_stays_silent_when_every_band_is_declared(site, assumptions) -> None:
    """A declared 0.0 is a fact, so the complete-data run must not carry the label."""
    result = _solve(
        site,
        assumptions,
        {"duos_red_rate": 0.10, "duos_amber_rate": 0.0, "duos_green_rate": 0.0},
    )
    assert result.solver_status == SolverStatus.OPTIMAL
    assert [w for w in result.warnings if w.startswith(_LABEL)] == []
