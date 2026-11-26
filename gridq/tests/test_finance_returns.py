"""Tests for simple payback and the robust project-IRR calculation.

IRR is reported only when a unique, reliable root exists (conventional one-sign-change
cashflow); otherwise it is unavailable with a warning. Payback returns ``None`` when the
project never recovers its outlay.
"""

from __future__ import annotations

import pytest

from gridq.finance.debt import level_payment_schedule
from gridq.finance.returns import (
    EquityIrrResult,
    equity_irr,
    npv,
    project_irr,
    simple_payback,
)

# --- simple payback --------------------------------------------------------


def test_simple_payback_interpolated() -> None:
    # -1000 then +400/yr: cumulative crosses 0 partway through year 3 → 2.5 years.
    res = simple_payback([-1000.0, 400.0, 400.0, 400.0, 400.0])
    assert res.available is True
    assert res.years == pytest.approx(2.5)


def test_simple_payback_exact_year() -> None:
    res = simple_payback([-1000.0, 500.0, 500.0])
    assert res.available is True
    assert res.years == pytest.approx(2.0)


def test_simple_payback_never_recovers() -> None:
    res = simple_payback([-1000.0, -50.0, -50.0])
    assert res.available is False
    assert res.years is None
    assert any("does not pay back" in w.lower() for w in res.warnings)


def test_simple_payback_no_outlay_is_unavailable() -> None:
    res = simple_payback([0.0, 100.0, 100.0])
    assert res.available is False
    assert any("no upfront outlay" in w.lower() for w in res.warnings)


def test_simple_payback_needs_two_periods() -> None:
    assert simple_payback([-1000.0]).available is False


# --- npv -------------------------------------------------------------------


def test_npv_zero_rate_is_plain_sum() -> None:
    assert npv(0.0, [-1000.0, 500.0, 500.0]) == pytest.approx(0.0)


def test_npv_discounts_future_flows() -> None:
    # 100 next year at 10% ⇒ 90.909...
    assert npv(0.10, [0.0, 100.0]) == pytest.approx(90.9090909, abs=1e-6)


def test_npv_rejects_rate_at_or_below_minus_one() -> None:
    with pytest.raises(ValueError):
        npv(-1.0, [-100.0, 100.0])


# --- project IRR -----------------------------------------------------------


def test_project_irr_conventional_cashflow() -> None:
    cashflows = [-1000.0, 500.0, 500.0, 500.0]
    res = project_irr(cashflows)
    assert res.available is True
    assert res.irr_pct is not None
    # NPV at the computed IRR is ~0 (the defining property)
    assert npv(res.irr_pct / 100.0, cashflows) == pytest.approx(0.0, abs=1e-3)


def test_project_irr_matches_known_value() -> None:
    # -1000, +1100 next year ⇒ exactly 10%.
    res = project_irr([-1000.0, 1100.0])
    assert res.irr_pct == pytest.approx(10.0, abs=1e-3)


def test_project_irr_all_positive_unavailable() -> None:
    res = project_irr([100.0, 100.0, 100.0])
    assert res.available is False
    assert any("no outlay" in w.lower() or "no negative" in w.lower() for w in res.warnings)


def test_project_irr_all_negative_unavailable() -> None:
    res = project_irr([-100.0, -100.0, -100.0])
    assert res.available is False
    assert res.irr_pct is None


def test_project_irr_multiple_sign_changes_unavailable() -> None:
    # -, +, -, + → 3 sign changes ⇒ not a single trustworthy IRR.
    res = project_irr([-1000.0, 3000.0, -2500.0, 1000.0])
    assert res.available is False
    assert any("sign change" in w.lower() for w in res.warnings)


def test_project_irr_single_period_unavailable() -> None:
    assert project_irr([-1000.0]).available is False


# --- equity (levered) IRR --------------------------------------------------

# A conventional geared case: £250k capex, 70% gearing, a 10-year level loan, a flat 15-year P50.
_CAPEX = 250_000.0
_PRINCIPAL = round(_CAPEX * 0.70, 2)  # 175_000.0
_SERVICE = level_payment_schedule(_PRINCIPAL, annual_rate_pct=7.0, tenor_years=10)
_P50_FCF = [40_000.0] * 15


def test_equity_irr_conventional_is_available_and_solves() -> None:
    r = equity_irr(_CAPEX, _PRINCIPAL, _SERVICE, _P50_FCF)
    assert isinstance(r, EquityIrrResult)
    assert r.available is True
    assert r.irr_pct is not None
    # The reported rate is a genuine root: NPV of the equity series at that rate is ~0.
    assert npv(r.irr_pct / 100.0, list(r.equity_cashflows)) == pytest.approx(0.0, abs=1.0)


