"""Value → string formatting for the output artifacts (M8).

This module is the **single place a number becomes a string**. Per the M8 rules, the
renderers (`underwriting`, `proposal`, `html_report`) do *no* arithmetic — they call these
helpers, which are allowed to "format existing values" (e.g. divide by 1000 for a £k headline).
Nothing here computes a new financial figure: every function takes a value that already exists
on the run record and returns its display string, or an explicit *unavailable* / *placeholder*
label when the value is absent.

Keeping all numeric→text conversion here is what lets a test assert the HTML generator contains
no arithmetic (see tests/test_outputs_no_arithmetic_in_html.py).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the view model imports nothing from here, so this stays one-way
    from gridq.outputs.viewmodel import (
        AssumptionPageRow,
        AssumptionsPageView,
        MethodologyView,
        StreamAssumptionRow,
        StreamNote,
    )
    from gridq.schemas import SizingGrid, SizingGridPoint

# The dash characters a client-facing deliverable must not contain. Built with `chr` on purpose:
# the package sweep in tests/test_outputs_no_em_dashes.py reads every string literal in this
# package, so the characters themselves may not appear as literals here either.
_EM_DASH = chr(0x2014)  # em dash
_EN_DASH = chr(0x2013)  # en dash
_HORIZONTAL_BAR = chr(0x2015)  # horizontal bar (an em-dash variant)


def no_dashes(text: str) -> str:
    """Rewrite em and en dashes out of text on its way to the reader.

    The requirement is absolute: no rendered deliverable carries an em dash or an en dash. The
    strings that would carry one arrive from all over ``src`` (``quote.warnings`` above all, which
    the report reproduces verbatim and unfiltered), so exempting record text would leave the
    requirement unmet on exactly the text a client reads most closely.

    The rewrite therefore happens at render time, on the finished document, and the run record keeps
    its original strings untouched: nothing upstream is scrubbed, nothing is stored differently, and
    re-reading the record returns what the engine wrote. An em dash marks an aside, so it becomes a
    comma; an en dash marks a range, so it becomes the word "to". Spaced forms are handled before
    bare ones so no stray space is left in front of the comma.
    """
    for dash in (_EM_DASH, _HORIZONTAL_BAR):
        text = text.replace(f" {dash} ", ", ").replace(f"{dash} ", ", ")
        text = text.replace(f" {dash}", ", ").replace(dash, ", ")
    text = text.replace(f" {_EN_DASH} ", " to ").replace(f"{_EN_DASH} ", " to ")
    return text.replace(f" {_EN_DASH}", " to ").replace(_EN_DASH, " to ")


def unavailable(reason: str) -> str:
    """Render an absent value as an explicit, reasoned *unavailable* label (never a 0 or guess).

    The word carries the sentinel: the label always states *why* the figure is missing, so a
    reader never sees a blank cell and never sees an invented 0 (degrade honestly, see SKILLS.md).
    """
    return f"unavailable ({reason})"


def placeholder(value_str: str) -> str:
    """Tag an already-formatted value as placeholder-driven (not yet bankable)."""
    return f"{value_str} [placeholder: not bankable]"


def gbp(value: float) -> str:
    """Whole-pounds-and-pence sterling with thousands separators; sign kept outside the £."""
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value):,.2f}"


def gbp_compact(value: float) -> str:
    """Compact sterling for headlines (£1.2m / £21.0k / £840). Formatting-only scaling."""
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1_000_000:
        return f"{sign}£{magnitude / 1_000_000:,.1f}m"
    if magnitude >= 1_000:
        return f"{sign}£{magnitude / 1_000:,.1f}k"
    return f"{sign}£{magnitude:,.0f}"


def gbp_per_kwh(value: float) -> str:
    """A £/kWh price to 4 dp (the unit the price recommendation is stated in)."""
    return f"£{value:.4f}/kWh"


def pct(value: float) -> str:
    """A percentage value (already in %, not a fraction) to 1 dp."""
    return f"{value:.1f}%"


def ratio(value: float) -> str:
    """A coverage ratio (e.g. DSCR) to 2 dp with a multiplication sign."""
    return f"{value:.2f}×"


def kw(value: float) -> str:
    """Power in kW (1 dp)."""
    return f"{value:,.1f} kW"


def kwh(value: float) -> str:
    """Energy in kWh (1 dp)."""
    return f"{value:,.1f} kWh"


def hours(value: float) -> str:
    """A duration in hours (1 dp)."""
    return f"{value:.1f} h"


def kw_at(value: float, places: int) -> str:
    """Power in kW at a chosen number of decimals. ``places=1`` is exactly `kw`.

    Chosen precision exists for one reason: a table that shows power, duration and energy side by
    side makes an arithmetic claim, and rounding each column independently can break it. At 1 dp a
    45.75 kW candidate prints "45.8 kW", and 45.8 times 8.0 is 366.4 against a recorded 366.0 kWh:
    a reader with a calculator finds the row wrong. The caller picks the precision at which the row
    reconciles (see `report_charts`), and it is picked from the record's own values, never invented.
    """
    return f"{value:,.{places}f} kW"


def hours_at(value: float, places: int) -> str:
    """A duration in hours at a chosen number of decimals. ``places=1`` is exactly `hours`.

    Same reason as `kw_at`: a duration derived as energy over power lands on 0.5283 h, and printing
    it as "0.5 h" beside its own recorded energy states a product that does not multiply.
    """
    return f"{value:.{places}f} h"


def kwh_at(value: float, places: int) -> str:
    """Energy in kWh at a chosen number of decimals. ``places=1`` is exactly `kwh`.

    The third lever in the same reconciliation: a coarser energy column widens the tolerance the
    other two columns have to hit, so whole kWh on a table of candidate sizes can be what makes
    every row reconcile exactly where a tenth of a kWh cannot.
    """
    return f"{value:,.{places}f} kWh"


def years(value: float) -> str:
    """A number of years (1 dp)."""
    return f"{value:.1f} yr"


def count(value: int) -> str:
    """A counted quantity of record items (rows, evaluations, asks) with thousands separators."""
    return f"{value:,}"


#: Cardinal words up to twelve. Beyond that a numeral reads better than a word, which is where
#: `count_word` hands over to `count`.
_CARDINALS: tuple[str, ...] = (
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def count_word(value: int) -> str:
    """A small count as a cardinal word, a larger one as a numeral.

    Report prose says "seven answers", not "7 answers". The word still comes from the record's own
    count, so a sentence that names how many of something there are cannot drift from the table
    underneath it: that drift is exactly what a fixed "Three answers" produced above seven rows.
    """
    if 0 <= value < len(_CARDINALS):
        return _CARDINALS[value]
    return count(value)


def date_str(value: date | datetime) -> str:
    """An ISO date (YYYY-MM-DD); datetimes are reduced to their date part."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def yes_no(value: bool) -> str:  # noqa: FBT001 (positional by design: a boolean formatter)
    """A boolean as Yes/No."""
    return "Yes" if value else "No"


