"""Tests for the load-service obligation in the dispatch solver (issue 017 / D42, P-B).

Verified on the real linopy/HiGHS solver over the shared 7-day fixture: the declared
shortfall is a CONSTANT forced draw (never in the priced charge/discharge series, so the
objective cannot credit import-displacement for load the wire could never deliver), charging
headroom shrinks by the declared addition inside windows, the battery pre-positions the
worst-case occurrence energy before every window, and an impossible obligation is warned —
never silently relaxed.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.schemas import AvailabilityWindow, ProvisioningRequirement

POWER_KW = 200.0
ENERGY_KWH = 400.0


@pytest.fixture(scope="module")
def site_and_lib(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("prov_milp") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _requirement(**overrides) -> ProvisioningRequirement:
    kwargs: dict = {
        "requirement_id": "hgv-1",
        # weekday 17:00-19:00 — inside the fixture's 320 kW red-band peak
        "windows": [
            AvailabilityWindow(days_of_week=[0, 1, 2, 3, 4], start_time=time(17), end_time=time(19))
        ],
        "additional_load_kw": 300.0,
        "tenor_years": 10.0,
    }
    kwargs.update(overrides)
    return ProvisioningRequirement(**kwargs)


def _solve(site, lib):
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=POWER_KW,
            energy_kwh=ENERGY_KWH,
            duration_hours=ENERGY_KWH / POWER_KW,
            constraints=[],
        )
    )
    return result, solver.last_schedule()


def _with_requirement(site, req: ProvisioningRequirement):
    return site.model_copy(update={"provisioning_requirements": [req]})


# fixture facts: load 320 kW weekday 17-19, conn limit 500 kW, addition 300 kW
# => unmet = 320 + 300 - 500 = 120 kW over 4 half-hours/day => 240 kWh per occurrence
UNMET_KW = 120.0
OCCURRENCE_KWH = 240.0


def test_no_requirement_leaves_the_solve_untouched(site_and_lib) -> None:
    site, lib = site_and_lib
    result, schedule = _solve(site, lib)
    assert schedule is not None
    assert schedule.load_service_kw is None
    assert not any(b.startswith("load_service") for b in result.constraints_binding)


def test_obligation_is_forced_served_and_never_priced(site_and_lib) -> None:
    site, lib = site_and_lib
    base_result, base_schedule = _solve(site, lib)
    result, schedule = _solve(_with_requirement(site, _requirement()), lib)
    assert schedule is not None and schedule.load_service_kw is not None

    # the forced draw is exactly the derived shortfall, only in the windows
    unmet = schedule.load_service_kw
    assert float(unmet.max()) == pytest.approx(UNMET_KW)
    in_window = unmet > 0
    assert int(in_window.sum()) == 5 * 4  # 5 weekdays x 4 half-hours
    assert np.all(unmet[in_window] == pytest.approx(UNMET_KW))

    # never in the priced series: the priced discharge still fits inside the metered load
    # and shares the inverter with the forced service
    assert np.all(schedule.discharge_kw[in_window] <= POWER_KW - UNMET_KW + 1e-6)
    # charging is physically excluded in-window (headroom is negative with the addition)
    assert np.all(schedule.charge_kw[in_window] <= 1e-6)

    # the obligation always binds and is reported
    assert f"load_service:{_requirement().requirement_id}" in result.constraints_binding

    # it costs economics (the forced draw consumes energy that would have arbitraged):
    # the priced floor with the obligation can never exceed the unconstrained one
    def _priced(res) -> float:
        return sum(v.annual_revenue_gbp - v.annual_cost_gbp for v in res.value_streams)

    assert _priced(result) <= _priced(base_result) + 1e-6


def test_pre_window_soe_holds_the_worst_case_level(site_and_lib) -> None:
    site, lib = site_and_lib
    _, schedule = _solve(_with_requirement(site, _requirement()), lib)
    assert schedule is not None and schedule.load_service_kw is not None
    unmet = schedule.load_service_kw
    in_window = unmet > 0
    # occurrence starts: first in-window instant of each run
    starts = np.flatnonzero(in_window & ~np.roll(in_window, 1))
    assert starts.size == 5
    # the instant BEFORE each start holds at least the worst-case occurrence energy
    pre = starts - 1
    assert np.all(schedule.soe_kwh[pre] >= OCCURRENCE_KWH - 1e-3)


def test_daily_curve_requirement_compiles_by_slot(site_and_lib) -> None:
    site, lib = site_and_lib
    profile = [0.0] * 48
    for slot in (34, 35, 36, 37):  # 17:00-19:00 local
        profile[slot] = 250.0
    req = _requirement(additional_load_kw=None, additional_load_profile_daily_kw=profile)
    _, schedule = _solve(_with_requirement(site, req), lib)
    assert schedule is not None and schedule.load_service_kw is not None
    unmet = schedule.load_service_kw
    # 320 + 250 - 500 = 70 kW on exactly the same 20 weekday instants
    assert float(unmet.max()) == pytest.approx(70.0)
    assert int((unmet > 0).sum()) == 5 * 4


def test_over_power_obligation_is_warned_never_relaxed(site_and_lib) -> None:
    site, lib = site_and_lib
    req = _requirement(additional_load_kw=800.0)  # unmet 620 kW >> 200 kW battery
    result, _ = _solve(_with_requirement(site, req), lib)
    joined = " ".join(result.warnings)
    assert "infeasible by construction" in joined
    assert "never silently relaxed" in joined


def test_recharge_infeasible_names_reinforcement(site_and_lib) -> None:
    site, lib = site_and_lib
    # cap barely above the overnight trough: near-zero gap headroom, can't ever refill
    req = _requirement(import_cap_kw=65.0, additional_load_kw=100.0)
    result, _ = _solve(_with_requirement(site, req), lib)
    joined = " ".join(result.warnings)
    assert "recharge-infeasible" in joined
    assert "connection reinforcement" in joined


def test_provisioned_solve_is_deterministic(site_and_lib) -> None:
    site, lib = site_and_lib
    prov_site = _with_requirement(site, _requirement())
    _, s1 = _solve(prov_site, lib)
    _, s2 = _solve(prov_site, lib)
    assert s1 is not None and s2 is not None
    assert np.array_equal(s1.charge_kw, s2.charge_kw)
    assert np.array_equal(s1.discharge_kw, s2.discharge_kw)
    assert np.array_equal(s1.soe_kwh, s2.soe_kwh)


# ------------------------------------------- sizing floors + no-viable (P-C)


def test_engine_no_viable_when_recharge_infeasible(site_and_lib) -> None:
    """The D39 verdict carries the provisioning reason — never a manufactured optimum."""
    from gridq.optimisation.engine import MilpEngine
    from gridq.quotes import generate_quote
    from gridq.schemas import SelectionStatus

    site, lib = site_and_lib
    req = _requirement(import_cap_kw=65.0, additional_load_kw=100.0)
    run = generate_quote(_with_requirement(site, req), lib, engine=MilpEngine())
    assert run.result.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION
    assert run.result.selected_power_kw is None
    joined = " ".join(run.result.warnings)
    assert "connection reinforcement" in joined


def test_engine_no_viable_when_floor_exceeds_domain(site_and_lib) -> None:
    from gridq.optimisation.engine import MilpEngine
    from gridq.quotes import generate_quote
    from gridq.schemas import SelectionStatus

    site, lib = site_and_lib
    # unmet peak 320 + 800 - 500 = 620 kW; end-of-tenor floor > the 500 kW connection itself
    req = _requirement(additional_load_kw=800.0)
    run = generate_quote(_with_requirement(site, req), lib, engine=MilpEngine())
    assert run.result.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION
    joined = " ".join(run.result.warnings)
    # N7 (WP-0 review 2026-08-10): REBASELINED from "exceeds every candidate" — the old verdict
    # was declared off the COARSE SEARCH GRID (1.5x the site's own peak, 8 h), a structural
    # constant, so a floor its connection could carry was refused. The verdict now cites the
    # connection, and this fixture still earns it: the floor genuinely exceeds MIC/ASC.
    assert "requires more import capacity than this connection has" in joined
    assert "declared limit is 500 kW" in joined
    assert "connection reinforcement" in joined


def test_search_floors_respected_by_the_two_stage_search(site_and_lib) -> None:
    """Every candidate the search evaluates clears the obligation floor (feasible case)."""
    from gridq.optimisation.engine import MilpEngine
    from gridq.quotes import generate_quote

    site, lib = site_and_lib
    engine = MilpEngine()
    generate_quote(_with_requirement(site, _requirement()), lib, engine=engine)
    sizing = engine.last_sizing_run()
    assert sizing is not None and sizing.candidates_evaluated
    # floor: 120 kW / 240 kWh uplifted by 10y fade — every evaluated candidate clears it
    retention_floor_kw = 120.0  # day-one floor is the loose lower bound either way
    for ev in sizing.candidates_evaluated:
        assert ev.candidate.power_kw >= retention_floor_kw - 1e-6
        assert ev.candidate.energy_kwh >= 240.0 - 1e-6


# ------------------------------------------- LOAD_SERVICE stream + value (P-D)


def _stream(result, sid):
    import gridq.schemas as s

    return next(v for v in result.value_streams if v.stream == s.MarketStream[sid])


def test_load_service_stream_closed_without_a_requirement(site_and_lib) -> None:
    site, lib = site_and_lib
    result, _ = _solve(site, lib)
    ls = _stream(result, "LOAD_SERVICE")
    assert ls.annual_revenue_gbp == 0.0
    import gridq.schemas as s

    assert ls.stream not in s.GATED_UPSIDE_STREAMS


def test_unreferenced_requirement_is_conditional_and_out_of_the_floor(site_and_lib) -> None:
    import gridq.schemas as s

    site, lib = site_and_lib
    req = _requirement(
        declared_counterfactual_gbp_per_year=45_000.0,
        counterfactual_basis="DNO reinforcement quote ref RT-123, annualised over 15y",
    )
    assert req.reference is None
    result, _ = _solve(_with_requirement(site, req), lib)
    ls = _stream(result, "LOAD_SERVICE")
    # the declared £ is visible but the firmness is NOT contracted — out of the floor
    assert ls.annual_revenue_gbp == pytest.approx(45_000.0)
    assert ls.firmness != s.Firmness.CONTRACTED
    joined = " ".join(ls.warnings)
    assert "modelling input, not evidence" in joined


def test_referenced_counterfactual_prices_contracted(site_and_lib) -> None:
    import gridq.schemas as s

    site, lib = site_and_lib
    req = _requirement(
        reference="contract-RTF-2026-07",
        counterparty="Robert Thomas Farms",
        declared_counterfactual_gbp_per_year=45_000.0,
        counterfactual_basis="DNO reinforcement quote ref RT-123, annualised over 15y",
    )
    result, schedule = _solve(_with_requirement(site, req), lib)
    ls = _stream(result, "LOAD_SERVICE")
    assert ls.annual_revenue_gbp == pytest.approx(45_000.0)
    assert ls.firmness == s.Firmness.CONTRACTED
    # measured forced delivery rides as evidence
    assert schedule.load_service_energy_mwh is not None
    assert schedule.load_service_energy_mwh > 0


def test_no_counterfactual_prices_zero_never_invented(site_and_lib) -> None:
    site, lib = site_and_lib
    req = _requirement(reference="contract-RTF-2026-07")
    result, _ = _solve(_with_requirement(site, req), lib)
    ls = _stream(result, "LOAD_SERVICE")
    assert ls.annual_revenue_gbp == 0.0
    joined = " ".join(ls.warnings)
    assert "no declared counterfactual" in joined
