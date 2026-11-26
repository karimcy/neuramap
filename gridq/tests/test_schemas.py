"""Tests for the shared schema layer — construction, validation rules, and examples.

No pricing logic is exercised; these assert the contracts' shapes and guard rails.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.finance.financial_model import capital_recovery_account

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"


# --- helpers ---------------------------------------------------------------


def _minimal_tariff() -> s.TariffStack:
    return s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        supplier="X",
        import_rates={"single": 0.24},
    )


def _minimal_counterparty() -> s.Counterparty:
    return s.Counterparty(legal_name="Acme Ltd", company_number="01234567")


def _site(data_tier: s.DataTier, **overrides: object) -> s.SiteModel:
    kwargs: dict = dict(
        site_id="site_1",
        postcode="LE17 4XN",
        data_tier=data_tier,
        load_data=s.LoadDataSummary(annual_kwh=100000.0),
        annual_consumption_kwh=100000.0,
        tariff_stack=_minimal_tariff(),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=_minimal_counterparty(),
    )
    kwargs.update(overrides)
    return s.SiteModel(**kwargs)


# --- SiteModel -------------------------------------------------------------


def test_valid_t0_site_model() -> None:
    site = _site(s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE)
    assert site.data_tier == s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
    assert site.schema_version == s.SCHEMA_VERSION


def test_valid_t2_site_model() -> None:
    site = _site(
        s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        ),
    )
    assert site.load_data.has_hh_coverage is True


def test_t2_without_hh_coverage_is_rejected() -> None:
    with pytest.raises(ValidationError, match="half-hourly coverage"):
        _site(
            s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
            load_data=s.LoadDataSummary(annual_kwh=1_800_000.0),  # no rows/dates
        )


def test_invalid_negative_power_is_rejected() -> None:
    with pytest.raises(ValidationError):
        s.ConnectionDetails(mic_kw=-1.0)


def test_invalid_data_tier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _site("T9_NOT_A_TIER")  # type: ignore[arg-type]


# --- OptimisationResult ----------------------------------------------------


def _result(**overrides: object) -> s.OptimisationResult:
    kwargs: dict = dict(
        result_id="res_1",
        request_id="req_1",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        solver_status=s.SolverStatus.MOCK,
    )
    kwargs.update(overrides)
    return s.OptimisationResult(**kwargs)


def test_baseline_result_cannot_be_bankable_candidate() -> None:
    with pytest.raises(ValidationError, match="baseline"):
        _result(
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.BANKABLE_CANDIDATE,
        )


def test_baseline_result_can_be_review_required() -> None:
    r = _result(
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        solver_status=s.SolverStatus.BASELINE,
    )
    assert r.bankable_status == s.BankableStatus.REVIEW_REQUIRED


# --- ValueStreamResult -----------------------------------------------------


def test_gated_stream_cannot_be_firm() -> None:
    # A gated-upside (conditional) stream cannot be marked firm.
    with pytest.raises(ValidationError, match="cannot be marked firm"):
        s.ValueStreamResult(stream=s.MarketStream.WHOLESALE_P415, firmness=s.Firmness.FIRM)


def test_non_gated_stream_may_be_firm() -> None:
    v = s.ValueStreamResult(stream=s.MarketStream.TARIFF_ARBITRAGE, firmness=s.Firmness.FIRM)
    assert v.firmness == s.Firmness.FIRM


# --- QuoteRecord -----------------------------------------------------------


def test_quote_record_validates() -> None:
    q = s.QuoteRecord(
        quote_id="q1",
        quote_mode=s.QuoteMode.INDICATIVE,
        site_id="site_1",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
    )
    assert q.quote_mode == s.QuoteMode.INDICATIVE


def test_underwritten_from_t0_without_acknowledgement_is_rejected() -> None:
    with pytest.raises(ValidationError, match="insufficient to underwrite firmly"):
        s.QuoteRecord(
            quote_id="q2",
            quote_mode=s.QuoteMode.UNDERWRITTEN,
            site_id="site_1",
            data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        )


def test_underwritten_from_t0_with_a_generic_warning_is_still_rejected() -> None:
    """N19: the guard used to accept ANY non-empty warnings list, and every quote the pipeline
    builds carries warnings by construction — so it could never fire. A plausible-looking
    hand-written warning must not satisfy it either."""
    with pytest.raises(ValidationError, match="insufficient to underwrite firmly"):
        s.QuoteRecord(
            quote_id="q3",
            quote_mode=s.QuoteMode.UNDERWRITTEN,
            site_id="site_1",
            data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
            warnings=["underwritten on T0 data — estimate only, validate before signature"],
        )


def test_underwritten_from_t0_with_the_acknowledgement_is_allowed() -> None:
    q = s.QuoteRecord(
        quote_id="q4",
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        site_id="site_1",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        warnings=[s.T0_UNDERWRITING_ACKNOWLEDGEMENT],
    )
    assert q.warnings


# --- FinancialCase ---------------------------------------------------------


def test_financial_case_requires_both_cashflow_series() -> None:
    with pytest.raises(ValidationError, match="P50/P90 mandatory"):
        s.FinancialCase(
            case_id="fc1",
            p50_cashflows=[-250000.0, 48000.0],
            p90_cashflows=[],  # missing P90
            dscr_summary=s.DscrSummary(min_dscr=1.3),
            capital_recovery=capital_recovery_account([-250000.0, 48000.0]),
            debt_assumptions=s.DebtAssumptions(),
            price_recommendation=s.PriceRecommendation(),
            covenant_status=s.CovenantStatus(),
        )


def _minimal_financial_case(**overrides: object) -> s.FinancialCase:
    kwargs: dict[str, object] = dict(
        case_id="fc_eq",
        p50_cashflows=[-250000.0, 48000.0],
        p90_cashflows=[-250000.0, 40000.0],
        dscr_summary=s.DscrSummary(min_dscr=1.3),
        capital_recovery=capital_recovery_account([-250000.0, 48000.0]),
        debt_assumptions=s.DebtAssumptions(),
        price_recommendation=s.PriceRecommendation(),
        covenant_status=s.CovenantStatus(),
    )
    kwargs.update(overrides)
    return s.FinancialCase(**kwargs)  # type: ignore[arg-type]


def test_financial_case_equity_irr_defaults_none() -> None:
    # The additive levered-return field is optional and defaults to None (backward-compatible).
    assert _minimal_financial_case().equity_irr is None


def test_financial_case_accepts_equity_irr() -> None:
    case = _minimal_financial_case(equity_irr=14.2)
    assert case.equity_irr == 14.2
    # extra='forbid' is unaffected by the additive field — an unknown field still rejects.
    with pytest.raises(ValidationError):
        _minimal_financial_case(not_a_field=1)


# --- examples round-trip ---------------------------------------------------


@pytest.mark.parametrize(
    "filename,model",
    [
        ("site_t0.json", s.SiteModel),
        ("site_t2.json", s.SiteModel),
        ("optimisation_request.json", s.OptimisationRequest),
        ("optimisation_result_baseline.json", s.OptimisationResult),
        ("quote_record_example.json", s.QuoteRecord),
    ],
)
def test_example_files_validate(filename: str, model: type) -> None:
    data = json.loads((EXAMPLES / filename).read_text())
    obj = model.model_validate(data)
    assert obj.schema_version == s.SCHEMA_VERSION


def test_strict_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        s.Counterparty(legal_name="Acme", nonexistent_field=1)  # type: ignore[call-arg]
