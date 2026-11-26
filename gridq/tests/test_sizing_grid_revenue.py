"""A no-spec run must still say what its best candidate EARNED — per-candidate £ on the grid.

**The defect.** A `SizingGridPoint` carried ``power_kw``, ``duration_hours``, ``energy_kwh``,
``project_irr_pct``, ``levered_return_pct``, ``min_p90_dscr``, ``passes_covenant``, ``priceable``,
``bankable``, ``solver_status``, ``stage``, ``proxy``, ``end_of_life_deliverable`` — every ratio the
objective ranks on, and **no revenue field at all**. So a run that selected no system persisted a
grid of 170 evaluated candidates that could not state what any of them had earned, while the
top-level record's streams all read ``0.00`` because it reports no candidate. The only reading
available to a pack was *"this site earns nothing"* — on runs whose own recorded stream note said
the dispatch had DISCHARGED 30,133.0 kWh/yr, while `optimisation.valuation` was already telling
readers "the per-candidate figures are in the sizing grid" about figures the grid did not carry.

    "this site earns nothing"                                                    (false)
    "the best candidate earns £1,463/yr and still does not cover its costs"      (true)

Both are negatives. Only the second is a measurement, and only the second can be checked.

**What is asserted, and at which layer.** The grid is production output of the REAL
`enumerate_sizing` loop against the REAL finance model, never a hand-built `SizingGrid`. ``runs/``
is gitignored, so a
test that loaded a run directory would pass vacuously on a fresh checkout; the `QuoteRecord` is
therefore assembled in the test, but the grid inside it is not. The reader assertions land on the
**rendered pack bytes**, because a figure that reaches the record and not the document is a figure
nobody reads.

**The fixture is deliberately the campaign's own shape** — a non-zero measured floor whose
candidates still never return capital, so ``selected is None`` and ``selection_basis == "none"``.
That matters
for mutation-verification: a fixture whose revenue were zero would leave every assertion here green
under a mutation that deleted the copy, because there would be nothing to distinguish an absent
figure from a measured one. See `test_the_fixture_really_is_the_no_spec_condition`.

**D45.** The three states a ``£0.00`` collapses into are kept apart on the record: an unpriceable
candidate carries ``None`` (never ``0.0``), a floor stream at ``0.0`` was priced and declined, and a
held-out stream is marked ``in_priced_floor=False`` with its gate. Each is pinned below.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.finance import run_financial_model
from gridq.outputs.consulting_report import render_report_for_quote
from gridq.quotes import assemble_quote_record
from gridq.quotes.disclosures import candidate_revenue_disclosure, record_disclosures
from gridq.schemas import SIZING_GRID_SCHEMA_VERSION, SizingGrid
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing
from gridq.sizing.sizer import SizingRun

COVENANT = 1.3

#: Revenue low enough that no candidate ever returns capital (so nothing is selectable on ANY
#: basis, including the 2026-08-17 unlevered one) yet high enough to be a plain non-zero
#: MEASUREMENT. This is the Bishops Cleeve Ash Plant shape, whose own measured floor was £1,463.15.
UNVIABLE_BUT_MEASURED_REVENUE = 1_463.15

#: Revenue high enough that candidates DO return capital, used only to prove the disclosure is
#: scoped to no-spec runs rather than firing on every record.
VIABLE_REVENUE = 120_000.0


class _StreamMixSolver:
    """Synthetic solver emitting a fixed stream MIX at a constant revenue (fixture only).

    Three streams on purpose, because the per-stream distinction is half of what is under test: one
    FIRM stream carrying the £ (in the priced floor), one FIRM stream at exactly ``0.0`` (in the
    floor, priced, declined — a MEASURED zero), and one CONDITIONAL stream on a shut-ish gate (held
    OUT of the floor — a zero that is not a floor measurement at all). Revenue is held constant
    across sizes so a candidate's return is a clean function of revenue against its own capex.
    """

    MODEL_VERSION = "stream-mix-fixture-v1"

    def __init__(self, annual_revenue_gbp: float, *, no_sizing_below_kw: float = 0.0) -> None:
        self._rev = annual_revenue_gbp
        #: Below this power the dispatch reports NO POSITIVE SIZING, which is how production
        #: produces an unpriceable candidate: finance then raises `InsufficientInputsError` and
        #: `_evaluate_spec` records ``priceable=False`` with no cashflow model at all.
        self._no_sizing_below_kw = no_sizing_below_kw

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        sized = problem.power_kw >= self._no_sizing_below_kw
        return DispatchResult(
            power_kw=problem.power_kw if sized else 0.0,
            energy_kwh=problem.energy_kwh if sized else 0.0,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
            value_streams=[
                s.ValueStreamResult(
                    stream=s.MarketStream.TARIFF_ARBITRAGE,
                    annual_revenue_gbp=round(self._rev, 2),
                    firmness=s.Firmness.FIRM,
                    gate_status="open",
                ),
                s.ValueStreamResult(
                    stream=s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
                    annual_revenue_gbp=0.0,
                    firmness=s.Firmness.FIRM,
                    gate_status="open",
                ),
                s.ValueStreamResult(
                    stream=s.MarketStream.CAPACITY_MARKET,
                    annual_revenue_gbp=0.0,
                    firmness=s.Firmness.CONDITIONAL,
                    gate_status="conditional",
                    unlock_condition="a connection capacity of at least 1.000 MW",
                ),
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


def _run(
    site: s.SiteModel,
    *,
    revenue_gbp: float,
    powers: list[float],
    no_sizing_below_kw: float = 0.0,
) -> SizingRun:
    """Drive the REAL sizing loop against the REAL finance model — no stubbed evaluation."""
    return enumerate_sizing(
        site,
        load_assumptions("gb_v0_1"),
        SearchSpace(power_kw_candidates=powers, duration_hour_candidates=[2.0]),
        dispatch_solver=_StreamMixSolver(revenue_gbp, no_sizing_below_kw=no_sizing_below_kw),
        objective=SizingObjective(min_p90_dscr_covenant=COVENANT),
        # No DebtTerms: exactly what production threads (``debt_terms`` appears nowhere in
        # optimisation/engine.py), so this is the real no-DSCR condition rather than a contrivance.
        debt_terms=None,
    )


@pytest.fixture
def no_spec_grid(make_site: Callable[..., s.SiteModel]) -> SizingGrid:
    """The campaign condition, from the real loop: measured revenue, nothing selectable."""
    return _run(
        make_site(), revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[25.0, 100.0]
    ).sizing_grid()


def _no_spec_quote(grid: SizingGrid, site: s.SiteModel) -> s.QuoteRecord:
    """A `QuoteRecord` for a run that specified NO system, carrying a production-made grid.

    ``selected_power_kw`` is left unset, which is what a real no-spec run persists (verified on
    ``runs/quote_run_a7bfc8821e1a273c``: ``selected_power_kw``, ``selected_energy_kwh`` and
    ``duration_hours`` are all null and ``financial_case`` is ``None``). ``financial_case=None``
    likewise: there is no priced spec to build a case from, which is precisely why the pack has
    nothing but the grid to tell a reader what the site measured.
    """
    result = s.OptimisationResult(
        result_id="res_no_spec",
        request_id="req_no_spec",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        # Every stream reads 0.00 because the record reports NO CANDIDATE — the exact top-level
        # shape whose only available reading was "this site earns nothing".
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.FIRM,
                gate_status="open",
            )
        ],
        solver_status=s.SolverStatus.OPTIMAL,
    )
    assert not result.selected_power_kw, "fixture must be a NO-SPEC record or nothing is proven"
    return assemble_quote_record(
        site,
        result,
        None,
        load_assumptions("gb_v0_1"),
        sizing_grid=grid,
    )


# ------------------------------------------------- the fixture is the real condition


def test_the_fixture_really_is_the_no_spec_condition(no_spec_grid: SizingGrid) -> None:
    """Preconditions asserted, not assumed — the trap that refuted an Outcome-1 mutation claim.

    An adversarial review of the previous outcome killed a ``fails_if_deleted`` claim because its
    fixture could not reach the branch at all: the pool was empty, so the test passed under the very
    mutation it named. Every assertion below is a precondition this module's mutations depend on, so
    a fixture that stops exercising the condition fails HERE rather than going quietly green.
    """
    assert no_spec_grid.selected is None, "nothing may be selectable, or this is not a no-spec run"
    assert no_spec_grid.selection_basis == "none"
    assert no_spec_grid.points, "an empty grid proves nothing"
    # No DSCR anywhere and no return anywhere — the campaign's own state.
    assert all(p.min_p90_dscr is None for p in no_spec_grid.points)
    assert all(p.project_irr_pct is None for p in no_spec_grid.points)
    # ...and yet every candidate WAS priced, and priced at a strictly positive revenue. That is the
    # whole point: the site earns something the record could not previously state.
    assert all(p.priceable for p in no_spec_grid.points)
    assert all(
        p.annual_revenue_gbp is not None and p.annual_revenue_gbp > 0.0 for p in no_spec_grid.points
    ), "a zero-revenue fixture would make every mutation below pass vacuously"


# ------------------------------------------------- the £ are ON the persisted grid


def test_a_no_spec_run_still_carries_per_candidate_revenue(no_spec_grid: SizingGrid) -> None:
    """The headline: every evaluated candidate carries its measured priced floor and cost stack."""
    assert no_spec_grid.selected is None, "precondition: this is the no-spec case"

    for point in no_spec_grid.points:
        assert point.annual_revenue_gbp == pytest.approx(UNVIABLE_BUT_MEASURED_REVENUE), (
            "the candidate's priced floor must be persisted, not dropped"
        )
        assert point.annual_operating_cost_gbp is not None
        assert point.annual_dispatch_cost_gbp is not None
        # The recorded floor is measured, and it does NOT cover the recorded cost — the honest
        # negative this whole change exists to make sayable.
        assert (
            point.annual_revenue_gbp
            < point.annual_operating_cost_gbp + point.annual_dispatch_cost_gbp
        )


def test_the_grid_floor_total_is_the_figure_finance_actually_used(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The persisted floor is the finance model's own number, not a re-derivation of it.

    Asserted against `run_financial_model` run independently over the same spec: if the projection
    ever started re-summing streams itself, the two could drift and the grid would carry a figure no
    other artifact could reproduce.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[100.0]).sizing_grid()
    point = grid.points[0]

    independent = run_financial_model(
        site,
        s.OptimisationResult(
            result_id="res_check",
            request_id="req_check",
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            selected_power_kw=point.power_kw,
            selected_energy_kwh=point.energy_kwh,
            duration_hours=point.duration_hours,
            value_streams=_StreamMixSolver(UNVIABLE_BUT_MEASURED_REVENUE)
            .solve(
                DispatchProblem(
                    site_model=site,
                    assumptions=load_assumptions("gb_v0_1"),
                    power_kw=point.power_kw,
                    energy_kwh=point.energy_kwh,
                    duration_hours=point.duration_hours,
                    requested_streams=[],
                )
            )
            .value_streams,
            solver_status=s.SolverStatus.BASELINE,
        ),
        load_assumptions("gb_v0_1"),
    )

    assert point.annual_revenue_gbp == independent.cashflows.total_annual_revenue_gbp
    assert point.annual_operating_cost_gbp == independent.cashflows.total_annual_operating_cost_gbp
    assert point.annual_dispatch_cost_gbp == independent.cashflows.annual_dispatch_cost_gbp
    # And the floor total is exactly the sum of the streams the grid marks as being IN the floor —
    # one definition of the priced floor, shared with `finance.cashflows.revenue_by_stream`.
    assert point.annual_revenue_gbp == pytest.approx(
        sum(v.annual_revenue_gbp for v in point.revenue_streams if v.in_priced_floor)
    )


# ------------------------------------------------- D45: the three zeros stay apart


def test_a_floor_zero_and_a_gated_zero_are_distinguishable_on_the_grid(
    no_spec_grid: SizingGrid,
) -> None:
    """Two ``£0.00`` figures, two different facts, and the record keeps them apart (D45).

    ``DUOS_RED_BAND_AVOIDANCE`` at ``0.0`` inside the floor was priced and declined — a measurement.
    ``CAPACITY_MARKET`` at ``0.0`` was never in the floor at all. A grid storing only the £ would
    render both as the same cell, which is the defect CLAUDE.md §2 describes: a null that reads as
    a measured zero.
    """
    streams = {v.stream: v for v in no_spec_grid.points[0].revenue_streams}

    priced_zero = streams["DUOS_RED_BAND_AVOIDANCE"]
    assert priced_zero.annual_revenue_gbp == 0.0
    assert priced_zero.in_priced_floor is True
    assert priced_zero.firmness == "firm"

    gated_zero = streams["CAPACITY_MARKET"]
    assert gated_zero.annual_revenue_gbp == 0.0
    assert gated_zero.in_priced_floor is False, (
        "a conditional stream is upside, never part of the priced floor"
    )
    assert gated_zero.gate_status == "conditional", "the gate state must say WHY it was held out"

    # The two zeros are equal as numbers and unequal as records. That is the whole requirement.
    assert priced_zero.annual_revenue_gbp == gated_zero.annual_revenue_gbp
    assert priced_zero.in_priced_floor != gated_zero.in_priced_floor


def test_an_unpriceable_candidate_carries_none_not_zero(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """An absent figure stays absent. A ``0.0`` here would be a confidently wrong number.

    Reached the way production reaches it: a dispatch that returns **no positive sizing**, which
    makes `run_financial_model` raise `InsufficientInputsError` (it can cost neither capex nor
    cashflows), which `_evaluate_spec` records as ``priceable=False``. No cashflow model exists, so
    no floor revenue was ever computed. Writing zero would make it indistinguishable from a
    candidate that WAS priced and earned nothing, and would then feed any "best candidate earned…"
    sentence as a real measurement — the exact defect CLAUDE.md §2 calls a confidently wrong number.
    """
    grid = _run(
        make_site(),
        revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE,
        powers=[25.0, 100.0],
        no_sizing_below_kw=50.0,
    ).sizing_grid()
    unpriceable = [p for p in grid.points if not p.priceable]
    assert unpriceable, "precondition: the fixture must contain an unpriceable candidate"
    assert [p for p in grid.points if p.priceable], (
        "precondition: a priced candidate must also exist, or 'None not 0.0' proves nothing"
    )

    for point in unpriceable:
        assert point.annual_revenue_gbp is None, "an unmeasured floor must never read as £0"
        assert point.annual_operating_cost_gbp is None
        assert point.annual_dispatch_cost_gbp is None
        # Its dispatch DID report streams, so the per-stream copy is still there and is still the
        # dispatch's own figure — an absent FLOOR total is not an absent dispatch.
        assert point.revenue_streams, "the candidate's own streams are still a record"


def test_the_grid_contract_is_versioned_at_the_grid_not_globally(no_spec_grid: SizingGrid) -> None:
    """The version bump is declared, and it is declared LOCALLY on purpose.

    `SizingGrid` is excluded from the canonical hash input (`quotes.assembly._canonical_run_input`
    never reads it), so it feeds no ``input_hash``/``quote_id``/``run_id``; and the package-wide
    `SCHEMA_VERSION` is pinned to `OptimisationResult.schema_version` by `engine.conformance`, so
    moving it would re-mint every identity in the repo to version an additive output projection.
    """
    assert SIZING_GRID_SCHEMA_VERSION == "0.2.0"
    assert no_spec_grid.schema_version == SIZING_GRID_SCHEMA_VERSION
    assert all(p.schema_version == SIZING_GRID_SCHEMA_VERSION for p in no_spec_grid.points)
    assert all(
        v.schema_version == SIZING_GRID_SCHEMA_VERSION
        for p in no_spec_grid.points
        for v in p.revenue_streams
    )
    # The global stays where it was: this projection is not part of any run's identity.
    assert s.SCHEMA_VERSION == "0.1.0"


# ------------------------------------------------- the pack SAYS it


def test_the_rendered_pack_states_what_the_best_candidate_earned(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Asserted on the RENDERED BYTES — the layer a reader reads.

    Persisting the £ and never printing them would leave the pack saying exactly what it said
    before. These are the sentences that replace "this site earns nothing".
    """
    site = make_site()
    grid = _run(site, revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[25.0, 100.0]).sizing_grid()
    assert grid.selected is None, "precondition: a no-spec run"

    html = render_report_for_quote(
        _no_spec_quote(grid, site), site, assumptions=load_assumptions("gb_v0_1")
    )

    assert "WHAT THE BEST EVALUATED CANDIDATE ACTUALLY EARNED" in html
    # The measured floor itself, formatted as the pack formats it.
    assert f"£{UNVIABLE_BUT_MEASURED_REVENUE:,.2f}/yr" in html
    # ...and the comparison stated as the subtraction it is, so a reader can redo it.
    assert "does NOT cover" in html
    assert "before capex, debt service and tax" in html
    # The per-stream split, with both kinds of zero named for what they are.
    assert "Priced floor, per stream" in html
    assert "was priced and the optimiser declined it" in html
    assert "Held OUT of the floor and excluded from every figure above" in html
    assert "CAPACITY_MARKET" in html
    # The conclusion a reader is entitled to.
    assert "does not earn nothing" in html


