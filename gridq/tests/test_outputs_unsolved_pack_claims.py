"""The four artifacts must not assert what an unsolved, study-basis record does not hold.

**The defect class these tests pin.** An adversarial review of the eight client-facing packs
published on 2026-08-15 returned DO NOT SEND. Not one finding was a wrong number: every numeric
token traced to its record and the bill arithmetic was exact to the penny. Every finding was
**prose asserting what the record does not hold** — which CLAUDE.md §4 forbids exactly as firmly as
a fabricated figure, because a reader told that a comparison was made is misled by the sentence
precisely as they would be by the number.

What shipped, and what each test below pins:

1. ``Structure: Fixed GBP/kWh`` printed as a record fact on all eight do-not-proceed documents.
   `viewmodel._price` defaulted `PriceView.structure` to the enum's first member whenever
   ``financial_case is None``, so a commercial term appeared beside an unavailable price.
2. ``The customer's delivered cost today is on the record`` captioning a **modelled** bill. On one
   site that caption sat above GBP 33,837 in a pack whose own Gate-3b line recorded the invoice at
   GBP 185,862; on another it sat above a bill priced on a tariff the record states was taken by
   analogy from a different site's meter, with nothing on the page saying so.
3. ``no configuration is economically viable on the priced value floor``, hardcoded off
   ``selection_status`` alone, in packs whose own ``UNEVIDENCED RETURN COMPARISON`` disclosure
   states that no return figure was persisted for any candidate, and whose sizing grid records a
   second, incompatible reason. The pack asserted a cause its record does not evidence.
4. ``The connection permits 500.0 kW of import on the recorded agreed capacity`` over a site whose
   ``connection.mic_kw`` is a labelled `DerivedAssumption` reading "NO kVA FIGURE EXISTS FOR
   TANHOUSE in the supplied data" — one exhibit below a diagram correctly printing "ASC not
   recorded". `report_charts.capacity_is_supplied` used three heuristics and never read
   ``derived_assumptions``, which is the authoritative signal.
5. The D46 study basis — which prices export INTO the priced floor and is therefore **not an
   underwriting output** — first appearing at roughly line 140 of 1,070, inside a warning block,
   and absent entirely from ``proposal.md``. All eight packs also titled identically but for the
   run id, so a dual-basis pair was indistinguishable by browser tab or saved PDF.
6. No statement of scope, on two packs that cover one of a site's two supplies, or a grid meter
   behind a largely self-supplied site. The site record answers this on ``supply_boundary``; the
   pack never rendered it.
7. ``proposal.md`` — the most customer-facing document in the repo — opening "This proposal sets
   out a battery energy storage system sized for your site" when nothing was sized, printing
   "Contracted value (counted in your case): GBP 0.00/year. This is the firm and contracted
   revenue underwriting relies on" over streams the record marks "value unavailable (not
   invented)", and naming a study-basis run "the underwriting run" three times. ``memo.md`` and
   ``report.html`` printed "Simple payback: unavailable (does not pay back over the horizon)"
   where no case existed to evaluate.

**Why the existing suite stayed green through all seven.** Every output test renders `rich_quote`,
which carries a `FinancialCase`, a selected system and priced streams. Not one rendered the record
shape production actually produced on 2026-08-15: ``financial_case is None``, no system selected,
every stream value an absence, and a study basis. That shape is built here.

**Why the fixture, and not the published runs.** ``runs/`` is gitignored (`.gitignore:46`), so a
test reading it would pass vacuously wherever the packs are absent — the eighth instance of the
built-tested-never-called failure, in test form. `_unsolved` therefore reconstructs the shape, and
`test_the_disclosure_labels_are_still_the_ones_the_pricing_path_writes` pins the fixture's labels
to the pricing path's own constants so it cannot drift away from what production emits.

**Production call site** (`docs/validation_requirements.md` §3, clause 1). Every test renders
through `outputs/generate.py::generate_artifacts` (lines 111-123), which is what
`quotes/persistence.py::publish_run` (line 315) calls to write ``memo.md``, ``proposal.md``,
``report.html`` and ``consulting_report.html`` into every ``runs/`` directory, and what `cli.py`
and `app/app.py` render through. Clause 2 (the test that fails if the call site is deleted): every
assertion below is on **rendered document text**, never on a view-model attribute, so reverting any
one renderer — or the view-model source each was fixed at — fails the test that names it. Asserting
the flag alone would prove only that it was built, which is the mistake all eight prior instances
made.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

import gridq.schemas as s
from gridq.outputs.generate import generate_artifacts
from gridq.outputs.report_charts import capacity_is_derived, capacity_is_supplied

# --- the sentences that shipped. Each must be absent from every artifact. -------------------

#: 1. A commercial term stated as a record fact on a record holding no financial case.
FALSE_STRUCTURE = "Fixed £/kWh"
#: 2. A modelled delivered cost captioned as the customer's own.
FALSE_BILL_CAPTION = "The customer's delivered cost today is on the record"
#: 2. A tariff exhibit implying the rates below it are the site's own.
FALSE_TARIFF_CAPTION = "The delivered cost stack is only as complete as the tariff on file"
#: 3. An economic cause the record does not evidence.
FALSE_VERDICT = "no configuration is economically viable on the priced value floor"
#: 3. The same unevidenced comparison, in the customer's document.
FALSE_PROPOSAL_COMPARISON = "none of them earns enough to justify its cost"
#: 4. A derived capacity asserted as a recorded one.
FALSE_CAPACITY = "of import on the recorded agreed capacity"
#: 7. A system asserted over a run that selected none.
FALSE_SIZED_CLAIM = "This proposal sets out a battery energy storage system sized for your site"
#: 7. A null presented to a customer as a measured zero.
FALSE_MEASURED_ZERO = "This is the firm and contracted revenue underwriting relies on"
#: 7. A horizon verdict over a case that was never evaluated.
FALSE_PAYBACK = "does not pay back over the horizon"
#: 7. The same, one row up.
FALSE_IRR = "no positive return to solve an IRR"

# --- the record shape production produced on 2026-08-15 --------------------------------------

#: Quoted from `optimisation/valuation.py:779`, which writes it on every stream of a run that
#: selected no system. It is the record's own statement that the recorded ``0.0`` is an absence.
_NO_DISPATCH_NOTE = "no dispatch solved - value unavailable (not invented)."

#: Quoted from `quotes/disclosures.py:262`. Truncated after the label and its first clause: the
#: renderers match on the label, and pinning the whole paragraph would fail on an unrelated edit.
_UNEVIDENCED_RETURN_WARNING = (
    "UNEVIDENCED RETURN COMPARISON — no system was specified, and the persisted sizing grid "
    "carries no return figure for any candidate: all 27 full-series candidate(s) record "
    "project_irr_pct = null and levered_return_pct = null."
)
#: Quoted from `quotes/disclosures.py:121`. The second, incompatible account of the same verdict:
#: the engine's warning blames the return, this one blames the P90 DSCR covenant.
_SIZING_VERDICT_WARNING = (
    "SIZING VERDICT — no system was specified, and this is the sizing search's own recorded "
    'reason, verbatim from sizing_grid.selection_reason: "no bankable candidate: no size meets '
    'the P90 DSCR covenant."'
)
#: The engine's own reason, which `viewmodel._spec` lifts into ``selection_verdict_reason``.
_NO_VIABLE_WARNING = (
    "no viable configuration: 27 candidate system(s) were evaluated on the full series (dispatch "
    "solved and priced) and none is economically viable."
)
#: The study-basis banner, built from the pricing path's own constant so it cannot drift.
_STUDY_BASIS_WARNING = f"{s.StudyBasis.LABEL}. This run was priced on the D46 study basis."

#: The site's scope, in the record's own words. Quoted from the published Ewelme metered run: the
#: pack that covers one of the site's two supplies and never said so.
_BOUNDARY_NOTES = (
    "ONE MPAN, ONE SUPPLY. Ewelme carries two live MPANs; this file models the MRF supply and "
    "nothing else. Ewelme Workshop (2000055901639, 750 kVA, 570,641 kWh in 2025, 82% of site "
    "load) has no half-hourly export and is not modelled here."
)
_METERING_POINT = "Ewelme MRF (per-meter tab 'Ewelme MRF', 200 kVA)"

#: The derived connection capacity, quoted from the published Tanhouse run.
_DERIVED_MIC = s.DerivedAssumption(
    kind="derived_input",
    key="connection.mic_kw",
    missing_input=(
        "this site's connection capacity. No kVA figure exists for Tanhouse anywhere in the "
        "supplied data"
    ),
    value=500.0,
    unit="kw",
    basis=(
        "DERIVED FROM THE SITE'S OWN MEASURED PEAK: this site's meter recorded a maximum "
        "half-hourly demand of 486.35 kW over a full metered year, and 500 kW is the smallest "
        "standard GB supply rating above it."
    ),
    confirmation_ask="Confirm the private wire's rating in kVA.",
    derivation_method="fable_advisor",
    derived_by="claude-fable-5 (advisor consultation, offline derivation)",
    derived_on_release="gb_v1_8",
    # Structural feasibility inputs cap the band at LOW (CLAUDE.md §2, Ceilings).
    confidence_ceiling=s.Confidence.LOW,
)

_BEFORE_ONLY_BILL = s.CustomerBill(
    annual_bill_before_gbp=33_837.26,
    annual_bill_after_gbp=None,
    annual_saving_gbp=None,
    energy_before_gbp=20_000.0,
    energy_after_gbp=None,
    duos_before_gbp=9_000.0,
    duos_after_gbp=None,
    levies_before_gbp=4_837.26,
    levies_after_gbp=None,
    import_before_kwh=118_788.2,
    import_after_kwh=None,
    levy_rate_gbp_per_kwh=0.0407,
    peak_import_before_kw=69.28,
    basis=(
        "BEFORE ONLY: no system is recommended on this run, so there is no dispatch and no "
        "after-battery bill: every AFTER figure and the annual saving are ABSENT, never GBP 0."
    ),
)

#: Every stream the production runs carried, each at ``0.0`` with the record's own statement that
#: the value was never computed. This is the exact state that printed "GBP 0.00 / year" to a
#: customer as "the firm and contracted revenue underwriting relies on".
_FLOOR_STREAMS = (
    s.MarketStream.PPA_OR_BILL_SAVINGS,
    s.MarketStream.TARIFF_ARBITRAGE,
    s.MarketStream.MIC_ASC_HEADROOM,
)
_UPSIDE_STREAMS = (s.MarketStream.CAPACITY_MARKET, s.MarketStream.LOCAL_FLEX_PICLO)
_GATED_STREAMS = (s.MarketStream.WHOLESALE_P415, s.MarketStream.BALANCING_MECHANISM)


def _unvalued(stream: s.MarketStream, firmness: s.Firmness) -> s.ValueStreamResult:
    """A stream whose recorded revenue is an absence, exactly as the pricing path records it."""
    return s.ValueStreamResult(
        stream=stream,
        annual_revenue_gbp=0.0,
        firmness=firmness,
        gate_status="open",
        warnings=[_NO_DISPATCH_NOTE],
    )


@pytest.fixture
def unsolved_record(rich_quote):
    """The ``(quote, site)`` pair in the shape the 2026-08-15 packs shipped from.

    Built by overriding `rich_quote` rather than beside it, so the site, counterparty and assumption
    stamps stay the ones the rest of the suite exercises and only the state under test changes.
    """
    result = s.OptimisationResult(
        result_id="res_unsolved",
        request_id="req_unsolved",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        # No system was selected: the whole point of the record shape.
        selected_power_kw=None,
        selected_energy_kwh=None,
        duration_hours=None,
        solver_status=s.SolverStatus.OPTIMAL,
        selection_status=s.SelectionStatus.NO_VIABLE_CONFIGURATION,
        value_streams=[
            *(_unvalued(m, s.Firmness.FIRM) for m in _FLOOR_STREAMS),
            *(_unvalued(m, s.Firmness.CONDITIONAL) for m in _UPSIDE_STREAMS),
        ],
        gated_upside=[_unvalued(m, s.Firmness.CONDITIONAL) for m in _GATED_STREAMS],
        warnings=[_NO_VIABLE_WARNING],
    )
    quote = rich_quote.quote.model_copy(
        update={
            # The state that made `_price` invent a commercial structure.
            "financial_case": None,
            "optimisation_result": result,
            "customer_bill": _BEFORE_ONLY_BILL,
            "warnings": [
                _NO_VIABLE_WARNING,
                _STUDY_BASIS_WARNING,
                _UNEVIDENCED_RETURN_WARNING,
                _SIZING_VERDICT_WARNING,
            ],
        }
    )
    site = rich_quote.site.model_copy(
        update={
            "derived_assumptions": [_DERIVED_MIC],
            "connection": rich_quote.site.connection.model_copy(
                update={"mic_kw": 500.0, "asc_kw": None, "import_headroom_kw": 13.65}
            ),
            "supply_boundary": s.SupplyBoundary(
                import_mpan="2000027378439",
                metering_point=_METERING_POINT,
                notes=_BOUNDARY_NOTES,
            ),
        }
    )
    return quote, site


@pytest.fixture
def unsolved(unsolved_record):
    """The four documents `publish_run` writes, rendered from that record through production."""
    quote, site = unsolved_record
    return generate_artifacts(quote, site)


def _documents(arts) -> dict[str, str]:
    """The four documents `publish_run` writes, by the filename it writes them under."""
    return {
        "memo.md": arts.memo_markdown,
        "proposal.md": arts.proposal_markdown,
        "report.html": arts.report_html,
        "consulting_report.html": arts.consulting_report_html,
    }


def _flat(html: str) -> str:
    """Rendered HTML as flowing text, so an assertion is not defeated by markup between words."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


