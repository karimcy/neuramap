"""M8 outputs — end-to-end generation, robustness and honest degradation.

Covers: all three artifacts generate (a) from the real baseline pipeline quote, (b) from the
minimal committed example record (null result + null finance) without exception, and (c) from the
rich synthetic quote; the NOT-BANKABLE banner is present on a baseline run; and degraded figures
render as explicit *unavailable* labels rather than invented values.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.outputs import OutputArtifacts, generate_artifacts, generate_for_run
from gridq.quotes import generate_quote

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "quote_record_example.json"


def _assert_three_artifacts(arts: OutputArtifacts) -> None:
    assert arts.memo_markdown.startswith("# GridQ: Underwriting pack")
    assert arts.proposal_markdown.startswith("# Your battery storage proposal")
    assert arts.report_html.startswith("<!doctype html>")
    assert arts.report_html.rstrip().endswith("</html>")
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
        assert doc.strip()


def test_generates_from_real_baseline_quote(make_site: Callable[..., s.SiteModel]) -> None:
    site = make_site()
    run = generate_quote(site, load_assumptions("gb_v0_1"), contributor="Olly")
    arts = generate_for_run(run)
    _assert_three_artifacts(arts)
    # A baseline run is labelled NOT BANKABLE in every artifact (Done-when criterion).
    assert "NOT BANKABLE" in arts.memo_markdown
    assert "NOT BANKABLE" in arts.report_html
    assert "indicative" in arts.proposal_markdown.lower()


def test_generates_from_minimal_example_record_without_exception() -> None:
    """The committed example record carries null optimisation_result + null financial_case."""
    quote = s.QuoteRecord.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    arts = generate_artifacts(quote)  # no site, no finance, no result — must not raise
    _assert_three_artifacts(arts)
    # Absent figures degrade to a labelled unavailable, never a fabricated 0/number.
    assert "unavailable" in arts.memo_markdown.lower()
    # Provenance still ties the document to the record.
    assert quote.input_hash is not None and quote.input_hash in arts.memo_markdown


def test_generates_from_rich_synthetic_quote(rich_quote) -> None:
    # Underwritten (firm) pack — the full pack cites gated streams; the fast indicative path omits
    # them (covered separately in test_outputs_indicative_omits_gated).
    quote = rich_quote.quote.model_copy(update={"quote_mode": s.QuoteMode.UNDERWRITTEN})
    arts = generate_artifacts(quote, rich_quote.site)
    _assert_three_artifacts(arts)
    # The populated pack shows real, record-derived figures and labels.
    assert "Tariff arbitrage" in arts.memo_markdown
    assert "Wholesale (P415)" in arts.memo_markdown  # gated upside labelled, not in the floor


def test_site_optional_counterparty_section_labelled(rich_quote) -> None:
    # Without the site, the counterparty/consents sections say so rather than inventing them.
    without = generate_artifacts(rich_quote.quote)
    assert "site context not supplied" in without.memo_markdown.lower()
    # With the site, the counterparty identity is shown.
    with_site = generate_artifacts(rich_quote.quote, rich_quote.site)
    assert "Synthetic Demo Ltd" in with_site.memo_markdown
