"""Tests for the cashflow model + financial-model orchestrator + pricing.

These assert the deterministic finance behaviour and the hard rules: the financial case
validates, P50/P90 are present, placeholder assumptions raise warnings, major assumptions are
not hidden constants, output is deterministic, and negative capex is rejected.
"""

from __future__ import annotations

from datetime import date

import pytest

import gridq.finance as fin
import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import (
    SECTION_NAMES,
    AssumptionLibrary,
    AssumptionStatus,
    load_assumptions,
)
from gridq.finance import DebtTerms, DispatchEnergy, InsufficientInputsError
from gridq.finance.cashflows import (
    build_cashflow_model,
    dispatch_cost_total,
    estimate_capex,
    revenue_by_stream,
)

# --- helpers ---------------------------------------------------------------


def _lib() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def _site(*, levies: dict[str, float] | None = None) -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    return s.SiteModel(
        site_id="site_fin",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies=levies or {},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _result(
    *,
    power_kw: float | None = 500.0,
    revenue: float = 0.0,
    cost: float = 0.0,
    stream: s.MarketStream = s.MarketStream.TARIFF_ARBITRAGE,
    bankable_status: s.BankableStatus = s.BankableStatus.REVIEW_REQUIRED,
) -> s.OptimisationResult:
    """A synthetic result — clearly a TEST fixture, never real site data."""
    streams = []
    if power_kw is not None:
        streams = [
            s.ValueStreamResult(
                stream=stream,
                annual_revenue_gbp=revenue,
                annual_cost_gbp=cost,
                firmness=s.Firmness.FIRM,
            )
        ]
    return s.OptimisationResult(
        result_id="res_fin",
        request_id="req_fin",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=bankable_status,
        selected_power_kw=power_kw,
        selected_energy_kwh=(power_kw * 2 if power_kw else None),
        duration_hours=(2.0 if power_kw else None),
        value_streams=streams,
        solver_status=s.SolverStatus.MOCK,
    )


def _confirmed_lib() -> AssumptionLibrary:
    """A deep copy of gb_v0_1 with every assumption flipped to ``confirmed`` — TEST ONLY.

    Used to prove the bankability gate *releases* when nothing is a placeholder, so the gate is
    real logic, not a constant False. Never represents a real, confirmed library.
    """
    lib = _lib()
    for section in SECTION_NAMES:
        for a in getattr(lib, section).values():
            a.status = AssumptionStatus.CONFIRMED
    return lib


# --- the financial case validates + P50/P90 present ------------------------


def test_financial_case_validates_and_has_p50_p90() -> None:
    case = fin.build_financial_case(
        _site(), _result(revenue=100000.0, cost=20000.0), _lib(), horizon_years=15
    )
    assert isinstance(case, s.FinancialCase)
    # P50 and P90 both present, same length = horizon + year 0.
    assert len(case.p50_cashflows) == 16
    assert len(case.p90_cashflows) == len(case.p50_cashflows)
    # year 0 is the negative capex; operating years are present.
    assert case.p50_cashflows[0] < 0


def test_p90_is_a_haircut_below_p50_when_revenue_present() -> None:
    case = fin.build_financial_case(_site(), _result(revenue=100000.0), _lib(), horizon_years=10)
    # year 0 (capex) identical; operating years are lower under P90 (revenue haircut).
    assert case.p90_cashflows[0] == case.p50_cashflows[0]
    assert case.p90_cashflows[1] < case.p50_cashflows[1]


def test_p50_p90_equal_when_no_revenue() -> None:
    # Zero revenue ⇒ a 0 haircut on 0 ⇒ the two series coincide (cost-only).
    case = fin.build_financial_case(_site(), _result(revenue=0.0), _lib(), horizon_years=5)
    assert case.p50_cashflows == case.p90_cashflows


# --- placeholder assumptions produce warnings ------------------------------


def test_placeholder_assumptions_produce_warnings() -> None:
    case = fin.build_financial_case(_site(), _result(revenue=50000.0), _lib(), horizon_years=10)
    assert any("placeholder" in w.lower() for w in case.warnings)
    # the capex assumption (a placeholder) is named in a warning
    assert any("battery_capex" in w for w in case.warnings)


def test_not_bankable_on_placeholder_even_if_result_bankable_and_covenant_passes() -> None:
    # The hard case: a BANKABLE_CANDIDATE result AND injected debt terms that pass the covenant.
    # The case must STILL be not-bankable because every library assumption used is a placeholder.
    res = fin.run_financial_model(
        _site(levies={"RO": 0.025}),
        _result(
            revenue=150000.0, cost=20000.0, bankable_status=s.BankableStatus.BANKABLE_CANDIDATE
        ),
        _lib(),
        horizon_years=15,
        debt_terms=DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8),
        dispatch_energy=DispatchEnergy(annual_charge_kwh=400000.0),
    )
    assert res.case.covenant_status.passes is True
    assert res.bankable is False
    assert any("not bankable" in w.lower() for w in res.case.warnings)


