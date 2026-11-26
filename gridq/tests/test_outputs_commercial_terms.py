"""Commercial structure is explicit: term, indexation, buyout (L22 · M8 outputs).

System Brief: commercial structures "with term, indexation and the buyout schedule explicit." The
proposal previously named only term + structure; these tests pin that indexation and the buyout
schedule are also named in the offer — explicitly as confirmed-at-signature, never invented (the
actual values and PPA structure design are human-gated, H06).
"""

from __future__ import annotations

from gridq.outputs import generate_artifacts


def test_proposal_names_indexation_and_buyout(rich_quote) -> None:
    proposal = generate_artifacts(rich_quote.quote, rich_quote.site).proposal_markdown
    assert "Indexation" in proposal
    assert "Buyout schedule" in proposal
    # Explicit but honest — confirmed with the commercial terms at signature, not fabricated.
    assert "confirmed" in proposal and "signature" in proposal


def test_indexation_and_buyout_are_not_fabricated(rich_quote) -> None:
    # No invented indexation rate or buyout price — they are named as set-at-signature.
    proposal = generate_artifacts(rich_quote.quote, rich_quote.site).proposal_markdown
    assert "set with the commercial terms at signature" in proposal