# --- 1. no financial case, therefore no commercial structure ---------------------------------


def test_no_artifact_states_a_commercial_structure_without_a_financial_case(unsolved) -> None:
    """Source fix at `viewmodel.py::_price`; all five render sites go through `structure_label`."""
    for name, text in _documents(unsolved).items():
        assert FALSE_STRUCTURE not in text, (
            f"{name} states a commercial structure on a record holding no financial case; a "
            "structure is a term of an offer, and this run priced nothing"
        )


def test_the_absent_structure_is_labelled_with_its_reason_not_merely_dropped(unsolved) -> None:
    """An absence carries a reason (`formatting.unavailable`), never a blank or a silence."""
    reason = "no financial case on this record, so no commercial structure has been set"
    assert reason in unsolved.proposal_markdown
    assert reason in unsolved.memo_markdown
    assert reason in _flat(unsolved.report_html)
    # The consulting pack states the same fact as a sentence rather than as a labelled field.
    assert "No commercial structure is set on this record" in _flat(unsolved.consulting_report_html)


# --- 2. a modelled bill is not the customer's own cost ---------------------------------------


def test_the_bill_exhibit_never_calls_a_modelled_figure_the_customers_own_cost(unsolved) -> None:
    """Production call site: `consulting_report.py::_resolution`, the ``bill.available`` branch."""
    flat = _flat(unsolved.consulting_report_html)
    assert FALSE_BILL_CAPTION not in flat, (
        "a modelled delivered cost is captioned as the customer's actual cost; whether the two "
        "agree is exactly what the Gate-3b reconciliation tests"
    )
    assert "modelled from the site's recorded load and the tariff on the record" in flat
    assert "not read off an invoice" in flat
    # The truthful half of the old caption survives: there genuinely is no after side.
    assert "no after-battery bill and no saving to set against it" in flat


