"""D59: every artifact runs in the order the owner specified, and the new sections really render.

**The owner's instruction, verbatim** (Olly, 2026-08-18): "For each of the sites from Grundon and
Harwell you are to fully reproduce the reports - starting with the IRR and Revenue breakdown and
graphs, then the methodology and then the assumptions register for each site - no em-dashes
allowed at all in the reports." And earlier, still binding: "ensure that this is clearly shows the
full sizing surface in the report, and ensure that it is very clear all assumptions made and
ensure that somewhere in the report it clearly shows the breakdown of revenues per stream and the
assumptions made for each of the streams should be shown and it should also give graphs that show
exactly how the battery has been dispatched on a given day within the report to show how the
dispatch works and also graphs that clearly highlight how the MILP selection was done."

**Every assertion here is on the RENDERED BYTES of a record built in this file.** That is not a
style preference. ``runs/`` is gitignored, so a test that loads a run directory passes VACUOUSLY
on a fresh checkout, and this repo has shipped the same built-tested-never-called defect twelve
times, each with a passing test. The record below carries a sizing grid with confirmed and proxy
points, a selected point, a 48-interval solved dispatch day, streams that cite assumption keys and
streams that cite none, and a D57 assumed-approval statement, so every branch the four renderers
grew for D59 is exercised by bytes this file produced.

Ordering is asserted with ``index()`` rather than by membership: a renderer that emits every
required section in the wrong order is exactly the defect D59 exists to fix, and a membership
check would pass on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy, run_financial_model
from gridq.outputs import formatting as fmt
from gridq.outputs import generate_artifacts
from gridq.outputs.consulting_report import ConfidenceView, render_report_for_quote
from gridq.outputs.viewmodel import build_view_model
from gridq.quotes import assemble_quote_record

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

#: The release the campaign runs are pinned to. Loaded rather than mocked so the per-stream register
#: resolves real library entries and the test would notice a key that stopped existing.
_RELEASE = "gb_v1_12"

#: A stream that cites keys, and the keys it cites. Real keys in the pinned release, so the
#: per-stream table renders a resolved value and a real source rather than "key not present".
_CITED_KEYS = ("round_trip_efficiency.ac_round_trip",)

#: The D57 statement the engine writes onto a stream whose award is assumed granted. Written here in
#: the engine's own register-facing shape, because both the assumptions page and the per-stream
#: register harvest it by its ``D57`` token and a paraphrase would not exercise that path.
_D57_STATEMENT = (
    "D57 ASSUMED APPROVED: the local flexibility award, its registration and its route to market "
    "are "
    "assumed granted for this site and battery size. Not granted: assumed approved."
)
#: A non-D57 statement on the same stream, so the register has to split one list into two.
_RATE_STATEMENT = (
    "availability rate GBP 174.99/MW/h taken from the awarded clear for this licence area."
)


@dataclass(frozen=True)
class _Fixture:
    quote: s.QuoteRecord
    site: s.SiteModel


def _grid_point(
    power_kw: float,
    duration_hours: float,
    irr_pct: float | None,
    *,
    stage: str,
    proxy: bool,
) -> s.SizingGridPoint:
    """One evaluated candidate. Energy is power times duration, stated at the record's precision."""
    return s.SizingGridPoint(
        power_kw=power_kw,
        duration_hours=duration_hours,
        energy_kwh=power_kw * duration_hours,
        project_irr_pct=irr_pct,
        priceable=True,
        bankable=False,
        solver_status=s.SolverStatus.OPTIMAL,
        stage=stage,
        proxy=proxy,
        annual_revenue_gbp=6000.0,
        annual_operating_cost_gbp=340.0,
        annual_dispatch_cost_gbp=0.0,
    )


