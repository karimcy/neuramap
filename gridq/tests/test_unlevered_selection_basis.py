"""A financing-data absence must select UNLEVERED, and must be LABELLED unlevered.

**The defect.** `docs/validation_requirements.md` §3 and CLAUDE.md §2 both say the same thing in
the same words: *"the absence of a credit assessment or debt structure means the site underwrites
on an UNLEVERED basis with the confidence band adjusted, not that the site fails"*, and *"never
return 'no viable configuration' for a data reason of any kind"*. The sizing search did the
opposite. `SizingObjective.feasible` returns ``False`` the moment ``min_p90_dscr`` is ``None``, and
the sizing loop is never handed any `DebtTerms` at all (``debt_terms`` appears nowhere in
`gridq/optimisation/engine.py`; `build_debt_terms_from_credit` is called only at
`gridq/finance/financial_model.py:154`, after sizing has already chosen). So on every default run
EVERY candidate was infeasible, `_select` returned no selection, and the absence of debt was
published as an economic verdict. That is a DATA reason wearing an ECONOMIC verdict, which both
documents forbid.

**The mislabelling trap, and why it is tested harder than the selection itself.**
`SizingRun.sizing_grid()` used to hard-code ``selection_basis = "covenant"`` for any run whose
``selected`` was not ``None``. An unlevered pick made inside `_select` would therefore have
persisted onto the `QuoteRecord`, and into the pack, as a covenant-feasible optimum — a size that
was never tested against a covenant, presented as having met one. The basis is now copied off the
run, and these tests assert the label on the persisted `SizingGrid` and in the rendered report,
not merely on the in-memory pick.

**What must NOT change, and is pinned here too:**

* Where the covenant gate genuinely RAN, it is untouched. A measured covenant failure is an
  evidenced finding and may never be relabelled as a financing-data absence.
* The `candidate_viable` red-team gate (2026-07-07) survives: an unlevered basis chooses among
  candidates that return capital. It is not a licence to recommend the least-bad loss-maker.
* Placeholder gearing is NEVER threaded into selection. No test here injects the assumption
  library's ``financing.debt_gearing_pct`` into the objective, and none may: it carries
  ``status: placeholder`` and manufacturing a DSCR from it would be inventing the number the whole
  covenant test exists to measure.

**Reachability.** `_select` is the one choke point both production searches funnel through — the
legacy enumeration (`sizer.enumerate_sizing`, from `MilpEngine._run_search`) and the two-stage
search (`two_stage.two_stage_sizing`, which imports the same function object). Both are driven
here, and the assertions land on the **persisted `SizingGrid`** and on the **rendered report
bytes**, so deleting the wiring — the branch in `_select`, the ``selection_basis=basis`` thread at
either call site, or the copy in `sizing_grid()` — fails these tests rather than leaving them
green.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.finance import DebtTerms
from gridq.optimisation import two_stage
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.two_stage import two_stage_sizing
from gridq.outputs.consulting_report import render_report_for_quote
from gridq.quotes import assemble_quote_record
from gridq.quotes.confidence import assess_confidence
from gridq.schemas import ConfidenceBand, SizingGrid
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, SizingRun

COVENANT = 1.3

#: The claim the old `_covenant_account` published into eight client packs. It is FALSE: every
#: release from gb_v1_2 to gb_v1_10 carries `financing.debt_gearing_pct` (60.0, placeholder), and
#: the branch that said it had never read a library at all.
_FALSE_LIBRARY_CLAIM = "the assumption library carries no gearing"


def _objective() -> SizingObjective:
    return SizingObjective(min_p90_dscr_covenant=COVENANT)


class _RevenueSolver:
    """Synthetic solver emitting a constant FIRM revenue regardless of size (fixture only).

    Mirrors `tests/test_sizing_verdict_is_evidenced.py`: it makes the *finance* outcome the thing
    under test by holding the dispatch constant, so a candidate's project IRR is a clean function
    of revenue against the candidate's own capex.
    """

    def __init__(self, annual_revenue_gbp: float) -> None:
        self._rev = annual_revenue_gbp

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        return DispatchResult(
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
            value_streams=[
                s.ValueStreamResult(
                    stream=s.MarketStream.TARIFF_ARBITRAGE,
                    annual_revenue_gbp=round(self._rev, 2),
                    firmness=s.Firmness.FIRM,
                )
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


def _run(
    site: s.SiteModel,
    *,
    revenue_gbp: float,
    debt: DebtTerms | None,
    powers: list[float],
) -> SizingRun:
    """Drive the REAL sizing loop against the REAL finance model."""
    return enumerate_sizing(
        site,
        load_assumptions("gb_v0_1"),
        SearchSpace(power_kw_candidates=powers, duration_hour_candidates=[2.0]),
        dispatch_solver=_RevenueSolver(revenue_gbp),
        objective=_objective(),
        debt_terms=debt,
    )


# ------------------------------------------------------ the selection is MADE


def test_no_debt_resolves_so_the_sizing_search_selects_on_an_unlevered_basis(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The whole outcome, on the persisted grid: a viable size IS selected with no debt anywhere.

    Before this branch existed the same run persisted ``selected=None`` and a no-viable verdict,
    on candidates carrying positive returns, purely because no `DebtTerms` reached the sizing loop.
    """
    grid = _run(make_site(), revenue_gbp=120_000.0, debt=None, powers=[100.0, 200.0]).sizing_grid()

    # Preconditions: this really is the financing-data absence, not a covenant result.
    assert all(p.min_p90_dscr is None for p in grid.points), (
        "precondition: no DSCR anywhere, or this is not the no-debt condition"
    )
    assert all(p.levered_return_pct is None for p in grid.points)
    assert all(p.project_irr_pct is not None and p.project_irr_pct > 0.0 for p in grid.points), (
        "precondition: candidates must carry a POSITIVE unlevered return to be selectable"
    )

    assert grid.selected is not None, "a financing-data absence must not read as no viable size"
    assert grid.selection_basis == "unlevered"
    # Ranked on the objective's own rank_key: the higher unlevered IRR wins (100 kW earns the same
    # revenue off a smaller capex), never the bigger system.
    assert grid.selected.power_kw == 100.0
    assert grid.bankable is False


