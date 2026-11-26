"""Underwriting pack carries scenario DSCR/return sensitivities (L15 · M6 → M8).

Output 2 of the System Brief lists "DSCR sensitivities". The pipeline now re-runs the financial
model under each Low/Central/High scenario, persists the rows on the record
(`QuoteRecord.scenario_sensitivities`), and the underwriting memo renders them from the record (so
the figures trace to it). The Low/High spread is flat until real price bands land (human item H04)
— the mechanism is honest, never a fabricated spread.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy
from gridq.outputs import build_view_model, generate_artifacts
from gridq.quotes import assemble_quote_record
from gridq.quotes.sensitivities import compute_scenario_sensitivities

_DEBT = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
_DISPATCH = DispatchEnergy(annual_discharge_kwh=400_000.0)


def _sensitivities(rich_quote):
    return compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
    )


def test_compute_returns_low_central_high(rich_quote) -> None:
    rows = _sensitivities(rich_quote)
    assert [r.label for r in rows] == ["Low", "Central", "High"]
    # Each row is the financial model under that scenario — DSCR present here (debt injected).
    assert all(r.min_dscr is not None for r in rows)


def test_computation_is_deterministic(rich_quote) -> None:
    assert _sensitivities(rich_quote) == _sensitivities(rich_quote)


def test_sensitivities_persist_on_record(rich_quote) -> None:
    rows = _sensitivities(rich_quote)
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        scenario_sensitivities=rows,
    )
    assert quote.scenario_sensitivities == rows
    # Persisting is additive: it does not change the stamped identity (excluded from input_hash).
    bare = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
    )
    assert quote.input_hash == bare.input_hash and quote.quote_id == bare.quote_id


def test_underwriting_pack_renders_sensitivities_from_the_record(rich_quote) -> None:
    rows = _sensitivities(rich_quote)
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        scenario_sensitivities=rows,
    )
    vm = build_view_model(quote, rich_quote.site)
    assert [r.label for r in vm.sensitivities] == ["Low", "Central", "High"]

    memo = generate_artifacts(quote, rich_quote.site).memo_markdown
    assert "Sensitivities" in memo
    assert "not attached to this run" not in memo  # the pending placeholder is gone
    for row in rows:
        assert row.label in memo


def test_without_sensitivities_the_pack_shows_the_pending_note(rich_quote) -> None:
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
    )
    arts = generate_artifacts(quote, rich_quote.site)
    assert "not attached to this run" in arts.memo_markdown
    # The on-screen HTML report (what the web app embeds) shows the standing pending note.
    assert "activate" in arts.report_html


def test_html_report_renders_sensitivities_when_attached(rich_quote) -> None:
    # The HTML report (the artifact the web app embeds) must show the same sensitivities the memo
    # does — not the stale "activate when run" placeholder — once they are attached.
    rows = _sensitivities(rich_quote)
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        scenario_sensitivities=rows,
    )
    report = generate_artifacts(quote, rich_quote.site).report_html
    assert "Scenario sensitivities" in report
    assert "Time to energisation" in report
    assert "activate when" not in report  # the stale placeholder is gone
    for row in rows:
        assert row.label in report
