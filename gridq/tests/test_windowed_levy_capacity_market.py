"""Time-windowed levies, and the GB Capacity Market supplier obligation that needs them.

**The defect these tests pin.** Every campaign site's ``tariff_stack.levies`` carried CCL, RO,
CfD_ILR, BSUoS, AAHEDC, NuclearRAB and FiT and **no Capacity Market line**, so a real avoided cost
priced at £0 on the pricing path — a D45 defect, not a conservatism. The line could not simply be
added to ``levies``: that map is summed flat and applied to *every* half-hour, so a ~30 p/kWh
Capacity Market rate would have been charged on every charged kWh at 03:00 in July and wrecked the
dispatch. The obligation is charged on 16:00–19:00 working-day November–February consumption only
(SI 2014/3354 reg. 2 "period of high demand"; npower Business Solutions' published pass-through),
so the fix is a **windowed** levy the objective sees in the right half-hours and nowhere else.

**What each mutation point is, and which test goes RED when it is deleted.** This repo has
shipped built-tested-never-called eleven times, every one with a passing test, so each link in the
chain has a test that asserts a NUMBER rather than a call:

* ``milp_solver`` objective (``price = … + windowed_levy``) →
  `test_capacity_market_levy_is_what_makes_the_optimiser_discharge_in_the_window`. The fixture
  has a flat tariff and zero DUoS rates, so the windowed levy is the *only* reason to cycle: delete
  the term and discharge collapses to zero. A test that merely asserted the reported avoidance £
  would NOT catch this — that figure comes from the schedule's array, which survives the deletion.
* ``schedule.windowed_levy_avoidance_gbp`` / the ``DispatchResult`` field /
  ``to_finance_dispatch_energy`` / ``build_cost_lines`` / the cashflow year loop →
  `test_windowed_levy_credit_reaches_the_cost_line` and
  `test_windowed_levy_raises_year_one_cashflow_at_a_FIXED_system_size`. Break any one and the cost
  line or the year-1 P50 cashflow moves. The size is PINNED in the second because the two arms do
  not select the same machine, and differencing two different-sized batteries measures the sizing
  decision rather than the levy — the false premise that made its predecessor fail (see that test's
  docstring).
* the per-candidate ``dispatch_energy=`` argument in `sizing/sizer.py`, i.e. whether the credit
  reaches the ranking the sizer optimises and not just the chosen spec's final cashflows →
  `test_windowed_levy_raises_the_return_the_sizer_maximises_end_to_end`, which asserts the levy
  raises the return at every shared candidate size and lowers it at none.
* The site DATA → `test_grid_supplied_campaign_sites_carry_the_capacity_market_line`. A verifier on
  this workflow refuted a claimed fix that keyed on a record **zero of 49 site files carried**, so
  the levy the engine tests exercise is loaded **verbatim from a real campaign site file**.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import latest_version, load_assumptions
from gridq.dispatch import DispatchProblem, dispatch_constraints_for, to_finance_dispatch_energy
from gridq.finance.financial_model import run_financial_model
from gridq.optimisation import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver, levy_rate_gbp_per_kwh
from gridq.optimisation.tariff_calendar import windowed_levy_rate_series
from gridq.quotes import generate_quote
from gridq.schemas import (
    ConfidenceBand,
    LoadDataSummary,
    MarketStream,
    PhysicalGateStatus,
    SiteModel,
    SolverStatus,
    TariffStack,
    WindowedLevy,
)
from gridq.schemas.connection import ConnectionDetails
from gridq.schemas.consents import ConsentState
from gridq.schemas.counterparty import Counterparty
from gridq.schemas.derived import ceiling_for_key
from gridq.schemas.enums import DataTier
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import _to_optimisation_result

SITES = Path(__file__).resolve().parents[1] / "examples" / "real" / "sites"

#: The seven GRID-SUPPLIED campaign sites. The Capacity Market Supplier Obligation is levied on
#: LICENSED suppliers' Chargeable Demand, so it applies to a grid supply and not to the two
#: private-wire sites below.
GRID_SUPPLIED = (
    "bishops_cleeve_ash_plant",
    "bishops_cleeve_mrf",
    "ewelme",
    "ewelme_invoice_anchored",
    "harwell",
    "knowl_hill",
    "knowl_hill_invoice_anchored",
)
#: Licence-exempt private-wire supplies (EfW private wire). No licensed supplier takes the volume,
#: so there is no supplier obligation to pass through; both files record that the GB grid network
#: stack is deliberately not applied, and tanhouse's non-commodity line is an unattributed
#: back-solved residual that could already contain the charge. A CM line here would be invention.
PRIVATE_WIRE = ("colnbrook", "tanhouse")


def _real_cm_levy() -> dict:
    """The CM_peak record **verbatim from a real campaign site file** — never a hand-written shape.

    The whole point: the engine tests below must exercise the record production actually carries.
    """
    stack = json.loads((SITES / "harwell.json").read_text())["tariff_stack"]
    return stack["windowed_levies"]["CM_peak"]


# --------------------------------------------------------------------------------------------
# 1. The calendar: a windowed levy is charged inside its window and nowhere else
# --------------------------------------------------------------------------------------------


def test_windowed_levy_is_charged_only_inside_its_window():
    levy = WindowedLevy.model_validate(_real_cm_levy())
    tariff = TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        levies={"RO": 0.03273},
        windowed_levies={"CM_peak": levy},
    )
    ts = pd.date_range("2025-08-13 00:00", "2026-08-12 23:30", freq="30min")
    series, notes = windowed_levy_rate_series(tariff, ts)

    rate = levy.rate_gbp_per_kwh
    at = dict(zip(ts, series, strict=True))
    # Inside: a Thursday in January, 16:00–18:30 (period-start, half-open to 19:00).
    assert at[pd.Timestamp("2026-01-08 16:00")] == rate
    assert at[pd.Timestamp("2026-01-08 18:30")] == rate
    # Outside, four different ways — each a measured £0, not a missing input.
    assert at[pd.Timestamp("2026-01-08 15:30")] == 0.0  # before the window
    assert at[pd.Timestamp("2026-01-08 19:00")] == 0.0  # half-open at the top
    assert at[pd.Timestamp("2026-01-10 17:00")] == 0.0  # Saturday
    assert at[pd.Timestamp("2026-07-09 17:00")] == 0.0  # outside November-February
    assert at[pd.Timestamp("2025-12-25 17:00")] == 0.0  # Christmas Day is not a working day
    # 82 working days x 3 h with the three England & Wales bank holidays excluded.
    assert float((series > 0).sum()) * 0.5 == pytest.approx(246.0)
    assert notes and "WINDOWED LEVY 'CM_peak'" in notes[0]


def test_the_flat_levy_map_would_have_charged_the_capacity_market_on_every_half_hour():
    """Why the windowed field exists at all — the trap it exists to avoid, measured."""
    levy = WindowedLevy.model_validate(_real_cm_levy())
    ts = pd.date_range("2025-08-13 00:00", "2026-08-12 23:30", freq="30min")

    flat_stack = TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        levies={"CM_peak": levy.rate_gbp_per_kwh},
    )
    windowed_stack = TariffStack(
        basis=SYNTHETIC_BASIS, provenance=SYNTHETIC_PROVENANCE, windowed_levies={"CM_peak": levy}
    )
    site = _flat_tariff_site(Path("/nonexistent.csv"), rows=len(ts), tariff=flat_stack)

    # In the flat map the rate is a scalar applied to EVERY half-hour by the objective.
    assert levy_rate_gbp_per_kwh(site) == pytest.approx(levy.rate_gbp_per_kwh)
    # Windowed, it lands on 2.8% of them.
    series, _ = windowed_levy_rate_series(windowed_stack, ts)
    assert float((series > 0).mean()) == pytest.approx(0.0281, abs=0.0005)


def test_a_levy_label_may_not_sit_in_both_maps():
    with pytest.raises(ValueError, match="appear in BOTH"):
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            levies={"CM_peak": 0.30581},
            windowed_levies={"CM_peak": WindowedLevy.model_validate(_real_cm_levy())},
        )


# --------------------------------------------------------------------------------------------
# 2. The dispatch objective — the mutation point a reporting test cannot see
# --------------------------------------------------------------------------------------------


def _write_winter_hh(path: Path) -> tuple[int, pd.DatetimeIndex]:
    """A flat 200 kW site load over a November-February horizon (the levy's own season)."""
    ts = pd.date_range("2025-11-03 00:00", "2026-02-27 23:30", freq="30min")
    pd.DataFrame(
        {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kwh": np.full(len(ts), 100.0)}
    ).to_csv(path, index=False)
    return len(ts), ts


def _flat_tariff_site(
    hh_path: Path, *, rows: int, tariff: TariffStack, site_id: str = "site_cm"
) -> SiteModel:
    """A site with a FLAT import tariff and NO DUoS band rates.

    Deliberately gives the optimiser no arbitrage spread and no DUoS band spread, so every cycle
    strictly loses the round-trip losses (plus cycle wear) — unless a windowed levy makes in-window
    discharge worth more than out-of-window charging. That is what makes the objective term the
    single cause of any discharge at all, and therefore what makes deleting it detectable.
    """
    return SiteModel(
        site_id=site_id,
        postcode="OX11 0QX",
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=LoadDataSummary(
            annual_kwh=1_752_000.0,
            half_hourly_file_ref=str(hh_path),
            half_hourly_rows=rows,
            start_date=date(2025, 11, 3),
            end_date=date(2026, 2, 27),
            max_import_kw=200.0,
        ),
        annual_consumption_kwh=1_752_000.0,
        tariff_stack=tariff,
        connection=ConnectionDetails(mic_kw=400.0, asc_kw=400.0),
        consents=ConsentState(),
        physical_gates=PhysicalGateStatus(),
        counterparty=Counterparty(legal_name="CM Test Ltd", company_number="01234567"),
    )


def _flat_stack(*, with_cm: bool, duos_red: float = 0.0) -> TariffStack:
    """A flat-tariff stack; ``duos_red`` > 0 gives the optimiser an independent reason to cycle.

    ``duos_red=0`` is the objective-mutation fixture: the windowed levy is the ONLY reason to cycle.
    The end-to-end test needs a stack that sizes a system with the levy ABSENT too — otherwise the
    baseline run correctly returns no viable configuration and there is no cashflow to compare — so
    it passes a real red rate and measures the DELTA the levy adds on top.
    """
    return TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        supplier="CMTestCo",
        import_rates={"flat": 0.10},
        duos_red_rate=duos_red,
        duos_amber_rate=0.0,
        duos_green_rate=0.0,
        levies={"RO": 0.02},
        windowed_levies=(
            {"CM_peak": WindowedLevy.model_validate(_real_cm_levy())} if with_cm else {}
        ),
    )


def _viable_stack(*, with_cm: bool) -> TariffStack:
    """A day/night + red-band stack that sizes a viable system with OR without the windowed levy.

    The end-to-end delta test needs both runs to price: with `_flat_stack` the baseline correctly
    returns "no viable configuration" (a flat tariff plus a 6 p/kWh red band does not cover capex on
    this fixture), and a run with no financial case cannot be differenced. This is the shape
    `tests/_milp_support` already uses for a viable MILP site, plus the windowed levy under test.
    """
    return TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        supplier="CMTestCo",
        import_rates={"day": 0.30, "night": 0.10},
        duos_red_rate=0.10,
        duos_amber_rate=0.01,
        duos_green_rate=0.001,
        levies={"RO": 0.02},
        windowed_levies=(
            {"CM_peak": WindowedLevy.model_validate(_real_cm_levy())} if with_cm else {}
        ),
    )


