"""Bankable deliverable floor (L05 · M8 outputs).

System Brief acceptance test 1: "P50/P90 and DSCR are mandatory; point estimates are not
deliverables." An artifact must never present as bankable without all three. These tests prove the
guard: a record that *claims* bankable but lacks P50/P90/DSCR is downgraded to an honest
not-bankable (with a warning naming the gap), while a record that carries the full trio stays
bankable. The guard never fabricates the missing figure to keep the badge.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.outputs import build_view_model, generate_artifacts

_BANKABLE = {
    "bankable_status": s.BankableStatus.BANKABLE_CANDIDATE,
    "implementation_level": s.ImplementationLevel.PRODUCTION,
}


def test_artifact_refuses_bankable_without_p50_p90_and_dscr(rich_quote) -> None:
    # Claim bankable on the result but strip the financial case → no P50/P90, no DSCR.
    bankable_result = rich_quote.result.model_copy(update=_BANKABLE)
    quote = rich_quote.quote.model_copy(
        update={"optimisation_result": bankable_result, "financial_case": None}
    )
    vm = build_view_model(quote, rich_quote.site)

    assert vm.cashflows.year0_p90 is None
    assert vm.dscr.min_dscr is None
    assert vm.bankable is False  # downgraded despite the record claiming bankable
    assert any("mandatory" in w.lower() for w in vm.warnings)

    arts = generate_artifacts(quote, rich_quote.site)
    assert "NOT BANKABLE" in arts.memo_markdown
    assert 'class="banner warn"' in arts.report_html
    assert "BANKABLE CANDIDATE" not in arts.memo_markdown


def test_artifact_is_bankable_when_the_mandatory_trio_is_present(rich_quote) -> None:
    # The positive control: rich_quote carries cashflows (P50/P90) and a DSCR (debt injected).
    bankable_result = rich_quote.result.model_copy(update=_BANKABLE)
    quote = rich_quote.quote.model_copy(update={"optimisation_result": bankable_result})
    vm = build_view_model(quote, rich_quote.site)

    assert vm.cashflows.year0_p50 is not None
    assert vm.cashflows.year0_p90 is not None
    assert vm.dscr.min_dscr is not None
    assert vm.bankable is True
    assert not any("mandatory" in w.lower() for w in vm.warnings)
