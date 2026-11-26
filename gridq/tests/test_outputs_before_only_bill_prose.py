"""A BEFORE-only bill must not make the artifacts say things the record does not hold.

**The defect these tests pin, and why the existing suite let it ship.** On 2026-08-15 a run with no
viable configuration started carrying a `CustomerBill` with a real BEFORE side and an absent AFTER
side (`after_side_priced is False`; see `tests/test_before_bill_without_dispatch.py`). Until then a
persisted bill implied a priced saving, so `viewmodel._bill_savings` set ``available=True`` whenever
a bill existed and every renderer branched on ``available``. After the change that branch asserted,
in rendered customer-facing prose:

* "The customer's own saving is on the record" over a record holding **no** saving (false);
* "no customer bill is persisted on this record" over a record that persists **one** (false in the
  opposite direction, and it hides the one figure that IS evidenced);
* ``unavailable ()`` with an empty parenthetical, because ``pending_reason`` was empty whenever a
  bill existed, so a consumer reaching its unavailable branch by a different route printed nothing;
* the customer proposal's "not yet computed" seam over a bill that HAD been computed, discarding
  the site's real delivered cost today.

No number was fabricated: the figures correctly read "unavailable". This is prose asserting the
opposite of the record, which CLAUDE.md §4 forbids just as firmly ("LLM prose never creates or
changes a financial number"): a reader told a saving is evidenced is misled by the sentence exactly
as they would be by the figure.

**Every existing bill test renders a SOLVED record or a record with no bill at all**, which is
precisely why all of them stayed green. These render the third state.

**Production call site** (`docs/validation_requirements.md` §3, clause 1): the state discriminator
is set at `outputs/viewmodel.py::_bill_savings` (``after_side_priced=priced``), reached from
`build_view_model` → `outputs/generate.py::generate_artifacts` (line 105), which is what
`generate_for_run` (`generate.py:146`) returns through for `gridq run` (`cli.py:301`), what
`gridq report` calls directly (`cli.py:362`), and what the app (`app/app.py:1145`) and
`quotes/persistence.py::publish_run` (line 315) call. Clause 2 (the test that fails if the call
site is deleted): every test below renders through `generate_artifacts` and asserts the *rendered
text*, so deleting the discriminator, or reverting any renderer to branch on ``available``, fails
them. Asserting on the view object alone would prove only that the flag was built, which is the
mistake the eight prior instances made.
"""

from __future__ import annotations

import re
from html import escape, unescape

import pytest

from gridq.outputs import formatting as fmt
from gridq.outputs.generate import generate_artifacts
from gridq.outputs.report_charts import bill_columns
from gridq.outputs.viewmodel import build_view_model
from gridq.schemas import CustomerBill, SelectionStatus

#: The sentence the defect printed over a record that holds no saving.
FALSE_SAVING_CLAIM = "The customer's own saving is on the record"
#: The sentence the defect printed over a record that DOES persist a bill.
FALSE_ABSENCE_CLAIM = "no customer bill is persisted on this record"
#: `formatting.unavailable("")` — a reason-free absence, which the whole label exists to prevent.
EMPTY_PARENTHETICAL = "unavailable ()"

#: Mirrors the production label (`optimisation/engine.py::_before_only_customer_bill`) closely
#: enough that a renderer treating it as a solved bill's basis would be visible here.
_BEFORE_ONLY_BASIS = (
    "BEFORE ONLY: NO SYSTEM IS RECOMMENDED ON THIS RUN, so there is no dispatch and no "
    "after-battery bill: every AFTER figure and the annual saving are ABSENT, never GBP 0. "
    "The BEFORE side is a real measurement and is dispatch-invariant."
)

