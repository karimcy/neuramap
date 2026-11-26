"""Tests for the deterministic counterparty credit -> rate/tenor mapping (M9) and its wiring.

Covers the mapping itself (`finance/credit.py`) and its integration into the financial model:
- a given credit band always yields the same rate/tenor (deterministic, library-driven);
- worse credit never buys a cheaper or longer loan (monotonicity — spec M9);
- unassessed credit is *unavailable*, never defaulted to a band or a guessed number;
- the mapped rate/tenor reach the `FinancialCase` even when gearing (Sim's) is absent, while DSCR
  stays unavailable and the case stays not-bankable on placeholders;
- an injected `DebtTerms` always wins (the credit path is skipped).
"""

from __future__ import annotations

from datetime import date

import pytest

import gridq.schemas as s
from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus, load_assumptions
from gridq.finance import (
    DebtTerms,
    build_debt_terms_from_credit,
    map_credit_to_rate_tenor,
    run_financial_model,
)
from gridq.schemas.enums import Confidence, CreditStatus

# Mirrors the gb_v0_1 placeholder policy table; the tests assert against the library, not these.
_EXPECTED = {
    CreditStatus.STRONG: (6.0, 12),
    CreditStatus.ADEQUATE: (7.5, 10),
    CreditStatus.WEAK: (9.5, 7),
}


@pytest.fixture
def lib() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def _assumption(
    value: object, *, status: AssumptionStatus = AssumptionStatus.PLACEHOLDER
) -> Assumption:
    return Assumption(
        value=value,
        unit="test",
        source="test",
        status=status,
        owner_or_reviewer="test",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 1, 1),
        notes="test fixture",
    )


def _with_policy(
    lib: AssumptionLibrary, table: object, *, status: AssumptionStatus
) -> AssumptionLibrary:
    copy = lib.model_copy(deep=True)
    copy.financing["credit_rate_tenor_policy"] = _assumption(table, status=status)
    return copy


def _with_gearing(
    lib: AssumptionLibrary, gearing_pct: float, *, status: AssumptionStatus
) -> AssumptionLibrary:
    copy = lib.model_copy(deep=True)
    copy.financing["debt_gearing_pct"] = _assumption(gearing_pct, status=status)
    return copy


def _positive_result() -> s.OptimisationResult:
    """A minimal result with positive sizing so the financial model has something to price."""
    return s.OptimisationResult(
        result_id="res_credit",
        request_id="req_credit",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=500.0,
        selected_energy_kwh=1000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=40000.0,
                firmness=s.Firmness.FIRM,
                gate_status="met",
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )


def _site_with_credit(make_site, credit_status: CreditStatus, **cp_kwargs) -> s.SiteModel:
    site = make_site()
    cp = s.Counterparty(
        legal_name="Acme Ltd",
        company_number="01234567",
        credit_status=credit_status,
        **cp_kwargs,
    )
    return site.model_copy(update={"counterparty": cp})


# --- the mapping ----------------------------------------------------------------------------


@pytest.mark.parametrize("band", list(_EXPECTED))
def test_maps_each_band_to_library_values(lib: AssumptionLibrary, band: CreditStatus) -> None:
    crt, warnings = map_credit_to_rate_tenor(band, lib)
    assert crt is not None
    assert (crt.rate_pct, crt.tenor_years) == _EXPECTED[band]
    # placeholder policy ⇒ a "not bankable until confirmed" warning is surfaced
    assert any("not bankable until confirmed" in w or "indicative" in w for w in warnings)


def test_mapping_is_total_and_complete_over_every_credit_band(lib: AssumptionLibrary) -> None:
    """Every credit band is handled, and the mapped set is exactly the documented policy bands.

    Totality: each `CreditStatus` member either maps to a documented (rate, tenor) or is explicitly
    unavailable (with a reason) — none falls through unhandled, so adding a band to the enum without
    handling it fails here. Completeness: the bands that map are exactly the bands the library
    documents (no documented band ignored, no band mapped from outside the library).
    """
    policy_bands = set(lib.get("financing", "credit_rate_tenor_policy").value.keys())
    mapped: set[str] = set()
    unavailable: set[str] = set()
    for band in CreditStatus:
        crt, warnings = map_credit_to_rate_tenor(band, lib)
        if crt is None:
            assert warnings, f"{band.value} is unavailable but gives no reason"
            unavailable.add(band.value)
        else:
            assert crt.rate_pct > 0 and crt.tenor_years > 0
            assert band.value in policy_bands  # mapped value is documented in the library
            mapped.add(band.value)
    # Totality: every enum member is handled exactly once.
    assert mapped | unavailable == {b.value for b in CreditStatus}
    assert not (mapped & unavailable)
    # Completeness: the mapped bands are exactly the documented policy bands.
    assert mapped == policy_bands
    assert mapped  # the policy is non-empty


