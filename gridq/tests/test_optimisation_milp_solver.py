"""Tests for the real half-hourly dispatch MILP (`MilpDispatchSolver`).

Physics (SoE balance, round-trip efficiency, connection + behind-the-meter bounds, warranty
throughput cap, no simultaneous charge/discharge), determinism, real per-stream value, and the
honest degrade paths (no raise on insufficient data).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.engine.errors import InvalidRequestError
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.tariff_calendar import (
    duos_rate_series,
    duos_windows_from_assumptions,
    energy_price_series,
)
from gridq.schemas import BankableStatus, ImplementationLevel, SolverStatus

# --- library values, read not restated ----------------------------------------------------------
# Efficiency and the warranty cap are release content, so every expectation below is expressed in
# terms of what the release under test declares. A decimal repeated here would be a second, silent
# copy of a library number — exactly what a release move falsifies without failing loudly.


def _rte(assumptions: AssumptionLibrary) -> float:
    """``round_trip_efficiency.ac_round_trip`` as the release under test declares it.

    WHICH FIGURE THESE TESTS PIN: the dispatch input, this key, whatever it currently holds (0.88
    in gb_v0_1, unchanged through gb_v1_8). gb_v1_8 additionally carries
    ``round_trip_efficiency.ac_round_trip_measured_benchmark`` — PNNL's *measured* 2020 AC-AC 0.86
    against the 2030 *projection* 0.88, and 0.86 is what the degradation break-even check is
    computed at — but that entry is marked REFERENCE ONLY and the library states that moving
    ``ac_round_trip`` onto 0.86 is a human decision still owed. Reading the key means these tests
    pin whatever the solver actually optimised against and survive that decision with no
    re-baseline; hardcoding 0.88 (or pre-empting it with 0.86) would break on the day it lands.
    """
    return float(assumptions.get("round_trip_efficiency", "ac_round_trip").value)


def _eta(assumptions: AssumptionLibrary) -> float:
    """One leg of the split AC round trip: ``eta_ch = eta_dis = sqrt(RTE)`` (B-01).

    ``milp_solver._solve_milp`` splits the round trip symmetrically, so ``eta_ch·eta_dis == RTE``
    leaves the closed-cycle £ identity (grid charge == AC discharge ÷ RTE) untouched while the SoE
    trajectory finally charges a discharge the ``1/eta_dis`` of *stored* energy it consumes. Before
    B-01 every loss sat on the charge leg, which understated that draw by ``sqrt(RTE)`` — 6.2% at
    RTE 0.88 — so held-SoE floors certified energy the pack could not deliver at the AC terminals
    (an obligation-BREACH direction, not a conservative one).
    """
    return math.sqrt(_rte(assumptions))


def _cycles_per_year_2h(assumptions: AssumptionLibrary) -> float:
    """``warranty_cycle_limits.max_cycles_per_year_by_duration_h["2"]`` from the release."""
    table = assumptions.get("warranty_cycle_limits", "max_cycles_per_year_by_duration_h").value
    return float(table["2"])


@pytest.fixture
def site(tmp_path):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
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


def test_solves_to_production_optimal_not_bankable(site, assumptions):
    result = MilpDispatchSolver().solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert result.implementation_level == ImplementationLevel.PRODUCTION
    # gb_v0_1 round-trip efficiency is a placeholder, so the dispatch can never be bankable.
    assert result.bankable_status == BankableStatus.NOT_BANKABLE
    assert result.power_kw == 100.0 and result.energy_kwh == 200.0


def test_round_trip_loss_grosses_charge_above_discharge(site, assumptions):
    result = MilpDispatchSolver().solve(_problem(site, assumptions=assumptions))
    assert result.annual_charge_kwh and result.annual_discharge_kwh
    assert result.annual_charge_kwh > result.annual_discharge_kwh  # losses bought on charge
    # charge == discharge / RTE (the exact convention finance grosses up by). This identity is
    # INVARIANT under B-01's split: a closed cycle stores charge·eta_ch kWh and delivers
    # charge·eta_ch·eta_dis at the terminals, so discharge/charge = eta_ch·eta_dis = RTE whichever
    # leg carries the loss. It is precisely why the pre-B-01 total £ was right while the SoE
    # trajectory was not — so this assertion must NOT move, and does not.
    assert result.annual_charge_kwh == pytest.approx(
        result.annual_discharge_kwh / _rte(assumptions), rel=1e-4
    )


def test_real_arbitrage_and_red_band_value_in_the_firm_floor(site, assumptions):
    result = MilpDispatchSolver().solve(_problem(site, assumptions=assumptions))
    by = {v.stream.value: v for v in result.value_streams}
    assert by["TARIFF_ARBITRAGE"].annual_revenue_gbp > 0
    assert by["TARIFF_ARBITRAGE"].firmness.value == "firm"
    assert by["DUOS_RED_BAND_AVOIDANCE"].annual_revenue_gbp > 0
    assert by["DUOS_RED_BAND_AVOIDANCE"].firmness.value == "firm"
    # MIC/ASC headroom is not separately priced (avoids double-counting the same MWh).
    assert by["MIC_ASC_HEADROOM"].annual_revenue_gbp == 0.0
    assert result.priced_revenue_gbp > 0


def test_deterministic_same_problem_same_result(site, assumptions):
    solver = MilpDispatchSolver()
    problem = _problem(site, assumptions=assumptions)
    assert solver.solve(problem).model_dump(mode="json") == solver.solve(problem).model_dump(
        mode="json"
    )


def test_warranty_throughput_cap_binds_for_a_4h_battery(site, assumptions):
    # A 4h battery wants ~1 cycle/day (~365/yr) but the warranty caps a 4h system at 250 cycles/yr.
    result = MilpDispatchSolver().solve(
        _problem(site, power=100.0, duration=4.0, assumptions=assumptions)
    )
    assert "warranty_throughput_cap" in result.constraints_binding
    assert result.equivalent_full_cycles == pytest.approx(250.0, abs=1.0)


def test_connection_limit_binds_with_a_tight_mic(tmp_path, assumptions):
    # MIC 150 over a 60 kW overnight trough leaves 90 kW of charge headroom, far below the 300 kW
    # rating: the WIRE sets the charge rate, which is what `binding_constraints` detects (charge
    # pinned at the load-reduced headroom in a half-hour where that headroom is under rated power).
    # Under B-01 a full 600 kWh cycle draws 600/sqrt(0.88) ≈ 639.6 kWh of grid energy (it drew
    # 600/0.88 ≈ 681.8 kWh when every loss sat on the charge leg), which at 90 kW occupies ~7.1 h of
    # the 8 h night band — so the cycle still completes and the charge bound is still active
    # throughout. (250 kW stopped binding under D38: the peak-levelling tie-break spreads night
    # charging well below a cap that loose, and the diagnostic honestly reports only constraints
    # that actually shape the solved schedule.)
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=3)
    tight = multi_rate_site(path, rows=rows, mic_kw=150.0, max_import_kw=320.0)
    result = MilpDispatchSolver().solve(
        _problem(tight, power=300.0, duration=2.0, assumptions=assumptions)
    )
    assert "connection_charge_limit" in result.constraints_binding


def test_physics_invariants_on_the_solved_schedule(tmp_path, assumptions):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=3)
    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    rte, eta = _rte(assumptions), _eta(assumptions)
    load, timestamps, interval, _ = solver._load_series(str(path))
    schedule, _binding, _warn = solver._solve_milp(
        _problem(site, power=100.0, duration=2.0, assumptions=assumptions),
        load,
        timestamps,
        interval,
        rte,
    )
    assert schedule is not None
    dt = interval / 60.0
    # No simultaneous charge & discharge (RTE<1 makes it suboptimal; the regulariser enforces it).
    assert not np.any((schedule.charge_kw > 1e-6) & (schedule.discharge_kw > 1e-6))
    # Behind-the-meter + rated-power bounds.
    assert np.all(schedule.discharge_kw <= load + 1e-6)
    assert np.all(schedule.charge_kw <= 100.0 + 1e-6)
    assert np.all(schedule.discharge_kw <= 100.0 + 1e-6)
    # State of energy within [0, nameplate]. gb_v0_1 declares no usable SoC window, so nameplate IS
    # the model's bound here. A release carrying warranty_cycle_limits.soc_min/max_fraction narrows
    # the real bound to [soc_min·E, soc_max·E], which this looser pair would no longer pin — the
    # window itself is covered by the D-169 tests in tests/test_milp_dispatch_physics.py.
    assert np.all(schedule.soe_kwh >= -1e-6)
    assert np.all(schedule.soe_kwh <= 200.0 + 1e-6)
    # SoE balance, cyclic, re-derived for the SPLIT round trip (B-01). The constraint the model
    # actually writes (milp_solver `_solve_milp`, "B-01: soe[t] - soe[pred[t]] ...") is
    #     soe[t] − soe[pred[t]] − charge[t]·eta_ch·dt + discharge[t]·dt/eta_dis == 0,
    # with eta_ch = eta_dis = sqrt(RTE) and pred[t] = t−1 inside a segment. So the residual below is
    # the balance itself, with the discharge DIVIDING: delivering 1 kWh at the AC terminals drains
    # 1/eta_dis kWh of store.
    #
    # The old form here asserted `soe[t] − soe[t−1] = charge·RTE·dt − discharge·dt`, i.e. the whole
    # loss on the charge leg and none on discharge. Evaluated on a correct schedule its residual is
    #     charge·dt·(sqrt(RTE) − RTE) + discharge·dt·(1 − 1/sqrt(RTE)),
    # so a full-power 100 kW charge half-hour shows 100·0.5·(0.938083 − 0.88) = 2.904 kWh — exactly
    # the 2.904158 the old assertion reported. That was never a correct invariant under a split
    # round trip; it is arithmetic about a convention the model no longer uses.
    #
    # Tolerance stays 1e-3 and is not doing any work: the schedule arrays are rounded to 6 dp, so
    # the residual floor is ~1e-6·(1 + eta·dt + dt/eta) ≈ 2e-6 kWh. Measured max here is 7.4e-7.
    prev = np.roll(schedule.soe_kwh, 1)
    residual = (
        schedule.soe_kwh
        - prev
        - schedule.charge_kw * (eta * dt)
        + schedule.discharge_kw * (dt / eta)
    )
    assert float(np.max(np.abs(residual))) < 1e-3


def test_degrade_no_half_hourly_series(site, assumptions):
    no_ref = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"half_hourly_file_ref": None})}
    )
    result = MilpDispatchSolver().solve(_problem(no_ref, assumptions=assumptions))
    assert result.solver_status == SolverStatus.INSUFFICIENT_DATA
    assert result.power_kw is None and result.annual_charge_kwh is None  # never invented
    assert any("half-hourly" in w for w in result.warnings)


def test_degrade_no_observed_peak(site, assumptions):
    no_peak = site.model_copy(
        update={"load_data": site.load_data.model_copy(update={"max_import_kw": None})}
    )
    result = MilpDispatchSolver().solve(_problem(no_peak, assumptions=assumptions))
    assert result.solver_status == SolverStatus.INSUFFICIENT_DATA
    assert result.power_kw is None


def test_degrade_unreadable_file(tmp_path, assumptions):
    site = multi_rate_site(tmp_path / "does_not_exist.csv", rows=336)
    result = MilpDispatchSolver().solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.INSUFFICIENT_DATA
    assert result.annual_discharge_kwh is None


def test_solve_rejects_non_dispatch_problem():
    with pytest.raises(InvalidRequestError):
        MilpDispatchSolver().solve("not a problem")  # type: ignore[arg-type]


def test_flat_tariff_yields_no_arbitrage(tmp_path, assumptions):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=3)
    site = multi_rate_site(path, rows=rows)
    flat = site.model_copy(
        update={
            "tariff_stack": site.tariff_stack.model_copy(update={"import_rates": {"single": 0.24}})
        }
    )
    result = MilpDispatchSolver().solve(_problem(flat, assumptions=assumptions))
    by = {v.stream.value: v for v in result.value_streams}
    # A flat tariff has no spread; arbitrage is closed/zero (DUoS red-band can still accrue).
    assert by["TARIFF_ARBITRAGE"].annual_revenue_gbp == 0.0


# --- correctness-gap tests (G1–G8): the LP's OPTIMALITY, not just feasibility ------------------
# The 12 tests above verify every constraint is *respected* and value is *directional*; none pins
# the LP to a known-best value. These do: closed-form optima, temporal placement, SoE-capacity
# binding, gap segmentation, input hygiene and objective↔reported-£ reconciliation.


def _with_rates(site, *, rates: dict[str, float], red: float | None):
    """The fixture site with its import rates / DUoS red rate swapped (all else unchanged)."""
    return site.model_copy(
        update={
            "tariff_stack": site.tariff_stack.model_copy(
                update={"import_rates": rates, "duos_red_rate": red}
            )
        }
    )


def _firm_total_gbp(result) -> float:
    """Sum of the FIRM-floor stream revenues on a DispatchResult."""
    return sum(v.annual_revenue_gbp for v in result.value_streams if v.firmness.value == "firm")


def _rebuilt_price(site, solver, assumptions) -> np.ndarray:
    """Rebuild the exact £/kWh series the LP optimised against, via the public calendar fns."""
    _load, ts, _interval, _warn = solver._load_series(str(site.load_data.half_hourly_file_ref))
    windows, _note = duos_windows_from_assumptions(assumptions)
    energy_price, _reason = energy_price_series(site.tariff_stack.import_rates, ts, windows)
    duos = duos_rate_series(site.tariff_stack, ts, windows)
    return (energy_price if energy_price is not None else np.zeros(len(ts))) + duos


def _cap_horizon_kwh(schedule, assumptions: AssumptionLibrary) -> float:
    """The warranty throughput cap over this horizon, in AC-discharge kWh.

    The model's `throughput_cap` row sums ``discharge·dt``, i.e. energy at the AC terminals, so this
    ceiling is UNMOVED by B-01 — unlike the store-side ceiling it competes with.
    """
    return _cycles_per_year_2h(assumptions) * schedule.energy_kwh / schedule.annual_factor


def _closed_form_optimum(
    schedule, days: int, p_lo: float, p_hi: float, assumptions: AssumptionLibrary
) -> float:
    """The analytic optimum for a strictly two-level price with one trough→peak cycle per day.

    RE-DERIVED for the split AC round trip (B-01), from ``eta_ch = eta_dis = sqrt(RTE)``:

    1. *What one full-depth cycle DELIVERS.* Draining the store's E kWh puts ``E·eta_dis`` =
       ``E·sqrt(RTE)`` kWh at the AC terminals, because the discharge leg now divides. The pre-B-01
       model claimed the whole E, over-certifying deliverable energy by ``1/sqrt(RTE)``.
    2. *What it COSTS.* Storing E kWh buys ``E/eta_ch`` = ``E/sqrt(RTE)`` kWh of grid energy, so per
       AC kWh delivered the gross-up is ``(E/sqrt(RTE)) / (E·sqrt(RTE)) = 1/RTE`` — unchanged. The
       £-per-kWh-delivered structure of the trade is therefore identical; only the VOLUME one cycle
       can deliver moved. Hence ``x·p_hi − (x/RTE)·p_lo`` keeps its shape and x alone rescales.
    3. *Which ceiling binds.* Two independent ceilings on AC-side volume: the store gives
       ``days·E·sqrt(RTE)``, and the warranty cap is written on AC DISCHARGE (`throughput_cap` sums
       ``discharge·dt``), so it is unchanged in AC kWh. Pre-B-01 the two coincided exactly on this
       fixture — 7 days × 200 kWh = the 365-cycle cap prorated over a 7-day horizon = 1400 kWh —
       and the old docstring leaned on that. Under B-01 the store side falls to 1400·sqrt(0.88) =
       1313.32 kWh while the cap stays 1400, so the STORE binds with ~6.2% of cap slack. min()
       keeps this honest either way (and if a release moves RTE to 0.86 the store side falls
       further, so the store keeps binding).

    Break-even is unaffected as a *condition* (``p_hi > p_lo/RTE`` still), which is why the same
    schedules stay optimal in placement while the volumes shrink.
    """
    rte = _rte(assumptions)
    cap_horizon_kwh = _cap_horizon_kwh(schedule, assumptions)
    x = min(days * schedule.energy_kwh * math.sqrt(rte), cap_horizon_kwh)
    return x * p_hi - (x / rte) * p_lo


def test_g1_two_level_optimum_matches_closed_form(tmp_path, assumptions):
    # Two-level price only (red band removed): night 23:00–07:00 at 0.10, day at 0.30, every day
    # identical — the optimum is exactly one full cycle per day, in closed form.
    days = 7
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=days)
    site = _with_rates(
        multi_rate_site(path, rows=rows), rates={"day": 0.30, "night": 0.10}, red=None
    )
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=100.0, duration=2.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    price = _rebuilt_price(site, solver, assumptions)
    p_lo, p_hi = float(price.min()), float(price.max())
    assert p_lo == pytest.approx(0.10) and p_hi == pytest.approx(0.30)  # two-level as built
    dt = schedule.interval_hours
    achieved = float((price * (schedule.discharge_kw - schedule.charge_kw) * dt).sum())
    expected = _closed_form_optimum(schedule, days, p_lo, p_hi, assumptions)
    # B-01 re-baseline: 1313.32 kWh × 0.30 − (1313.32/0.88) × 0.10 = 244.754, where 1313.32 =
    # 7 × 200 × sqrt(0.88) is the week's AC-deliverable energy. Was 260.909 on 1400 kWh, i.e. the
    # old figure × sqrt(RTE) exactly — nothing but the split moved it.
    assert achieved == pytest.approx(expected, rel=1e-3)
    # And state WHICH ceiling the closed form is standing on, so a future change that silently
    # swaps them cannot keep this test green: the store binds, the warranty cap has slack.
    assert days * schedule.energy_kwh * _eta(assumptions) < _cap_horizon_kwh(schedule, assumptions)


def test_g2_adversarial_spread_no_co_dispatch_and_clean_arbitrage(tmp_path, assumptions):
    # The shape that most tempts a money-pump: a near-free trough (0.02) feeding a 60× spike
    # (0.30 day + 0.90 red DUoS = 1.20 in the weekday 16:00–19:00 band). Mon–Fri only so every
    # day carries the red spike. The LP must still never co-dispatch, and its value must be the
    # clean one-cycle-per-day arbitrage into the spike — nothing manufactured.
    days = 5
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=days)
    site = _with_rates(
        multi_rate_site(path, rows=rows), rates={"day": 0.30, "night": 0.02}, red=0.90
    )
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=100.0, duration=2.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    # The property the ε-regulariser + crossover design is responsible for. UNCHANGED by B-01, and
    # the 1e-6 threshold is not touched: churning 1 kWh through the pack costs the same either way
    # (1 grid kWh → eta_ch stored → eta_ch·eta_dis = RTE back out, so 1 − RTE is lost however the
    # legs are split), so the objective's disincentive and the _EPS throughput regulariser both have
    # exactly the same strength as before. Only the deliverable VOLUME below moves.
    assert not np.any((schedule.charge_kw > 1e-6) & (schedule.discharge_kw > 1e-6))
    price = _rebuilt_price(site, solver, assumptions)
    p_lo, p_hi = float(price.min()), float(price.max())
    assert p_lo == pytest.approx(0.02) and p_hi == pytest.approx(1.20)
    dt = schedule.interval_hours
    achieved = float((price * (schedule.discharge_kw - schedule.charge_kw) * dt).sum())
    expected = _closed_form_optimum(schedule, days, p_lo, p_hi, assumptions)
    # B-01 re-baseline: 938.08 kWh × 1.20 − (938.08/0.88) × 0.02 = 1104.380, where 938.08 =
    # 5 × 200 × sqrt(0.88). Was 1177.273 on 1000 kWh — again exactly × sqrt(RTE). The 60× spread
    # cannot manufacture volume the store cannot deliver, which is the whole point of the shape.
    assert achieved == pytest.approx(expected, rel=1e-3)


def test_g3_soe_capacity_binds_and_caps_value(site, assumptions):
    # A tiny store (0.5h × 100kW = 50kWh) fills to exactly its capacity each cycle, and earns
    # strictly less than the same power with 4× the energy.
    solver = MilpDispatchSolver()
    small = solver.solve(_problem(site, power=100.0, duration=0.5, assumptions=assumptions))
    assert small.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    assert float(schedule.soe_kwh.max()) == pytest.approx(50.0, abs=1e-3)
    large = MilpDispatchSolver().solve(
        _problem(site, power=100.0, duration=2.0, assumptions=assumptions)
    )
    assert _firm_total_gbp(small) < _firm_total_gbp(large)


def test_g4_charges_in_cheap_hours_discharges_in_dear_hours(site, assumptions):
    # Temporal placement, not just magnitudes: the mean price faced while charging must sit
    # strictly below the mean price captured while discharging.
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    price = _rebuilt_price(site, solver, assumptions)
    charging = schedule.charge_kw > 1e-6
    discharging = schedule.discharge_kw > 1e-6
    assert charging.any() and discharging.any()
    assert float(price[charging].mean()) < float(price[discharging].mean())


def test_g5_cyclic_soe_closes_on_a_gapless_series(site, assumptions):
    # Explicit cyclic closure, RE-DERIVED for the split round trip (B-01). Closure is a statement
    # about STORED energy: the wrap constraint sets pred[0] = T (the segment's own end), so the
    # model's own row at t=0 is
    #     soe[0] − soe[T] − charge[0]·eta_ch·dt + discharge[0]·dt/eta_dis == 0.
    # Where the losses sit changes the two COEFFICIENTS, never the closure itself — so this test's
    # invariant is unchanged in substance and the residual expression is simply the model's row with
    # eta_ch = eta_dis = sqrt(RTE) instead of (RTE, 1).
    #
    # The old form (charge·RTE, discharge·1) left a residual of charge[0]·dt·(sqrt(RTE) − RTE) +
    # discharge[0]·dt·(1 − 1/sqrt(RTE)) — a pure artefact of the retired convention, which is why it
    # reported 0.767 kWh on a wrap interval that happens to be charging. That interval's kW level is
    # solver-chosen (D38 pins only the peak-bound placement), so it is deliberately NOT restated
    # here as if it were an invariant.
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    dt, eta = schedule.interval_hours, _eta(assumptions)
    residual = (
        schedule.soe_kwh[0]
        - schedule.soe_kwh[-1]
        - schedule.charge_kw[0] * (eta * dt)
        + schedule.discharge_kw[0] * (dt / eta)
    )
    assert abs(float(residual)) < 1e-3


def test_g5_gap_segments_soe_and_never_chains_across_it(tmp_path, assumptions):
    # Delete one metered interval: the solve must warn about the discontinuity and each segment
    # must close its own cyclic SoE — state of energy is never chained across the hole (that
    # would manufacture value from absent data).
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=2)
    df = pd.read_csv(path)
    df = df.drop(index=30)  # remove Monday 15:00 — a hole mid-series
    df.to_csv(path, index=False)
    site = multi_rate_site(path, rows=rows - 1)
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert any("discontinuit" in w for w in result.warnings)
    schedule = solver.last_schedule()
    load, ts, interval, _warn = solver._load_series(str(path))
    deltas = np.diff(ts.values)
    seg_starts = np.flatnonzero(deltas != np.timedelta64(interval, "m")) + 1
    assert seg_starts.size == 1  # exactly the hole we cut
    starts = np.concatenate(([0], seg_starts))
    ends = np.concatenate((seg_starts - 1, [len(load) - 1]))
    dt = interval / 60.0
    eta = _eta(assumptions)
    # Same re-derivation as the gapless case, applied per segment: `pred[start] = end` for EVERY
    # segment, so each one carries its own copy of the model's wrap row
    #     soe[s] − soe[e] − charge[s]·eta_ch·dt + discharge[s]·dt/eta_dis == 0
    # with eta_ch = eta_dis = sqrt(RTE). Segmentation is independent of the loss placement — it is a
    # statement about which intervals may be chained at all — so B-01 moves the coefficients and
    # nothing else. (The old form's residual on the first segment was 2.904 kWh =
    # 100·0.5·(sqrt(0.88) − 0.88), a full-power charge half-hour at the wrap; the second segment
    # starts idle, so it read 0.000 under both conventions — a real but weak check, since with
    # charge[s] = discharge[s] = 0 the row degenerates to soe[s] == soe[e] and the coefficients
    # drop out.)
    for s, e in zip(starts, ends, strict=True):
        residual = (
            schedule.soe_kwh[s]
            - schedule.soe_kwh[e]
            - schedule.charge_kw[s] * (eta * dt)
            + schedule.discharge_kw[s] * (dt / eta)
        )
        assert abs(float(residual)) < 1e-3  # each segment closes on itself


def test_g6_load_series_clips_negatives_with_warning(tmp_path):
    path = tmp_path / "hh.csv"
    write_hh_csv(path, days=1)
    df = pd.read_csv(path)
    df.loc[10, "import_kw"] = -5.0
    df.to_csv(path, index=False)
    load, _ts, _interval, warns = MilpDispatchSolver()._load_series(str(path))
    assert len(load) == 48 and load[10] == 0.0
    assert any("negative" in w for w in warns)


def test_g6_load_series_drops_nulls_with_warning(tmp_path):
    path = tmp_path / "hh.csv"
    write_hh_csv(path, days=1)
    df = pd.read_csv(path)
    df.loc[5, "import_kw"] = np.nan
    df.to_csv(path, index=False)
    load, ts, _interval, warns = MilpDispatchSolver()._load_series(str(path))
    assert len(load) == 47 and len(ts) == 47  # dropped, never back-filled
    assert any("null" in w for w in warns)


def test_g6_load_series_sorts_unordered_rows_with_warning(tmp_path):
    path = tmp_path / "hh.csv"
    write_hh_csv(path, days=1)
    df = pd.read_csv(path)
    df.iloc[::-1].to_csv(path, index=False)  # fully reversed file order
    load, ts, _interval, warns = MilpDispatchSolver()._load_series(str(path))
    assert ts.is_monotonic_increasing and len(load) == 48
    assert any("sorted by timestamp" in w for w in warns)


def test_g7_firm_floor_reconciles_with_the_objective(site, assumptions):
    # The reported firm-floor £ must equal the annualised objective the LP actually maximised:
    # Σ price·(discharge − charge)·dt · annual_factor, price rebuilt independently.
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    price = _rebuilt_price(site, solver, assumptions)
    objective_annual_gbp = (
        float((price * (schedule.discharge_kw - schedule.charge_kw)).sum())
        * schedule.interval_hours
        * schedule.annual_factor
    )
    assert _firm_total_gbp(result) == pytest.approx(objective_annual_gbp, rel=1e-3)


def test_g8_firm_value_weakly_increases_with_power(site, assumptions):
    # Coarse economic monotonicity: more power (fixed duration, generous connection) can never
    # earn less on the firm floor, and must earn strictly more somewhere across the range.
    values = [
        _firm_total_gbp(
            MilpDispatchSolver().solve(
                _problem(site, power=p, duration=2.0, assumptions=assumptions)
            )
        )
        for p in (50.0, 100.0, 150.0)
    ]
    assert values[0] <= values[1] + 1e-6
    assert values[1] <= values[2] + 1e-6
    assert values[2] > values[0]


def test_g9_peak_tiebreak_pins_levelled_red_band_discharge(site, assumptions):
    # Placement (D38, supersedes the unlanded D37 SoE lean): the price objective alone is
    # degenerate inside the flat 0.40 red band (any fixed energy spread across the 6 red half-hours
    # earns the same £). The _EPS_PEAK epigraph tie-break must select the schedule that minimises
    # the residual peak net import — the day's whole deliverable energy LEVELLED across all six red
    # half-hours — identically on every weekday, not an arbitrary 4-of-6 vertex per day, and without
    # moving the achieved £ off the hand-computed optimum. Placement that the peak does not bind
    # (weekend day-rate discharge, sub-peak night charge) stays solver-chosen: deterministic on
    # a machine, but deliberately NOT pinned here (see D38).
    #
    # WHAT LEVELLING NOW YIELDS (B-01 re-derivation, and why it is not 200/3). The levelled kW is
    # (energy delivered in the band) ÷ (band hours). The band is 6 half-hours = 3.0 h; that is
    # unchanged. What changed is the numerator: a full-depth cycle DELIVERS E·eta_dis = E·sqrt(RTE)
    # kWh at the AC terminals, not E, because the discharge leg divides by eta_dis. So
    #     level = E·sqrt(RTE) / 3.0 h = 200·sqrt(0.88)/3 = 187.6166/3 = 62.5389 kW,
    # against the old 200/3 = 66.6667 kW, which is the same figure × sqrt(RTE). The old value was
    # the quantity B-01 identified as over-certified: it asserted the pack could put its full
    # nameplate on the wire, so a held-SoE floor built on it promised energy it cannot deliver.
    #
    # D62 limb 3 (2026-11-22): THE FIXTURE MOVES, THE ASSERTIONS DO NOT. DFS is in every objective
    # (D57, 205ed92). On the shared fixture's postcode the 7-day horizon carries DFS-called
    # half-hours, two of them inside day 0's red band, so day 0 sets the weekly peak and the
    # red-band placement on days 1-4 is a degenerate, solver-chosen vertex: the premise this pin
    # tests no longer exists there. This one test therefore runs the same site at IV1 1AA (DFS
    # zone 1: 82 observed periods a year, which scales to zero called half-hours on a 7-day
    # horizon), and the guard below makes the premise explicit, so a future fixture that brings
    # DFS back fails loudly instead of re-opening a degenerate placement.
    site = site.model_copy(update={"postcode": "IV1 1AA"})
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=100.0, duration=2.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    schedule = solver.last_schedule()
    assert schedule.dfs_price_gbp_per_kwh is None, (
        "this test is about peak levelling; it must run on a horizon with no DFS-called period "
        "(D62 limb 3)"
    )
    e, eta, rte = schedule.energy_kwh, _eta(assumptions), _rte(assumptions)
    red_hours = 3.0  # 16:00–19:00, the 6 red half-hours asserted below
    level_kw = e * eta / red_hours

    # Every weekday (fixture starts Monday): the same levelled E·sqrt(RTE)/3 kW across all 6 red
    # half-hours — the unique minimum-peak point of the £-tied set.
    for day in range(5):
        sl = slice(day * 48, (day + 1) * 48)
        red = schedule.red_band_mask[sl]
        assert int(red.sum()) == 6
        assert red.sum() * schedule.interval_hours == red_hours
        assert np.allclose(schedule.discharge_kw[sl][red], level_kw, atol=1e-3)

    # The epigraph is minimised exactly: max net import after the battery over the whole week
    # is the levelled red band, and the battery never raises the peak elsewhere (charging stays
    # inside the 60 kW overnight trough's headroom). The shave is the levelled kW, so the peak
    # improves by sqrt(RTE) LESS than the pre-B-01 test claimed — 320 − 62.5389 = 257.4611 kW,
    # not 320 − 66.6667: firming credit the pack could not actually deliver is now not credited.
    load, _, _, _ = solver._load_series(site.load_data.half_hourly_file_ref)
    net_import = load + schedule.charge_kw - schedule.discharge_kw
    assert float(net_import.max()) == pytest.approx(320.0 - level_kw, abs=1e-3)

    # The tie-break chose *among* optima: the achieved value is still the closed-form week —
    # 5 weekday cycles into the 0.40 red band + 2 weekend cycles into the 0.30 day rate, all
    # charged at the 0.10 night rate grossed up by round-trip losses. Per cycle the AC volume is
    # x = E·sqrt(RTE) and the grid energy behind it is E/sqrt(RTE) = x/RTE (§2 of
    # `_closed_form_optimum`), so the £ identity keeps its old shape with x in place of E:
    #     5·(x·0.40 − (x/RTE)·0.10) + 2·(x·0.30 − (x/RTE)·0.10) = 338.563, was 360.909 on x = E.
    # It is a WEEKLY identity: the cyclic wrap redistributes charging between the two weekend days
    # (day 6 buys 250 kWh, day 5 buys 213.2), which cannot move the weekly total.
    price = _rebuilt_price(site, solver, assumptions)
    dt = schedule.interval_hours
    achieved = float((price * (schedule.discharge_kw - schedule.charge_kw) * dt).sum())
    x = e * eta
    expected = 5 * (x * 0.40 - (x / rte) * 0.10) + 2 * (x * 0.30 - (x / rte) * 0.10)
    assert achieved == pytest.approx(expected, rel=1e-3)


def test_peak_pass_leaves_a_measured_zero_untouched(tmp_path):
    """T-P3 (D67): the bill's peak-minimising second pass never disturbs a measured zero.

    The site is `test_valuation_zero_dispatch_note.unprofitable_site`, rebuilt here: its own prices
    cannot repay a round trip, so on gb_v1_9 at 100 kW / 2 h the optimum discharges into the
    DFS-called periods and nowhere else (0.0 kWh outside them). Run on every solve during the
    design (D67), the second pass leaked about 0.001 kWh a year of dispatch into schedules like
    this one, and four tests that assert a measured zero went red. The second schedule is
    therefore adopted only when it lowers the peak by more than 0.001 kW; otherwise the first
    schedule is returned bit for bit, which is what this asserts. Dies if the adopt test is made
    unconditional (M-P4).
    """
    hh = tmp_path / "flat.csv"
    rows, _ = write_hh_csv(hh, days=7)
    base = multi_rate_site(hh, rows=rows)
    site = base.model_copy(
        update={
            "tariff_stack": base.tariff_stack.model_copy(
                update={
                    "import_rates": {"day": 0.10, "night": 0.095},
                    "duos_red_rate": 0.0,
                    "duos_amber_rate": 0.0,
                    "duos_green_rate": 0.0,
                    "levies": {"private_wire_non_commodity_residual": 0.13},
                }
            )
        }
    )
    lib = load_assumptions("gb_v1_9")
    schedules = []
    for flag in (False, True):
        solver = MilpDispatchSolver()
        problem = DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=100.0,
            energy_kwh=200.0,
            duration_hours=2.0,
            minimise_published_peak=flag,
        )
        assert solver.solve(problem).solver_status == SolverStatus.OPTIMAL
        schedules.append(solver.last_schedule())
    unflagged, flagged = schedules
    # The premise: the first pass's own answer is a measured zero outside the DFS periods.
    assert unflagged.annual_discharge_outside_dfs_kwh == 0.0
    assert flagged.annual_discharge_outside_dfs_kwh == 0.0
    assert np.array_equal(flagged.charge_kw, unflagged.charge_kw)
    assert np.array_equal(flagged.discharge_kw, unflagged.discharge_kw)
    assert np.array_equal(flagged.soe_kwh, unflagged.soe_kwh)
