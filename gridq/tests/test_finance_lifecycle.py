"""WP-UW-17: finance completeness — levelisation, indexation, construction and lifecycle flows.

Every test drives `run_financial_model`, the entry point `quotes/pipeline.py` uses, so nothing
here passes on a helper that production never calls. Every site, result, term and lifecycle input
is SYNTHETIC (``source: synthetic``) and stands for nothing; no customer record is read.

1. The price-to-target is levelised over the modelled (faded, availability-scaled) volume in
   each year, and ``hits_target_return`` is measured in a closed loop (N12; D128).
2. ``financing.cpi_pct_per_year`` has a reader: absent = the legacy flat basis, null = a warned
   gap, numeric = the selectively escalated nominal ledger, hand-recomputed below; every line
   states its basis; a tenored CM payment is indexed per SI 2014/2043 Sch 1 para 3 (N13, H-15,
   D-33; D129).
3. A construction phase precedes operation, with IDC, and a COD delay is applied (H-16;
   D130).
4. Terminal value, decommissioning and replacement are each modelled or excluded by a labelled
   warning, and the ledger is always populated (N14; D131).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.assumptions.models import AssumptionStatus
from gridq.finance import DebtTerms, DispatchEnergy, run_financial_model
from gridq.finance import financial_model as fm
from gridq.finance.debt import SCULPTING_P90_DSCR, size_and_measure_dscr
from gridq.finance.lifecycle import (
    CM_RULE_SOURCE,
    INDEXED_COST_LINE_KEYS,
    LifecycleInputsError,
    accumulation_factor,
    escalation_factors,
    parse_lifecycle_inputs_text,
)
from gridq.finance.returns import project_irr, xnpv
from gridq.scenarios.models import Scenario, ScenarioName
from gridq.schemas.lifecycle import (
    CapacityAgreementIndexation,
    CashflowLineBasis,
    ConstructionPhase,
    ConstructionStatus,
    Decommissioning,
    DrawdownPoint,
    LifecycleFlow,
    LifecycleFlowStatus,
    LifecycleInputs,
    LifecycleProvenance,
    Replacement,
    ReturnBasis,
    TerminalValue,
)

_HORIZON = 15
_CM = s.MarketStream.CAPACITY_MARKET.value
_DISPATCH = DispatchEnergy(annual_charge_kwh=600_000.0, annual_discharge_kwh=516_000.0)
_TERMS = DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)
_PROV = LifecycleProvenance(source="synthetic", reference="SYNTHETIC WP-UW-17 test inputs")


# --- fixtures ----------------------------------------------------------------------------------


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_uw17",
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
            import_rates={"day": 0.2431, "night": 0.1187},
            levies={"cclevy": 0.00775},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Synthetic Ltd", company_number="01234567"),
    )


def _result(arbitrage: float = 220_000.0, cm: float | None = 30_000.0) -> s.OptimisationResult:
    streams = [
        s.ValueStreamResult(
            stream=s.MarketStream.TARIFF_ARBITRAGE,
            annual_revenue_gbp=arbitrage,
            annual_cost_gbp=12_000.0,
            firmness=s.Firmness.FIRM,
        )
    ]
    if cm is not None:
        streams.append(
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=cm,
                annual_cost_gbp=0.0,
                firmness=s.Firmness.CONTRACTED,
            )
        )
    return s.OptimisationResult(
        result_id="res_uw17",
        request_id="req_uw17",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=1_000.0,
        selected_energy_kwh=2_000.0,
        duration_hours=2.0,
        value_streams=streams,
        solver_status=s.SolverStatus.BASELINE,
    )


def _run(release: str | AssumptionLibrary = "gb_v1_6", **kwargs: Any) -> Any:
    lib = load_assumptions(release) if isinstance(release, str) else release
    kwargs.setdefault("result", _result())
    kwargs.setdefault("debt_terms", _TERMS)
    result = kwargs.pop("result")
    return run_financial_model(
        _site(), result, lib, horizon_years=_HORIZON, dispatch_energy=_DISPATCH, **kwargs
    )


def _confirmed_target(release: str = "gb_v1_6") -> AssumptionLibrary:
    """The release with its 12% target marked CONFIRMED, so a measured hit can read True."""
    lib = load_assumptions(release).model_copy(deep=True)
    target = lib.financing["target_equity_irr_pct"]
    lib.financing["target_equity_irr_pct"] = target.model_copy(
        update={"status": AssumptionStatus.CONFIRMED}
    )
    return lib


def _without_cpi_value(release: str) -> AssumptionLibrary:
    """``release`` with `financing.cpi_pct_per_year` nulled: the un-escalated twin."""
    lib = load_assumptions(release).model_copy(deep=True)
    cpi = lib.financing["cpi_pct_per_year"]
    lib.financing["cpi_pct_per_year"] = cpi.model_copy(update={"value": None})
    return lib


def _lifecycle(**blocks: Any) -> LifecycleInputs:
    return LifecycleInputs(provenance=_PROV, **blocks)


def _warning(case: s.FinancialCase, needle: str) -> str:
    hits = [w for w in case.warnings if needle in w]
    assert hits, f"no warning contains {needle!r}"
    return hits[0]


_LOW = _result(arbitrage=40_000.0, cm=None)  # a stack that needs a customer price


# --- 1. levelised price and the closed loop (N12; D128) --------------------------------


def test_the_delivered_volume_fades_and_is_availability_scaled_each_year() -> None:
    cf = _run("gb_v1_12", result=_LOW).cashflows
    lib = load_assumptions("gb_v1_12")
    fade = float(lib.get("degradation", "annual_capacity_fade_pct").value) / 100.0
    avail = cf.availability_factor_pct / 100.0
    energy = cf.delivered_energy_kwh_by_year
    assert energy[0] == 0.0 and len(energy) == _HORIZON + 1
    for k in range(1, _HORIZON + 1):
        assert energy[k] == pytest.approx(516_000.0 * (1 - fade) ** (k - 1) * avail)
    assert energy[_HORIZON] < energy[1]


def test_the_price_is_levelised_over_the_modelled_volume_not_flat_year_one() -> None:
    """At the recommended price the project's P50 series earns exactly the target (by hand).

    A flat year-1 volume over-counts the faded later years and so under-prices; the levelised
    price is the higher, correct one, and the NPV identity closes at it.
    """
    out = _run(result=_LOW, debt_terms=None)
    price = out.case.price_recommendation
    fcf, energy = out.cashflows.p50_fcf_pre_debt_gbp, out.cashflows.delivered_energy_kwh_by_year
    assert price.levelisation_basis == "annual"
    p = price.value_gbp_per_kwh
    assert p is not None and p > 0
    with_price = [fcf[0], *(v + p * e for v, e in zip(fcf[1:], energy[1:], strict=True))]
    assert project_irr(with_price).irr_pct == pytest.approx(12.0, abs=0.01)
    flows = [(12 * t, v) for t, v in enumerate(fcf)]
    flat = [(12 * t, energy[1]) for t in range(1, _HORIZON + 1)]
    flat_price = -xnpv(0.12, flows) / xnpv(0.12, flat, anchor_month=0)
    assert p > flat_price + 1e-4, "levelising over flat year-1 volume would under-price"
    assert "levelised over the modelled delivered volume" in _warning(out.case, "Price-to-target")


def test_hits_target_return_is_true_only_when_the_measured_equity_irr_clears_the_target() -> None:
    out = _run(_confirmed_target(), result=_LOW)
    price = out.case.price_recommendation
    assert price.hits_target_return is True
    assert price.measured_irr_with_price_pct is not None
    assert price.measured_irr_with_price_pct >= 12.0 - 0.01
    # The equity-solved diagnostic: cheap debt means equity clears 12% at a LOWER price.
    assert price.equity_closed_loop_gbp_per_kwh is not None
    assert price.equity_closed_loop_gbp_per_kwh < price.value_gbp_per_kwh


def test_hits_target_return_is_false_when_the_measured_equity_irr_misses() -> None:
    """Debt dearer than the target: the levelised (project) price leaves equity short. Measured."""
    dear = DebtTerms(gearing_pct=70.0, annual_rate_pct=25.0, tenor_years=10)
    out = _run(_confirmed_target(), result=_LOW, debt_terms=dear)
    price = out.case.price_recommendation
    assert price.hits_target_return is False
    assert price.measured_irr_with_price_pct is not None
    assert price.measured_irr_with_price_pct < 12.0
    assert price.equity_closed_loop_gbp_per_kwh is not None
    assert price.equity_closed_loop_gbp_per_kwh > price.value_gbp_per_kwh
    assert "is below the 12.00% target" in _warning(out.case, "With the recommended price")


def test_hits_target_return_stays_unknown_on_a_placeholder_target() -> None:
    out = _run(result=_LOW)
    price = out.case.price_recommendation
    assert price.measured_irr_with_price_pct is not None
    assert price.hits_target_return is None
    assert "placeholder" in _warning(out.case, "With the recommended price")


def test_the_levelisation_is_wired_into_the_production_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut the wire and the case falls back to the scalar CRF path: no basis, no measurement."""
    monkeypatch.setattr(fm, "_levelisation", lambda *a, **k: None)
    price = _run(_confirmed_target(), result=_LOW).case.price_recommendation
    assert price.levelisation_basis is None and price.measured_irr_with_price_pct is None


