"""A priced TARIFF_ARBITRAGE stream that earns £0 must say the zero was a DECISION.

Why this file exists. ``DUOS_RED_BAND_AVOIDANCE`` reading £0 because ``duos_red_rate`` is NULL and
``TARIFF_ARBITRAGE`` reading £0 because the optimiser refused to cycle are two completely different
facts that used to render as the same figure. D45's guard separates the null case at the gate; the
note under test separates the other one at the price. Telling them apart previously required
re-deriving the dispatch economics by hand (the 2026-08-15 Tanhouse investigation), which is the
cost this note removes.

**Both tests drive a REAL MILP solve.** No hand-built ``HalfHourlyDispatchSchedule`` appears here:
this repo has repeatedly shipped built-tested-never-called defects whose tests passed against a
fabricated input, so the zero dispatch is one the solver actually chose, from a site whose own
prices cannot repay a round trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.valuation import ZERO_DISPATCH_LABEL, build_value_streams
from gridq.schemas import MarketStream

RELEASE = "gb_v1_9"
POWER_KW = 100.0
DURATION_H = 2.0


def _solve(site, assumptions):
    """Run the production dispatch solver and return its solved schedule."""
    solver = MilpDispatchSolver()
    solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=assumptions,
            power_kw=POWER_KW,
            energy_kwh=POWER_KW * DURATION_H,
            duration_hours=DURATION_H,
        )
    )
    schedule = solver.last_schedule()
    assert schedule is not None, "the solver returned no schedule"
    return schedule


def _arbitrage(streams):
    """The TARIFF_ARBITRAGE entry from a value-stream list."""
    return next(v for v in streams if v.stream == MarketStream.TARIFF_ARBITRAGE)


@pytest.fixture
def assumptions():
    return load_assumptions(RELEASE)


@pytest.fixture
def unprofitable_site(tmp_path: Path):
    """A site whose OWN prices cannot repay a round trip, so the optimum is to do nothing.

    Mirrors the real private-wire signature (Tanhouse, Colnbrook): a genuine but tiny time-of-use
    spread, no DUoS band to avoid, and a large FLAT levy whose only dispatch effect is that the
    round-trip losses are bought at the full delivered price. Every DUoS band is a declared 0.0
    rather than ``None`` so the gate stays OPEN and no D45 null label fires: this test is about the
    priced-and-earned-nothing case, not the null case.
    """
    hh = tmp_path / "flat.csv"
    rows, _ = write_hh_csv(hh, days=7)
    site = multi_rate_site(hh, rows=rows)
    return site.model_copy(
        update={
            "tariff_stack": site.tariff_stack.model_copy(
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


@pytest.fixture
def profitable_site(tmp_path: Path):
    """The support module's default site: a 0.20 spread plus a 0.10 DUoS red band. Cycles hard."""
    hh = tmp_path / "spread.csv"
    rows, _ = write_hh_csv(hh, days=7)
    return multi_rate_site(hh, rows=rows)


