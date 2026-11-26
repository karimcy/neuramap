"""`ValueStreamResult.warnings` must reach the RENDERED BYTES of a published pack.

**The defect.** A run record holds a full account of why a stream earned nothing. On the
2026-08-15 packs it said, per stream, that 27 candidates were solved on the full series, that the
dispatch discharged nothing, and that a cycle needs a spread of 0.04788 GBP/kWh against a best
available 0.03525, netting -0.011113 GBP/kWh so no pair of half-hours repays the round trip. None
of it was rendered. A reader opened the pack, saw "Tariff arbitrage GBP 0.00", and had no way to
tell that zero from any other. Grepping the published `report.html` and `consulting_report.html`
for "declined every cycle", "0.011113" or "round trip" returned nothing at all.

**Why a GBP 0 row is the worst place to lose it.** Three zeros look identical on a page and this
repo distinguishes all three on the record:

1. **Decided** , the optimiser priced the trade and declined it. The zero is a measurement.
2. **Gated** , a gate held the stream out of the priced floor: a declared input reading null under
   D45, or an eligibility rule (a sub-1 MW connection cannot prequalify for the Capacity Market).
3. **Unmeasured** , no dispatch was solved, so no figure was ever computed.

Collapsing them into one "GBP 0.00" is exactly the class of defect CLAUDE.md §4 forbids: the pack
lets a reader draw a conclusion the record does not support.

**Why this test goes one link further than the nine before it.** This repo has shipped the same
built-tested-never-called defect repeatedly, and every instance had a passing test, because each
test called the new code at the layer the new code lived in. The chain here is
**record -> view model -> rendered pack**, and only the first link had ever been closed:
`StreamRow.warnings` was populated at `viewmodel.py::_stream_row` and read by nothing.

So nothing below asserts on a record, a view model, or a hand-built `StreamRow`. Every assertion
is on **bytes read back off disk** from a pack published end to end by
`quotes.persistence.publish_run`, which persists the record, reloads it, and renders from the
reload. Deleting any one of the three render sites, or the view-model wiring beneath them, fails
these tests.

**Production call sites** (`docs/validation_requirements.md` §3):

- Clause 1 , `quotes/persistence.py::publish_run` -> `outputs/generate.py::generate_artifacts` ->
  `outputs/html_report.py::_stream_table` + `::_stream_notes`,
  `outputs/consulting_report.py::_stream_rows` + `::_stream_notes`,
  `outputs/underwriting.py::_stream_table` + `::_stream_notes`; fed by
  `outputs/viewmodel.py::_number_stream_notes` and `StreamRow.figure_basis`. This is the same path
  `cli.py` and `app/app.py` write every ``runs/`` directory through.
- Clause 2 , every assertion reads a file `publish_run` wrote. Reverting any render site, or the
  numbering in `_split`, fails the test that names it.

**Why the record is built here and not loaded from ``runs/``.** ``runs/`` is gitignored, so a test
reading a published directory passes vacuously on a fresh checkout , the built-tested-never-called
failure in test form. The record is constructed below, and
`test_the_labels_are_still_the_ones_the_pricing_path_writes` pins its labels to the pricing path's
own constants so the fixture cannot drift away from what production emits.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.outputs.viewmodel import (
    FIGURE_BASIS_DECIDED,
    FIGURE_BASIS_GATED,
    FIGURE_BASIS_UNMEASURED,
)
from gridq.quotes.persistence import publish_run

# --- the record's own labels, quoted from the modules that write them -------------------------

#: `optimisation/valuation.py::ZERO_DISPATCH_LABEL`. Written when a stream's 0.0 is the optimiser's
#: own priced decision rather than an absent input.
_ZERO_DISPATCH_LABEL = "GBP 0 IS A DISPATCH DECISION, NOT A MISSING INPUT"
#: `value_stack/gates.py::D45_NULL_INPUT_LABEL`. Written when a DECLARED pricing input reads null
#: and the stream is therefore held out of the priced floor rather than priced at zero.
_D45_NULL_INPUT_LABEL = "NULL INPUT ON A DECLARED PRICING INPUT (D45)"
#: `optimisation/valuation.py::VALUE_UNAVAILABLE_MARKER`. Written when no dispatch was solved, so
#: the recorded 0.0 is an absence rather than a result.
_VALUE_UNAVAILABLE_MARKER = "value unavailable (not invented)"

# --- the note bodies. Distinctive, and deliberately free of punctuation the render rewrites -----
#
# No em dash, no en dash and no apostrophe in any body below. `formatting.no_dashes` rewrites the
# first two on their way into `report.html` and `memo.md`, and `html.escape` rewrites the third,
# so a body carrying them could fail an assertion for a reason that has nothing to do with whether
# the note was rendered. Each body is unique so a hit can only have come from that note.

#: The arithmetic the published packs withheld, in the shape the pricing path writes it.
_DECIDED_BODY = (
    "27 candidate(s) were solved and priced on the full series and the optimiser declined every "
    "cycle: a cycle needs a spread of 0.04788 GBP per kWh and the best this site offers is "
    "0.03525, netting minus 0.011113 GBP per kWh, so no pair of half hours repays the round trip."
)
_DECIDED_NOTE = f"{_ZERO_DISPATCH_LABEL}: {_DECIDED_BODY}"

#: A gate closed by a null declared input (D45): the stream is held out, never priced at zero.
_NULL_INPUT_BODY = (
    "tariff_stack.duos_red_rate reads as NULL on this site, so the stream is held OUT of the "
    "priced floor rather than priced at zero, because a null priced through would enter P50, P90 "
    "and the DSCR indistinguishable from a measured result."
)
_NULL_INPUT_NOTE = f"{_D45_NULL_INPUT_LABEL}: {_NULL_INPUT_BODY}"

#: A gate closed by an eligibility rule the site cannot clear at any battery size.
_INELIGIBLE_NOTE = (
    "INELIGIBLE: prequalification requires at least 1.000 MW of connection capacity and this "
    "connection presents at most 0.500 MW, so no battery here can prequalify standalone."
)

#: No dispatch solved at all: the recorded figure is an absence, not a result.
_UNMEASURED_NOTE = f"no dispatch solved - {_VALUE_UNAVAILABLE_MARKER}."

#: The one note that DOES carry an em dash, because production warnings are full of them and this
#: is the first record text to reach `report.html` and `memo.md` through a stream table. Neither
#: document is scrubbed at document level, so the note renderers call `formatting.no_dashes`
#: themselves; without that call this string reaches a reader with the dash intact.
_EM_DASH = chr(0x2014)
_DASHED_NOTE = (
    f"local flex is not priced into the floor {_EM_DASH} no known open tender in the DNO area."
)
#: The same sentence as `no_dashes` documents its rewrite: an em dash marks an aside, so it becomes
#: a comma. Hardcoded rather than computed by calling the helper, which would only test it against
#: itself.
_DASHED_NOTE_REWRITTEN = (
    "local flex is not priced into the floor, no known open tender in the DNO area."
)

#: The four documents `publish_run` writes into every run directory.
_DOCUMENTS = ("memo.md", "proposal.md", "report.html", "consulting_report.html")
#: The two a reader opens in a browser, and the two this fix is judged on.
_HTML_DOCUMENTS = ("report.html", "consulting_report.html")


def _stream(
    stream: s.MarketStream,
    firmness: s.Firmness,
    gate_status: str,
    warnings: list[str],
) -> s.ValueStreamResult:
    return s.ValueStreamResult(
        stream=stream,
        annual_revenue_gbp=0.0,
        firmness=firmness,
        gate_status=gate_status,
        warnings=warnings,
    )


@pytest.fixture
def published(rich_quote, tmp_path: Path) -> dict[str, str]:
    """Publish a record carrying all three zero-bases, and return the documents read off disk.

    The record is deliberately the awkward one: every stream reads GBP 0.00, so the revenue column
    alone tells a reader nothing and the basis has to come from somewhere else. Built by overriding
    `rich_quote` so the site, counterparty and assumption stamps stay the ones the rest of the
    suite exercises, and only the state under test changes.
    """
    result = rich_quote.result.model_copy(
        update={
            "value_streams": [
                # 1. DECIDED: the gate is open and the optimiser priced the trade and declined it.
                _stream(
                    s.MarketStream.TARIFF_ARBITRAGE,
                    s.Firmness.FIRM,
                    "open",
                    [_DECIDED_NOTE],
                ),
                # The same note again, on a second stream. The pricing path stamps it on every
                # stream of an unselected run, which is what makes de-duplication load-bearing.
                _stream(
                    s.MarketStream.PPA_OR_BILL_SAVINGS,
                    s.Firmness.FIRM,
                    "open",
                    [_DECIDED_NOTE],
                ),
                # 2a. GATED by a null declared input. Excluded, so `report.html` and `memo.md`
                # give it no table row: only the notes block can carry its reason.
                _stream(
                    s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
                    s.Firmness.EXCLUDED,
                    "closed",
                    [_NULL_INPUT_NOTE, _DECIDED_NOTE],
                ),
                # 2b. GATED by an eligibility rule. Conditional, so it keeps a row.
                _stream(
                    s.MarketStream.CAPACITY_MARKET,
                    s.Firmness.CONDITIONAL,
                    "conditional",
                    [_INELIGIBLE_NOTE, _DECIDED_NOTE],
                ),
                # 3. UNMEASURED: no dispatch was solved, so the 0.0 is an absence.
                _stream(
                    s.MarketStream.MIC_ASC_HEADROOM,
                    s.Firmness.FIRM,
                    "open",
                    [_UNMEASURED_NOTE],
                ),
                # A note carrying an em dash, as production warnings routinely do.
                _stream(
                    s.MarketStream.LOCAL_FLEX_PICLO,
                    s.Firmness.CONDITIONAL,
                    "conditional",
                    [_DASHED_NOTE],
                ),
            ],
            "gated_upside": [],
        }
    )
    quote = rich_quote.quote.model_copy(
        update={
            "quote_mode": s.QuoteMode.UNDERWRITTEN,
            "optimisation_result": result,
        }
    )

    class _Run:  # minimal duck of QuoteRunResult: publish_run reads quote / site / telemetry
        pass

    run = _Run()
    run.quote = quote  # type: ignore[attr-defined]
    run.site = rich_quote.site  # type: ignore[attr-defined]
    run.telemetry = None  # type: ignore[attr-defined]

    result_run = publish_run(run, root=tmp_path)  # type: ignore[arg-type]
    directory = result_run.persisted.directory
    documents = {name: (directory / name).read_text(encoding="utf-8") for name in _DOCUMENTS}
    # Fixture sanity: these really are the bytes publish_run wrote, not an in-memory render.
    assert {p.name for p in result_run.written} == set(_DOCUMENTS)
    assert documents["report.html"].startswith("<!doctype html>")
    return documents


def _flat(text: str) -> str:
    """Rendered markup as flowing text, so markup between words cannot defeat an assertion."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text)))


