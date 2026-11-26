"""The report looks like a Neura document, and only ships what it can carry in one file.

An adversarial review of the six site reports found the brand entirely absent from the rendered
artifact: no wordmark, no logo, no serif headline, no margin labels, no confidentiality stamp, no
hatched capacity device. Every measurement it made is pinned here, against
`docs/design/neura_brand_spec.md` and both pages of the source PDF, so the surface cannot quietly
revert to the grey corporate chrome it started as.

The checks are on the **rendered document**, not on the source: only rendering resolves the
stylesheet, the inline mark and the branch-dependent headline into what a client sees.
"""

from __future__ import annotations

import re
from html import escape

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.ingestion.improvement_asks import build_ranked_data_asks
from gridq.outputs import formatting as fmt
from gridq.outputs import report_charts as charts
from gridq.outputs.consulting_report import ConfidenceView, render_report_for_quote
from gridq.outputs.viewmodel import build_view_model

#: The eight section labels, in the order the deliverable runs them, with the margin numeral each
#: carries. The heading text is the name the repo knows the section by and is asserted elsewhere;
#: what is asserted here is that the numeral and the label reach the page.
#:
#: **The order is the owner's** (D59, Olly, 2026-08-18): the value story leads (I and II), the
#: method
#: that produced it comes next (III), the assumptions register comes third (IV), and the evidence
#: sections that used to open the pack moved DOWN to V through VIII rather than out.
_SECTIONS = (
    ("I", "Results: IRR and revenue"),
    ("II", "The graphs: dispatch, the sizing surface and the selection"),
    ("III", "Methodology"),
    ("IV", "Assumptions register"),
    ("V", "Situation"),
    ("VI", "Complication"),
    ("VII", "Evidence and provenance"),
    ("VIII", "What would change this"),
)
#: The chrome the brand sheet replaced (`report_spec.md` §D.3). Any of these back in the document
#: means the grey corporate styling has returned.
_RETIRED_CHROME = (
    "#22252b",
    "#7a7f8a",
    "#6b7078",
    "#d9dce2",
    "#f6f6f4",
    "#eef0f3",
    "#fafbfc",
    "#1f7a4d",
    "#b45309",
    "Segoe UI",
    "GridQ · Underwriting review",
)


def _render(rq, **kwargs) -> str:
    return render_report_for_quote(
        rq.quote, rq.site, assumptions=load_assumptions("gb_v0_1"), **kwargs
    )


