"""Time-to-energisation (COD) is reported on the quote (L16 · M6).

System Brief: "Time-to-energisation is a scenario axis — consenting timelines shift COD and the
quote reports it." The scenario engine already *applies* a COD delay to cashflows; this pins that
the delay is also *reported* — carried onto each `ScenarioSensitivity` and rendered in the
underwriting pack's sensitivity table.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy
from gridq.outputs import generate_artifacts
from gridq.quotes import assemble_quote_record
from gridq.quotes.sensitivities import compute_scenario_sensitivities
from gridq.scenarios import Scenario, ScenarioName, ScenarioSet

_DEBT = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
_DISPATCH = DispatchEnergy(annual_discharge_kwh=400_000.0)


def _cod_delayed_set() -> ScenarioSet:
    # A consenting-timeline scenario that shifts COD by a whole year.
    return ScenarioSet(
        scenario_set_id="cod",
        assumption_version="gb_v0_1",
        scenarios=[
            Scenario(
                scenario_id="delayed",
                name=ScenarioName.CENTRAL,
                probability_weight=1.0,
                cod_delay_months=12,
            )
        ],
    )


def test_compute_carries_the_cod_delay(rich_quote) -> None:
    rows = compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
        scenario_set=_cod_delayed_set(),
    )
    assert rows[0].cod_delay_months == 12


def test_default_scenarios_report_on_time(rich_quote) -> None:
    rows = compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
    )
    assert all(r.cod_delay_months == 0 for r in rows)  # standard set energises on time


def test_underwriting_pack_reports_the_cod_delay(rich_quote) -> None:
    rows = compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
        scenario_set=_cod_delayed_set(),
    )
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        scenario_sensitivities=rows,
    )
    memo = generate_artifacts(quote, rich_quote.site).memo_markdown
    assert "Time to energisation" in memo  # the column is present
    assert "+12 mo (COD delay)" in memo  # the delay is reported, not just applied


def test_on_time_is_labelled(rich_quote) -> None:
    rows = compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
    )
    quote = assemble_quote_record(
        rich_quote.site,
        rich_quote.result,
        rich_quote.case,
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        scenario_sensitivities=rows,
    )
    memo = generate_artifacts(quote, rich_quote.site).memo_markdown
    assert "on time" in memo
