"""Indicative artifacts never cite *unconsented* gated streams (L13 · M8 outputs).

System Brief, indicative lifecycle: "Banded outputs from minimal data, an expiry date, never cites
gated streams." The fast/banded path (draft, indicative) omits the dedicated gated-upside streams
from all three artifacts; the firm (underwritten+) pack cites them. Moves the brief's "indicative
must not cite gated streams" from a v1 process convention to enforced, tested code.

Revenue-Data Drive slice 2c relaxes exactly one case: a gated stream the customer has explicitly
*consented* to (``consent_status == "given"``) is cited in every mode (including indicative), so
the "consent given" basis is never hidden. An *unconsented* gated stream is still omitted from the
indicative artifacts (the regression below pins that).
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.outputs import build_view_model, generate_artifacts

_GATED_LABEL = "Wholesale (P415)"  # rich_quote's gated_upside entry
_GATED_UNLOCK = "RTM partner + export consents"


def _consent_gated_quote(rich_quote) -> s.QuoteRecord:
    """rich_quote with its single gated-upside stream marked consented (else identical)."""
    consented = rich_quote.result.gated_upside[0].model_copy(update={"consent_status": "given"})
    result = rich_quote.result.model_copy(update={"gated_upside": [consented]})
    return rich_quote.quote.model_copy(update={"optimisation_result": result})


def test_indicative_quote_omits_unconsented_gated_streams(rich_quote) -> None:
    assert rich_quote.quote.quote_mode == s.QuoteMode.INDICATIVE
    # The record still carries the gated stream; the indicative *artifacts* must not cite it.
    assert rich_quote.result.gated_upside
    assert rich_quote.result.gated_upside[0].consent_status is None  # unconsented
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
        assert _GATED_LABEL not in doc
        assert _GATED_UNLOCK not in doc
    # The conditional (non-gated) merchant stream is still shown — only gated streams are omitted.
    assert "Local flexibility (Piclo)" in arts.memo_markdown


def test_indicative_quote_cites_a_consented_gated_stream(rich_quote) -> None:
    """The one relaxation: a consented gated stream IS cited even on the indicative path."""
    quote = _consent_gated_quote(rich_quote)
    assert quote.quote_mode == s.QuoteMode.INDICATIVE
    arts = generate_artifacts(quote, rich_quote.site)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
        assert _GATED_LABEL in doc  # the consented gated stream is now cited
    # And it is annotated as consent-given (authorisation, not a revenue guarantee).
    assert "Consent given" in arts.memo_markdown
    assert "Consent given" in arts.report_html
    assert "(consent given)" in arts.proposal_markdown


def test_underwritten_quote_cites_gated_streams(rich_quote) -> None:
    quote = rich_quote.quote.model_copy(update={"quote_mode": s.QuoteMode.UNDERWRITTEN})
    arts = generate_artifacts(quote, rich_quote.site)
    assert _GATED_LABEL in arts.memo_markdown
    assert _GATED_UNLOCK in arts.memo_markdown


def test_split_drops_gated_only_for_banded_modes(rich_quote) -> None:
    gated_labels = {s.MarketStream.WHOLESALE_P415.value}

    indicative_vm = build_view_model(rich_quote.quote, rich_quote.site)
    indicative_streams = {r.stream for r in indicative_vm.split.upside}
    assert not (gated_labels & indicative_streams)

    underwritten = rich_quote.quote.model_copy(update={"quote_mode": s.QuoteMode.UNDERWRITTEN})
    underwritten_vm = build_view_model(underwritten, rich_quote.site)
    underwritten_streams = {r.stream for r in underwritten_vm.split.upside}
    assert gated_labels & underwritten_streams


def test_split_cites_consented_gated_in_indicative_mode(rich_quote) -> None:
    """A consented gated stream is cited in the indicative split; the mode gate governs only
    unconsented gated rows."""
    quote = _consent_gated_quote(rich_quote)
    assert quote.quote_mode == s.QuoteMode.INDICATIVE
    vm = build_view_model(quote, rich_quote.site)
    upside = {r.stream for r in vm.split.upside}
    assert s.MarketStream.WHOLESALE_P415.value in upside
    assert vm.split.has_consented
    row = next(r for r in vm.split.upside if r.stream == s.MarketStream.WHOLESALE_P415.value)
    assert row.consent_status == "given"
