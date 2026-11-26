"""Executable dispatch invariants from the manual/goal spec (docs/goal/spec/).

Covers the priority physics invariants for the MILP dispatch (M3):

- INV gap-segmentation: state of energy is never chained across missing-interval gaps —
  a battery cannot carry charge across a hole in the metered data as if no time passed
  (docs/goal/FINDINGS.md F-001; manual ch09 is silent, the goal spec requires segmenting).
- INV connection cap: charge + site load never exceeds min(MIC, ASC) in any half hour
  (manual ch09 "the single most important invariant").
- INV warranty cycles: annualised equivalent full cycles stay within the per-duration
  warranty cap (manual ch09 hard throughput constraint).
- INV value partition: the two bill-reduction streams (tariff arbitrage + DUoS red-band
  avoidance) together account for the schedule's entire net bill reduction, recomputed
  independently from the solved arrays (manual ch08/ch09 exact-partition claim).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.schemas import SolverStatus
from gridq.schemas.connection import ConnectionDetails


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


def _write_gapped_flat_band_csv(path) -> int:
    """A gappy HH series where every present block sits inside ONE tariff band.

    Three overnight blocks (00:00-06:30, all 'night') and three daytime blocks
    (10:00-15:30, all 'day'), separated by multi-hour gaps. Within any contiguous
    block the day/night price is flat, so a correctly gap-segmented dispatch has
    nothing to arbitrage and does nothing. Only by chaining SoE across the gaps
    (charging cheap in a night block, discharging dear in a later day block with
    no time passing) can the optimiser manufacture value.
    """
    blocks = [
        pd.date_range(f"{day} 00:00", periods=14, freq="30min")
        for day in ("2025-01-06", "2025-01-07", "2025-01-08")
    ] + [
        pd.date_range(f"{day} 10:00", periods=12, freq="30min")
        for day in ("2025-01-09", "2025-01-10", "2025-01-13")
    ]
    ts = pd.DatetimeIndex(np.concatenate([b.values for b in blocks]))
    load = np.full(len(ts), 100.0)
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    return len(ts)


def test_soe_never_chains_across_gaps(tmp_path, assumptions):
    """A gap in the metered series is a wall: no charge carried across it, ever."""
    path = tmp_path / "hh_gapped.csv"
    rows = _write_gapped_flat_band_csv(path)
    site = multi_rate_site(path, rows=rows)

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))

    assert result.solver_status == SolverStatus.OPTIMAL
    # Flat price inside every contiguous block => a gap-segmented dispatch cycles nothing.
    # Any material discharge here is value manufactured by teleporting energy across gaps.
    assert result.annual_discharge_kwh == pytest.approx(0.0, abs=1e-3)
    assert result.annual_charge_kwh == pytest.approx(0.0, abs=1e-3)


def test_charge_plus_load_never_exceeds_min_mic_asc(tmp_path, assumptions):
    """In every half hour: site load + battery charge <= min(MIC, ASC)."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, mic_kw=500.0)
    # ASC below MIC so the test proves the *min* of the two limits is the binding one.
    site = site.model_copy(
        update={"connection": ConnectionDetails(mic_kw=500.0, mec_kw=100.0, asc_kw=380.0)}
    )

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=300.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL

    schedule = solver.last_schedule()
    assert schedule is not None
    total_import = schedule.charge_kw + schedule.site_load_kw
    assert float(np.max(total_import)) <= 380.0 + 1e-6