def pass_fail(value: bool | None) -> str:
    """A covenant-style verdict; ``None`` is *not evaluated* (never silently a pass)."""
    if value is None:
        return "not evaluated"
    return "PASS" if value else "FAIL"


def titleise(token: str) -> str:
    """Turn an enum-ish token (``not_bankable`` / ``draft``) into a readable label.

    For value streams and price structures prefer `stream_label` / `structure_label`, which keep
    domain acronyms (DUoS, MIC, P415, £/kWh) intact.
    """
    return token.replace("_", " ").strip().title()


# Readable labels for the closed enums, keyed by their ``.value`` strings. These are presentation
# only (no number, no logic); an unknown value falls back to `titleise` so nothing breaks.
_STREAM_LABELS: dict[str, str] = {
    "PPA_OR_BILL_SAVINGS": "PPA / bill savings",
    "TARIFF_ARBITRAGE": "Tariff arbitrage",
    "DUOS_RED_BAND_AVOIDANCE": "DUoS red-band avoidance",
    "MIC_ASC_HEADROOM": "MIC / ASC headroom",
    "CAPACITY_MARKET": "Capacity Market",
    "LOCAL_FLEX_PICLO": "Local flexibility (Piclo)",
    "WHOLESALE_P415": "Wholesale (P415)",
    "BALANCING_MECHANISM": "Balancing Mechanism",
    "ANCILLARY_DC_DM_DR": "Ancillary services (DC/DM/DR)",
    "AGREED_CAPACITY_REDUCTION": "Avoided agreed capacity + TCR band",
}