def test_equity_irr_exceeds_project_irr_when_leverage_accretive() -> None:
    # Same P50 series, unlevered: leverage amplifies the return when project IRR > debt rate.
    project = project_irr([-_CAPEX, *_P50_FCF])
    equity = equity_irr(_CAPEX, _PRINCIPAL, _SERVICE, _P50_FCF)
    assert project.available and equity.available
    assert project.irr_pct is not None and equity.irr_pct is not None
    assert equity.irr_pct > project.irr_pct


def test_equity_irr_year0_is_capex_minus_principal() -> None:
    r = equity_irr(_CAPEX, _PRINCIPAL, _SERVICE, _P50_FCF)
    assert r.equity_cashflows[0] == -round(_CAPEX - _PRINCIPAL, 2)  # -75_000.0


def test_equity_irr_runs_full_horizon_past_tenor() -> None:
    # Horizon 15, tenor 10 ⇒ 16 entries (year 0 + 15); post-tenor years carry no debt service.
    r = equity_irr(_CAPEX, _PRINCIPAL, _SERVICE, _P50_FCF)
    assert len(r.equity_cashflows) == 16
    assert r.equity_cashflows[11] == 40_000.0  # operating year 11 — beyond the 10-year tenor


def test_n11_post_horizon_debt_service_is_never_dropped_from_the_equity_series() -> None:
    """N11 (WP-0 review 2026-08-10). The review's worked example: capex £500k, 60% gearing,
    6.0%/12y, P50 operating FCF £60k/yr, `horizon_years=10`. The loop used to iterate the
    OPERATING list, so years 11 and 12 of debt service simply vanished — the equity investor
    kept the full year-0 draw and was never charged for the tail that repays it. Worse, dropping
    those late negatives made the series look conventional, so `project_irr`'s sign-change gate
    was bypassed and +3.6389% was published where the honest answer is "unavailable"."""
    principal = 300_000.0
    service = level_payment_schedule(principal, annual_rate_pct=6.0, tenor_years=12)
    operating = [60_000.0] * 10  # horizon 10 < tenor 12

    r = equity_irr(500_000.0, principal, service, operating)

    # The series runs to the LONGER of the two, and the tail is the negative flow it really is.
    assert len(r.equity_cashflows) == 13  # year 0 + 12 debt years
    assert r.equity_cashflows[11] == pytest.approx(-service[10])
    assert r.equity_cashflows[12] == pytest.approx(-service[11])
    # Two sign changes ⇒ the reliability gate refuses the metric instead of flattering it.
    assert r.available is False
    assert r.irr_pct is None
    assert any("sign change" in w.lower() for w in r.warnings)


def test_n11_horizon_at_least_the_tenor_is_unchanged() -> None:
    """The fix touches only the truncated case: the shipped path (horizon 15 > every tenor) keeps
    its exact series, so no live number moves."""
    r = equity_irr(_CAPEX, _PRINCIPAL, _SERVICE, _P50_FCF)
    assert len(r.equity_cashflows) == 16
    assert r.available is True


def test_equity_irr_unavailable_without_debt() -> None:
    # No principal at all (no debt terms) and explicit all-equity (0) both degrade honestly.
    for principal in (None, 0.0):
        r = equity_irr(_CAPEX, principal, [], _P50_FCF)
        assert r.available is False
        assert r.irr_pct is None
        assert any("no debt principal" in w.lower() for w in r.warnings)


def test_equity_irr_unavailable_with_empty_debt_service() -> None:
    r = equity_irr(_CAPEX, _PRINCIPAL, [], _P50_FCF)
    assert r.available is False
    assert r.irr_pct is None


def test_equity_irr_unavailable_on_non_positive_equity_outlay() -> None:
    # Principal == capex ⇒ zero equity outlay ⇒ no IRR to report (not a guess).
    r = equity_irr(_CAPEX, _CAPEX, _SERVICE, _P50_FCF)
    assert r.available is False
    assert any("equity outlay" in w.lower() for w in r.warnings)


def test_equity_irr_unavailable_on_non_conventional_series() -> None:
    # Heavy mid-loan service flips the equity series sign more than once ⇒ no unique IRR.
    # equity_cf = [-50_000, +80_000, -85_000, +100_000] → 3 sign changes.
    r = equity_irr(100_000.0, 50_000.0, [20_000.0, 95_000.0], [100_000.0, 10_000.0, 100_000.0])
    assert r.available is False
    assert r.irr_pct is None
    assert any("sign change" in w.lower() for w in r.warnings)
