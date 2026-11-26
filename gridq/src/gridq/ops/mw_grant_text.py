"""What the grant application and the competition scope say about the MW figure (WP-OPS-11).

A typed register (D146) of the documents read, each pinned by the SHA-256 of its bytes
and located **outside this repository** (the grant documents are never copied in), and of short
verbatim quotes from them, each with the document and section it comes from. A test re-extracts
each document's text with the same reader and checks every fragment is still there, so a quote
cannot drift from its source. Text that is not held is listed as an ask to the owner; figures only
an award can state (project number, grant amount, agreed KPIs) stay unknown with the award
register entry that will hold them.

Internal meeting notes are registered for context only and can never be cited as grant text.

Readers: ``pdftotext`` (no layout) for the PDF; the paragraph text of ``word/document.xml`` for a
.docx; tags stripped and entities unescaped for the saved competition page. Every reader collapses
whitespace runs to one space. The PDF's columns interleave under ``pdftotext``, so a sentence that
spans a column is quoted as its fragments in reading order.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Annotated, Final, Literal
from xml.etree import ElementTree

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Unknown, content_hash
from gridq.governance.dpa_guard import repo_root
from gridq.ops.dossier_model import AskHolder, DossierAsk

__all__ = [
    "GRANT_ASKS",
    "GRANT_TEXT_REGISTER",
    "GRANT_TEXT_ROOT_ENV",
    "AwardFigure",
    "ConceptRelation",
    "DraftDivergence",
    "GrantDocument",
    "GrantQuote",
    "GrantTextRegister",
    "GrantTextUnavailable",
    "MW_CONCEPTS",
    "concept_relations",
    "document_path",
    "extract_text",
    "normalise",
]

#: Overrides where the documents are looked for (default: the workspace above ``Neura GitHub``).
GRANT_TEXT_ROOT_ENV: Final = "GRIDQ_GRANT_TEXT_ROOT"

_ID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
_HEX = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class GrantTextUnavailable(OSError):
    """A held document is not at its location, or its reader is not installed."""


class GrantDocument(ContractModel):
    doc_id: _ID
    kind: Literal[
        "application_as_held", "application_draft", "competition_page", "internal_notes", "not_held"
    ]
    title: NonEmptyStr
    #: Path relative to the workspace root (the folder holding ``Neura GitHub``); never in a repo
    #: this package writes to. Not applicable for a document that is not held.
    location: NonEmptyStr | NotApplicable
    sha256: _HEX | NotApplicable
    reader: Literal["pdftotext", "docx_xml", "html_gz"] | NotApplicable
    #: What the document is, in one line (dates and provenance as known).
    note: NonEmptyStr
    #: The ask that would close a not-held document (its id); not applicable when held.
    ask_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | NotApplicable

    @model_validator(mode="after")
    def _held_iff_located(self) -> GrantDocument:
        held = self.kind != "not_held"
        located = not isinstance(self.location, NotApplicable)
        if not (held == located == (not isinstance(self.sha256, NotApplicable))):
            raise ValueError(
                "a held document has a location and a hash; a not-held one has neither"
            )
        if held != isinstance(self.ask_id, NotApplicable):
            raise ValueError("a not-held document, and only one, carries its ask")
        if held and isinstance(self.reader, NotApplicable):
            raise ValueError("a held document names its reader")
        if isinstance(self.location, str) and re.match(
            r"(?i)neura github/uk[^/]*/(src|docs|tests)/", self.location
        ):
            raise ValueError("a grant document is never located inside a GridQ source tree")
        return self


MwNotion = Literal[
    "distributed_capacity",
    "under_management",
    "validated_flexibility",
    "reconciled_delivery",
    "per_mw_price",
    "system_target",
    "deployment_scale",
    "installed_capacity",
    "market_threshold",
    "none_stated",
]


class NotionFigure(ContractModel):
    """A figure exactly as the text writes it (``~50MW``), with the notion it is a figure of."""

    notion: MwNotion
    figure: NonEmptyStr | NotApplicable


class GrantQuote(ContractModel):
    quote_id: _ID
    doc_id: _ID
    #: The section heading (or anchoring phrase) the fragments sit under.
    location: NonEmptyStr
    #: Verbatim fragments in reading order; each one is in the document's normalised text.
    fragments: tuple[Annotated[str, Field(min_length=3, max_length=240)], ...] = Field(min_length=1)
    figures: tuple[NotionFigure, ...] = Field(min_length=1)

    @property
    def text(self) -> str:
        return " ".join(self.fragments)


class AbsenceCheck(ContractModel):
    """A pattern the document's normalised text does not contain (re-checked by a test)."""

    doc_id: _ID
    pattern: NonEmptyStr
    statement: NonEmptyStr