_STRUCTURE_LABELS: dict[str, str] = {
    "fixed_gbp_per_kwh": "Fixed £/kWh",
    "savings_share": "Savings share",
    "capacity_fee": "Capacity fee",
}

#: Why no commercial structure is shown when the record carries no financial case. Stated once and
#: reused, so all five render sites give the reader the same reason.
_NO_STRUCTURE = "no financial case on this record, so no commercial structure has been set"


def stream_label(value: str) -> str:
    """Readable name for a `MarketStream` value (keeps DUoS/MIC/P415 acronyms)."""
    return _STREAM_LABELS.get(value, titleise(value))


def structure_label(value: str | None) -> str:
    """Readable name for a `PriceStructure` value (keeps the £/kWh form).

    ``None`` is the record's own silence, not a default. A run with no financial case has priced
    nothing and therefore settled no commercial structure, so every surface that shows a structure
    must say that rather than print the enum's first member. Naming a structure on a do-not-proceed
    document is a commercial claim the record does not hold.
    """
    if value is None:
        return unavailable(_NO_STRUCTURE)
    return _STRUCTURE_LABELS.get(value, titleise(value))


# --- the dedicated assumptions page, in Markdown (D56 rule 4) --------------------------------

#: The page's own heading, in every pack. One string so the four artifacts cannot drift into four
#: different names for the same page, and so a test can assert the page reached the bytes.
ASSUMPTIONS_PAGE_TITLE = "Assumptions: every assumption this pack rests on"
#: The library register's sub-heading. Named here for the same reason, and deliberately free of
#: an apostrophe: two of the four packs HTML-escape it, and a title a test cannot find in the
#: rendered bytes is a title nobody can prove reached the reader.
ASSUMPTIONS_LIBRARY_TITLE = "Assumption library entries cited by this run"
#: A real page break for any pipeline that paginates (print, md to PDF, md to HTML), plus a visible
#: rule for a reader looking at the raw Markdown. The owner asked for a FRESH PAGE, so the break is
#: part of the deliverable rather than a styling nicety.
_PAGE_BREAK = '<div style="break-before: page; page-break-before: always"></div>'


def _pipe_safe(text: str) -> str:
    """Record text inside a Markdown table cell: a bare pipe would end the cell."""
    return text.replace("|", "\\|")


def _assumption_row_markdown(row: AssumptionPageRow) -> str:
    """One row as a labelled block rather than a table line.

    Deliberately not a table: the basis, the citation and above all the confirmation ask run to
    several lines of prose, and a Markdown table cell cannot hold them without truncation. The ask
    is the one field on this page that must never be shortened, so the layout is chosen to fit it.
    """
    unit = f" {row.unit}" if row.unit else ""
    lines = [
        f"- **Value used:** {row.value}{unit}",
        f"- **Class on the record:** {row.label}",
    ]
    if row.asserted_by:
        lines.append(f"- **Asserted by:** {row.asserted_by}")
    lines.append(f"- **Published range:** {row.published_range}")
    lines.append(f"- **Source:** {row.source}")
    lines.append(f"- **Status:** {row.status}")
    lines.append(f"- **Direction of effect:** {row.direction} ({row.direction_basis})")
    lines.append(f"- **Confirmation ask:** {row.confirmation_ask}")
    if row.verbatim:
        lines.append(f"- **Full statement, verbatim from the record:** {row.verbatim}")
    body = "\n".join(lines)
    return f"#### `{row.key}`\n\n{body}"