def test_the_tariff_exhibit_states_whose_tariff_it_prices(unsolved) -> None:
    """Production call site: `consulting_report.py::_situation` + `_tariff_rows`.

    The exhibit printed nine rates with nothing on the page saying where they came from. One
    published site is priced on a commodity rate the record states was taken by analogy from a
    different site's meter. The record answers the question in its own words, so the exhibit now
    reproduces its answer rather than implying one.
    """
    flat = _flat(unsolved.consulting_report_html)
    assert FALSE_TARIFF_CAPTION not in flat
    assert "Tariff basis" in flat, "the tariff exhibit does not say whose tariff it prices"


# --- 3. the verdict reports the record's accounts and asserts no cause -----------------------


def test_the_verdict_does_not_assert_an_economic_cause_the_record_disclaims(unsolved) -> None:
    """Production call site: `consulting_report.py::_no_viable_qualifier`, via `_recommendation`.

    The record holds three relevant statements and the pack must not collapse them: the engine's
    reason, the sizing search's own (incompatible) reason, and a disclosure that no return figure
    was persisted for any candidate. The pack used to print an economic finding that the third
    statement, in the same document, says is not a comparison the record evidences.
    """
    flat = _flat(unsolved.consulting_report_html)
    assert FALSE_VERDICT not in flat, (
        "the pack asserts an economic cause while its own UNEVIDENCED RETURN disclosure states "
        "no candidate's return was ever computed"
    )
    # The verdict itself is a recorded outcome and survives.
    assert "Do not proceed on these inputs" in flat
    assert "selected no system. That much is on the record." in flat
    assert "Why it did is not settled by this record" in flat