def test_an_unlevered_selection_is_never_stamped_covenant(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The mislabelling trap. `sizing_grid()` used to hard-code "covenant" whenever a size was
    selected, so this pick would have been persisted as covenant-feasible."""
    run = _run(make_site(), revenue_gbp=120_000.0, debt=None, powers=[100.0, 200.0])
    grid = run.sizing_grid()

    assert grid.selection_basis != "covenant"
    assert grid.selected is not None and grid.selected.passes_covenant is None, (
        "the selected point itself records that no covenant was ever applied to it"
    )
    assert run.selection_basis == grid.selection_basis, "the grid copies the run, never re-derives"

    reason = grid.selection_reason
    assert "UNLEVERED SELECTION" in reason
    assert "NEITHER PASSED NOR FAILED A COVENANT" in reason
    # The hard-won framing from the no-selection verdict survives inside the selection verdict.
    assert "THE COVENANT GATE DID NOT RUN" in reason
    assert "NOTHING HERE IS EVIDENCE THAT ANY SIZE FAILED THE COVENANT" in reason


def test_the_verdict_no_longer_asserts_the_library_carries_no_gearing(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A D45 defect in its own right: the branch published a fact about a file it never opened.

    The sentence was keyed only on "no candidate carries a DSCR" and asserted, in the same breath,
    that the assumption library carries no gearing. It does. This test both removes the false claim
    and proves it was false, by reading the release the campaign ran on.
    """
    library = load_assumptions("gb_v1_10")
    gearing = library.financing["debt_gearing_pct"]
    assert gearing.value == pytest.approx(60.0), (
        "the premise of this test: gb_v1_10 DOES carry a gearing, so the old sentence was false"
    )

    for revenue in (0.0, 120_000.0):
        run = _run(make_site(), revenue_gbp=revenue, debt=None, powers=[100.0])
        reason = run.sizing_grid().selection_reason
        assert _FALSE_LIBRARY_CLAIM not in reason
        # ...replaced by what the call actually knows: what the SIZING PATH was handed.
        assert "NO DEBT STRUCTURE WAS RESOLVED ON THE SIZING PATH" in reason
        assert "build_debt_terms_from_credit" in reason
        assert "no claim is made here about what any assumption release does" in reason


# ------------------------------------------------- what must NOT have changed


def test_a_candidate_that_never_returns_capital_is_still_never_selected(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The `candidate_viable` red-team gate (2026-07-07) survives the unlevered branch.

    An unlevered basis is a basis for ranking candidates that return capital. It must not become a
    route by which the least-bad loss-maker is presented as the optimum, which is the exact failure
    the 4 kW grid-corner "recommendation" was.
    """
    grid = _run(make_site(), revenue_gbp=0.0, debt=None, powers=[100.0]).sizing_grid()

    assert all(p.project_irr_pct is None for p in grid.points)
    assert grid.selected is None
    assert grid.selection_basis == "none"
    assert "UNLEVERED SELECTION" not in grid.selection_reason


def test_a_measured_covenant_failure_is_never_relabelled_as_a_financing_absence(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Where debt IS resolved the covenant gate is untouched: it ran, it rejected, and it says so.

    This is the guard against the obvious wrong fix — treating "no covenant-feasible candidate" as
    the unlevered trigger, which would have swallowed every real covenant failure.
    """
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    grid = _run(make_site(), revenue_gbp=1_000.0, debt=debt, powers=[4_000.0]).sizing_grid()

    measured = [p.min_p90_dscr for p in grid.points if p.min_p90_dscr is not None]
    assert measured, "precondition: injected debt must produce a real DSCR"
    assert grid.selected is None
    assert grid.selection_basis == "none", "a covenant that RAN may never yield an unlevered basis"
    assert "covenant gate RAN on" in grid.selection_reason
    assert "UNLEVERED SELECTION" not in grid.selection_reason


def test_a_covenant_feasible_selection_keeps_the_covenant_label(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The complement: a size that genuinely met the covenant is still stamped ``covenant``."""
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    grid = _run(make_site(), revenue_gbp=600_000.0, debt=debt, powers=[100.0]).sizing_grid()

    assert grid.selected is not None
    assert grid.selected.passes_covenant is True
    assert grid.selected.min_p90_dscr is not None and grid.selected.min_p90_dscr >= COVENANT
    assert grid.selection_basis == "covenant"


# ------------------------------------------------------- the two-stage search


def _stub_evaluate(*, project_irr_pct: float | None):
    """A `_evaluate_spec` double: prices every spec, no DSCR anywhere (the campaign condition)."""

    def _evaluate(site, assumptions, candidate: SizingCandidate, **kwargs) -> CandidateEvaluation:
        return CandidateEvaluation(
            candidate=candidate,
            optimisation_result=s.OptimisationResult(
                result_id="res_stub",
                request_id="req_stub",
                implementation_level=s.ImplementationLevel.PRODUCTION,
                bankable_status=s.BankableStatus.NOT_BANKABLE,
                selected_power_kw=candidate.power_kw,
                selected_energy_kwh=candidate.energy_kwh,
                duration_hours=candidate.duration_hours,
                value_streams=[],
                solver_status=s.SolverStatus.OPTIMAL,
            ),
            priceable=True,
            bankable=False,
            min_p90_dscr=None,
            covenant=COVENANT,
            passes_covenant=None,
            levered_return_pct=None,
            project_irr_pct=project_irr_pct,
            financial=None,
        )

    return _evaluate


@pytest.fixture(scope="module")
def two_stage_site(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("unlevered") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def test_the_two_stage_search_reaches_the_unlevered_basis_too(two_stage_site, monkeypatch) -> None:
    """The 2026-08-15 campaign ran on ``m4-two-stage-v2``, so a fix inert there is inert where it
    matters. Both searches call the same `_select`; this proves the two-stage caller threads the
    basis onto its own `SizingRun` rather than leaving the default in place."""
    site, lib = two_stage_site
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=6.0))

    run = two_stage_sizing(
        site,
        lib,
        base_solver=MilpDispatchSolver(),
        objective=_objective(),
        binding_capacity_kw=320.0,
        seed=0,
    )
    grid = run.sizing_grid()

    assert grid.search_version == "m4-two-stage-v2"
    assert grid.selected is not None
    assert grid.selection_basis == "unlevered"
    assert grid.bankable is False


# ------------------------------------------- the layer a reader actually reads


def _quote_carrying(grid: SizingGrid, site: s.SiteModel) -> s.QuoteRecord:
    """A `QuoteRecord` carrying a grid the REAL sizing loop produced (never a hand-built one).

    ``runs/`` is gitignored, so a test that loaded a run directory would pass vacuously on a fresh
    checkout. The record is therefore built here, but the *grid* inside it — the thing under test —
    is production output.
    """
    selected = grid.selected
    assert selected is not None
    result = s.OptimisationResult(
        result_id="res_unlevered",
        request_id="req_unlevered",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=selected.power_kw,
        selected_energy_kwh=selected.energy_kwh,
        duration_hours=selected.duration_hours,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=120_000.0,
                firmness=s.Firmness.FIRM,
                gate_status="met",
            )
        ],
        solver_status=s.SolverStatus.OPTIMAL,
    )
    from gridq.finance import run_financial_model

    assumptions = load_assumptions("gb_v0_1")
    case = run_financial_model(site, result, assumptions).case
    return assemble_quote_record(
        site,
        result,
        case,
        assumptions,
        quote_mode=s.QuoteMode.INDICATIVE,
        sizing_grid=grid,
        half_hourly_basis=s.HalfHourlyBasis.METERED,
        confidence_ceiling=s.ConfidenceBand.HIGH,
    )


def test_the_rendered_report_states_the_unlevered_basis(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Asserted on the RENDERED BYTES: the pack must say the size was chosen without a covenant.

    A basis that reaches the record but not the document is a caveat nobody reads.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=120_000.0, debt=None, powers=[100.0, 200.0]).sizing_grid()
    assert grid.selection_basis == "unlevered"

    html = render_report_for_quote(
        _quote_carrying(grid, site), site, assumptions=load_assumptions("gb_v0_1")
    )

    # The renderer's own caption for the basis.
    assert "chosen on an unlevered basis" in html
    assert "no debt structure was resolved" in html
    assert "UNLEVERED basis" in html
    # ...and the RECORD's own verdict, verbatim, so the reader gets the search's words and not a
    # summary of them. All three of these sentences are what a reader needs and none of them was
    # reachable before: the run persisted "no viable configuration" instead.
    assert "UNLEVERED SELECTION" in html
    assert "NEITHER PASSED NOR FAILED A COVENANT" in html
    assert "THE COVENANT GATE DID NOT RUN" in html
    # The false claim is gone from the artifact a client actually reads, which is where it was
    # published in the first place.
    assert _FALSE_LIBRARY_CLAIM not in html
    assert "no claim is made here about what any assumption release" in html
    assert "indicative fallback" not in html.lower(), (
        "an unlevered selection is a recorded basis under validation_requirements.md 3, not a "
        "fallback, and the two must never be conflated in the pack"
    )


def test_the_confidence_band_is_capped_by_the_unlevered_financing_basis(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """§3's "with the confidence band adjusted", made real and made binding.

    Without this the pack would tell the reader the size was chosen with no debt structure while
    the band beside it reported the same evidential strength as a covenant-tested one.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=120_000.0, debt=None, powers=[100.0, 200.0]).sizing_grid()
    quote = _quote_carrying(grid, site)

    assessment = assess_confidence(site, quote)

    assert assessment.band is ConfidenceBand.LOW
    binding = {f.name for f in assessment.binding_factors}
    assert "financing basis" in binding, (
        "the unlevered basis must be one of the factors HOLDING THE BAND DOWN, not merely present"
    )
    reason = next(f.reason for f in assessment.factors if f.name == "financing basis")
    assert "UNLEVERED" in reason
    assert "credit assessment" in reason, "the factor must carry the confirmation ask"


def test_a_covenant_selection_is_not_capped_by_the_financing_basis_factor(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The complement: the new factor must not quietly cap every record it touches."""
    site = make_site()
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    grid = _run(site, revenue_gbp=600_000.0, debt=debt, powers=[100.0]).sizing_grid()
    assert grid.selection_basis == "covenant"

    assessment = assess_confidence(site, _quote_carrying(grid, site))

    factor = next(f for f in assessment.factors if f.name == "financing basis")
    assert factor.ceiling is ConfidenceBand.HIGH


def test_the_financing_cap_fires_on_the_absent_dscr_not_on_the_basis_label(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Keying the cap on ``selection_basis == "unlevered"`` alone would leave a hole.

    `MilpEngine._choose`'s indicative fallback can also report a size for which no DSCR was ever
    computed — that is precisely what happens when the only viable candidates carry an explicit
    non-deliverable verdict, which `sizer._unlevered_pool` rejects and the fallback does not. Such a
    record has no debt structure anywhere in it and would have carried a HIGH financing factor. The
    trigger is therefore the selected point's own null ``min_p90_dscr`` — the absence itself.
    """
    site = make_site()
    grid = _run(site, revenue_gbp=120_000.0, debt=None, powers=[100.0]).sizing_grid()
    assert grid.selected is not None and grid.selected.min_p90_dscr is None
    # Relabel the basis WITHOUT touching the numbers: the cap must not depend on the string.
    relabelled = grid.model_copy(update={"selection_basis": "indicative_fallback"})

    factor = next(
        f
        for f in assess_confidence(site, _quote_carrying(relabelled, site)).factors
        if f.name == "financing basis"
    )
    assert factor.ceiling is ConfidenceBand.LOW
    assert "NO P90 DSCR EXISTS" in factor.reason
    # ...and it quotes whatever basis the record actually recorded, rather than asserting one.
    assert "indicative_fallback" in factor.reason


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