#: Two confirmed candidates and two proxy-scored ones, so the surface table has to split the blocks,
#: the ranking has to exclude the proxies, and the selected-row match has to distinguish a confirmed
#: solve from the proxy score at the SAME coordinates. That last case is a real defect this shape
#: caught: a coordinate-only match marked both rows SELECTED.
_SELECTED = _grid_point(10.0, 0.5, 42.0, stage="confirm", proxy=False)
_POINTS = (
    _SELECTED,
    _grid_point(10.0, 0.5, 38.0, stage="coarse", proxy=True),
    _grid_point(20.0, 1.0, 31.0, stage="confirm", proxy=False),
    _grid_point(20.0, 1.0, 29.0, stage="coarse", proxy=True),
)
_SELECTION_REASON = (
    "UNLEVERED SELECTION: 10.0 kW x 0.5 h, chosen from 2 eligible candidate(s) of 2 confirmed, on "
    "an unlevered project IRR of 42.0%, ranked by the same objective the covenant path uses, "
    "smallest system as the tie-break."
)


def _dispatch_day() -> s.DispatchProfile:
    """A 48-interval solved day: charging overnight, discharging through the evening peak."""
    points = []
    for period in range(48):
        charging = 4 <= period <= 7
        discharging = 34 <= period <= 37
        points.append(
            s.DispatchProfilePoint(
                period=period,
                charge_kw=10.0 if charging else 0.0,
                discharge_kw=10.0 if discharging else 0.0,
                soe_kwh=5.0 if 8 <= period <= 33 else 0.5,
                site_load_kw=80.0,
                duos_rate_gbp_per_kwh=0.12 if 32 <= period <= 39 else 0.001,
                red_band=32 <= period <= 39,
                energy_price_gbp_per_kwh=0.09,
            )
        )
    return s.DispatchProfile(
        power_kw=10.0,
        energy_kwh=5.0,
        duration_hours=0.5,
        interval_hours=0.5,
        representative_day_index=121,
        selection_basis="highest-discharge day",
        points=points,
    )


def _build() -> _Fixture:
    """A record with everything D59 renders from, built here so no test reads ``runs/``."""
    site = s.SiteModel(
        site_id="site_d59",
        site_name="D59 Order Fixture (illustrative)",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies={"cclevy": 0.01},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=s.Counterparty(
            legal_name="D59 Fixture Ltd",
            company_number="09999999",
            sector="manufacturing",
            credit_status=s.CreditStatus.ADEQUATE,
            expected_tenure_years=10.0,
        ),
    )
    result = s.OptimisationResult(
        result_id="res_d59",
        request_id="req_d59",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=10.0,
        selected_energy_kwh=5.0,
        duration_hours=0.5,
        solver_status=s.SolverStatus.OPTIMAL,
        value_streams=[
            # Cites a real library key: the per-stream table must resolve it.
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=38000.0,
                firmness=s.Firmness.FIRM,
                gate_status="met",
                assumptions_used=list(_CITED_KEYS),
                warnings=[_RATE_STATEMENT],
            ),
            # Cites NOTHING: the register must state the traceability gap rather than print a blank.
            s.ValueStreamResult(
                stream=s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
                annual_revenue_gbp=12000.0,
                firmness=s.Firmness.FIRM,
                gate_status="met",
            ),
            # Carries a D57 assumed approval AND a rate statement: the register must split them.
            s.ValueStreamResult(
                stream=s.MarketStream.LOCAL_FLEX_PICLO,
                annual_revenue_gbp=5000.0,
                firmness=s.Firmness.CONTRACTED,
                gate_status="open",
                warnings=[_D57_STATEMENT, _RATE_STATEMENT, _D57_STATEMENT],
            ),
        ],
        gated_upside=[
            # Held out, and still owed a per-stream basis: a page reading only `value_streams`
            # drops every gated stream's assumptions.
            s.ValueStreamResult(
                stream=s.MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.CONDITIONAL,
                gate_status="gated",
                unlock_condition="RTM partner + export consents",
            ),
        ],
    )
    assumptions = load_assumptions(_RELEASE)
    case = run_financial_model(
        site,
        result,
        assumptions,
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400_000.0),
    ).case
    quote = assemble_quote_record(
        site,
        result,
        case,
        assumptions,
        quote_mode=s.QuoteMode.UNDERWRITTEN,
        half_hourly_basis=s.HalfHourlyBasis.METERED,
        confidence_ceiling=s.ConfidenceBand.HIGH,
    )
    quote = quote.model_copy(
        update={
            "warnings": [],
            "sizing_grid": s.SizingGrid(
                search_space_size=len(_POINTS),
                covenant=1.3,
                bankable=False,
                selection_reason=_SELECTION_REASON,
                selection_basis="unlevered",
                search_version="m4-two-stage-v2",
                points=list(_POINTS),
                selected=_SELECTED,
            ),
            "dispatch_profile": _dispatch_day(),
        }
    )
    return _Fixture(quote=quote, site=site)


