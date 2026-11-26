"""Tests for the customer bill chain (W7-5, C017): schedule → solver → engine → QuoteRecord.

Invariants: every figure is an identity over the series the MILP optimised (asserted against
manual sums — no direction is assumed, a negative saving is representable); a tariff with no
time-mappable energy rate produces NO bill (the honest pending seam, never a partial figure);
the persisted field is hash-excluded (quote_id unchanged — the D23/D25 additive property);
engines without the hook (BaselineEngine) leave it None.
"""

from __future__ import annotations

import numpy as np
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.engine import BaselineEngine
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.quotes import generate_quote
from gridq.quotes.assembly import assemble_quote_record
from gridq.schemas import CustomerBill

POWER_KW = 200.0
ENERGY_KWH = 400.0
LEVY_RATE = 0.03  # the fixture tariff's levy lines: ro 0.02 + fit 0.01 £/kWh


@pytest.fixture(scope="module")
def solved(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("hh") / "load.csv"
    rows, _ = write_hh_csv(path)
    site = multi_rate_site(path, rows=rows)
    lib = load_assumptions("gb_v0_1")
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=POWER_KW,
            energy_kwh=ENERGY_KWH,
            duration_hours=ENERGY_KWH / POWER_KW,
        )
    )
    assert result.solver_status.value == "optimal"
    return site, lib, solver


def test_bill_components_are_identities_over_the_solved_series(solved) -> None:
    _, _, solver = solved
    schedule = solver.last_schedule()
    bill = solver.last_customer_bill()
    assert bill is not None
    factor = schedule.interval_hours * schedule.annual_factor
    net = np.clip(schedule.site_load_kw + schedule.charge_kw - schedule.discharge_kw, 0.0, None)

    assert bill.energy_before_gbp == pytest.approx(
        float((schedule.site_load_kw * schedule.energy_price_gbp_per_kwh).sum()) * factor, abs=0.02
    )
    assert bill.duos_after_gbp == pytest.approx(
        float((net * schedule.duos_rate_gbp_per_kwh).sum()) * factor, abs=0.02
    )
    assert bill.levies_before_gbp == pytest.approx(
        float(schedule.site_load_kw.sum()) * factor * LEVY_RATE, abs=0.02
    )
    assert bill.import_after_kwh == pytest.approx(float(net.sum()) * factor, abs=0.01)
    assert bill.levy_rate_gbp_per_kwh == pytest.approx(LEVY_RATE)
    # The battery imports MORE kWh than the bare site (round-trip losses are real energy)…
    assert bill.import_after_kwh > bill.import_before_kwh
    # …and the saving is exactly before − after (validator-enforced; sign never assumed).
    assert bill.annual_saving_gbp == pytest.approx(
        bill.annual_bill_before_gbp - bill.annual_bill_after_gbp, abs=0.02
    )
    # The honesty label travels with the number.
    assert "NOT modelled" in bill.basis


def test_no_time_mappable_energy_rate_means_no_bill(solved) -> None:
    _, _, solver = solved
    schedule = solver.last_schedule()
    import dataclasses

    stripped = dataclasses.replace(schedule, energy_price_gbp_per_kwh=None)
    assert stripped.customer_bill(levy_rate_gbp_per_kwh=LEVY_RATE) is None


def test_engine_hook_and_pipeline_persist_the_bill(solved) -> None:
    site, lib, _ = solved
    run = generate_quote(site, lib, engine=MilpEngine())
    assert run.customer_bill is not None
    assert run.quote.customer_bill == run.customer_bill
    assert run.quote.customer_bill.annual_bill_before_gbp > 0
    # BaselineEngine has no hook → honest None, zero downstream change.
    baseline = generate_quote(site, lib, engine=BaselineEngine())
    assert baseline.customer_bill is None
    assert baseline.quote.customer_bill is None


def test_customer_bill_is_hash_excluded(solved) -> None:
    # The additive property (D23/D25 pattern): the bill never feeds quote identity.
    site, lib, _ = solved
    run = generate_quote(site, lib, engine=MilpEngine())
    without = assemble_quote_record(site, run.result, run.financial_case, lib, customer_bill=None)
    with_bill = assemble_quote_record(
        site, run.result, run.financial_case, lib, customer_bill=run.customer_bill
    )
    assert with_bill.quote_id == without.quote_id
    assert with_bill.input_hash == without.input_hash


def test_bill_validator_rejects_inconsistent_totals() -> None:
    kwargs = dict(
        annual_bill_before_gbp=100.0,
        annual_bill_after_gbp=80.0,
        annual_saving_gbp=20.0,
        energy_before_gbp=60.0,
        energy_after_gbp=50.0,
        duos_before_gbp=30.0,
        duos_after_gbp=20.0,
        levies_before_gbp=10.0,
        levies_after_gbp=10.0,
        import_before_kwh=1000.0,
        import_after_kwh=1100.0,
        levy_rate_gbp_per_kwh=0.01,
        basis="test",
    )
    CustomerBill(**kwargs)  # consistent → valid
    with pytest.raises(ValueError, match="components must sum"):
        CustomerBill(**{**kwargs, "energy_before_gbp": 99.0})
    with pytest.raises(ValueError, match="before − after"):
        CustomerBill(**{**kwargs, "annual_saving_gbp": 5.0})


def test_peak_import_physics_are_measured_not_priced(solved) -> None:
    # W9/C029: peaks are informational identities off the solved series — the dispatch is
    # bill-optimised, not peak-optimised, so the after-peak may legitimately exceed the before.
    _, _, solver = solved
    schedule = solver.last_schedule()
    bill = solver.last_customer_bill()
    assert bill is not None
    net = np.clip(schedule.site_load_kw + schedule.charge_kw - schedule.discharge_kw, 0.0, None)
    assert bill.peak_import_before_kw == pytest.approx(float(schedule.site_load_kw.max()), abs=0.01)
    assert bill.peak_import_after_kw == pytest.approx(float(net.max()), abs=0.01)
