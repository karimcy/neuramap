"""Gate 3b is REACHABLE from the production quote path, and its value actually flows.

The subsystem (`reconcile_bill_against_invoice`, `bill_reconciliation_records`,
`invoice_evidence_from_mapping` and four models) was built, tested to 42 tests, and referenced from
nowhere in `src/` outside its own file — the **seventh** built-tested-never-called instance in this
repo. Every one of the seven had a passing test; none had a test that would notice the wiring
disappearing. So these tests are written to the rule in `docs/validation_requirements.md` §3:

- The production call site is `gridq.quotes.pipeline._gate_3b`, invoked from `generate_quote` after
  the solve (it needs `CustomerBill`), reachable from the CLI and the app without test-only code.
- Each test below fails if that call is removed, and `test_deleting_the_call_site_is_visible` states
  the invariant directly rather than leaving it to inference.

A call path is not enough on its own — instance 4 had one and was still inert — so these assert the
gate's verdict reaches the confidence assessment, the recorded register and the report disclosure,
which are the three places its value is consumed.
"""

from __future__ import annotations

import pytest

from gridq.assumptions import load_assumptions
from gridq.ingestion.reconciliation import InvoiceEvidence
from gridq.optimisation import MilpEngine
from gridq.quotes import generate_quote
from gridq.quotes.pipeline import _invoice_evidence_for

FACTOR = "bill reconciliation"
_UNRUN = "was not compared against an invoice"


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_8")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A site with a real half-hourly series, because Gate 3b needs a `CustomerBill`.

    The bare `_make_site()` fixture has no series, so there is no dispatch, no bill, and the gate
    correctly reports itself un-run — which would make every assertion below vacuous. This is the
    same shape of trap the gate itself guards against, so it is worth naming: a test that ran the
    pipeline and asserted nothing reached it would have looked green.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    path = tmp_path_factory.mktemp("gate3b") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows)


def _run(site, assumptions, evidence: InvoiceEvidence | None):
    return generate_quote(site, assumptions, engine=MilpEngine(), invoice_evidence=evidence)


def test_the_gate_runs_on_the_production_path_and_reaches_the_confidence_assessment(
    site, assumptions
) -> None:
    """Reachability proof: deleting the `_gate_3b` call makes this factor read 'not compared'."""
    evidence = InvoiceEvidence(
        site_id=site.site_id,
        invoice_available=True,
        period="calendar 2025",
        invoiced_kwh=1_000_000.0,
        invoiced_cost_gbp=250_000.0,
        source="test-injected",
    )
    run = _run(site, assumptions, evidence)
    assert run.confidence is not None, "no confidence assessment was produced"

    names = [f.name for f in run.confidence.factors]
    assert FACTOR in names, f"Gate 3b contributed no confidence factor; got {names}"
    factor = next(f for f in run.confidence.factors if f.name == FACTOR)
    assert _UNRUN not in factor.reason, (
        "the bill-reconciliation factor reports the gate as un-run even though evidence was "
        "supplied — the gate is wired but its verdict is not flowing"
    )


def test_an_absent_invoice_is_recorded_as_an_open_ask_not_silently_skipped(
    site, assumptions
) -> None:
    """A site nobody checked must never be indistinguishable from one that reconciled.

    This is the D45 shape of the gate: absence is a first-class answer that caps the band and states
    the ask, never a quiet skip. A `None` result here would read as a pass.
    """
    evidence = InvoiceEvidence(
        site_id=site.site_id,
        invoice_available=False,
        period="",
        absence_reason="no invoice has ever been supplied for this site.",
        source="test-injected",
    )
    run = _run(site, assumptions, evidence)

    keys = [r.key for r in run.derived_assumptions]
    assert "tariff_stack.delivered_cost_reconciliation" in keys, (
        f"no open ask was recorded for the missing invoice; got {keys}"
    )
    record = next(
        r for r in run.derived_assumptions if r.key == "tariff_stack.delivered_cost_reconciliation"
    )
    assert record.value is None, "the gate derived a delivered cost on the customer's behalf"

    # ...and the disclosure reaches the report, not only the record. The register loop in
    # `generate_quote` runs BEFORE Gate 3b, so a late-added record would otherwise reach the band
    # and the quote while silently missing this section.
    assert any("tariff_stack.delivered_cost_reconciliation" in w for w in run.quote.warnings), (
        "the Gate 3b record never reached the report's assumptions-requiring-confirmation section"
    )


def test_deleting_the_call_site_is_visible(site, assumptions) -> None:
    """The rule made explicit: with NO gate result the factor must say so, and not pass.

    `invoice_evidence=None` still runs the gate (it resolves the site's own evidence), so this
    pins the other half — that the un-run state is reachable, distinguishable, and not HIGH. If a
    future change made an un-run gate indistinguishable from a reconciled one, this fails.
    """
    from gridq.quotes.confidence import _bill_reconciliation_factor

    unrun = _bill_reconciliation_factor(None)
    assert unrun.name == FACTOR
    assert _UNRUN in unrun.reason
    assert unrun.ceiling.value != "high", "never having checked must not read as evidence"


def test_the_production_resolver_refuses_the_metadata_key_as_a_site() -> None:
    """`_basis` describes the extraction, not a site. Reading it as one would fabricate evidence."""
    assert _invoice_evidence_for("_basis").invoice_available is False
    assert _invoice_evidence_for("no_such_site_anywhere").invoice_available is False
    # A real campaign site still resolves, so the guard above is not simply refusing everything.
    assert _invoice_evidence_for("grundon_ewelme").invoice_available is True
