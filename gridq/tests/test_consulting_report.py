"""The consulting report generator: it formats record values and never computes one.

These pin the properties that make the deliverable trustworthy rather than merely long:

* the generator performs **no arithmetic** on record values (asserted structurally, over the
  module's AST — the same technique that guards `html_report`);
* every figure it does render is the view model's value, formatted;
* an unavailable figure degrades to an explicit label **with a stated reason**, never a blank or
  a zero — *including* a total the view model aggregates to ``0.0`` over an empty list;
* the NOT-BANKABLE banner appears whenever the record says so, and appears before any section;
* a solver status that is not a real solve gets its own banner: the reader must not be able to
  read a mock/baseline/infeasible run as a solved one;
* the caveat ladder prints **every** condition that fires, so an earlier caveat never suppresses
  a later one (a covenant failure used to vanish behind a not-bankable stamp);
* no exhibit title asserts more than the record supports (nothing is "recommended" or "solved"
  when the dispatch was not solved);
* the confidence band and the factors that capped it render prominently;
* the same record renders byte-identically twice.
"""

from __future__ import annotations

import ast
import re
from html import escape
from pathlib import Path

import gridq.outputs.consulting_report as report_mod
import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.ingestion.improvement_asks import build_ranked_data_asks
from gridq.outputs import formatting as fmt
from gridq.outputs.consulting_report import (
    ConfidenceView,
    render_consulting_report,
    render_report_for_quote,
)
from gridq.outputs.viewmodel import build_view_model
from gridq.quotes.confidence import assess_confidence

_ARITH_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.MatMult,
)
#: Aggregations that would author a new figure out of record values.
_FORBIDDEN_CALLS = {"sum", "round", "min", "max", "abs", "pow", "divmod"}


def _render(rq, **kwargs) -> str:
    return render_report_for_quote(
        rq.quote, rq.site, assumptions=load_assumptions("gb_v0_1"), **kwargs
    )


def _vm(rq):
    return build_view_model(rq.quote, rq.site, assumptions=load_assumptions("gb_v0_1"))


# --- the central invariant ------------------------------------------------------------------


def test_generator_performs_no_arithmetic_on_record_values() -> None:
    """No arithmetic operator, no augmented assignment, no aggregation call in the module.

    Every figure must arrive already computed on the run record; this module may only format it.
    Value scaling (e.g. £k headlines) lives in `gridq.outputs.formatting`, which is exercised by
    tests/test_outputs_no_arithmetic_in_html.py.
    """
    tree = ast.parse(Path(report_mod.__file__).read_text(encoding="utf-8"))

    binops = [
        ast.dump(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp) and isinstance(n.op, _ARITH_OPS)
    ]
    assert not binops, f"arithmetic found in consulting_report.py: {binops}"

    augs = [
        n for n in ast.walk(tree) if isinstance(n, ast.AugAssign) and isinstance(n.op, _ARITH_OPS)
    ]
    assert not augs, "augmented arithmetic assignment found in consulting_report.py"

    calls = [
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in _FORBIDDEN_CALLS
    ]
    assert not calls, f"aggregation call(s) found in consulting_report.py: {calls}"


def test_every_headline_figure_is_the_view_model_value_formatted(rich_quote) -> None:
    """The numbers on the page are the record's, formatted — not adjusted, not rounded again."""
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert fmt.gbp(vm.split.floor_total_gbp) in html
    assert fmt.gbp(vm.split.upside_total_gbp) in html
    assert vm.returns.irr_pct is not None and fmt.pct(vm.returns.irr_pct) in html
    assert vm.returns.payback_years is not None and fmt.years(vm.returns.payback_years) in html
    assert vm.dscr.min_dscr is not None and fmt.ratio(vm.dscr.min_dscr) in html
    assert vm.cashflows.year0_p50 is not None and fmt.gbp(vm.cashflows.year0_p50) in html
    for row in vm.split.floor:
        assert fmt.gbp(row.annual_revenue_gbp) in html
    # the floor total is the record's own total, not a re-derived one
    assert fmt.gbp(rich_quote.floor_total_gbp) in html