def test_bankable_only_when_confirmed_result_bankable_and_covenant_passes() -> None:
    # Proves the gate releases: a fully-confirmed library + a bankable-candidate result + injected
    # debt terms that pass the covenant ⇒ bankable. (Confirms the gate is real logic, not constant.)
    res = fin.run_financial_model(
        _site(levies={"RO": 0.025}),
        _result(
            revenue=150000.0, cost=20000.0, bankable_status=s.BankableStatus.BANKABLE_CANDIDATE
        ),
        _confirmed_lib(),
        horizon_years=15,
        debt_terms=DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8),
        dispatch_energy=DispatchEnergy(annual_charge_kwh=400000.0),
    )
    assert res.cashflows.uses_unconfirmed_assumptions is False
    assert res.case.covenant_status.passes is True
    assert res.bankable is True


def test_equity_irr_present_with_debt_absent_without() -> None:
    # With injected debt the levered (equity) IRR is computed (derived from FCF - debt service)...
    with_debt = fin.run_financial_model(
        _site(levies={"RO": 0.025}),
        _result(
            revenue=150000.0, cost=20000.0, bankable_status=s.BankableStatus.BANKABLE_CANDIDATE
        ),
        _lib(),
        horizon_years=15,
        debt_terms=DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8),
        dispatch_energy=DispatchEnergy(annual_charge_kwh=400000.0),
    )
    assert with_debt.case.equity_irr is not None
    # ...and without any debt structure there is no levered split, so it stays unavailable (None).
    without_debt = fin.run_financial_model(
        _site(), _result(revenue=150000.0), _lib(), horizon_years=15
    )
    assert without_debt.case.equity_irr is None


# --- no hidden constants for major assumptions -----------------------------


def test_capex_is_not_a_hidden_constant() -> None:
    base = fin.run_financial_model(_site(), _result(), _lib(), horizon_years=10).cashflows
    lib2 = _lib()
    lib2.battery_capex["installed_cost"].value = (
        float(lib2.battery_capex["installed_cost"].value) * 2
    )
    doubled = fin.run_financial_model(_site(), _result(), lib2, horizon_years=10).cashflows
    assert doubled.capex.total_gbp == pytest.approx(base.capex.total_gbp * 2)


def test_opex_is_not_a_hidden_constant() -> None:
    base = fin.run_financial_model(_site(), _result(), _lib(), horizon_years=10).cashflows
    lib2 = _lib()
    lib2.battery_opex["annual_fixed_opex"].value = (
        float(lib2.battery_opex["annual_fixed_opex"].value) * 3
    )
    tripled = fin.run_financial_model(_site(), _result(), lib2, horizon_years=10).cashflows
    assert tripled.annual_cost_by_line_gbp["opex"] == pytest.approx(
        base.annual_cost_by_line_gbp["opex"] * 3
    )


def test_p90_haircut_is_not_a_hidden_constant() -> None:
    base = fin.build_financial_case(_site(), _result(revenue=100000.0), _lib(), horizon_years=10)
    lib2 = _lib()
    lib2.p50_p90_haircuts["p90_revenue_haircut_pct"].value = 50.0
    bigger = fin.build_financial_case(_site(), _result(revenue=100000.0), lib2, horizon_years=10)
    # a bigger haircut ⇒ a lower P90 operating cashflow
    assert bigger.p90_cashflows[1] < base.p90_cashflows[1]


# --- deterministic output --------------------------------------------------