def test_a_stack_that_clears_the_target_is_priced_at_zero_not_negative() -> None:
    out = _run()
    assert out.case.price_recommendation.value_gbp_per_kwh == 0.0
    assert "customer price is £0" in _warning(out.case, "The value stack alone clears")


# --- 2. indexation (N13, H-15, D-33; D129) ----------------------------------------------


def test_an_absent_cpi_key_keeps_every_line_flat_and_unescalated() -> None:
    out = _run("gb_v1_6")
    cf = out.cashflows
    assert cf.cpi_state == "absent" and cf.escalation_by_year is None
    assert cf.line_basis and {b for _, b in cf.line_basis} == {
        CashflowLineBasis.FLAT_NOMINAL_UNESCALATED.value
    }
    assert out.case.lifecycle.indexation.cpi_state == "absent"
    assert not any("cpi_pct_per_year is null" in w for w in out.case.warnings)


def test_a_null_cpi_key_is_warned_and_never_read_as_zero() -> None:
    out = _run("gb_v1_7")
    assert out.cashflows.cpi_state == "null" and out.cashflows.escalation_by_year is None
    assert "not read as 0%" in _warning(out.case, "financing.cpi_pct_per_year is null")


def test_a_numeric_cpi_escalates_exactly_the_indexed_cost_lines_by_hand() -> None:
    """Hand recomputation (D129): with CPI c, year t's EBITDA falls by B x (E_t - 1),
    E_t = (1+c)^(t-1), B the year-1 sum of the five indexed cost lines, and nothing else moves.
    No CM in this stack, so no index touches revenue."""
    no_cm = _result(cm=None)
    escalated = _run("gb_v1_12", result=no_cm).cashflows
    flat = _run(_without_cpi_value("gb_v1_12"), result=no_cm).cashflows
    cpi = float(load_assumptions("gb_v1_12").get("financing", "cpi_pct_per_year").value)
    assert escalated.cpi_state == "numeric" and escalated.cpi_pct_per_year == cpi
    base = sum(
        escalated.annual_cost_by_line_gbp[k] or 0.0
        for k in INDEXED_COST_LINE_KEYS
        if k in escalated.annual_cost_by_line_gbp
    )
    assert base > 0
    for t in range(1, _HORIZON + 1):
        expected = -base * ((1 + cpi / 100.0) ** (t - 1) - 1.0)
        delta = escalated.p50_ebitda_gbp[t - 1] - flat.p50_ebitda_gbp[t - 1]
        assert delta == pytest.approx(expected, abs=0.011), t
        delta90 = escalated.p90_ebitda_gbp[t - 1] - flat.p90_ebitda_gbp[t - 1]
        assert delta90 == pytest.approx(expected, abs=0.011), t
    assert escalated.p50_ebitda_gbp[0] == flat.p50_ebitda_gbp[0]  # year 1 is the price base
    assert escalated.escalation_by_year == escalation_factors(cpi, _HORIZON)