def assumptions_page_markdown(page: AssumptionsPageView) -> str:
    """The consolidated assumptions page as Markdown, for the memo and the customer proposal.

    Every class gets its own sub-heading and its own lead, and an empty class gets an explicit
    "none recorded" line rather than no section at all: a reader must be able to tell "no pending
    permissions on this run" from "nobody looked".

    Passed through `no_dashes` here, at the point of use, because the memo is deliberately not
    scrubbed at document level (its warnings section is byte-for-byte the record's) and this page
    reproduces record prose. The proposal is scrubbed again downstream, which is idempotent.
    """
    parts = [
        _PAGE_BREAK,
        "---",
        f"# {ASSUMPTIONS_PAGE_TITLE}",
        f"**Which basis produced this pack.** {page.basis_statement}",
        f"**What this page could see.** {page.site_note}",
    ]
    for block in page.blocks:
        parts.append(f"## {block.title}")
        parts.append(block.lead)
        if block.rows:
            parts.extend(_assumption_row_markdown(row) for row in block.rows)
        else:
            parts.append(f"**None recorded:** {block.empty_note}")
        parts.append(f"*Source: {block.source}*")
    parts.append(f"## {ASSUMPTIONS_LIBRARY_TITLE}")
    if page.library_rows:
        parts.append(page.library_note)
        parts.append(
            "\n".join(
                [
                    "| Key | Value | Unit | Status | Confidence | Source |",
                    "|---|---|---|---|---|---|",
                    *(
                        f"| `{_pipe_safe(a.key)}` | {_pipe_safe(a.value)} | {_pipe_safe(a.unit)} "
                        f"| {_pipe_safe(a.status)} | {_pipe_safe(a.confidence)} "
                        f"| {_pipe_safe(a.source)} |"
                        for a in page.library_rows
                    ),
                ]
            )
        )
    else:
        parts.append(f"**None recorded:** {page.library_note}")
    parts.append(f"*{page.footnote}*")
    return no_dashes("\n\n".join(parts))


# --- D59: the markdown halves of the three new sections ---------------------------------------
#
# The owner's order (D59, Olly, 2026-08-18) puts the per-stream basis, the sizing surface and the
# methodology into every artifact, and two of the four are Markdown. They live here for the same
# reason `assumptions_page_markdown` does: the memo and the customer proposal must publish the same
# rows in the same words, and a copy of this logic in each renderer is a copy that drifts.

#: The heading the memo and the proposal share for the per-stream basis, so a reader moving between
#: them is looking at one register.
STREAM_ASSUMPTIONS_TITLE = "Assumptions behind each revenue stream"
#: Said where a stream shares statements with other streams. The citation numbers follow it at the
#: point of use. Display copy, so it lives here and not on the view model: the record carries the
#: note numbers; how a reader is told what they mean is this module's business.
STREAM_SHARED_NOTES_LEAD = (
    "This stream also carries statements it shares with other streams on this run. Each is printed "
    "ONCE, in full, in the numbered stream notes under the revenue register, with every stream it "
    "covers named. Citing it here rather than repeating it drops nothing"
)
#: ...and for the sizing surface and the selection.
SIZING_SURFACE_TITLE = "The full sizing surface, and how the selection was made"
#: ...and for the methodology.
METHODOLOGY_TITLE = "Methodology: how these figures were produced"

_NO_STREAM_ROWS_MD = (
    "**None recorded:** the record carries no value stream, so there is no per-stream basis to "
    "show. That is an absent result, not a set of streams that earned nothing."
)
_NO_SURFACE_MD = (
    "**None recorded:** the run record carries no evaluated sizing grid, so the searched space "
    "cannot be shown. That is an absent record, not an empty search."
)
_SURFACE_LEAD_MD = (
    "Every candidate size the search evaluated, one row per real solve. This is the whole searched "
    "space and not a selection from it. Nothing is interpolated between rows: a power and "
    "duration with no row here was never solved and no figure is claimed for it. The Stage column "
    "says which rows were CONFIRMED on the full half-hourly series and which were scored on a "
    "typical-period "
    "proxy to steer the search; only the first are reportable, and a size is never selected on the "
    "second."
)