def _solve(site: SiteModel, assumptions, *, power_kw: float = 100.0, duration_h: float = 2.0):
    solver = MilpDispatchSolver()
    streams = [MarketStream.TARIFF_ARBITRAGE, MarketStream.DUOS_RED_BAND_AVOIDANCE]
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=power_kw,
        energy_kwh=power_kw * duration_h,
        duration_hours=duration_h,
        requested_streams=streams,
        constraints=dispatch_constraints_for(streams, site, assumptions),
    )
    result = solver.solve(problem)
    return result, solver


@pytest.fixture(scope="module")
def assumptions():
    """The newest release on disk — the same resolution `cli.py` performs (MD-F-15)."""
    return load_assumptions(latest_version())


def test_capacity_market_levy_is_what_makes_the_optimiser_discharge_in_the_window(
    tmp_path_factory, assumptions
):
    """The OBJECTIVE mutation point: delete ``+ windowed_levy`` from ``price`` and this goes RED.

    With a flat tariff and zero DUoS rates there is no spread to arbitrage, so the *only* reason to
    cycle is the windowed levy avoided by in-window discharge. Without the levy the optimiser
    correctly declines every cycle; with it, it cycles and puts the discharge in the window.
    """
    path = tmp_path_factory.mktemp("cm") / "hh.csv"
    rows, _ts = _write_winter_hh(path)

    without, _ = _solve(
        _flat_tariff_site(path, rows=rows, tariff=_flat_stack(with_cm=False)), assumptions
    )
    with_cm, solver = _solve(
        _flat_tariff_site(path, rows=rows, tariff=_flat_stack(with_cm=True)), assumptions
    )

    assert without.solver_status == SolverStatus.OPTIMAL
    assert with_cm.solver_status == SolverStatus.OPTIMAL
    # No windowed levy: no spread anywhere, so the optimiser declines to cycle at all.
    assert without.annual_discharge_kwh == pytest.approx(0.0, abs=1.0)
    # With it: the battery cycles, and the discharge is inside the window it is paid for.
    assert with_cm.annual_discharge_kwh > 1_000.0
    schedule = solver._last_schedule
    assert schedule is not None
    in_window = schedule.windowed_levy_rate_gbp_per_kwh > 0
    discharged_in_window = float(schedule.discharge_kw[in_window].sum())
    discharged_total = float(schedule.discharge_kw.sum())
    assert discharged_total > 0
    assert discharged_in_window / discharged_total > 0.9