def test_every_line_states_whether_it_is_real_or_nominal() -> None:
    out = _run("gb_v1_12", contract_tenor_years={_CM: 15.0})
    basis = dict(out.cashflows.line_basis)
    for key in INDEXED_COST_LINE_KEYS:
        if f"cost:{key}" in basis:
            assert basis[f"cost:{key}"] == CashflowLineBasis.NOMINAL_CPI_ESCALATED.value
    assert basis["revenue:TARIFF_ARBITRAGE"] == CashflowLineBasis.NOMINAL_FLAT.value
    assert basis[f"revenue:{_CM}"] == CashflowLineBasis.NOMINAL_CM_CPI_INDEXED.value
    assert basis["capex"] == CashflowLineBasis.NOMINAL_FLAT.value
    record = out.case.lifecycle.indexation
    assert {(e.line, e.basis.value) for e in record.line_basis} == set(basis.items())
    assert "NOMINAL, SELECTIVELY CPI-ESCALATED" in record.cashflow_basis
    # The real view: nominal deflated by E_t, year 0 undeflated; a lower IRR than the nominal.
    esc = out.cashflows.escalation_by_year
    nominal = out.cashflows.p50_fcf_pre_debt_gbp
    real = record.real_p50_fcf_gbp
    assert real[0] == nominal[0]
    for t in range(1, _HORIZON + 1):
        assert real[t] == pytest.approx(nominal[t] / esc[t - 1], abs=0.01)
    assert record.real_project_irr_pct < project_irr(nominal).irr_pct