def test_annualised_cycles_within_warranty_cap(tmp_path, assumptions):
    """Annualised equivalent full cycles never exceed the per-duration warranty cap."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=100.0, duration=2.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert result.equivalent_full_cycles is not None

    cap_table = assumptions.get("warranty_cycle_limits", "max_cycles_per_year_by_duration_h").value
    cap = float(cap_table["2"])
    assert result.equivalent_full_cycles <= cap + 1e-6


def test_bill_reduction_streams_partition_the_whole(tmp_path, assumptions):
    """Tariff arbitrage + DUoS red-band avoidance = the entire net bill reduction, exactly.

    Recomputed independently from the solved arrays: sum over the horizon of
    (discharge - charge) x (energy price + DUoS rate) x dt, annualised. The two streams
    must partition it (CM / local flex are availability revenue, not bill reduction).
    """
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL

    schedule = solver.last_schedule()
    assert schedule is not None
    assert schedule.energy_price_gbp_per_kwh is not None

    net_kw = schedule.discharge_kw - schedule.charge_kw
    price = schedule.energy_price_gbp_per_kwh + schedule.duos_rate_gbp_per_kwh
    total_bill_reduction = (
        float((net_kw * price).sum()) * schedule.interval_hours * schedule.annual_factor
    )

    by = {v.stream.value: v for v in result.value_streams}
    streamed = (
        by["TARIFF_ARBITRAGE"].annual_revenue_gbp + by["DUOS_RED_BAND_AVOIDANCE"].annual_revenue_gbp
    )
    assert streamed == pytest.approx(total_bill_reduction, abs=0.05)
    assert streamed > 0  # the fixture has a real spread; the partition is not vacuously zero


def test_parasitic_draw_reserves_connection_headroom_when_keyed(tmp_path):
    """gb_v1_2's parasitic key shrinks the charge headroom by fraction x rated power (D33)."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, mic_kw=500.0)
    site = site.model_copy(
        update={"connection": ConnectionDetails(mic_kw=500.0, mec_kw=100.0, asc_kw=380.0)}
    )
    lib = load_assumptions("gb_v1_2")
    fraction = float(lib.get("battery_opex", "parasitic_load_fraction_of_power").value)
    power = 300.0

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=power, assumptions=lib))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert any("parasitic auxiliary draw" in w for w in result.warnings)

    schedule = solver.last_schedule()
    assert schedule is not None
    total_import = schedule.charge_kw + schedule.site_load_kw
    # Headroom is reserved for the auxiliary draw: load + charge never enters the reserved band.
    assert float(np.max(total_import)) <= 380.0 - fraction * power + 1e-6

    # Releases without the key are unchanged: no parasitic warning, full headroom usable.
    baseline = MilpDispatchSolver()
    unkeyed = baseline.solve(_problem(site, power=power, assumptions=load_assumptions("gb_v0_1")))
    assert not any("parasitic" in w for w in unkeyed.warnings)


def test_duration_key_lookups_are_conservative_in_both_directions() -> None:
    """D35 (resolves F-002/C030): between tabulated nodes, the warranty cycle cap takes the
    CEILING node (a decreasing table - the cap never exceeds either bracketing contract
    point) while CM de-rating keeps the FLOOR node (an increasing table - revenue is
    under-stated, not flattered). On the nodes both lookups return the node exactly."""
    from gridq.optimisation._assumptions import ceiling_value_for_duration, value_for_duration

    warranty = {"1": 548.0, "2": 365.0, "4": 250.0}  # decreasing
    derate = {"1": 0.40, "2": 0.65, "4": 0.95}  # increasing

    # Warranty: ceiling — a 3h system gets the 4h point's 250, never the 2h point's 365.
    assert ceiling_value_for_duration(warranty, 3.0)[0] == 250.0
    assert ceiling_value_for_duration(warranty, 2.0) == (365.0, None)  # node exact, no warning
    assert ceiling_value_for_duration(warranty, 0.5)[0] == 548.0  # below range: smallest node
    above, above_warn = ceiling_value_for_duration(warranty, 8.0)
    assert above == 250.0 and above_warn is not None and "above the largest" in above_warn
    for bad in (None, {}, {"one": "x"}):
        value, warn = ceiling_value_for_duration(bad, 2.0)
        assert value is None and warn is not None

    # CM de-rating: floor — a 3h system clears in the 2h class (0.65), never the 4h class.
    assert value_for_duration(derate, 3.0)[0] == 0.65
    assert value_for_duration(derate, 2.0) == (0.65, None)


def test_off_node_duration_gets_the_ceiling_warranty_cap_in_dispatch(tmp_path, assumptions):
    """The MILP's throughput cap at an off-node duration binds at the CEILING node's cap:
    annualised cycles at 3h never exceed the 4h contract point's cap (D35)."""
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)

    solver = MilpDispatchSolver()
    result = solver.solve(_problem(site, power=100.0, duration=3.0, assumptions=assumptions))
    assert result.solver_status == SolverStatus.OPTIMAL
    assert result.equivalent_full_cycles is not None

    cap_table = assumptions.get("warranty_cycle_limits", "max_cycles_per_year_by_duration_h").value
    ceiling_cap = float(cap_table["4"])  # the conservative bracketing point for 3h
    assert result.equivalent_full_cycles <= ceiling_cap + 1e-6
    assert any("ceiling lookup" in w for w in result.warnings)