class DraftDivergence(ContractModel):
    """Where the held application and the earlier draft say different things. Which version was
    submitted is unknown until the owner says (the ask)."""

    topic: _ID
    as_held: tuple[_ID, ...] = Field(min_length=1)
    draft: tuple[_ID, ...]
    absences: tuple[AbsenceCheck, ...]
    submitted_version: Unknown
    ask_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class AwardFigure(ContractModel):
    """A figure only the award states. Never filled here."""

    award_ref: Annotated[str, Field(pattern=r"^AW-[0-9]{2}$")]
    label: NonEmptyStr
    value: Unknown
    ask_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class GrantTextRegister(ContractModel):
    documents: tuple[GrantDocument, ...]
    quotes: tuple[GrantQuote, ...]
    divergences: tuple[DraftDivergence, ...]
    award_figures: tuple[AwardFigure, ...]
    asks: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _consistent(self) -> GrantTextRegister:
        docs = {d.doc_id: d for d in self.documents}
        if len(docs) != len(self.documents):
            raise ValueError("document ids are unique")
        ask_ids = {a.ask_id for a in self.asks}
        if len(ask_ids) != len(self.asks):
            raise ValueError("each ask is listed once")
        qids = [q.quote_id for q in self.quotes]
        if len(qids) != len(set(qids)):
            raise ValueError("quote ids are unique")
        for q in self.quotes:
            if q.doc_id not in docs or docs[q.doc_id].kind == "not_held":
                raise ValueError(f"{q.quote_id} quotes a document that is not held")
        quoted = set(qids)
        for dv in self.divergences:
            if not set(dv.as_held + dv.draft) <= quoted or dv.ask_id not in ask_ids:
                raise ValueError(f"divergence {dv.topic} cites an unknown quote or ask")
            for qid in dv.as_held:
                if docs[self.quote(qid).doc_id].kind != "application_as_held":
                    raise ValueError(
                        f"divergence {dv.topic}: {qid} is not from the held application"
                    )
            for qid in dv.draft:
                if docs[self.quote(qid).doc_id].kind != "application_draft":
                    raise ValueError(f"divergence {dv.topic}: {qid} is not from the draft")
        for d in self.documents:
            if isinstance(d.ask_id, str) and d.ask_id not in ask_ids:
                raise ValueError(f"{d.doc_id}: its ask is not listed")
        for a in self.award_figures:
            if a.ask_id not in ask_ids:
                raise ValueError(f"{a.award_ref}: its ask is not listed")
        return self

    def document(self, doc_id: str) -> GrantDocument:
        return next(d for d in self.documents if d.doc_id == doc_id)

    def quote(self, quote_id: str) -> GrantQuote:
        return next(q for q in self.quotes if q.quote_id == quote_id)

    def grant_quotes(self) -> tuple[GrantQuote, ...]:
        """Quotes a reader may cite as grant or competition text (internal notes excluded)."""
        return tuple(q for q in self.quotes if self.document(q.doc_id).kind != "internal_notes")

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------------------------

_W: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def normalise(text: str) -> str:
    return " ".join(text.split())


def workspace_root() -> Path:
    override = os.environ.get(GRANT_TEXT_ROOT_ENV)
    if override:
        return Path(override)
    return repo_root().parent.parent


def document_path(doc: GrantDocument) -> Path:
    if isinstance(doc.location, NotApplicable):
        raise GrantTextUnavailable(f"{doc.doc_id} is not held")
    return workspace_root() / doc.location


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_text(doc: GrantDocument, path: Path | None = None) -> str:
    """The document's normalised text, by its registered reader. Refuses a hash mismatch."""
    p = path if path is not None else document_path(doc)
    if not p.is_file():
        raise GrantTextUnavailable(f"{doc.doc_id}: no file at {p}")
    if isinstance(doc.sha256, str) and _sha256(p) != doc.sha256:
        raise ValueError(f"{doc.doc_id}: {p.name} is not the registered document (sha256 differs)")
    if doc.reader == "pdftotext":
        try:
            out = subprocess.run(
                ["pdftotext", str(p), "-"], capture_output=True, check=True, text=True
            ).stdout
        except FileNotFoundError as exc:
            raise GrantTextUnavailable("pdftotext is not installed") from exc
        return normalise(out)
    if doc.reader == "docx_xml":
        with zipfile.ZipFile(p) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
        paras = ["".join(t.text or "" for t in para.iter(_W + "t")) for para in root.iter(_W + "p")]
        return normalise(" ".join(paras))
    if doc.reader == "html_gz":
        text = gzip.decompress(p.read_bytes()).decode("utf-8")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return normalise(html.unescape(text))
    raise GrantTextUnavailable(f"{doc.doc_id} has no reader")


