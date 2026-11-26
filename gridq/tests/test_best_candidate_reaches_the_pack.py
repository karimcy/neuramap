"""The best evaluated candidate's MEASURED figures must reach the rendered bytes of every pack.

**The defect.** A run that selects no system reports no candidate, so every value stream on its
top-level `OptimisationResult` reads ``0.00`` and every row of the pack's stream register prints
"Not measured". A client reads that column as "there is no value at this site". On the 2026-08-15
campaign that reading was false: the same records' persisted sizing grids held a best candidate
measuring real money (Bishops Cleeve Ash Plant: 893.85 tariff arbitrage + 569.30 DUoS red-band
avoidance = 1,463.15/yr at 65.1 kW x 8 h) from a dispatch whose own stream note recorded
30,133.0 kWh/yr DISCHARGED. The customer proposal went further and asserted, in the document the
client actually reads, "no value has been calculated for any of these streams". The record
contradicts it.

**Where the figures were before this.** Outcome 2 put them on the grid and stated them in the
record's own CANDIDATE_REVENUE warning. Measured on the real Bishops Cleeve run, that warning
landed 42% of the way down a 110 KB consulting report, inside the Complication block, and nowhere
at all in the customer proposal. The stream register a reader actually looks at still said 0.00 with
no cross-reference. So the figures were on the record and effectively not in the pack.

**What is asserted here.** Nothing below asserts on a record, a view model, or a hand-built view.
Every assertion reads **bytes off disk** from a pack published end to end by
`gridq.quotes.persistence.publish_run`, which persists the record, reloads it, and renders from the
reload. The record is built in this file and assembled through the production
`assemble_quote_record`, because ``runs/`` is gitignored and a test that reads a published run
directory passes vacuously on a fresh checkout, which is the built-tested-never-called failure in
test form.

**Production call sites** (`docs/validation_requirements.md` §3):

- Clause 1, one seam and four renderers. `outputs/viewmodel.py::build_view_model` ->
  ``best_candidate=_best_candidate(quote)``, reading ``QuoteRecord.sizing_grid``; then
  `outputs/consulting_report.py::_best_candidate_exhibit` (+ the ``stack_lead`` in `_resolution`),
  `outputs/html_report.py::_best_candidate` (+ `_split`'s lead),
  `outputs/underwriting.py::_best_candidate` (+ `_split`'s lead), and
  `outputs/proposal.py::_value_today` / `::_your_system`. This is the same path `cli.py` and
  `app/app.py` write every ``runs/`` directory through, via
  `quotes/persistence.py::publish_run` -> `outputs/generate.py::generate_artifacts`.
- Clause 2, every assertion reads a file `publish_run` wrote. Deleting any one render site, or the
  view-model wiring beneath them all, fails the test that names it. The mutations were run and their
  RED output recorded.

**Two pickers, one candidate.** `viewmodel._best_candidate` and
`quotes/disclosures.py::candidate_revenue_disclosure` independently choose "the best candidate" off
the same grid. `test_the_pack_and_the_record_name_the_same_candidate` pins them equal on the
published bytes: two pickers that could disagree would put two different sizes on one page, which is
the record-conflict defect this pack has already been caught publishing once.

**The null financial case.** A no-spec run persists ``financial_case = None``, verified on the real
Bishops Cleeve run. An UNLEVERED selection does NOT: a real ``gridq run`` of the demo site with
``counterparty.credit_status = "unknown"`` selected 166.25 kW on ``selection_basis = "unlevered"``
and persisted a case with ``irr = 3.9146``, ``equity_irr = null`` and no DSCR. So the two absences
are different facts, and the pack may not render the first as an economic verdict.
`test_a_null_financial_case_is_never_presented_as_an_economic_verdict` pins that, and
`test_a_selected_system_with_no_debt_still_produces_a_financial_case` pins the mechanism the
observation rests on.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import Any

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.outputs.viewmodel import (
    BEST_CANDIDATE_HEADING,
    BEST_CANDIDATE_HELD_OUT,
    BEST_CANDIDATE_IN_FLOOR,
    NO_FINANCIAL_CASE_REASON,
)
from gridq.quotes import assemble_quote_record
from gridq.quotes.disclosures import candidate_revenue_disclosure
from gridq.quotes.persistence import publish_run

# --- the fixture's figures, chosen so no other number in a pack can collide with them ----------
#
# The floor total is deliberately the EXACT sum of the three in-floor stream rows, as the pricing
# path guarantees, so a renderer that summed the register instead of reading the recorded total
# would still be caught: the held-out Capacity Market row carries a NON-ZERO figure, so any total
# that included it would differ.

_BEST_POWER_KW = 137.5
_BEST_DURATION_H = 4.0
_BEST_ENERGY_KWH = 550.0
_BEST_FLOOR = 24_681.57
_BEST_OPEX = 31_111.42
_BEST_DISPATCH_COST = 2_222.33
_BEST_COST_TOTAL = 33_333.75
_BEST_NET = -8_652.18
#: The rendered form (`formatting.gbp`): the sign leads. The record's own CANDIDATE_REVENUE warning
#: spells the SAME number "£-8,652.18" (`disclosures._cost_clause` formats inside the symbol), so
#: the two live side by side on a published page. Both are asserted, each where it belongs, rather
#: than one being assumed to cover the other.
_NET_RENDERED = "-£8,652.18"
_NET_IN_RECORD = "£-8,652.18"

_ARB = 19_432.11
_DUOS = 5_249.46
#: Held OUT of the floor and non-zero: the trap. Adding it to the floor is a false claim in the
#: opposite direction to the one this work removes.
_HELD_OUT_CM = 7_777.01

#: A smaller confirmed candidate, so "best" is a rank in a population and not the only point.
_RUNNER_UP_FLOOR = 11_111.11
#: A proxy (typical-period) candidate whose scaled score reads HIGH. Quoting it would overstate the
#: site in the one sentence written to stop it being understated (D32).
_PROXY_FLOOR = 99_999.99

#: The four documents `publish_run` writes into every run directory.
_DOCUMENTS = ("memo.md", "proposal.md", "report.html", "consulting_report.html")
#: The three that carry a stream register, and so a zero column to explain.
_REGISTER_DOCUMENTS = ("memo.md", "report.html", "consulting_report.html")

_EM_DASH = chr(0x2014)
_EN_DASH = chr(0x2013)

#: `optimisation/valuation.py::VALUE_UNAVAILABLE_MARKER`, imported from the producer rather than
#: copied: the fixture's realism (the register cells reading "Not measured") depends on the exact
#: string, so a rename must move the fixture with it rather than silently degrade it.
from gridq.optimisation.valuation import (  # noqa: E402  (deliberate: documented above)
    VALUE_UNAVAILABLE_MARKER,
)

_UNMEASURED_NOTE = (
    f"no candidate's figures are reported on this record - {VALUE_UNAVAILABLE_MARKER}."
)


def _stream(
    stream: s.MarketStream, gbp: float, firmness: s.Firmness, gate: str, in_floor: bool
) -> s.SizingGridStreamRevenue:
    return s.SizingGridStreamRevenue(
        stream=stream.value,
        annual_revenue_gbp=gbp,
        firmness=firmness.value,
        gate_status=gate,
        in_priced_floor=in_floor,
    )


def _best_streams() -> list[s.SizingGridStreamRevenue]:
    return [
        # In the floor and earning.
        _stream(s.MarketStream.TARIFF_ARBITRAGE, _ARB, s.Firmness.FIRM, "open", True),
        _stream(s.MarketStream.DUOS_RED_BAND_AVOIDANCE, _DUOS, s.Firmness.FIRM, "open", True),
        # In the floor and zero: priced, then declined. A measurement.
        _stream(s.MarketStream.PPA_OR_BILL_SAVINGS, 0.0, s.Firmness.FIRM, "open", True),
        # Held OUT and non-zero: must never be added to the floor.
        _stream(
            s.MarketStream.CAPACITY_MARKET,
            _HELD_OUT_CM,
            s.Firmness.CONDITIONAL,
            "conditional",
            False,
        ),
        # Held OUT and zero: a different fact from the priced zero above.
        _stream(s.MarketStream.LOAD_SERVICE, 0.0, s.Firmness.EXCLUDED, "closed", False),
    ]


def _point(
    power: float,
    duration: float,
    energy: float,
    *,
    floor: float | None,
    opex: float | None = _BEST_OPEX,
    dispatch_cost: float | None = _BEST_DISPATCH_COST,
    proxy: bool = False,
    priceable: bool = True,
    streams: list[s.SizingGridStreamRevenue] | None = None,
) -> s.SizingGridPoint:
    return s.SizingGridPoint(
        power_kw=power,
        duration_hours=duration,
        energy_kwh=energy,
        levered_return_pct=None,
        project_irr_pct=None,
        min_p90_dscr=None,
        passes_covenant=None,
        priceable=priceable,
        bankable=False,
        solver_status=s.SolverStatus.OPTIMAL,
        stage="confirm" if not proxy else "coarse",
        proxy=proxy,
        annual_revenue_gbp=floor,
        annual_operating_cost_gbp=opex,
        annual_dispatch_cost_gbp=dispatch_cost,
        revenue_streams=streams or [],
    )


#: The COVERS shape: a measured floor ABOVE the recorded cost stack, on a run that still selected
#: nothing. The demo site is exactly this (Outcome 2 measured £61,889.20 against £41,637.93), so it
#: is a live branch and not a hypothetical, and it is the one place this change can produce an
#: upward-reading sentence.
_COVERS_FLOOR = 44_444.44
_COVERS_NET = 11_110.69


def _grid(*, best_opex: float | None = _BEST_OPEX) -> s.SizingGrid:
    """The campaign shape: candidates priced, none selected, no return computed for any of them."""
    points = [
        _point(100.0, 2.0, 200.0, floor=_RUNNER_UP_FLOOR),
        _point(
            _BEST_POWER_KW,
            _BEST_DURATION_H,
            _BEST_ENERGY_KWH,
            floor=_BEST_FLOOR,
            opex=best_opex,
            streams=_best_streams(),
        ),
        # A proxy point that reads HIGHER than every confirmed one.
        _point(900.0, 8.0, 7200.0, floor=_PROXY_FLOOR, proxy=True),
        # An unpriceable candidate: no cashflow model, so every figure is absent, never 0.0.
        _point(50.0, 1.0, 50.0, floor=None, opex=None, dispatch_cost=None, priceable=False),
    ]
    return s.SizingGrid(
        search_space_size=len(points),
        covenant=1.3,
        bankable=False,
        selection_reason=(
            "NO SELECTION. The covenant gate did not run on any candidate and no candidate carries "
            "a strictly positive return, so none is selectable on any basis."
        ),
        points=points,
        selected=None,
        selection_basis="none",
        search_version="m4-two-stage-v2",
    )


def _no_spec_result() -> s.OptimisationResult:
    """A run that selected nothing: every top-level stream reads 0.00 because none is reported."""
    return s.OptimisationResult(
        result_id="res_no_spec",
        request_id="req_no_spec",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=None,
        selected_energy_kwh=None,
        duration_hours=None,
        selection_status=s.SelectionStatus.NO_VIABLE_CONFIGURATION,
        value_streams=[
            s.ValueStreamResult(
                stream=stream,
                annual_revenue_gbp=0.0,
                firmness=firmness,
                gate_status=gate,
                warnings=[_UNMEASURED_NOTE],
            )
            for stream, firmness, gate in (
                (s.MarketStream.TARIFF_ARBITRAGE, s.Firmness.FIRM, "open"),
                (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, s.Firmness.FIRM, "open"),
                (s.MarketStream.PPA_OR_BILL_SAVINGS, s.Firmness.FIRM, "open"),
                (s.MarketStream.CAPACITY_MARKET, s.Firmness.CONDITIONAL, "conditional"),
                (s.MarketStream.LOAD_SERVICE, s.Firmness.EXCLUDED, "closed"),
            )
        ],
        gated_upside=[],
        solver_status=s.SolverStatus.OPTIMAL,
        warnings=["no viable configuration: no candidate in the evaluated space was selectable."],
    )


def _publish(site: s.SiteModel, quote: s.QuoteRecord, root: Path) -> dict[str, str]:
    class _Run:  # minimal duck of QuoteRunResult: publish_run reads quote / site / telemetry
        pass

    run = _Run()
    run.quote = quote  # type: ignore[attr-defined]
    run.site = site  # type: ignore[attr-defined]
    run.telemetry = None  # type: ignore[attr-defined]
    published = publish_run(run, root=root)  # type: ignore[arg-type]
    directory = published.persisted.directory
    documents = {name: (directory / name).read_text(encoding="utf-8") for name in _DOCUMENTS}
    # Fixture sanity: these really are the bytes publish_run wrote, from the reloaded record.
    assert {p.name for p in published.written} == set(_DOCUMENTS)
    assert documents["report.html"].startswith("<!doctype html>")
    return documents


def _no_spec_quote(
    site: s.SiteModel,
    *,
    best_opex: float | None = _BEST_OPEX,
    sizing_grid: s.SizingGrid | None = None,
    with_grid: bool = True,
) -> s.QuoteRecord:
    """Assembled through the PRODUCTION assembler, so the record's own disclosures come with it."""
    grid = sizing_grid if sizing_grid is not None else _grid(best_opex=best_opex)
    return assemble_quote_record(
        site,
        _no_spec_result(),
        None,  # financial_case: the state the campaign runs persist
        load_assumptions("gb_v0_1"),
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        sizing_grid=grid if with_grid else None,
        half_hourly_basis=s.HalfHourlyBasis.METERED,
        confidence_ceiling=s.ConfidenceBand.HIGH,
    )


