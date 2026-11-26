"""The consulting-grade underwriting report — one self-contained HTML deliverable per run.

A strategy deliverable leads with the answer, then earns it: **executive summary → situation →
complication → resolution → evidence → what would change this**. The complication section is what
separates a credible deliverable from a brochure; it is written to be uncomfortable and nothing
here softens it.

**Every number comes from the run record.** This module formats values; it never computes one. It
reads the `OutputViewModel` (`gridq.outputs.viewmodel` — the one seam that touches the record) and
turns values into strings only through `gridq.outputs.formatting`. There is no arithmetic here at
all: no ``+``/``-``/``*``/``/``, no ``sum``/``round``/``min``/``max``/``abs``, no augmented
assignment. ``tests/test_consulting_report.py`` asserts that structurally over this module's AST,
so the rule holds by construction rather than by vigilance.

Two things the view model does not carry are read from the schema objects directly, because
nothing else exposes them: the site's own load / tariff / connection description (the Situation),
and the run's confidence band (`gridq.quotes.confidence`). Both are copied verbatim.

A figure the record does not carry renders as an explicit *unavailable* label naming why — never
blank, never zero, never a guess. That includes totals the view model aggregates to ``0.0`` over an
empty list: a floor with no streams behind it is *unavailable*, because ``£0.00`` reads as a priced
result of nothing. Output is deterministic: one record renders byte-identically every time.

Two conditions are loud enough to precede the numbers rather than qualify them, so both get a
banner and a place at the top of the caveat ladder: a record that is **not bankable**, and a record
whose **solver status is not a real solve** (`mock`/`baseline` are the integration-only engines;
`infeasible`/`insufficient_data` never produced a dispatch). The caveat ladder prints *every*
condition that fires, worst first — an earlier one never suppresses a later one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from html import escape
from itertools import count

from gridq.ingestion.improvement_asks import DataAsk
from gridq.optimisation.tariff_calendar import tariff_provenance_phrase
from gridq.outputs import formatting as fmt
from gridq.outputs import report_charts as charts
from gridq.outputs.viewmodel import (
    BEST_CANDIDATE_CAVEAT,
    BEST_CANDIDATE_HELD_OUT,
    BEST_CANDIDATE_IN_FLOOR,
    NO_CANDIDATE_COST_REASON,
    NO_FINANCIAL_CASE_REASON,
    AssumptionPageRow,
    AssumptionSourceRow,
    OutputViewModel,
    StreamAssumptionRow,
    StreamNote,
    StreamRow,
    build_view_model,
)
from gridq.quotes.confidence import ConfidenceAssessment
from gridq.schemas import DispatchProfile, QuoteRecord, SiteModel, SizingGrid

__all__ = [
    "ConfidenceView",
    "render_consulting_report",
    "render_publishable_pack",
    "render_report_for_quote",
]

# --- stated reasons for an absent figure (a blank is never acceptable) ---------------------

_NO_FIN = "the run record carries no value for this figure"
_NO_SITE = "no site model was supplied alongside the run record"
_NO_SPEC = "the run record carries no sized system"
_NO_DUOS = "no DUoS band rates on the site record"
_NO_MODEL = "the half-hourly series is not recorded as modelled"
_NO_FLOOR = "the run record carries no firm or contracted stream, so there is no floor to total"
_NO_UPSIDE = "the run record cites no conditional or gated stream in this quote mode"
_NO_BASIS = (
    "neither the run record's Gate-2 stamp nor the site record says how the half-hourly series "
    "was obtained"
)
_NO_BAND = (
    "no confidence band is recorded on this run: gridq.quotes.confidence was not run, or its "
    "result was not persisted, so evidential strength is unassessed"
)
_NO_FACTORS = "the run did not record which factors set the band"
_NO_METERING_POINT = "the site record does not name the metering point this pack prices"
_NO_BOUNDARY_NOTES = (
    "the site record states no scope note for this supply boundary, so what this pack does and "
    "does not cover is not written down"
)
#: Why the scope block leads Section 1. A verdict is about a perimeter, and a reader who does not
#: know the perimeter cannot use the verdict.
_SCOPE_NOTE = (
    "Every figure in this pack is about the supply below and nothing else. The boundary is the "
    "site record's own, reproduced verbatim."
)
_NO_CONSTRAINTS = "the run record names no binding constraint on the selected size"
_NO_CASE_FINDING = (
    "There is no financial case on this record at all, and its absence is not an economic verdict"
)
_ABSENT_CASE_NOTE = (
    "Every figure in this part of the pack is ABSENT, not zero and not a failure to clear a "
    "threshold. No system was costed, so nothing was evaluated to produce them. Read this panel "
    "as silence."
)
#: Appended ONLY when the record actually carries a measured candidate. Unconditionally, it pointed
#: at an exhibit that is absent on a record with no grid (the committed degraded example, any
#: pre-0.2.0 grid re-rendered through `gridq report`, any grid whose candidates are all
#: unpriceable), asserting that measured figures exist where none do: the same defect class this
#: work removes, one branch over.
_ABSENT_CASE_HAS_CANDIDATE = (
    " The measured economics this run does hold are the per-candidate figures stated above; read "
    "those as the evidence."
)
_BAND_NOTE = (
    "A band is evidential strength: how much weight this result can carry. Not a probability, "
    "not P50/P90, not a hurdle. It is capped by the weakest input the result depends on, so the "
    "binding factors are the only things that can raise it."
)
#: The BEFORE bill is a model, and the reader has to be told so on the row itself. It prices the
#: site's recorded half-hourly load against the tariff stack on the record; it is not the invoice,
#: and on this portfolio the two have diverged by up to 82%. Gate 3b tests exactly that gap, and
#: its verdict is reproduced verbatim in the warnings exhibit.
_BILL_MODELLED = (
    "A modelled figure, not an invoice: the site's recorded load priced on the tariff stack on the "
    "record. Whether it agrees with the customer's actual bill is what the Gate-3b reconciliation "
    "in the warnings tests, and that line is the record's own verdict on it."
)
#: The tariff exhibit prints rates without saying whose they are. The site record answers that in
#: its own words (`tariff_stack.tariff_name` / `.source`), and one site in this portfolio records a
#: commodity rate taken by analogy from a different site's meter. Rendering the record's statement
#: is the mark; inferring "borrowed" from free text would be a heuristic, and this pack does not
#: draw conclusions the record has not written down.
_NO_TARIFF_NAME = "the site record names no tariff"
_NO_TARIFF_SOURCE = "the site record states no source for this tariff stack"

#: "Floor" is a firmness classification on the record, and readers routinely mistake it for a
#: statement about who pays. Said once, where the word first appears.
_FLOOR_NOTE = (
    "“Priced floor” means firmness recorded as firm or contracted. It is a classification of the "
    "streams, not a claim that the revenue is contracted with a counterparty or hedged on price. "
    "Behind-the-meter savings sit in the floor and are still exposed to tariff and dispatch."
)
#: What the floor total means when the record marked its streams' values unavailable. The total is
#: a real sum of the recorded numbers and stays on the page; a reader simply must not read it as a
#: priced result, because the record says those numbers were never computed.
_FLOOR_UNMEASURED = (
    "READ THIS BEFORE THE FIGURE: the record marks every stream behind this total as “value "
    "unavailable (not invented)” because no system was selected and so no dispatch was priced for "
    "one. The total is the arithmetic sum of what the record holds, and what the record holds is "
    "absence. It is not a measurement that these streams earn nothing."
)

#: Leading labels the pricing path writes on the disclosures a run raises against itself. Matched
#: on the label, never on the body: the label is the record's signal, the body is its evidence.
_UNEVIDENCED_RETURN = "UNEVIDENCED RETURN COMPARISON"
_SIZING_VERDICT = "SIZING VERDICT"
#: The short chip shown in the tab title and the masthead. A named feasibility-study basis
#: prices normally-gated revenue into the priced floor, which the underwriting basis forbids. The
#: run states which basis on itself; every artifact must repeat it where a reader cannot miss it,
#: because the whole risk of a second basis is being mistaken for the first (CLAUDE.md §6,
#: D46/D55).
#:
#: Basis-agnostic on purpose: it read ``"STUDY BASIS (D46)"`` until 2026-08-18, which titled a D55
#: all-streams pack with the wrong basis number. The full label comes from the record
#: (`OutputViewModel.study_basis_label`); this is only the generic chip used where a title needs a
#: short form.
_STUDY_BASIS = "STUDY BASIS"
#: Said in the executive summary, in the reader's language rather than the record's. The record's
#: own warning is reproduced verbatim further down; this is the sentence that has to survive being
#: the only one a reader takes away. It names no individual stream — which streams are in the floor
#: differs by basis (D46: export only; D55: six streams and every award assumed), and the record is
#: what states that.
_STUDY_BASIS_CAVEAT = (
    "THIS IS A FEASIBILITY STUDY. The run was priced on a named study basis, which prices "
    "revenue the underwriting basis treats as gated or conditional into the priced floor and "
    "therefore into every revenue, cashflow and ratio below. The underwriting basis forbids "
    "exactly that. No figure in this pack may be presented as an underwriting result, and no site "
    "here holds the awards, consents, export MPAN or G99 approval the priced streams require."
)
#: Appended to the caveat only when the record carries the basis's own prose statement. A record
#: that names its basis by ``study_basis_id`` alone carries no such warning, and telling a reader a
#: verbatim reason is reproduced below when it is not is the same class of false claim as dropping
#: the caveat: `OutputViewModel.study_basis_note` is the field that says which case holds.
_STUDY_BASIS_VERBATIM_POINTER = (
    " The record's own statement of the basis, naming every stream it prices and every award it "
    "assumes, is reproduced verbatim in the warnings."
)
#: Said instead. It names the recorded id inline, because on this record the id IS the whole of the
#: record's statement and there is no section to point at.
_STUDY_BASIS_NO_VERBATIM = (
    " The record names the basis by its recorded basis id {basis_id} and carries no prose "
    "statement of it, so none is reproduced below."
)

#: Solver statuses that represent a real half-hourly dispatch solve. Anything else — including a
#: missing status — means no figure in the pack came from a solved dispatch, and the pack has to
#: say so before it shows a number.
_REAL_SOLVE = frozenset({"optimal", "feasible"})
_SOLVER_CONSEQUENCE: dict[str, str] = {
    "mock": (
        "the integration-only mock engine, which returns illustrative figures rather than a "
        "dispatch"
    ),
    "baseline": (
        "the integration-only baseline engine, which is not a half-hourly dispatch and whose "
        "numbers are never to be treated as real"
    ),
    # Read inside the parentheses `_solver_alarm` puts them in, so a colon here reads as intended.
    "infeasible": "an infeasible solve: no dispatch satisfies the constraints as recorded",
    "insufficient_data": "a refusal for insufficient data: no dispatch was attempted",
}


@dataclass(frozen=True)
class ConfidenceView:
    """The band, its ceiling and the factors that capped it — copied, never derived."""

    band: str | None
    ceiling: str | None = None
    binding_factors: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    @classmethod
    def unavailable(cls, reason: str = _NO_BAND) -> ConfidenceView:
        """No band on this run: say so, and say why."""
        return cls(band=None, unavailable_reason=reason)

    @classmethod
    def from_assessment(
        cls, assessment: ConfidenceAssessment, *, ceiling: str | None = None
    ) -> ConfidenceView:
        """Adapt a live `ConfidenceAssessment` (band plus the factors that set it)."""
        return cls(
            band=str(assessment.band),
            ceiling=ceiling,
            binding_factors=tuple(f"{f.name}: {f.reason}" for f in assessment.binding_factors),
        )

    @classmethod
    def from_record(cls, quote: QuoteRecord) -> ConfidenceView:
        """Read the band persisted on the record (N21). Absent ⇒ an honest unavailable."""
        if quote.confidence_band is None:
            return cls.unavailable()
        return cls(
            band=str(quote.confidence_band),
            ceiling=None if quote.confidence_ceiling is None else str(quote.confidence_ceiling),
            binding_factors=tuple(quote.confidence_binding_factors),
        )


# --- HTML helpers. Free text is escaped; ``*_html`` arguments are already-escaped markup. --


def _na(reason: str) -> str:
    """An absent figure, always carrying the reason it is absent."""
    return f'<span class="na">{escape(fmt.unavailable(reason))}</span>'


def _kv(label: str, value_html: str, note: str = "") -> str:
    tail = f'<div class="rownote">{escape(note)}</div>' if note else ""
    return f'<tr><th scope="row">{escape(label)}</th><td>{value_html}{tail}</td></tr>'


def _num(
    label: str,
    value: float | None,
    formatter: Callable[[float], str],
    reason: str = _NO_FIN,
    note: str = "",
) -> str:
    """One numeric row: the record's value formatted, or a reasoned *unavailable*."""
    if value is None:
        return _kv(label, _na(reason), note)
    return _kv(label, escape(formatter(value)), note)


def _txt(label: str, value: str | None, reason: str = _NO_FIN, note: str = "") -> str:
    """One text row: the record's value escaped, or a reasoned *unavailable*."""
    if value is None or value == "":
        return _kv(label, _na(reason), note)
    return _kv(label, escape(value), note)


def _kv_table(rows: Iterable[str]) -> str:
    return f'<table class="kv">{"".join(rows)}</table>'


def _cells(*values: str) -> list[str]:
    """One grid row's worth of escaped cells."""
    return [escape(v) for v in values]