def test_the_customer_document_makes_no_comparison_the_record_disclaims(unsolved) -> None:
    """Production call site: `proposal.py::_your_system`. The same claim, in plainer words."""
    proposal = unsolved.proposal_markdown
    assert FALSE_PROPOSAL_COMPARISON not in proposal, (
        "the customer proposal states a cost comparison for candidates whose returns the record "
        "says were never computed"
    )
    # The recommendation survives: it is true on either account of the verdict.
    assert "we are not proposing a battery at this site today" in proposal
    assert "do not proceed on the current numbers" in proposal


def test_the_floor_total_is_not_presented_as_a_priced_result(unsolved) -> None:
    """Production call site: `consulting_report.py::_floor_note` + the exec-summary floor phrase."""
    flat = _flat(unsolved.consulting_report_html)
    assert "was not enough to make any candidate viable" not in flat
    assert "the total below is a sum of absences and not a priced result" in flat


# --- 4. a derived capacity is neither supplied nor the site's own peak -----------------------


def test_a_derived_connection_capacity_is_never_asserted_as_recorded(unsolved) -> None:
    """Production call site: `consulting_report.py::_capacity_finding`, via
    `report_charts.capacity_is_derived`."""
    flat = _flat(unsolved.consulting_report_html)
    assert FALSE_CAPACITY not in flat, (
        "a labelled DerivedAssumption is reported as a recorded agreed capacity, one exhibit "
        "below a diagram correctly printing that the ASC is not recorded"
    )
    assert "is a derived assumption, not a recorded agreed capacity" in flat