_BEFORE_ONLY_BILL = CustomerBill(
    annual_bill_before_gbp=100_000.0,
    annual_bill_after_gbp=None,
    annual_saving_gbp=None,
    energy_before_gbp=60_000.0,
    energy_after_gbp=None,
    duos_before_gbp=25_000.0,
    duos_after_gbp=None,
    levies_before_gbp=12_000.0,
    levies_after_gbp=None,
    standing_gbp=1_500.0,
    capacity_gbp=1_500.0,
    import_before_kwh=500_000.0,
    import_after_kwh=None,
    levy_rate_gbp_per_kwh=0.03,
    peak_import_before_kw=320.0,
    basis=_BEFORE_ONLY_BASIS,
)

_PRICED_BILL = CustomerBill(
    annual_bill_before_gbp=100_000.0,
    annual_bill_after_gbp=88_500.0,
    annual_saving_gbp=11_500.0,
    energy_before_gbp=60_000.0,
    energy_after_gbp=51_500.0,
    duos_before_gbp=25_000.0,
    duos_after_gbp=21_000.0,
    levies_before_gbp=12_000.0,
    levies_after_gbp=13_000.0,
    standing_gbp=1_500.0,
    capacity_gbp=1_500.0,
    import_before_kwh=500_000.0,
    import_after_kwh=515_000.0,
    levy_rate_gbp_per_kwh=0.03,
    basis="Priced both sides (test label).",
)

_TEXT_NODE = re.compile(r"<text[^>]*>(.*?)</text>", re.DOTALL)


def _flat_svg_text(svg: str) -> str:
    """SVG text nodes joined back into flowing prose (a caption is one node per wrapped line)."""
    return " ".join(unescape(m.group(1)) for m in _TEXT_NODE.finditer(svg))


def _artifacts(rich_quote, bill: CustomerBill | None):
    quote = rich_quote.quote.model_copy(update={"customer_bill": bill})
    return quote, generate_artifacts(quote, rich_quote.site)


@pytest.fixture
def before_only(rich_quote):
    """The three artifacts rendered from a record whose bill has a BEFORE side only."""
    quote, arts = _artifacts(rich_quote, _BEFORE_ONLY_BILL)
    return quote, arts


# --- the view model's discriminator ----------------------------------------------------------


def test_the_view_mirrors_the_schema_discriminator_in_all_three_states(rich_quote) -> None:
    """`available` answers "is there a bill"; `after_side_priced` answers "is there a saving"."""
    before = build_view_model(
        rich_quote.quote.model_copy(update={"customer_bill": _BEFORE_ONLY_BILL})
    ).bill_savings
    assert before.available is True, "a persisted bill is a persisted bill"
    assert before.after_side_priced is False, "the view claims a priced AFTER side that is absent"
    assert before.annual_savings_gbp is None
    assert before.pending_reason != "", (
        "an absent AFTER side with no stated reason renders as 'unavailable ()' wherever a "
        "consumer reaches its unavailable branch"
    )

    priced = build_view_model(
        rich_quote.quote.model_copy(update={"customer_bill": _PRICED_BILL})
    ).bill_savings
    assert priced.available is True and priced.after_side_priced is True
    assert priced.pending_reason == ""

    none_at_all = build_view_model(rich_quote.quote).bill_savings
    assert none_at_all.available is False and none_at_all.after_side_priced is False
    assert none_at_all.pending_reason != ""


# --- THE acceptance test: the false sentences are absent, the true ones present ---------------


def test_the_consulting_report_never_claims_a_saving_it_does_not_hold(before_only) -> None:
    """Defect 1 and 3: the false finding, and the reason-free `unavailable ()` beside it."""
    _quote, arts = before_only
    html = arts.consulting_report_html

    assert escape(FALSE_SAVING_CLAIM) not in html and FALSE_SAVING_CLAIM not in html, (
        "the report tells the reader a saving is on the record; the record holds none"
    )
    assert EMPTY_PARENTHETICAL not in html, (
        "an absence rendered with an empty reason: the label exists to carry the reason"
    )
    # Present, not merely not-false: the truthful finding and the evidenced BEFORE figure.
    assert escape("there is no after-battery bill and no saving to set against it") in html
    assert escape(fmt.gbp(_BEFORE_ONLY_BILL.annual_bill_before_gbp)) in html, (
        "the one evidenced figure on the record did not reach the report"
    )
    assert "unavailable (" in html, "the absent AFTER rows must still render their labelled seam"
    # The lean table's state cell: "modelled" against a row named before/after would claim both
    # sides were modelled. Case-sensitive on purpose, so the record's own uppercase BEFORE ONLY
    # basis label cannot satisfy this assertion in the renderer's place.
    assert "before side only" in html, (
        "the assumption-state table still reports the before/after bill as modelled"
    )