# --- honest degradation ---------------------------------------------------------------------


def test_unavailable_figure_states_a_reason(rich_quote) -> None:
    """The fixture carries no customer bill; the saving must say so, and say why."""
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert not vm.bill_savings.available
    assert "unavailable" in html
    # the reason travels with the label, and it is the record layer's own stated reason
    assert escape(vm.bill_savings.pending_reason) in html
    assert escape(fmt.unavailable(vm.bill_savings.pending_reason)) in html


def test_no_figure_renders_blank(rich_quote) -> None:
    """A missing figure is never an empty cell — that would read as a zero."""
    html = _render(rich_quote)
    assert "<td></td>" not in html
    assert "<td> </td>" not in html


def test_a_total_with_no_streams_behind_it_is_unavailable_not_zero(rich_quote) -> None:
    """The view model sums an empty stream list to ``0.0``; ``£0.00`` reads as a priced nothing.

    A record with no optimisation result has no floor — that is an absence, and the pack has to
    say so with its reason rather than print a total nobody computed.
    """
    quote = rich_quote.quote.model_copy(update={"optimisation_result": None})
    vm = build_view_model(quote, rich_quote.site)
    html = render_consulting_report(vm, site=rich_quote.site)

    assert vm.split.floor_total_gbp == 0.0  # the view model's own aggregate over nothing
    assert ">£0.00<" not in html  # no cell anywhere renders the phantom total
    reason = "the run record carries no firm or contracted stream, so there is no floor to total"
    assert escape(fmt.unavailable(reason)) in html
    assert "no firm or contracted stream is priced on this record" in html


def test_an_unstamped_half_hourly_basis_is_unavailable_not_the_words_not_recorded(
    rich_quote,
) -> None:
    """Unknown is not modelled.

    The basis row used to print ``NOT RECORDED`` as if it were the value, under a note asserting
    the shape was a typical-site reference profile — a claim about data nobody had made.
    """
    quote = rich_quote.quote.model_copy(update={"half_hourly_basis": None})
    html = render_report_for_quote(quote, rich_quote.site)

    assert "NOT RECORDED" not in html
    assert escape(fmt.unavailable(report_mod._NO_BASIS)) in html
    assert "Unknown is not the same as modelled" in html
    assert "Evidence about a typical site of this type" not in html


def test_load_basis_comes_from_the_records_gate_2_stamp(rich_quote) -> None:
    """The record stamps the basis; the report must state it rather than say "not recorded".

    It states it as a *stamp*, though. "Every number here rests on the site's own metered
    half-hourly load" was more than the record says — the stamp is a validation verdict about how
    the series was obtained, not evidence that any number was computed from it (on this record,
    the solver never ran).
    """
    assert rich_quote.quote.half_hourly_basis is not None
    html = _render(rich_quote)
    assert "METERED" in html
    assert (
        "The record stamps the load shape as the site&#x27;s own metered half-hourly data" in html
    )
    assert "A validation stamp, not the series itself" in html
    assert "Every number here rests on" not in html
    assert "basis is NOT RECORDED" not in html


def test_missing_site_degrades_with_a_reason_rather_than_omitting_the_situation(
    rich_quote,
) -> None:
    vm = build_view_model(rich_quote.quote)
    html = render_consulting_report(vm)
    assert "5 · Situation" in html
    reason = "no site model was supplied alongside the run record"
    assert escape(fmt.unavailable(reason)) in html


# --- the banner -----------------------------------------------------------------------------


def test_not_bankable_banner_appears_when_the_record_says_so(rich_quote) -> None:
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert not vm.bankable
    assert "NOT BANKABLE" in html
    assert "BANKABLE CANDIDATE" not in html
    assert fmt.titleise(vm.bankable_status) in html


def test_not_bankable_banner_is_never_buried(rich_quote) -> None:
    """It sits above the results — the first thing after the title block."""
    html = _render(rich_quote)
    assert html.index("NOT BANKABLE") < html.index("1 · Results: IRR and revenue")


# --- the solve behind the numbers -------------------------------------------------------------


