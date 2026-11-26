"""D47/D50: a return metric is ALWAYS defined, even where the project IRR does not exist.

The defect this pins (Olly, 2026-08-17): on every site in the 2026-08 campaign every priced
candidate recorded ``project_irr_pct = null``, because ``finance.returns.project_irr`` correctly
refuses a series whose every cashflow is non-positive (capital is never returned, so no discount
rate sets NPV to zero). A null is the wrong output for that: it reads to a client as "no value was
found" when the truth is "the value was measured and it falls GBP N short". ``capital_recovery``
is the always-defined account that carries the verdict instead.

These tests assert on the FIGURES, not on prose, except where the prose is the deliverable (the
narrative that reaches a pack must never carry an em/en dash, the house style being zero).
"""

from __future__ import annotations

import re

from gridq.finance.returns import capital_recovery, project_irr

# Bishops Cleeve Ash Plant, the measured case (runs/quote_run_223a0b29daae98ab, gb_v1_10):
# best modelled annual revenue GBP 1,463.15; annual cost GBP 2,700 size-invariant
# (metering 1,200 + DNO 1,500) + GBP 22.50/kW; a 25 kW unit at GBP 500,000/MW is a
# GBP 12,500 outlay.
_ASH_PLANT_ANNUAL_NET = 1463.15 - (2700.0 + 22.50 * 25.0)
_ASH_PLANT_OUTLAY = 12_500.0
_HORIZON_YEARS = 15


def _ash_plant_series() -> list[float]:
    return [-_ASH_PLANT_OUTLAY] + [_ASH_PLANT_ANNUAL_NET] * _HORIZON_YEARS


def test_irr_is_genuinely_unavailable_on_the_measured_series() -> None:
    """The premise. If this ever passes an IRR, the rest of the file is testing the wrong thing."""
    result = project_irr(_ash_plant_series())
    assert result.available is False
    assert result.irr_pct is None


def test_capital_recovery_is_defined_where_the_irr_is_not() -> None:
    """The fix: every field carries a number on exactly the series the IRR refuses."""
    recovery = capital_recovery(_ash_plant_series())

    assert recovery.status == "capital_never_returned"
    assert recovery.every_operating_year_non_positive is True
    # -1,799.35/yr: revenue 1,463.15 against 3,262.50 of cost.
    assert recovery.annual_net_margin_gbp == -1799.35
    assert recovery.operating_cashflow_gbp == -26_990.25
    assert recovery.capital_outlay_gbp == _ASH_PLANT_OUTLAY
    # The number that replaces the null: outlay + the cash the asset also burns.
    assert recovery.capital_recovery_gap_gbp == 39_490.25
    assert recovery.capital_recovery_pct == -215.92
    assert recovery.lifetime_net_cashflow_gbp == -39_490.25

    # No field may be None here: a null is the defect being fixed.
    for field in (
        recovery.annual_net_margin_gbp,
        recovery.operating_cashflow_gbp,
        recovery.capital_outlay_gbp,
        recovery.lifetime_net_cashflow_gbp,
        recovery.capital_recovery_gap_gbp,
        recovery.capital_recovery_pct,
    ):
        assert field is not None


def test_narrative_states_the_measured_position_and_is_pack_safe() -> None:
    """The narrative reaches rendered packs, so it carries the numbers and no em/en dash."""
    narrative = capital_recovery(_ash_plant_series()).narrative

    assert "39,490.25" in narrative  # the shortfall is stated, not implied
    assert "12,500.00" in narrative  # against the outlay it is measured on
    # It must say WHY there is no IRR, so the absence never reads as "unmeasured".
    assert "CAPITAL IS NEVER RETURNED" in narrative
    assert "measured and does not pay" in narrative
    assert re.search(r"[—–]", narrative) is None, "house style: zero em/en dashes in packs"


def test_a_paying_project_reports_recovery_and_an_irr() -> None:
    """Direction of effect: better cashflows must move the status UP, not just change wording."""
    series = [-100_000.0] + [14_000.0] * _HORIZON_YEARS

    recovery = capital_recovery(series)
    assert recovery.status == "capital_returned"
    assert recovery.capital_recovery_gap_gbp < 0  # negative gap == a surplus
    assert recovery.capital_recovery_pct == 210.0
    assert recovery.every_operating_year_non_positive is False

    # And here an IRR genuinely exists, so the two metrics agree rather than compete.
    irr = project_irr(series)
    assert irr.available is True
    assert irr.irr_pct is not None and irr.irr_pct > 0


def test_partial_recovery_is_distinguished_from_never_returning() -> None:
    """Three different negatives must not collapse into one label."""
    # Pays something every year, but never enough to return the outlay.
    partial = capital_recovery([-100_000.0] + [2_000.0] * _HORIZON_YEARS)
    assert partial.status == "partial_capital_recovery"
    assert partial.every_operating_year_non_positive is False
    assert partial.capital_recovery_gap_gbp == 70_000.0
    assert "PARTLY recovered" in partial.narrative

    # Loses money every year: a different finding, and it must say so.
    never = capital_recovery([-100_000.0] + [-2_000.0] * _HORIZON_YEARS)
    assert never.status == "capital_never_returned"
    assert never.every_operating_year_non_positive is True


def test_degenerate_series_still_return_a_defined_account() -> None:
    """D47 admits no null anywhere, including the shapes that are nobody's real project."""
    too_short = capital_recovery([-100.0])
    assert too_short.status == "insufficient_series"
    assert too_short.narrative  # still explains itself

    no_outlay = capital_recovery([0.0, 500.0, 500.0])
    assert no_outlay.status == "no_capital_outlay"
    assert no_outlay.capital_outlay_gbp == 0.0
    assert no_outlay.capital_recovery_pct is None  # the one legitimate None: nothing to recover
    assert no_outlay.operating_cashflow_gbp == 1_000.0