# --- 1. the account reaches the rendered bytes -------------------------------------------------


@pytest.mark.parametrize("document", _HTML_DOCUMENTS)
def test_the_dispatch_arithmetic_reaches_the_rendered_html(published, document: str) -> None:
    """The sentences the published packs withheld, in the pack a reader can open.

    This is the whole defect in one assertion. Before the wiring, grepping the published
    `report.html` and `consulting_report.html` for "declined every cycle", "0.011113" and
    "round trip" returned zero hits in both, while the record beside them held all three.
    """
    flat = _flat(published[document])
    for fragment in ("declined every cycle", "0.011113", "round trip"):
        assert fragment in flat, (
            f"{document} renders GBP 0.00 without the record's own account of it: {fragment!r} is "
            "on the record and absent from the pack"
        )
    assert _DECIDED_BODY in flat, f"{document} does not carry the decided note in full"


@pytest.mark.parametrize("document", _HTML_DOCUMENTS)
def test_every_stream_note_on_the_record_reaches_the_rendered_html(
    published, document: str
) -> None:
    """All three classes, including the two whose streams have no table row in `report.html`."""
    flat = _flat(published[document])
    for label, body in (
        ("decided", _DECIDED_BODY),
        ("null input", _NULL_INPUT_BODY),
        ("ineligible", _INELIGIBLE_NOTE),
        ("unmeasured", _UNMEASURED_NOTE),
    ):
        assert body in flat, f"{document} drops the {label} note the record wrote"