def stream_assumptions_markdown(rows: tuple[StreamAssumptionRow, ...]) -> str:
    """The per-stream assumption register as Markdown, for the memo and the customer proposal.

    One block per stream, and three parts inside each: the figure, the assumption-library entries
    that stream's own dispatch cited, and the statements the engine wrote onto the stream. The
    approvals assumed under D57 are pulled out of the statements into their own list, because that
    is
    the owner's limb-4 requirement and because a reader must not have to find them among twenty
    other
    sentences.

    A stream that cites no key, or records no statement, says so. Losing a stream silently is the
    one
    failure this register cannot survive.
    """
    if not rows:
        return f"## {STREAM_ASSUMPTIONS_TITLE}\n\n{_NO_STREAM_ROWS_MD}"
    parts = [
        f"## {STREAM_ASSUMPTIONS_TITLE}",
        "For each revenue stream, the assumptions behind ITS OWN figure: the assumption-library "
        "entries that stream's dispatch cited, with the value and source of each, and then the "
        "statements the engine itself wrote onto the stream. The statements are where the rate "
        "actually used, the end of a range taken, the route assumed and the approvals assumed "
        "under "
        "D57 are recorded, and they are reproduced verbatim rather than summarised.",
    ]
    for row in rows:
        parts.append(f"### {stream_label(row.stream)}")
        parts.append(
            "\n".join(
                [
                    f"- **Annual revenue on the record:** {gbp(row.annual_revenue_gbp)}",
                    f"- **Counted in:** {row.counted_in}",
                    f"- **Firmness:** {titleise(row.firmness)}",
                    f"- **Gate on the record:** {row.gate_status}",
                ]
            )
        )
        if row.library:
            parts.append(
                "\n".join(
                    [
                        "| Assumption key | Value | Unit | Status | Source |",
                        "|---|---|---|---|---|",
                        *(
                            f"| `{_pipe_safe(a.key)}` | {_pipe_safe(a.value)} "
                            f"| {_pipe_safe(a.unit)} | {_pipe_safe(a.status)} "
                            f"| {_pipe_safe(a.source)} |"
                            for a in row.library
                        ),
                    ]
                )
            )
        else:
            parts.append(f"**No assumption key cited.** {row.keys_note}")
        if row.approvals:
            parts.append(
                "**Assumed approved for this stream under D57.** Not granted: the record says "
                "assumed approved, which is true, never granted, which would be a claim about a "
                "third party."
            )
            parts.append("\n".join(f"- {text}" for text in row.approvals))
        other = [text for text in row.statements if text not in row.approvals]
        if other:
            parts.append(
                "**Everything else this stream alone recorded about its own figure, verbatim.**"
            )
            parts.append("\n".join(f"- {text}" for text in other))
        elif not row.approvals and not row.shared_notes:
            parts.append(f"**No statement recorded.** {row.statements_note}")
        # CITED, never repeated: the numbered stream notes under the revenue register hold a shared
        # statement once, in full, naming every stream it covers.
        if row.shared_notes:
            numbers = ", ".join(str(number) for number in row.shared_notes)
            parts.append(f"{STREAM_SHARED_NOTES_LEAD}: {numbers}.")
    return no_dashes("\n\n".join(parts))


#: The heading the customer proposal gives the numbered stream notes. The capital-facing documents
#: render them under the revenue register in their own voice; the proposal had no notes block at
#: all, which is why this exists.
STREAM_NOTES_TITLE = "Why each of those figures reads as it does"
_STREAM_NOTES_LEAD_MD = (
    "Each note below is the modelling run's own statement about the streams named in it, "
    "reproduced in full and stated ONCE however many streams carry it. The per-stream register "
    "above cites them by number. A figure of zero is not one fact: the optimiser may have priced "
    "the stream and declined it, a permission may have held it out, or no dispatch may have been "
    "solved to measure it at all, and the note says which."
)


def stream_notes_markdown(notes: tuple[StreamNote, ...]) -> str:
    """The numbered, de-duplicated stream notes as Markdown, for the customer proposal.

    **Why the proposal needs its own copy.** The per-stream register cites a statement several
    streams share by its note number rather than repeating it, which is the standing anti-bloat
    invariant (`tests/test_outputs_stream_notes_reach_the_pack.py`). The memo, the HTML report and
    the consulting pack all render those notes under the revenue register, so the citation resolves
    inside the
    document. The proposal rendered no notes at all, so a citation there pointed at nothing: the
    statement was cited out of the document rather than into it. This closes that.

    Stated once, with every stream it covers named, exactly as the other three do.
    """
    if not notes:
        return ""
    parts = [f"## {STREAM_NOTES_TITLE}", _STREAM_NOTES_LEAD_MD]
    for note in notes:
        streams = ", ".join(stream_label(stream) for stream in note.streams)
        parts.append(f"**Note {note.number}. {streams}.** {note.text}")
    return no_dashes("\n\n".join(parts))