def test_the_bill_exhibit_does_not_deny_the_bill_it_was_handed(before_only, rich_quote) -> None:
    """Defect 2: false in the other direction. A bill IS persisted on this record."""
    quote, arts = before_only
    view = build_view_model(quote, rich_quote.site).bill_savings
    flat = _flat_svg_text(bill_columns(bill=view))

    assert FALSE_ABSENCE_CLAIM not in flat, (
        "the exhibit denies a bill the record persists, which also tells the reader the BEFORE "
        "figure does not exist"
    )
    assert EMPTY_PARENTHETICAL not in flat
    assert "unavailable (" in flat, "the exhibit must still degrade to a framed, reasoned absence"
    assert "no after-battery bill" in flat, "the exhibit does not state WHY it cannot be drawn"
    # And the same exhibit, embedded in the rendered pack, carries the same statement.
    assert FALSE_ABSENCE_CLAIM not in arts.consulting_report_html


def test_the_caveat_ladder_fires_on_a_bill_with_no_saving(before_only) -> None:
    """Defect 4, decided and pinned: the ladder fires on BOTH absent states, not only "no bill".

    A reader shown a bill with no saving needs the caveat at least as much as one shown no bill,
    and `pending_reason` names which of the two applies. Before this decision the ladder was
    silent on a before-only record, which was the one state a reader could most easily misread.
    """
    _quote, arts = before_only
    html = arts.consulting_report_html
    assert escape("No system is recommended on this run") in html, (
        "the bill caveat did not fire on a record whose bill carries no saving"
    )


def test_the_customer_proposal_shows_the_bill_it_has_and_denies_the_saving_it_has_not(
    before_only,
) -> None:
    """The proposal used to print "not yet computed" over a computed bill, and drop the figure."""
    _quote, arts = before_only
    proposal = arts.proposal_markdown
    section = proposal.split("## Your bill")[1].split("\n## ")[0]

    assert "not yet computed" not in section, (
        "the bill WAS computed; only the after side does not exist"
    )
    assert EMPTY_PARENTHETICAL not in proposal
    assert fmt.gbp(_BEFORE_ONLY_BILL.annual_bill_before_gbp) in section, (
        "the customer's own evidenced cost today was discarded"
    )
    assert "no saving to show you" in section
    # No saving row and no saving figure may appear anywhere in the customer-facing document.
    assert "Estimated annual saving" not in proposal
    assert "**What we need from you:** \n" not in proposal, "an empty data ask rendered"


def test_the_html_report_bill_block_states_its_reason(before_only) -> None:
    """The HTML report's block used to render an empty `<p class="note"></p>` for the reason."""
    _quote, arts = before_only
    html = arts.report_html

    assert '<p class="note"></p>' not in html, "an absence rendered with an empty reason"
    assert EMPTY_PARENTHETICAL not in html
    assert escape(fmt.gbp(_BEFORE_ONLY_BILL.annual_bill_before_gbp)) in html
    assert escape("no after-battery bill and no saving exist to set against it") in html


def test_no_artifact_renders_a_reason_free_absence(before_only) -> None:
    """One sweep across the whole pack: no `unavailable ()` anywhere, in any document."""
    _quote, arts = before_only
    for name, doc in (
        ("memo", arts.memo_markdown),
        ("proposal", arts.proposal_markdown),
        ("report", arts.report_html),
        ("consulting report", arts.consulting_report_html),
    ):
        assert EMPTY_PARENTHETICAL not in doc, f"{name} renders an absence with no stated reason"