def test_a_derived_capacity_is_not_relabelled_as_the_sites_own_peak(unsolved) -> None:
    """The third state exists for a reason: the other absent-capacity account is also false here.

    Tanhouse's 500 kW is the smallest standard GB rating above its measured 486.35 kW peak, so it
    is not the peak and saying it is would swap one false sentence for another. Routing the
    derived case into the existing ``not supplied`` branch would do exactly that.
    """
    flat = _flat(unsolved.consulting_report_html)
    assert "the recorded MIC is the site's own peak" not in flat
    assert "the MIC on the record was set to the site's own peak" not in flat.lower()


def test_the_derived_signal_beats_every_heuristic_in_the_drawn_exhibits(
    unsolved, rich_quote
) -> None:
    """`capacity_is_supplied` passed Tanhouse on all three heuristics; the record did not.

    Asserted on the **drawn exhibits**, not only on the function: `capacity_is_supplied` decides
    whether the system diagram draws the MIC as a ceiling, whether the firming panel rules a
    capacity line across the solved day, and which account of the absent capacity the load-duration
    note carries. A test that only called the function would prove the guard exists while a
    diagram went on drawing a capacity rule from a derived number.
    """
    flat = _flat(unsolved.consulting_report_html)
    assert "MIC (lower bound)" in flat, (
        "the system diagram draws a derived MIC as a recorded ceiling"
    )
    assert "the MIC on the record is a labelled derived assumption" in flat
    assert "no unused capacity is implied" in flat
    # And the seam itself, so a caller of either helper gets the same answer.
    site = rich_quote.site.model_copy(update={"derived_assumptions": [_DERIVED_MIC]})
    assert capacity_is_derived(site)
    assert not capacity_is_supplied(site)
    # A site with no derived record is untouched: this must not reclassify the whole portfolio.
    assert not capacity_is_derived(rich_quote.site)
    assert capacity_is_supplied(rich_quote.site)