def test_a_mock_solve_is_banner_level_not_a_table_cell(rich_quote) -> None:
    """The fixture ran on the integration-only mock engine.

    A reader must not be able to take any figure here for a dispatch outcome, so the status gets
    its own banner above the summary — not one middle row of a Complication table.
    """
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert vm.spec.solver_status == "mock"
    assert "DISPATCH NOT SOLVED" in html
    assert html.index("DISPATCH NOT SOLVED") < html.index("1 · Results: IRR and revenue")
    # and the verdict itself refuses to send the reader to plan against it
    assert "solve the dispatch first" in html


def test_an_unsolved_run_never_calls_its_system_recommended(rich_quote) -> None:
    """Nothing is "recommended" and no case "rests on" a floor when nothing was solved."""
    html = _render(rich_quote)
    assert "The recommended system is" not in html
    assert "the case rests on a priced floor" not in html
    assert "selected without a solved dispatch" in html
    assert "the unsolved run reports a priced floor" in html


def test_a_real_solve_removes_the_alarm_and_restores_the_plain_language(rich_quote) -> None:
    """The alarm is a property of the record, not decoration: an OPTIMAL solve clears it."""
    result = rich_quote.result.model_copy(update={"solver_status": s.SolverStatus.OPTIMAL})
    quote = rich_quote.quote.model_copy(update={"optimisation_result": result})
    html = render_report_for_quote(quote, rich_quote.site)

    assert "DISPATCH NOT SOLVED" not in html
    assert "The recommended system is" in html
    assert "the case rests on a priced floor" in html


# --- confidence ------------------------------------------------------------------------------


def test_confidence_band_and_binding_factors_render(rich_quote) -> None:
    assessment = assess_confidence(rich_quote.site, rich_quote.quote)
    view = ConfidenceView.from_assessment(assessment, ceiling="high")
    html = _render(rich_quote, confidence=view)

    assert f"CONFIDENCE {assessment.band.value.upper()}" in html
    assert assessment.binding_factors, "the fixture should be capped by at least one factor"
    # The report rewrites em/en dashes out of every string on its way to the reader, record text
    # included, so the expectation is the rewritten form of the factor's own reason.
    for factor in assessment.binding_factors:
        assert escape(fmt.no_dashes(factor.name)) in html
        assert escape(fmt.no_dashes(factor.reason)) in html
    # the band is never presented as a probability or a hurdle
    assert "Not a probability" in html
    assert "Ceiling set by the half-hourly basis: HIGH." in html


def test_an_unassessed_band_says_so_rather_than_defaulting(rich_quote) -> None:
    """The fixture's record carries no assessed band; the report must not invent one."""
    assert rich_quote.quote.confidence_band is None
    html = _render(rich_quote)
    assert "CONFIDENCE NOT ASSESSED" in html
    assert "unavailable" in html
    for band in ("CONFIDENCE LOW", "CONFIDENCE MEDIUM", "CONFIDENCE HIGH"):
        assert band not in html


def test_binding_factors_are_repeated_as_what_would_raise_the_band(rich_quote) -> None:
    assessment = assess_confidence(rich_quote.site, rich_quote.quote)
    html = _render(rich_quote, confidence=ConfidenceView.from_assessment(assessment))
    assert "8 · What would change this" in html
    assert "only these factors can raise it" in html


# --- determinism ------------------------------------------------------------------------------


def test_same_record_renders_byte_identically_twice(rich_quote) -> None:
    assessment = assess_confidence(rich_quote.site, rich_quote.quote)
    view = ConfidenceView.from_assessment(assessment)
    asks = build_ranked_data_asks(rich_quote.site)
    first = _render(rich_quote, confidence=view, data_asks=asks)
    second = _render(rich_quote, confidence=view, data_asks=asks)
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


# --- presentation ------------------------------------------------------------------------------