def test_the_notes_and_the_markers_reach_the_underwriting_memo_too(published) -> None:
    """The lender-facing document renders the same account, and the same markers, as the HTML packs.

    The marker half is asserted here rather than left to the HTML-only parametrisation above: the
    memo's `_stream_table` builds its own basis cell, so without this, deleting that cell would
    leave every test green : this file's own version of the defect it exists to pin.
    """
    flat = _flat(published["memo.md"])
    for body in (_DECIDED_BODY, _NULL_INPUT_BODY, _INELIGIBLE_NOTE):
        assert body in flat, "memo.md drops a note the record wrote against a value stream"
    assert "Basis of figure" in flat, "memo.md has no basis column on its stream tables"
    for marker in ("Priced, then declined", "Held out by a gate", "Not measured"):
        assert marker in flat, (
            f"memo.md prints GBP 0.00 rows without saying which zero they are: no {marker!r}"
        )


# --- 2. which zero it is, on the row itself ----------------------------------------------------


@pytest.mark.parametrize("document", _HTML_DOCUMENTS)
def test_each_zero_row_says_which_kind_of_zero_it_is(published, document: str) -> None:
    """A marker per row, so the distinction survives a reader who never reaches the notes.

    Every stream on this record reads GBP 0.00, so the revenue column is uniform and the marker is
    the only thing on the row that separates a measured decline from a gate and from an absence.
    """
    flat = _flat(published[document])
    for basis, marker in (
        (FIGURE_BASIS_DECIDED, "Priced, then declined"),
        (FIGURE_BASIS_GATED, "Held out by a gate"),
        (FIGURE_BASIS_UNMEASURED, "Not measured"),
    ):
        assert marker in flat, (
            f"{document} prints GBP 0.00 rows without saying which zero they are: no {basis!r} "
            f"marker ({marker!r}) is rendered"
        )
    assert "Basis of figure" in flat, f"{document} has no basis column on the stream table"


