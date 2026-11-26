"""Customer proposal cites term and structure (L19 · M8 outputs).

Output 3 (the customer proposal) must state "term, structure" in plain language. Both are
rendered from the view model; these tests pin them to the record so a regression dropping either
fails (the artifact-consistency test deliberately covers size/price/value, not term/structure).
"""

from __future__ import annotations

import gridq.outputs.formatting as fmt
from gridq.outputs import build_view_model, generate_artifacts


def test_proposal_cites_commercial_structure(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    proposal = generate_artifacts(rich_quote.quote, rich_quote.site).proposal_markdown
    assert "Commercial structure" in proposal
    assert fmt.structure_label(vm.price.structure) in proposal


def test_proposal_cites_the_modelled_term(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    proposal = generate_artifacts(rich_quote.quote, rich_quote.site).proposal_markdown
    assert "Term (modelled)" in proposal
    assert vm.cashflows.operating_years is not None
    assert fmt.years(vm.cashflows.operating_years) in proposal
    # The term is honestly labelled as the modelling horizon, not the contracted term.
    assert "contracted term" in proposal
    assert "confirmed with the commercial terms at signature" in proposal