@pytest.fixture
def d59() -> _Fixture:
    return _build()


def _artifacts(fx: _Fixture):
    return generate_artifacts(fx.quote, fx.site, assumptions=load_assumptions(_RELEASE))


def _consulting(fx: _Fixture) -> str:
    return render_report_for_quote(
        fx.quote,
        fx.site,
        assumptions=load_assumptions(_RELEASE),
        confidence=ConfidenceView(band="low", ceiling="medium", binding_factors=("load basis",)),
    )


def _in_order(doc: str, headings: tuple[str, ...], label: str) -> None:
    """Every heading present, and present in this order, in the rendered bytes."""
    for heading in headings:
        assert heading in doc, f"{label}: missing section {heading!r}"
    positions = [doc.index(heading) for heading in headings]
    assert positions == sorted(positions), (
        f"{label}: sections are out of the order the owner specified. Rendered order: "
        f"{[h for _, h in sorted(zip(positions, headings, strict=True))]}"
    )


# --- 1. the order, in all four artifacts -----------------------------------------------------


def test_consulting_pack_runs_in_the_owners_order(d59: _Fixture) -> None:
    """IRR and revenue, then the graphs, then the methodology, then the assumptions register.

    The evidence sections follow. They moved DOWN and not out, which the tail of this list pins:
    Situation, Complication, Evidence and What would change this are all still rendered.
    """
    _in_order(
        _consulting(d59),
        (
            "1 · Results: IRR and revenue",
            "2 · The graphs",
            "3 · Methodology",
            "4 · Assumptions register",
            "5 · Situation",
            "6 · Complication",
            "7 · Evidence and provenance",
            "8 · What would change this",
        ),
        "consulting pack",
    )


def test_memo_runs_in_the_owners_order(d59: _Fixture) -> None:
    _in_order(
        _artifacts(d59).memo_markdown,
        (
            "## Project IRR, returns and price to target",
            "## Contracted vs merchant",
            f"## {fmt.STREAM_ASSUMPTIONS_TITLE}",
            f"## {fmt.SIZING_SURFACE_TITLE}",
            f"## {fmt.METHODOLOGY_TITLE}",
            f"# {fmt.ASSUMPTIONS_PAGE_TITLE}",
            "## Provenance",
            "## Warnings",
        ),
        "underwriting memo",
    )


def test_html_report_runs_in_the_owners_order(d59: _Fixture) -> None:
    _in_order(
        _artifacts(d59).report_html,
        (
            "<h2>Project IRR and returns</h2>",
            "<h2>Contracted vs merchant</h2>",
            f"<h2>{escape(fmt.STREAM_ASSUMPTIONS_TITLE)}</h2>",
            "<h2>The graphs: revenue, dispatch, the sizing surface and the selection</h2>",
            f"<h2>{escape(fmt.METHODOLOGY_TITLE)}</h2>",
            f"<h2>{escape(fmt.ASSUMPTIONS_PAGE_TITLE)}</h2>",
            "<h2>Provenance</h2>",
        ),
        "html report",
    )


def test_proposal_runs_in_the_owners_order(d59: _Fixture) -> None:
    _in_order(
        _artifacts(d59).proposal_markdown,
        (
            "## The project's return",
            "## What's secured vs what's upside",
            f"## {fmt.STREAM_ASSUMPTIONS_TITLE}",
            f"## {fmt.SIZING_SURFACE_TITLE}",
            f"## {fmt.METHODOLOGY_TITLE}",
            f"# {fmt.ASSUMPTIONS_PAGE_TITLE}",
            "## Next steps",
        ),
        "customer proposal",
    )