def test_a_gated_stream_is_never_reported_as_one_the_optimiser_declined(published) -> None:
    """The classification reads `gate_status`, not warning prose, and this is why it must.

    The pricing path stamps the zero-dispatch label on EVERY stream of a run that selected nothing,
    gated streams included. A classifier that matched that label alone would print "Priced, then
    declined" against the Capacity Market row here , asserting the optimiser weighed a stream the
    record says was never eligible to be weighed. That is prose asserting what the record does not
    hold, which this repo treats exactly as seriously as a wrong number.
    """
    html = published["consulting_report.html"]
    # The register row itself, not the chart label of the same name: the Capacity Market stream
    # here carries the zero-dispatch label AND a conditional eligibility gate.
    row = re.search(r"<tr><td>Capacity Market</td>.*?</tr>", html, re.S)
    assert row is not None, "fixture sanity: no Capacity Market register row rendered"
    cells = _flat(row.group(0))
    assert "Held out by a gate" in cells, (
        "a gated stream is reported as one the optimiser priced and declined; the classifier is "
        f"reading warning prose instead of the structured gate_status. Row: {cells}"
    )
    assert "Priced, then declined" not in cells

    # And the converse, on the same record: an open-gate stream carrying the same label IS
    # reported as decided, so the guard above is a distinction and not a blanket suppression.
    arb = re.search(r"<tr><td>Tariff arbitrage</td>.*?</tr>", html, re.S)
    assert arb is not None, "fixture sanity: no Tariff arbitrage register row rendered"
    assert "Priced, then declined" in _flat(arb.group(0))


# --- 3. the presentation defence: stated once, not once per stream ------------------------------


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_the_shared_note_is_stated_once_however_many_streams_carry_it(
    published, document: str
) -> None:
    """De-duplication, which is what makes the disclosure affordable.

    Four of the five streams on this record carry the same dispatch paragraph, and a real run
    carries it on all eleven. Printed per row it is one long paragraph repeated once per stream,
    in packs an adversarial review already returned as bloated for exactly that (the same 200-word
    paragraph three to five times). Stated once with its streams named, the disclosure is complete
    and costs a fraction of the words. If this count ever exceeds one, the pack has started
    repeating itself again.
    """
    assert _flat(published[document]).count(_DECIDED_BODY) <= 1, (
        f"{document} repeats one stream note per stream carrying it; state it once and name the "
        "streams it applies to"
    )