# --------------------------------------------------------------------------------------------
# Asks (owner-held text and award figures)
# --------------------------------------------------------------------------------------------


def _owner_ask(request: str) -> DossierAsk:
    return DossierAsk.make(request=request, holder=AskHolder.NEURA_OWNER, scope="dossier")


ASK_SUBMITTED_VERSION: Final = _owner_ask(
    "Confirm which version of the Q9 answer was submitted to Innovate UK: the held PDF "
    "'Qu9 App - NeuraFlex.pdf' or the draft 'Q9 App - Neura - CLF.docx' (they differ on the "
    "demonstrator MW figure, the pilot window and the 2029/2030 targets)"
)
ASK_THEORY_OF_CHANGE: Final = _owner_ask(
    "Provide the Theory of Change document the application cites for the project KPIs "
    "('Project KPIs captured in Theory of Change')"
)
ASK_THEMES: Final = _owner_ask(
    "Provide 'CLF June competition additional pdf document on themes.pdf', named on the "
    "competition 2509 overview page"
)
ASK_ASSESSOR_GUIDANCE: Final = _owner_ask(
    "Provide 'Consumer Led Flexibility for the Clean Energy Mission - Assessor guidance for "
    "applicants .pdf', named on the competition 2509 overview page"
)
ASK_BRIEFING: Final = _owner_ask(
    "Provide 'Applicant Briefing Consumer Led Flexibility for the Clean Energy Mission.pdf', "
    "named on the competition 2509 overview page"
)
ASK_OFFER_LETTER: Final = _owner_ask(
    "Provide the grant offer letter and its annexes: the project number, the grant amount and "
    "the project KPIs and milestones it sets (award register AW-01, AW-04, AW-09)"
)
ASK_LETTERS_OF_SUPPORT: Final = _owner_ask(
    "Provide the letters of support submitted with the application, in particular Chainergy's "
    "(the application names it the anchor deployment partner with a ~200MW pipeline)"
)

GRANT_ASKS: Final = tuple(
    sorted(
        (
            ASK_SUBMITTED_VERSION,
            ASK_THEORY_OF_CHANGE,
            ASK_THEMES,
            ASK_ASSESSOR_GUIDANCE,
            ASK_BRIEFING,
            ASK_OFFER_LETTER,
            ASK_LETTERS_OF_SUPPORT,
        ),
        key=lambda a: a.ask_id,
    )
)

# --------------------------------------------------------------------------------------------
# The register
# --------------------------------------------------------------------------------------------

_NA_HELD: Final = NotApplicable(kind="not_applicable", reason="held: no ask")


def _na(reason: str) -> NotApplicable:
    return NotApplicable(kind="not_applicable", reason=reason)


def _held(
    doc_id: str, kind: str, title: str, location: str, sha256: str, reader: str, note: str
) -> GrantDocument:
    return GrantDocument.model_validate(
        {
            "doc_id": doc_id,
            "kind": kind,
            "title": title,
            "location": location,
            "sha256": sha256,
            "reader": reader,
            "note": note,
            "ask_id": _NA_HELD,
        }
    )


def _not_held(doc_id: str, title: str, note: str, ask: DossierAsk) -> GrantDocument:
    return GrantDocument(
        doc_id=doc_id,
        kind="not_held",
        title=title,
        location=_na("not held"),
        sha256=_na("not held"),
        reader=_na("not held"),
        note=note,
        ask_id=ask.ask_id,
    )


def _q(
    quote_id: str,
    doc_id: str,
    location: str,
    fragments: tuple[str, ...],
    *figures: tuple[str, str | None],
) -> GrantQuote:
    return GrantQuote.model_validate(
        {
            "quote_id": quote_id,
            "doc_id": doc_id,
            "location": location,
            "fragments": fragments,
            "figures": tuple(
                {"notion": n, "figure": f if f is not None else _na("no figure stated")}
                for n, f in (figures or (("none_stated", None),))
            ),
        }
    )