def _assert_cm_delta(a: Any, b: Any, cm_gbp: float, scale: list[float]) -> None:
    """``a - b`` in each year is ``cm_gbp x scale_t``, to the pence each EBITDA is rounded to."""
    for t, (x, y, k) in enumerate(zip(a.p50_ebitda_gbp, b.p50_ebitda_gbp, scale, strict=True)):
        assert x - y == pytest.approx(cm_gbp * k, abs=0.011), t + 1


def test_a_t4_cm_payment_is_indexed_u_times_e_t_per_the_published_rule() -> None:
    """SI 2014/2043 Sch 1 para 3(5): PE = CCP x CPI_x / CPI_base. Against the T-1 (un-indexed) twin
    the in-term CM line rises by CM x (U x E_t - 1) each year, so that delta over (U x E_t - 1)
    is the one flat in-term CM amount in every year."""
    tenor = {_CM: 15.0}
    t4 = CapacityAgreementIndexation(
        auction_type="T-4",
        delivery_year="2029-30",
        cpi_base=130.0,
        cpi_x_year1=140.0,
        reference="SYNTHETIC T-4 agreement",
    )
    t1 = CapacityAgreementIndexation(
        auction_type="T-1", delivery_year="2026-27", reference="SYNTHETIC T-1 agreement"
    )
    run4 = _run("gb_v1_12", contract_tenor_years=tenor, lifecycle=_lifecycle(capacity_agreement=t4))
    run1 = _run("gb_v1_12", contract_tenor_years=tenor, lifecycle=_lifecycle(capacity_agreement=t1))
    u = 140.0 / 130.0
    esc = run4.cashflows.escalation_by_year
    avail = run4.cashflows.availability_factor_pct / 100.0
    _assert_cm_delta(run4.cashflows, run1.cashflows, 30_000.0 * avail, [u * e - 1.0 for e in esc])
    rec4, rec1 = run4.case.lifecycle.indexation, run1.case.lifecycle.indexation
    assert rec4.cm_auction_type == "T-4" and rec4.cm_year1_uplift == pytest.approx(u)
    assert rec4.cm_base_period_label == "Winter 2024/2025"
    assert CM_RULE_SOURCE in _warning(run4.case, "CM T-4 payment indexed per")
    assert rec1.cm_auction_type == "T-1" and rec1.cm_year1_uplift == 1.0
    assert "SI 2025/917" in _warning(run1.case, "T-1 CM held un-indexed")
    assert dict(run1.cashflows.line_basis)[f"revenue:{_CM}"] == "nominal_flat"