def _covers_grid() -> s.SizingGrid:
    """The same grid with the best candidate's floor raised ABOVE its recorded cost stack."""
    grid = _grid()
    points = [
        p.model_copy(update={"annual_revenue_gbp": _COVERS_FLOOR})
        if p.annual_revenue_gbp == _BEST_FLOOR
        else p
        for p in grid.points
    ]
    return grid.model_copy(update={"points": points})


@pytest.fixture
def published(make_site: Any, tmp_path: Path) -> dict[str, str]:
    site = make_site(with_max_import=True)
    return _publish(site, _no_spec_quote(site), tmp_path)


def _flat(text: str) -> str:
    """Rendered markup as flowing text, so markup between words cannot defeat an assertion."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text)))


# --- 0. the fixture really is the condition under test ------------------------------------------


def test_the_fixture_is_the_no_spec_condition_with_measured_candidates(make_site: Any) -> None:
    """Preconditions asserted, not assumed: a wrong fixture makes every mutation below vacuous.

    Outcome 1 was refuted on exactly this, a mutation reported RED that was GREEN because the
    fixture could not reach the branch. So: no spec, no return figure anywhere, no DSCR anywhere,
    and yet a strictly positive measured floor on a CONFIRMED candidate, with a proxy candidate
    reading higher than all of them.
    """
    quote = _no_spec_quote(make_site(with_max_import=True))
    result = quote.optimisation_result
    grid = quote.sizing_grid
    assert result is not None and grid is not None
    assert not result.selected_power_kw, "fixture must select no system"
    assert result.selection_status is s.SelectionStatus.NO_VIABLE_CONFIGURATION
    assert quote.financial_case is None, "fixture must carry the null financial case"
    assert grid.selected is None and grid.selection_basis == "none"
    assert all(p.project_irr_pct is None for p in grid.points)
    assert all(p.min_p90_dscr is None for p in grid.points)
    assert all(v.annual_revenue_gbp == 0.0 for v in result.value_streams), (
        "every top-level stream must read zero: that is the impression under test"
    )
    confirmed = [p for p in grid.points if not p.proxy and p.annual_revenue_gbp is not None]
    assert len(confirmed) == 2
    assert max(p.annual_revenue_gbp or 0.0 for p in confirmed) == _BEST_FLOOR
    proxy = [p for p in grid.points if p.proxy]
    assert proxy and (proxy[0].annual_revenue_gbp or 0.0) > _BEST_FLOOR, (
        "the proxy must read HIGHER than the best confirmed candidate, or the D32 test is vacuous"
    )
    # The recorded floor total really is the sum of the in-floor rows and excludes the held-out one.
    in_floor = [r for r in _best_streams() if r.in_priced_floor]
    assert round(sum(r.annual_revenue_gbp for r in in_floor), 2) == _BEST_FLOOR
    assert _HELD_OUT_CM not in [r.annual_revenue_gbp for r in in_floor]


# --- 1. the measured figures reach the rendered bytes -------------------------------------------


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_the_measured_floor_reaches_every_published_document(published, document: str) -> None:
    """The whole defect in one assertion, on the bytes a reader opens.

    Before this, `proposal.md` contained no candidate figure of any kind, and the other three
    carried it only inside the record's own warning paragraph two thirds of the way down.
    """
    flat = _flat(published[document])
    assert "24,681.57" in flat, (
        f"{document} does not state what the best evaluated candidate measured; the record's grid "
        "holds the figure and the pack keeps it to itself, which is the whole defect"
    )


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_the_figures_are_labelled_as_a_candidate_and_not_a_recommendation(
    published, document: str
) -> None:
    """A reader must not be able to mistake the measurement for a system on offer.

    Asserted per document because the label is the only thing standing between a measured candidate
    size and a quoted specification, and each renderer prints it in its own markup.
    """
    flat = _flat(published[document])
    assert "not a recommendation" in flat, (
        f"{document} states candidate figures without the words 'not a recommendation'"
    )
    assert "best" in flat.lower()
    # And that no system is recommended, in each document's own register: the three lender-facing
    # packs quote the record's phrasing, the customer proposal says it as a customer would read it.
    # Both are checked rather than one standing in for the other, because the plain-language
    # document is the one where the false claim actually reached a client.
    disclaimer = (
        "we are not proposing a system"
        if document == "proposal.md"
        else "no system is recommended on this record"
    )
    assert disclaimer in flat, (
        f"{document} does not say, beside the figures, that no system is recommended"
    )


@pytest.mark.parametrize("document", _REGISTER_DOCUMENTS)
def test_the_full_candidate_block_reaches_the_three_register_documents(
    published, document: str
) -> None:
    """The heading, the caveat and the cost stack, in the documents that carry a stream register.

    The customer proposal deliberately gets one plain-language paragraph instead (see
    `test_the_customer_proposal_states_the_measurement_in_plain_language`); repeating a
    lender-facing table there would be noise, and noise is how a real disclosure gets skipped.
    """
    flat = _flat(published[document])
    assert BEST_CANDIDATE_HEADING in flat
    assert "These are CANDIDATE measurements from the sizing search, not a specification." in flat
    for figure in ("137.5 kW", "£24,681.57", "£31,111.42", "£2,222.33", _NET_RENDERED):
        assert figure in flat, f"{document} drops {figure!r} from the candidate cost stack"
    assert "Confirmed candidates measured" in flat


@pytest.mark.parametrize("document", _REGISTER_DOCUMENTS)
def test_the_per_stream_split_distinguishes_a_floor_zero_from_a_held_out_one(
    published, document: str
) -> None:
    """D45 at the layer a reader reads: two 0.00 rows on one candidate, stated as different facts.

    ``PPA / bill savings`` is IN the floor at 0.00 (priced, then declined, a measurement).
    ``Load Service`` is 0.00 and was never in the floor at all. A bare zero collapses them.
    """
    flat = _flat(published[document])
    assert BEST_CANDIDATE_IN_FLOOR in flat, f"{document} does not mark the in-floor rows"
    assert BEST_CANDIDATE_HELD_OUT in flat, f"{document} does not mark the held-out rows"
    for figure in ("19,432.11", "5,249.46", "7,777.01"):
        assert figure in flat, f"{document} drops the per-stream figure {figure!r}"


@pytest.mark.parametrize("document", _REGISTER_DOCUMENTS)
def test_the_zero_register_says_why_every_row_reads_zero(published, document: str) -> None:
    """The lead sentence above the all-zero register: the point where the misreading happens.

    The exhibit alone is not enough. A reader's eye lands on the revenue column, and every cell of
    it reads 0.00 with "Not measured" beside it, which is true of this record and false as an
    impression about the site.
    """
    flat = _flat(published[document])
    assert "because this record reports NO CANDIDATE" in flat, (
        f"{document} prints an all-zero revenue register with nothing above it saying why"
    )
    assert "its best confirmed candidate earned" in flat


def test_the_customer_proposal_states_the_measurement_in_plain_language(published) -> None:
    """The false claim, in the document the client reads, replaced by the measurement.

    ``proposal.md`` asserted "no value has been calculated for any of these streams" on records
    whose grid held measured revenue for every priced candidate. That is a factual claim the record
    contradicts, and it is the version of this defect that reaches a customer.
    """
    text = published["proposal.md"]
    assert "no value has been calculated for any of these streams" not in text, (
        "the customer proposal still asserts that no value was calculated, on a record that "
        "measured it"
    )
    assert "value WAS calculated" in text
    assert "£24,681.57" in text and "£33,333.75" in text and _NET_RENDERED in text
    assert "does not cover" in text
    assert "not an offer and it is not a recommendation" in text
    assert "That is why we are not proposing a system." in text, (
        "the measurement must be given as the REASON for the negative verdict, not beside it"
    )
    # And the section a customer reads first points at the figures rather than implying none exist.
    assert "We did measure real value at candidate sizes" in text


# --- 2. the traps -------------------------------------------------------------------------------


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_a_proxy_candidate_is_never_quoted_as_a_measurement(published, document: str) -> None:
    """D32: a typical-period proxy reads HIGH and must never be the figure a pack quotes.

    On the demo site the best proxy floor is 66,651.91 against 61,889.20 confirmed, so quoting the
    proxy overstates the site by 8% in the very sentence written to stop it being understated.
    """
    assert "99,999.99" not in _flat(published[document]), (
        f"{document} quotes a proxy candidate's scaled score as a measured figure"
    )


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_the_held_out_stream_is_never_added_into_the_measured_floor(
    published, document: str
) -> None:
    """The false claim in the opposite direction, which is just as bad.

    The candidate's held-out Capacity Market row carries 7,777.01. The floor total is 24,681.57 and
    a renderer that summed the register would print 32,458.58. That figure must appear nowhere.
    """
    assert "32,458.58" not in _flat(published[document]), (
        f"{document} totals a held-out stream into the priced floor"
    )


def test_the_pack_and_the_record_name_the_same_candidate(published, make_site: Any) -> None:
    """`viewmodel._best_candidate` and `disclosures.candidate_revenue_disclosure`, pinned equal.

    Two independent pickers over the same grid could disagree about which candidate is "best" and
    put two different sizes on one page. Both are on these published bytes: the disclosure inside
    the record's warnings, the view inside the exhibit. Their figures must be the same figures.
    """
    quote = _no_spec_quote(make_site(with_max_import=True))
    disclosure = candidate_revenue_disclosure(quote.optimisation_result, quote.sizing_grid)
    assert disclosure is not None, "fixture sanity: the record's own disclosure did not fire"
    # The record's sentence names the same size and the same figures the exhibit does.
    for fragment in ("137.5 kW", "24,681.57", "33,333.75", _NET_IN_RECORD):
        assert fragment in disclosure.text, (
            f"the record's own disclosure does not carry {fragment!r}, so the exhibit and the "
            "warning on the same page are describing different candidates"
        )
    # And both are on the published page, which is where a disagreement would be visible.
    flat = _flat(published["consulting_report.html"])
    assert "WHAT THE BEST EVALUATED CANDIDATE ACTUALLY EARNED" in flat
    assert BEST_CANDIDATE_HEADING in flat


def test_the_candidate_triple_multiplies_as_displayed(published) -> None:
    """Power times duration must reproduce the energy on a reader's calculator.

    The evaluated-points table shipped nine of 24 rows that failed exactly this, because each figure
    was rounded independently. The consulting report states all three, so it must state them at a
    precision on which they reconcile; the memo and the HTML report state only power and duration,
    which cannot disagree.
    """
    flat = _flat(published["consulting_report.html"])
    block = flat[flat.find("Candidate power") :][:400]
    triple = re.search(
        r"Candidate power ([\d,.]+) kW Candidate energy ([\d,.]+) kWh Candidate duration "
        r"([\d,.]+) h",
        block,
    )
    assert triple is not None, f"the candidate triple is not stated as three rows: {block[:300]}"
    power, energy, duration = (float(g.replace(",", "")) for g in triple.groups())
    assert power * duration == pytest.approx(energy, rel=1e-9), (
        f"the displayed triple does not multiply: {power} kW x {duration} h != {energy} kWh"
    )
    # The memo and the HTML report print two of the three, so they cannot disagree at all. Neither
    # may raise the precision (no arithmetic there), so neither may print the third.
    for document in ("memo.md", "report.html"):
        candidate = _flat(published[document])
        assert "Candidate power" in candidate and "Candidate duration" in candidate
        assert "Candidate energy" not in candidate, (
            f"{document} prints an energy it cannot hold to the multiplication rule"
        )


def test_an_unrecorded_cost_line_is_never_rendered_as_zero(make_site: Any, tmp_path: Path) -> None:
    """D45 one layer up: with a cost line absent, the comparison is withheld, not completed.

    A "does not cover its costs" verdict resting on a cost line that does not exist would be the
    same null-reads-as-zero defect in a new place. The assertion is on the ROW, not on the absence
    of one particular number: a renderer that substituted zero for the missing operating cost would
    print a different, wrong net figure, and a test that only looked for the right net figure being
    absent would pass while the wrong one was published. (Observed: it did. The
    ``absent_cost_reads_zero`` mutation was GREEN against the first version of this test.)
    """
    site = make_site(with_max_import=True)
    documents = _publish(site, _no_spec_quote(site, best_opex=None), tmp_path)
    money = re.compile(r"-?£-?[\d,]+\.\d\d")
    # The rows in the order every renderer emits them, so a cell can be sliced at the next label
    # rather than by a character count that bleeds into the row below.
    labels = (
        "Recorded operating cost",
        "Recorded dispatch cost",
        "Revenue less recorded cost",
        "Confirmed candidates measured",
    )
    for document in _REGISTER_DOCUMENTS:
        flat = _flat(documents[document])
        assert "£24,681.57" in flat, f"{document} drops the measured floor when a cost is absent"
        # The two rows whose value depends on the absent line must carry a REASON, not a figure.
        for label, next_label in zip(labels, labels[1:], strict=False):
            if label == "Recorded dispatch cost":
                continue  # this one IS recorded on the fixture; it must still print its figure
            start = flat.find(label)
            end = flat.find(next_label, start)
            assert start >= 0 and end > start, f"{document} has no {label!r} row"
            cell = flat[start + len(label) : end]
            assert "unavailable" in cell, (
                f"{document}'s {label!r} row does not say the figure is unavailable: {cell[:140]}"
            )
            found = money.search(cell)
            assert found is None, (
                f"{document} prints {found.group(0)!r} on the {label!r} row, so an unrecorded cost "
                "line has been read as a number"
            )
        # And the line that IS recorded still shows: the guard is discriminating, not a blackout.
        assert "£2,222.33" in flat, f"{document} drops the cost line the grid DOES record"
        assert "not a cost of zero" in flat, (
            f"{document} shows an absent cost line without saying it is not a zero"
        )
        assert "no revenue-vs-cost comparison is made" in flat or document != (
            "consulting_report.html"
        ), "the consulting report must withhold the comparison explicitly"


def test_a_run_that_selected_a_system_carries_no_candidate_block(
    rich_quote: Any, tmp_path: Path
) -> None:
    """Scoping. On a run that specified a system the pack states THAT system, and only that.

    A "best candidate" block beside a recommended specification is exactly the confusion the label
    exists to prevent, and a disclosure that fired on healthy runs would be noise.
    """
    quote = rich_quote.quote.model_copy(update={"sizing_grid": _grid()})
    documents = _publish(rich_quote.site, quote, tmp_path)
    for document in _DOCUMENTS:
        flat = _flat(documents[document])
        assert BEST_CANDIDATE_HEADING not in flat, (
            f"{document} prints the candidate block on a run that recommended a system"
        )
        assert "24,681.57" not in flat, f"{document} states a candidate figure beside a real spec"
        assert "because this record reports NO CANDIDATE" not in flat


# --- 3. the null financial case is not an economic verdict --------------------------------------


@pytest.mark.parametrize("document", ("memo.md", "report.html", "consulting_report.html"))
def test_a_null_financial_case_is_never_presented_as_an_economic_verdict(
    published, document: str
) -> None:
    """An absent case must not read as a case that was run and fell short.

    The three claims removed here were all made from ``selection_status`` alone, on records carrying
    no financial case and no computed return for any candidate: "none is economically viable on the
    priced value floor" (`report.html`), "none produces a positive return on the priced value floor"
    (`memo.md`), and a returns panel captioned "the returns below stand alone" over a panel with no
    returns in it (`consulting_report.html`).
    """
    flat = _flat(published[document])
    assert NO_FINANCIAL_CASE_REASON in flat, (
        f"{document} shows an empty financial section without saying the case is absent and that "
        "the absence is not an economic verdict"
    )
    for claim in (
        "none is economically viable on the priced value floor",
        "none produces a positive return on the priced value floor",
        "the returns below stand alone",
        "this case is not yet a deliverable",
    ):
        assert claim not in flat, f"{document} still asserts {claim!r} from an absent case"


def test_the_recorded_outcome_is_stated_and_the_reason_attributed(published) -> None:
    """THAT nothing was selected is a record fact; WHY is the engine's claim, reproduced.

    Both replacement blocks state the outcome and then quote the engine, so the pack stays true
    whichever account of the cause turns out to be right.
    """
    for document, phrase in (
        ("memo.md", "NO SYSTEM WAS SELECTED"),
        ("report.html", "No system was selected."),
    ):
        flat = _flat(published[document])
        assert phrase in flat, f"{document} does not state the recorded outcome"
        assert "no viable configuration: no candidate in the evaluated space was selectable." in (
            flat
        ), f"{document} does not reproduce the engine's own stated reason"


def test_a_selected_system_with_no_debt_still_produces_a_financial_case(make_site: Any) -> None:
    """The mechanism behind the observation in this module's docstring, pinned.

    An UNLEVERED selection (Outcome 1) has ``selected_power_kw > 0``, so the finance model does not
    refuse, so ``financial_case`` is populated with a project IRR and NO DSCR. Only a run that
    selected nothing yields ``financial_case = None``. Without this, a future change that started
    nulling the case for an unlevered selection would silently route every unlevered run into the
    absent-case rendering above, and the pack's account of the two states would be wrong.

    Confirmed on a real production run: ``gridq run`` of the demo site with
    ``counterparty.credit_status = "unknown"`` selected 166.25 kW at
    ``sizing_grid.selection_basis = "unlevered"`` and persisted ``irr = 3.9146``,
    ``equity_irr = null``, ``dscr_summary.min_dscr = null``.
    """
    from gridq.finance import run_financial_model

    site = make_site(with_max_import=True)
    result = _no_spec_result().model_copy(
        update={
            "selected_power_kw": 200.0,
            "selected_energy_kwh": 400.0,
            "duration_hours": 2.0,
            "selection_status": s.SelectionStatus.SELECTED,
            "value_streams": [
                s.ValueStreamResult(
                    stream=s.MarketStream.TARIFF_ARBITRAGE,
                    annual_revenue_gbp=60_000.0,
                    firmness=s.Firmness.FIRM,
                    gate_status="open",
                )
            ],
        }
    )
    # No `debt_terms`: exactly what `optimisation/engine.py` threads on the sizing path.
    model = run_financial_model(site, result, load_assumptions("gb_v0_1"), debt_terms=None)
    assert model.case is not None, (
        "a selected system with no debt structure must still produce a financial case; if this "
        "fails, an unlevered selection now renders through the absent-case path"
    )
    assert model.case.dscr_summary.min_dscr is None, "no debt structure means no DSCR, by design"
    assert model.case.equity_irr is None, "no gearing means no levered return, by design"


# --- 4. house style ------------------------------------------------------------------------------


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_the_new_copy_carries_no_em_or_en_dash(published, document: str) -> None:
    """Zero em and en dashes in the copy this change added, in every document.

    ``report.html`` and ``memo.md`` are not scrubbed at document level (they reproduce record
    warnings byte-for-byte), so renderer-authored copy there has to be dash-free at the source. The
    check is scoped to the candidate block rather than the whole file for exactly that reason: the
    record's own warnings legitimately carry dashes and are reproduced verbatim by contract.
    """
    text = published[document]
    marker = BEST_CANDIDATE_HEADING if BEST_CANDIDATE_HEADING in text else "value WAS calculated"
    start = text.find(marker)
    assert start >= 0, f"{document} carries no candidate copy to check"
    block = text[start : start + 3000]
    assert _EM_DASH not in block, f"{document}'s candidate block carries an em dash"
    assert _EN_DASH not in block, f"{document}'s candidate block carries an en dash"


# --- 5. the two branches the main fixture cannot reach ------------------------------------------


def test_a_measured_surplus_is_never_given_as_the_reason_nothing_was_proposed(
    make_site: Any, tmp_path: Path
) -> None:
    """The one upward-reading sentence this change can produce, held to what the record says.

    Where the measured floor DOES cover the recorded cost stack, "that is why we are not proposing a
    system" is a false causal claim: the record states no cause, and a measured operating surplus is
    not one. This is live rather than hypothetical, because the demo site is exactly this shape (a
    no-spec run whose best confirmed candidate measures well above its recorded cost). The covering
    branch may state the comparison and must not explain the verdict with it.
    """
    site = make_site(with_max_import=True)
    documents = _publish(site, _no_spec_quote(site, sizing_grid=_covers_grid()), tmp_path)
    proposal = documents["proposal.md"]
    assert "£44,444.44" in proposal and "£33,333.75" in proposal, "fixture sanity: figures absent"
    assert "covers the £33,333.75" in proposal, "fixture sanity: this must be the COVERS branch"
    assert "That is why we are not proposing a system" not in proposal, (
        "the proposal gives a measured operating SURPLUS as the reason no system is proposed, "
        "which is a cause the record does not state"
    )
    assert "Covering the running costs is not the same as paying for the equipment" in proposal
    assert "the modelling still selected no system" in proposal
    assert "not an offer and it is not a recommendation" in proposal
    # And the consulting exhibit's own covering caption, which the main fixture never reaches.
    flat = _flat(documents["consulting_report.html"])
    assert "The measured floor COVERS the recorded year-1 cost stack." in flat
    assert "That is an operating margin, not a return" in flat
    assert "the search still selected no system" in flat
    # The negative caption must be nowhere near it.
    assert "does NOT cover the recorded year-1 cost stack" not in flat


def test_an_absent_case_with_no_measured_candidate_points_at_nothing(
    make_site: Any, tmp_path: Path
) -> None:
    """The absent-case note may not cite figures that are not on the page.

    Reached by the committed degraded example record, by any pre-0.2.0 grid re-rendered through
    ``gridq report`` (`cli.py:362`), and by any grid whose candidates are all unpriceable. The note
    used to assert unconditionally that "the measured economics this run does hold are the
    per-candidate figures stated above" over a document with no such figures in it, which is the
    same defect class as the claims this change removed, one branch over.
    """
    site = make_site(with_max_import=True)
    documents = _publish(site, _no_spec_quote(site, with_grid=False), tmp_path)
    flat = _flat(documents["consulting_report.html"])
    assert BEST_CANDIDATE_HEADING not in flat, "fixture sanity: there must be no candidate block"
    assert NO_FINANCIAL_CASE_REASON in flat, "the absent case must still say it is absent"
    assert "Read this panel as silence." in flat
    assert "stated above" not in flat, (
        "the pack cites per-candidate figures 'stated above' on a document that carries none"
    )
    # And the complement, so the guard is discriminating rather than a blanket deletion.
    with_grid = _publish(site, _no_spec_quote(site), tmp_path / "with_grid")
    assert "stated above" in _flat(with_grid["consulting_report.html"])
