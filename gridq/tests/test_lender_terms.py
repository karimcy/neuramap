"""WP-UW-03: the typed lender-terms object and what the engine does with it (D75 to D78).

Every term set in this file is SYNTHETIC: made up to exercise the machinery, labelled
``source: synthetic`` in its provenance, and never a lender's offer. No figure here is, or stands
in for, a real rate, tenor, gearing or covenant.

Everything engine-side goes through `run_financial_model`, the entry point every production caller
reaches, so a test fails if the lender terms stop reaching the arithmetic, not only if the
arithmetic is wrong.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, run_financial_model
from gridq.finance.debt import (
    DSCR_BASIS_LENDER_PRE_TAX,
    DSCR_BASIS_P50_PRE_TAX,
    DSCR_BASIS_P90_PRE_TAX,
    straight_line_schedule,
)
from gridq.finance.financial_model import _hash_input
from gridq.finance.lender_terms import (
    LenderTermsError,
    lender_terms_are_firm,
    lender_terms_from_fields,
    load_lender_terms,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lender_terms.synthetic.json"
_HORIZON = 15
_LIB = "gb_v1_12"

#: SYNTHETIC lender terms: the base every test below varies one field of.
_SYNTHETIC: dict[str, Any] = {
    "provenance": {"source": "synthetic", "reference": "SYNTHETIC WP-UW-03 test terms"},
    "tenor_years": 10,
    "interest_rate_pct": 7.0,
    "gearing_pct": 50.0,
    "amortisation": "annuity",
    "cashflow_case": "P90",
    "calculation_period": "annual",
    "covenant": {"default_dscr": 1.2, "lock_up_dscr": 1.3},
}


def _terms(**overrides: Any) -> s.LenderTerms:
    data = {**_SYNTHETIC, **overrides}
    return s.LenderTerms.model_validate(data)


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_lender_terms",
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
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


#: A synthetic stack whose CFADS is positive in every year, with a contracted leg so the LENDER
#: case has its own (smaller) series.
_RESULT = s.OptimisationResult(
    result_id="res_lender_terms",
    request_id="req_lender_terms",
    implementation_level=s.ImplementationLevel.BASELINE,
    bankable_status=s.BankableStatus.NOT_BANKABLE,
    selected_power_kw=1_000.0,
    selected_energy_kwh=2_000.0,
    duration_hours=2.0,
    value_streams=[
        s.ValueStreamResult(stream=st, annual_revenue_gbp=rev, annual_cost_gbp=cost, firmness=firm)
        for st, rev, cost, firm in [
            (s.MarketStream.TARIFF_ARBITRAGE, 220_000.0, 12_000.0, s.Firmness.FIRM),
            (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 45_000.0, 0.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 45_000.0, 0.0, s.Firmness.CONTRACTED),
            (s.MarketStream.LOCAL_FLEX_PICLO, 15_000.0, 0.0, s.Firmness.CONTRACTED),
        ]
    ],
    solver_status=s.SolverStatus.BASELINE,
)


def _run(**kwargs: Any) -> Any:
    kwargs.setdefault("horizon_years", _HORIZON)
    return run_financial_model(_site(), _RESULT, load_assumptions(_LIB), **kwargs)


# --- the schema: nothing defaulted, nothing guessed -----------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "tenor_years",
        "interest_rate_pct",
        "gearing_pct",
        "amortisation",
        "cashflow_case",
        "calculation_period",
    ],
)
def test_every_core_term_is_required(missing: str) -> None:
    """No rate, tenor, gearing, profile, case or period is ever defaulted by the schema."""
    data = {k: v for k, v in _SYNTHETIC.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        s.LenderTerms.model_validate(data)


def test_the_covenant_is_optional_and_never_defaulted() -> None:
    terms = _terms(covenant=None)
    assert terms.covenant is None


@pytest.mark.parametrize("field", ["interest_rate_pct", "gearing_pct"])
def test_a_fraction_in_a_percent_field_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match="FRACTION"):
        _terms(**{field: 0.07})


def test_a_sculpting_target_belongs_to_a_sculpted_profile_only() -> None:
    with pytest.raises(ValidationError, match="sculpting_target_dscr applies only"):
        _terms(amortisation="annuity", sculpting_target_dscr=1.35)
    assert _terms(amortisation="sculpted", sculpting_target_dscr=1.35).sculpting_target_dscr


def test_a_covenant_block_states_a_threshold_and_lock_up_sits_above_default() -> None:
    with pytest.raises(ValidationError, match="must state default_dscr"):
        _terms(covenant={})
    with pytest.raises(ValidationError, match="below default_dscr"):
        _terms(covenant={"default_dscr": 1.3, "lock_up_dscr": 1.2})
    assert _terms(covenant={"lock_up_dscr": 1.25}).covenant is not None


def test_terms_are_frozen() -> None:
    terms = _terms()
    with pytest.raises(ValidationError):
        terms.tenor_years = 12  # type: ignore[misc]


def test_the_synthetic_fixture_loads_from_json_and_yaml(tmp_path: Path) -> None:
    import json

    import yaml

    from_json = load_lender_terms(FIXTURE)
    assert from_json.is_synthetic
    yml = tmp_path / "terms.yaml"
    yml.write_text(yaml.safe_dump(json.loads(FIXTURE.read_text())), encoding="utf-8")
    assert load_lender_terms(yml) == from_json


def test_an_invalid_file_is_a_reader_facing_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"tenor_years": 10}', encoding="utf-8")
    with pytest.raises(LenderTermsError, match="provenance"):
        load_lender_terms(bad)


def test_form_fields_all_blank_mean_no_terms_and_a_partial_entry_is_refused() -> None:
    blank = {name: "" for name in ("lender_source", "lender_tenor_years", "lender_gearing_pct")}
    assert lender_terms_from_fields(blank) is None
    with pytest.raises(LenderTermsError, match="interest_rate_pct"):
        lender_terms_from_fields({"lender_tenor_years": "10", "lender_source": "synthetic"})
    full = lender_terms_from_fields(
        {
            "lender_source": "synthetic",
            "lender_reference": "SYNTHETIC form test",
            "lender_tenor_years": "10",
            "lender_interest_rate_pct": "7",
            "lender_gearing_pct": "50",
            "lender_amortisation": "straight_line",
            "lender_cashflow_case": "LENDER",
            "lender_calculation_period": "annual",
            "lender_default_dscr": "1.2",
            "lender_lock_up_dscr": "",
        }
    )
    assert full is not None
    assert full.amortisation is s.AmortisationProfile.STRAIGHT_LINE
    assert full.cashflow_case is s.CashflowCase.LENDER
    assert full.covenant == s.DscrCovenantTerms(default_dscr=1.2)


# --- the engine: supplied terms produce a measured DSCR --------------------------------------


def test_supplied_terms_measure_a_dscr_and_ride_onto_the_case() -> None:
    terms = _terms()
    model = _run(lender_terms=terms)
    case = model.case
    assert case.dscr_summary.status is s.DscrStatus.MEASURED
    assert case.dscr_summary.min_dscr is not None and case.dscr_summary.min_dscr > 0
    assert case.dscr_summary.unavailable_reason is None
    assert case.dscr_summary.cashflow_case is s.CashflowCase.P90
    assert case.lender_terms == terms
    assert case.equity_irr is not None, "the lender's debt service reaches the equity return"
    assert case.debt_assumptions.principal_gbp == pytest.approx(
        model.cashflows.capex.total_gbp * 0.5, abs=0.01
    )
    assert case.debt_assumptions.sized_by == "gearing"
    # The synthetic label rides on the case, and the case is never bankable on synthetic terms.
    assert any(w.startswith("SYNTHETIC LENDER TERMS") for w in case.warnings)
    assert model.bankable is False


def test_annuity_terms_reproduce_the_legacy_debt_terms_measurement() -> None:
    """The same structure stated as LenderTerms or as bare DebtTerms gives the same ratio."""
    via_terms = _run(lender_terms=_terms(covenant=None)).case.dscr_summary
    via_legacy = _run(
        debt_terms=DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)
    ).case.dscr_summary
    assert via_terms.min_dscr == via_legacy.min_dscr
    assert via_terms.by_year == via_legacy.by_year
    assert via_terms.basis == via_legacy.basis == DSCR_BASIS_P90_PRE_TAX


def test_the_cashflow_case_selects_the_series_the_dscr_is_measured_on() -> None:
    p50 = _run(lender_terms=_terms(cashflow_case="P50"))
    p90 = _run(lender_terms=_terms(cashflow_case="P90"))
    lender = _run(lender_terms=_terms(cashflow_case="LENDER"))
    assert p50.case.dscr_summary.basis == DSCR_BASIS_P50_PRE_TAX
    assert p90.case.dscr_summary.basis == DSCR_BASIS_P90_PRE_TAX
    assert lender.case.dscr_summary.basis == DSCR_BASIS_LENDER_PRE_TAX
    # Same loan, same service: the ratio is each case's own CFADS over it, year by year.
    service = p90.dscr.debt_service_by_year_gbp
    assert p50.dscr.debt_service_by_year_gbp == service == lender.dscr.debt_service_by_year_gbp
    for model, series in (
        (p50, p50.cashflows.p50_fcf_pre_debt_gbp[1:]),
        (p90, p90.cashflows.p90_fcf_pre_debt_gbp[1:]),
        (lender, lender.cashflows.p90_contracted_ebitda_gbp),
    ):
        assert model.case.dscr_summary.by_year == [
            round(c / d, 4) for c, d in zip(series, service, strict=False)
        ]
    assert (
        p50.case.dscr_summary.min_dscr
        > p90.case.dscr_summary.min_dscr
        > lender.case.dscr_summary.min_dscr
    )


def test_straight_line_amortisation_repays_equal_principal() -> None:
    schedule = straight_line_schedule(100_000.0, 10.0, 4)
    assert schedule == [35_000.0, 32_500.0, 30_000.0, 27_500.0]
    model = _run(lender_terms=_terms(amortisation="straight_line"))
    service = model.dscr.debt_service_by_year_gbp
    assert service == sorted(service, reverse=True) and service[0] > service[-1]
    assert model.case.debt_assumptions.sculpting_basis == "STRAIGHT_LINE"


def test_a_sculpting_target_sizes_the_debt_when_the_cfads_supports_less_than_the_cap() -> None:
    """D77: principal = min(capex x gearing, PV(CFADS / target)); every year at the target."""
    model = _run(
        lender_terms=_terms(
            amortisation="sculpted", sculpting_target_dscr=2.0, gearing_pct=100.0, covenant=None
        )
    )
    da = model.case.debt_assumptions
    assert da.sized_by == "dscr_target"
    assert da.sculpting_basis == "P90_DSCR"
    assert da.principal_gbp is not None and da.principal_gbp < model.cashflows.capex.total_gbp
    assert all(r == pytest.approx(2.0, abs=1e-3) for r in model.case.dscr_summary.by_year)
    assert any("sized by the sculpting target" in w for w in model.case.warnings)


def test_a_sculpting_target_never_lends_past_the_gearing_cap() -> None:
    model = _run(
        lender_terms=_terms(amortisation="sculpted", sculpting_target_dscr=1.1, gearing_pct=20.0)
    )
    da = model.case.debt_assumptions
    assert da.sized_by == "gearing"
    assert da.principal_gbp == pytest.approx(model.cashflows.capex.total_gbp * 0.2, abs=0.01)
    assert model.case.dscr_summary.min_dscr is not None
    assert model.case.dscr_summary.min_dscr >= 1.1


def test_covenant_tiers_come_from_the_terms_with_a_lock_up() -> None:
    """D76 / H-05: default and lock-up thresholds, both tested, sourced from the terms."""
    model = _run(lender_terms=_terms(covenant={"default_dscr": 1.2, "lock_up_dscr": 50.0}))
    cov = model.case.covenant_status
    assert cov.source == "lender_terms"
    assert cov.covenant == 1.2 and cov.passes is True
    assert cov.lock_up_dscr == 50.0 and cov.lock_up_passes is False


def test_terms_without_a_covenant_are_never_tested_against_the_library_placeholder() -> None:
    model = _run(lender_terms=_terms(covenant=None))
    cov = model.case.covenant_status
    assert cov.covenant is None and cov.passes is None
    assert cov.not_evaluated_reason is not None
    assert "state no DSCR covenant" in cov.not_evaluated_reason
    assert model.case.dscr_summary.min_dscr is not None, "the DSCR is still measured"


def test_a_semi_annual_test_period_is_refused_not_annualised() -> None:
    model = _run(lender_terms=_terms(calculation_period="semi_annual"))
    d = model.case.dscr_summary
    assert d.min_dscr is None and d.status is s.DscrStatus.PERIOD_NOT_MODELLED
    assert d.unavailable_reason is not None and "semi-annual" in d.unavailable_reason
    assert model.dscr.debt_service_by_year_gbp == [], "no debt is sized on a refused test period"
    assert model.case.equity_irr is None
    assert model.case.debt_assumptions.tenor_years == 10.0, "the lender's figures are still shown"


def test_a_tenor_past_the_horizon_is_unavailable_with_its_reason() -> None:
    model = _run(lender_terms=_terms(tenor_years=20))
    d = model.case.dscr_summary
    assert d.min_dscr is None and d.status is s.DscrStatus.TENOR_BEYOND_HORIZON
    assert d.unavailable_reason is not None and "runs past the projection horizon" in (
        d.unavailable_reason
    )


def test_a_run_with_no_terms_states_why_it_has_no_dscr() -> None:
    """B001: the real-site case (credit band unknown, no terms) says so on the record."""
    case = _run().case
    d = case.dscr_summary
    assert d.min_dscr is None
    assert d.status is s.DscrStatus.NO_DEBT_TERMS
    assert d.unavailable_reason is not None
    assert d.unavailable_reason.startswith("No lender terms supplied")
    assert "'unknown'" in d.unavailable_reason, "the credit band that supplied nothing is named"
    assert case.lender_terms is None


def test_lender_terms_and_legacy_debt_terms_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        _run(
            lender_terms=_terms(),
            debt_terms=DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10),
        )


def test_only_a_term_sheet_with_a_default_covenant_is_firm() -> None:
    term_sheet = {"source": "term_sheet", "reference": "SYNTHETIC stand-in for a term sheet"}
    assert lender_terms_are_firm(_terms(provenance=term_sheet))
    assert not lender_terms_are_firm(_terms(provenance=term_sheet, covenant=None))
    assert not lender_terms_are_firm(_terms())  # synthetic
    indication = {"source": "lender_indication", "reference": "SYNTHETIC indication"}
    assert not lender_terms_are_firm(_terms(provenance=indication))


# --- identity (N25, D78) ---------------------------------------------------------------------


def test_debt_terms_lender_terms_and_price_structure_split_the_case_id() -> None:
    """N25: different debt or price structures may never share a case_id."""
    ids = {
        _run().case.case_id,
        _run(lender_terms=_terms()).case.case_id,
        _run(lender_terms=_terms(gearing_pct=40.0)).case.case_id,
        _run(
            debt_terms=DebtTerms(gearing_pct=60.0, annual_rate_pct=7.0, tenor_years=10)
        ).case.case_id,
        _run(
            debt_terms=DebtTerms(gearing_pct=40.0, annual_rate_pct=7.0, tenor_years=10)
        ).case.case_id,
        _run(price_structure=s.PriceStructure.CAPACITY_FEE).case.case_id,
    }
    assert len(ids) == 6


def test_a_run_without_terms_keeps_its_historical_case_hash_input() -> None:
    payload = _hash_input(_site(), _RESULT, load_assumptions(_LIB), _HORIZON, None, None, ())
    assert not {"debt_terms", "lender_terms", "price_structure"} & set(payload)
