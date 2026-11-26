"""Renderers say "Consent given" per consented stream (Revenue-Data Drive slice 2c · M8 outputs).

The customer-facing artifacts are record-driven: the "Consent given" wording originates from the
record's ``ValueStreamResult.consent_status`` (carried onto ``StreamRow.consent_status``), never
from a site lookup or a number authored in a template. When the customer has authorised a stream,
the proposal / underwriting memo / HTML report say so — reserving the conditional/upside wording
for streams *without* consent. Consent is authorisation, not a revenue guarantee: a consented
stream is still shown at its own firmness (contracted, never firm for a gated stream) with its
haircut context intact.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.outputs import build_view_model, generate_artifacts

# Streams marked consented in the fully-consented fixture below.
_CONSENTED = {
    s.MarketStream.CAPACITY_MARKET,  # contracted → priced floor
    s.MarketStream.LOCAL_FLEX_PICLO,  # conditional → merchant upside
    s.MarketStream.WHOLESALE_P415,  # gated → cited via consent
}


def _fully_consented_quote(rich_quote) -> s.QuoteRecord:
    """rich_quote with a consent basis on a floor, an upside and a gated stream (else identical)."""
    value_streams = [
        v.model_copy(update={"consent_status": "given"}) if v.stream in _CONSENTED else v
        for v in rich_quote.result.value_streams
    ]
    gated = [
        g.model_copy(update={"consent_status": "given"}) if g.stream in _CONSENTED else g
        for g in rich_quote.result.gated_upside
    ]
    result = rich_quote.result.model_copy(
        update={"value_streams": value_streams, "gated_upside": gated}
    )
    return rich_quote.quote.model_copy(update={"optimisation_result": result})


def test_consent_status_flows_from_record_to_view_model(rich_quote) -> None:
    quote = _fully_consented_quote(rich_quote)
    vm = build_view_model(quote, rich_quote.site)
    assert vm.split.has_consented
    consented_rows = [r for r in (*vm.split.floor, *vm.split.upside) if r.consent_status == "given"]
    # Every stream we consented to surfaces on the split with consent_status == "given".
    consented_streams = {r.stream for r in consented_rows}
    assert consented_streams == {m.value for m in _CONSENTED}


def test_all_three_artifacts_say_consent_given_per_consented_stream(rich_quote) -> None:
    quote = _fully_consented_quote(rich_quote)
    arts = generate_artifacts(quote, rich_quote.site)

    # The underwriting memo + HTML report cite "Consent given" and list the consented streams.
    for doc in (arts.memo_markdown, arts.report_html):
        assert "Consent given" in doc
        assert "Stream consents given" in doc  # the consents-section note
    # The proposal annotates each consented stream label with "(consent given)".
    assert "(consent given)" in arts.proposal_markdown

    # Each consented stream is named in every artifact (proposal, memo, HTML).
    import gridq.outputs.formatting as fmt

    for stream in _CONSENTED:
        label = fmt.stream_label(stream.value)
        for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
            assert label in doc


def test_consented_stream_stays_at_its_own_firmness(rich_quote) -> None:
    """ "Consent given" never elevates firmness: the consented CM stream stays contracted, and the
    consented gated stream can never be firm — the labels are orthogonal."""
    quote = _fully_consented_quote(rich_quote)
    vm = build_view_model(quote, rich_quote.site)
    cm = next(r for r in vm.split.floor if r.stream == s.MarketStream.CAPACITY_MARKET.value)
    assert cm.firmness == "contracted"  # not firm
    gated = next(r for r in vm.split.upside if r.stream == s.MarketStream.WHOLESALE_P415.value)
    assert gated.firmness != "firm"


def test_unconsented_quote_keeps_conditional_wording(rich_quote) -> None:
    """The unconsented control: no "Consent given" wording anywhere; conditional/upside copy
    stands. (The demo carries no consents, so this pins its artifacts unchanged.)"""
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert not vm.split.has_consented
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)

    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
        assert "Consent given" not in doc
        assert "Stream consents given" not in doc
    assert "(consent given)" not in arts.proposal_markdown
    # The conditional/upside framing is still present and truthful.
    assert "not promised" in arts.proposal_markdown
