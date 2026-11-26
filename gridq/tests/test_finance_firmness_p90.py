"""Tests for firmness-differentiated P90 + the penalty cost line (W8, north_star W3).

The lender risk shapes: FIRM dispatch revenue keeps the merchant haircut; CONTRACTED
availability revenue gets its own haircut key when a release carries it (else it shares the
merchant one, loudly warned); CM non-delivery enters the P90 downside only once the D30
'penalties' section is populated — an empty section is a warned DATA-ASK, never zero. On today's
releases (no new keys) the numbers are IDENTITY-equal to the pre-slice formula.

N2 (2026-08-10 review) changed the penalty's SIZE, not its placement: it is the annual cap scaled
by the same delivery shortfall the contracted haircut applies (one event, one shortfall), with the
combined deduction bounded at the contracted revenue. The pin below is rebaselined accordingly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance.cashflows import build_cashflow_model


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_firmness",
        request_id="req_firmness",
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
            (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 10_000.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, 9_000.0, s.Firmness.CONTRACTED),
        ]
    )


def _assumption(value: float) -> dict:
    return {
        "value": value,
        "unit": "pct",
        "source": "test",
        "status": "placeholder",
        "owner_or_reviewer": "test",
        "confidence": "low",
        "last_reviewed": str(date(2026, 7, 2)),
        "notes": "test-only",
    }


def test_current_releases_share_the_merchant_haircut_with_warnings(
    make_site: Callable[..., s.SiteModel],
) -> None:
    model = build_cashflow_model(make_site(), _mixed(), load_assumptions("gb_v0_1"))
    assert model.annual_revenue_firm_gbp == 50_000.0
    assert model.annual_revenue_contracted_gbp == 9_000.0
    assert model.p90_penalty_exposure_gbp is None
    assert any("shares the merchant P90 haircut" in w for w in model.warnings)
    assert any("penalties' assumption section is empty" in w for w in model.warnings)
    # Identity with the undifferentiated formula: same haircut on both classes, no penalty.
    lib = load_assumptions("gb_v0_1")
    hc = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    adjusted = 59_000.0 - model.perfect_foresight_adjustment_gbp
    expected = round(
        adjusted * (1 - hc / 100.0)
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_ebitda_gbp[0] == pytest.approx(expected, abs=0.02)


def test_contracted_key_differentiates_the_p90(make_site: Callable[..., s.SiteModel]) -> None:
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["p50_p90_haircuts"]["p90_revenue_haircut_contracted_pct"] = _assumption(5.0)
    from gridq.assumptions import AssumptionLibrary

    lib = AssumptionLibrary.model_validate(base)
    model = build_cashflow_model(make_site(), _mixed(), lib)
    hc = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    firm_base = 50_000.0 - model.perfect_foresight_adjustment_gbp
    expected = round(
        firm_base * (1 - hc / 100.0)
        + 9_000.0 * (1 - 5.0 / 100.0)
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_ebitda_gbp[0] == pytest.approx(expected, abs=0.02)
    assert "p50_p90_haircuts.p90_revenue_haircut_contracted_pct" in model.assumptions_used
    assert not any("shares the merchant P90 haircut" in w for w in model.warnings)


def test_populated_penalties_section_enters_the_p90_downside_only(
    make_site: Callable[..., s.SiteModel],
) -> None:
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["penalties"] = {"cm_annual_cap_pct_of_revenue": _assumption(100.0)}
    from gridq.assumptions import AssumptionLibrary

    lib = AssumptionLibrary.model_validate(base)
    model = build_cashflow_model(make_site(), _mixed(), lib)
    # REBASELINED for N2 (2026-08-10 review): was £9,000 — the FULL 100% annual cap, deducted on
    # top of the P90 haircut already taken on the same CM revenue. Two deductions, one event. The
    # penalty is now the cap scaled by the SAME 15% delivery shortfall the contracted haircut
    # applies (100% × £9,000 × 0.15), and the combined deduction is bounded at the CM revenue.
    hc = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    penalty = 9_000.0 * (hc / 100.0)
    assert model.p90_penalty_exposure_gbp == pytest.approx(penalty)
    assert "penalties.cm_annual_cap_pct_of_revenue" in model.assumptions_used
    assert any("CM non-delivery penalty line" in w for w in model.warnings)
    # P50 is unpenalised expected operation; the line hits the P90 series only.
    adjusted = 59_000.0 - model.perfect_foresight_adjustment_gbp
    expected_p90 = round(
        adjusted * (1 - hc / 100.0)
        - penalty
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_ebitda_gbp[0] == pytest.approx(expected_p90, abs=0.02)
    expected_p50 = round(
        adjusted - model.annual_dispatch_cost_gbp - model.total_annual_operating_cost_gbp, 2
    )
    assert model.p50_ebitda_gbp[0] == pytest.approx(expected_p50, abs=0.02)


def test_no_contracted_revenue_means_no_penalty_machinery(
    make_site: Callable[..., s.SiteModel],
) -> None:
    firm_only = _result([(s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM)])
    model = build_cashflow_model(make_site(), firm_only, load_assumptions("gb_v0_1"))
    assert model.annual_revenue_contracted_gbp == 0.0
    assert model.p90_penalty_exposure_gbp is None
    assert not any("penalties' assumption section is empty" in w for w in model.warnings)
    assert not any("shares the merchant P90 haircut" in w for w in model.warnings)


def test_firmness_split_sums_to_the_stream_total(make_site: Callable[..., s.SiteModel]) -> None:
    model = build_cashflow_model(make_site(), _mixed(), load_assumptions("gb_v0_1"))
    assert model.annual_revenue_firm_gbp + model.annual_revenue_contracted_gbp == pytest.approx(
        model.total_annual_revenue_gbp
    )


def _with_consented_bm() -> s.OptimisationResult:
    """A mixed result whose CONTRACTED balancing-mechanism stream was consented into the floor.

    Mirrors the Revenue-Data Drive 2b placement: a consented gated stream (never FIRM) sits in
    ``value_streams`` as CONTRACTED, carrying its "consent given" basis. Finance must price it into
    the contracted class (with the contracted haircut + contracted-basis DSCR), not the firm one.
    """
    return s.OptimisationResult(
        result_id="res_consent",
        request_id="req_consent",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=40_000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.BALANCING_MECHANISM,
                annual_revenue_gbp=6_000.0,
                firmness=s.Firmness.CONTRACTED,
                consent_status="given",
            ),
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


def test_consented_contracted_gated_stream_takes_the_contracted_haircut(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A consented CONTRACTED balancing-mechanism stream is priced with the contracted haircut,
    distinct from the merchant haircut on firm dispatch revenue — and moves the P90 downward."""
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["p50_p90_haircuts"]["p90_revenue_haircut_contracted_pct"] = _assumption(5.0)
    from gridq.assumptions import AssumptionLibrary

    lib = AssumptionLibrary.model_validate(base)
    model = build_cashflow_model(make_site(), _with_consented_bm(), lib)
    # The consented BM revenue lands in the CONTRACTED class, not FIRM.
    assert model.annual_revenue_firm_gbp == 40_000.0
    assert model.annual_revenue_contracted_gbp == 6_000.0
    hc = float(lib.get("p50_p90_haircuts", "p90_revenue_haircut_pct").value)
    firm_base = 40_000.0 - model.perfect_foresight_adjustment_gbp
    expected = round(
        firm_base * (1 - hc / 100.0)
        + 6_000.0 * (1 - 5.0 / 100.0)
        - model.annual_dispatch_cost_gbp
        - model.total_annual_operating_cost_gbp,
        2,
    )
    assert model.p90_ebitda_gbp[0] == pytest.approx(expected, abs=0.02)
    assert "p50_p90_haircuts.p90_revenue_haircut_contracted_pct" in model.assumptions_used
    # The contracted haircut (5%) is gentler than the merchant one, so the consented BM raises the
    # P90 vs treating it as unpriced upside (a firm-only floor).
    firm_only = _result([(s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM)])
    floor_model = build_cashflow_model(make_site(), firm_only, lib)
    assert model.p90_ebitda_gbp[0] > floor_model.p90_ebitda_gbp[0]