def test_deterministic_output_on_same_inputs() -> None:
    site, result, lib = _site(levies={"RO": 0.025}), _result(revenue=80000.0, cost=10000.0), _lib()
    a = fin.build_financial_case(site, result, lib, horizon_years=15).model_dump()
    b = fin.build_financial_case(site, result, lib, horizon_years=15).model_dump()
    assert a == b
    # the deterministic case_id is stable too
    assert a["case_id"] == b["case_id"]


# --- negative capex rejected -----------------------------------------------


def test_negative_capex_is_rejected() -> None:
    lib2 = _lib()
    lib2.battery_capex["installed_cost"].value = -1.0
    with pytest.raises(ValueError, match="capex cannot be negative"):
        fin.build_financial_case(_site(), _result(), lib2, horizon_years=10)


def test_estimate_capex_rejects_negative_power() -> None:
    with pytest.raises(InsufficientInputsError):
        estimate_capex(-0.5, _lib(), warnings=[], used=[])


# --- insufficient inputs ---------------------------------------------------


def test_no_sizing_raises_insufficient_inputs() -> None:
    with pytest.raises(InsufficientInputsError, match="selected_power_kw"):
        fin.build_financial_case(_site(), _result(power_kw=None), _lib(), horizon_years=10)


def test_horizon_default_emits_warning() -> None:
    model = build_cashflow_model(_site(), _result(), _lib())  # no horizon_years
    assert model.horizon_years == fin.DEFAULT_HORIZON_YEARS
    assert any("horizon defaulted" in w.lower() for w in model.warnings)


# --- capex + revenue + cost lines ------------------------------------------


def test_capex_estimate_scales_with_power() -> None:
    cap = estimate_capex(0.5, _lib(), warnings=[], used=[])
    assert cap.power_mw == 0.5
    assert cap.total_gbp == pytest.approx(cap.per_mw_gbp * 0.5)


def test_revenue_by_stream_sums_priced_streams_only() -> None:
    result = _result(revenue=42000.0)
    rev = revenue_by_stream(result, warnings=[])
    assert rev[s.MarketStream.TARIFF_ARBITRAGE.value] == 42000.0


def test_excluded_streams_contribute_no_revenue_or_cost() -> None:
    # A priced-out (excluded) stream with a non-zero figure must NOT be counted.
    result = s.OptimisationResult(
        result_id="res_x",
        request_id="req_x",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=500.0,
        selected_energy_kwh=1000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=50000.0,
                annual_cost_gbp=8000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=99999.0,  # excluded ⇒ must be ignored
                annual_cost_gbp=11111.0,
                firmness=s.Firmness.EXCLUDED,
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )
    model = build_cashflow_model(_site(), result, _lib(), horizon_years=5)
    assert model.annual_revenue_by_stream_gbp == {s.MarketStream.TARIFF_ARBITRAGE.value: 50000.0}
    assert model.annual_dispatch_cost_gbp == 8000.0


def test_conditional_streams_contribute_no_revenue_or_cost() -> None:
    # Regression for the priced-floor semantics (OPEN_COMMENTS C014): the floor is
    # firmness ∈ {firm, contracted}. A *valued* CONDITIONAL stream — what M3's real engine will
    # emit, unlike the all-£0 baseline — must be held out of BOTH priced revenue and dispatch
    # cost. It is upside, not the priced case. CAPACITY_MARKET is used (non-gated, so any firmness
    # is valid) to prove the exclusion is driven by CONDITIONAL itself, not by gating.
    result = s.OptimisationResult(
        result_id="res_cond",
        request_id="req_cond",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=500.0,
        selected_energy_kwh=1000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=50000.0,
                annual_cost_gbp=8000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=99999.0,  # conditional ⇒ must be ignored
                annual_cost_gbp=11111.0,
                firmness=s.Firmness.CONDITIONAL,
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )
    # Revenue side: only the firm floor stream is summed.
    rev = revenue_by_stream(result, warnings=[])
    assert rev == {s.MarketStream.TARIFF_ARBITRAGE.value: 50000.0}
    # Cost side: the conditional stream's dispatch cost is held out of the floor too.
    assert dispatch_cost_total(result) == 8000.0
    # And the same holds end-to-end through the cashflow model.
    model = build_cashflow_model(_site(), result, _lib(), horizon_years=5)
    assert model.annual_revenue_by_stream_gbp == {s.MarketStream.TARIFF_ARBITRAGE.value: 50000.0}
    assert model.annual_dispatch_cost_gbp == 8000.0


