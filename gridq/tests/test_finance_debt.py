"""Tests for debt sizing, the debt-service schedule, DSCR and the covenant check.

The headline rule: when the assumption library has no debt structure (as in v1's gb_v0_1),
DSCR is **unavailable with a warning — never guessed**. The schedule/DSCR maths is exercised
with explicitly-injected debt terms (a test input, not library data).
"""

from __future__ import annotations

import pytest

from gridq.assumptions import load_assumptions
from gridq.finance.debt import (
    DebtTerms,
    assess_debt,
    build_debt_terms,
    check_covenant,
    covenant_value,
    dscr_by_year,
    level_payment_schedule,
    size_and_measure_dscr,
)


def _lib():
    return load_assumptions("gb_v0_1")


# --- DSCR unavailable when debt assumptions are missing --------------------


def test_build_debt_terms_unavailable_in_v1() -> None:
    terms, warnings = build_debt_terms(_lib())
    assert terms is None
    assert any("DSCR unavailable" in w for w in warnings)
    # the warning names the missing keys, not a guessed value
    assert any("debt_gearing_pct" in w for w in warnings)


def test_assess_debt_unavailable_without_terms() -> None:
    dscr, covenant = assess_debt(250000.0, [50000.0] * 15, _lib())
    assert dscr.available is False
    assert dscr.min_dscr is None
    assert dscr.by_year == []
    # covenant value exists in the library, but it cannot be evaluated without a DSCR
    assert covenant.passes is None
    assert covenant.covenant == pytest.approx(1.30)


def test_covenant_value_reads_library() -> None:
    warnings: list[str] = []
    assert covenant_value(_lib(), warnings=warnings) == pytest.approx(1.30)
    # it is a placeholder ⇒ a warning is emitted
    assert any("placeholder" in w.lower() for w in warnings)


# --- the schedule + DSCR maths (with injected terms) -----------------------


def test_level_payment_schedule_zero_rate_is_straight_line() -> None:
    sched = level_payment_schedule(100000.0, 0.0, 5)
    assert sched == [20000.0] * 5


def test_level_payment_schedule_positive_rate_amortises_principal() -> None:
    principal, rate, tenor = 100000.0, 7.5, 8
    sched = level_payment_schedule(principal, rate, tenor)
    assert len(sched) == tenor
    assert all(p == sched[0] for p in sched)  # level payment
    # the discounted sum of payments equals the principal (annuity identity)
    i = rate / 100.0
    pv = sum(p / (1 + i) ** t for t, p in enumerate(sched, start=1))
    assert pv == pytest.approx(principal, abs=0.5)


def test_dscr_by_year_basic() -> None:
    assert dscr_by_year([130.0, 140.0], [100.0, 100.0]) == [1.3, 1.4]


def test_dscr_by_year_skips_zero_service_years() -> None:
    assert dscr_by_year([130.0, 140.0], [100.0, 0.0]) == [1.3]


def test_size_and_measure_dscr_known_inputs() -> None:
    # 50% gearing on £200k = £100k principal; zero-rate over 5 years ⇒ £20k/yr service.
    # CFADS of £30k/yr ⇒ DSCR 1.5 every year.
    terms = DebtTerms(gearing_pct=50.0, annual_rate_pct=0.0, tenor_years=5)
    res = size_and_measure_dscr(200000.0, [30000.0] * 10, terms)
    assert res.available is True
    assert res.principal_gbp == pytest.approx(100000.0)
    assert res.min_dscr == pytest.approx(1.5)
    assert len(res.by_year) == 5


def test_size_and_measure_dscr_zero_gearing_unavailable() -> None:
    terms = DebtTerms(gearing_pct=0.0, annual_rate_pct=7.5, tenor_years=8)
    res = size_and_measure_dscr(200000.0, [30000.0] * 10, terms)
    assert res.available is False
    assert any("all-equity" in w for w in res.warnings)


def test_n10_horizon_shorter_than_tenor_returns_unavailable_not_a_partial_minimum() -> None:
    """N10 (WP-0 review 2026-08-10). REBASELINED: this used to assert `len(by_year) == 3` — a min
    DSCR measured over 3 of the 8 debt years and returned `available=True`. P90 CFADS fades
    against flat costs, so the 5 dropped years are always the WORST ones: the covenant the
    project fails reports as passed, and `SizingObjective.feasible` (the hard gate between a
    sub-covenant spec and a recommendation) admits it. A partial window is not a minimum."""
    terms = DebtTerms(gearing_pct=50.0, annual_rate_pct=0.0, tenor_years=8)
    res = size_and_measure_dscr(200000.0, [30000.0] * 3, terms)  # only 3 operating years
    assert res.available is False
    assert res.min_dscr is None
    assert not res.by_year
    assert any("shorter than the debt tenor" in w for w in res.warnings)
    # The schedule and principal still travel, so the equity/covenant callers can say WHY.
    assert res.principal_gbp == 100000.0
    assert len(res.debt_service_by_year_gbp) == 8


def test_n10_the_dropped_years_are_the_binding_ones() -> None:
    """The measurement the truncation hid: on a fading CFADS series the min over the full tenor
    fails a covenant the truncated window passes."""
    terms = DebtTerms(gearing_pct=60.0, annual_rate_pct=6.0, tenor_years=12)
    full = [60000.0 * (0.975**y) for y in range(12)]
    truncated = size_and_measure_dscr(500000.0, full[:10], terms)
    complete = size_and_measure_dscr(500000.0, full, terms)
    assert truncated.available is False  # N10: refused rather than flattered
    assert complete.available is True
    assert complete.min_dscr is not None
    # The years the old window dropped are exactly the ones that break the 1.30 covenant.
    assert complete.min_dscr < 1.30
    assert min(dscr_by_year(full[:10], complete.debt_service_by_year_gbp)) >= 1.30


# --- covenant verdict ------------------------------------------------------


def test_check_covenant_pass() -> None:
    assert check_covenant(1.45, 1.30).passes is True


def test_check_covenant_fail() -> None:
    assert check_covenant(1.10, 1.30).passes is False


def test_check_covenant_unavailable_when_dscr_none() -> None:
    out = check_covenant(None, 1.30)
    assert out.passes is None
    assert any("not evaluated" in w.lower() for w in out.warnings)


# --- DebtTerms validation --------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gearing_pct": -1.0, "annual_rate_pct": 7.5, "tenor_years": 8},
        {"gearing_pct": 101.0, "annual_rate_pct": 7.5, "tenor_years": 8},
        {"gearing_pct": 60.0, "annual_rate_pct": -0.1, "tenor_years": 8},
        {"gearing_pct": 60.0, "annual_rate_pct": 7.5, "tenor_years": 0},
    ],
)
def test_debt_terms_validation_rejects_bad_inputs(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        DebtTerms(**kwargs)


def test_assess_debt_with_injected_terms_is_available() -> None:
    terms = DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=8)
    dscr, covenant = assess_debt(250000.0, [60000.0] * 15, _lib(), debt_terms=terms)
    assert dscr.available is True
    assert dscr.min_dscr is not None
    assert covenant.covenant == pytest.approx(1.30)
    assert covenant.passes == (dscr.min_dscr >= 1.30)