# --- 5. the study basis is unmissable, and packs are told apart ------------------------------


def test_every_artifact_states_the_study_basis(unsolved) -> None:
    """A D46 run prices export INTO the priced floor, which the underwriting basis forbids.

    Production call sites: `viewmodel.build_view_model` (``study_basis``), then
    `consulting_report._head`/`_doc_title`/`_caveats`, `underwriting._banner`,
    `html_report._banner` and `proposal.render_customer_proposal`.
    """
    for name, text in _documents(unsolved).items():
        flat = _flat(text) if name.endswith(".html") else text
        assert "NOT AN UNDERWRITING OUTPUT" in flat.upper(), (
            f"{name} carries no statement that it was produced on the D46 study basis, which "
            "prices export into the priced floor and is not an underwriting output"
        )


def test_the_study_basis_reaches_the_front_page_not_only_the_warnings(unsolved) -> None:
    """It used to appear first at roughly line 140 of 1,070, inside a warning block."""
    html = unsolved.consulting_report_html
    title = re.search(r"<title>(.*?)</title>", html)
    assert title is not None
    assert "STUDY BASIS (D46)" in title.group(1)
    subtitle = re.search(r'<div class="sub">(.*?)</div>', html, re.DOTALL)
    assert subtitle is not None
    assert "NOT AN UNDERWRITING OUTPUT" in _flat(subtitle.group(1))
    # And in the executive summary, above every other caveat on the ladder. Both halves are
    # asserted: the BASIS'S OWN LABEL (so a five-stream D55 run cannot be summarised in D46's
    # export-only words) and the reader-language caveat that follows it.
    summary = re.search(r'<section id="results".*?</section>', html, re.DOTALL)
    assert summary is not None
    flat_summary = _flat(summary.group(0))
    assert "STUDY BASIS (D46)" in flat_summary, (
        "the executive summary does not name WHICH study basis priced the pack, so a dual-basis "
        "pair reads identically at the one place a reader is guaranteed to look"
    )
    assert "NOT AN UNDERWRITING OUTPUT" in flat_summary
    assert "THIS IS A FEASIBILITY STUDY" in flat_summary