def test_without_an_agreement_the_cm_is_assumed_t4_and_the_uplift_is_withheld_and_asked() -> None:
    tenor = {_CM: 15.0}
    t1 = CapacityAgreementIndexation(
        auction_type="T-1", delivery_year="2026-27", reference="SYNTHETIC T-1 agreement"
    )
    assumed = _run("gb_v1_12", contract_tenor_years=tenor)
    run1 = _run("gb_v1_12", contract_tenor_years=tenor, lifecycle=_lifecycle(capacity_agreement=t1))
    esc = assumed.cashflows.escalation_by_year
    avail = assumed.cashflows.availability_factor_pct / 100.0
    assert assumed.cashflows.p50_ebitda_gbp[0] == run1.cashflows.p50_ebitda_gbp[0]  # U = 1.0
    _assert_cm_delta(assumed.cashflows, run1.cashflows, 30_000.0 * avail, [e - 1.0 for e in esc])
    assert "ASSUMED T-4" in _warning(assumed.case, "CM agreement auction type")
    ask = _warning(assumed.case, "CM year-1 uplift CPI_x/CPI_base NOT applied")
    assert "Winter 2024/2025" in ask and "Data ask" in ask


def test_an_untenored_cm_is_not_indexed_and_says_why() -> None:
    out = _run("gb_v1_12")
    assert out.cashflows.cm_indexation is None
    assert "withheld until a tenor is supplied" in _warning(out.case, "CM revenue is UN-TENORED")


# --- 3. construction, IDC and the COD delay (H-16; D130) --------------------------------

_BUILD = ConstructionPhase(
    construction_months=12,
    drawdown=(
        DrawdownPoint(months_before_cod=12, capex_fraction=0.3),
        DrawdownPoint(months_before_cod=6, capex_fraction=0.4),
        DrawdownPoint(months_before_cod=0, capex_fraction=0.3),
    ),
    idc_rate_pct=8.0,
)


def test_a_construction_phase_precedes_operation_with_idc_by_hand() -> None:
    plain = _run()
    built = _run(lifecycle=_lifecycle(construction=_BUILD))
    i = 1.08 ** (1 / 12) - 1
    a = 0.3 * (1 + i) ** 12 + 0.4 * (1 + i) ** 6 + 0.3
    assert accumulation_factor([(12, 0.3), (6, 0.4), (0, 0.3)], 8.0, 0) == pytest.approx(a)
    rec = built.case.lifecycle.construction
    assert rec.status is ConstructionStatus.MODELLED
    assert rec.return_basis is ReturnBasis.DATED_MONTHLY
    assert rec.accumulation_factor == pytest.approx(a)
    capex = built.cashflows.capex.total_gbp
    principal = round(capex * 0.5 * a, 2)
    assert rec.principal_at_cod_gbp == principal
    assert rec.idc_gbp == pytest.approx(principal - principal / a, abs=0.01)
    assert rec.idc_rate_pct == 8.0 and "construction programme" in rec.idc_rate_source
    # The dated series starts at the first draw, 12 months before COD.
    flows = rec.dated_p50_project_flows
    assert flows[0].month == -12 and flows[0].gbp == pytest.approx(-0.3 * capex, abs=0.01)
    # Money spent earlier earns later: the dated IRR is below the COD-anchored one.
    assert built.case.irr < plain.case.irr
    assert rec.cod_anchored_irr_pct == plain.case.irr
    assert "12-month construction programme" in _warning(built.case, "RETURN BASIS — DATED")
    assert built.case.price_recommendation.levelisation_basis == "dated"


def test_without_a_stated_construction_rate_the_term_rate_stands_in_and_is_labelled() -> None:
    build = _BUILD.model_copy(update={"idc_rate_pct": None})
    out = _run(lifecycle=_lifecycle(construction=build))
    rec = out.case.lifecycle.construction
    assert rec.idc_rate_pct == 7.0
    assert rec.idc_rate_source.startswith("term rate assumed (caller-injected DebtTerms")
    assert "DATA REQUEST" in _warning(out.case, "IDC rate assumed equal to term rate")


def test_without_a_programme_construction_is_excluded_and_labelled() -> None:
    out = _run()
    rec = out.case.lifecycle.construction
    assert rec.status is ConstructionStatus.EXCLUDED_NO_INPUT
    assert rec.return_basis is ReturnBasis.ANNUAL_COD_ANCHORED
    assert rec.accumulation_factor is None and rec.dated_p50_project_flows is None
    assert "This flatters IRR" in _warning(out.case, "CONSTRUCTION PHASE EXCLUDED")