def _grid(head: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A data table. Head cells are plain text; body cells are already-escaped markup."""
    head_html = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in row)}</tr>" for row in rows)
    return f'<div class="scroll"><table class="grid"><tr>{head_html}</tr>{body}</table></div>'


def _bullets(items: Iterable[str], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    return f"<{tag}>{''.join(f'<li>{escape(i)}</li>' for i in items)}</{tag}>"


def _p(text: str) -> str:
    return f"<p>{escape(text)}</p>"


@dataclass
class _Exhibits:
    """Sequential exhibit numbering — the only counter here. It never touches a record value."""

    seq: Iterator[int] = field(default_factory=lambda: count(1))

    def figure(self, finding: str, body_html: str, source: str) -> str:
        number = next(self.seq)
        return (
            '<figure class="ex"><figcaption>'
            f'<span class="exnum">Exhibit {number}</span>'
            f'<span class="exfind">{escape(finding)}</span></figcaption>'
            f'{body_html}<div class="src">Source: {escape(source)}</div></figure>'
        )


def _source(vm: OutputViewModel, path: str) -> str:
    return f"Run record {vm.provenance.quote_id} · {path}. Formatted, never computed."


# --- the brand surface: masthead, margin labels, footer -------------------------------------

#: The Neura mark: an orange rounded square carrying a white angular glyph, then the wordmark as
#: document text in the serif stack. Inline, with no ``xmlns`` and no external reference, so the
#: report stays one self-contained file and the wordmark degrades to whatever serif the reader has.
_LOGO_SVG = (
    '<svg class="mark" viewBox="0 0 28 28" role="img" aria-label="Neura Energy">'
    '<rect x="0" y="0" width="28" height="28" rx="6.5" fill="#E2551F"/>'
    '<polygon points="19,5 8,15.5 13.5,15.5 10,23 21,12.5 15.5,12.5" fill="#FFFFFF"/>'
    "</svg>"
)
#: Read from the header of the brand reference, which stamps every page the same way.
_DOC_KIND = "SITE FEASIBILITY VALIDATION"
#: Valid HTML: an ampersand followed by a space is not an ambiguous ampersand, so the literal the
#: brand reference prints is the literal in the file.
_CONFIDENTIAL = "PRIVATE & CONFIDENTIAL"
_WORDMARK_A = "Neura"
_WORDMARK_B = "Energy"


@dataclass(frozen=True)
class _SectionLabel:
    """One section's identity: anchor, roman numeral for the margin, ordinal and name.

    The margin label is the brand idiom (grey roman numeral over an orange small-caps name, sat in
    the left margin). The ordinal keeps the document's accessible heading text, which is also the
    name the section is known by across the repo, so the two never drift apart.
    """

    anchor: str
    numeral: str
    ordinal: str
    name: str


#: **The order is the owner's, verbatim** (D59, Olly, 2026-08-18): "starting with the IRR and
#: Revenue
#: breakdown and graphs, then the methodology and then the assumptions register for each site". The
#: value story leads, the method that produced it comes next, the register of what it assumed comes
#: third, and the evidence sections that used to open the pack move DOWN rather than out: a reader
#: who
#: wants the site context, the reasons the case could be wrong, or the run's identity finds all
#: three
#: below, in full, unchanged.
#:
#: Movement one is two sections rather than one, because the owner named an order inside it: "The
#: project IRR first, then revenue per stream, then the charts". Section I is the figures, section
#: II
#: is the charts that explain them; splitting them keeps the reading order the owner asked for
#: without
#: a single section running to forty exhibits.
_I = _SectionLabel("results", "I", "1", "Results: IRR and revenue")
_II = _SectionLabel(
    "graphs", "II", "2", "The graphs: dispatch, the sizing surface and the selection"
)
_III = _SectionLabel("methodology", "III", "3", "Methodology")
#: D56 rule 4 (owner, 2026-08-18): the dedicated assumptions page, on a fresh page of its own. It
#: sits
#: third on the owner's order. The per-key register in section VII answers a different question
#: (which
#: library entry priced this) from the one this page answers (what is this pack assuming, which way
#: does it push the answer, and what would settle it), which is why both exist.
_IV = _SectionLabel("assumptions", "IV", "4", "Assumptions register")
_V = _SectionLabel("situation", "V", "5", "Situation")
_VI = _SectionLabel("complication", "VI", "6", "Complication")
_VII = _SectionLabel("evidence", "VII", "7", "Evidence and provenance")
_VIII = _SectionLabel("change", "VIII", "8", "What would change this")


def _section(label: _SectionLabel, parts: Iterable[str], *, extra_class: str = "") -> str:
    """One section: its heading for assistive technology, its margin label, then its content.

    The visible label is the drawn brand object and is hidden from assistive technology, because the
    heading immediately above it already announces the section by name. The heading itself is
    visually hidden rather than deleted: sighted readers get the margin idiom, screen readers get a
    real ``h2`` outline, and neither gets the same words twice.
    """
    heading = f"{label.ordinal} · {label.name}"
    classes = f"sec {extra_class}" if extra_class else "sec"
    return "".join(
        [
            f'<section id="{escape(label.anchor)}" class="{escape(classes)}">',
            f'<h2 class="sechead">{escape(heading)}</h2>',
            '<div class="seclab" aria-hidden="true">',
            f'<span class="secnum">{escape(label.numeral)}</span>',
            f'<span class="secname">{escape(label.name)}</span></div>',
            *parts,
            "</section>",
        ]
    )


# --- narrative judgements: categorical facts on the record, never an authored figure -------


def _solver_alarm(vm: OutputViewModel) -> str | None:
    """Say — in the reader's language — that no figure here came from a solved dispatch.

    ``None`` when the record's solver status is a real solve. Categorical: it reads a recorded
    status token and returns fixed prose, never a figure.
    """
    status = vm.spec.solver_status
    if status is None:
        return (
            "The record carries no solver status, so nothing in this pack can be shown to have "
            "come from a solved half-hourly dispatch. Treat every figure as unsourced until it "
            "can."
        )
    if status in _REAL_SOLVE:
        return None
    detail = _SOLVER_CONSEQUENCE.get(status, "not a solved half-hourly dispatch")
    return (
        "The dispatch behind these numbers was not solved: the solver status on the record is "
        f"{fmt.titleise(status)} ({detail}). Every revenue, cashflow and ratio in this pack "
        "inherits that. They are the shape of an answer, not an answer."
    )


def _has_disclosure(vm: OutputViewModel, prefix: str) -> bool:
    """Whether the record raised the named disclosure against itself.

    A prefix test on the record's own warnings, not a search of their prose: the pricing path
    writes these disclosures with a fixed leading label, and the label is the signal. Reading the
    body would be inferring a conclusion the record did not write down.
    """
    return any(w.startswith(prefix) for w in vm.warnings)


def _no_viable_qualifier(vm: OutputViewModel) -> str:
    """What the record says about a search that selected nothing, attributed, never restated.

    Two things are separable here and the pack had merged them. That **no system was selected** is
    a recorded outcome. **Why** is a claim, and on these runs the record carries more than one
    answer to it: the engine's stated reason, a second reason recorded against the sizing grid, and
    a disclosure that no return figure was persisted for any candidate at all. This sentence
    therefore names the accounts and points at them; it never adopts one. If a later diagnosis
    settles which is the cause, nothing written here becomes false.
    """
    # Assembled with f-strings, never `+`: this module is guarded against any arithmetic operator
    # in its AST, and a `+` between two strings is indistinguishable from one between two figures.
    lead = (
        "The sizing search ran over the candidate space and selected no system. That much is on "
        "the record."
    )
    unevidenced = _has_disclosure(vm, _UNEVIDENCED_RETURN)
    two_accounts = _has_disclosure(vm, _SIZING_VERDICT) and vm.spec.selection_verdict_reason
    if unevidenced and two_accounts:
        return (
            f"{lead} Why it did is not settled by this record, and this pack does not settle it. "
            "The record holds two separate accounts of the verdict, the engine's and the sizing "
            "search's own, and it holds a disclosure of its own that no return figure was "
            "persisted for any candidate, so nothing here was measured against zero. All three "
            "are reproduced verbatim below. Read them before repeating any of them."
        )
    if unevidenced:
        return (
            f"{lead} Why it did is not settled by this record. The record discloses that no "
            "return figure was persisted for any candidate, so no candidate was compared against "
            "zero: treat the verdict as an outcome to diagnose, not an economic finding to "
            "publish. The record's stated reason is reproduced verbatim below."
        )
    if two_accounts:
        return (
            f"{lead} Why it did is given twice on this record, by the engine and by the sizing "
            "search, and the two accounts are reproduced verbatim below rather than merged into "
            "one sentence here. Read both before repeating either."
        )
    return (
        f"{lead} The reason is the engine's own, reproduced verbatim below. This pack states it "
        "as the record's account of the verdict, not as a finding of its own."
    )


def _recommendation(vm: OutputViewModel) -> tuple[str, str]:
    """The answer, first. A verdict, and the sentence that qualifies it."""
    unsolved = _solver_alarm(vm) is not None
    if vm.spec.selection_status == "no_viable_configuration":
        if unsolved:
            return (
                "Do not proceed, and do not quote the search as conclusive",
                "The sizing search returned no viable configuration, but the record's own solver "
                "status says the dispatch under that search was not solved. Act on the verdict; "
                "do not publish it. Re-run on a solved dispatch before anyone is told this site "
                "cannot take a battery.",
            )
        # The verdict is a record fact: the search ran and selected nothing. The CAUSE is not.
        # This branch used to assert "no configuration is economically viable on the priced value
        # floor" from `selection_status` alone, in packs whose own UNEVIDENCED_RETURN disclosure
        # says no return figure was persisted for any candidate, and whose sizing grid records a
        # second, incompatible reason. The pack now reports the accounts the record holds and
        # attributes each one, so it stays true whichever account turns out to be the real cause.
        return ("Do not proceed on these inputs", _no_viable_qualifier(vm))
    if unsolved:
        return (
            "Do not plan against these numbers: solve the dispatch first",
            "Every figure below is formatted faithfully from the record, and the record's own "
            "solver status says no half-hourly dispatch was solved to produce it. This pack "
            "therefore documents a run, not a case. Nothing here is evidence about this site "
            "until the dispatch is solved.",
        )
    if not vm.bankable:
        return (
            "Proceed to data and assumption work, not to capital",
            "The record does not present as bankable, so the economics below are indicative. Read "
            "them as the current state of a work-in-progress model, not as a forecast: the "
            "placeholder inputs and the gaps named in the Complication section can move any "
            "figure here, and nothing in the record bounds how far.",
        )
    if vm.study_basis:
        # The one branch a study basis had to change and did not. "Proceed to underwriting on the
        # priced floor" is the recommendation this pack must never make on a study basis: the floor
        # HERE contains revenue the underwriting basis holds out of it, so proceeding on this floor
        # is proceeding on a number that does not exist on the basis being proceeded to. The
        # companion sentence was false too, verbatim: merchant upside does NOT stay outside the case
        # on a study basis, it is the case.
        return (
            "Do not proceed to underwriting on this floor: reprice it on the underwriting basis",
            "The record presents as bankable, and it was priced on a named study basis, so its "
            "priced floor contains revenue the underwriting basis holds outside the floor "
            "altogether. The bankability stamp therefore attaches to a floor that does not exist "
            "on the basis a lender would underwrite. Reprice on the underwriting basis before any "
            "of this reaches capital; the basis banner above states which basis produced it.",
        )
    return (
        "Proceed to underwriting on the priced floor",
        "The record presents as bankable: the mandatory P50/P90 and DSCR deliverables are present "
        "and the run is stamped against the assumption version that priced it. The merchant "
        "upside stays outside the underwritten case.",
    )


def _caveats(vm: OutputViewModel, confidence: ConfidenceView) -> list[str]:
    """Every caveat on the fixed ladder that fires, worst first — none filtered.

    The ladder used to ``return`` at the first hit, so a covenant failure disappeared behind a
    not-bankable stamp and was never stated in the summary. Now the first is the lead and the rest
    are printed under it, in the same order.
    """
    out: list[str] = []
    # First on the ladder, above every other caveat: which basis produced these figures. It
    # governs how the whole pack may be used, so nothing may be read before it.
    if vm.study_basis:
        pointer = (
            _STUDY_BASIS_VERBATIM_POINTER
            if vm.study_basis_note is not None
            else _STUDY_BASIS_NO_VERBATIM.format(basis_id=vm.study_basis_id or "not recorded")
        )
        out.append(f"{vm.study_basis_label}. {_STUDY_BASIS_CAVEAT}{pointer}")
    if vm.spec.selection_status == "no_viable_configuration":
        out.append(
            vm.spec.selection_verdict_reason
            or "No configuration in the evaluated space is viable; no system is recommended."
        )
    alarm = _solver_alarm(vm)
    if alarm is not None:
        out.append(alarm)
    if not vm.bankable:
        out.append(
            f"This run is not bankable (status on record: {fmt.titleise(vm.bankable_status)}). "
            "Every figure in this pack is indicative and must not be presented to capital as "
            "underwritten."
        )
    if vm.dscr.covenant_passes is False:
        if vm.dscr.min_dscr is not None and vm.dscr.covenant is not None:
            out.append(
                f"The minimum {vm.dscr.basis} DSCR on the record is "
                f"{fmt.ratio(vm.dscr.min_dscr)} against a covenant of "
                f"{fmt.ratio(vm.dscr.covenant)}: it fails. On the debt terms as recorded this "
                "case does not service its debt; the capital structure, not the revenue stack, "
                "is the binding problem."
            )
        else:
            out.append(
                "The minimum DSCR on the record does not clear its covenant. The capital "
                "structure, not the revenue stack, is the binding problem."
            )
    if confidence.band is not None and confidence.band != "high":
        out.append(
            f"Confidence is {confidence.band.upper()}: the result may be right, but the evidence "
            "under it does not yet carry underwriting weight. See the binding factors."
        )
    # Decided and pinned (2026-08-15): the caveat fires on BOTH absent states, not only on "no
    # bill". A reader shown a bill with no saving needs the caveat at least as much as one shown
    # no bill at all, and `pending_reason` names which of the two applies.
    if not vm.bill_savings.after_side_priced:
        out.append(vm.bill_savings.pending_reason)
    if not out:
        out.append(
            "No caveat on this pack's fixed ladder fired on this record. That is not a clean bill "
            "of health: read the warnings in the Complication section in full before relying on "
            "any figure here. None of them has been filtered."
        )
    return out


def _largest_priced(vm: OutputViewModel) -> StreamRow | None:
    """The largest priced stream by its own recorded revenue.

    An ordering of record values, not a calculation: no figure is created, and the stable sort
    keeps the record's order on ties, so the choice is deterministic.
    """
    rows = list(vm.split.floor)
    if not rows:
        return None
    return sorted(rows, key=lambda r: r.annual_revenue_gbp, reverse=True)[0]


# --- sections ------------------------------------------------------------------------------


#: The headline's second clause, branched on the recorded half-hourly basis. It states what the
#: validation rests on and nothing more: a modelled basis is named as modelled on the front page,
#: never softened, and an unstamped basis says so rather than implying either.
_BASIS_CLAUSE: dict[str, str] = {
    "metered": "a battery feasibility validation from the site's own metered half-hourly data",
    "modelled": "a battery feasibility validation on a modelled load basis, stated as such",
    "mixed": "a battery feasibility validation on a mixed load basis, stated as such",
}
_BASIS_CLAUSE_ABSENT = "a battery feasibility validation with the load basis unrecorded"


def _stamped_basis(site: SiteModel | None, basis: str | None) -> str | None:
    """The basis the whole document reads from: the run's Gate-2 stamp, then the site's own."""
    if basis is not None:
        return basis
    if site is None:
        return None
    return site.load_data.half_hourly_basis


def _headline(vm: OutputViewModel, site: SiteModel | None, basis: str | None) -> str:
    """The serif headline: the site, then what the document is, as one sentence ending in a stop.

    A formula, not a claim. The site name is the record's; the clause after it is fixed per recorded
    basis, so the headline cannot say more about the evidence than the record stamped.
    """
    name = site.site_name if site is not None and site.site_name else vm.provenance.site_id
    if basis is None:
        clause = _BASIS_CLAUSE_ABSENT
    else:
        # A basis this map does not know is still a *recorded* basis. Naming it keeps the headline
        # from reading as "unrecorded", which would be the one claim the front page must not make.
        clause = _BASIS_CLAUSE.get(basis) or (
            f"a battery feasibility validation on a {fmt.titleise(basis).lower()} load basis, "
            "stated as such"
        )
    return f"{name}: {clause}."


def _site_label(vm: OutputViewModel, site: SiteModel | None) -> str:
    """The site's own name, falling back to the record's site id. Never a generic label."""
    if site is not None and site.site_name:
        return site.site_name
    return vm.provenance.site_id


def _basis_short_form(vm: OutputViewModel) -> str:
    """The basis's label cut to its leading phrase, e.g. ``"STUDY BASIS (D55)"``.

    The short form a browser tab, a saved filename and a masthead line can carry. Taken from the
    RECORD so a dual-basis pair is told apart by WHICH basis and not merely by the word "study";
    falls back to the generic chip when the record carries no label at all.

    It cuts on the comma left behind by `formatting.no_dashes` rather than on an em dash directly:
    every `StudyBasis.LABEL` joins its phrases with em dashes, house style admits no em dash in a
    rendered deliverable, and `test_outputs_no_em_dashes` sweeps this module's string literals as
    well as its output. Scrubbing first and cutting second needs no em dash in this file.
    """
    return fmt.no_dashes(vm.study_basis_label or _STUDY_BASIS).split(", ")[0]


def _doc_title(vm: OutputViewModel, site: SiteModel | None) -> str:
    """The browser-tab and saved-PDF title: which site, on which basis, from which run.

    All eight packs used to title identically but for the run id, so a dual-basis pair was
    indistinguishable by tab or by saved filename, which is precisely the confusion D46 warns
    about. The site comes first because that is what a reader scans for.
    """
    basis = f" · {_basis_short_form(vm)}" if vm.study_basis else ""
    return (
        f"{_site_label(vm, site)} · {_DOC_KIND.title()}{basis} · "
        f"Neura Energy · {vm.provenance.quote_id}"
    )


def _head(vm: OutputViewModel, site: SiteModel | None, basis: str | None) -> str:
    """The masthead: the mark and wordmark, the document stamp, and the headline sentence."""
    # The basis rides in the subtitle, beside the quote id, where every reader of the front page
    # sees it. It used to appear first at roughly line 140 of 1,070, inside a warning block.
    # The SHORT form plus this line's own qualifier, not the whole label: every `StudyBasis.LABEL`
    # already ends "NOT AN UNDERWRITING OUTPUT", so pasting the full label in front of the qualifier
    # printed that phrase twice in one subtitle. The short form names WHICH basis, which is the part
    # the label contributes that the qualifier cannot.
    study = (
        f" · <strong>{escape(_basis_short_form(vm))} - FEASIBILITY BASIS, "
        "NOT AN UNDERWRITING OUTPUT</strong>"
        if vm.study_basis
        else ""
    )
    return "".join(
        [
            '<header class="doc"><div class="masthead">',
            f'<div class="logo">{_LOGO_SVG}'
            f'<span class="wordmark"><span class="wm-a">{_WORDMARK_A}</span>'
            f'<span class="wm-b">{_WORDMARK_B}</span></span></div>',
            f'<div class="stamp"><div class="dockind">{_DOC_KIND} · '
            f"{escape(fmt.date_str(vm.provenance.created_at))}</div>"
            f'<div class="confidential">{_CONFIDENTIAL}</div></div>',
            "</div>",
            f"<h1>{escape(_headline(vm, site, basis))}</h1>",
            f'<div class="sub">Quote {escape(vm.provenance.quote_id)} · '
            f"{escape(fmt.titleise(vm.quote_mode))} · data tier "
            f"{escape(fmt.titleise(vm.data_tier))}{study}</div></header>",
        ]
    )


def _banner(vm: OutputViewModel) -> str:
    """The two banners a reader must not be able to miss: bankability, then whether it was solved.

    They are separate failure axes — a record can be stamped bankable and still carry a solver
    status that never solved anything — so neither is allowed to stand in for the other.
    """
    status = escape(fmt.titleise(vm.bankable_status))
    if vm.bankable:
        first = (
            '<div class="banner ok"><strong>BANKABLE CANDIDATE</strong><span>Status on record: '
            f"{status}. P50/P90, DSCR and the assumption-version stamp are all present."
            "</span></div>"
        )
    else:
        first = (
            '<div class="banner stop"><strong>NOT BANKABLE</strong><span>Status on record: '
            f"{status}. This document is indicative. Do not present it to capital as an "
            "underwritten case.</span></div>"
        )
    alarm = _solver_alarm(vm)
    if alarm is None:
        return first
    solver = "absent" if vm.spec.solver_status is None else fmt.titleise(vm.spec.solver_status)
    return (
        f'{first}<div class="banner stop"><strong>DISPATCH NOT SOLVED</strong><span>Solver status '
        f"on record: {escape(solver)}. No figure in this pack came from a solved half-hourly "
        "dispatch. See the caveats under the results.</span></div>"
    )


def _confidence_strip(confidence: ConfidenceView) -> str:
    if confidence.band is None:
        reason = confidence.unavailable_reason or _NO_BAND
        return (
            '<div class="conf"><div class="band none">CONFIDENCE NOT ASSESSED</div>'
            f'<div class="confbody"><p>{_na(reason)}</p>{_p(_BAND_NOTE)}</div></div>'
        )
    ceiling = (
        _p(f"Ceiling set by the half-hourly basis: {confidence.ceiling.upper()}.")
        if confidence.ceiling
        else ""
    )
    if confidence.binding_factors:
        capped = f"<p><strong>Capped by:</strong></p>{_bullets(confidence.binding_factors)}"
    else:
        capped = f"<p>{_na(_NO_FACTORS)}</p>"
    label = escape(confidence.band.upper())
    return (
        f'<div class="conf"><div class="band {escape(confidence.band)}">CONFIDENCE {label}</div>'
        f'<div class="confbody">{ceiling}{_p(_BAND_NOTE)}{capped}</div></div>'
    )


def _floor_note(vm: OutputViewModel) -> str:
    """The floor row's note, plus the record's own disclaimer when the total sums absences.

    The figure stays on the page: it is the record's sum and a reader is entitled to it. What it
    may not do is stand unqualified next to streams the record itself marked as never computed.
    """
    base = f"The sum of the record's own firm and contracted stream revenues. {_FLOOR_NOTE}"
    if vm.split.floor and not vm.split.floor_value_measured:
        return f"{base} {_FLOOR_UNMEASURED}"
    return base


def _floor_total(vm: OutputViewModel) -> float | None:
    """The recorded floor total — ``None`` when there is no stream behind it.

    The view model totals an empty list to ``0.0``. Printed as ``£0.00`` that reads as a priced
    result of nothing, which is the one thing this pack must never do: an absent figure is
    labelled, never zeroed. So an empty floor becomes an explicit unavailable.
    """
    return vm.split.floor_total_gbp if vm.split.floor else None


def _upside_total(vm: OutputViewModel) -> float | None:
    """The recorded upside total — ``None`` when no upside stream is cited (see `_floor_total`)."""
    return vm.split.upside_total_gbp if vm.split.upside else None


def _price_note(vm: OutputViewModel) -> str:
    """A price never stands alone: structure, target-return verdict, and the customer's own cost."""
    # A structure is a term of an offer. With no financial case the record has settled none, and
    # saying so plainly beats printing "Structure: unavailable (...)" as though a field were empty.
    parts = (
        ["No commercial structure is set on this record: there is no financial case behind it."]
        if vm.price.structure is None
        else [f"Structure: {fmt.structure_label(vm.price.structure)}."]
    )
    if vm.price.hits_target_return is None:
        parts.append("The record does not assert that this price hits the target return.")
    else:
        parts.append(f"Hits target return: {fmt.yes_no(vm.price.hits_target_return)}.")
    if vm.price.counterfactual_import_gbp_per_kwh is not None:
        parts.append(
            "Customer's own blended import cost on record: "
            f"{fmt.gbp_per_kwh(vm.price.counterfactual_import_gbp_per_kwh)}."
        )
    else:
        parts.append("No counterfactual import cost is on the record to compare it against.")
    return " ".join(parts)


def _scope_block(site: SiteModel | None) -> str:
    """What this pack covers and what it does not, in the site record's own words.

    Section 1 stated a verdict without ever stating its perimeter. Two packs in this portfolio
    cover one of a site's two supplies, or a grid meter carrying a small share of a
    largely self-supplied site, and a reader had to reach the connection notes to find that out.
    The site record already answers it on `supply_boundary`, so the answer is reproduced rather
    than composed: an authored summary of a scope is a claim, and a quoted one is a record.
    """
    if site is None:
        # `_na` returns markup and `_p` escapes its argument, so the two never compose: wrap in a
        # literal paragraph, exactly as the other absence branches in this module do.
        return (
            '<div class="caveat"><div class="caveatlab">What this pack covers</div>'
            f"<p>{_na(_NO_SITE)}</p></div>"
        )
    b = site.supply_boundary
    rows = [
        _txt("Import MPAN", b.import_mpan, "no import MPAN on the site record"),
        _txt("Metering point", b.metering_point, _NO_METERING_POINT),
    ]
    stated = _p(b.notes) if b.notes else f"<p>{_na(_NO_BOUNDARY_NOTES)}</p>"
    return (
        '<div class="caveat"><div class="caveatlab">What this pack covers</div>'
        f"{_p(_SCOPE_NOTE)}{_kv_table(rows)}{stated}</div>"
    )


def _answer_parts(
    vm: OutputViewModel, confidence: ConfidenceView, ex: _Exhibits, site: SiteModel | None = None
) -> list[str]:
    """The answer, first: the verdict, the band, the headline figures and the caveats.

    Returns the section's PARTS rather than a section, because the owner's order (D59) puts the
    headline figures and the revenue breakdown in one movement: the caller assembles both into
    section I so a reader meets the IRR and the streams that produced it without a section break
    between them.

    **The row order is the owner's instruction, not house style.** "The project IRR first, then
    revenue per stream": the IRR leads the panel, the totals follow it, and the per-stream register
    sits directly beneath. It used to open with the priced-floor total, which answered a question
    the owner had not asked first.
    """
    verdict, qualifier = _recommendation(vm)
    d = vm.dscr
    floor_note = _floor_note(vm)
    upside_note = "Conditional and gated streams as this quote mode cites them. Not underwritten."
    dscr_note = f"Covenant test: {fmt.pass_fail(d.covenant_passes)}."
    irr_note = "Unlevered. It does not reflect the debt terms in the returns panel below."
    rows = [
        _num("Project IRR (unlevered)", vm.returns.irr_pct, fmt.pct, _NO_FIN, irr_note),
        _num("Payback", vm.returns.payback_years, fmt.years),
        _num(
            f"Minimum DSCR ({d.basis} basis)",
            d.min_dscr,
            fmt.ratio,
            d.unavailable_reason or _NO_FIN,
            dscr_note,
        ),
        _num("Year-0 cashflow (P50)", vm.cashflows.year0_p50, fmt.gbp),
        _num(
            "Priced floor (firm + contracted)",
            _floor_total(vm),
            fmt.gbp,
            _NO_FLOOR,
            floor_note,
        ),
        _num("Merchant upside, held out", _upside_total(vm), fmt.gbp, _NO_UPSIDE, upside_note),
        _num(
            "Price to the customer",
            vm.price.value_gbp_per_kwh,
            fmt.gbp_per_kwh,
            note=_price_note(vm),
        ),
    ]
    # The clause after the verdict must not contradict it: only a solved run that selected a
    # system has a "case", and only then does it "rest on" anything.
    if not vm.split.floor:
        floor_phrase = "no firm or contracted stream is priced on this record"
    elif not vm.split.floor_value_measured:
        # The record wrote "value unavailable (not invented)" against these streams, so their
        # total is a sum of absences. Reporting it as a priced floor that "was not enough" made an
        # economic claim out of a figure the record says was never computed.
        floor_phrase = (
            "the floor streams carry no computed value on this record, so the total below is a "
            "sum of absences and not a priced result"
        )
    elif vm.spec.selection_status == "no_viable_configuration":
        floor_phrase = (
            f"the record's priced floor totals {fmt.gbp_compact(vm.split.floor_total_gbp)} a year "
            "against the candidates the search evaluated"
        )
    elif _solver_alarm(vm) is not None:
        floor_phrase = (
            "the unsolved run reports a priced floor of "
            f"{fmt.gbp_compact(vm.split.floor_total_gbp)} a year"
        )
    else:
        floor_phrase = (
            "the case rests on a priced floor of "
            f"{fmt.gbp_compact(vm.split.floor_total_gbp)} a year"
        )
    exhibit = ex.figure(
        f"{verdict}: {floor_phrase}",
        _kv_table(rows),
        _source(vm, "optimisation_result.value_streams, financial_case"),
    )
    caveats = _caveats(vm, confidence)
    lead, rest = caveats[0], caveats[1:]
    others = (
        (
            "<p><strong>Every other caveat this pack tests for that fired on this record, in the "
            f"same order:</strong></p>{_bullets(rest)}"
        )
        if rest
        else ""
    )
    return [
        f'<div class="answer"><div class="verdict">{escape(verdict)}</div>{_p(qualifier)}</div>',
        _scope_block(site),
        _confidence_strip(confidence),
        exhibit,
        '<div class="caveat"><div class="caveatlab">Read before any figure below</div>'
        f"{_p(lead)}{others}</div>",
    ]


def _basis_row(stamped: str | None) -> str:
    """The half-hourly basis: the stamp, what it is evidence of, or an honest unknown.

    Unknown is not modelled. The row used to print the words ``NOT RECORDED`` as though they were
    the value, under a note asserting the shape was a typical-site profile — a claim about data
    nobody had made.
    """
    if stamped is None:
        return _kv(
            "Half-hourly basis",
            _na(_NO_BASIS),
            "Unknown is not the same as modelled. Until the basis is stamped, nothing establishes "
            "what the load-derived figures in this pack are evidence about.",
        )
    if stamped == "metered":
        note = (
            "A validation stamp, not the series itself: the record states the shape came from the "
            "site's own meter, so these figures are evidence about this site."
        )
    else:
        note = (
            "Not the site's own meter. The record states the shape was obtained as "
            f"{fmt.titleise(stamped)}, so these figures are evidence about a site of this type, "
            "not about this one."
        )
    return _txt("Half-hourly basis", stamped.upper(), note=note)


def _load_rows(site: SiteModel, vm: OutputViewModel, stamped: str | None) -> list[str]:
    load = site.load_data
    window = (
        f"{fmt.date_str(load.start_date)} → {fmt.date_str(load.end_date)}"
        if load.start_date is not None and load.end_date is not None
        else None
    )
    return [
        _basis_row(stamped),
        _txt("Reference profile", load.half_hourly_reference_profile, _NO_MODEL),
        _txt("Scaled to", load.half_hourly_scaled_to, _NO_MODEL),
        _num("Annual consumption", load.annual_kwh, fmt.kwh),
        _num("Half-hourly intervals", load.half_hourly_rows, lambda v: f"{v:,.0f}"),
        _txt("Coverage window", window, "no half-hourly window on the site record"),
        _num("Peak import", load.max_import_kw, fmt.kw),
        _txt("Data tier", fmt.titleise(vm.data_tier)),
    ]


def _tariff_rows(site: SiteModel) -> list[str]:
    t = site.tariff_stack
    c = site.connection
    voltage = None if c.dno_region is None else f"{c.voltage_level or 'n/a'} · {c.dno_region}"
    imports = sorted(t.import_rates.items())
    return [
        _txt("Supplier", t.supplier, "no supplier on the site record"),
        # Whose tariff this is, in the record's own words, above the rates it produces. Without
        # these two rows the exhibit prints nine rates with nothing saying where they came from.
        _txt("Tariff basis", t.tariff_name, _NO_TARIFF_NAME, note=t.source or _NO_TARIFF_SOURCE),
        *[_num(f"Import rate · {k}", v, fmt.gbp_per_kwh) for k, v in imports],
        _num("DUoS red", t.duos_red_rate, fmt.gbp_per_kwh, _NO_DUOS),
        _num("DUoS amber", t.duos_amber_rate, fmt.gbp_per_kwh, _NO_DUOS),
        _num("DUoS green", t.duos_green_rate, fmt.gbp_per_kwh, _NO_DUOS),
        *[_num(f"Levy · {k}", v, fmt.gbp_per_kwh) for k, v in sorted(t.levies.items())],
        _num("MIC", c.mic_kw, fmt.kw),
        _num("ASC", c.asc_kw, fmt.kw, "no agreed supply capacity on the site record"),
        _num("Export capacity (MEC)", c.mec_kw, fmt.kw, "no export capacity on the site record"),
        _txt("Voltage / DNO region", voltage, "no DNO region on the site record"),
    ]


#: Consent / gate states that mean the item is not yet settled.
_UNSETTLED = frozenset({"unknown", "issue", "not_started", "applied", "rejected"})


def _boundary_finding(site: SiteModel) -> str:
    """What the record establishes about the metering boundary. A branch on a recorded flag."""
    behind = site.supply_boundary.behind_the_meter
    if behind is True:
        return "The battery is recorded as sitting behind the site's own supply meter"
    if behind is False:
        return "The record does not place the battery behind the site's own supply meter"
    return (
        "The metering boundary is unconfirmed on the record, so what sits behind the meter is not "
        "established"
    )


def _capacity_finding(site: SiteModel) -> str:
    """Where the connection capacity every headroom figure rests on actually came from.

    Three states, not two. A derived capacity is neither supplied nor the site's own peak standing
    in, and this caption asserted the first of those over Tanhouse's labelled derivation while the
    diagram one exhibit above printed "ASC not recorded". Collapsing the derived case into either
    of the other two swaps one false sentence for another.
    """
    if charts.capacity_is_derived(site):
        return (
            f"The {fmt.kw(site.connection.mic_kw)} import capacity behind every headroom figure "
            "here is a derived assumption, not a recorded agreed capacity"
        )
    if charts.capacity_is_supplied(site):
        return (
            f"The connection permits {fmt.kw(site.connection.mic_kw)} of import on the recorded "
            "agreed capacity"
        )
    return (
        "Connection capacity was never supplied for this site, so the recorded MIC is the site's "
        "own peak and a lower bound"
    )


def _site_exhibits(vm: OutputViewModel, site: SiteModel, ex: _Exhibits) -> str:
    """The two drawn site exhibits: the arrangement being underwritten, and the load's shape."""
    diagram = charts.system_diagram(
        site=site,
        spec=vm.spec,
        export_stream_on_record=bool(vm.split.upside),
    )
    curve = charts.load_duration_curve(site=site)
    return "".join(
        [
            ex.figure(
                _boundary_finding(site),
                diagram,
                _source(vm, "site.connection, site.supply_boundary, site.load_data"),
            ),
            ex.figure(
                _capacity_finding(site),
                curve,
                _source(vm, "site.load_data, site.connection"),
            ),
        ]
    )


def _situation(
    vm: OutputViewModel, site: SiteModel | None, ex: _Exhibits, basis: str | None
) -> str:
    if site is None:
        absent = ex.figure(
            "The site behind these numbers is not described on this run",
            f"<p>{_na(_NO_SITE)}</p>",
            _source(vm, "site model (absent)"),
        )
        return _section(_V, [absent])
    # The record's Gate-2 stamp is authoritative; the site's own field is the fallback.
    stamped = _stamped_basis(site, basis)
    if stamped is None:
        load_finding = "How the load shape under every number here was obtained is not recorded"
    elif stamped == "metered":
        load_finding = "The record stamps the load shape as the site's own metered half-hourly data"
    else:
        load_finding = (
            "The load shape under every number here is "
            f"{fmt.titleise(stamped)}, not this site's own meter"
        )
    stack = site.tariff_stack
    tariff_finding = (
        # Never "the tariff on file" unqualified: it reads as the site's own contract, and one site
        # here is priced on a commodity rate taken by analogy from a different site's meter. R8
        # (WP-UW-07 acceptance 5): whose rates these are is rendered from the record's typed
        # provenance by the one phrase that may say "own tariff stack", and only when it is.
        "Every delivered-cost figure in this pack is priced on the tariff stack below, and the "
        f"site is {tariff_provenance_phrase(stack)}"
        if stack.duos_red_rate is not None or stack.network_tariff is not None
        # Never "DUoS value is unpriced": the record may well price a DUoS stream (the results
        # shows it). What is true is narrower and worse — whatever it priced, it did not price
        # from this site's own bands.
        else "No DUoS band rates are on the site record, so any DUoS value priced in the "
        "results was not priced from this site's own tariff"
    )
    gates = vm.consents_gates
    states = (*gates.consents, *gates.gates)
    gate_body = (
        _grid(("Consent / gate", "State"), [_cells(k, fmt.titleise(v)) for k, v in states])
        if gates.available
        else f"<p>{_na(_NO_SITE)}</p>"
    )
    gate_finding = (
        "Delivery depends on consents and physical gates that are not all resolved"
        if any(v in _UNSETTLED for _, v in states)
        else "Every consent and physical gate on the record is resolved"
    )
    return _section(
        _V,
        [
            _p(
                "This is the site as the record describes it: what it consumes, how that "
                "consumption is known, what it pays for power, and what the connection permits. "
                "Every figure in the results above is downstream of these facts."
            ),
            _site_exhibits(vm, site, ex),
            ex.figure(
                load_finding,
                _kv_table(_load_rows(site, vm, stamped)),
                _source(vm, "site.load_data"),
            ),
            ex.figure(
                tariff_finding,
                _kv_table(_tariff_rows(site)),
                _source(vm, "site.tariff_stack, site.connection"),
            ),
            ex.figure(gate_finding, gate_body, _source(vm, "site.consents, site.physical_gates")),
        ],
    )


def _lean_rows(vm: OutputViewModel) -> list[Sequence[str]]:
    # A bare "within it" next to a £0.0000/kWh price reads as a competitive verdict, so the price
    # itself travels in the same cell as the comparison.
    priced = (
        "no price on record"
        if vm.price.value_gbp_per_kwh is None
        else fmt.gbp_per_kwh(vm.price.value_gbp_per_kwh)
    )
    price_state = (
        _na("the record carries no counterfactual import cost")
        if vm.price.exceeds_counterfactual is None
        else escape(
            f"{priced} (exceeds it)" if vm.price.exceeds_counterfactual else f"{priced} (within it)"
        )
    )
    price_note = (
        _na("the record carries no counterfactual import cost")
        if vm.price.counterfactual_import_gbp_per_kwh is None
        else escape(
            "Blended import cost on record: "
            f"{fmt.gbp_per_kwh(vm.price.counterfactual_import_gbp_per_kwh)}. "
            "Being under the customer's cost is not the same as earning the target return. See "
            "the target-return row in the results."
        )
    )
    dscr_note = (
        escape(vm.dscr.unavailable_reason)
        if vm.dscr.unavailable_reason
        else escape(f"Covenant {fmt.pass_fail(vm.dscr.covenant_passes)} on the recorded test.")
    )
    # "Not established" alone contradicts the warnings below, which name placeholder keys one by
    # one. The register cannot be built; that is not the same as knowing nothing.
    register_note = (
        escape("See the assumptions register: a status per key.")
        if vm.assumption_sources
        else _na(
            "the record's streams cite no assumption keys, so no per-key register can be built; "
            "the warnings below name the assumptions the run itself flagged"
        )
    )
    solver = escape(fmt.titleise(vm.spec.solver_status)) if vm.spec.solver_status else _na(_NO_SPEC)
    alarm = _solver_alarm(vm)
    solver_note = escape(
        f"Implementation level: {fmt.titleise(vm.spec.implementation_level or 'not recorded')}."
        if alarm is None
        else alarm
    )
    sourced = "sourced" if vm.assumption_sources else "no per-key register"
    return [
        [*_cells("Assumption provenance", sourced), register_note],
        _cells(
            "Debt terms",
            "on record" if vm.debt.available else "absent",
            # "On record" and the seam note read as a contradiction side by side. They are not:
            # the terms exist, what is missing is any basis for them being this counterparty's.
            "Terms being on the record is not the same as their being the right terms for this "
            f"counterparty. {vm.counterparty.rate_tenor_note}"
            if vm.debt.available
            else vm.counterparty.rate_tenor_note,
        ),
        [*_cells("DSCR", "measured" if vm.dscr.available else "unavailable"), dscr_note],
        _cells(
            "Customer bill before/after",
            # "Modelled" against a row named before/after claims both sides were modelled. On a
            # BEFORE-only record only one was, and the state cell is the reader's index into
            # whether the comparison exists at all.
            "modelled"
            if vm.bill_savings.after_side_priced
            else ("before side only" if vm.bill_savings.available else "not modelled"),
            vm.bill_savings.basis or vm.bill_savings.pending_reason,
        ),
        [
            *_cells("Solver"),
            solver,
            solver_note,
        ],
        [*_cells("Price vs the customer's own cost"), price_state, price_note],
    ]


def _complication(vm: OutputViewModel, confidence: ConfidenceView, ex: _Exhibits) -> str:
    if vm.warnings:
        warn_body = _bullets(vm.warnings, ordered=True)
        warn_finding = "The run recorded warnings against itself, and every one is reproduced here"
    else:
        warn_body = _p(
            "The run recorded no warnings. Treat that as an absence of evidence rather than "
            "evidence of absence: a clean warning list on placeholder inputs means the checks did "
            "not fire, not that the case is sound."
        )
        warn_finding = "The run recorded no warnings, which is itself worth checking"
    band_line = (
        _p(
            f"Confidence is {confidence.band.upper()} and cannot rise until the binding factors "
            "in the results change. Strength elsewhere does not compensate: the band is "
            "a minimum across factors, by design."
        )
        if confidence.band is not None and confidence.binding_factors
        else ""
    )
    alarm = _solver_alarm(vm)
    solver_line = _p(alarm) if alarm is not None else ""
    # The covenant failure used to live only as a table-cell "FAIL". It is the loudest thing on
    # the record and belongs in prose, in the section that exists to say uncomfortable things.
    if vm.dscr.covenant_passes is False and vm.dscr.min_dscr is not None:
        against = (
            ""
            if vm.dscr.covenant is None
            else f" against a covenant of {fmt.ratio(vm.dscr.covenant)}"
        )
        covenant_line = _p(
            f"The debt does not cover. Minimum {vm.dscr.basis} DSCR on the record is "
            f"{fmt.ratio(vm.dscr.min_dscr)}{against}. No revenue assumption in this pack repairs "
            "that on its own: the shortfall is in the capital structure."
        )
    else:
        covenant_line = ""
    if alarm is not None:
        lean_finding = "The case leans on a dispatch that was never solved"
    elif not vm.debt.available:
        lean_finding = "The case leans on debt terms that are not on the record"
    elif not vm.assumption_sources:
        lean_finding = "The case leans on assumptions whose per-key provenance is not on the record"
    else:
        lean_finding = "The case leans on inputs that are not all established"
    return _section(
        _VI,
        [
            _p(
                "This section exists to be read against the recommendation. Everything below is a "
                "reason the numbers in this pack could be wrong, stated at the strength the "
                "record supports. Nothing has been softened or omitted for length."
            ),
            solver_line,
            covenant_line,
            band_line,
            ex.figure(
                lean_finding,
                _grid(
                    ("What the case leans on", "State on record", "What that means"), _lean_rows(vm)
                ),
                _source(vm, "financial_case, optimisation_result, view model"),
            ),
            ex.figure(
                warn_finding, warn_body, _source(vm, "quote.warnings, verbatim and unfiltered")
            ),
        ],
    )


def _stream_rows(rows: Sequence[StreamRow], counted_in: str) -> list[Sequence[str]]:
    """One group's rows, each carrying the group it belongs to.

    Floor, upside and excluded streams share one table; without the ``counted_in`` column a reader
    cannot tell which of them the totals underneath actually add up.
    """
    return [
        _cells(
            fmt.stream_label(r.stream),
            fmt.gbp(r.annual_revenue_gbp),
            counted_in,
            fmt.titleise(r.firmness),
            r.gate_status or "n/a",
            r.unlock_condition or "n/a",
            # Not "No": a status of None means no consent was RECORDED, which is not the same as
            # the customer declining. The neutral cell keeps the pack from asserting either.
            "Yes" if r.consent_status == "given" else "n/a",
            # Which zero this is. A revenue cell reading GBP 0.00 is the place a reader most needs
            # the record's own account, and it was the one cell that carried none.
            r.figure_basis_cell,
        )
        for r in rows
    ]


#: See `html_report._STREAM_NOTES_LEAD`: same fact, this document's voice. The consulting pack puts
#: the reason under the exhibit it qualifies rather than in a warnings appendix, because a zero in
#: the register is unreadable without it.
_STREAM_NOTES_LEAD = (
    "Each note below is the run record's own statement against the streams named in it, "
    "reproduced in full and stated once however many streams carry it. The register's Basis of "
    "figure column cites them by number. A revenue of zero is not one fact: the optimiser may "
    "have priced the stream and declined it, a gate may have held it out of the priced floor, or "
    "no dispatch may have been solved to measure it at all."
)


def _stream_notes(notes: Sequence[StreamNote]) -> str:
    """The de-duplicated stream notes, each naming the streams it covers.

    Stated once rather than once per stream: a run that selects no system writes the same
    paragraph of dispatch arithmetic onto every stream, and reproducing it per row would repeat
    one 250-word paragraph eight times in a pack already reviewed as bloated. The saving is real
    disclosure, not omission: every distinct warning still appears here in full.

    Excluded streams keep a register row in this document, but the notes name their own streams
    regardless, so the block stays complete where the other two artifacts drop those rows.
    """
    if not notes:
        return ""
    items = "".join(
        f'<li><span class="notenum">Note {escape(str(note.number))}</span> '
        f"<strong>{escape(', '.join(fmt.stream_label(s) for s in note.streams))}.</strong> "
        f"{escape(note.text)}</li>"
        for note in notes
    )
    return f'{_p(_STREAM_NOTES_LEAD)}<ol class="streamnotes">{items}</ol>'


def _spec_exhibit(vm: OutputViewModel, ex: _Exhibits) -> str:
    s = vm.spec
    if s.selection_status == "no_viable_configuration":
        return ex.figure(
            "No configuration in the evaluated space is viable, so none is recommended",
            _p(
                s.selection_verdict_reason
                or "The sizing search evaluated candidates and none is economically viable."
            ),
            _source(vm, "optimisation_result.selection_status"),
        )
    # Power, energy and duration state an arithmetic claim: two of them multiply to the third. The
    # chart module picks the precision on which they do, so the panel cannot print a product that
    # fails a reader's calculator (the same rule the evaluated-points table now holds).
    power_str, energy_str, duration_str = charts.spec_triple(
        s.selected_power_kw, s.selected_energy_kwh, s.duration_hours
    )
    sized = s.selected_power_kw is not None and s.selected_energy_kwh is not None
    if not sized:
        finding = "The record carries no sized system, so there is nothing to recommend"
    elif _solver_alarm(vm) is not None:
        # An unsolved run selects nothing. Say "carries", not "recommends".
        finding = (
            f"The record carries a {power_str} / {energy_str} system, selected without a solved "
            "dispatch"
        )
    else:
        finding = f"The recommended system is {power_str} / {energy_str}"
    status = None if s.selection_status is None else fmt.titleise(s.selection_status)
    binding = ", ".join(s.constraints_binding) if s.constraints_binding else None
    rows = [
        _txt("Power", power_str, _NO_SPEC),
        _txt("Energy", energy_str, _NO_SPEC),
        _txt("Duration", duration_str, _NO_SPEC),
        _txt("Selection status", status, "the record does not carry a sizing verdict"),
        _txt(
            "Binding constraints",
            binding,
            _NO_CONSTRAINTS,
            note="What limited the chosen size, verbatim from the run: connection, throughput or "
            "energy. An empty list is the record naming none, not proof that none binds.",
        ),
    ]
    return ex.figure(finding, _kv_table(rows), _source(vm, "optimisation_result"))


def _best_candidate_exhibit(vm: OutputViewModel, ex: _Exhibits) -> str:
    """What the best evaluated candidate MEASURED, stated where the reader meets the zeros.

    A no-spec pack's stream register reads 0.00 on every row and "Not measured" in every basis cell,
    which is true of the top-level record and false as an impression: the run's own sizing grid
    holds the candidate figures, and on the 2026-08-15 campaign they were non-zero on a dispatch
    that discharged. Until now the only place they appeared was the record's own CANDIDATE_REVENUE
    warning, 42% of the way down a 110 KB document inside the Complication block, and they appeared
    nowhere at all in the customer proposal.

    The exhibit sits immediately after the specification panel, so the sentence "no system is
    recommended" and the sentence "here is what the best size measured" are read together and
    neither can be lifted without the other. `BestCandidateView.headline` carries "not a
    recommendation" into the caption for the same reason.

    Renders nothing at all when `vm.best_candidate` is ``None`` (a run that selected a system, or a
    grid with no measured candidate): an empty block would be a claim about a search this record
    does not describe.
    """
    bc = vm.best_candidate
    if bc is None:
        return ""
    # The same precision rule the spec panel and the evaluated-points table are held to: the power
    # and duration printed here must reproduce the energy printed here on a reader's calculator.
    power_str, energy_str, duration_str = charts.spec_triple(
        bc.power_kw, bc.energy_kwh, bc.duration_hours
    )
    if bc.covers_cost is None:
        cost_note = (
            "A cost line was not recorded on the grid for this candidate, so no revenue-vs-cost "
            "comparison is made. An unrecorded cost line is not a cost of zero."
        )
    elif bc.covers_cost:
        cost_note = (
            "The measured floor COVERS the recorded year-1 cost stack. That is an operating "
            "margin, not a return: capex, debt service and tax are not in it, and the search still "
            "selected no system."
        )
    else:
        cost_note = (
            "The measured floor does NOT cover the recorded year-1 cost stack, before any capex, "
            "debt service or tax. This is the measured form of the negative verdict above."
        )
    figures = _kv_table(
        [
            _txt("Candidate power", power_str, _NO_SPEC),
            _txt("Candidate energy", energy_str, _NO_SPEC),
            _txt("Candidate duration", duration_str, _NO_SPEC),
            _num(
                "Priced-floor revenue measured",
                bc.annual_revenue_gbp,
                fmt.gbp,
                _NO_FLOOR,
                "Firm and contracted streams only, verbatim from the candidate's own cashflow "
                "model. It is not the sum of the register below, which also lists held-out "
                "streams.",
            ),
            _num(
                "Recorded operating cost",
                bc.annual_operating_cost_gbp,
                fmt.gbp,
                NO_CANDIDATE_COST_REASON,
            ),
            _num(
                "Recorded dispatch cost",
                bc.annual_dispatch_cost_gbp,
                fmt.gbp,
                NO_CANDIDATE_COST_REASON,
            ),
            _num(
                "Recorded cost, year 1", bc.annual_cost_total_gbp, fmt.gbp, NO_CANDIDATE_COST_REASON
            ),
            _num(
                "Revenue less recorded cost",
                bc.net_annual_gbp,
                fmt.gbp,
                NO_CANDIDATE_COST_REASON,
                cost_note,
            ),
            _txt(
                "Confirmed candidates measured",
                f"{bc.confirmed_candidate_count}",
                "the grid records no confirmed candidate count",
            ),
        ]
    )
    rows = [
        _cells(
            fmt.stream_label(r.stream),
            fmt.gbp(r.annual_revenue_gbp),
            BEST_CANDIDATE_IN_FLOOR if r.in_priced_floor else BEST_CANDIDATE_HELD_OUT,
            fmt.titleise(r.firmness),
            r.gate_status or "n/a",
        )
        for r in (*bc.floor_streams, *bc.held_out_streams)
    ]
    register = (
        _grid(("Stream", "Annual revenue", "Counted in", "Firmness", "Gate"), rows)
        if rows
        else _p(
            "This candidate's dispatch reported no value streams on the grid, which is silence "
            "rather than a set of zeros."
        )
    )
    body = "".join(
        [
            _p(BEST_CANDIDATE_CAVEAT),
            figures,
            _p(
                "Per stream. A floor row at zero was priced and the optimiser declined it, which "
                "is a measurement; a held-out row was never in the floor to begin with and its "
                "figure is added to nothing here."
            ),
            register,
        ]
    )
    return ex.figure(
        bc.headline(fmt.gbp),
        body,
        _source(vm, "quote.sizing_grid.points (confirmed, non-proxy)"),
    )


def _dispatch_exhibits(
    vm: OutputViewModel,
    site: SiteModel | None,
    dispatch_profile: DispatchProfile | None,
    sizing_grid: SizingGrid | None,
    ex: _Exhibits,
) -> str:
    """The two drawn engine exhibits: the solved day, then the space that day was chosen from."""
    alarm = _solver_alarm(vm)
    if dispatch_profile is None:
        firming_finding = (
            "No solved dispatch day is on this record, so when the battery would carry the site is "
            "unevidenced"
        )
    elif site is not None and charts.capacity_is_derived(site):
        firming_finding = (
            "The solved day is shown without a capacity ceiling, because this site's import "
            "capacity is a derived assumption rather than a recorded one"
        )
    elif site is None or not charts.capacity_is_supplied(site):
        firming_finding = (
            "The solved day is shown without a capacity ceiling, because no connection capacity "
            "was supplied for this site"
        )
    else:
        firming_finding = (
            "On the recorded solved day the battery's contribution is shown against load, because "
            "the run did not record a grid-import series"
        )
    if sizing_grid is None or not sizing_grid.points:
        sizing_finding = (
            "No evaluated sizing grid is on this record, so where the search looked cannot be shown"
        )
    elif sizing_grid.selection_basis == "unlevered":
        sizing_finding = (
            "The selected size was chosen on an unlevered basis: no debt structure was resolved, "
            "so no candidate was tested against the covenant"
        )
    elif sizing_grid.selection_basis == "indicative_fallback":
        sizing_finding = (
            "The selected size is an indicative fallback: no evaluated candidate met the covenant"
        )
    elif sizing_grid.selected is None:
        sizing_finding = "The search evaluated candidates and marked none of them selected"
    else:
        sizing_finding = (
            f"The search evaluated {sizing_grid.search_space_size} candidate sizes and selected "
            f"{fmt.kw(sizing_grid.selected.power_kw)} at "
            f"{fmt.hours(sizing_grid.selected.duration_hours)}"
        )
    if sizing_grid is None or not sizing_grid.points:
        surface_finding = (
            "No evaluated sizing grid is on this record, so the searched space cannot be enumerated"
        )
        selection_finding = (
            "No evaluated sizing grid is on this record, so how the size was selected cannot be "
            "shown"
        )
    else:
        confirmed = [point for point in sizing_grid.points if not point.proxy]
        surface_finding = (
            f"The search evaluated {fmt.count(len(sizing_grid.points))} candidate sizes and every "
            "one of them is listed below"
        )
        if not confirmed:
            selection_finding = (
                "No candidate was confirmed on the full half-hourly series, so no ranking of "
                "confirmed candidates can be shown"
            )
        elif sizing_grid.selected is None:
            selection_finding = (
                f"{fmt.count(len(confirmed))} candidates were confirmed on the full series and the "
                "record marks none of them selected"
            )
        else:
            selection_finding = (
                f"The selected size is the top of a ranking of {fmt.count(len(confirmed))} "
                "candidates confirmed on the full half-hourly series"
            )
    return "".join(
        [
            ex.figure(
                firming_finding,
                charts.firming_diagram(profile=dispatch_profile, site=site, solver_note=alarm),
                _source(vm, "quote.dispatch_profile, site.connection"),
            ),
            ex.figure(
                sizing_finding,
                charts.sizing_map(
                    grid=sizing_grid,
                    constraints_binding=vm.spec.constraints_binding,
                    solver_note=alarm,
                ),
                _source(vm, "quote.sizing_grid, optimisation_result.constraints_binding"),
            ),
            # The owner asked for the sizing surface to be legibly FULL rather than summarised, so
            # the
            # map is followed by every evaluated candidate as a row. The map shows the shape of the
            # space; only the table states what candidate N actually scored.
            ex.figure(
                surface_finding,
                charts.sizing_surface(grid=sizing_grid),
                _source(vm, "quote.sizing_grid.points, every evaluation"),
            ),
            # ...and by the ranking that turned that space into one answer.
            ex.figure(
                selection_finding,
                charts.milp_selection(grid=sizing_grid, solver_note=alarm),
                _source(
                    vm,
                    "quote.sizing_grid.points (confirmed), selection_basis, selection_reason",
                ),
            ),
        ]
    )


def _value_parts(vm: OutputViewModel, ex: _Exhibits) -> list[str]:
    """The figures: the returns panel, then revenue per stream, then the cashflow series.

    Section I's second half, in the owner's order (D59): the IRR panel answers "what does it
    return", the register answers "out of what", and the per-stream assumption blocks answer "on
    what basis, stream by stream". Returned as parts so the caller can put the headline answer above
    them inside one section.
    """
    biggest = _largest_priced(vm)
    stack_finding = (
        f"{fmt.stream_label(biggest.stream)} is the largest priced stream at "
        f"{fmt.gbp_compact(biggest.annual_revenue_gbp)} a year"
        if biggest is not None
        else "No stream is priced into the floor on this record"
    )
    stack_rows = [
        *_stream_rows(vm.split.floor, "Priced floor"),
        *_stream_rows(vm.split.upside, "Merchant upside"),
        *_stream_rows(vm.split.excluded, "Excluded"),
    ]
    # The chart answers the proportion question a register cannot; the register stays beneath it for
    # the gate, unlock and consent columns a bar has nowhere to put.
    stack_chart = charts.revenue_stack(
        floor=vm.split.floor,
        upside=vm.split.upside,
        excluded=vm.split.excluded,
    )
    stack_register = _grid(
        (
            "Stream",
            "Annual revenue",
            "Counted in",
            "Firmness",
            "Gate",
            "Unlock condition",
            "Consent",
            "Basis of figure",
        ),
        stack_rows,
    )
    # The lead sentence goes ABOVE the register, at the point of misreading. Every row of a no-spec
    # register prints 0.00 and "Not measured", which is true of this record and false as an
    # impression: the grid beside it holds measured candidate figures. Stated here as well as in the
    # exhibit because the register is what a reader's eye lands on, and the sentence is authored
    # once on the view model so the memo and the HTML report cannot word it differently.
    stack_lead = "" if vm.best_candidate is None else _p(vm.best_candidate.register_lead(fmt.gbp))
    stack_body = (
        f"{stack_lead}{stack_chart}{stack_register}{_stream_notes(vm.split.stream_notes)}"
        if stack_rows
        else f"<p>{_na('the record carries no value streams')}</p>"
    )
    totals = _kv_table(
        [
            _num(
                "Priced floor (firm + contracted)",
                _floor_total(vm),
                fmt.gbp,
                _NO_FLOOR,
                _floor_note(vm),
            ),
            _num(
                "Merchant upside (conditional + gated)",
                _upside_total(vm),
                fmt.gbp,
                _NO_UPSIDE,
                "As this quote mode cites them: a gated stream the mode does not cite has no row "
                "above and is not in this total.",
            ),
        ]
    )

    # The customer's own bill moved to section II with the chart it is drawn beside: it answers
    # "what
    # does this do to the invoice", which is a different question from "what does the project
    # return",
    # and the owner's order puts the second first.
    cf = vm.cashflows
    if cf.available:
        cf_body = _grid(
            ("Period", "P50 free cashflow", "P90 free cashflow"),
            [
                [escape(f"Year {i}"), escape(fmt.gbp(p50)), escape(fmt.gbp(p90))]
                for i, (p50, p90) in enumerate(zip(cf.p50, cf.p90, strict=False))
            ],
        )
        # Never "a -£250.0k outlay" — a negative outlay reads as an inflow. State the signed
        # cashflow as a cashflow.
        cf_finding = (
            f"Year 0 is {fmt.gbp_compact(cf.year0_p50 or 0.0)} on the P50 series, and "
            f"{cf.operating_years} operating years have to repay it"
        )
    elif not vm.returns.case_available:
        # The whole financial case is absent, and the reason is structural: no system was costed,
        # so no cashflow was ever built. "Not yet a deliverable" reads as a case that fell short of
        # a standard; this one was never run. The distinction is the difference between an economic
        # verdict and an absence, and only the record can say which this is.
        absent_note = _ABSENT_CASE_NOTE
        if vm.best_candidate is not None:
            absent_note = f"{absent_note}{_ABSENT_CASE_HAS_CANDIDATE}"
        cf_body = f"<p>{_na(NO_FINANCIAL_CASE_REASON)}</p>{_p(absent_note)}"
        cf_finding = _NO_CASE_FINDING
    else:
        cf_body = f"<p>{_na('the record carries no P50/P90 cashflow series')}</p>"
        cf_finding = "No cashflow series is on the record, so this case is not yet a deliverable"

    tenor = vm.counterparty.rate_tenor_note
    hits = vm.price.hits_target_return
    dscr_note = f"Basis {vm.dscr.basis}. Covenant {fmt.pass_fail(vm.dscr.covenant_passes)}."
    returns_rows = [
        _num(
            "Project IRR (unlevered)",
            vm.returns.irr_pct,
            fmt.pct,
            _NO_FIN,
            "Unlevered: it is the return on the whole project, before the debt below. No levered "
            "or equity return appears anywhere in this pack.",
        ),
        _num("Payback", vm.returns.payback_years, fmt.years),
        _num(
            "Minimum DSCR",
            vm.dscr.min_dscr,
            fmt.ratio,
            vm.dscr.unavailable_reason or _NO_FIN,
            dscr_note,
        ),
        _num("Covenant threshold", vm.dscr.covenant, fmt.ratio, "no covenant is on the record"),
        _num("Gearing", vm.debt.gearing_pct, fmt.pct, tenor),
        _num("Debt rate", vm.debt.rate_pct, fmt.pct, tenor),
        _num("Tenor", vm.debt.tenor_years, fmt.years, tenor),
        _txt(
            "Price hits target return",
            None if hits is None else fmt.yes_no(hits),
            "the record does not assert a target-return verdict",
        ),
    ]
    if not vm.returns.case_available:
        # Ahead of the DSCR branches, which all presuppose a case. "The returns below stand alone"
        # asserts that returns exist; on a record with no `financial_case` at all, every row of the
        # panel is absent and the panel would otherwise read as a case that was evaluated and came
        # out empty. It was never evaluated, and the two must not look the same.
        returns_finding = _NO_CASE_FINDING
    elif vm.dscr.min_dscr is None:
        returns_finding = "No DSCR is measured on this record, so the returns below stand alone"
    elif vm.dscr.covenant_passes is False and vm.dscr.covenant is not None:
        returns_finding = (
            f"Minimum {vm.dscr.basis} DSCR is {fmt.ratio(vm.dscr.min_dscr)} against a "
            f"{fmt.ratio(vm.dscr.covenant)} covenant: the debt does not cover"
        )
    elif vm.dscr.covenant_passes is False:
        returns_finding = (
            f"Minimum {vm.dscr.basis} DSCR is {fmt.ratio(vm.dscr.min_dscr)} and fails its "
            "covenant: the debt does not cover"
        )
    elif vm.dscr.covenant_passes is None:
        returns_finding = (
            f"Minimum {vm.dscr.basis} DSCR is {fmt.ratio(vm.dscr.min_dscr)}, but no covenant test "
            "was evaluated against it"
        )
    else:
        returns_finding = (
            f"Minimum {vm.dscr.basis} DSCR is {fmt.ratio(vm.dscr.min_dscr)} and clears its "
            "covenant on the recorded debt terms"
        )
    return [
        _p(
            "What the record returns, and what it is recorded as earning to return it. Each figure "
            "is lifted from the run record and formatted, never recomputed here. Read every one of "
            "them against the Complication section below."
        ),
        ex.figure(
            returns_finding,
            _kv_table(returns_rows),
            _source(vm, "financial_case.irr / payback / dscr_summary / debt_assumptions"),
        ),
        ex.figure(
            stack_finding,
            f"{stack_body}{totals}",
            _source(vm, "optimisation_result.value_streams, gated_upside"),
        ),
        # The owner's explicit ask: for EACH stream, the assumptions behind ITS figure. It sits
        # directly under the register so a reader meets a stream's pound and its basis together.
        _stream_assumptions_exhibit(vm, ex),
        ex.figure(cf_finding, cf_body, _source(vm, "financial_case.p50_cashflows / p90_cashflows")),
    ]


#: The columns of the per-stream assumption table. Fixed here so the four artifacts cannot drift
#: into
#: four ideas of what a reader is owed about the basis of one stream's pound.
_STREAM_ASSUMPTION_COLUMNS = ("Assumption key", "Value used", "Unit", "Status", "Source")

#: Said once above the per-stream blocks. It states what the blocks are FOR, because a reader who
#: has
#: just met a revenue register will otherwise read them as a repeat of it.
_STREAM_ASSUMPTIONS_LEAD = (
    "For each revenue stream in the register above, the assumptions behind ITS OWN figure: the "
    "assumption-library entries that stream's dispatch cited, with the value and source of each, "
    "and then the statements the engine itself wrote onto the stream. The statements are where the "
    "rate actually used, the end of a range taken, the route assumed and the approvals assumed "
    "under D57 are recorded, and they are reproduced verbatim rather than summarised."
)
_STREAM_APPROVALS_LEAD = (
    "Assumed approved for this stream under D57. Not granted: the record says assumed approved, "
    "which is true, never granted, which would be a claim about a third party."
)
_STREAM_STATEMENTS_LEAD = (
    "Everything else this stream alone recorded about its own figure, verbatim."
)
_NO_STREAM_ROWS = (
    "The record carries no value stream, so there is no per-stream basis to show. That is an "
    "absent result, not a set of streams that earned nothing."
)


def _stream_assumption_block(row: StreamAssumptionRow) -> str:
    """One stream: its figure, the library entries it cited, and its own statements.

    Three parts because the record holds three different kinds of evidence about one pound, and
    flattening them would let a cited library value and an engine-written assumption read as the
    same class of thing. An empty part states its emptiness; none is silently dropped.

    Rendered as a titled block rather than as its own numbered exhibit, deliberately: twelve streams
    would otherwise insert twelve exhibit numbers into the middle of one figure, and the pack's
    exhibit numbering is contiguous by contract (`test_exhibits_are_numbered_contiguously`).
    """
    library = (
        _grid(
            _STREAM_ASSUMPTION_COLUMNS,
            [_cells(a.key, a.value, a.unit, a.status, a.source) for a in row.library],
        )
        if row.library
        else _p(row.keys_note)
    )
    approvals = f"{_p(_STREAM_APPROVALS_LEAD)}{_bullets(row.approvals)}" if row.approvals else ""
    other = tuple(text for text in row.statements if text not in row.approvals)
    if other:
        statements = f"{_p(_STREAM_STATEMENTS_LEAD)}{_bullets(other)}"
    elif row.approvals or row.shared_notes:
        statements = ""
    else:
        statements = _p(row.statements_note)
    # A statement several streams carry is CITED, not repeated: the numbered note under the revenue
    # register holds it once, in full, naming every stream it covers. Repeating it per stream is the
    # bloat those notes exist to prevent, and it is pinned against
    # (tests/test_outputs_stream_notes_reach_the_pack.py).
    shared = (
        _p(
            f"{fmt.STREAM_SHARED_NOTES_LEAD}: "
            f"{', '.join(str(number) for number in row.shared_notes)}."
        )
        if row.shared_notes
        else ""
    )
    figures = _kv_table(
        [
            _txt("Annual revenue on the record", fmt.gbp(row.annual_revenue_gbp)),
            _txt("Counted in", row.counted_in),
            _txt("Firmness", fmt.titleise(row.firmness)),
            _txt("Gate on the record", row.gate_status),
            _txt("Assumption keys cited", fmt.count(len(row.library))),
            _txt("Approvals assumed under D57", fmt.count(len(row.approvals))),
        ]
    )
    heading = (
        f"{fmt.stream_label(row.stream)}: {fmt.gbp(row.annual_revenue_gbp)} a year, "
        f"{row.counted_in.lower()}"
    )
    return (
        f'<div class="grouplab">{escape(heading)}</div>'
        f"{figures}{library}{approvals}{statements}{shared}"
    )


def _stream_assumptions_exhibit(vm: OutputViewModel, ex: _Exhibits) -> str:
    """The per-stream assumption register: one block per stream, in the record's own order.

    The owner asked for this explicitly ("the assumptions made for each of the streams should be
    shown") and until now it was scattered: a stream's cited keys appeared in one anonymous table of
    the whole run's keys, and its own statements appeared as numbered footnotes under the revenue
    register. Neither told a reader what was behind ONE stream's figure.
    """
    if not vm.stream_assumptions:
        return ex.figure(
            f"{fmt.STREAM_ASSUMPTIONS_TITLE}: none, because the record carries no value stream",
            _p(_NO_STREAM_ROWS),
            _source(vm, "optimisation_result.value_streams (absent)"),
        )
    blocks = "".join(_stream_assumption_block(row) for row in vm.stream_assumptions)
    return ex.figure(
        # Opens with the shared title so the same register is findable by one string in all four
        # artifacts, then states the count so a reader can check no stream was dropped.
        f"{fmt.STREAM_ASSUMPTIONS_TITLE}: all {fmt.count(len(vm.stream_assumptions))} of them, "
        "each with the assumptions behind its own figure",
        f"{_p(_STREAM_ASSUMPTIONS_LEAD)}{blocks}",
        _source(vm, "optimisation_result.value_streams[] + gated_upside[], per stream"),
    )


def _methodology(vm: OutputViewModel, ex: _Exhibits) -> str:
    """How the dispatch, the sizing search and the finance model work, and what THIS run did.

    Section III on the owner's order. The prose comes from `MethodologyView` so all four
    artifacts describe one machine in one set of words; the rows are this run's own recorded
    identifiers, copied.
    """
    m = vm.methodology
    recorded = (
        _kv_table([_txt(label, value) for label, value in m.recorded])
        if m.recorded
        else f"<p>{_na(m.empty_note)}</p>"
    )
    recorded_finding = (
        f"This run's own record of what the method did, in {fmt.count(len(m.recorded))} recorded "
        "fields"
        if m.recorded
        else "This record names none of the solver, search or dispatch identifiers"
    )
    return _section(
        _III,
        [
            _p(m.lead),
            ex.figure(
                "The battery's operation is decided half hour by half hour, on this site's own "
                "series",
                _bullets(m.dispatch),
                _source(vm, "method: gridq.optimisation (MILP dispatch)"),
            ),
            ex.figure(
                "The size is chosen by solving candidates, never by interpolating between them",
                _bullets(m.sizing),
                _source(vm, "method: gridq.sizing (two-stage search)"),
            ),
            ex.figure(
                "The financial case is built from the solved dispatch, in one direction only",
                _bullets(m.finance),
                _source(vm, "method: gridq.finance (cashflow, DSCR, returns)"),
            ),
            ex.figure(recorded_finding, recorded, _source(vm, "quote record, verbatim")),
            _p(m.note),
        ],
    )


def _graphs(
    vm: OutputViewModel,
    ex: _Exhibits,
    site: SiteModel | None = None,
    dispatch_profile: DispatchProfile | None = None,
    sizing_grid: SizingGrid | None = None,
) -> str:
    """The drawn exhibits behind section I: the system, the dispatch day, the surface, the ranking.

    Section II on the owner's order, and its content is the four charts the owner named plus the
    two panels that make them readable: the specification the charts explain, and the customer's own
    bill before and after. Nothing here computes; every figure is a display copy and every geometry
    is over recorded values.
    """
    bill = vm.bill_savings
    no_peak = "no dispatch-derived peak is on the record"
    dispatch_rows = [
        _num("Peak import before", bill.peak_before_kw, fmt.kw, no_peak),
        _num("Peak import after", bill.peak_after_kw, fmt.kw, no_peak),
        _num("Bill before", bill.bill_before_gbp, fmt.gbp, bill.pending_reason, _BILL_MODELLED),
        _num("Bill after", bill.bill_after_gbp, fmt.gbp, bill.pending_reason),
        _num(
            "Annual saving", bill.annual_savings_gbp, fmt.gbp, bill.pending_reason, bill.basis or ""
        ),
    ]
    if bill.after_side_priced:
        bill_finding = (
            "The customer's own saving is on the record, with the basis it was modelled on"
        )
    elif bill.available:
        # Never "the customer's delivered cost today is on the record". The BEFORE bill is the
        # site's recorded load priced on the tariff stack on file: a MODEL of the delivered cost,
        # not the invoice. On this portfolio the two differ by up to 82%, and the Gate-3b line in
        # the warnings is the record's own account of that gap. A caption that calls the model the
        # customer's actual cost states the one thing the reconciliation exists to test.
        bill_finding = (
            "The delivered cost below is modelled from the site's recorded load and the tariff on "
            "the record, not read off an invoice, and no system is recommended, so there is no "
            "after-battery bill and no saving to set against it"
        )
    else:
        bill_finding = (
            "No customer bill is on this record, so the saving the customer would actually see "
            "is unevidenced"
        )
    return _section(
        _II,
        [
            _p(
                "The four exhibits the owner asked for, in order: how the battery was dispatched "
                "on "
                "a real solved day, the whole searched sizing space, the ranking that picked one "
                "point out of it, and what the customer's bill does either side of it. The "
                "per-stream revenue breakdown is drawn in section I, beside the register it "
                "belongs to."
            ),
            _spec_exhibit(vm, ex),
            # Immediately after the specification panel, so "no system is recommended" and "here is
            # what the best size measured" are read as one thought. Empty string on a run that
            # selected a system.
            _best_candidate_exhibit(vm, ex),
            _dispatch_exhibits(vm, site, dispatch_profile, sizing_grid, ex),
            ex.figure(
                bill_finding,
                f"{charts.bill_columns(bill=bill)}{_kv_table(dispatch_rows)}",
                _source(vm, "customer_bill"),
            ),
        ],
    )


def _evidence(vm: OutputViewModel, ex: _Exhibits) -> str:
    if vm.assumption_sources:
        reg_body = _grid(
            ("Key", "Value", "Unit", "Status", "Confidence", "Source"),
            [
                _cells(a.key, a.value, a.unit, a.status, a.confidence, a.source)
                for a in vm.assumption_sources
            ],
        )
        reg_finding = "Each load-bearing assumption is shown with its status and its source"
    else:
        no_keys = _na(
            "the record's streams cite no assumption keys, so no per-key register can be built "
            "from this run"
        )
        reg_caveat = _p(
            "That is a gap in traceability, not evidence the run used nothing unconfirmed: the "
            "warnings reproduced in the Complication section name the assumptions the run "
            "itself flagged as "
            "placeholder or needing review."
        )
        reg_body = f"<p>{no_keys}</p>{reg_caveat}"
        reg_finding = (
            "No per-key assumption register can be built from this run, so no figure here can be "
            "traced to the entry that priced it"
        )

    if vm.sensitivities:
        not_run = "this scenario did not evaluate the figure"
        sens_body = _grid(
            ("Scenario", "Min DSCR", "IRR", "COD delay", "Note"),
            [
                [
                    *_cells(r.label),
                    escape(fmt.ratio(r.min_dscr)) if r.min_dscr is not None else _na(not_run),
                    escape(fmt.pct(r.irr_pct)) if r.irr_pct is not None else _na(not_run),
                    *_cells(f"{r.cod_delay_months} months", r.note or "n/a"),
                ]
                for r in vm.sensitivities
            ],
        )
        sens_finding = "The result has been re-run under scenarios and the spread is on record"
    else:
        no_sens = _na("no scenario sensitivities are persisted on this record")
        single = _p(
            "The case has been run once, at one set of inputs. A single point is not a range, and "
            "nothing in this pack should be read as one."
        )
        sens_body = f"<p>{no_sens}</p>{single}"
        sens_finding = "The case has been run once, so there is no sensitivity spread to show"

    p = vm.provenance
    no_version = "no assumption version was stamped, so there is nothing to reprice against"
    expiry = None if p.expiry_date is None else fmt.date_str(p.expiry_date)
    prov_rows = [
        _txt("Quote id", p.quote_id),
        _txt("Run mode", fmt.titleise(p.quote_mode)),
        _txt("Site id", p.site_id),
        _txt("Input hash", p.input_hash, "no input hash was stamped on this run"),
        _txt("Assumption version", p.assumption_version, no_version),
        _txt("Code version", p.code_version, "no code version was stamped on this run"),
        _txt("Schema version", p.schema_version),
        _txt("Expiry", expiry, "no expiry date is on the record"),
        _txt("Created", fmt.date_str(p.created_at)),
    ]
    cp = vm.counterparty
    credit = None if cp.credit_status is None else fmt.titleise(cp.credit_status)
    cp_rows = [
        _txt("Legal name", cp.legal_name, _NO_SITE),
        _txt("Company number", cp.company_number, _NO_SITE),
        _txt("Sector", cp.sector, _NO_SITE),
        _txt("Credit standing", credit, _NO_SITE),
        _num("Expected tenure", cp.expected_tenure_years, fmt.years, _NO_SITE),
        _kv("Rate & tenor", _na(cp.rate_tenor_note)),
    ]
    cp_finding = (
        f"{cp.legal_name} carries this contract, and nothing in this pack prices its credit"
        if cp.legal_name
        else "No counterparty is named on this run, so nothing here is attached to a payer"
    )
    unstamped = [
        name
        for name, value in (
            ("input hash", p.input_hash),
            ("assumption version", p.assumption_version),
            ("code version", p.code_version),
        )
        if value is None
    ]
    # "Reproducible" is a claim the stamps do not yet support (the input hash pins neither the
    # half-hourly content nor the assumption content). Claim only what is on the record: the
    # stamps are present, so the run can be identified.
    prov_finding = (
        f"The run cannot be pinned down: no {', no '.join(unstamped)} is stamped on it"
        if unstamped
        else "Every figure in this pack traces to one run, stamped with hash, assumptions, code "
        "and schema"
    )
    return _section(
        _VII,
        [
            _p(
                "Nothing above is legible without this section: what was assumed, what was "
                "sourced, what was placeheld, and the identifiers that pin this pack to one "
                "specific run."
            ),
            ex.figure(
                reg_finding, reg_body, _source(vm, "assumption library at the stamped version")
            ),
            ex.figure(sens_finding, sens_body, _source(vm, "quote.scenario_sensitivities")),
            ex.figure(
                cp_finding,
                _kv_table(cp_rows),
                _source(vm, "site.counterparty"),
            ),
            ex.figure(
                prov_finding,
                _kv_table(prov_rows),
                _source(vm, "quote provenance spine"),
            ),
        ],
    )


_BANKABLE_WORK = (
    "Confirm every assumption the run flagged as placeholder or needs_review, then reprice "
    "against the confirmed release.",
    "Establish real debt terms (gearing, rate, tenor) so DSCR and the covenant test are measured "
    "rather than indicative.",
    "Persist the P50/P90 cashflows, the DSCR summary and the assumption-version stamp: without "
    "all three the record cannot present as bankable.",
    "Re-run under Low/Central/High scenarios so the pack carries a spread, not a point.",
)


# --- VII. the dedicated assumptions page (D56 rule 4) ----------------------------------------

#: The page's columns. Fixed here so the four artifacts cannot drift into four different ideas of
#: what a reader is owed about an assumption.
_ASSUMPTION_COLUMNS = (
    "Key",
    "Value used and class",
    "Source or published range",
    "Status",
    "Direction of effect",
    "Confirmation ask",
)


def _assumption_status_cell(row: AssumptionPageRow) -> str:
    """The status cell, carrying the asserter where the record names one.

    The asserter travels with the assertion (D54): a commitment the owner attests and one
    transcribed from a DNO contract are the same shape on the record, and the only thing that keeps
    them from reading the same way on the page is printing who says so.
    """
    if not row.asserted_by:
        return escape(row.status)
    return f"<strong>Asserted by:</strong> {escape(row.asserted_by)}<br>{escape(row.status)}"


def _assumption_source_cell(row: AssumptionPageRow) -> str:
    """The source cell: the published range first, then the citation, then any full statement."""
    tail = (
        f"<br><em>Full statement, verbatim from the record: {escape(row.verbatim)}</em>"
        if row.verbatim
        else ""
    )
    return (
        f"<em>Range:</em> {escape(row.published_range)}<br>"
        f"<em>Source:</em> {escape(row.source)}{tail}"
    )


def _assumption_grid(rows: tuple[AssumptionPageRow, ...]) -> str:
    """One class of assumption as a table. Every cell is a display copy of the record."""
    return _grid(
        _ASSUMPTION_COLUMNS,
        [
            [
                f"<code>{escape(row.key)}</code>",
                f"{escape(row.value)} {escape(row.unit)}<br><em>{escape(row.label)}</em>",
                _assumption_source_cell(row),
                _assumption_status_cell(row),
                f'<span class="dirflag">{escape(row.direction)}</span><br>'
                f"{escape(row.direction_basis)}",
                escape(row.confirmation_ask),
            ]
            for row in rows
        ],
    )


def _assumptions(vm: OutputViewModel, ex: _Exhibits) -> str:
    """Every assumption the run rests on, on a page of its own, classed and never merged.

    The owner's standing rules (CLAUDE.md 2.0) take the favourable end of a cited range and assume
    awards and consents granted. This page is what makes that admissible rather than merely
    optimistic: the optimism lives in the value, the candour lives in the label, and a reader sees
    both in one place, with the direction each assumption pushes the answer and the single piece of
    evidence that would settle it.

    An empty class prints an explicit *none recorded*, never nothing at all. "No pending permission
    on this run" and "nobody looked" are different facts about a case and a reader is entitled to
    tell them apart.
    """
    page = vm.assumptions_page
    parts = [
        # The page's own name, the same string in all four artifacts, so a reader moving between
        # them is looking at one page and not four differently-titled attempts at it.
        _p(f"{fmt.ASSUMPTIONS_PAGE_TITLE}."),
        _p(f"Which basis produced this pack. {page.basis_statement}"),
        _p(f"What this page could see. {page.site_note}"),
    ]
    for block in page.blocks:
        count = len(block.rows)
        if count:
            finding = f"{block.title}: {count} recorded"
            body = f"{_p(block.lead)}{_assumption_grid(block.rows)}"
        else:
            finding = f"{block.title}: none recorded"
            # "None recorded" rather than the report's *unavailable* label: an empty class is not a
            # figure the record failed to carry, it is an evidenced emptiness, and the two must not
            # read the same way. The reason travels with it either way.
            body = (
                f"{_p(block.lead)}<p><strong>None recorded.</strong> {escape(block.empty_note)}</p>"
            )
        parts.append(ex.figure(finding, body, block.source))
    if page.library_rows:
        lib_finding = (
            f"{fmt.ASSUMPTIONS_LIBRARY_TITLE}: {len(page.library_rows)} entry(ies) at the stamped "
            "release"
        )
        lib_body = f"{_p(page.library_note)}{_assumption_library_grid(page.library_rows)}"
    else:
        lib_finding = f"{fmt.ASSUMPTIONS_LIBRARY_TITLE}: none recorded"
        lib_body = f"<p><strong>None recorded.</strong> {escape(page.library_note)}</p>"
    parts.append(
        ex.figure(lib_finding, lib_body, _source(vm, "assumption library at the stamped version"))
    )
    parts.append(_p(page.footnote))
    return _section(_IV, parts, extra_class="freshpage")


def _assumption_library_grid(rows: tuple[AssumptionSourceRow, ...]) -> str:
    """The per-key library register, the same rows section V shows, reproduced on the page.

    Duplication on purpose: the page claims to enumerate EVERY assumption the run rests on, and a
    claim that sends the reader to another section for half of them is not that claim.
    """
    return _grid(
        ("Key", "Value", "Unit", "Status", "Confidence", "Source"),
        [_cells(a.key, a.value, a.unit, a.status, a.confidence, a.source) for a in rows],
    )


def _what_would_change(
    vm: OutputViewModel,
    confidence: ConfidenceView,
    data_asks: Sequence[DataAsk],
    ex: _Exhibits,
) -> str:
    if data_asks:
        ask_body = _grid(
            ("Rank", "Ask", "Why it matters", "What it unblocks"),
            [
                _cells(f"{a.rank}", a.title, a.why, ", ".join(a.blocks) if a.blocks else "n/a")
                for a in data_asks
            ],
        )
        # Name the top ask rather than praising the list: the reader should know what to send
        # first without reading the table.
        ask_finding = f"The first thing to supply is: {data_asks[0].title}"
    else:
        ask_body = f"<p>{_na('no ranked data ask was supplied with this run')}</p>"
        ask_finding = "No ranked data ask accompanies this run"

    if confidence.band is None:
        band_body = f"<p>{_na(confidence.unavailable_reason or _NO_BAND)}</p>"
        band_finding = "The band is unassessed, so nothing can be named as the thing to fix"
    elif confidence.binding_factors:
        lead = _p(
            "The band is the minimum across the evidence factors. Only the factors below set it; "
            "fixing anything else moves nothing."
        )
        band_body = f"{lead}{_bullets(confidence.binding_factors)}"
        band_finding = f"Confidence is {confidence.band.upper()}; only these factors can raise it"
    else:
        band_body = f"<p>{_na(_NO_FACTORS)}</p>"
        band_finding = f"Confidence is {confidence.band.upper()}, but what capped it is unrecorded"

    bankable_finding = (
        "This record already presents as bankable; the work below keeps it there"
        if vm.bankable
        else "What separates this run from a bankable one is evidence to be gathered, not a "
        "judgement to be made"
    )
    # The count comes from the asks the run actually carries. A fixed "Three answers" sat above
    # whatever number of rows the record held, identically in every site report, which is the one
    # thing a generated document has no excuse for.
    if data_asks:
        lead = (
            "The useful question about a result like this is not whether it is good but what would "
            f"move it. The record ranks {fmt.count_word(len(data_asks))}, in the order they "
            "pay back."
        )
    else:
        lead = (
            "The useful question about a result like this is not whether it is good but what would "
            "move it. This run carries no ranked answer to it."
        )
    return _section(
        _VIII,
        [
            _p(lead),
            ex.figure(ask_finding, ask_body, _source(vm, "ranked data ask (gridq.ingestion)")),
            ex.figure(band_finding, band_body, _source(vm, "gridq.quotes.confidence")),
            ex.figure(
                bankable_finding,
                _bullets(_BANKABLE_WORK),
                _source(vm, f"bankable status on record: {fmt.titleise(vm.bankable_status)}"),
            ),
        ],
    )


#: What the footer says when no receipt backs the page. The old sentence said the figures were
#: "copied from run record X" and that the generator "performs no arithmetic": false on both counts
#: for a run that was never written (the six 2026-08-13 packs) and at the view-model seam, which
#: sums the record's stream revenues at render time. A claim a reader cannot test is worse than no
#: claim, so an unbacked page says exactly that and names itself unpublishable.
_UNVERIFIABLE = (
    "This page cites run record {quote_id}, and no such record is persisted where a reader could "
    "fetch it. Nothing on this page can therefore be re-read at its source, and the page states no "
    "claim that it can be: it is a working render, not a publishable pack. Publish through "
    "gridq.quotes.publish_run, which writes the record, its site model and a provenance receipt "
    "pinning both by SHA-256, and renders the pack by reading those files back."
)


def _footer(vm: OutputViewModel, provenance_claim: str | None) -> str:
    """The brand footer: hairline rule, the wordmark left, the run identity right.

    ``provenance_claim`` is the sentence produced by `gridq.provenance.claims.publication_claim`
    from the receipt written beside the run's files. It is reproduced **verbatim**: it names each
    file, publishes its SHA-256, and enumerates exhaustively the values the pack recomputes rather
    than copies. Shortening it would withdraw the only assertion a reviewer could falsify, which is
    what made the previous footer worse than useless. ``None`` means no receipt backs this page, and
    the footer says so instead of claiming anything.

    No ``PAGE n OF m``. The brand reference is a paged PDF and prints one; an HTML document has no
    reliable cross-engine page counter, and a faked page number is worse than none
    (`docs/design/report_spec.md` §A.2). The stylesheet declares real ``@page`` margin boxes
    instead, so a paged renderer numbers the pages and a browser shows nothing invented.
    """
    p = vm.provenance
    if provenance_claim is None:
        body = f'<p class="unverified">{escape(_UNVERIFIABLE.format(quote_id=p.quote_id))}</p>'
    else:
        body = f"<p>{escape(provenance_claim)}</p>"
    return (
        '<footer class="doc"><div class="footrule"></div>'
        '<div class="footbar"><span class="footmark">NEURA ENERGY</span>'
        f'<span class="footrun">Run record {escape(p.quote_id)} · code '
        f"{escape(p.code_version or 'unstamped')} · schema {escape(p.schema_version)} · "
        f"assumptions {escape(p.assumption_version or 'unstamped')}</span></div>"
        f"{body}</footer>"
    )


#: The brand sheet (E-7), implementing `docs/design/neura_brand_spec.md`. One fixed light document:
#: a printed brand page, not a themed app surface. The serif and humanist-sans stacks both end in
#: system faces, so nothing is fetched and nothing is missing. The single non-brand colour is the
#: signal red, and it appears only on the two stop banners, on a covenant failure and on the stated
#: reason a figure is absent.
_STYLE = """
:root{--neura-orange:#E2551F;--neura-orange-mid:#EF8B4E;--neura-peach:#F5C9A8;
--neura-cream:#EAE7E1;--neura-cream-light:#F5F3EF;--neura-ink:#1F1F1D;--neura-grey:#8A8A85;
--neura-rule:#D8D5CE;--neura-dashed:#9A9A94;--neura-signal:#A5261F;
--serif:Spectral,"Source Serif 4","EB Garamond",Georgia,serif;
--sans:"Source Sans 3",Inter,-apple-system,system-ui,sans-serif}
*{box-sizing:border-box}p{margin:.7em 0}.scroll{overflow-x:auto}li{margin:5px 0}
body{margin:0;background:var(--neura-cream-light);color:var(--neura-ink);
font:15px/1.62 var(--sans);-webkit-font-smoothing:antialiased}
.page{max-width:940px;margin:0 auto;padding:44px 34px 64px;background:#fff}
header.doc{padding-bottom:6px}
.masthead{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;
padding-bottom:26px}
.logo{display:flex;align-items:center;gap:9px}
svg.mark{width:27px;height:27px;flex:0 0 auto;display:block}
.wordmark{font-family:var(--serif);font-size:21px;letter-spacing:-.005em;white-space:nowrap}
.wm-a{color:var(--neura-ink)}.wm-b{color:var(--neura-grey);margin-left:.24em}
.stamp{text-align:right;padding-top:3px}
.dockind{font-size:10px;letter-spacing:.17em;color:var(--neura-grey);white-space:nowrap}
.confidential{font-size:10px;letter-spacing:.17em;color:var(--neura-orange);margin-top:3px;
white-space:nowrap}
h1{font-family:var(--serif);font-size:31px;font-weight:400;line-height:1.2;margin:0 0 10px;
letter-spacing:-.005em;max-width:24em}
.sub{font-size:12px;letter-spacing:.02em;color:var(--neura-grey)}
/* Section labels: a grey roman numeral over an orange small-caps name, in the left margin. The
   heading itself stays in the document for assistive technology and for the outline. */
.sechead{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;
clip:rect(0 0 0 0);white-space:nowrap}
section.sec{display:grid;grid-template-columns:116px minmax(0,1fr);column-gap:30px;
margin:38px 0 0;padding-top:22px;border-top:1px solid var(--neura-rule)}
section.sec>*{grid-column:2;min-width:0}
section.sec>.seclab{grid-column:1;grid-row:1}
section.sec>figure.ex{grid-column:1/-1}
.seclab{padding-top:4px}
.secnum{display:block;font-size:11.5px;letter-spacing:.18em;color:var(--neura-grey);
margin-bottom:7px}
/* True small caps at one uniform height, which is how the brand reference sets these labels.
   `font-variant` first as the fallback for engines without the `all-small-caps` longhand. */
.secname{display:block;font-variant:small-caps;font-variant-caps:all-small-caps;
letter-spacing:.13em;font-size:14.5px;font-weight:600;line-height:1.4;color:var(--neura-orange)}
section.sec>p{text-align:justify;hyphens:auto}
.banner strong{font-size:13px;letter-spacing:.11em}
.banner{display:flex;flex-direction:column;gap:4px;padding:13px 16px;margin:18px 0;
border-left:4px solid}.banner span{font-size:13px;line-height:1.5}
.banner.stop{background:#FBF1EF;border-color:var(--neura-signal);color:#7D1C17}
.banner.ok{background:var(--neura-cream-light);border-color:var(--neura-orange);
color:var(--neura-ink)}
.answer{border-left:3px solid var(--neura-orange);padding:2px 0 2px 16px;margin:18px 0}
.verdict{font-family:var(--serif);font-size:21px;font-weight:400;line-height:1.3}
.conf{display:flex;gap:16px;align-items:flex-start;border:1px solid var(--neura-rule);
padding:14px 16px;margin:18px 0;background:var(--neura-cream-light)}.confbody{font-size:13px}
.band{flex:0 0 auto;font-size:11px;font-weight:700;letter-spacing:.11em;padding:6px 10px;
color:#fff;background:var(--neura-grey);white-space:nowrap}
/* A band is evidential strength, never an alarm, so no band borrows the signal colour. LOW is the
   heaviest object on the page because LOW is the common state on this portfolio. */
.band.high{background:var(--neura-orange)}.band.medium{background:var(--neura-orange-mid)}
.band.low{background:var(--neura-ink)}
.confbody p{margin:.2em 0 .5em}.na{color:var(--neura-signal);font-style:italic}
.caveat{border:1px solid var(--neura-signal);padding:12px 16px;margin:20px 0;background:#FBF4F2}
.caveatlab{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--neura-signal);
font-weight:700}
figure.ex{margin:26px 0;padding:0;border-top:1px solid var(--neura-ink)}
figcaption{display:flex;gap:14px;align-items:baseline;padding:9px 0 11px}
.exnum{flex:0 0 auto;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
color:var(--neura-grey);font-weight:700;white-space:nowrap}
.exfind{font-family:var(--serif);font-size:17px;font-weight:400;line-height:1.34}
/* Footnote typography (tiny sans, superscript-scale), kept per exhibit rather than collected in a
   three-column block: each claim on this page is an exhibit, so its source belongs beside it. */
.src{font-size:10.5px;line-height:1.5;color:var(--neura-grey);border-top:1px solid
var(--neura-rule);padding-top:6px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
/* Enough room for a two- or three-digit marker: the verbatim warning list runs past ten, and a
   marker that overflows a full-width figure is clipped at the page edge. */
ul{margin:.6em 0;padding-left:22px}ol{margin:.6em 0;padding-left:34px}
table.kv th{width:38%;text-align:left;font-weight:400;color:var(--neura-grey);vertical-align:top}
table.kv th,table.kv td{padding:7px 10px 7px 0;border-bottom:1px solid var(--neura-rule);
vertical-align:top}
table.grid th{text-align:left;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
color:var(--neura-grey);border-bottom:1px solid var(--neura-ink);padding:6px 10px 6px 0}
table.grid td{padding:6px 10px 6px 0;border-bottom:1px solid var(--neura-rule);vertical-align:top}
table.grid tr:nth-child(even) td{background:var(--neura-cream-light)}
/* A long evaluated-points register is set tighter than a body table: it is read by scanning. */
.figtable table.grid{font-size:12.5px}
.figtable table.grid td{padding:4px 10px 4px 0}
.rownote{font-size:11.5px;line-height:1.5;color:var(--neura-grey);margin-top:3px}
/* The stream notes: set as a register of findings, one rule per note, so a reader can scan the
   stream names and stop at the one whose zero they are questioning. */
ol.streamnotes{margin:.5em 0 0;padding:0;list-style:none;max-width:46em}
ol.streamnotes li{font-size:11.5px;line-height:1.55;color:var(--neura-grey);
border-top:1px solid var(--neura-rule);padding:7px 0;break-inside:avoid}
ol.streamnotes strong{color:var(--neura-ink);font-weight:600}
ol.streamnotes .notenum{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;
color:var(--neura-signal);margin-right:6px;white-space:nowrap}
footer.doc{margin-top:44px}
.footrule{border-top:1px solid var(--neura-ink)}
.footbar{display:flex;justify-content:space-between;gap:20px;padding-top:9px;font-size:10px;
letter-spacing:.15em;color:var(--neura-grey)}
.footmark{white-space:nowrap}.footrun{text-align:right;letter-spacing:.04em}
footer.doc p{font-size:11px;line-height:1.55;color:var(--neura-grey);max-width:46em}
footer.doc p.unverified{color:var(--neura-signal);border-left:3px solid var(--neura-signal);
padding-left:12px}
@media (max-width:700px){
section.sec{grid-template-columns:minmax(0,1fr)}
section.sec>*,section.sec>.seclab,section.sec>figure.ex{grid-column:1}
.seclab{display:flex;align-items:baseline;gap:12px;padding:0 0 6px}
.secnum{margin-bottom:0}
.masthead{flex-direction:column;gap:14px}.stamp{text-align:left}
section.sec>p{text-align:left}}
/* The assumptions page is a page, not a panel: the owner asked for a fresh page and this is it.
   Declared outside the print block too, so a paged HTML renderer honours it either way. */
section.sec.freshpage{break-before:page;page-break-before:always}
.dirflag{font-weight:700;text-transform:uppercase;font-size:10px;letter-spacing:.08em;
color:var(--neura-signal)}
@media print{body{background:#fff}.page{max-width:none;padding:0}
/* An exhibit stays whole; the prose panels are allowed to break. Holding every panel back leaves
   half-empty pages, and a section is many pages long, so it must break too. */
figure.ex,.banner{break-inside:avoid}
figcaption,.grouplab{break-after:avoid}
table.grid tr{break-inside:avoid}
/* Real page numbers and a running confidentiality stamp where a paged renderer exists; never a
   faked page number in the browser. The masthead already carries the stamp on the first page. */
@page{margin:16mm 14mm 14mm;@top-right{content:"PRIVATE & CONFIDENTIAL";font-size:7.5pt;
letter-spacing:.15em;color:#E2551F}@bottom-left{content:"NEURA ENERGY";font-size:8pt;
letter-spacing:.15em;color:#8A8A85}@bottom-right{content:"PAGE " counter(page) " OF "
counter(pages);font-size:8pt;letter-spacing:.15em;color:#8A8A85}}
@page:first{@top-right{content:""}}}
"""


def render_consulting_report(
    vm: OutputViewModel,
    *,
    site: SiteModel | None = None,
    confidence: ConfidenceView | None = None,
    data_asks: Sequence[DataAsk] = (),
    half_hourly_basis: str | None = None,
    sizing_grid: SizingGrid | None = None,
    dispatch_profile: DispatchProfile | None = None,
    provenance_claim: str | None = None,
) -> str:
    """Render the report as one self-contained HTML document.

    ``vm`` is the only source of run figures. ``site`` supplies the Situation description (load,
    tariff, connection) the view model does not carry; ``confidence``, ``data_asks`` and
    ``half_hourly_basis`` (the record's Gate-2 stamp) are likewise passed in. ``sizing_grid`` and
    ``dispatch_profile`` (E-3) are the two persisted engine projections the view model does not
    expose: without them the sizing map and the firming diagram have no record to draw and render
    their reasoned *unavailable*. Anything not supplied renders as an explicit, reasoned
    *unavailable*.

    ``provenance_claim`` is `gridq.provenance.claims.publication_claim` built from the receipt
    written beside the run's persisted files. Only a caller holding that receipt can supply it, so
    the footer cannot assert verifiability the page does not have; without it the footer says the
    page is a working render rather than a publishable pack.

    The finished document is passed through `formatting.no_dashes`, so no em dash or en dash reaches
    the reader from anywhere: not from this module's copy, and not from the record text the report
    reproduces verbatim. The record itself is untouched.
    """
    band = confidence if confidence is not None else ConfidenceView.unavailable()
    stamped = _stamped_basis(site, half_hourly_basis)
    ex = _Exhibits()
    # THE OWNER'S ORDER (D59, verbatim): "starting with the IRR and Revenue breakdown and graphs,
    # then the methodology and then the assumptions register for each site". The basis banner stays
    # above everything because it is a safety label, not an evidence section: a reader must not meet
    # a study-basis IRR before being told which basis produced it.
    body = "".join(
        [
            _head(vm, site, stamped),
            _banner(vm),
            # I: the answer, the IRR, then revenue per stream and the basis of each stream's figure.
            _section(_I, [*_answer_parts(vm, band, ex, site), *_value_parts(vm, ex)]),
            # II: the charts that explain section I.
            _graphs(vm, ex, site, dispatch_profile, sizing_grid),
            # III: how the machine that produced them works.
            _methodology(vm, ex),
            # IV: D56 rule 4, on a page of its own.
            _assumptions(vm, ex),
            # V to VIII: the evidence sections. They moved DOWN, not out: every exhibit that opened
            # this pack before D59 is still here, in full.
            _situation(vm, site, ex, half_hourly_basis),
            _complication(vm, band, ex),
            _evidence(vm, ex),
            _what_would_change(vm, band, data_asks, ex),
            _footer(vm, provenance_claim),
        ]
    )
    return fmt.no_dashes(
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(_doc_title(vm, site))}</title>\n"
        f"<style>{_STYLE}{charts.CHART_CSS}</style>\n</head>\n<body>\n"
        f'<main class="page">{body}</main>\n</body>\n</html>\n'
    )


