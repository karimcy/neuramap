"""The dispatch-energy seam end-to-end: the MILP's real volumes light up finance with no change.

Proves W4-1's "populate DispatchEnergy so finance levies + price-to-target are computable":
the real MILP fills ``annual_charge_kwh``/``annual_discharge_kwh``; ``to_finance_dispatch_energy``
maps them onto finance's hook; and finance then computes the levy line + the fixed-£/kWh
price-to-target — all through the *existing* finance machinery, unchanged.
"""

from __future__ import annotations

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, to_finance_dispatch_energy
from gridq.finance.cashflows import build_cashflow_model
from gridq.finance.debt import DebtTerms
from gridq.finance.financial_model import run_financial_model
from gridq.optimisation import MilpDispatchSolver, MilpEngine
from gridq.quotes.pipeline import generate_quote


def _site(tmp_path):
    path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _engine_result(tmp_path):
    """Run the engine; return (site, assumptions, OptimisationResult, DispatchEnergy)."""
    site, assumptions = _site(tmp_path)
    engine = MilpEngine()
    result = engine.solve(engine.build_optimisation_request(site, assumptions))
    return site, assumptions, result, engine.selected_dispatch_energy()


def test_milp_populates_both_annual_volumes(tmp_path):
    site, assumptions = _site(tmp_path)
    dispatch = MilpDispatchSolver().solve(
        DispatchProblem(
            site_model=site,
            assumptions=assumptions,
            power_kw=150.0,
            energy_kwh=300.0,
            duration_hours=2.0,
        )
    )
    assert dispatch.annual_charge_kwh is not None and dispatch.annual_charge_kwh > 0
    assert dispatch.annual_discharge_kwh is not None and dispatch.annual_discharge_kwh > 0
    assert dispatch.annual_charge_kwh > dispatch.annual_discharge_kwh  # round-trip losses
    # The converter copies both volumes 1:1 onto finance's hook.
    energy = to_finance_dispatch_energy(dispatch)
    assert energy.annual_charge_kwh == dispatch.annual_charge_kwh
    assert energy.annual_discharge_kwh == dispatch.annual_discharge_kwh


def test_levies_on_charge_activate_with_real_volumes(tmp_path):
    site, assumptions, result, dispatch_energy = _engine_result(tmp_path)
    assert dispatch_energy is not None and dispatch_energy.annual_charge_kwh

    # Without dispatch energy the levy line is unavailable (None) — the honest baseline.
    without = build_cashflow_model(site, result, assumptions)
    assert without.annual_cost_by_line_gbp["levies_on_charge"] is None

    # With the MILP's real charge volume it computes — finance unchanged.
    with_energy = build_cashflow_model(site, result, assumptions, dispatch_energy=dispatch_energy)
    levy = with_energy.annual_cost_by_line_gbp["levies_on_charge"]
    assert levy is not None and levy > 0  # losses exist because RTE < 1
    levy_rate = sum(site.tariff_stack.levies.values())
    # D36: levies fall on the NET import increase (charge − discharge, the round-trip losses),
    # matching the dispatch objective's and the customer bill's net-import convention.
    net = dispatch_energy.annual_charge_kwh - (dispatch_energy.annual_discharge_kwh or 0.0)
    assert levy == round(levy_rate * max(0.0, net), 2)


def test_price_to_target_computes_with_delivered_energy(tmp_path):
    site, assumptions, result, dispatch_energy = _engine_result(tmp_path)
    model = run_financial_model(
        site,
        result,
        assumptions,
        dispatch_energy=dispatch_energy,
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
    )
    # The fixed-£/kWh price-to-target needs the delivered (discharge) volume; it is now available.
    assert model.case.price_recommendation.value_gbp_per_kwh is not None


def test_quote_pipeline_threads_dispatch_energy_from_the_engine(tmp_path):
    site, assumptions = _site(tmp_path)
    engine = MilpEngine()
    run = generate_quote(site, assumptions, engine=engine)
    # The engine exposes the selected candidate's volumes, which the pipeline threads into finance
    # (a BaselineEngine has no such hook, so the levy/price lines would stay unavailable there).
    assert engine.selected_dispatch_energy() is not None
    assert run.financial_case is not None  # a positive sizing was priced
    assert run.bankable is False  # still not bankable on placeholder assumptions