def test_sections_follow_the_deliverable_structure(rich_quote) -> None:
    html = _render(rich_quote)
    # THE OWNER'S ORDER (D59, verbatim): "starting with the IRR and Revenue breakdown and graphs,
    # then the methodology and then the assumptions register for each site". Asserted on the
    # rendered bytes by position, so a renderer that emits the right sections in the wrong order
    # fails here rather than passing on a set membership check.
    headings = [
        "1 · Results: IRR and revenue",
        "2 · The graphs: dispatch, the sizing surface and the selection",
        "3 · Methodology",
        "4 · Assumptions register",
        "5 · Situation",
        "6 · Complication",
        "7 · Evidence and provenance",
        "8 · What would change this",
    ]
    positions = [html.index(h) for h in headings]
    assert positions == sorted(positions), "sections must appear in the order the owner specified"


def test_exhibits_are_numbered_contiguously_and_each_carries_a_source(rich_quote) -> None:
    html = _render(rich_quote)
    numbers = [int(n) for n in re.findall(r'class="exnum">Exhibit (\d+)<', html)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert len(numbers) == html.count('class="src">Source:')


def test_exhibit_titles_state_a_finding_not_a_topic(rich_quote) -> None:
    """The revenue exhibit names the stream that carries the value, from the record's own rows."""
    vm = _vm(rich_quote)
    html = _render(rich_quote)
    biggest = max(vm.split.floor, key=lambda r: r.annual_revenue_gbp)
    assert fmt.stream_label(biggest.stream) in html
    assert "is the largest priced stream at" in html


def test_the_caveat_ladder_prints_every_condition_that_fires(rich_quote) -> None:
    """An earlier caveat must never suppress a later one.

    The ladder used to ``return`` at its first hit, so this record's covenant failure — the
    loudest fact on it — never reached the summary at all: it was hidden behind the not-bankable
    stamp and demoted to a ``FAIL`` in a table cell.
    """
    vm = _vm(rich_quote)
    html = _render(rich_quote)
    summary = html[html.index("1 · Results: IRR and revenue") : html.index("2 · The graphs")]

    assert vm.dscr.covenant_passes is False
    assert vm.dscr.min_dscr is not None and vm.dscr.covenant is not None
    assert not vm.bankable
    # the not-bankable caveat AND the covenant caveat, both, with the two ratios side by side
    assert "This run is not bankable" in summary
    assert fmt.ratio(vm.dscr.min_dscr) in summary
    assert fmt.ratio(vm.dscr.covenant) in summary
    assert "does not service its debt" in summary
    assert "The one caveat that matters most" not in html


def test_the_exhibit_titles_state_findings_the_record_supports(rich_quote) -> None:
    """Titles are findings, and never claim more than the record carries.

    Two specific overclaims are pinned: the returns exhibit states the DSCR outcome rather than a
    maxim about debt, and the provenance exhibit claims identification, not reproducibility (the
    input hash pins neither the half-hourly nor the assumption content — F-06/F-09).
    """
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert vm.dscr.min_dscr is not None
    assert f"Minimum {vm.dscr.basis} DSCR is {fmt.ratio(vm.dscr.min_dscr)} against a" in html
    assert "the debt does not cover" in html
    assert "Returns are only as firm as the debt terms behind them" not in html
    assert "reproducible run" not in html


def test_the_pack_never_contradicts_itself_about_duos(rich_quote) -> None:
    """The tariff exhibit may not say DUoS value is unpriced while Resolution prices a DUoS stream.

    The site record carries no DUoS band rates *and* the run prices £12k of DUoS red-band
    avoidance. Both are true; the honest finding is the narrower, worse one — whatever was
    priced, it was not priced from this site's own bands.
    """
    vm = _vm(rich_quote)
    html = _render(rich_quote)

    assert rich_quote.site.tariff_stack.duos_red_rate is None
    duos = [r for r in vm.split.floor if r.stream == "DUOS_RED_BAND_AVOIDANCE"]
    assert duos, "the fixture should price a DUoS stream"
    assert "most behind-the-meter value is unpriced" not in html
    assert "was not priced from this site&#x27;s own tariff" in html


def test_a_price_never_appears_without_its_target_return_state(rich_quote) -> None:
    """A headline price is exactly the figure that must not stand alone.

    WP-UW-17 (D128): the fixture's price is no longer £0.0000/kWh. Its project IRR is
    7.41% against a 12% target, so a zero price was the N12 defect (the floor netted on a flat
    year-one volume). The levelised price is positive, and the target-return state must still sit
    beside it: the target is a placeholder, so ``hits_target_return`` stays unknown.
    """
    vm = _vm(rich_quote)
    html = _render(rich_quote)
    summary = html[html.index("1 · Results: IRR and revenue") : html.index("2 · The graphs")]

    price = vm.price.value_gbp_per_kwh
    assert price is not None and price > 0.0
    assert rich_quote.case.irr is not None and rich_quote.case.irr < 12.0
    assert vm.price.hits_target_return is None
    assert "does not assert that this price hits the target return" in summary
    assert escape(fmt.gbp_per_kwh(vm.price.counterfactual_import_gbp_per_kwh or 0.0)) in summary
    # and the Complication comparison carries the price, so "within it" is never read alone
    assert escape(f"{fmt.gbp_per_kwh(price)} (within it)") in html


def test_the_irr_is_labelled_unlevered_wherever_it_appears(rich_quote) -> None:
    """7.4% next to 70% gearing invites the reader to take it for an equity return."""
    html = _render(rich_quote)
    assert html.count("Project IRR (unlevered)") == 2
    assert ">IRR<" not in html
    assert "No levered or equity return appears anywhere in this pack." in html


def test_every_stream_row_says_which_total_it_is_counted_in(rich_quote) -> None:
    """Floor, upside and excluded share one table; the reader must see which is which."""
    vm = _vm(rich_quote)
    html = _render(rich_quote)
    assert "Counted in" in html
    for label, group in (
        ("Priced floor", vm.split.floor),
        ("Merchant upside", vm.split.upside),
        ("Excluded", vm.split.excluded),
    ):
        assert group, f"the fixture should carry at least one {label} stream"
        assert f"<td>{label}</td>" in html


def test_complication_says_the_uncomfortable_things_in_prose(rich_quote) -> None:
    """Not in a table cell, not softened: the unsolved dispatch and the covenant shortfall."""
    html = _render(rich_quote)
    complication = html[html.index("6 · Complication") : html.index("7 · Evidence")]
    assert "The dispatch behind these numbers was not solved" in complication
    assert "The debt does not cover." in complication
    assert "the shortfall is in the capital structure" in complication


def test_complication_reproduces_every_warning_unfiltered(rich_quote) -> None:
    vm = _vm(rich_quote)
    html = _render(rich_quote)
    assert vm.warnings, "the fixture should carry warnings"
    complication = html[html.index("6 · Complication") : html.index("7 · Evidence")]
    # Unfiltered still means unfiltered: every warning is reproduced whole. The only transform is
    # the render-time em/en dash rewrite, which the record itself never sees.
    for warning in vm.warnings:
        assert escape(fmt.no_dashes(warning)) in complication


def test_provenance_spine_is_present(rich_quote) -> None:
    q = rich_quote.quote
    html = _render(rich_quote)
    assert q.quote_id in html
    assert q.input_hash is not None and q.input_hash in html
    assert q.assumption_version is not None and q.assumption_version in html
    assert q.code_version is not None and q.code_version in html
    assert q.schema_version in html


def test_report_is_self_contained_and_print_friendly(rich_quote) -> None:
    html = _render(rich_quote)
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "@media print" in html
    for forbidden in ("http://", "https://", "<script", "<link", "@import"):
        assert forbidden not in html


def test_free_text_from_the_site_is_escaped(rich_quote) -> None:
    hostile = rich_quote.site.model_copy(update={"site_name": "<script>alert(1)</script>"})
    html = render_consulting_report(build_view_model(rich_quote.quote, hostile), site=hostile)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_ranked_data_asks_render_when_supplied(rich_quote) -> None:
    asks = build_ranked_data_asks(rich_quote.site)
    html = _render(rich_quote, data_asks=asks)
    for ask in asks:
        assert escape(ask.title) in html
        assert escape(ask.why) in html


def test_absent_data_asks_are_labelled_rather_than_dropped(rich_quote) -> None:
    html = _render(rich_quote)
    reason = "no ranked data ask was supplied with this run"
    assert escape(fmt.unavailable(reason)) in html