_PDF: Final = "q9_application_pdf"
_DRAFT: Final = "q9_application_draft_docx"
_COMP: Final = "competition_2509_overview"
_KICKOFF: Final = "kickoff_meeting_notes"
_PDF_EXIT: Final = "Exit Point / Route to Scale"
_PDF_INNOV: Final = "Project Innovation and R&D Focus"

GRANT_TEXT_REGISTER: Final = GrantTextRegister(
    documents=(
        _held(
            _PDF,
            "application_as_held",
            "Qu9 App - NeuraFlex.pdf",
            "Qu9 App - NeuraFlex.pdf",
            "358b6cee405cc87dc2171773b1a810855c71943f69e907d12cb54725d9af96e5",
            "pdftotext",
            "The Q9 answer (technical summary and commercial potential) as held in the workspace, "
            "4 pages; whether it is the submitted version is an ask",
        ),
        _held(
            _DRAFT,
            "application_draft",
            "Q9 App - Neura - CLF.docx",
            "Neura GitHub/uk/Q9 App - Neura - CLF.docx",
            "4f1be0187cab40ac3ce56d3e4ef6d39154b92876eed5aad5ac7130661c25f2ff",
            "docx_xml",
            "An earlier draft of the Q9 answer (outside version control in the uk checkout)",
        ),
        _held(
            _COMP,
            "competition_page",
            "Competition overview - Consumer Led Flexibility for the Clean Energy Superpower "
            "Mission (Innovation Funding Service, competition 2509)",
            "Neura GitHub/atlas/roadmap/governance/regulatory/sources/"
            "180b95aea555_comp2509.html.gz",
            "11bdfb53766c3ebacc7d5509371d7b5fefc501d69ef9aff16691e15c213ed85b",
            "html_gz",
            "The saved competition page (atlas award register IUK-2509-OVERVIEW; the "
            "decompressed page's sha256 is 180b95aea555502c519aa5f8ddc25226da5e6ffa30a8ab5621b31db"
            "83c4f4f17)",
        ),
        _held(
            _KICKOFF,
            "internal_notes",
            "Kickoff Meeting.docx",
            "Kickoff Meeting.docx",
            "06f6afccd7ce8516856c855b585234d9f62aabda14497c2b5aee37c984397ae0",
            "docx_xml",
            "Internal Neura meeting notes, 08/06/2026; context only, never grant text",
        ),
        _not_held(
            "applicant_briefing",
            "Applicant Briefing Consumer Led Flexibility for the Clean Energy Mission.pdf",
            "Briefing slides named on the competition page",
            ASK_BRIEFING,
        ),
        _not_held(
            "assessor_guidance",
            "Consumer Led Flexibility for the Clean Energy Mission - Assessor guidance for "
            "applicants .pdf",
            "The assessment criteria named on the competition page",
            ASK_ASSESSOR_GUIDANCE,
        ),
        _not_held(
            "competition_themes",
            "CLF June competition additional pdf document on themes.pdf",
            "The themes document the competition page says applicants must review",
            ASK_THEMES,
        ),
        _not_held(
            "grant_offer_letter",
            "Grant offer letter and annexes",
            "States the project number, grant amount, KPIs and milestones",
            ASK_OFFER_LETTER,
        ),
        _not_held(
            "letters_of_support",
            "Letters of support",
            "Partner letters submitted with the application",
            ASK_LETTERS_OF_SUPPORT,
        ),
        _not_held(
            "theory_of_change",
            "Theory of Change",
            "Where the application says the project KPIs are captured",
            ASK_THEORY_OF_CHANGE,
        ),
    ),
    quotes=(
        _q(
            "app_demonstrator_pilot",
            _PDF,
            _PDF_INNOV,
            (
                "A stage-gated ~50MW distributed",
                "UK winter real-world",
                "demonstrator pilot,",
                "independently verified by",
                "PSAL, will reconcile",
                "delivered flexibility,",
                "revenue and constraint",
                "compliance against the",
                "underwritten case.",
            ),
            ("distributed_capacity", "~50MW"),
            ("reconciled_delivery", None),
        ),
        _q(
            "app_kpis_theory_of_change",
            _PDF,
            _PDF_INNOV,
            ("Project KPIs captured", "in Theory of Change."),
        ),
        _q(
            "app_live_validation_target",
            _PDF,
            _PDF_EXIT,
            (
                "Live validation will target ~50MW distributed capacity, with Chainergy keen to "
                "act as the anchor deployment partner through its ~200MW pipeline.",
            ),
            ("distributed_capacity", "~50MW"),
        ),
        _q(
            "app_multiple_sites",
            _PDF,
            _PDF_EXIT,
            (
                "Multiple smaller sites reduce single-host risk and test transferability across "
                "different site configurations and network areas.",
            ),
        ),
        _q(
            "app_reconciled_delivery",
            _PDF,
            "Technical Challenges, item 3 (Finance-grade underwriting and verification)",
            (
                "be reconciled against delivered MW, MWh, revenue, availability and thermal "
                "compliance",
            ),
            ("reconciled_delivery", None),
        ),
        _q(
            "app_software_price",
            _PDF,
            "Commercial model table, row 'Software and dispatch'",
            ("Software and", "£10,000/MW/year", "Recurring optimisation, connection"),
            ("per_mw_price", "£10,000/MW/year"),
        ),
        _q(
            "app_targets_2030",
            _PDF,
            _PDF_EXIT,
            (
                "By 2030, Neura targets ~750MW under management, supporting ~274MW of validated "
                "flexibility",
            ),
            ("under_management", "~750MW"),
            ("validated_flexibility", "~274MW"),
        ),
        _q(
            "app_window",
            _PDF,
            _PDF_EXIT,
            ("independently verified live winter validation in Nov-Dec 2028.",),
        ),
        _q(
            "comp_2gw_target",
            _COMP,
            "Description",
            (
                "Projects must provide strong justification for how they will contribute to or "
                "enable our target of at least 2 Gigawatts (GW) of additional flexibility on the "
                "system by 2030.",
            ),
            ("system_target", "at least 2 Gigawatts (GW)"),
        ),
        _q(
            "comp_minimum_2gw",
            _COMP,
            "Description",
            (
                "contribute to the minimum two Gigawatts (GW) flexibility target is central to "
                "this opportunity.",
            ),
            ("system_target", "two Gigawatts (GW)"),
        ),
        _q(
            "comp_q9_route_to_scale",
            _COMP,
            "2. Application questions, Question 9 (Technical project summary and commercial "
            "potential)",
            ("your route to scale to multi-Megawatt deployment by 2030",),
            ("deployment_scale", "multi-Megawatt"),
        ),
        _q(
            "comp_scale_of_flexibility",
            _COMP,
            "Funding (capital equipment)",
            ("the scale of flexibility you anticipate your project will enable",),
        ),
        _q(
            "comp_winter_turn_down",
            _COMP,
            "Description",
            (
                "This is part of the Clean Power 2030 Ambition to achieve 10-12 GW demand turn "
                "down during a winter peak",
            ),
            ("system_target", "10-12 GW"),
        ),
        _q(
            "draft_kpis_theory_of_change",
            _DRAFT,
            "Paragraph beginning 'Connection and operating evidence will feed the underwriting "
            "layer'",
            ("Project KPIs captured in Theory of Change.",),
        ),
        _q(
            "draft_pilot_window",
            _DRAFT,
            "Exit Point / Route to Scale",
            ("a live partial winter pilot (Oct- Dec27)",),
        ),
        _q(
            "draft_reconciled_delivery",
            _DRAFT,
            "Technical Challenges, item 3",
            (
                "be reconciled against delivered MW, MWh, revenue, availability and thermal "
                "compliance",
            ),
            ("reconciled_delivery", None),
        ),
        _q(
            "draft_targets_placeholder",
            _DRAFT,
            "Paragraph beginning 'The winter pilot will provide independently verified "
            "performance evidence'",
            (
                "Targets are X contracted sites/XMW by 2029 and X sites/XMW under management by "
                "2030",
            ),
            ("under_management", "XMW"),
        ),
        _q(
            "draft_winter_pilot",
            _DRAFT,
            "Paragraph beginning 'Connection and operating evidence will feed the underwriting "
            "layer'",
            (
                "A live UK winter pilot and independent PSAL verification will reconcile delivered "
                "flexibility, revenue and constraint compliance against the underwritten case",
            ),
            ("reconciled_delivery", None),
        ),
        _q(
            "notes_battery_threshold",
            _KICKOFF,
            "Finance \u2013 Sim",
            (
                "As soon as you’re more than 1MW of BATTERY capacity, then you can bid into "
                "the capacity market",
            ),
            ("market_threshold", "1MW"),
        ),
        _q(
            "notes_capacity_market",
            _KICKOFF,
            "Opening notes under '08/06/2026 \u2013 Kickoff meeting'",
            ("All of 50MW we would isntall would go into capacity market",),
            ("installed_capacity", "50MW"),
        ),
    ),
    divergences=(
        DraftDivergence(
            topic="mw_figure",
            as_held=("app_demonstrator_pilot", "app_live_validation_target"),
            draft=(),
            absences=(
                AbsenceCheck(
                    doc_id=_DRAFT,
                    pattern=r"(?<![0-9])50 ?MW",
                    statement="the draft states no 50 MW figure",
                ),
            ),
            submitted_version=Unknown(kind="unknown", reason="which Q9 version was submitted"),
            ask_id=ASK_SUBMITTED_VERSION.ask_id,
        ),
        DraftDivergence(
            topic="pilot_window",
            as_held=("app_window",),
            draft=("draft_pilot_window",),
            absences=(),
            submitted_version=Unknown(kind="unknown", reason="which Q9 version was submitted"),
            ask_id=ASK_SUBMITTED_VERSION.ask_id,
        ),
        DraftDivergence(
            topic="targets_2030",
            as_held=("app_targets_2030",),
            draft=("draft_targets_placeholder",),
            absences=(),
            submitted_version=Unknown(kind="unknown", reason="which Q9 version was submitted"),
            ask_id=ASK_SUBMITTED_VERSION.ask_id,
        ),
    ),
    award_figures=(
        AwardFigure(
            award_ref="AW-01",
            label="Project number",
            value=Unknown(kind="unknown", reason="stated only by the award"),
            ask_id=ASK_OFFER_LETTER.ask_id,
        ),
        AwardFigure(
            award_ref="AW-04",
            label="Grant amount",
            value=Unknown(kind="unknown", reason="stated only by the award"),
            ask_id=ASK_OFFER_LETTER.ask_id,
        ),
        AwardFigure(
            award_ref="AW-09",
            label="Project KPIs set with UKRI (including any MW figure)",
            value=Unknown(kind="unknown", reason="stated only by the award"),
            ask_id=ASK_OFFER_LETTER.ask_id,
        ),
    ),
    asks=GRANT_ASKS,
)


