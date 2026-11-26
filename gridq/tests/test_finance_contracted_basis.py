"""Tests for the contracted-basis CFADS measurement in debt sizing (W8 slice 4).

The lender shape: the contracted-basis P90 DSCR is MEASURED against the same debt service
(conservative all-costs CFADS — no cost allocation invented) and reported; it is gated only
informationally until `dscr_covenants.min_p90_dscr_contracted` exists — the blended P90
covenant remains the formal gate, and DSCR stays unavailable without debt terms (B001).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.finance.cashflows import build_cashflow_model
from gridq.finance.debt import DebtTerms, assess_debt, dscr_by_year
from gridq.finance.financial_model import run_financial_model

DEBT = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_basis",
        request_id="req_basis",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(stream=st, annual_revenue_gbp=rev, firmness=firm)
            for st, rev, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def _mixed() -> s.OptimisationResult:
    return _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 60_000.0, s.Firmness.CONTRACTED),
        ]
    )


def test_contracted_series_identity(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    model = build_cashflow_model(make_site(), _mixed(), lib)
    hc = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)  # shared (no key)
    expected_year1 = round(
        60_000.0 * (1 - hc / 100.0)
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_contracted_ebitda_gbp[0] == pytest.approx(expected_year1, abs=0.02)
    assert len(model.p90_contracted_ebitda_gbp) == model.horizon_years


def test_contracted_dscr_measured_not_gated_without_the_key(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    model = build_cashflow_model(make_site(), _mixed(), lib)
    dscr, covenant = assess_debt(
        model.capex.total_gbp,
        model.p90_fcf_pre_debt_gbp[1:],
        lib,
        debt_terms=DEBT,
        p90_contracted_cfads_by_year=model.p90_contracted_ebitda_gbp,
    )
    assert dscr.available is True
    expected_series = dscr_by_year(
        model.p90_contracted_ebitda_gbp[: DEBT.tenor_years], dscr.debt_service_by_year_gbp
    )
    assert dscr.contracted_by_year == expected_series
    assert dscr.min_contracted_dscr == pytest.approx(round(min(expected_series), 4))
    assert any("NOT gated" in w for w in dscr.warnings)
    # The formal gate is unchanged: blended covenant only.
    assert covenant.min_dscr == dscr.min_dscr


def test_per_basis_key_yields_an_informational_verdict(
    make_site: Callable[..., s.SiteModel],
) -> None:
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["dscr_covenants"]["min_p90_dscr_contracted"] = {
        "value": 1.0,
        "unit": "ratio",
        "source": "test",
        "status": "placeholder",
        "owner_or_reviewer": "test",
        "confidence": "low",
        "last_reviewed": str(date(2026, 7, 2)),
        "notes": "test-only",
    }
    lib = AssumptionLibrary.model_validate(base)
    model = build_cashflow_model(make_site(), _mixed(), lib)
    dscr, _ = assess_debt(
        model.capex.total_gbp,
        model.p90_fcf_pre_debt_gbp[1:],
        lib,
        debt_terms=DEBT,
        p90_contracted_cfads_by_year=model.p90_contracted_ebitda_gbp,
    )
    assert any(("meets" in w or "BREACHES" in w) for w in dscr.warnings)
    assert not any("NOT gated" in w for w in dscr.warnings)


def test_no_debt_terms_means_no_contracted_measurement(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")  # no gearing keys → DSCR unavailable (B001)
    model = build_cashflow_model(make_site(), _mixed(), lib)
    dscr, _ = assess_debt(
        model.capex.total_gbp,
        model.p90_fcf_pre_debt_gbp[1:],
        lib,
        p90_contracted_cfads_by_year=model.p90_contracted_ebitda_gbp,
    )
    assert dscr.available is False
    assert dscr.min_contracted_dscr is None  # the gate never softens


def test_financial_model_threads_the_basis_only_when_contracted_is_priced(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site()
    lib = load_assumptions("gb_v0_1")
    mixed = run_financial_model(site, _mixed(), lib, debt_terms=DEBT)
    assert mixed.dscr.min_contracted_dscr is not None
    merchant_only = run_financial_model(
        site,
        _result([(s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM)]),
        lib,
        debt_terms=DEBT,
    )
    assert merchant_only.dscr.min_contracted_dscr is None  # no basis to report, not a negative one