def test_mapping_is_deterministic(lib: AssumptionLibrary) -> None:
    first = map_credit_to_rate_tenor(CreditStatus.ADEQUATE, lib)[0]
    second = map_credit_to_rate_tenor(CreditStatus.ADEQUATE, lib)[0]
    assert first == second


def test_monotonic_worse_credit_is_dearer_and_shorter(lib: AssumptionLibrary) -> None:
    strong = map_credit_to_rate_tenor(CreditStatus.STRONG, lib)[0]
    adequate = map_credit_to_rate_tenor(CreditStatus.ADEQUATE, lib)[0]
    weak = map_credit_to_rate_tenor(CreditStatus.WEAK, lib)[0]
    assert strong is not None and adequate is not None and weak is not None
    assert strong.rate_pct <= adequate.rate_pct <= weak.rate_pct
    assert strong.tenor_years >= adequate.tenor_years >= weak.tenor_years


@pytest.mark.parametrize("band", [CreditStatus.UNKNOWN, CreditStatus.NOT_ASSESSED])
def test_unassessed_credit_is_unavailable_never_defaulted(
    lib: AssumptionLibrary, band: CreditStatus
) -> None:
    crt, warnings = map_credit_to_rate_tenor(band, lib)
    assert crt is None  # never falls through to WEAK or any band
    assert any("not assessed" in w for w in warnings)


def test_library_driven_no_private_constants(lib: AssumptionLibrary) -> None:
    custom = _with_policy(
        lib,
        {
            "strong": {"rate_pct": 4.0, "tenor_years": 15},
            "adequate": {"rate_pct": 5.0, "tenor_years": 13},
            "weak": {"rate_pct": 6.0, "tenor_years": 11},
        },
        status=AssumptionStatus.PLACEHOLDER,
    )
    crt = map_credit_to_rate_tenor(CreditStatus.STRONG, custom)[0]
    assert crt is not None
    assert (crt.rate_pct, crt.tenor_years) == (4.0, 15)  # reads the library, not a constant


def test_non_monotonic_policy_refuses_to_map(lib: AssumptionLibrary) -> None:
    bad = _with_policy(
        lib,
        {
            "strong": {"rate_pct": 9.0, "tenor_years": 7},  # strong dearer than weak — invalid
            "adequate": {"rate_pct": 7.5, "tenor_years": 10},
            "weak": {"rate_pct": 6.0, "tenor_years": 12},
        },
        status=AssumptionStatus.PLACEHOLDER,
    )
    crt, warnings = map_credit_to_rate_tenor(CreditStatus.STRONG, bad)
    assert crt is None
    assert any("non-monotonic" in w for w in warnings)


def test_absent_policy_is_unavailable(lib: AssumptionLibrary) -> None:
    copy = lib.model_copy(deep=True)
    copy.financing.pop("credit_rate_tenor_policy", None)
    crt, warnings = map_credit_to_rate_tenor(CreditStatus.STRONG, copy)
    assert crt is None
    assert any("credit_rate_tenor_policy" in w for w in warnings)


def test_malformed_policy_band_skipped_never_guessed(lib: AssumptionLibrary) -> None:
    bad = _with_policy(
        lib,
        {"strong": {"rate_pct": "cheap", "tenor_years": 12}},  # non-numeric rate
        status=AssumptionStatus.PLACEHOLDER,
    )
    crt, warnings = map_credit_to_rate_tenor(CreditStatus.STRONG, bad)
    assert crt is None
    assert any("malformed" in w for w in warnings)


def test_confirmed_policy_has_no_placeholder_warning(lib: AssumptionLibrary) -> None:
    confirmed = _with_policy(
        lib,
        {
            "strong": {"rate_pct": 6.0, "tenor_years": 12},
            "adequate": {"rate_pct": 7.5, "tenor_years": 10},
            "weak": {"rate_pct": 9.5, "tenor_years": 7},
        },
        status=AssumptionStatus.CONFIRMED,
    )
    crt, warnings = map_credit_to_rate_tenor(CreditStatus.STRONG, confirmed)
    assert crt is not None
    assert not any("not bankable until confirmed" in w for w in warnings)