def test_the_irr_leads_the_first_section_of_every_artifact(d59: _Fixture) -> None:
    """ "The project IRR first": ahead of the priced-floor total in the same panel.

    Pinned per artifact because each one had its own opening panel and three of the four led with
    something else: the priced floor, the price to target, or a prose summary.
    """
    arts = _artifacts(d59)
    html = _consulting(d59)
    assert html.index("Project IRR (unlevered)") < html.index("Priced floor (firm + contracted)")
    memo = arts.memo_markdown
    assert memo.index("Project IRR (P50)") < memo.index("Price to target")
    report = arts.report_html
    assert report.index("Project IRR (P50, unlevered)") < report.index("Price to target")
    proposal = arts.proposal_markdown
    assert proposal.index("Project return (IRR, before any borrowing)") < proposal.index(
        "Contracted value (counted in your case)"
    )


# --- 2. no em or en dash, anywhere, in any of the four ---------------------------------------


def test_no_artifact_carries_an_em_or_en_dash_even_with_record_warnings(d59: _Fixture) -> None:
    """The owner has said this three times, so it is pinned WITH record warnings present.

    The memo and the HTML report used to be exempt from the scrub so that `quote.warnings`
    appeared byte-for-byte; the persisted campaign packs carried 100 and 101 em dashes as a result.
    The record here carries warnings that contain both dash characters, so this test fails if either
    exemption comes back.
    """
    dashed = (
        f"RECORD WARNING {EM_DASH} it carries an em dash, and an en dash in a range 5{EN_DASH}31, "
        "because the engine writes prose and the reader gets no dash."
    )
    quote = d59.quote.model_copy(update={"warnings": [dashed]})
    fx = _Fixture(quote=quote, site=d59.site)
    arts = _artifacts(fx)
    docs = {
        "memo": arts.memo_markdown,
        "proposal": arts.proposal_markdown,
        "report_html": arts.report_html,
        "consulting": _consulting(fx),
    }
    # Fixture sanity: the warning really reached the documents that reproduce warnings, so a pass
    # cannot mean "nothing rendered". The scrubbed head of the sentence is what survives. The
    # customer proposal deliberately reproduces no warning section, so it is swept for dashes
    # without this check.
    for name in ("memo", "report_html", "consulting"):
        assert "RECORD WARNING" in docs[name], (
            f"{name}: the record warning did not reach the document"
        )
    offences = {
        name: (doc.count(EM_DASH), doc.count(EN_DASH))
        for name, doc in docs.items()
        if EM_DASH in doc or EN_DASH in doc
    }
    assert not offences, f"em or en dash in rendered output: {offences}"


# --- 3. per-stream assumptions -----------------------------------------------------------------


def test_every_stream_gets_its_own_assumptions_in_every_artifact(d59: _Fixture) -> None:
    """One block per stream, floor and held-out alike, with the keys THAT stream cited.

    The held-out stream is the load-bearing half: the engine writes to `gated_upside` as well as
    `value_streams`, and a register reading only the first drops it.
    """
    arts = _artifacts(d59)
    vm = build_view_model(d59.quote, d59.site, assumptions=load_assumptions(_RELEASE))
    assert len(vm.stream_assumptions) == 4, "fixture sanity: three floor streams and one held out"
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html, _consulting(d59)):
        for row in vm.stream_assumptions:
            label = fmt.stream_label(row.stream)
            assert label in doc, f"stream {label} has no per-stream block"
        # The cited key is printed, and it is printed INSIDE the citing stream's block: between that
        # stream's heading and the next stream's heading. A key that only appeared in the run-wide
        # library register would satisfy a bare containment check and answer nobody's question.
        # Sliced from the per-stream REGISTER, not from the document: the revenue register above it
        # names every stream too, and a slice that started there would prove nothing about where the
        # key was printed.
        register = doc[doc.index(fmt.STREAM_ASSUMPTIONS_TITLE) :]
        arbitrage = register.index(fmt.stream_label("TARIFF_ARBITRAGE"))
        duos = register.index(fmt.stream_label("DUOS_RED_BAND_AVOIDANCE"), arbitrage)
        assert _CITED_KEYS[0] in register[arbitrage:duos], (
            "the assumption key a stream cites must appear inside that stream's own block"
        )