def test_windowed_levy_avoidance_is_capped_by_metered_in_window_import(
    tmp_path_factory, assumptions
):
    """Chargeable Demand is Active Import with no export credit, so the £ cannot exceed the load.

    The guard against the £41-73/kW/yr rated-power extrapolation: at 200 kW of rated power on a
    100 kW flat load the avoided cost must still be bounded by the levy on the site's own in-window
    import, because ``discharge_t <= min(power, load_t)``.
    """
    path = tmp_path_factory.mktemp("cm_cap") / "hh.csv"
    rows, _ts = _write_winter_hh(path)
    site = _flat_tariff_site(path, rows=rows, tariff=_flat_stack(with_cm=True))
    result, solver = _solve(site, assumptions, power_kw=200.0, duration_h=4.0)

    schedule = solver._last_schedule
    assert schedule is not None
    rate = schedule.windowed_levy_rate_gbp_per_kwh
    assert rate is not None
    in_window_import_kwh = float((schedule.site_load_kw[rate > 0]).sum()) * 0.5
    ceiling = float(rate.max()) * in_window_import_kwh * schedule.annual_factor

    assert result.windowed_levy_avoidance_gbp is not None
    assert 0.0 < result.windowed_levy_avoidance_gbp <= ceiling + 0.01
    # Net import never goes negative, which is what makes the bound structural rather than asserted.
    net_import = schedule.site_load_kw + schedule.charge_kw - schedule.discharge_kw
    assert net_import.min() > -1e-6