def test_the_disclosure_quotes_the_full_series_candidate_not_the_proxy(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A proxy point reads HIGH and must never be the figure quoted (D32).

    On the demo site the best proxy floor is £66,651.91 against £61,889.20 confirmed on the full
    series, so quoting the proxy would overstate the site by 8% in the one sentence written to stop
    it being understated. The grid persists proxy points deliberately; this pins that the reader
    filters them.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[25.0, 100.0]).sizing_grid()
    # Add a proxy point carrying an inflated floor, exactly as a typical-period scoring solve would.
    inflated = grid.points[0].model_copy(
        update={"proxy": True, "stage": "coarse", "annual_revenue_gbp": 999_999.0}
    )
    with_proxy = grid.model_copy(update={"points": [inflated, *grid.points]})

    disclosure = candidate_revenue_disclosure(
        s.OptimisationResult(
            result_id="res_p",
            request_id="req_p",
            implementation_level=s.ImplementationLevel.PRODUCTION,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.OPTIMAL,
        ),
        with_proxy,
    )

    assert disclosure is not None
    assert "999,999.00" not in disclosure.text, "a proxy figure must never be quoted as measured"
    assert f"£{UNVIABLE_BUT_MEASURED_REVENUE:,.2f}/yr" in disclosure.text


def test_the_disclosure_is_scoped_to_no_spec_runs(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """It must not fire on a run that specified a system — the pack already states those figures.

    A disclosure that fired on healthy runs would be noise, and noise is how a real disclosure gets
    skipped (this module's own stated rule).
    """
    site = make_site()
    grid = _run(site, revenue_gbp=VIABLE_REVENUE, powers=[100.0]).sizing_grid()
    selected = grid.points[0]

    specified = s.OptimisationResult(
        result_id="res_spec",
        request_id="req_spec",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=selected.power_kw,
        selected_energy_kwh=selected.energy_kwh,
        duration_hours=selected.duration_hours,
        solver_status=s.SolverStatus.OPTIMAL,
    )

    assert candidate_revenue_disclosure(specified, grid) is None
    assert not [
        d for d in record_disclosures(site, specified, grid) if d.code == "CANDIDATE_REVENUE"
    ]


def test_a_grid_with_no_measured_revenue_makes_no_claim(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Silence stays silence: with nothing measured, the disclosure does not fire at all.

    It must not fall back to £0, and it must not fall back to a proxy. Saying what the record cannot
    evidence would recreate the defect in the opposite direction — and stating that no figure exists
    is already `unevidenced_return_disclosure`'s job, not this one's.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[100.0]).sizing_grid()
    stripped = grid.model_copy(
        update={
            "points": [
                p.model_copy(
                    update={
                        "annual_revenue_gbp": None,
                        "annual_operating_cost_gbp": None,
                        "annual_dispatch_cost_gbp": None,
                    }
                )
                for p in grid.points
            ]
        }
    )

    no_spec = s.OptimisationResult(
        result_id="res_none",
        request_id="req_none",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        solver_status=s.SolverStatus.OPTIMAL,
    )

    assert candidate_revenue_disclosure(no_spec, stripped) is None
    # ...while the grid that DOES carry figures fires, so the guard above is discriminating rather
    # than the disclosure simply being dead.
    assert candidate_revenue_disclosure(no_spec, grid) is not None


def test_an_unrecorded_cost_line_is_never_treated_as_zero(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """No revenue-vs-cost verdict is issued against a cost line that does not exist (D45).

    "It covers its costs" resting on an absent cost would be the same null-reads-as-zero defect one
    layer up from the one this change removes.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=UNVIABLE_BUT_MEASURED_REVENUE, powers=[100.0]).sizing_grid()
    costless = grid.model_copy(
        update={
            "points": [
                p.model_copy(update={"annual_operating_cost_gbp": None}) for p in grid.points
            ]
        }
    )

    disclosure = candidate_revenue_disclosure(
        s.OptimisationResult(
            result_id="res_c",
            request_id="req_c",
            implementation_level=s.ImplementationLevel.PRODUCTION,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.OPTIMAL,
        ),
        costless,
    )

    assert disclosure is not None, "the measured revenue is still worth stating"
    assert "was not recorded on the grid" in disclosure.text
    assert "not a cost of zero" in disclosure.text
    assert "does NOT cover" not in disclosure.text, "no verdict may rest on an absent cost line"
    assert "COVERS" not in disclosure.text
