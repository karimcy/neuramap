"""The dispatch-energy producer seam (OPEN_COMMENTS C010 / decision D13).

Proves two things:
1. On the placeholder (charge/discharge unavailable), finance's ``levies_on_charge`` and the
   fixed-£/kWh price-to-target stay honestly *unavailable* — never zero, never invented.
2. The moment a (here: synthetic) dispatch result carries real charge/discharge volumes, the same
   converter + finance entry point compute the levy line and the price-to-target with **no
   downstream change** — i.e. the seam is wired, just waiting on the real MILP.
"""

from __future__ import annotations

from datetime import date

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult, SimpleSolver, to_finance_dispatch_energy
from gridq.finance import run_financial_model


def _site_with_levies() -> s.SiteModel:
    """A T2 site whose tariff carries levy lines (so levies-on-charge can be priced)."""
    return s.SiteModel(
        site_id="site_levies",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies={"ro": 0.015, "cfd": 0.01},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _baseline_optimisation_result(power_kw: float = 100.0) -> s.OptimisationResult:
    """A minimal not-bankable OptimisationResult with positive sizing (so finance can build it)."""
    return s.OptimisationResult(
        result_id="res_seam",
        request_id="req_seam",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=power_kw,
        selected_energy_kwh=power_kw * 2,
        duration_hours=2.0,
        solver_status=s.SolverStatus.BASELINE,
    )


def test_placeholder_leaves_levies_and_price_unavailable() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _site_with_levies()
    streams = list(s.MarketStream)
    problem = DispatchProblem(
        site_model=site,
        assumptions=lib,
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        requested_streams=streams,
    )
    dispatch_result = SimpleSolver().solve(problem)
    fin = run_financial_model(
        site,
        _baseline_optimisation_result(),
        lib,
        dispatch_energy=to_finance_dispatch_energy(dispatch_result),
    )
    # levies-on-charge unavailable (no charge volume from the placeholder), not zero, not invented
    assert fin.cashflows.annual_cost_by_line_gbp["levies_on_charge"] is None
    assert any("levies_on_charge" in w and "unavailable" in w for w in fin.cashflows.warnings)
    # price-to-target has no delivered-energy volume → no value
    assert fin.case.price_recommendation.value_gbp_per_kwh is None


def test_real_volumes_make_levies_and_price_computable() -> None:
    """A synthetic (test-only) dispatch result with volumes activates both finance lines."""
    lib = load_assumptions("gb_v0_1")
    site = _site_with_levies()
    # NOTE: these volumes are a synthetic test fixture, NOT produced by SimpleSolver — they stand
    # in for what the real MILP will supply. The placeholder never invents them.
    synthetic = DispatchResult(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        solver_status=s.SolverStatus.BASELINE,
        annual_charge_kwh=200_000.0,
        annual_discharge_kwh=176_000.0,
    )
    dispatch_energy = to_finance_dispatch_energy(synthetic)
    assert dispatch_energy.annual_charge_kwh == 200_000.0
    assert dispatch_energy.annual_discharge_kwh == 176_000.0

    fin = run_financial_model(
        site,
        _baseline_optimisation_result(),
        lib,
        dispatch_energy=dispatch_energy,
    )
    # levies now compute on the NET import increase (D36): rate (0.015 + 0.01 = 0.025 £/kWh)
    # × (200,000 − 176,000) kWh of round-trip losses = £600 — never on gross charge.
    levies = fin.cashflows.annual_cost_by_line_gbp["levies_on_charge"]
    assert levies is not None
    assert levies == round(0.025 * (200_000.0 - 176_000.0), 2)
    # price-to-target now computes a value (still not bankable on placeholder assumptions)
    assert fin.case.price_recommendation.value_gbp_per_kwh is not None
    assert fin.case.price_recommendation.value_gbp_per_kwh > 0
    # the run is still NOT bankable (placeholder assumptions) — the seam computes, it does not bless
    assert fin.bankable is False


def test_discharge_only_volume_grosses_up_for_levies() -> None:
    """If only discharge is known, finance grosses it up by round-trip efficiency for the levy."""
    lib = load_assumptions("gb_v0_1")
    site = _site_with_levies()
    synthetic = DispatchResult(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        solver_status=s.SolverStatus.BASELINE,
        annual_discharge_kwh=176_000.0,  # no explicit charge volume
    )
    fin = run_financial_model(
        site,
        _baseline_optimisation_result(),
        lib,
        dispatch_energy=to_finance_dispatch_energy(synthetic),
    )
    levies = fin.cashflows.annual_cost_by_line_gbp["levies_on_charge"]
    assert levies is not None and levies > 0
    assert any("round-trip efficiency" in w for w in fin.cashflows.warnings)
