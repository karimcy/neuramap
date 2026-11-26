"""Three artifacts consistent by construction (L04 · M8 outputs).

The System Brief: the spec, the underwriting pack and the customer proposal are "the same model
viewed three ways" — size, price and value must agree across them.
`test_outputs_numbers_match_record` already pins each artifact to the *record*; this pins the
three artifacts to *each other*: the same
figure, formatted identically, must appear in the underwriting memo, the proposal and the HTML
report (the spec view). All three render from one view model, so a regression that let any artifact
author its own size/price/value figure would break this.
"""

from __future__ import annotations

import gridq.outputs.formatting as fmt
from gridq.outputs import build_view_model, generate_artifacts


def test_three_artifacts_cite_consistent_size_price_and_value(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    docs = {
        "memo": arts.memo_markdown,
        "proposal": arts.proposal_markdown,
        "report": arts.report_html,
    }

    s = vm.spec
    assert s.selected_power_kw is not None  # the fixture is a sized run

    # Each figure is formatted by the same helper on the same view-model field in all three
    # renderers, so the *token* below must appear verbatim in every artifact that is the
    # "same model viewed three ways". (fmt tokens carry no HTML-special chars, so they survive
    # escaping in the HTML report unchanged.)
    shared_figures = [
        fmt.kw(s.selected_power_kw),  # system size — power
        fmt.kwh(s.selected_energy_kwh),  # system size — energy
        fmt.hours(s.duration_hours),  # system size — duration
        fmt.gbp(vm.split.floor_total_gbp),  # priced floor value / year
        fmt.gbp(vm.split.upside_total_gbp),  # merchant upside value / year
    ]
    if vm.price.value_gbp_per_kwh is not None:
        shared_figures.append(fmt.gbp_per_kwh(vm.price.value_gbp_per_kwh))

    for figure in shared_figures:
        for name, doc in docs.items():
            assert figure in doc, f"{name} does not cite the shared figure {figure!r}"


def test_savings_seam_is_consistently_pending_not_fabricated(rich_quote) -> None:
    # The customer-bill / savings model is a labelled-pending v1 seam: no artifact may author a
    # savings figure while it is pending. All three share the one view model, so the seam is
    # pending everywhere or nowhere.
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert vm.bill_savings.annual_savings_gbp is None
    arts = generate_artifacts(rich_quote.quote, rich_quote.site)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html):
        assert "Estimated annual saving" not in doc
        assert "Annual saving" not in doc