def _persisted_claim(quote_id: str, root: object | None) -> str | None:
    """The provenance claim for ``quote_id``, or ``None`` when nothing on disk backs it.

    Looked up rather than asserted: the claim and the evidence for it come from the same call, so
    the footer can only say "copied from the persisted record" about a record that is persisted.
    Imported inside the function because `gridq.quotes` reaches back into this package to render,
    and a module-scope import would close that loop.

    A store that is incoherent (a record with no receipt beside it, a directory holding a different
    run) is not an error *here*: it means the same thing an absent record means, which is that this
    page has nothing to cite, and the footer says so. Refusing outright is the publication path's
    job (`render_publishable_pack`), where a caller has asked for a pack a reader can check.
    """
    from pathlib import Path

    from gridq.quotes.persistence import RunStoreError, load_persisted_run

    try:
        persisted = load_persisted_run(quote_id, root=None if root is None else Path(str(root)))
    except RunStoreError:
        return None
    return None if persisted is None else persisted.provenance_claim


def render_report_for_quote(
    quote: QuoteRecord,
    site: SiteModel | None = None,
    *,
    assumptions: object | None = None,
    confidence: ConfidenceView | None = None,
    data_asks: Sequence[DataAsk] = (),
    run_store_root: object | None = None,
    provenance_claim: str | None = None,
) -> str:
    """Convenience: extract the view model from ``quote``, then render.

    The only place this module touches the record. Every figure goes through `build_view_model`;
    the record is read directly for the four things the view model does not expose: the persisted
    confidence band (used when the caller supplies none), the half-hourly basis stamped by
    validation Gate 2, and the two persisted engine projections the drawn exhibits need (the
    evaluated sizing grid and the representative dispatch day, E-3). All are copied, never derived.

    The footer's claim comes from whichever of two sources knows the truth. A caller that just wrote
    the run passes its receipt's ``provenance_claim`` and that wins outright: it holds the evidence,
    and it may have written it somewhere this function would not think to look, in which case a disk
    lookup would disclaim a pack sitting beside its own receipt. Otherwise the run is looked up by
    ``quote.quote_id`` (``run_store_root`` overrides where): found, the footer states the receipt's
    claim with each file's SHA-256; not found, the footer states that the page is a working render
    and not a publishable pack. Publishing is `publish_run` / `render_publishable_pack`, both of
    which refuse a run that is not on disk.
    """
    vm = build_view_model(quote, site, assumptions=assumptions)
    band = confidence if confidence is not None else ConfidenceView.from_record(quote)
    basis = None if quote.half_hourly_basis is None else str(quote.half_hourly_basis)
    claim = (
        provenance_claim
        if provenance_claim is not None
        else _persisted_claim(quote.quote_id, run_store_root)
    )
    return render_consulting_report(
        vm,
        site=site,
        confidence=band,
        data_asks=data_asks,
        half_hourly_basis=basis,
        sizing_grid=quote.sizing_grid,
        dispatch_profile=quote.dispatch_profile,
        provenance_claim=claim,
    )


