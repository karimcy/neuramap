"""Dispatch physics and solver-accuracy regression tests for ``MilpDispatchSolver``.

One test file for the sequenced 2026-08-12 change set in
``src/gridq/optimisation/milp_solver.py``, in the order the findings were worked:

- **DEG-01** — the objective priced NO marginal cycle cost, so cycling was free and the break-even
  arbitrage spread sat ~65% below the true one.
- **B-01** — all round-trip losses on the charge side, so the SoE trajectory understated the stored
  energy a discharge commitment consumes by ~sqrt(RTE): held-SoE floors certified deliverability the
  battery could not meet (an obligation-BREACH direction).
- **D-169 / JSON-F-46** — SoE swept 0-100% of nameplate with no usable window or DoD limit.
- **D-174 / JSON-F-49** — negative net import (the signature of PV surplus) clipped to zero, so the
  modelled profile was corrupted upward in exactly the midday hours a battery would charge.
- **B-17** — unity power factor assumed globally with no labelled warning and no data ask.
- **B-09** — ``mip_rel_gap`` never set and the achieved gap never recorded on binary solves.

New file by construction: it asserts the CORRECTED physics, so it must not be mixed into the files
whose assertions pin the pre-fix behaviour.
"""

from __future__ import annotations

import math
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import Assumption, AssumptionLibrary, load_assumptions
from gridq.assumptions.models import AssumptionStatus
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import (
    MilpDispatchSolver,
    ObjectiveWeights,
    _achieved_mip_gap,
    _charge_power_factor,
    _soc_window_fractions,
    _UnpriceableDispatch,
    degradation_cost_gbp_per_kwh,
)
from gridq.schemas import (
    AvailabilityCommitment,
    AvailabilityWindow,
    BankableStatus,
    MarketStream,
    SolverStatus,
)
from gridq.schemas.enums import Confidence
from gridq.value_stack.interface import DispatchConstraintDescriptor

_RELEASE = "gb_v0_1"
_RTE = 0.88
_ETA = math.sqrt(_RTE)  # B-01: the symmetric split of the AC round trip


# --------------------------------------------------------------------------- helpers


def _assumption(value: object, unit: str) -> Assumption:
    """A minimal, fully-documented `Assumption` for injecting a release key in a test."""
    return Assumption(
        value=value,
        unit=unit,
        source="test fixture — not a sourced number, never loaded by a production run",
        status=AssumptionStatus.PLACEHOLDER,
        owner_or_reviewer="Olly",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 8, 12),
        notes="injected by tests/test_milp_dispatch_physics.py",
    )


def _with_keys(library: AssumptionLibrary, section: str, **keys: Assumption) -> AssumptionLibrary:
    """``library`` plus extra keys in one section — the release-gated paths under test."""
    merged = {**getattr(library, section), **keys}
    return library.model_copy(update={section: merged})


def _problem(site, assumptions, *, power=200.0, duration=2.0, constraints=None) -> DispatchProblem:
    return DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=power,
        energy_kwh=power * duration,
        duration_hours=duration,
        constraints=list(constraints or []),
    )


def _solved(site, assumptions, **kw):
    """``(result, schedule)`` from one full ``solve``."""
    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions, **kw))
    return result, solver.last_schedule()


def _retariff(site, *, day: float, night: float, duos: float = 0.0, levies=None):
    """The same site on a different import tariff (the spread is what DEG-01 is measured on)."""
    from gridq.schemas.tariff import TariffStack

    return site.model_copy(
        update={
            "tariff_stack": TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="PhysicsTestCo",
                import_rates={"day": day, "night": night},
                duos_red_rate=duos,
                levies=dict(levies or {}),
            )
        }
    )