def test_contracted_streams_count_toward_priced_floor() -> None:
    # The floor is {firm, contracted}, not firm-only: a CONTRACTED stream's revenue AND cost both
    # count. Guards the fix against over-narrowing the floor to FIRM alone (OPEN_COMMENTS C014).
    result = s.OptimisationResult(
        result_id="res_contr",
        request_id="req_contr",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=500.0,
        selected_energy_kwh=1000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=50000.0,
                annual_cost_gbp=8000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=30000.0,
                annual_cost_gbp=5000.0,
                firmness=s.Firmness.CONTRACTED,
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )
    assert revenue_by_stream(result, warnings=[]) == {
        s.MarketStream.TARIFF_ARBITRAGE.value: 50000.0,
        s.MarketStream.CAPACITY_MARKET.value: 30000.0,
    }
    assert dispatch_cost_total(result) == 13000.0


def test_cost_lines_all_sourced_from_assumptions() -> None:
    model = build_cashflow_model(_site(), _result(), _lib(), horizon_years=10)
    # every assumption-driven cost line is present (levies need dispatch volume — None here)
    for line in (
        "opex",
        "insurance",
        "metering_idno",
        "dno",
        "business_rates",
    ):
        assert model.annual_cost_by_line_gbp[line] is not None
    assert model.annual_cost_by_line_gbp["levies_on_charge"] is None


def test_levies_unavailable_without_dispatch_volume() -> None:
    model = build_cashflow_model(_site(levies={"RO": 0.025}), _result(), _lib(), horizon_years=10)
    assert model.annual_cost_by_line_gbp["levies_on_charge"] is None
    assert any("levies_on_charge" in w and "unavailable" in w for w in model.warnings)


def test_levies_computed_with_dispatch_volume() -> None:
    model = build_cashflow_model(
        _site(levies={"RO": 0.025, "FiT": 0.006}),
        _result(),
        _lib(),
        horizon_years=10,
        dispatch_energy=DispatchEnergy(annual_charge_kwh=100000.0),
    )
    # D36 net convention: discharge derives via RTE (100,000 x 0.88 = 88,000), and the levy
    # cost falls on the NET import increase - the round-trip losses:
    # (0.025 + 0.006) £/kWh x (100,000 - 88,000) kWh = £372.
    assert model.annual_cost_by_line_gbp["levies_on_charge"] == pytest.approx(372.0)


def test_scenario_price_multiplier_scales_revenue() -> None:
    class _Scn:
        scenario_id = "high"
        price_multiplier = 1.2
        cost_multiplier = 1.0

    base = build_cashflow_model(_site(), _result(revenue=100000.0), _lib(), horizon_years=5)
    high = build_cashflow_model(
        _site(), _result(revenue=100000.0), _lib(), horizon_years=5, scenario=_Scn()
    )
    assert high.p50_ebitda_gbp[0] > base.p50_ebitda_gbp[0]


def test_scenario_cost_multiplier_scales_capex() -> None:
    class _Scn:
        scenario_id = "high_cost"
        price_multiplier = 1.0
        cost_multiplier = 1.5

    base = build_cashflow_model(_site(), _result(), _lib(), horizon_years=5)
    high = build_cashflow_model(_site(), _result(), _lib(), horizon_years=5, scenario=_Scn())
    assert high.capex.total_gbp == pytest.approx(base.capex.total_gbp * 1.5)


# --- pricing ---------------------------------------------------------------


def test_capital_recovery_factor_zero_rate() -> None:
    assert fin.capital_recovery_factor(0.0, 10) == pytest.approx(0.1)


def test_capital_recovery_factor_positive_rate() -> None:
    # 10% over 10 years ≈ 0.16275
    assert fin.capital_recovery_factor(10.0, 10) == pytest.approx(0.162745, abs=1e-5)


def test_fixed_price_per_kwh_math() -> None:
    assert fin.fixed_price_per_kwh(10000.0, 50000.0) == pytest.approx(0.2)


