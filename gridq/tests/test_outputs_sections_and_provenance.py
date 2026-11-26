"""M8 outputs — required sections, provenance, warnings and a clear firm-vs-conditional split.

These pin the deliverable's shape: the underwriting pack and HTML report carry every section the
System Brief names (Output 2/3), the provenance spine (hashes + versions) and every warning, and
the contracted-floor vs merchant-upside split is unambiguous with unlock conditions.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

import gridq.outputs.formatting as fmt
import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.outputs import generate_artifacts, generate_for_run
from gridq.quotes import generate_quote

#: Every section the memo must carry, in the order the owner specified (D59, 2026-08-18): the IRR
#: and
#: the revenue breakdown first, then the methodology, then the assumptions register, then the
#: evidence sections. Asserted BY POSITION below, so a renderer that emits all of them in the wrong
#: order fails rather than passing a membership check.
_MEMO_SECTIONS = (
    "## Project IRR, returns and price to target",
    "## P50 / P90 cashflows",
    "## DSCR & covenant",
    "## Contracted vs merchant",
    "## Assumptions behind each revenue stream",
    "## System specification",
    "## The full sizing surface, and how the selection was made",
    "## Methodology: how these figures were produced",
    "# Assumptions: every assumption this pack rests on",
    "## Debt assumptions",
    "## Counterparty credit",
    "## Consents & physical gates",
    "## Provenance",
    "## Warnings",
)

#: The proposal's sections, in the owner's order (D59). The banners and the intro sit above them
#: and stay there: they are safety labels, not evidence, and a customer must not meet a
#: study-basis return before being told the document is not an offer.
_PROPOSAL_SECTIONS = (
    "## The project's return",
    "## What's secured vs what's upside",
    "## Assumptions behind each revenue stream",
    "## Your bill: before & after",
    "## Your system",
    "## Your offer",
    "## The full sizing surface, and how the selection was made",
    "## Methodology: how these figures were produced",
    "# Assumptions: every assumption this pack rests on",
    "## Next steps",
)


def test_memo_has_all_required_sections_in_the_owners_order(rich_quote) -> None:
    memo = generate_artifacts(rich_quote.quote, rich_quote.site).memo_markdown
    for heading in _MEMO_SECTIONS:
        assert heading in memo, f"missing memo section: {heading}"
    positions = [memo.index(heading) for heading in _MEMO_SECTIONS]
    assert positions == sorted(positions), (
        "memo sections must appear in the order the owner specified: the IRR and revenue "
        "breakdown, then the methodology, then the assumptions register, then the evidence"
    )


def test_proposal_has_all_required_sections_in_the_owners_order(rich_quote) -> None:
    proposal = generate_artifacts(rich_quote.quote, rich_quote.site).proposal_markdown
    for heading in _PROPOSAL_SECTIONS:
        assert heading in proposal, f"missing proposal section: {heading}"
    positions = [proposal.index(heading) for heading in _PROPOSAL_SECTIONS]
    assert positions == sorted(positions), (
        "proposal sections must appear in the order the owner specified"
    )


def test_provenance_present_in_memo_and_html(rich_quote) -> None:
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    q = rich_quote.quote
    for doc in (arts.memo_markdown, arts.report_html):
        assert q.quote_id in doc
        assert q.input_hash is not None and q.input_hash in doc
        assert q.assumption_version is not None and q.assumption_version in doc
        assert q.code_version is not None and q.code_version in doc
        assert q.schema_version in doc


def test_every_warning_is_surfaced(make_site: Callable[..., s.SiteModel]) -> None:
    """Every warning reaches every artifact in full, with one transform: the dash rewrite.

    **The comparison is against `fmt.no_dashes(warning)`, and that is a deliberate change (D59).**
    The memo and the HTML report used to be exempt from the document-level scrub so that a warning
    appeared byte-for-byte as the engine wrote it. The owner has now instructed three times that no
    report may carry an em dash, and that instruction outranks a typographic exemption. `no_dashes`
    rewrites nothing but the em and en dash characters, the RECORD is untouched, and the guarantee
    this test exists to hold is unchanged: no warning is filtered, shortened or summarised.
    """
    # The baseline quote carries many warnings (placeholders, DSCR unavailable, not bankable).
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"), contributor="Olly")
    assert run.quote.warnings  # sanity
    arts = generate_for_run(run)
    for warning in run.quote.warnings:
        assert fmt.no_dashes(warning) in arts.memo_markdown
        # The HTML report HTML-escapes free text, so compare against the escaped form.
        assert escape(fmt.no_dashes(warning)) in arts.report_html


def test_firm_vs_conditional_is_clear(rich_quote) -> None:
    # Underwritten (firm) pack — gated streams are cited only here; the fast indicative path omits
    # them (test_outputs_indicative_omits_gated).
    quote = rich_quote.quote.model_copy(update={"quote_mode": s.QuoteMode.UNDERWRITTEN})
    arts = generate_artifacts(quote, rich_quote.site)
    for doc in (arts.memo_markdown, arts.report_html):
        # The two sides are explicitly named and separated.
        assert "Contracted floor (priced)" in doc
        assert "Merchant upside (not priced" in doc
        # Firm/contracted streams sit in the floor; conditional/gated sit in upside with an unlock.
        assert "Tariff arbitrage" in doc  # firm → floor
        assert "Capacity Market" in doc  # contracted → floor
        assert "Local flexibility (Piclo)" in doc  # conditional → upside
        assert "Wholesale (P415)" in doc  # gated → upside
        assert "RTM partner + export consents" in doc  # an unlock condition is shown


def test_not_bankable_banner_on_baseline(make_site: Callable[..., s.SiteModel]) -> None:
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"), contributor="Olly")
    arts = generate_for_run(run)
    assert "NOT BANKABLE" in arts.memo_markdown
    assert 'class="banner warn"' in arts.report_html


def test_bankable_banner_when_result_is_bankable_candidate(rich_quote) -> None:
    # Flip only the optimisation result's status to a bankable candidate (synthetic) and confirm
    # the banner switches — proving the banner is driven by the record, not hard-coded.
    bankable_result = rich_quote.result.model_copy(
        update={
            "implementation_level": s.ImplementationLevel.PRODUCTION,
            "bankable_status": s.BankableStatus.BANKABLE_CANDIDATE,
        }
    )
    quote = rich_quote.quote.model_copy(update={"optimisation_result": bankable_result})
    arts = generate_artifacts(quote, rich_quote.site)
    assert "BANKABLE CANDIDATE" in arts.memo_markdown
    assert 'class="banner ok"' in arts.report_html