# --- build_debt_terms_from_credit (combining with gearing) ----------------------------------


def test_no_gearing_keeps_rate_tenor_but_no_debt_terms(lib: AssumptionLibrary) -> None:
    crt, terms, warnings = build_debt_terms_from_credit(CreditStatus.ADEQUATE, lib)
    assert crt is not None and (crt.rate_pct, crt.tenor_years) == (7.5, 10)
    assert terms is None  # gb_v0_1 has no debt_gearing_pct
    assert any("debt_gearing_pct" in w for w in warnings)


def test_gearing_override_assembles_full_debt_terms(lib: AssumptionLibrary) -> None:
    crt, terms, _ = build_debt_terms_from_credit(CreditStatus.ADEQUATE, lib, gearing_override=60.0)
    assert terms == DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=10)


def test_library_gearing_assembles_full_debt_terms(lib: AssumptionLibrary) -> None:
    geared = _with_gearing(lib, 55.0, status=AssumptionStatus.PLACEHOLDER)
    _, terms, _ = build_debt_terms_from_credit(CreditStatus.STRONG, geared)
    assert terms is not None
    assert (terms.gearing_pct, terms.annual_rate_pct, terms.tenor_years) == (55.0, 6.0, 12)


def test_unmappable_credit_yields_no_terms(lib: AssumptionLibrary) -> None:
    crt, terms, warnings = build_debt_terms_from_credit(CreditStatus.UNKNOWN, lib)
    assert crt is None and terms is None
    assert warnings


def test_expected_tenure_shorter_than_tenor_warns_without_truncating(
    lib: AssumptionLibrary,
) -> None:
    crt, _, warnings = build_debt_terms_from_credit(
        CreditStatus.STRONG, lib, expected_tenure_years=5.0
    )
    assert crt is not None and crt.tenor_years == 12  # NOT truncated to 5
    assert any("expected tenure" in w for w in warnings)


# --- integration with run_financial_model ---------------------------------------------------


def test_credit_rate_tenor_flows_to_financial_case(make_site, lib: AssumptionLibrary) -> None:
    site = _site_with_credit(make_site, CreditStatus.ADEQUATE)
    out = run_financial_model(site, _positive_result(), lib)
    da = out.case.debt_assumptions
    assert (da.rate_pct, da.tenor_years) == (7.5, 10.0)  # mapped from the ADEQUATE band
    assert da.gearing_pct is None  # gearing (Sim's) absent in v1
    assert out.dscr.available is False  # DSCR honestly unavailable without gearing
    assert out.bankable is False  # never bankable on placeholders


def test_unassessed_credit_leaves_debt_assumptions_empty(make_site, lib: AssumptionLibrary) -> None:
    site = _site_with_credit(make_site, CreditStatus.UNKNOWN)
    out = run_financial_model(site, _positive_result(), lib)
    da = out.case.debt_assumptions
    assert da.rate_pct is None and da.tenor_years is None
    assert out.bankable is False


def test_injected_debt_terms_bypass_the_credit_path(make_site, lib: AssumptionLibrary) -> None:
    site = _site_with_credit(make_site, CreditStatus.ADEQUATE)
    out = run_financial_model(
        site,
        _positive_result(),
        lib,
        debt_terms=DebtTerms(gearing_pct=80.0, annual_rate_pct=3.0, tenor_years=15),
    )
    da = out.case.debt_assumptions
    # the injected terms win — NOT the 7.5%/10y the ADEQUATE band would have mapped
    assert (da.gearing_pct, da.rate_pct, da.tenor_years) == (80.0, 3.0, 15.0)


def test_library_gearing_enables_dscr_but_stays_not_bankable(
    make_site, lib: AssumptionLibrary
) -> None:
    geared = _with_gearing(lib, 60.0, status=AssumptionStatus.PLACEHOLDER)
    site = _site_with_credit(make_site, CreditStatus.ADEQUATE)
    out = run_financial_model(site, _positive_result(), geared)
    assert out.dscr.available is True  # gearing + credit rate/tenor ⇒ DSCR computes
    assert out.bankable is False  # covenant + policy still placeholder