def _surface_row_markdown(point: SizingGridPoint, *, levered: bool, selected: bool) -> str:
    """One evaluated candidate as a Markdown table row. Absences stay absences.

    No per-candidate pound, for the reason `report_charts._sizing_table` records: a candidate's own
    £ is a figure two standing invariants forbid a pack from quoting, and the surface's job is the
    space searched and the objective ranked on.
    """
    z = point.levered_return_pct if levered else point.project_irr_pct
    return (
        f"| {kw(point.power_kw)} | {hours(point.duration_hours)} | {kwh(point.energy_kwh)} "
        f"| {'n/a' if z is None else pct(z)} "
        f"| {'n/a' if point.min_p90_dscr is None else ratio(point.min_p90_dscr)} "
        f"| {point.stage} | {'yes' if point.proxy else 'no'} "
        f"| {'SELECTED' if selected else ''} |"
    )


def sizing_surface_markdown(grid: SizingGrid | None) -> str:
    """The full evaluated sizing space plus the selection, as Markdown.

    The capital-facing half of the owner's "clearly shows the full sizing surface" requirement: a
    lender reading the memo gets the same enumeration the consulting pack draws, with the selection
    basis and the record's own selection reason beneath it, verbatim.
    """
    if grid is None or not grid.points:
        return f"## {SIZING_SURFACE_TITLE}\n\n{_NO_SURFACE_MD}"
    levered = any(point.levered_return_pct is not None for point in grid.points)
    chosen = grid.selected
    key = (
        None
        if chosen is None
        else (chosen.power_kw, chosen.duration_hours, chosen.energy_kwh, chosen.stage, chosen.proxy)
    )
    z_head = "Levered return" if levered else "Project return (unlevered)"
    rows = [
        _surface_row_markdown(
            point,
            levered=levered,
            selected=key is not None
            and (
                point.power_kw,
                point.duration_hours,
                point.energy_kwh,
                point.stage,
                point.proxy,
            )
            == key,
        )
        for point in grid.points
    ]
    confirmed = [point for point in grid.points if not point.proxy]
    parts = [
        f"## {SIZING_SURFACE_TITLE}",
        _SURFACE_LEAD_MD,
        "\n".join(
            [
                f"| Power | Duration | Energy | {z_head} "
                "| Min P90 DSCR | Stage | Proxy | Selected |",
                "|---|---|---|---|---|---|---|---|",
                *rows,
            ]
        ),
        "\n".join(
            [
                f"- **Candidate sizes evaluated:** {count(len(grid.points))}",
                f"- **Confirmed on the full half-hourly series:** {count(len(confirmed))}",
                f"- **Search version:** {grid.search_version}",
                f"- **Selection basis, verbatim:** {grid.selection_basis or 'not stated'}",
                f"- **Selection reason, verbatim:** {grid.selection_reason or 'not stated'}",
                f"- **Covenant the feasibility flag is measured against:** "
                f"{'not stated' if grid.covenant is None else ratio(grid.covenant)}",
            ]
        ),
    ]
    return no_dashes("\n\n".join(parts))


def methodology_markdown(view: MethodologyView) -> str:
    """The methodology section as Markdown, for the memo and the customer proposal.

    Fixed mechanism copy first, then this run's own recorded identifiers. The split is the point:
    the
    copy describes the engine and is identical in every pack, the rows are copies off this record,
    and
    nothing here asserts a solver, a stage or an objective the record does not name.
    """
    parts = [f"## {METHODOLOGY_TITLE}", view.lead]
    for heading, bullets in (
        ("How the battery is dispatched", view.dispatch),
        ("How the size is chosen", view.sizing),
        ("How the financial case is built", view.finance),
    ):
        parts.append(f"### {heading}")
        parts.append("\n".join(f"- {line}" for line in bullets))
    parts.append("### What this run actually did, from the record")
    if view.recorded:
        parts.append("\n".join(f"- **{label}:** {value}" for label, value in view.recorded))
    else:
        parts.append(f"**None recorded:** {view.empty_note}")
    parts.append(f"*{view.note}*")
    return no_dashes("\n\n".join(parts))