def test_zero_dispatch_note_states_the_decision_and_its_arithmetic(unprofitable_site, assumptions):
    """A real solve that declines every TARIFF cycle must label the £0 a decision, with working.

    D62 limb 1 (2026-11-22) restates the premise. DFS is in every objective (D57, 205ed92), and this
    7-day fixture carries 8 DFS-called half-hours, so the optimum is no longer a null dispatch: the
    battery delivers into the called periods and NOWHERE else. Measured on gb_v1_9 at 100 kW / 2 h:
    discharge 7,826.294 kWh/yr, all of it inside DFS periods (0.0 outside, no charge inside them);
    charge 8,893.516 kWh/yr (= 7,826.294 / 0.88); dfs_delivered_mwh 7.826294. The note now keys on
    discharge OUTSIDE the DFS-called periods and states the DFS delivery separately.

    The TARIFF_ARBITRAGE cost is that DFS cycle's own energy-band spread, recomputed by hand from
    the fixture tariff: charge at night 0.095, discharge into the called day periods at 0.10, so
    8,893.516 x 0.095 - 7,826.294 x 0.10 = 844.884 - 782.629 = GBP 62.25/yr.
    """
    schedule = _solve(unprofitable_site, assumptions)
    # The premise: the SOLVER chose this, it is not asserted into existence. Nothing is discharged
    # outside the DFS-called periods, and everything that IS discharged is DFS delivery.
    assert schedule.annual_discharge_outside_dfs_kwh == 0.0, (
        "expected the optimum to decline every tariff cycle outside the DFS-called periods"
    )
    assert schedule.annual_discharge_kwh > 0.0
    assert schedule.dfs_delivered_mwh is not None
    assert schedule.annual_discharge_kwh == pytest.approx(schedule.dfs_delivered_mwh * 1000)

    value_streams, _gated, _used = build_value_streams(schedule, unprofitable_site, assumptions)
    arbitrage = _arbitrage(value_streams)

    # The stream is PRICED, not gated out: that is precisely what makes the zero ambiguous.
    assert arbitrage.gate_status == "open"
    assert arbitrage.annual_revenue_gbp == 0.0
    rates = unprofitable_site.tariff_stack.import_rates
    assert arbitrage.annual_cost_gbp == pytest.approx(
        schedule.annual_charge_kwh * rates["night"] - schedule.annual_discharge_kwh * rates["day"],
        abs=0.01,
    )

    note = next((w for w in arbitrage.warnings if w.startswith(ZERO_DISPATCH_LABEL)), None)
    assert note is not None, (
        "a priced TARIFF_ARBITRAGE stream earned £0 from an empty dispatch and said nothing about "
        "why; that £0 is indistinguishable from a null-input zero, which is the defect this note "
        "exists to remove"
    )
    # The decision, stated.
    assert "dispatched NOTHING" in note
    assert "declining a loss-making trade" in note
    # The arithmetic, so the verdict is CHECKABLE rather than merely asserted. cheapest 0.095 and
    # levy 0.13 at RTE 0.88 with c_deg 0.0213/kWh give a 0.05198 hurdle against a 0.00500 spread.
    assert "a cycle needs a spread of 0.05198 GBP/kWh" in note
    assert "offers is 0.00500 GBP/kWh" in note
    assert "levy leakage -0.015600" in note
    assert "cycle wear -0.018744" in note
    # D62 limb 1: the DFS delivery is stated separately, formatted from the schedule itself.
    assert "DFS DELIVERY IS SEPARATE" in note
    delivered = f"delivered {schedule.dfs_delivered_mwh:,.3f} MWh/yr inside the DFS-called periods"
    assert delivered in note
    # Determinism: the same inputs must produce the identical string, every time.
    again = _arbitrage(build_value_streams(schedule, unprofitable_site, assumptions)[0])
    assert [w for w in again.warnings if w.startswith(ZERO_DISPATCH_LABEL)] == [note]


def test_no_zero_dispatch_note_when_the_battery_actually_runs(profitable_site, assumptions):
    """The note must never fire on a site that dispatched: it explains an EMPTY dispatch only.

    Without this, deleting the ``annual_discharge_outside_dfs_kwh > 0`` guard (D62 limb 1) would
    put "the optimiser dispatched NOTHING" onto every hard-cycling site in the portfolio, which is a
    worse defect than the silence it replaced.
    """
    schedule = _solve(profitable_site, assumptions)
    assert schedule.annual_discharge_outside_dfs_kwh > 0.0, "expected this site to cycle"

    value_streams, _gated, _used = build_value_streams(schedule, profitable_site, assumptions)
    arbitrage = _arbitrage(value_streams)

    assert arbitrage.annual_revenue_gbp > 0.0
    assert not any(w.startswith(ZERO_DISPATCH_LABEL) for w in arbitrage.warnings), (
        "the zero-dispatch note fired on a site whose battery ran"
    )