def render_publishable_pack(
    quote_id: str,
    *,
    run_store_root: object | None = None,
    assumptions: object | None = None,
    confidence: ConfidenceView | None = None,
) -> str:
    """The pack for a persisted run, refusing outright when the run is not on disk.

    The publication entry point. `require_persisted_run` raises `UnpersistedRunError` for a run
    whose record no reader could fetch, which is the state the six 2026-08-13 packs shipped in, and
    it returns the record **as read back off disk** so the page is rendered from the same bytes the
    reader can fetch rather than from whatever is in memory. The footer then carries the receipt's
    claim, naming each file and its SHA-256.

    A pack that cannot be checked is not produced at all: there is no degraded version of an
    unverifiable claim about money, and the fix is always to persist the run.

    ``assumptions`` defaults to the release the record pins, resolved exactly as `publish_run`
    resolves it, so the two sanctioned paths produce the same pack rather than one silently missing
    its assumption register and scenario spread. An unresolvable version degrades to the pending
    notes, never to a guess.
    """
    from pathlib import Path

    from gridq.ingestion.improvement_asks import build_ranked_data_asks
    from gridq.quotes.persistence import require_persisted_run

    persisted = require_persisted_run(
        quote_id, root=None if run_store_root is None else Path(str(run_store_root))
    )
    quote, site = persisted.quote, persisted.site
    if assumptions is None and quote.assumption_version:
        from gridq.assumptions import AssumptionError, load_assumptions

        try:
            assumptions = load_assumptions(quote.assumption_version)
        except AssumptionError:
            assumptions = None  # honest degrade: the pack keeps its pending notes
    vm = build_view_model(quote, site, assumptions=assumptions)
    band = confidence if confidence is not None else ConfidenceView.from_record(quote)
    basis = None if quote.half_hourly_basis is None else str(quote.half_hourly_basis)
    return render_consulting_report(
        vm,
        site=site,
        confidence=band,
        data_asks=() if site is None else tuple(build_ranked_data_asks(site)),
        half_hourly_basis=basis,
        sizing_grid=quote.sizing_grid,
        dispatch_profile=quote.dispatch_profile,
        provenance_claim=persisted.provenance_claim,
    )