# --------------------------------------------------------------------------------------------
# Which words of a quote a definition's quantity shares (no fit judgement)
# --------------------------------------------------------------------------------------------

#: A controlled vocabulary: each concept and the words in a text that show it.
MW_CONCEPTS: Final[dict[str, tuple[str, ...]]] = {
    "battery": ("battery", "bess"),
    "capacity": ("capacity",),
    "delivered": ("delivered",),
    "dispatch": ("dispatch", "dispatched"),
    "distributed": ("distributed",),
    "flexibility": ("flexibility", "flexible"),
    "load": ("load",),
    "management": ("under management",),
    "reconciled": ("reconcile", "reconciled"),
    "validated": ("validated", "validation"),
    "verified": ("verified", "verification"),
}

#: The concepts each candidate's own words carry (spec §15.3 names).
DEFINITION_CONCEPTS: Final[dict[str, tuple[str, ...]]] = {
    "site_load_under_management": ("load", "management"),
    "bess_mw_actively_dispatched": ("battery", "dispatch"),
    "verified_flexible_mw": ("flexibility", "verified"),
}


class ConceptRelation(ContractModel):
    """Which of a definition's concepts a quote's words show, and which it does not. No fit,
    match or score field: the reader judges."""

    quote_id: _ID
    definition_id: NonEmptyStr
    shared: tuple[NonEmptyStr, ...]
    not_stated_in_text: tuple[NonEmptyStr, ...]
    #: The quote's other concepts (in the vocabulary, not the definition's).
    other_concepts_in_text: tuple[NonEmptyStr, ...]


def _concepts(text: str) -> set[str]:
    lowered = text.lower()
    return {
        c
        for c, words in MW_CONCEPTS.items()
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words)
    }


def concept_relations(
    register: GrantTextRegister = GRANT_TEXT_REGISTER,
) -> tuple[ConceptRelation, ...]:
    """For each grant quote stating a figure, against each candidate definition."""
    out: list[ConceptRelation] = []
    for q in register.grant_quotes():
        if all(isinstance(f.figure, NotApplicable) for f in q.figures):
            continue
        found = _concepts(q.text)
        for def_id, own in DEFINITION_CONCEPTS.items():
            out.append(
                ConceptRelation(
                    quote_id=q.quote_id,
                    definition_id=def_id,
                    shared=tuple(sorted(found & set(own))),
                    not_stated_in_text=tuple(sorted(set(own) - found)),
                    other_concepts_in_text=tuple(sorted(found - set(own))),
                )
            )
    return tuple(out)
