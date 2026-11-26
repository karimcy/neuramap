"""One-call generation of the three M8 artifacts from a single run record.

`generate_artifacts` is the public entry point: give it a stamped `QuoteRecord` (the source of
every number) and, optionally, the `SiteModel` (for the counterparty / consent / gate sections the
record does not embed), and it returns the underwriting memo (Markdown), the customer proposal
(Markdown) and the HTML report — all three extracted from the *same* view model, so they are
consistent by construction and contain no figure the record does not.

`generate_for_run` is a thin convenience that unpacks a `QuoteRunResult` (the object the quote
pipeline returns). It is duck-typed (reads ``.quote`` / ``.site``) so this module stays light and
free of the heavy pipeline imports.

No wall-clock enters the artifacts: all timing/provenance comes from the record (``created_at`` is
record metadata), so regenerating from the same record is byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gridq.ingestion.improvement_asks import build_ranked_data_asks
from gridq.outputs.consulting_report import render_report_for_quote
from gridq.outputs.html_report import render_html_report
from gridq.outputs.proposal import render_customer_proposal
from gridq.outputs.prose import ProseDrafter, default_prose_drafter
from gridq.outputs.underwriting import render_underwriting_memo
from gridq.outputs.viewmodel import SensitivityRow, build_view_model
from gridq.schemas import QuoteRecord, SiteModel

if TYPE_CHECKING:
    from gridq.quotes import QuoteRunResult


@dataclass(frozen=True)
class OutputArtifacts:
    """The documents generated from one run record.

    ``consulting_report_html`` is the client-facing feasibility validation (additive): a resolved
    run produces it automatically rather than leaving it to a caller who remembers to ask, so the
    deliverable follows the validation instead of being assembled by hand.
    """

    memo_markdown: str
    proposal_markdown: str
    report_html: str
    consulting_report_html: str = ""


def generate_artifacts(
    quote: QuoteRecord,
    site: SiteModel | None = None,
    *,
    prose_drafter: ProseDrafter | None = None,
    sensitivities: tuple[SensitivityRow, ...] = (),
    assumptions: object | None = None,
    require_persisted: bool = False,
    provenance_claim: str | None = None,
    run_store_root: object | None = None,
) -> OutputArtifacts:
    """Generate the memo, proposal and HTML report from a `QuoteRecord` (+ optional `SiteModel`).

    ``prose_drafter`` defaults to the deterministic, number-free v1 drafter (no LLM). A perimeter
    LLM drafter implementing `ProseDrafter` may be injected; it can only ever change prose, never a
    figure. ``assumptions`` (W8) is the versioned library matching ``quote.assumption_version``:
    when supplied it (a) threads per-assumption source/status/confidence into the artifacts
    (C020/C023) and (b) auto-builds the Low/Central/High sensitivity rows from the record's own
    case via the scenario engine — unless ``sensitivities`` are supplied explicitly. Without it,
    both render their honest pending notes (never guessed).

    ``require_persisted`` refuses outright, with `gridq.quotes.UnpersistedRunError`, when the run's
    record is not on disk where a reader could fetch it. **Any caller writing these documents to a
    file for someone else to read must pass it**, and the sanctioned publication paths already
    guarantee it another way (`gridq.quotes.publish_run` persists first and renders from what it
    read back; `gridq.outputs.render_publishable_pack` takes a run id and refuses without one). It
    defaults off only because this function is also the in-memory primitive that the pipeline and
    the test suite render through, and flipping the default is a change to those callers, not to
    this one. The pack is never able to *claim* verifiability it lacks either way: the consulting
    report's footer states the receipt's claim when a receipt exists, and says the page is a working
    render rather than a publishable pack when one does not.

    ``provenance_claim`` is that receipt's claim, for a caller that has just written the run and
    therefore already holds it. Passing it is not an optimisation: a caller that persisted to a
    store of its own choosing knows where the evidence is and this function does not, so without it
    the footer would disclaim a pack written beside its own receipt. ``run_store_root`` says where
    to look when no claim is supplied.
    """
    if require_persisted:
        from gridq.quotes.persistence import require_persisted_run

        require_persisted_run(quote.quote_id)
    drafter = prose_drafter or default_prose_drafter
    # Precedence: explicit caller > record-persisted (quote.scenario_sensitivities, which
    # build_view_model reads) > library-computed here. Never shadow record-traceable rows
    # with generator-authored ones.
    if (
        assumptions is not None
        and not sensitivities
        and not quote.scenario_sensitivities
        and quote.financial_case is not None
    ):
        from gridq.outputs.sensitivities import scenario_sensitivity_rows

        sensitivities = scenario_sensitivity_rows(quote.financial_case, assumptions)  # type: ignore[arg-type]
    vm = build_view_model(quote, site, sensitivities=tuple(sensitivities), assumptions=assumptions)
    # The consulting report reads the record directly for the confidence band, the Gate-2 basis and
    # the two persisted engine projections, so it is rendered from the record rather than the view
    # model. The ranked data asks are site-derived and deterministic; without a site the report
    # keeps its honest "no ranked data ask" seam rather than an empty table.
    data_asks = () if site is None else tuple(build_ranked_data_asks(site))
    return OutputArtifacts(
        # The sizing grid and the dispatch profile are the two persisted engine projections the
        # view model does not expose, and the memo and the HTML report publish the full sizing
        # surface and the dispatch day from them (D59). Passed from the record here so a caller
        # cannot forget one and silently ship a pack with no surface.
        memo_markdown=render_underwriting_memo(vm, drafter=drafter, sizing_grid=quote.sizing_grid),
        proposal_markdown=render_customer_proposal(
            vm, drafter=drafter, sizing_grid=quote.sizing_grid
        ),
        report_html=render_html_report(
            vm,
            drafter=drafter,
            sizing_grid=quote.sizing_grid,
            dispatch_profile=quote.dispatch_profile,
        ),
        consulting_report_html=render_report_for_quote(
            quote,
            site,
            assumptions=assumptions,
            data_asks=data_asks,
            run_store_root=run_store_root,
            provenance_claim=provenance_claim,
        ),
    )


def generate_for_run(
    run: QuoteRunResult,
    *,
    prose_drafter: ProseDrafter | None = None,
    sensitivities: tuple[SensitivityRow, ...] = (),
) -> OutputArtifacts:
    """Generate all three artifacts from a `QuoteRunResult` (unpacks its quote + site).

    Auto-resolves the run's pinned assumption release (by ``quote.assumption_version``) so the
    per-assumption sources + scenario sensitivities render with no caller change; an
    unresolvable version degrades to the pending notes (never guessed).
    """
    assumptions: object | None = None
    if run.quote.assumption_version:
        from gridq.assumptions import AssumptionError, load_assumptions

        try:
            assumptions = load_assumptions(run.quote.assumption_version)
        except AssumptionError:
            assumptions = None  # honest degrade — the artifacts keep their pending notes
    return generate_artifacts(
        run.quote,
        run.site,
        prose_drafter=prose_drafter,
        sensitivities=sensitivities,
        assumptions=assumptions,
    )


__all__ = ["OutputArtifacts", "generate_artifacts", "generate_for_run"]
