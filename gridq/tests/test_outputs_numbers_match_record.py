"""M8 outputs — every figure equals its run-record value, and regeneration is deterministic.

Issues 011/012 require that no number is authored by the generator: each displayed figure must
equal the corresponding value on the record, the contracted/merchant totals must be exactly the
sum of the record's per-stream revenues (an aggregation, not a new figure), and regenerating from
the same record must be byte-identical.
"""

from __future__ import annotations

import gridq.outputs.formatting as fmt
from gridq.outputs import build_view_model, generate_artifacts


def test_floor_and_upside_totals_are_sums_of_record_values(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    result = rich_quote.result

    # Floor total == sum of the record's firm + contracted stream revenues.
    floor_from_record = round(
        sum(
            v.annual_revenue_gbp
            for v in result.value_streams
            if v.firmness.value in {"firm", "contracted"}
        ),
        2,
    )
    assert vm.split.floor_total_gbp == floor_from_record == rich_quote.floor_total_gbp

    # Upside total == sum of conditional value streams + every gated_upside entry.
    upside_from_record = round(
        sum(v.annual_revenue_gbp for v in result.value_streams if v.firmness.value == "conditional")
        + sum(v.annual_revenue_gbp for v in result.gated_upside),
        2,
    )
    assert vm.split.upside_total_gbp == upside_from_record


def test_memo_figures_match_record(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    memo, html = arts.memo_markdown, arts.report_html
    case = rich_quote.case

    # Capex (year-0 cashflow) and the floor total appear verbatim (record-derived).
    assert fmt.gbp(case.p50_cashflows[0]) in memo
    assert fmt.gbp(vm.split.floor_total_gbp) in memo
    assert fmt.gbp(vm.split.floor_total_gbp) in html

    # Every firm/contracted stream's own revenue is shown.
    for r in vm.split.floor:
        assert fmt.gbp(r.annual_revenue_gbp) in memo
        assert fmt.gbp(r.annual_revenue_gbp) in html

    # DSCR, price and IRR — shown exactly as the record carries them (when present).
    if case.dscr_summary.min_dscr is not None:
        assert fmt.ratio(case.dscr_summary.min_dscr) in memo
    if case.price_recommendation.value_gbp_per_kwh is not None:
        assert fmt.gbp_per_kwh(case.price_recommendation.value_gbp_per_kwh) in memo
    if case.irr is not None:
        assert fmt.pct(case.irr) in memo


def test_every_cashflow_year_rendered_from_record(rich_quote) -> None:
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    for value in rich_quote.case.p50_cashflows:
        assert fmt.gbp(value) in arts.memo_markdown
    for value in rich_quote.case.p90_cashflows:
        assert fmt.gbp(value) in arts.memo_markdown


def test_regeneration_is_byte_identical(rich_quote) -> None:
    a = generate_artifacts(rich_quote.quote, rich_quote.site)
    b = generate_artifacts(rich_quote.quote, rich_quote.site)
    assert a.memo_markdown == b.memo_markdown
    assert a.proposal_markdown == b.proposal_markdown
    assert a.report_html == b.report_html


def test_no_record_figure_leaks_into_prose(rich_quote) -> None:
    """The prose drafter only sees categorical facts, so no record £-figure can appear in it."""
    from gridq.outputs.prose import default_prose_drafter as d

    prose_blocks = " ".join(
        [
            d.banner_line(bankable=False),
            d.banner_line(bankable=True),
            d.memo_summary(
                bankable=False,
                quote_mode="indicative",
                data_tier="T2_VALIDATED_12_MONTH_HALF_HOURLY",
                has_warnings=True,
            ),
            d.firmness_explainer(),
            d.proposal_intro(bankable=False, quote_mode="indicative", has_priced_floor=True),
            d.next_steps(bankable=False, site_supplied=True),
        ]
    )
    # Distinctive record figures must not appear anywhere in the prose.
    for figure in ("59,000", "38,000", "250,000", "0.1536"):
        assert figure not in prose_blocks