def test_a_stream_citing_no_key_states_the_gap_rather_than_printing_a_blank(
    d59: _Fixture,
) -> None:
    """A blank cell reads as "no assumption was made", which is false and is the worse failure."""
    arts = _artifacts(d59)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html, _consulting(d59)):
        assert "No assumption key cited" in doc or "cited NO assumption-library key" in doc


def test_a_streams_d57_approvals_are_shown_against_that_stream(d59: _Fixture) -> None:
    """D57 limb 4 at the stream level: the approval is read beside the pound it produced.

    Also pins the de-duplication: the fixture writes the same statement onto the stream twice, and a
    register that printed one sentence twice is not disclosure.
    """
    arts = _artifacts(d59)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html, _consulting(d59)):
        needle = _D57_STATEMENT if "<h2>" not in doc else escape(_D57_STATEMENT)
        flex = doc.index(fmt.stream_label("LOCAL_FLEX_PICLO"))
        assert needle in doc[flex:], "the D57 statement must appear in its own stream's block"
        assert "Assumed approved for this stream under D57" in doc
        # ...and the non-D57 statement on the same stream is NOT swallowed into the approvals list.
        assert (_RATE_STATEMENT if "<h2>" not in doc else escape(_RATE_STATEMENT)) in doc


def test_the_d57_approvals_also_reach_the_consolidated_assumptions_register(
    d59: _Fixture,
) -> None:
    """Limb 4's other half: the run-level register enumerates them too, in its FIRST block."""
    vm = build_view_model(d59.quote, d59.site, assumptions=load_assumptions(_RELEASE))
    first = vm.assumptions_page.blocks[0]
    assert "D57" in first.title, "the D57 block must lead the assumptions register"
    assert first.rows, "the fixture's D57 statement must produce a register row"
    for doc in (
        _artifacts(d59).memo_markdown,
        _artifacts(d59).report_html,
        _consulting(d59),
    ):
        assert (
            "Assumed-approved contracts, consents, awards and thresholds (D57)" in doc
            or escape("Assumed-approved contracts, consents, awards and thresholds (D57)") in doc
        )


# --- 4. the full sizing surface and the selection ---------------------------------------------


def test_the_sizing_surface_publishes_every_evaluated_candidate(d59: _Fixture) -> None:
    """FULL means full: EXACTLY one row per evaluation, counted against the record's own points.

    The count is exact rather than a lower bound on purpose. A lower bound passes on a table that
    drops the proxy-scored evaluations, which is the specific way a "full" surface stops being full:
    the confirmed rows are the reportable ones, so they are the ones a well-meaning filter keeps.
    """
    html = _consulting(d59)
    assert "THE FULL SIZING SURFACE" in html
    surface = html[html.index("THE FULL SIZING SURFACE") : html.index("HOW THE SELECTION WAS MADE")]
    rows = surface.count("<tr><td>")
    assert rows == len(_POINTS), (
        f"the surface must publish all {len(_POINTS)} evaluations; it rendered {rows}"
    )
    memo = _artifacts(d59).memo_markdown
    section = memo[memo.index(fmt.SIZING_SURFACE_TITLE) : memo.index(fmt.METHODOLOGY_TITLE)]
    md_rows = [line for line in section.splitlines() if line.startswith("| ") and " kW |" in line]
    assert len(md_rows) == len(_POINTS), (
        f"the memo's surface table must carry all {len(_POINTS)} evaluations; it carried "
        f"{len(md_rows)}"
    )
    # The proxy/confirm distinction survives into the reader's copy: a typical-day score and a
    # full-series solve are not the same evidence and the surface says which is which.
    assert "confirm" in section and "coarse" in section
    assert surface.count("confirm") and surface.count("coarse")


