"""Outputs (M8) — the three artifacts generated from one run record.

From a single stamped `QuoteRecord` this package produces the three documents that are consistent
by construction (System Brief, Outputs 1–3):

- **Underwriting pack** (capital-facing, Markdown) — P50/P90 cashflows, DSCR + covenant +
  sensitivities, the contracted-vs-merchant split, price to target, counterparty credit, consents
  and gate status, the provenance spine and warnings. See `render_underwriting_memo`.
- **Customer proposal** (plain-language, Markdown) — system size, structure, term, bill
  before/after + savings (a labelled-pending seam in v1), and what is contracted vs upside. See
  `render_customer_proposal`.
- **HTML report** — one self-contained visual page combining both. See `render_html_report`.
- **Consulting report** — the strategy-deliverable HTML page (answer first, then the evidence that
  earns it). See `render_consulting_report`, or `render_report_for_quote` to go straight from a
  stamped record. `render_publishable_pack` is the publication path: it takes a run id, refuses a
  run whose record is not on disk, and renders from the persisted files so the pack's footer can
  state a claim its reader is able to check.

Hard rules (issues 011 / 012, SKILLS.md): **every number is injected from the run record, never
authored**; missing figures degrade to an explicit *unavailable* / *placeholder* label naming why,
never a guess; provenance is shown; baseline/placeholder runs carry a NOT-BANKABLE banner; the LLM
(if any) drafts prose only and never a number; and the HTML generator does no arithmetic. Numbers
are extracted once into an `OutputViewModel` (`viewmodel`), turned to strings only in `formatting`,
and rendered by the per-artifact modules — so the rules hold by construction.
"""

from __future__ import annotations

from gridq.outputs.consulting_report import (
    render_consulting_report,
    render_publishable_pack,
    render_report_for_quote,
)
from gridq.outputs.generate import OutputArtifacts, generate_artifacts, generate_for_run
from gridq.outputs.html_report import render_html_report
from gridq.outputs.proposal import render_customer_proposal
from gridq.outputs.prose import DeterministicProseDrafter, ProseDrafter, default_prose_drafter
from gridq.outputs.underwriting import render_underwriting_memo
from gridq.outputs.viewmodel import (
    OutputViewModel,
    SensitivityRow,
    StudyBasisView,
    build_view_model,
    detect_study_basis,
)

__all__ = [
    # one-call generation
    "OutputArtifacts",
    "generate_artifacts",
    "generate_for_run",
    # per-artifact renderers
    "render_underwriting_memo",
    "render_customer_proposal",
    "render_html_report",
    "render_consulting_report",
    "render_report_for_quote",
    "render_publishable_pack",
    # view model + prose seam
    "OutputViewModel",
    "SensitivityRow",
    "build_view_model",
    # The pricing-basis detector, for surfaces that show a record WITHOUT building a whole
    # view model (the app's project listings). One detector, so no surface can disagree about
    # whether a record was priced on a study basis.
    "StudyBasisView",
    "detect_study_basis",
    "ProseDrafter",
    "DeterministicProseDrafter",
    "default_prose_drafter",
]
