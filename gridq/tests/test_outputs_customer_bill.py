"""Tests for the bill rendering (W7-5 slice 2, closes C017).

Invariants: the proposal/report headline is the RECORD's number verbatim (never recomputed —
the view copies, the templates format); the basis honesty label always travels with the
headline; a record without a persisted bill keeps the labelled-pending seam unchanged.
"""

from __future__ import annotations

from gridq.outputs import formatting as fmt
from gridq.outputs.html_report import render_html_report
from gridq.outputs.proposal import render_customer_proposal
from gridq.outputs.viewmodel import build_view_model
from gridq.schemas import CustomerBill

_BILL = CustomerBill(
    annual_bill_before_gbp=100_000.0,
    annual_bill_after_gbp=88_500.0,
    annual_saving_gbp=11_500.0,
    energy_before_gbp=60_000.0,
    energy_after_gbp=53_000.0,
    duos_before_gbp=25_000.0,
    duos_after_gbp=21_000.0,
    levies_before_gbp=15_000.0,
    levies_after_gbp=14_500.0,
    import_before_kwh=500_000.0,
    import_after_kwh=515_000.0,
    levy_rate_gbp_per_kwh=0.03,
    basis="Modelled lines only — standing charges NOT modelled (test label).",
)


def _quote_with_bill(rich_quote):
    return rich_quote.quote.model_copy(update={"customer_bill": _BILL})


def test_view_model_copies_the_persisted_bill_verbatim(rich_quote) -> None:
    vm = build_view_model(_quote_with_bill(rich_quote))
    b = vm.bill_savings
    assert b.available is True
    assert b.bill_before_gbp == _BILL.annual_bill_before_gbp
    assert b.bill_after_gbp == _BILL.annual_bill_after_gbp
    assert b.annual_savings_gbp == _BILL.annual_saving_gbp
    assert b.basis == _BILL.basis


def test_proposal_headline_is_the_record_number_with_the_basis_label(rich_quote) -> None:
    proposal = render_customer_proposal(build_view_model(_quote_with_bill(rich_quote)))
    assert fmt.gbp(_BILL.annual_bill_before_gbp) in proposal
    assert fmt.gbp(_BILL.annual_bill_after_gbp) in proposal
    assert fmt.gbp(_BILL.annual_saving_gbp) in proposal
    assert "standing charges NOT modelled" in proposal  # the honesty label travels
    assert "not yet computed" not in proposal.split("## Your bill")[1].split("##")[0]


def test_html_report_renders_the_bill_with_the_basis_note(rich_quote) -> None:
    html = render_html_report(build_view_model(_quote_with_bill(rich_quote)))
    assert fmt.gbp(_BILL.annual_saving_gbp) in html
    assert "standing charges NOT modelled" in html


def test_record_without_a_bill_keeps_the_pending_seam(rich_quote) -> None:
    vm = build_view_model(rich_quote.quote)  # the fixture record has no customer_bill
    assert vm.bill_savings.available is False
    assert vm.bill_savings.basis is None
    proposal = render_customer_proposal(vm)
    assert "not yet computed" in proposal
    assert "What we need from you" in proposal


def test_peak_row_renders_with_the_not_priced_label(rich_quote) -> None:
    bill = _BILL.model_copy(update={"peak_import_before_kw": 320.0, "peak_import_after_kw": 250.0})
    quote = rich_quote.quote.model_copy(update={"customer_bill": bill})
    proposal = render_customer_proposal(build_view_model(quote))
    assert "Peak import (measured)" in proposal
    assert "not priced until the peak-protection objective" in proposal
    # Without peaks (older records), the row and note stay absent — no invented physics.
    older = render_customer_proposal(build_view_model(_quote_with_bill(rich_quote)))
    assert "Peak import (measured)" not in older