def test_the_selection_is_marked_once_and_on_the_confirmed_row(d59: _Fixture) -> None:
    """The proxy score at the SAME coordinates must not also read SELECTED.

    A coordinate-only match marked both, which told a reader the search selected a typical-day proxy
    evaluation. The fixture carries that exact collision.
    """
    html = _consulting(d59)
    surface = html[html.index("THE FULL SIZING SURFACE") : html.index("HOW THE SELECTION WAS MADE")]
    assert surface.count(">SELECTED<") == 1, "exactly one surface row is the selected candidate"
    memo = _artifacts(d59).memo_markdown
    section = memo[memo.index(fmt.SIZING_SURFACE_TITLE) :]
    section = section[: section.index(fmt.METHODOLOGY_TITLE)]
    assert section.count("| SELECTED |") == 1


def test_the_selection_chart_ranks_only_confirmed_candidates_and_quotes_the_reason(
    d59: _Fixture,
) -> None:
    """The owner asked for a chart making the MILP selection legible: out of what, and why."""
    html = _consulting(d59)
    assert "HOW THE SELECTION WAS MADE" in html
    chart = html[html.index("HOW THE SELECTION WAS MADE") :]
    confirmed = len([p for p in _POINTS if not p.proxy])
    proxied = len([p for p in _POINTS if p.proxy])
    assert f"Ranked here: {confirmed} confirmed candidate(s)" in chart
    assert f"{proxied} evaluation(s) were" in chart
    assert "excluded from this ranking by design" in chart
    # The record's own reason, verbatim (with the dash rewrite the whole document gets).
    assert escape(fmt.no_dashes(_SELECTION_REASON))[:80] in chart
    assert "Selection basis on the record: Unlevered." in chart


def test_the_dispatch_day_is_drawn_from_the_solved_half_hours(d59: _Fixture) -> None:
    """The owner asked for a graph showing exactly how the battery was dispatched on a given day."""
    html = _consulting(d59)
    assert "aria-label=" in html
    # The chart names the day it drew and the basis on which that day was chosen, both recorded.
    assert "highest-discharge day" in html
    report = _artifacts(d59).report_html
    assert "How the battery was dispatched on a real solved day" in report
    assert '<svg class="gqfig"' in report, "the HTML report must actually draw the figures"


# --- 5. the methodology section ---------------------------------------------------------------


def test_the_methodology_states_the_method_and_what_this_run_did(d59: _Fixture) -> None:
    """Fixed mechanism copy, then the record's own identifiers. Never an unrecorded claim."""
    arts = _artifacts(d59)
    for doc in (arts.memo_markdown, arts.proposal_markdown, arts.report_html, _consulting(d59)):
        assert "half-hourly dispatch optimisation" in doc or "half hourly" in doc
        assert "Sizing search version" in doc or escape("Sizing search version") in doc
        # This run's own recorded facts, copied: the search version and the stage counts.
        assert "m4-two-stage-v2" in doc
        assert "Selection basis, verbatim" in doc or escape("Selection basis, verbatim") in doc


def test_the_methodology_never_asserts_a_solver_the_record_does_not_name(d59: _Fixture) -> None:
    """The recorded rows are copies. Change the record's solver status and the page follows."""
    result = d59.quote.optimisation_result
    assert result is not None
    mocked = result.model_copy(update={"solver_status": s.SolverStatus.MOCK})
    quote = d59.quote.model_copy(update={"optimisation_result": mocked})
    doc = _artifacts(_Fixture(quote=quote, site=d59.site)).memo_markdown
    assert "**Dispatch solver status, verbatim:** mock" in doc
    assert "**Dispatch solver status, verbatim:** optimal" not in doc


# --- 6. the absence branches: a missing projection states itself ------------------------------


def test_a_record_with_no_sizing_grid_states_the_absence_rather_than_dropping_the_section(
    d59: _Fixture,
) -> None:
    """ "The search is not on this record" and "this pack does not show it" are different facts."""
    quote = d59.quote.model_copy(update={"sizing_grid": None, "dispatch_profile": None})
    fx = _Fixture(quote=quote, site=d59.site)
    arts = _artifacts(fx)
    assert fmt.SIZING_SURFACE_TITLE in arts.memo_markdown
    assert "carries no evaluated sizing grid" in arts.memo_markdown
    html = _consulting(fx)
    assert "THE FULL SIZING SURFACE" in html
    assert "HOW THE SELECTION WAS MADE" in html
    assert EM_DASH not in html and EN_DASH not in html