def test_a_cod_delay_moves_operation_and_consumes_the_contract_term() -> None:
    tenor = {_CM: 15.0}
    on_time = _run(contract_tenor_years=tenor)
    delayed = _run(
        contract_tenor_years=tenor,
        scenario=Scenario(
            scenario_id="cod_delay",
            name=ScenarioName.CUSTOM,
            probability_weight=0.0,
            cod_delay_months=12,
        ),
    )
    assert delayed.cashflows.cod_delay_months == 12
    rec = delayed.case.lifecycle.construction
    assert rec.return_basis is ReturnBasis.DATED_MONTHLY and rec.cod_delay_months == 12
    assert rec.dated_p50_project_flows[1].month == 24  # operating year 1 is booked 12 months late
    assert delayed.case.irr < on_time.case.irr
    assert "15 -> 14 yr" in _warning(delayed.case, "COD DELAY vs CONTRACT TERM")
    # The final year earns no in-term CM: the term ran out a year after the planned COD.
    assert delayed.cashflows.p50_ebitda_gbp[-1] < on_time.cashflows.p50_ebitda_gbp[-1]
    assert delayed.case.case_id != on_time.case.case_id


# --- 4. terminal value, decommissioning, replacement (N14; D131) -----------------------


def test_with_no_flow_input_the_ledger_shows_all_three_excluded_with_data_requests() -> None:
    out = _run()
    flows = out.case.lifecycle.flows
    assert [f.flow for f in flows] == [
        LifecycleFlow.TERMINAL_VALUE,
        LifecycleFlow.DECOMMISSIONING,
        LifecycleFlow.REPLACEMENT,
    ]
    for f in flows:
        assert f.status is LifecycleFlowStatus.EXCLUDED_NO_INPUT and f.data_request
    assert "terminal/residual value" in _warning(out.case, "OMITTED from the free-cashflow series")


def test_a_terminal_value_is_in_the_irr_and_never_in_cfads() -> None:
    plain = _run()
    tv = TerminalValue(
        basis="user_gbp_at_horizon", amount_gbp=100_000.0, price_basis="nominal_as_entered"
    )
    out = _run(lifecycle=_lifecycle(terminal_value=tv))
    assert out.cashflows.p50_fcf_pre_debt_gbp[-1] == pytest.approx(
        plain.cashflows.p50_fcf_pre_debt_gbp[-1] + 100_000.0, abs=0.01
    )
    assert out.cashflows.lifecycle_cfads_by_year == [0.0] * _HORIZON
    assert out.case.dscr_summary.by_year == plain.case.dscr_summary.by_year
    assert out.case.irr > plain.case.irr
    entry = next(f for f in out.case.lifecycle.flows if f.flow is LifecycleFlow.TERMINAL_VALUE)
    assert entry.status is LifecycleFlowStatus.MODELLED and entry.in_cfads is False
    assert "EXCLUDED from CFADS/DSCR" in _warning(out.case, "TERMINAL VALUE MODELLED")
    # The two unstated flows are still excluded, each by its own labelled warning.
    _warning(out.case, "DECOMMISSIONING EXCLUDED (N14)")
    _warning(out.case, "REPLACEMENT EXCLUDED (N14)")


def test_decommissioning_and_replacement_reduce_cfads_in_their_year() -> None:
    plain = _run()
    life = _lifecycle(
        decommissioning=Decommissioning(
            cost_gbp=50_000.0,
            year="horizon_end",
            net_of_salvage=True,
            price_basis="nominal_as_entered",
        ),
        replacements=(
            Replacement(
                year=4,
                component="inverter_pcs",
                cost_gbp=20_000.0,
                price_basis="nominal_as_entered",
            ),
            Replacement(
                year=20, component="transformer", cost_gbp=9_000.0, price_basis="nominal_as_entered"
            ),
        ),
    )
    out = _run(lifecycle=life)
    cfads = out.cashflows.lifecycle_cfads_by_year
    assert cfads[3] == -20_000.0 and cfads[_HORIZON - 1] == -50_000.0
    assert sum(cfads) == -70_000.0
    assert out.case.dscr_summary.by_year[3] < plain.case.dscr_summary.by_year[3]
    statuses = {f.flow: f.status for f in out.case.lifecycle.flows}
    assert statuses[LifecycleFlow.DECOMMISSIONING] is LifecycleFlowStatus.MODELLED
    assert statuses[LifecycleFlow.REPLACEMENT] is LifecycleFlowStatus.MODELLED
    assert "omission flatters the case" in _warning(
        out.case, "REPLACEMENT EXCLUDED — BEYOND HORIZON"
    )
    assert (
        "transformer replacement in year 20 (beyond horizon)" in out.cashflows.omitted_capital_flows
    )


