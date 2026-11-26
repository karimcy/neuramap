"""Gate 3b's full verdict reaches the caller, not just its summary string.

`QuoteRunResult` already carried Gate 3's `ReconciliationResult` whole, but Gate 3b's
`BillReconciliationResult` died inside `generate_quote`: it was computed, handed to
`assess_confidence`, and never returned. Everything downstream therefore saw Gate 3b only through
whatever `summary()` text reached `quote.warnings` — and `summary()` is built from the
**non-``ok``** findings only, so on a site whose bill reconciles the record carries *nothing at all*
about the check. A renderer could not show the per-limb view (volume / annual cost / monthly, each
with its own severity, signed variance and both compared sides) that the load side can.

Written to `docs/validation_requirements.md` §3, because the thing being fixed here is one hop of a
value flow and that is exactly the shape this repo has shipped inert eleven times:

- Production call site: ``bill_reconciliation=bill_reconciliation`` in the `QuoteRunResult` return
  of `gridq.quotes.pipeline.generate_quote` (`src/gridq/quotes/pipeline.py:590`), reachable with no
  test-only code from the CLI's ``run`` command (`cli.py`, via `_render_run_summary`), the web app's
  run screen and its `app/upside.py` re-run. Those are named by function, not line: they are other
  agents' files and their line numbers rot.
- Delete that one keyword argument — the failure shape that matters, since the *field* would still
  exist and any attribute-existence test would stay green — and
  `test_generate_quote_carries_the_gate_3b_verdict_whole` and
  `test_the_per_limb_detail_reaches_the_caller_and_the_warnings_alone_cannot` both go red.

The second test is the one that states *why* the field earns its place: it asserts on an
``ok``-severity limb, which by construction can never reach `quote.warnings` through any route
(`summary()` filters it, `bill_reconciliation_records` records only errors and the no-invoice state,
and the confidence factor's reason is built from `summary()`).
"""

from __future__ import annotations

import pytest

from gridq.assumptions import load_assumptions
from gridq.ingestion.reconciliation import InvoiceEvidence
from gridq.optimisation import MilpEngine
from gridq.quotes import generate_quote

_ABSENCE_REASON = "no invoice has ever been supplied for this site."


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_8")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A site with a real half-hourly series, because Gate 3b needs a `CustomerBill`.

    Same trap `test_gate_3b_wired.py` names: the bare `make_site()` fixture has no series, so there
    is no dispatch, no bill, and the gate honestly reports itself un-run. Every assertion below
    would then be vacuous while looking green.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    path = tmp_path_factory.mktemp("carries_gate3b") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def no_invoice_run(site, assumptions):
    """Solve 1: the gate runs, and finds this site has no invoice on file."""
    evidence = InvoiceEvidence(
        site_id=site.site_id,
        invoice_available=False,
        period="",
        absence_reason=_ABSENCE_REASON,
        source="test-injected",
    )
    return generate_quote(site, assumptions, engine=MilpEngine(), invoice_evidence=evidence)


@pytest.fixture(scope="module")
def matched_run(site, assumptions, no_invoice_run):
    """Solve 2: an invoice that matches the modelled bill exactly, so a limb comes back ``ok``.

    The modelled figures are read off solve 1's own `CustomerBill` rather than hard-coded: both
    are the dispatch-invariant BEFORE side, so they are identical across the two solves, and a
    hard-coded pair would silently drift into a ``warning`` limb the day the tariff stack moves and
    quietly stop testing the thing this file exists to test.
    """
    bill = no_invoice_run.customer_bill
    assert bill is not None, "no CustomerBill was produced, so Gate 3b could not have run"
    evidence = InvoiceEvidence(
        site_id=site.site_id,
        invoice_available=True,
        period="calendar 2025",
        invoiced_kwh=bill.import_before_kwh,
        invoiced_cost_gbp=bill.annual_bill_before_gbp,
        source="test-injected",
    )
    return generate_quote(site, assumptions, engine=MilpEngine(), invoice_evidence=evidence)