def test_windowed_levy_stays_out_of_the_priced_floor_identity(tmp_path_factory, assumptions):
    """``arbitrage + duos == priced_floor_terms`` must still hold with a windowed levy present.

    Both levy components are decision prices only. Admitting the windowed one into the reported
    floor would double-count it against the ``windowed_levies_avoided`` cost line and break the
    invariant ``tests/test_floor_equals_objective.py`` pins.
    """
    path = tmp_path_factory.mktemp("cm_floor") / "hh.csv"
    rows, _ts = _write_winter_hh(path)
    _result, solver = _solve(
        _flat_tariff_site(path, rows=rows, tariff=_flat_stack(with_cm=True)), assumptions
    )
    schedule = solver._last_schedule
    assert schedule is not None
    assert schedule.arbitrage_value_gbp + schedule.duos_avoidance_value_gbp == pytest.approx(
        schedule.priced_floor_terms_gbp, abs=0.02
    )
    # …and the windowed component is a real, separate, non-zero £ that is NOT in that identity.
    assert schedule.windowed_levy_avoidance_gbp > 0.0


# --------------------------------------------------------------------------------------------
# 3. The finance chain: DispatchResult -> DispatchEnergy -> cost line -> cashflows
# --------------------------------------------------------------------------------------------


def test_windowed_levy_credit_reaches_the_cost_line(tmp_path_factory, assumptions):
    """The three production hops the pipeline makes, in the order it makes them.

    `quotes.pipeline` calls ``engine.selected_dispatch_energy()`` (populated by the sizer through
    `to_finance_dispatch_energy`) and hands it to `run_financial_model`. Deleting the copy in
    `to_finance_dispatch_energy`, the field on `DispatchResult`, or the line in `build_cost_lines`
    all land here as a missing cost line.
    """
    path = tmp_path_factory.mktemp("cm_fin") / "hh.csv"
    rows, _ts = _write_winter_hh(path)
    site = _flat_tariff_site(path, rows=rows, tariff=_flat_stack(with_cm=True))
    dispatch_result, _solver = _solve(site, assumptions, power_kw=100.0, duration_h=2.0)
    energy = to_finance_dispatch_energy(dispatch_result)
    assert energy.windowed_levy_avoidance_gbp is not None
    assert energy.windowed_levy_avoidance_gbp > 0.0

    engine = MilpEngine()
    result = engine.solve(engine.build_optimisation_request(site, assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    model = run_financial_model(
        site, result, assumptions, dispatch_energy=engine.selected_dispatch_energy()
    )
    line = model.cashflows.annual_cost_by_line_gbp["windowed_levies_avoided"]
    assert line is not None
    # A CREDIT is a negative cost. Its magnitude is the £ the dispatch avoided.
    assert line < 0.0
    assert any("windowed_levies_avoided" in w for w in model.cashflows.warnings)


def test_windowed_levy_raises_year_one_cashflow_at_a_FIXED_system_size(
    tmp_path_factory, assumptions
):
    """The cashflow SERIES effect, measured with the system size held fixed in BOTH arms.

    Asserts the NUMBER, not the call: at one pinned spec, year-1 P50 cashflow is strictly higher
    with the windowed levy than without it, and the rise is exactly the avoided levy scaled by the
    fleet-availability factor the year loop applies to it. Every hop is load-bearing. Delete
    ``schedule.windowed_levy_avoidance_gbp``, the ``DispatchResult`` field, the copy in
    `to_finance_dispatch_energy`, the line in `build_cost_lines`, or the ``- windowed_year`` term in
    `build_cashflow_model`'s year loop, and the measured delta collapses to zero.

    WHY THE SPEC IS PINNED, and why its predecessor was wrong. This replaces
    ``test_windowed_levy_raises_year_one_cashflow_end_to_end``, which ran `generate_quote` on both
    arms and asserted the same rise on whatever spec the SIZER selected. That premise was false, and
    the test failed on it: GBP 3,765.33 with the levy against GBP 5,289.81 without, "worse" by
    GBP 1,524.48. It was differencing two DIFFERENT-SIZED batteries and reading the size
    difference as a lost credit. Nothing was lost; see
    `test_windowed_levy_raises_the_return_the_sizer_maximises_end_to_end` for the end-to-end
    position and the reconciliation of that GBP 1,524.48 to the penny.

    The equality in the last assertion rests on a law plus a fixture fact. The law: the no-levy
    arm's dispatch already maximises the arbitrage + DUoS floor, so the levy arm can never finish
    more than the credit ahead of it, at any size, on any site. The fixture fact: here it finishes
    exactly the credit ahead, because the levy costs nothing to chase (the 0.30 day rate and the
    0.10 DUoS red band already put discharge in 16:00-19:00 winter), so the floors are identical
    and only the credit differs. A strict inequality would mean the levy arm bought its credit by
    giving up floor.
    """
    path = tmp_path_factory.mktemp("cm_fixed") / "hh.csv"
    rows, _ts = _write_winter_hh(path)
    power_kw, duration_h = 100.0, 2.0

    def priced(with_cm: bool):
        """One arm: dispatch the PINNED spec, then price it through the production finance model."""
        site = _flat_tariff_site(
            path,
            rows=rows,
            tariff=_viable_stack(with_cm=with_cm),
            site_id=f"site_fixed_{'on' if with_cm else 'off'}",
        )
        dispatch_result, _solver = _solve(
            site, assumptions, power_kw=power_kw, duration_h=duration_h
        )
        assert dispatch_result.solver_status == SolverStatus.OPTIMAL
        # The SIZER's own mapper, so both arms ride the production DispatchResult ->
        # OptimisationResult hop rather than a hand-built result that could drift from it.
        optimisation_result = _to_optimisation_result(
            SizingCandidate(power_kw=power_kw, duration_hours=duration_h),
            dispatch_result,
            assumption_version=assumptions.metadata.version,
            model_version="test_fixed_size",
        )
        return run_financial_model(
            site,
            optimisation_result,
            assumptions,
            dispatch_energy=to_finance_dispatch_energy(dispatch_result),
        )

    on, off = priced(True), priced(False)
    year_one_on = on.case.p50_cashflows[1]
    year_one_off = off.case.p50_cashflows[1]

    # The claim the predecessor meant to make, on the comparison that actually supports it.
    assert year_one_on > year_one_off
    # Material, not float noise: the levy is ~30 p/kWh on real in-window volume.
    assert year_one_on - year_one_off > 1_000.0

    # ATTRIBUTION. Same spec, so the same capex and the same priced floor in both arms: the ONE
    # line that differs is the credit, which is what makes the delta readable as its effect.
    assert on.cashflows.capex.total_gbp == off.cashflows.capex.total_gbp
    assert on.cashflows.annual_revenue_by_stream_gbp == off.cashflows.annual_revenue_by_stream_gbp
    # No windowed levy declared means the line is UNAVAILABLE, never a modelled zero.
    assert off.cashflows.annual_cost_by_line_gbp["windowed_levies_avoided"] is None
    credit = -on.cashflows.annual_cost_by_line_gbp["windowed_levies_avoided"]
    assert credit > 0.0

    # The year-1 treatment, pinned exactly: capacity fade is 1.0 in year 1, the P50 series takes no
    # haircut, and the credit is scaled by the SAME fleet-availability factor the loop applies to
    # revenue (`build_cashflow_model`: ``windowed_year = windowed_credit_line * decay * factor``).
    availability = (on.cashflows.availability_factor_pct or 100.0) / 100.0
    assert year_one_on - year_one_off == pytest.approx(credit * availability, abs=0.02)


def test_windowed_levy_raises_the_return_the_sizer_maximises_end_to_end(
    tmp_path_factory, assumptions
):
    """`generate_quote` + `MilpEngine` -- the exact production path `cli.py:278` runs.

    WHAT MOVES, AND IN WHICH DIRECTION. Making the site's own peak-hour consumption avoidable
    raises the return on every candidate size and lowers it on none (the pointwise assertion
    below). What it does NOT raise is the year-1 GBP the chosen spec earns, because it changes
    WHICH spec is chosen.
    Measured on this fixture, gb_v1_12:

    ==========================  ===============  ===============
    year 1, P50                 levy OFF         levy ON
    ==========================  ===============  ===============
    selected spec               18.05 kW / 8 h   10.0 kW / 4 h
    capex                       GBP 39,168.50    GBP 11,900.00
    arbitrage + DUoS floor      GBP 8,273.74     GBP 2,649.88
    windowed levy avoided       unavailable      GBP 2,346.90
    year-1 P50 cashflow         GBP 5,289.81     GBP 3,765.33
    project IRR                 7.66%            28.31%
    payback                     8.25 yr          3.27 yr
    ==========================  ===============  ===============

    The year-1 cashflow falls GBP 1,524.48 and every penny of it is the SIZE change, not a lost
    credit. Reconciled against `build_cashflow_model`'s own year-1 terms, levy ON minus levy OFF:
    revenue after the perfect-foresight deduction and the 95% availability de-rate is
    GBP 4,274.14 LOWER (a machine less than a third the size), the credit is GBP 2,229.56 HIGHER
    (GBP 2,346.90 x 0.95), the smaller machine's fixed cost lines are GBP 523.66 LOWER, and the
    availability variable-cost offset is GBP 3.56 SMALLER: -4,274.14 + 2,229.56 + 523.66 - 3.56 =
    -1,524.48. The credit is present and correct inside the GBP 3,765.33, and the machine that earns
    it costs GBP 27,268.50 less to build. The levy is load-capped by construction (Chargeable Demand
    is the site's own in-window import), so it saturates early: that is why it pays best on a small
    system, and why the optimum moves DOWN when it is switched on.

    So absolute year-1 cashflow is the wrong quantity to assert on this path. `SizingObjective`
    maximises RETURN subject to the covenant, never absolute GBP, so differencing two arms' chosen
    cashflows measures the sizing decision and not the levy. This test asserts the levy's effect on
    the quantity the sizer actually ranks, plus the pointwise invariant that pins it.
    """
    path = tmp_path_factory.mktemp("cm_e2e") / "hh.csv"
    rows, _ts = _write_winter_hh(path)

    def quoted(with_cm: bool):
        site = _flat_tariff_site(
            path,
            rows=rows,
            tariff=_viable_stack(with_cm=with_cm),
            site_id=f"site_cm_{'on' if with_cm else 'off'}",
        )
        run = generate_quote(site, assumptions, engine=MilpEngine())
        assert run.quote.financial_case is not None
        return run

    on, off = quoted(True), quoted(False)
    case_on, case_off = on.quote.financial_case, off.quote.financial_case

    # 1. The metric the objective ranks on rises, materially (measured 7.66% -> 28.31%).
    assert case_on.irr is not None and case_off.irr is not None
    assert case_on.irr > case_off.irr + 5.0

    # 2. Year-1 cashflow PER POUND OF CAPITAL rises: the honest form of the naive assertion. The GBP
    # fell because the machine shrank; the GBP per GBP built more than doubled, and capital comes
    # back in less than half the time. ``p50_cashflows[0]`` is ``-capex`` by construction
    # (`build_cashflow_model`: ``p50_fcf = [-capex, *p50_ebitda]``).
    capex_on, capex_off = -case_on.p50_cashflows[0], -case_off.p50_cashflows[0]
    assert capex_on > 0.0 and capex_off > 0.0
    per_pound_on = case_on.p50_cashflows[1] / capex_on
    per_pound_off = case_off.p50_cashflows[1] / capex_off
    assert per_pound_on > per_pound_off * 1.5  # measured 0.3164 against 0.1351, i.e. 2.34x
    assert case_on.payback_years is not None and case_off.payback_years is not None
    assert case_on.payback_years < case_off.payback_years

    # 3. POINTWISE, off the persisted sizing grid: the levy never makes a candidate WORSE, and here
    # it makes every shared candidate strictly better. This is the F-02-class invariant the original
    # failure was mistaken for -- if the MILP objective valued the credit while the sizing ranking
    # could not see it, the sizer would be trading reported floor for a credit it never scored, and
    # some candidate would come out lower with the levy on. None does (measured: 74 shared specs, 0
    # regressions, 74 strictly better). It is also the mutation point for the per-candidate
    # ``dispatch_energy=to_finance_dispatch_energy(dispatch_result)`` argument in `sizing/sizer.py`:
    # delete it and every candidate is ranked as though the credit did not exist, so `strictly
    # better` collapses to zero. The fixed-size test above cannot see that seam at all, because it
    # bypasses the sizer.
    #
    # MEASURED, and the reason this assertion exists rather than a cashflow one: with that argument
    # severed, both arms select 18.05 kW / 8 h and the levy arm's year-1 P50 cashflow comes out at
    # GBP 9,314.16 against GBP 5,289.81. The PREDECESSOR TEST WOULD HAVE PASSED on that mutation and
    # failed on the working sizer, i.e. its polarity was inverted: it went green exactly when the
    # credit was invisible to the ranking. The same run also produced one candidate whose return
    # FELL with the levy on, which is the F-02 pathology itself (an objective chasing a window its
    # own ranking cannot score). This assertion is what stands between the two.
    assert on.sizing_grid is not None and off.sizing_grid is not None

    def by_spec(grid):
        """Points keyed by spec AND proxy flag, so a typical-period score is never compared to a
        full-series one (D32: the two stages are different bases, not different results)."""
        return {
            (round(p.power_kw, 4), round(p.duration_hours, 4), p.proxy): p
            for p in grid.points
            if p.project_irr_pct is not None
        }

    points_on, points_off = by_spec(on.sizing_grid), by_spec(off.sizing_grid)
    shared = sorted(set(points_on) & set(points_off))
    assert len(shared) > 20, f"too few shared specs to compare pointwise: {len(shared)}"
    regressions = [
        (spec, points_off[spec].project_irr_pct, points_on[spec].project_irr_pct)
        for spec in shared
        if points_on[spec].project_irr_pct < points_off[spec].project_irr_pct - 1e-9
    ]
    assert not regressions, (
        f"the windowed levy LOWERED the return at {len(regressions)} of {len(shared)} shared "
        f"specs, so the objective and the ranking disagree about what the credit is worth "
        f"(spec, IRR off, IRR on): {regressions[:5]}"
    )
    strictly_better = [
        spec
        for spec in shared
        if points_on[spec].project_irr_pct > points_off[spec].project_irr_pct + 1e-9
    ]
    assert len(strictly_better) == len(shared)


# --------------------------------------------------------------------------------------------
# 4. The site DATA — the shape production actually carries
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", GRID_SUPPLIED)
def test_grid_supplied_campaign_sites_carry_the_capacity_market_line(name):
    site = SiteModel.model_validate(json.loads((SITES / f"{name}.json").read_text()))
    levy = site.tariff_stack.windowed_levies["CM_peak"]

    # The rate, and the window both sources state.
    assert levy.rate_gbp_per_kwh == pytest.approx(0.30581)
    assert levy.months == (1, 2, 11, 12)
    assert [(w.start, w.end) for w in levy.weekday_windows] == [("16:00", "19:00")]
    assert levy.weekend_windows == ()
    # Non-working days are excluded by date, or the avoided cost is overstated 3.5%.
    assert "2025-12-25" in levy.excluded_dates and "2026-01-01" in levy.excluded_dates
    # Sourced, and honest about what it is.
    assert "SI 2014/3354" in (levy.source or "")
    assert "npowerbusinesssolutions.com" in (levy.source or "")
    assert levy.confidence is not None and levy.confidence.value == "low"
    assert "DIRECTION OF EFFECT: UP" in (levy.source or "")
    # The flat map is untouched and still carries the year-round levies.
    assert "CM_peak" not in site.tariff_stack.levies
    assert site.tariff_stack.levies["RO"] == pytest.approx(0.03273)
    # The stack's own narrative no longer claims the charge is deliberately excluded.
    assert "EXCLUDED DELIBERATELY: the Capacity Market" not in (site.tariff_stack.source or "")

    # The TYPED register record (validation_requirements §8.2). The tariff prose is what the ENGINE
    # reads; the technical report's assumption register is generated from THIS, never authored, so a
    # value with no record is invisible to the register however well documented the prose is.
    record = next(
        r
        for r in (site.derived_assumptions or [])
        if r.key == "tariff_stack.windowed_levies.CM_peak"
    )
    assert record.kind == "derived_input"  # a rate is a fact, not a permission
    assert record.value == pytest.approx(0.30581)
    assert record.unit == "gbp_per_kwh"
    assert record.derivation_method == "sourced_default"
    # LOW, and it must stay LOW: `ceiling_for_key` returns LOW for an unrecognised key, and adding
    # this key to `SHAPE_LIKE_KEYS` would RAISE the ceiling to MEDIUM. The rate is another
    # supplier's published forecast for a delivery year whose charged months are unpublished, and
    # whether it is avoidable at all depends on an unseen contract term.
    assert record.confidence_ceiling is ConfidenceBand.LOW
    assert ceiling_for_key(record.key) is ConfidenceBand.LOW
    assert any("legislation.gov.uk/uksi/2014/3354" in s for s in record.sources)
    assert any("npowerbusinesssolutions.com" in s for s in record.sources)
    assert any("bank-holidays" in s for s in record.sources)
    # The ask names BOTH the rate and the pass-through-vs-fixed term: on fixed terms the avoided
    # cost is not cash-real in the contract year however accurate the rate is.
    assert "PASSES THROUGH" in record.confirmation_ask
    assert "DIRECTION OF EFFECT: UP" in record.basis


@pytest.mark.parametrize("name", PRIVATE_WIRE)
def test_private_wire_sites_carry_no_capacity_market_line(name):
    """A licence-exempt private-wire supply has no licensed supplier, so no obligation to pass on.

    Adding the line here would be invention, and on tanhouse it would also risk double-counting:
    its 13.14 p/kWh non-commodity line is a back-solved residual of unknown composition.
    """
    site = SiteModel.model_validate(json.loads((SITES / f"{name}.json").read_text()))
    assert site.tariff_stack.windowed_levies == {}
    assert "PRIVATE-WIRE SITE" in (site.tariff_stack.source or "")


# --------------------------------------------------------------------------------------------
# 5. The P90 >= P50 trap a signed cost line opens
# --------------------------------------------------------------------------------------------


def test_a_positive_windowed_line_never_puts_p90_above_p50(assumptions):
    """A windowed levy that came out as a COST must not be haircut, or the cautious case wins.

    The haircut shrinks the number it multiplies. Applied to a positive (cost) windowed line it
    would deduct LESS in P90 than in P50 and lift P90 above it — the same invariant the negative
    merchant-haircut clamp in `build_cashflow_model` exists to protect, reached from the other side.
    Driven straight through `build_cashflow_model` with a signed £ on the `DispatchEnergy` seam,
    because that is the only input that can produce the sign.
    """
    from gridq.finance.cashflows import DispatchEnergy, build_cashflow_model

    site = SiteModel.model_validate(json.loads((SITES / "harwell.json").read_text()))

    class _Result:
        selected_power_kw = 200.0
        selected_energy_kwh = 400.0
        duration_hours = 2.0
        value_streams: list = []
        warnings: list = []

    for avoidance in (-500.0, 0.0, 500.0):
        model = build_cashflow_model(
            site,
            _Result(),
            assumptions,
            dispatch_energy=DispatchEnergy(
                annual_charge_kwh=120_000.0,
                annual_discharge_kwh=100_000.0,
                windowed_levy_avoidance_gbp=avoidance,
            ),
        )
        for year, (p50, p90) in enumerate(
            zip(model.p50_ebitda_gbp, model.p90_ebitda_gbp, strict=True), start=1
        ):
            assert p90 <= p50 + 0.005, f"avoidance={avoidance} year={year}: P90 {p90} > P50 {p50}"