def test_a_real_lifecycle_cost_is_escalated_on_an_indexed_run() -> None:
    dec = Decommissioning(
        cost_gbp=50_000.0, year=10, net_of_salvage=False, price_basis="real_at_as_of"
    )
    out = _run("gb_v1_12", lifecycle=_lifecycle(decommissioning=dec))
    e10 = out.cashflows.escalation_by_year[9]
    assert out.cashflows.lifecycle_cfads_by_year[9] == round(-50_000.0 * e10, 2)
    entry = next(f for f in out.case.lifecycle.flows if f.flow is LifecycleFlow.DECOMMISSIONING)
    assert entry.escalated and entry.basis_label == "nominal_cpi_escalated"


def test_a_sculpt_is_floored_at_zero_service_in_a_negative_lifecycle_year() -> None:
    cfads = [100_000.0, 100_000.0, -40_000.0, 100_000.0, 100_000.0]
    terms = DebtTerms(
        gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=5, sculpting_basis=SCULPTING_P90_DSCR
    )
    refused = size_and_measure_dscr(400_000.0, cfads, terms)
    floored = size_and_measure_dscr(400_000.0, cfads, terms, floor_negative_sculpt=True)
    assert any("sculpting unavailable" in w for w in refused.warnings)
    assert floored.debt_service_by_year_gbp[2] == 0.0
    assert all(v > 0 for i, v in enumerate(floored.debt_service_by_year_gbp) if i != 2)
    assert any("SCULPT FLOORED (D132)" in w for w in floored.warnings)


# --- identity and the no-input path ------------------------------------------------------------


def test_lifecycle_inputs_split_the_case_identity_and_absence_keeps_it() -> None:
    base = _run().case.case_id
    assert _run().case.case_id == base
    a = _run(lifecycle=_lifecycle(cod_delay_months=6)).case.case_id
    b = _run(lifecycle=_lifecycle(cod_delay_months=9)).case.case_id
    assert len({base, a, b}) == 3


def test_a_stated_cod_delay_alone_leaves_the_central_series_untouched() -> None:
    """The delay is a sensitivity row, not the central case (D130): no flow moves."""
    plain = _run()
    out = _run(lifecycle=_lifecycle(cod_delay_months=6))
    assert out.cashflows.p50_fcf_pre_debt_gbp == plain.cashflows.p50_fcf_pre_debt_gbp
    assert out.cashflows.p90_fcf_pre_debt_gbp == plain.cashflows.p90_fcf_pre_debt_gbp
    assert out.case.irr == plain.case.irr
    assert out.case.dscr_summary == plain.case.dscr_summary
    _warning(out.case, "SIZE SELECTED WITHOUT LIFECYCLE FLOWS")


def test_lifecycle_inputs_parse_from_json_and_yaml_and_refuse_bad_input() -> None:
    text = '{"provenance": {"source": "synthetic", "reference": "r"}, "cod_delay_months": 6}'
    assert parse_lifecycle_inputs_text(text).cod_delay_months == 6
    yaml_text = "provenance: {source: synthetic, reference: r}\ncod_delay_months: 3\n"
    assert parse_lifecycle_inputs_text(yaml_text, suffix=".yaml").cod_delay_months == 3
    with pytest.raises(LifecycleInputsError, match="at least one"):
        parse_lifecycle_inputs_text('{"provenance": {"source": "synthetic", "reference": "r"}}')
    with pytest.raises(LifecycleInputsError, match="not valid json"):
        parse_lifecycle_inputs_text("{")