def _stylesheet(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert match is not None, "the report must ship one inline stylesheet"
    return match.group(1)


# --- the mark, the wordmark and the masthead -------------------------------------------------


def test_the_report_carries_the_neura_mark_and_wordmark(rich_quote) -> None:
    """The review measured zero occurrences of the name. The mark is inline SVG, never a file."""
    html = _render(rich_quote)
    assert "Neura" in html
    assert "NEURA ENERGY" in html
    assert '<svg class="mark"' in html
    # An orange rounded square carrying a white angular glyph (brand spec, Logo).
    assert 'rx="6.5" fill="#E2551F"' in html
    assert "<polygon" in html
    # The wordmark is document text in the serif stack, so it needs no font and no image.
    assert ">Neura</span>" in html and ">Energy</span>" in html
    assert "@font-face" not in html


def test_the_page_is_stamped_private_and_confidential(rich_quote) -> None:
    """Zero occurrences before. The ampersand is followed by a space, so the literal is valid."""
    html = _render(rich_quote)
    assert "PRIVATE & CONFIDENTIAL" in html
    assert "SITE FEASIBILITY VALIDATION" in html


def test_the_headline_is_a_serif_sentence_ending_in_a_full_stop(rich_quote) -> None:
    """The headline was a bare label on a system sans stack with no full stop."""
    html = _render(rich_quote)
    headline = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
    assert headline is not None
    text = headline.group(1)
    assert text.endswith(".")
    assert escape(rich_quote.site.site_name) in text
    style = _stylesheet(html)
    assert "--serif:Spectral" in style
    assert "h1{font-family:var(--serif)" in style
    # Every stack ends in a generic family, so a missing brand face degrades rather than breaks.
    assert re.search(r"--serif:[^;]*serif\b", style) is not None
    assert re.search(r"--sans:[^;]*sans-serif\b", style) is not None


def test_the_headline_states_the_recorded_basis_and_never_more(rich_quote) -> None:
    """The clause after the site name is fixed per recorded basis, so it cannot overclaim."""
    metered = _render(rich_quote)
    assert escape("from the site's own metered half-hourly data.") in metered

    modelled = rich_quote.quote.model_copy(update={"half_hourly_basis": s.HalfHourlyBasis.MODELLED})
    html = render_report_for_quote(modelled, rich_quote.site)
    assert "on a modelled load basis, stated as such." in html
    assert "metered half-hourly data." not in html

    unstamped = rich_quote.quote.model_copy(update={"half_hourly_basis": None})
    bare = rich_quote.site.model_copy(
        update={
            "load_data": rich_quote.site.load_data.model_copy(update={"half_hourly_basis": None})
        }
    )
    html = render_report_for_quote(unstamped, bare)
    assert "with the load basis unrecorded." in html


@pytest.mark.parametrize("basis", list(s.HalfHourlyBasis))
def test_no_recorded_basis_is_ever_headlined_as_unrecorded(rich_quote, basis) -> None:
    """A basis the clause map has not learnt is still recorded, and must be named, not denied.

    Adding a member to `HalfHourlyBasis` must never make the front page of a validated site claim
    its load basis is unrecorded, which is the one thing the headline could get materially wrong.
    """
    quote = rich_quote.quote.model_copy(update={"half_hourly_basis": basis})
    html = render_report_for_quote(quote, rich_quote.site)
    headline = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
    assert headline is not None
    assert "load basis unrecorded" not in headline.group(1)
    assert str(basis) in headline.group(1)


# --- the margin labels ----------------------------------------------------------------------


def test_section_labels_are_orange_small_caps_with_a_grey_roman_numeral(rich_quote) -> None:
    """Measured before: `small-caps` 0 occurrences, roman numerals 0."""
    html = _render(rich_quote)
    style = _stylesheet(html)
    assert "small-caps" in style
    assert ".secname{" in style and "var(--neura-orange)" in style
    assert ".secnum{" in style and "var(--neura-grey)" in style
    # The label sits in the left margin: a two-column section grid with the label in column one.
    assert "section.sec>.seclab{grid-column:1" in style

    positions = []
    for numeral, name in _SECTIONS:
        numeral_at = html.index(f'<span class="secnum">{numeral}</span>')
        name_at = html.index(f'<span class="secname">{escape(name)}</span>')
        # The numeral sits above its label, so it comes first in document order.
        assert numeral_at < name_at
        positions.append(numeral_at)
    assert positions == sorted(positions), "the numerals must run I to VII in deliverable order"


def test_the_margin_label_is_decorative_and_the_heading_is_the_accessible_name(rich_quote) -> None:
    """A screen reader hears each section once: from the heading, not from the drawn label."""
    html = _render(rich_quote)
    assert html.count('<div class="seclab" aria-hidden="true">') == len(_SECTIONS)
    for _, name in _SECTIONS:
        assert '<h2 class="sechead">' in html
        assert escape(name) in html
    style = _stylesheet(html)
    assert ".sechead{position:absolute" in style and "clip:rect(0 0 0 0)" in style


# --- the footer -----------------------------------------------------------------------------


def test_the_footer_carries_the_wordmark_and_never_a_faked_page_number(rich_quote) -> None:
    """`NEURA ENERGY` left, run identity right. Page numbers come from a paged renderer only."""
    html = _render(rich_quote)
    footer = html[html.rindex("<footer") :]
    assert '<span class="footmark">NEURA ENERGY</span>' in footer
    assert rich_quote.quote.quote_id in footer
    # No page number is authored into the document body; the paged renderer counts real pages.
    assert not re.search(r"PAGE\s+\d+\s+OF", footer)
    style = _stylesheet(html)
    assert "@page{" in style
    assert "counter(page)" in style and "counter(pages)" in style
    assert '@bottom-left{content:"NEURA ENERGY"' in style


# --- the hatched available-capacity device ---------------------------------------------------


def test_the_hatched_capacity_device_marks_held_out_value_and_nothing_else(rich_quote) -> None:
    """Zero hatch before. Hatched means available and not counted, never an error or an absence."""
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert vm.split.floor and vm.split.upside, "the fixture must carry both groups"
    stack = charts.revenue_stack(
        floor=vm.split.floor, upside=vm.split.upside, excluded=vm.split.excluded
    )
    bars = re.findall(r'<svg class="gqbar".*?</svg>', stack, re.DOTALL)
    assert len(bars) == len(vm.split.floor) + len(vm.split.upside) + len(vm.split.excluded)
    floor_bars = bars[: len(vm.split.floor)]
    upside_bars = bars[len(vm.split.floor) : len(vm.split.floor) + len(vm.split.upside)]
    for bar in floor_bars:
        assert "<line" not in bar, "a counted stream is solid, never hatched"
        assert charts._ORANGE in bar
    for bar in upside_bars:
        strokes = re.findall(r"<line ", bar)
        assert strokes, "a held-out stream carries the hatch"
        assert charts._PEACH in bar
    # 45 degrees: every stroke advances equally in x and y.
    stroke = re.search(
        r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"', upside_bars[0]
    )
    assert stroke is not None
    x1, y1, x2, y2 = (float(value) for value in stroke.groups())
    assert abs((x2 - x1) - (y2 - y1)) < 0.01
    assert "available and not counted" in stack


def test_the_revenue_stack_states_every_value_as_document_text(rich_quote) -> None:
    """The bars are geometry; the figures beside them are the record's, formatted once."""
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    stack = charts.revenue_stack(
        floor=vm.split.floor, upside=vm.split.upside, excluded=vm.split.excluded
    )
    for row in (*vm.split.floor, *vm.split.upside, *vm.split.excluded):
        assert escape(fmt.gbp(row.annual_revenue_gbp)) in stack
        assert escape(fmt.stream_label(row.stream)) in stack
    # No drawn text at all, so the chart adds no numeric run to any SVG on the page.
    assert "<text" not in stack
    # Group sizes are counted from the rows they hold, never fixed in the copy.
    plural = "stream" if len(vm.split.floor) == 1 else "streams"
    assert f"Priced floor: {fmt.count_word(len(vm.split.floor))} {plural}" in stack


def test_the_revenue_stack_degrades_to_a_reasoned_unavailable() -> None:
    stack = charts.revenue_stack(floor=(), upside=(), excluded=())
    assert "unavailable" in stack
    assert "no value stream" in stack
    assert "<svg" not in stack


def test_the_revenue_stack_is_deterministic(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    first = charts.revenue_stack(
        floor=vm.split.floor, upside=vm.split.upside, excluded=vm.split.excluded
    )
    second = charts.revenue_stack(
        floor=vm.split.floor, upside=vm.split.upside, excluded=vm.split.excluded
    )
    assert first == second


# --- what the brand sheet replaced ----------------------------------------------------------


def test_the_grey_corporate_chrome_is_gone(rich_quote) -> None:
    """`report_spec.md` §D.3: the old palette and stack are replaced wholesale, not layered over."""
    html = _render(rich_quote, data_asks=build_ranked_data_asks(rich_quote.site))
    offenders = [token for token in _RETIRED_CHROME if token in html]
    assert not offenders, f"retired chrome back in the report: {offenders}"


def test_the_confidence_band_never_borrows_the_signal_colour(rich_quote) -> None:
    """A band is evidential strength, not an alarm (`report_spec.md` §B.0)."""
    style = _stylesheet(_render(rich_quote))
    assert ".band.low{background:var(--neura-ink)}" in style
    assert ".band.medium{background:var(--neura-orange-mid)}" in style
    assert ".band.high{background:var(--neura-orange)}" in style


def test_the_brand_tokens_are_declared_once_and_used_by_name(rich_quote) -> None:
    style = _stylesheet(_render(rich_quote))
    for token in (
        "--neura-orange:#E2551F",
        "--neura-orange-mid:#EF8B4E",
        "--neura-peach:#F5C9A8",
        "--neura-cream:#EAE7E1",
        "--neura-cream-light:#F5F3EF",
        "--neura-ink:#1F1F1D",
        "--neura-grey:#8A8A85",
        "--neura-rule:#D8D5CE",
    ):
        assert token in style


def test_the_document_stays_one_self_contained_file(rich_quote) -> None:
    """The brand surface adds no asset: no font file, no image, no fragment reference."""
    html = _render(rich_quote, confidence=ConfidenceView(band="low"))
    for forbidden in ("http://", "https://", "<script", "<link", "@import", "@font-face", "url("):
        assert forbidden not in html
