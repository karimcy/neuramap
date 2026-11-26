"""D51 (revised): a negative DSCR is reported as the measured coverage failure it is, and the record
says the cause is absent cash rather than a mis-sized loan.

HISTORY, because the first attempt at this was wrong and the wrong version is instructive.
On 2026-08-17 a guard was added that REFUSED the DSCR outright when P90 CFADS was non-positive in
every measured year, on the reasoning that "a coverage ratio on cash that does not exist is not a
covenant measurement". The full suite refuted it.
`test_covenant_gate_rejects_on_zero_revenue_with_debt` states in its own docstring that zero revenue
plus injected debt SHOULD give a negative DSCR and reject every candidate, and
`test_a_measured_covenant_failure_is_never_relabelled_as_a_financing_absence` exists precisely to
stop a measured economic failure being re-badged as a data absence - a defect this codebase had
already fixed once.

The correct reading: a project whose CFADS is negative genuinely cannot service its debt. That IS a
measured coverage failure and must report as one. The narrower hazard the guard was aiming at - a
reader taking "the covenant rejected every candidate" as evidence the LOAN was mis-sized, when no
gearing could have passed - is a job for a WARNING, which is what these tests pin.
"""

from __future__ import annotations

from gridq.finance.debt import DebtTerms, check_covenant, dscr_by_year, size_and_measure_dscr

_CAPEX = 100_000.0
_TERMS = DebtTerms(gearing_pct=60.0, annual_rate_pct=7.5, tenor_years=10)
_COVENANT = 1.3


def test_all_negative_cfads_still_reports_a_measured_covenant_failure() -> None:
    """The invariant the first version broke: an economic failure is never a data absence."""
    result = size_and_measure_dscr(_CAPEX, [-1_799.35] * 10, _TERMS)

    assert result.available is True
    assert result.min_dscr is not None and result.min_dscr < 0
    assert result.principal_gbp == 60_000.0
    # The covenant verdict is a real FALSE, not an unevaluated None.
    assert check_covenant(result.min_dscr, _COVENANT).passes is False


def test_the_record_says_absent_cash_not_a_mis_sized_loan() -> None:
    """The honesty the guard was actually reaching for, delivered as a warning."""
    joined = " ".join(size_and_measure_dscr(_CAPEX, [-1_799.35] * 10, _TERMS).warnings)

    assert "EVERY one of the 10 measured year(s)" in joined
    assert "AT ANY GEARING" in joined
    # The load-bearing clause: a reader must not conclude a different structure would pass.
    assert "do NOT read it as evidence the loan is mis-sized" in joined
    assert "re-gearing cannot create cash" in joined


def test_a_partly_negative_series_gets_the_milder_wording() -> None:
    """Two different findings must not share one sentence."""
    joined = " ".join(size_and_measure_dscr(_CAPEX, [20_000.0] * 9 + [-5_000.0], _TERMS).warnings)

    assert "negative in 1 of 10 measured year(s)" in joined
    assert "not because the loan is mis-sized" in joined
    # The all-years wording must NOT appear on a mixed series.
    assert "AT ANY GEARING" not in joined


def test_a_genuine_coverage_failure_still_reports_as_one() -> None:
    """Mixed cashflows keep their verdict: one unservicable year is a real breach."""
    result = size_and_measure_dscr(_CAPEX, [20_000.0] * 9 + [-5_000.0], _TERMS)

    assert result.available is True
    assert result.min_dscr is not None and result.min_dscr < 0
    assert check_covenant(result.min_dscr, _COVENANT).passes is False


def test_a_healthy_project_is_untouched_and_carries_no_cash_warning() -> None:
    """No regression, and no scary wording where there is nothing to warn about."""
    result = size_and_measure_dscr(_CAPEX, [20_000.0] * 10, _TERMS)

    assert result.available is True
    assert result.min_dscr is not None and result.min_dscr > _COVENANT
    assert check_covenant(result.min_dscr, _COVENANT).passes is True
    joined = " ".join(result.warnings)
    assert "AT ANY GEARING" not in joined
    assert "DSCR is negative in" not in joined


def test_zero_cash_counts_as_non_positive() -> None:
    """A flat zero covers nothing, so it takes the all-years wording too."""
    joined = " ".join(size_and_measure_dscr(_CAPEX, [0.0] * 10, _TERMS).warnings)

    assert "EVERY one of the 10 measured year(s)" in joined


def test_dscr_by_year_remains_arithmetic_only() -> None:
    """The helper stays a pure ratio; the judgement lives in the caller, where it can be tested."""
    raw = dscr_by_year([-1_799.35] * 10, [8_000.0] * 10)

    assert raw and all(v < 0 for v in raw)