def test_the_pack_title_names_the_site_so_two_packs_are_never_interchangeable(unsolved) -> None:
    """All eight packs titled identically but for the run id, so a dual-basis pair could not be
    told apart by browser tab or saved PDF filename."""
    title = re.search(r"<title>(.*?)</title>", unsolved.consulting_report_html)
    assert title is not None
    assert "Synthetic Demo Site" in unescape(title.group(1)), (
        "the pack title does not name its site, so two packs of one site's dual-basis pair are "
        "indistinguishable"
    )


# --- 6. the pack states its own perimeter ----------------------------------------------------


def test_section_one_states_what_the_pack_covers_and_what_it_does_not(unsolved) -> None:
    """Production call site: `consulting_report.py::_scope_block`, called from
    `_executive_summary`.

    Reproduced from ``supply_boundary``, never composed: an authored summary of a scope is a claim,
    and a quoted one is a record. The published Ewelme metered pack covers one of the site's two
    MPANs and its own record said so in a field the pack never rendered.
    """
    summary = re.search(
        r'<section id="results".*?</section>', unsolved.consulting_report_html, re.DOTALL
    )
    assert summary is not None
    flat = _flat(summary.group(0))
    assert "What this pack covers" in flat
    assert "750 kVA" in flat, "the scope statement omits the supply the pack does not model"
    assert "82% of site load" in flat
    assert "is not modelled here" in flat
    assert "2000027378439" in flat, "the scope statement does not name the supply it prices"


# --- 7. the sibling artifacts -----------------------------------------------------------------


def test_the_proposal_does_not_claim_a_system_was_sized(unsolved) -> None:
    """Production call site: `prose.py::DeterministicProseDrafter.proposal_intro`, keyed off the
    record's ``selection_status`` by `proposal.py::_system_selected`."""
    proposal = unsolved.proposal_markdown
    assert FALSE_SIZED_CLAIM not in proposal, (
        "the customer proposal opens by asserting a system sized for the site on a run that "
        "selected none"
    )
    assert "No system is proposed, because the modelling did not select one." in proposal
    # The same opening renders into the HTML report, which inherited the same false claim.
    assert FALSE_SIZED_CLAIM not in _flat(unsolved.report_html)


def test_the_proposal_never_shows_an_uncomputed_value_as_a_measured_zero(unsolved) -> None:
    """Production call site: `proposal.py::_value_today`, via `split.floor_value_measured`.

    The record marks every stream "value unavailable (not invented)". Printing their sum as
    "GBP 0.00 / year" told a customer a measurement had been taken and had come to nothing.
    """
    proposal = unsolved.proposal_markdown
    assert FALSE_MEASURED_ZERO not in proposal
    assert "Contracted value (counted in your case): £0.00" not in proposal, (
        "a null is presented to the customer as a measured zero, which is the single failure "
        "mode CLAUDE.md §2 names as worse than a blocked run"
    )
    assert "unavailable (not calculated on this run)" in proposal
    assert "we will not show you a zero we did not measure" in proposal


def test_no_artifact_states_a_horizon_verdict_on_a_case_it_never_evaluated(unsolved) -> None:
    """Production call site: `viewmodel.ReturnsView.payback_unavailable_reason` /
    `irr_unavailable_reason`, read by `underwriting.py::_price_and_returns` and
    `html_report.py::_price_debt`."""
    for name in ("memo.md", "report.html"):
        text = _documents(unsolved)[name]
        assert FALSE_PAYBACK not in text, (
            f"{name} tells the reader a system does not pay back over the horizon on a record "
            "carrying no financial case, so nothing was ever evaluated"
        )
        assert FALSE_IRR not in text
        assert "no payback was evaluated" in _flat(text)
        assert "no return was evaluated" in _flat(text)


def test_a_study_basis_run_is_never_called_the_underwriting_run(unsolved) -> None:
    """Production call site: `proposal.py::render_customer_proposal` + `_footer`, and
    `prose.py::proposal_intro`'s ``run_name``."""
    assert "the underwriting run" not in unsolved.proposal_markdown, (
        "the customer document calls a feasibility-study run an underwriting run"
    )
    assert "feasibility study run" in unsolved.proposal_markdown