def test_the_whole_pack_renders_on_the_record_shape_production_actually_produces(
    rich_quote,
) -> None:
    """The two branches must compose: a BEFORE-only bill only ever occurs with no selected spec.

    The other tests attach a BEFORE-only bill to a solved record to isolate the bill prose. This
    one builds the pairing production emits (`selection_status == "no_viable_configuration"`, no
    spec, no financial case, a BEFORE-only bill) and sweeps the whole pack, so a renderer cannot
    be honest about the bill while another one presupposes the system.
    """
    result = rich_quote.quote.optimisation_result.model_copy(
        update={
            "selection_status": SelectionStatus.NO_VIABLE_CONFIGURATION,
            "selected_power_kw": None,
            "selected_energy_kwh": None,
            "duration_hours": None,
            # With no schedule every stream prices to zero (`optimisation/valuation.py`), so the
            # floor totals nothing. That is the flag the prose drafter's intro branches on.
            "value_streams": [
                v.model_copy(update={"annual_revenue_gbp": 0.0})
                for v in rich_quote.quote.optimisation_result.value_streams
            ],
        }
    )
    quote = rich_quote.quote.model_copy(
        update={
            "optimisation_result": result,
            "financial_case": None,
            "customer_bill": _BEFORE_ONLY_BILL,
        }
    )
    arts = generate_artifacts(quote, rich_quote.site)
    for name, doc in (
        ("memo", arts.memo_markdown),
        ("proposal", arts.proposal_markdown),
        ("report", arts.report_html),
        ("consulting report", arts.consulting_report_html),
    ):
        for claim in (FALSE_SAVING_CLAIM, escape(FALSE_SAVING_CLAIM), FALSE_ABSENCE_CLAIM):
            assert claim not in doc, f"{name} asserts {claim!r} on a no-viable-configuration run"
        assert EMPTY_PARENTHETICAL not in doc
    # The evidenced figure survives into the customer's own document, where it matters most.
    assert fmt.gbp(_BEFORE_ONLY_BILL.annual_bill_before_gbp) in arts.proposal_markdown
    # A fifth instance of the same defect, found by sweeping for it. The prose drafter's
    # empty-floor clause fires on exactly the runs that carry a BEFORE-only bill, so the intro
    # denied the bill that the same page prints a few sections later.
    for artifact in (arts.proposal_markdown, arts.report_html):
        assert "before/after bill" not in artifact.replace("\n", " "), (
            "the intro tells the reader the before/after bill is not computed, over a document "
            "that then prints it"
        )
    assert "no revenue is counted in your case" in arts.proposal_markdown, (
        "the empty-floor clause must still fire; without it this assertion is vacuous"
    )


# --- the two states that already worked must keep working ------------------------------------


def test_a_priced_bill_still_states_the_saving_is_on_the_record(rich_quote) -> None:
    """The fix must not silence a claim that IS true: a solved record still says so."""
    _quote, arts = _artifacts(rich_quote, _PRICED_BILL)
    html = arts.consulting_report_html
    assert escape(FALSE_SAVING_CLAIM) in html, "the true finding was suppressed with the false one"
    assert fmt.gbp(11_500.0) in arts.proposal_markdown
    assert "Estimated annual saving" in arts.proposal_markdown


def test_a_record_with_no_bill_at_all_keeps_its_original_pending_seam(rich_quote) -> None:
    """The third state is unchanged: no bill means no bill, and the report still says so."""
    _quote, arts = _artifacts(rich_quote, None)
    assert "No customer bill is on this record" in unescape(arts.consulting_report_html)
    assert "not yet computed" in arts.proposal_markdown
    assert "What we need from you" in arts.proposal_markdown
    assert EMPTY_PARENTHETICAL not in arts.consulting_report_html