def test_generate_quote_carries_the_gate_3b_verdict_whole(no_invoice_run, site) -> None:
    """The wiring anchor: the gate's own result object reaches the caller, not a rendering of it."""
    verdict = no_invoice_run.bill_reconciliation

    assert verdict is not None, (
        "Gate 3b ran (a CustomerBill exists) but its verdict never reached QuoteRunResult — the "
        "`bill_reconciliation=` argument is missing from generate_quote's return"
    )
    assert verdict.site_id == site.site_id, "the carried verdict belongs to a different site"
    assert verdict.findings, "the verdict arrived with no findings, so nothing per-limb is carried"


def test_the_per_limb_detail_reaches_the_caller_and_the_warnings_alone_cannot(
    matched_run,
) -> None:
    """Why the field exists: an ``ok`` limb is invisible in `quote.warnings` by construction.

    `BillReconciliationResult.summary()` builds its text from ``severity != "ok"`` findings, so a
    limb that reconciles has no route into the record at all — not through the Gate 3b warning line,
    not through `bill_reconciliation_records` (errors and no-invoice only), and not through the
    confidence factor's reason (which is `summary()`). If a renderer is to show "volume: modelled X
    vs invoiced Y, +0.00%, reconciles" the way the load-reconciliation section shows its detail, it
    has to come from here.
    """
    verdict = matched_run.bill_reconciliation
    assert verdict is not None, "Gate 3b's verdict is not carried on the run result"

    ok_limbs = [f for f in verdict.findings if f.severity == "ok"]
    assert ok_limbs, (
        "the invoice was built from this run's own modelled bill, so at least one limb must "
        f"reconcile; got {[(f.field, f.severity) for f in verdict.findings]}"
    )

    limb = ok_limbs[0]
    # The itemisation a renderer needs, and none of it is a string a reader could parse back out.
    assert limb.field, "the limb does not name itself"
    assert limb.modelled is not None and limb.invoiced is not None, (
        "an ok limb must carry both compared sides, not just a verdict"
    )
    assert limb.variance_pct == pytest.approx(0.0, abs=1e-9), (
        f"invoice matched the modelled bill exactly, so variance must be 0.0; got "
        f"{limb.variance_pct}"
    )

    warnings = matched_run.quote.warnings
    assert not any(limb.message in w for w in warnings), (
        "an ok limb's message reached quote.warnings, which would mean this field is redundant; "
        "the assertion below about it being absent is the point of carrying the object"
    )
    assert limb.message in [f.message for f in verdict.findings], (
        "the ok limb's message is not on the carried verdict either, so it is reachable from "
        "nowhere"
    )


def test_an_un_run_gate_and_a_site_with_no_invoice_are_different_absences(
    make_site, assumptions, no_invoice_run
) -> None:
    """The two-level tri-state, pinned so a renderer cannot merge them into one sentence.

    ``bill_reconciliation is None`` means the gate did NOT run: no dispatch produced a
    `CustomerBill`, so nothing was compared. A present verdict whose ``reconciles`` is ``None``
    means the gate DID run and found the customer has supplied no invoice. Both cap the band and
    neither is a pass, but they are facts about different things and carry different asks — merging
    them would report our own missing dispatch as the customer's missing paperwork.
    """
    # No half-hourly series and the placeholder engine: no dispatch, so no bill, so no comparison.
    un_run = generate_quote(make_site(), assumptions)
    assert un_run.customer_bill is None, "this fixture was meant to produce no bill"
    assert un_run.bill_reconciliation is None, "an un-run gate must not present a verdict"

    ran_but_no_invoice = no_invoice_run.bill_reconciliation
    assert ran_but_no_invoice is not None, "the gate ran, so its verdict must be carried"
    assert ran_but_no_invoice.invoice_available is False
    assert ran_but_no_invoice.reconciles is None, (
        "a site with no invoice has not passed and has not failed; it was never checked"
    )
    assert _ABSENCE_REASON in ran_but_no_invoice.summary(), (
        "the recorded reason for the absence did not survive the carry"
    )