# --- the fixture cannot drift away from what the pricing path writes -------------------------


def test_the_disclosure_labels_are_still_the_ones_the_pricing_path_writes() -> None:
    """The renderers detect these states by label. If a producer renames one, every fix above goes
    silently inert and its test still passes, which is the failure mode this repo has shipped
    eight times. This test fails instead.
    """
    from gridq.outputs import consulting_report as cr
    from gridq.outputs import viewmodel as vmod

    # The study basis: the renderers' prefix must match the schema's own label on EVERY shipped
    # basis, not just the first one written. D55 (`AllStreamsEnabledStudyBasis`) is the reason this
    # loops: the detector used to be the literal "STUDY BASIS (D46)", so the D55 all-streams basis
    # was invisible to all four renderers and a study-basis proposal called itself an underwriting
    # run. A basis-numbered detector is the single worst place to hard-code one, so this asserts the
    # prefix covers every subclass — a seventh basis added without checking fails here.
    for basis in (s.StudyBasis, s.AllStreamsEnabledStudyBasis):
        assert basis.LABEL.startswith(vmod._STUDY_BASIS_PREFIX), (
            f"{basis.__name__}.LABEL is invisible to the renderers' detector "
            f"{vmod._STUDY_BASIS_PREFIX!r}, so every artifact of a run on this basis drops its "
            "basis banner and present study figures as underwriting ones"
        )
        assert basis.LABEL.startswith(cr._STUDY_BASIS)
        # And the note the pricing path actually writes must itself start with the label, because
        # that is what `viewmodel._study_basis_label` slices the label back out of.
        assert vmod._STUDY_BASIS_PREFIX in basis.LABEL
    # The two verdict disclosures, as `quotes/disclosures.py` writes them.
    assert _UNEVIDENCED_RETURN_WARNING.startswith(cr._UNEVIDENCED_RETURN)
    assert _SIZING_VERDICT_WARNING.startswith(cr._SIZING_VERDICT)
    # The per-stream absence marker, as `optimisation/valuation.py` writes it.
    assert vmod._VALUE_UNAVAILABLE in _NO_DISPATCH_NOTE


def test_the_fixture_is_still_the_shape_the_packs_shipped_from(unsolved_record) -> None:
    """Guards the fixture itself. Every assertion above is a *negative* about rendered prose, and
    a negative passes trivially against the wrong record: a solved record simply never reaches the
    branches under test. If the fixture stopped being the unsolved, study-basis, unvalued-stream
    shape, the whole file would go green while asserting nothing.
    """
    from gridq.outputs.viewmodel import build_view_model

    quote, site = unsolved_record
    vm = build_view_model(quote, site)

    # No priced case, so no structure and no evaluated return.
    assert quote.financial_case is None
    assert vm.price.structure is None
    assert not vm.returns.case_available
    # A search that ran and selected nothing (not a solver failure: the dispatch solved).
    assert vm.spec.selection_status == "no_viable_configuration"
    assert vm.spec.solver_status == "optimal"
    assert vm.spec.selected_power_kw is None
    # Every stream value is an absence the record disclaims, not a priced zero.
    assert vm.split.floor, "the floor must be non-empty: an empty one takes a different branch"
    assert vm.split.floor_total_gbp == 0.0
    assert not vm.split.floor_value_measured
    assert not vm.split.upside_value_measured
    # Priced on the study basis, with both verdict disclosures on the record.
    assert vm.study_basis and vm.study_basis_note is not None
    assert vm.spec.selection_verdict_reason is not None
    # A derived connection capacity, and a bill with a BEFORE side only.
    assert capacity_is_derived(site)
    assert site.connection.asc_kw is None
    assert vm.bill_savings.available and not vm.bill_savings.after_side_priced
    # A recorded scope the pack can quote rather than compose.
    assert site.supply_boundary.notes