@pytest.fixture(scope="module")
def hh(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    """The shared deterministic 7-day metered series + its row count."""
    path = tmp_path_factory.mktemp("milp_physics") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return path, rows


@pytest.fixture(scope="module")
def site(hh):
    path, rows = hh
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def library() -> AssumptionLibrary:
    return load_assumptions(_RELEASE)


# --------------------------------------------------------------------------- DEG-01


def test_deg01_the_default_objective_carries_a_sourced_marginal_cycle_cost(library) -> None:
    """THE DEFECT. No release carries a cycle cost, and the objective used to charge none — so
    cycling was free. The default is now the sourced C-34 first-pass figure (£21.3/MWh discharged),
    in £/kWh because that is what the objective's prices and volumes are in, and it announces
    itself as a data ask on every solve."""
    cost, note = degradation_cost_gbp_per_kwh(library)

    assert cost == pytest.approx(0.0213), "£21.3/MWh discharged expressed in £/kWh"
    assert "DEGRADATION COST NOT IN THE ASSUMPTION RELEASE" in note
    assert "C-34" in note and "£32.7/MWh" in note
    assert "INDICATIVE, not bankable" in note


def test_deg01_a_release_key_overrides_the_literature_default(library) -> None:
    """A release that publishes the number wins outright, and says so instead of asking for it."""
    lib = _with_keys(
        library,
        "degradation",
        marginal_cycle_cost_gbp_per_mwh_discharged=_assumption(8.5, "GBP/MWh"),
    )
    cost, note = degradation_cost_gbp_per_kwh(lib)

    assert cost == pytest.approx(0.0085)
    assert "read from the assumption release" in note
    assert "NOT IN THE ASSUMPTION RELEASE" not in note


def test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin(site, library) -> None:
    """The consequence the finding sized. On a 2.5p/kWh day/night spread the round trip clears
    efficiency alone but not efficiency PLUS wear, so the pre-DEG-01 objective booked the cycle as
    FIRM revenue while it was loss-making. Pinned against the same solve with the cost published as
    zero — the only honest way to hold everything else fixed."""
    thin = _retariff(site, day=0.125, night=0.10)
    zero_cost = _with_keys(
        library,
        "degradation",
        marginal_cycle_cost_gbp_per_mwh_discharged=_assumption(0.0, "GBP/MWh"),
    )

    free_cycling, free_schedule = _solved(thin, zero_cost)
    priced_wear, priced_schedule = _solved(thin, library)

    # WP-CORE-01 triage (2026-11-16). This used to assert ZERO total discharge with wear priced.
    # D57 limb 1 (docs/decisions.md: DFS "OPEN and PRICED on every site") and D57 item 4 (205ed92,
    # 2026-08-18, "build the DFS engine branch") put DFS utilisation into the objective on every
    # release, gb_v0_1 included. This 7-day fixture has 8 DFS-called half-hours (day 0, 18:00-22:00)
    # paying £0.1597/kWh on top of the energy price, which clears wear comfortably. Measured: with
    # wear priced, 19,565.734 kWh/yr is discharged and ALL of it is DFS delivery
    # (`dfs_delivered_mwh` = 19.565734), 0.0 kWh outside the DFS periods; with wear at zero, 117,394
    # kWh/yr is discharged OUTSIDE them. So DEG-01's claim (the thin TARIFF trade no longer pays) is
    # unchanged and is now asserted where it lives: outside the DFS-called periods.
    def _outside_dfs_kwh(schedule) -> float:
        mask = schedule.dfs_price_gbp_per_kwh > 0
        return float(
            schedule.discharge_kw[~mask].sum() * schedule.interval_hours * schedule.annual_factor
        )

    assert free_cycling.annual_discharge_kwh is not None
    assert _outside_dfs_kwh(free_schedule) > 0.0, (
        "fixture must exercise the defect: free cycling books the thin tariff trade"
    )
    assert _outside_dfs_kwh(priced_schedule) == pytest.approx(0.0), (
        "with the marginal cycle cost in the objective the trade no longer pays"
    )
    assert priced_wear.annual_discharge_kwh == pytest.approx(
        priced_schedule.dfs_delivered_mwh * 1000.0
    ), "every kWh still discharged with wear priced must be DFS delivery"


def test_deg01_a_spread_that_clears_wear_still_trades(site, library) -> None:
    """The cost is a filter, not a brake: a spread comfortably above the break-even still cycles,
    so DEG-01 does not simply switch dispatch off."""
    wide = _retariff(site, day=0.30, night=0.10)
    result, schedule = _solved(wide, library)

    assert result.solver_status is SolverStatus.OPTIMAL
    assert schedule is not None
    assert result.annual_discharge_kwh is not None and result.annual_discharge_kwh > 0.0
    assert schedule.priced_floor_terms_gbp > 0.0


def test_deg01_is_a_decision_cost_only_and_never_reaches_reported_revenue(site, library) -> None:
    """The reconciliation, exactly as F-03 did it for the levy. The reported floor is still
    Σ (energy + DUoS)·(discharge − charge)·Δt rebuilt from the schedule's own arrays: no wear £
    leaks into revenue, and no wear £ is double-counted."""
    wide = _retariff(site, day=0.30, night=0.10, duos=0.10)
    _result, schedule = _solved(wide, library)
    assert schedule is not None

    price = schedule.energy_price_gbp_per_kwh + schedule.duos_rate_gbp_per_kwh
    rebuilt = (
        float(((schedule.discharge_kw - schedule.charge_kw) * price).sum())
        * schedule.interval_hours
        * schedule.annual_factor
    )
    assert schedule.priced_floor_terms_gbp == pytest.approx(round(rebuilt, 2), abs=0.01)


def test_deg01_applies_on_the_aging_weight_branch_too(site, library) -> None:
    """`ObjectiveWeights` are regularisers, not the cycle cost: a non-zero-weight (MPC) build must
    carry `c_deg` as well, or the rolling-horizon path keeps pricing cycling at £0."""
    thin = _retariff(site, day=0.125, night=0.10)
    solver = MilpDispatchSolver()
    load, net, ts, interval, _warn = solver._load_series_signed(
        str(thin.load_data.half_hourly_file_ref)
    )
    schedule, _binding, _warnings = solver._solve_milp(
        _problem(thin, library),
        load,
        ts,
        interval,
        _RTE,
        net_load_kw=net,
        weights=ObjectiveWeights(aging_alpha=0.005, aging_beta=0.0185),
    )
    assert schedule is not None
    # WP-CORE-01 triage (2026-11-16): same re-statement as the test above. D57 item 4 (205ed92)
    # prices DFS in the objective, so the only discharge left with wear priced is DFS delivery
    # (measured 19,565.734 kWh/yr, all inside the 8 DFS-called half-hours). The aging-branch claim,
    # that the thin TARIFF trade is not booked, is asserted outside the DFS periods.
    dfs = schedule.dfs_price_gbp_per_kwh > 0
    assert float(schedule.discharge_kw[~dfs].sum()) == pytest.approx(0.0)
    assert schedule.annual_discharge_kwh == pytest.approx(schedule.dfs_delivered_mwh * 1000.0)


# --------------------------------------------------------------------------- B-01


def test_b01_the_split_efficiency_preserves_the_finance_gross_up(site, library) -> None:
    """B-01 must not move the £ identity finance depends on. Over the closed cyclic horizon
    charge·eta_ch == discharge/eta_dis, so total grid charge is still exactly total discharge ÷ RTE
    — the single AC round-trip number the cashflow model grosses up by."""
    wide = _retariff(site, day=0.30, night=0.10)
    result, schedule = _solved(wide, library)
    assert schedule is not None
    assert result.annual_charge_kwh and result.annual_discharge_kwh

    assert result.annual_discharge_kwh / result.annual_charge_kwh == pytest.approx(_RTE, abs=1e-6)


def test_b01_the_soe_recurrence_divides_the_discharge_by_eta(site, library) -> None:
    """THE DEFECT, at the recurrence. Every interval must satisfy
    soe_t − soe_{t−1} = charge·sqrt(RTE)·Δt − discharge·Δt/sqrt(RTE); the pre-B-01 form
    (charge·RTE in, discharge out) does NOT satisfy it, so this pins the corrected physics."""
    wide = _retariff(site, day=0.30, night=0.10)
    _result, schedule = _solved(wide, library)
    assert schedule is not None
    dt = schedule.interval_hours
    soe = schedule.soe_kwh

    residual = (
        soe[1:]
        - soe[:-1]
        - schedule.charge_kw[1:] * (_ETA * dt)
        + schedule.discharge_kw[1:] * (dt / _ETA)
    )
    assert float(np.abs(residual).max()) < 1e-3

    old_form = (
        soe[1:] - soe[:-1] - schedule.charge_kw[1:] * (_RTE * dt) + schedule.discharge_kw[1:] * dt
    )
    assert float(np.abs(old_form).max()) > 1e-3, (
        "fixture must discriminate: the pre-B-01 recurrence has to be violated by this schedule"
    )


def test_b01_a_held_energy_commitment_holds_the_stored_energy_it_costs(site, library) -> None:
    """The obligation-breach direction. A contract holding 200 kWh of DELIVERABLE energy needs
    200/eta_dis kWh in store, not 200 — the pre-B-01 floor certified ~6.6% more deliverable energy
    (at RTE 0.88) than the battery could put on the AC terminals."""
    held = 200.0
    commitment = AvailabilityCommitment(
        commitment_id="held-b01",
        stream=MarketStream.CAPACITY_MARKET,
        # A hold-only commitment still declares the power it is a hold for; only the energy floor
        # is under test here, and the 1 kW reservation cannot bind against a 200 kW battery.
        committed_power_kw=1.0,
        held_energy_kwh=held,
        windows=[AvailabilityWindow(start_time=time(0, 0), end_time=time(0, 0))],
        tenor_years=15.0,
        reference="TEST/B01-001",
    )
    wide = _retariff(site, day=0.30, night=0.10)
    result, schedule = _solved(
        wide,
        library,
        constraints=[
            DispatchConstraintDescriptor(
                constraint_id="held-b01",
                description="B-01 held-energy test descriptor",
                kind="held_state_of_energy",
                parameters=commitment,
            )
        ],
    )
    assert result.solver_status is SolverStatus.OPTIMAL
    assert schedule is not None

    assert float(schedule.soe_kwh.min()) >= held / _ETA - 1e-3
    assert held / _ETA > held + 1.0, "the fixture must make the gross-up observable"
    assert "held_soe:held-b01" in result.constraints_binding


# --------------------------------------------------------------------------- D-169


def test_d169_an_absent_soc_window_warns_and_asks_rather_than_inventing_one(library) -> None:
    """The register's own caveat forbids substituting a literature window for the datasheet's, so
    an absent key leaves the 0-100% sweep visible and says exactly what that costs."""
    lo, hi, note = _soc_window_fractions(library)

    assert (lo, hi) == (0.0, 1.0)
    assert note is not None
    assert "USABLE SoC WINDOW NOT IN THE ASSUMPTION RELEASE" in note
    assert "BMS reserve" in note and "D-169" in note


def test_d169_an_invalid_window_is_refused_as_a_pair(library) -> None:
    """Never half-applied and never silently corrected: an inverted window is rejected whole."""
    lib = _with_keys(
        library,
        "warranty_cycle_limits",
        soc_min_fraction=_assumption(0.9, "fraction"),
        soc_max_fraction=_assumption(0.1, "fraction"),
    )
    lo, hi, note = _soc_window_fractions(lib)

    assert (lo, hi) == (0.0, 1.0)
    assert note is not None and "not a valid window" in note


def test_d169_a_released_soc_window_bounds_the_state_of_energy(site, library) -> None:
    """THE DEFECT. With the warranty window published, SoE stays inside it — no schedule may sweep
    into the BMS reserve the same solve's cycle cap is honouring — and usable energy is reported
    separately from nameplate."""
    lib = _with_keys(
        library,
        "warranty_cycle_limits",
        soc_min_fraction=_assumption(0.05, "fraction"),
        soc_max_fraction=_assumption(0.95, "fraction"),
    )
    wide = _retariff(site, day=0.30, night=0.10)
    result, schedule = _solved(wide, lib, power=200.0, duration=2.0)
    assert result.solver_status is SolverStatus.OPTIMAL
    assert schedule is not None

    nameplate = 400.0
    assert float(schedule.soe_kwh.min()) >= 0.05 * nameplate - 1e-6
    assert float(schedule.soe_kwh.max()) <= 0.95 * nameplate + 1e-6
    usable = (0.95 - 0.05) * nameplate
    assert any(
        f"usable energy {round(usable, 3)} kWh of {round(nameplate, 3)} kWh nameplate" in w
        for w in result.warnings
    ), result.warnings


def test_d169_the_usable_window_reduces_deliverable_energy(site, library) -> None:
    """The window is a real constraint, not a label: less usable energy means less annual
    discharge, all else equal."""
    lib = _with_keys(
        library,
        "warranty_cycle_limits",
        soc_min_fraction=_assumption(0.2, "fraction"),
        soc_max_fraction=_assumption(0.8, "fraction"),
    )
    wide = _retariff(site, day=0.30, night=0.10)
    full_sweep, _ = _solved(wide, library)
    windowed, _ = _solved(wide, lib)

    assert full_sweep.annual_discharge_kwh and windowed.annual_discharge_kwh
    assert windowed.annual_discharge_kwh < full_sweep.annual_discharge_kwh


# --------------------------------------------------------------------------- D-174


def _pv_csv(
    path: Path,
    *,
    import_kw: float,
    generation_kw: float,
    days: int = 2,
    from_hour: int = 11,
    to_hour: int = 15,
    red_band_kw: float | None = None,
) -> int:
    """A metered series with an on-site generation column that goes net-EXPORT midday.

    ``red_band_kw`` raises the weekday 16:00-19:00 import so the battery has somewhere valuable to
    discharge to — without it the discharge opportunity, not the charge headroom, is what binds.
    """
    n = days * 48
    ts = pd.date_range("2025-01-06 00:00", periods=n, freq="30min")
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    imp = np.full(n, import_kw)
    if red_band_kw is not None:
        imp[weekday & (hour >= 16) & (hour < 19)] = red_band_kw
    gen = np.zeros(n)
    gen[(hour >= from_hour) & (hour < to_hour)] = generation_kw  # midday surplus
    pd.DataFrame(
        {
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "import_kw": imp,
            "export_kw": gen,
        }
    ).to_csv(path, index=False)
    return n


def test_d174_the_generation_channel_is_netted_into_the_signed_position(tmp_path: Path) -> None:
    """THE DEFECT. The loader already carries a generation column (``generation_kw`` is an alias of
    ``export_kw``); the solver now nets it, so midday surplus reaches the model as a NEGATIVE net
    position instead of being clipped upward to a positive import."""
    path = tmp_path / "pv.csv"
    _pv_csv(path, import_kw=50.0, generation_kw=150.0)
    load, net, _ts, _interval, warnings = MilpDispatchSolver()._load_series_signed(str(path))

    assert float(net.min()) == pytest.approx(-100.0), "50 kW import − 150 kW generation"
    assert float(load.min()) == 0.0, "the reporting/billing basis stays non-negative"
    assert float(load.max()) == pytest.approx(50.0)
    assert any("on-site generation present" in w for w in warnings)
    assert any("negative net import reading" in w for w in warnings)


def test_d174_surplus_is_real_charge_headroom_against_the_connection(tmp_path: Path) -> None:
    """The corruption the finding named: clipping the surplus away understated the charge headroom
    in exactly the hours a battery would charge from it.

    Fixture built so the CHARGE HEADROOM is the only thing that binds. A 95 kW base import against
    a 100 kW MIC leaves 5 kW of headroom everywhere except a one-hour surplus window, where 150 kW
    of on-site generation puts the true net position at −55 kW. The clip does two wrong things at
    once there: it hides the 55 kW of extra headroom AND it claims 0 kW of site load, so the
    pre-D-174 ceiling is exactly the bare connection (100 kW). A 300 kW weekday red-band peak gives
    the battery far more discharge opportunity than either solve can charge for, so BOTH saturate
    their charge ceiling — which is what makes the two ceilings directly comparable."""
    path = tmp_path / "pv.csv"
    rows = _pv_csv(
        path,
        import_kw=95.0,
        generation_kw=150.0,
        days=7,
        from_hour=12,
        to_hour=13,
        red_band_kw=300.0,
    )
    site = multi_rate_site(path, rows=rows, mic_kw=100.0, max_import_kw=300.0)
    site = _retariff(site, day=0.10, night=0.10, duos=0.30)

    solver = MilpDispatchSolver()
    load, net, ts, interval, _warn = solver._load_series_signed(str(path))
    surplus = net < 0
    assert surplus.any(), "the fixture must contain surplus half-hours"
    problem = _problem(site, load_assumptions(_RELEASE), power=200.0, duration=4.0)

    clipped_only, _b, _w = solver._solve_milp(problem, load, ts, interval, _RTE)
    signed, _b2, _w2 = solver._solve_milp(problem, load, ts, interval, _RTE, net_load_kw=net)
    assert clipped_only is not None and signed is not None

    assert float(clipped_only.charge_kw[surplus].max()) == pytest.approx(100.0, abs=1e-3), (
        "the clip turned the −55 kW net position into 0 kW of load, so the ceiling was the bare MIC"
    )
    assert float(signed.charge_kw[surplus].max()) == pytest.approx(155.0, abs=1e-3), (
        "the true signed net position makes the whole surplus available as charge headroom"
    )
    assert float(signed.charge_kw[~surplus].max()) <= 100.0 - 95.0 + 1e-6, (
        "outside the surplus the 95 kW import still leaves only 5 kW against the 100 kW MIC"
    )


def test_d174_no_generation_column_and_no_negatives_is_byte_identical(hh) -> None:
    """Every existing input is untouched: without a generation column and without negative
    readings, the signed series IS the clipped series and no new warning is emitted."""
    path, _rows = hh
    load, net, _ts, _interval, warnings = MilpDispatchSolver()._load_series_signed(str(path))

    assert np.array_equal(load, net)
    assert not any("generation" in w for w in warnings)
    assert not any("negative" in w for w in warnings)


# --------------------------------------------------------------------------- B-17


def test_b17_unity_power_factor_is_a_labelled_warning_and_a_data_ask(library) -> None:
    """No site in this schema carries a reactive series, so no power factor is invented — the
    exposure is stated instead, with the DUoS formulae it bears on."""
    pf, note = _charge_power_factor(library)

    assert pf == 1.0
    assert "UNITY POWER FACTOR ASSUMED" in note
    assert "0.90-0.95" in note and "exceeded-capacity" in note
    assert "No power factor is invented here" in note


def test_b17_a_released_power_factor_derates_the_charge_bound(hh, library) -> None:
    """When a release supplies a sourced factor, the kW charge bound becomes
    ``min(MIC, ASC) × pf`` — the connection limits kVA, not kW, so charging at full kW near the MIC
    is exactly the case that can trigger an exceeded-capacity charge the unity-PF model says is
    impossible.

    Pinned on a 100 kW MIC against the fixture's 60 kW overnight trough: the charge bound is the
    connection, at 40 kW at unity and 30 kW at 0.90 PF."""
    path, rows = hh
    lib = _with_keys(library, "mic_asc_headroom", assumed_power_factor=_assumption(0.90, "ratio"))
    pf, note = _charge_power_factor(lib)
    assert pf == pytest.approx(0.90)
    assert "de-rated to kVA at power factor 0.9" in note

    tight = _retariff(
        multi_rate_site(path, rows=rows, mic_kw=100.0), day=0.30, night=0.10, duos=0.10
    )
    _unity_result, unity = _solved(tight, library, power=200.0, duration=4.0)
    _derated_result, derated = _solved(tight, lib, power=200.0, duration=4.0)
    assert unity is not None and derated is not None

    assert float(unity.charge_kw.max()) == pytest.approx(100.0 - 60.0, abs=1e-3)
    assert float(derated.charge_kw.max()) == pytest.approx(100.0 * 0.90 - 60.0, abs=1e-3)


def test_b17_an_unusable_power_factor_is_refused_not_corrected(library) -> None:
    lib = _with_keys(library, "mic_asc_headroom", assumed_power_factor=_assumption(1.4, "ratio"))
    pf, note = _charge_power_factor(lib)

    assert pf == 1.0
    assert "not a usable power factor" in note


# --------------------------------------------------------------------------- B-09


def test_b09_a_binary_solve_pins_and_records_the_achieved_gap(site, library) -> None:
    """THE DEFECT. Only the termination string was checked; the requested and achieved optimality
    gaps were both absent, so the reported £ carried no statement of its distance from proven
    optimal. A binary (MPC-shaped) solve now records the achieved gap."""
    wide = _retariff(site, day=0.30, night=0.10)
    solver = MilpDispatchSolver()
    load, net, ts, interval, _warn = solver._load_series_signed(
        str(wide.load_data.half_hourly_file_ref)
    )
    schedule, _binding, warnings = solver._solve_milp(
        _problem(wide, library),
        load[:96],
        ts[:96],
        interval,
        _RTE,
        net_load_kw=net[:96],
        use_binary=True,
    )

    assert schedule is not None
    gap_notes = [w for w in warnings if "optimality gap" in w]
    assert gap_notes, warnings
    assert "requested 1e-06" in gap_notes[0]


def test_b09_the_lp_path_claims_no_mip_gap(site, library) -> None:
    """An LP has no MIP gap; the crossover returns a basic optimal vertex. The default annual solve
    must therefore say nothing about a gap rather than imply a measured one."""
    wide = _retariff(site, day=0.30, night=0.10)
    result, _schedule = _solved(wide, library)

    assert result.solver_status is SolverStatus.OPTIMAL
    assert not any("optimality gap" in w for w in result.warnings), result.warnings


def test_b09_a_gap_above_tolerance_degrades_and_never_prices(
    site, library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above tolerance the incumbent is not proven optimal to the £ rounding, so the run degrades to
    INSUFFICIENT_DATA with no volumes — never a priced number carrying a silent accuracy claim."""
    import gridq.optimisation.milp_solver as milp

    monkeypatch.setattr(milp, "_achieved_mip_gap", lambda _model: (1e-2, "stubbed"))
    wide = _retariff(site, day=0.30, night=0.10)
    solver = MilpDispatchSolver()
    load, net, ts, interval, _warn = solver._load_series_signed(
        str(wide.load_data.half_hourly_file_ref)
    )

    with pytest.raises(milp._UnpriceableDispatch, match="relative optimality gap"):
        solver._solve_milp(
            _problem(wide, library),
            load[:96],
            ts[:96],
            interval,
            _RTE,
            net_load_kw=net[:96],
            use_binary=True,
        )

    # ...and the MPC window entry point turns that refusal into "no window", not an exception.
    assert (
        solver.solve_window(_problem(wide, library), load[:96], ts[:96], interval, _RTE, 0.0)
        is None
    )


def test_b09_an_unreadable_gap_reports_itself_as_unreadable() -> None:
    """A linopy/highspy version that stops exposing the gap must degrade to an honest note, never to
    an exception on the pricing path and never to an implied gap it did not read."""

    class _NoSolverModel:
        solver_model = object()

    gap, note = _achieved_mip_gap(_NoSolverModel())

    assert gap is None
    assert "SOLVER OPTIMALITY GAP UNREADABLE" in note


# ------------------------------------------- the corrected closed form (B-01 re-baseline)
#
# ``tests/test_optimisation_milp_solver.py`` pins the PRE-B-01 analytic optimum in six places
# (``test_g1_two_level_optimum_matches_closed_form``, ``test_g2_adversarial_spread_...``,
# ``test_g9_peak_tiebreak_...`` and the three SoE-residual assertions). Those are stale by exactly
# the split-efficiency factor and belong to another batch's file, so they are NOT edited here — the
# orchestrator re-baselines them. What must not be lost in the meantime is the *replacement*
# invariant, so it is pinned here, in this batch's own file: the corrected closed form, and the
# proof that the move is √RTE and nothing else.


def _two_level_closed_form(schedule, days: int, p_lo: float, p_hi: float) -> float:
    """The analytic optimum for a strictly two-level price, CORRECTED for B-01.

    Pre-B-01 the store was assumed to deliver its full nameplate energy at the AC terminals, so the
    per-day volume was ``energy``. Draining ``energy`` kWh of stored charge actually delivers
    ``energy·η_dis`` kWh at the terminals, so the per-day deliverable volume is ``energy·√RTE``.
    The warranty cap is written on AC discharge and is unchanged, hence the ``min``.

    The £ identity is untouched: the losses are still bought at ``p_lo`` on ``x/RTE`` kWh of grid
    import, because η_ch·η_dis = RTE by construction.
    """
    cap_horizon_kwh = 365.0 * schedule.energy_kwh / schedule.annual_factor  # gb_v0_1 2h cap
    x = min(days * schedule.energy_kwh * _ETA, cap_horizon_kwh)
    return x * p_hi - (x / _RTE) * p_lo


def test_b01_the_corrected_two_level_closed_form_is_the_old_one_times_sqrt_rte(hh, library) -> None:
    """The re-baseline, stated as an equality rather than a new magic number.

    A strictly two-level price with one trough→peak cycle a day has an exact optimum. Under B-01 the
    optimiser realises exactly ``√RTE`` of the pre-B-01 figure — because the only thing that changed
    is how much AC energy one full-depth cycle can deliver — and the achieved value matches the
    corrected closed form. Both halves are asserted: the ratio (so the CAUSE is pinned, not just the
    level) and the absolute (so an offsetting pair of errors cannot hide).
    """
    path, rows = hh
    days = 7
    wide = _retariff(multi_rate_site(path, rows=rows), day=0.30, night=0.10)
    result, schedule = _solved(wide, library, power=100.0, duration=2.0)

    assert result.solver_status is SolverStatus.OPTIMAL
    assert schedule is not None
    price = np.asarray(schedule.energy_price_gbp_per_kwh, dtype=float) + np.asarray(
        schedule.duos_rate_gbp_per_kwh, dtype=float
    )
    p_lo, p_hi = float(price.min()), float(price.max())
    assert p_lo == pytest.approx(0.10) and p_hi == pytest.approx(0.30), "two-level as built"

    dt = schedule.interval_hours
    achieved = float((price * (schedule.discharge_kw - schedule.charge_kw) * dt).sum())
    corrected = _two_level_closed_form(schedule, days, p_lo, p_hi)
    cap_horizon_kwh = 365.0 * schedule.energy_kwh / schedule.annual_factor
    pre_b01 = (
        min(days * schedule.energy_kwh, cap_horizon_kwh) * p_hi
        - (min(days * schedule.energy_kwh, cap_horizon_kwh) / _RTE) * p_lo
    )

    assert achieved == pytest.approx(corrected, rel=1e-3)
    assert achieved / pre_b01 == pytest.approx(_ETA, rel=1e-5), (
        "the whole move is the split of the AC round trip — nothing else"
    )
    # And the spread here (0.30 - 0.10/0.88 = 18.6p/kWh) hugely clears wear, so DEG-01 must NOT be
    # what moved this number. Stated explicitly so a future reader does not attribute it wrongly.
    c_deg, _note = degradation_cost_gbp_per_kwh(library)
    assert p_hi - p_lo / _RTE > 5.0 * c_deg


def test_b01_deliverable_energy_per_cycle_is_nameplate_times_eta_not_nameplate(hh, library) -> None:
    """The physical statement behind the re-baseline, measured on a single day so exactly one
    full-depth cycle is available: total AC discharge is ``energy·√RTE``, not ``energy`` — the
    quantity a held-SoE floor used to over-certify."""
    path = Path(str(hh[0]))
    one_day = path.parent / "one_day.csv"
    rows, _ts = write_hh_csv(one_day, days=1)
    wide = _retariff(multi_rate_site(one_day, rows=rows), day=0.30, night=0.10)
    _result, schedule = _solved(wide, library, power=100.0, duration=2.0)

    assert schedule is not None
    delivered = float((schedule.discharge_kw * schedule.interval_hours).sum())
    assert delivered == pytest.approx(schedule.energy_kwh * _ETA, rel=1e-4)
    assert delivered < schedule.energy_kwh, "the pre-B-01 model claimed the full nameplate"


def test_d169_a_start_state_outside_the_usable_window_is_refused_with_the_reason(
    hh, library
) -> None:
    """The D-169 fallout on the rolling-horizon path, caught rather than left to HiGHS.

    ``mpc_solver`` starts its loop at SoE 0 — true while the window was 0-100%, outside it once a
    release declares one. The ``soe_initial`` row then needs the whole reserve charged inside the
    first half-hour, which is possible only while ``power·η_ch·Δt ≥ soc_min·energy``: it holds at
    1-2 h and fails at 4-8 h, so the long-duration specs went dark on a bare "infeasible". The start
    state must be REFUSED with its reason, and never quietly lifted onto the floor — that would
    credit energy the pack never bought.
    """
    path, rows = hh
    windowed = _with_keys(
        library,
        "warranty_cycle_limits",
        soc_min_fraction=_assumption(0.15, "fraction of rated energy capacity"),
        soc_max_fraction=_assumption(0.95, "fraction of rated energy capacity"),
    )
    site_w = _retariff(multi_rate_site(path, rows=rows), day=0.30, night=0.10)
    solver = MilpDispatchSolver()
    load, _net, ts, interval, _w = solver._load_series_signed(str(path))

    # 4 h: the reserve (60 kWh) is more than one half-hour of charging (100 x sqrt(0.88) x 0.5 =
    # 46.9 kWh) can reach, which is precisely the case that used to come back INFEASIBLE.
    with pytest.raises(_UnpriceableDispatch, match="outside the usable state-of-energy window"):
        solver._solve_milp(
            _problem(site_w, windowed, power=100.0, duration=4.0),
            load[:96],
            ts[:96],
            interval,
            _RTE,
            initial_soe_kwh=0.0,
            use_binary=True,
        )

    # Started AT the floor the very same window solves — so the refusal is about the start state,
    # not about the window making long durations undispatchable.
    at_floor, _binding, _warn = solver._solve_milp(
        _problem(site_w, windowed, power=100.0, duration=4.0),
        load[:96],
        ts[:96],
        interval,
        _RTE,
        initial_soe_kwh=0.15 * 400.0,
        use_binary=True,
    )
    assert at_floor is not None

    # And a release with NO declared window is untouched: SoE 0 is inside [0, nameplate].
    unchanged, _b, _w2 = solver._solve_milp(
        _problem(site_w, library, power=100.0, duration=4.0),
        load[:96],
        ts[:96],
        interval,
        _RTE,
        initial_soe_kwh=0.0,
        use_binary=True,
    )
    assert unchanged is not None


def test_d169_the_annual_production_path_never_trips_the_start_state_check(site, library) -> None:
    """Guard-rail on the guard-rail: ``solve`` supplies no start state (the horizon wraps
    cyclically), so the check above is unreachable from the production path on ANY release."""
    windowed = _with_keys(
        library,
        "warranty_cycle_limits",
        soc_min_fraction=_assumption(0.15, "fraction of rated energy capacity"),
        soc_max_fraction=_assumption(0.95, "fraction of rated energy capacity"),
    )
    result, schedule = _solved(site, windowed, power=100.0, duration=4.0)

    assert result.solver_status is SolverStatus.OPTIMAL
    assert schedule is not None
    assert float(schedule.soe_kwh.min()) >= 0.15 * 400.0 - 1e-6
    assert float(schedule.soe_kwh.max()) <= 0.95 * 400.0 + 1e-6


def test_the_bankability_gate_checks_the_degradation_key_the_solve_actually_read(
    site, library
) -> None:
    """Provenance completeness. ``degradation_cost_gbp_per_kwh`` reads either of two release homes,
    so the bankability gate must examine BOTH — gating only the better-named key would let a
    placeholder-status wear cost from the key shipped releases really use pass unexamined. Every
    shipped release carries it at ``needs_review`` at best, so the verdict must stay not-bankable
    even when the value is present and read."""
    published = _with_keys(
        library,
        "battery_opex",
        degradation_marginal_cost_gbp_per_mwh=_assumption(21.3, "GBP/MWh discharged"),
    )
    cost, note = degradation_cost_gbp_per_kwh(published)

    assert cost == pytest.approx(0.0213), "the legacy home is read, not skipped"
    assert "read from the assumption release" in note
    assert "NOT IN THE ASSUMPTION RELEASE" not in note

    result, _schedule = _solved(site, published)
    assert result.bankable_status is BankableStatus.NOT_BANKABLE