def test_pricing_unavailable_without_delivered_energy() -> None:
    rec, warnings = fin.recommend_price(
        s.PriceStructure.FIXED_GBP_PER_KWH,
        _lib(),
        capex_gbp=250000.0,
        annual_opex_gbp=10000.0,
        annual_delivered_energy_kwh=None,
        horizon_years=15,
    )
    assert rec.value_gbp_per_kwh is None
    assert rec.hits_target_return is None
    assert any("unavailable" in w.lower() for w in warnings)


def test_pricing_savings_share_is_labelled_placeholder() -> None:
    rec, warnings = fin.recommend_price(s.PriceStructure.SAVINGS_SHARE, _lib())
    assert rec.structure == s.PriceStructure.SAVINGS_SHARE
    assert rec.value_gbp_per_kwh is None
    assert any("placeholder" in w.lower() for w in warnings)


def test_pricing_capacity_fee_is_labelled_placeholder() -> None:
    rec, warnings = fin.recommend_price(s.PriceStructure.CAPACITY_FEE, _lib())
    assert rec.structure == s.PriceStructure.CAPACITY_FEE
    assert rec.value_gbp_per_kwh is None
    assert any("placeholder" in w.lower() for w in warnings)


def test_pricing_computes_value_but_not_bankable_on_placeholder() -> None:
    # With delivered energy supplied, a £/kWh is computed, but hits_target_return stays None
    # because the target-return assumption is a placeholder.
    rec, _ = fin.recommend_price(
        s.PriceStructure.FIXED_GBP_PER_KWH,
        _lib(),
        capex_gbp=250000.0,
        annual_opex_gbp=10000.0,
        annual_delivered_energy_kwh=350000.0,
        horizon_years=15,
    )
    assert rec.value_gbp_per_kwh is not None and rec.value_gbp_per_kwh > 0
    assert rec.hits_target_return is None


def _confirmed_target_lib() -> AssumptionLibrary:
    lib2 = _lib()
    lib2.financing["target_equity_irr_pct"].status = AssumptionStatus.CONFIRMED
    return lib2


def _price(lib: AssumptionLibrary, **kwargs):
    return fin.recommend_price(
        s.PriceStructure.FIXED_GBP_PER_KWH,
        lib,
        capex_gbp=250000.0,
        annual_opex_gbp=10000.0,
        annual_delivered_energy_kwh=350000.0,
        horizon_years=15,
        **kwargs,
    )


def test_n12_hits_target_is_measured_against_the_achieved_equity_irr() -> None:
    """N12 (WP-0 review 2026-08-10). REBASELINED (was `test_pricing_hits_target_only_when_
    confirmed`, which asserted True on a CONFIRMED target with no equity IRR in sight). The flag
    claimed "this price achieves the target EQUITY return" off an assumption's STATUS, while the
    construction behind the price is unlevered, pre-tax and assumes level volume. It is now a
    comparison against the equity IRR the same run computed."""
    lib = _confirmed_target_lib()
    target = float(lib.financing["target_equity_irr_pct"].value)

    hit, _ = _price(lib, achieved_equity_irr_pct=target + 2.0)
    assert hit.hits_target_return is True

    miss, warnings = _price(lib, achieved_equity_irr_pct=target - 2.0)
    assert miss.hits_target_return is False
    assert any("MISSES the target return" in w for w in warnings)


def test_n12_no_measured_equity_irr_means_unknown_never_true() -> None:
    """The live default: no debt structure resolves, so no equity IRR exists. The old code still
    asserted True the moment the target assumption was confirmed."""
    rec, warnings = _price(_confirmed_target_lib())
    assert rec.value_gbp_per_kwh is not None  # the price is still computed and reported
    assert rec.hits_target_return is None
    assert any("hits_target_return is UNKNOWN" in w for w in warnings)


def test_n12_a_measured_hit_on_a_placeholder_target_is_still_not_asserted() -> None:
    lib = _lib()  # target_equity_irr_pct is a placeholder in gb_v0_1
    target = float(lib.financing["target_equity_irr_pct"].value)
    rec, warnings = _price(lib, achieved_equity_irr_pct=target + 5.0)
    assert rec.hits_target_return is None
    assert any("placeholder" in w for w in warnings)