def test_a_deduplicated_note_names_every_stream_it_covers(published) -> None:
    """Stating a note once is only honest if it says who it applies to.

    This is also what keeps `report.html` and `memo.md` complete: both render excluded streams as a
    bare name list rather than table rows, so DUoS red-band avoidance has no row to carry a marker
    and the note is the only place its reason can live.
    """
    flat = _flat(published["report.html"])
    shared = flat.find(_DECIDED_BODY)
    assert shared > 0, "fixture sanity: the shared note did not render"
    # The note's own stream list is rendered immediately before its text.
    preamble = flat[max(0, shared - 400) : shared]
    for label in ("Tariff arbitrage", "PPA / bill savings", "Capacity Market"):
        assert label in preamble, f"the shared note does not name {label!r} among its streams"

    null_note = flat.find(_NULL_INPUT_BODY)
    assert null_note > 0, "fixture sanity: the null-input note did not render"
    assert "DUoS red-band avoidance" in flat[max(0, null_note - 300) : null_note], (
        "the null-input note does not name the excluded stream it belongs to, which is the only "
        "place that stream's reason appears in this document"
    )


# --- 4. the constraints the pack is held to ----------------------------------------------------


@pytest.mark.parametrize("document", ("memo.md", "report.html", "consulting_report.html"))
def test_a_dashed_note_is_rewritten_before_it_reaches_a_reader(published, document: str) -> None:
    """Record text reaches `report.html` and `memo.md` through a table for the first time here.

    Neither of those two is scrubbed at document level (`consulting_report` and `proposal` are), and
    production stream warnings are full of em dashes, so the note renderers call
    `formatting.no_dashes` themselves. Drop that call and the raw dash reaches a reader, which this
    asserts against directly: `_DASHED_NOTE` really does carry one, and the rendered form really is
    checked for it. The record keeps its own strings; only the render is rewritten.
    """
    text = published[document]
    assert _DASHED_NOTE_REWRITTEN in text, (
        f"{document} does not carry the dashed note in its rewritten form"
    )
    assert _DASHED_NOTE not in text, (
        f"{document} renders a stream note with its em dash intact; the note renderer is not "
        "passing record text through formatting.no_dashes"
    )


def test_the_notes_render_no_figure_the_record_does_not_hold(published) -> None:
    """Every number in a note is the record's own text, reproduced, never recomputed.

    The note bodies are asserted verbatim above, which is the real guarantee: a renderer that
    computed anything would not reproduce the record's string byte for byte. This pins the
    numbers a reader would act on, so a paraphrase that silently rounded them would fail.
    """
    flat = _flat(published["consulting_report.html"])
    for figure in ("0.04788", "0.03525", "0.011113", "1.000 MW", "0.500 MW"):
        assert figure in flat, f"{figure!r} is on the record and missing from the pack"


# --- 5. the fixture cannot drift away from what production writes -------------------------------


def test_the_labels_are_still_the_ones_the_pricing_path_writes() -> None:
    """The classification keys on these strings, so a producer rename must fail here, loudly.

    Imported from the producers rather than compared by eye: if `optimisation.valuation` or
    `value_stack.gates` reword a label, this fails and the classification is fixed with it, instead
    of silently falling through to an unmarked row on every future pack.
    """
    from gridq.optimisation.valuation import VALUE_UNAVAILABLE_MARKER, ZERO_DISPATCH_LABEL
    from gridq.value_stack.gates import D45_NULL_INPUT_LABEL

    assert _ZERO_DISPATCH_LABEL == ZERO_DISPATCH_LABEL
    assert _D45_NULL_INPUT_LABEL == D45_NULL_INPUT_LABEL
    assert _VALUE_UNAVAILABLE_MARKER == VALUE_UNAVAILABLE_MARKER


def test_the_view_model_carries_the_producers_own_label_constant() -> None:
    """`viewmodel` duplicates the labels rather than importing them, to stay free of `optimisation`.

    That is a deliberate arrangement (see `_VALUE_UNAVAILABLE`), and it is only safe while the
    duplicates are pinned equal. Without this, a rename upstream leaves the renderers matching a
    string nothing writes any more, and every future pack loses its markers in silence.
    """
    from gridq.optimisation.valuation import ZERO_DISPATCH_LABEL
    from gridq.outputs import viewmodel

    assert viewmodel._ZERO_DISPATCH_LABEL == ZERO_DISPATCH_LABEL
    assert viewmodel._VALUE_UNAVAILABLE == "value unavailable (not invented)"
