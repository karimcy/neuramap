"""Selection metric and no-spec verdicts: N4 + the N7-CAPACITY rider (WP-0 review 2026-08-10).

- **N4** — the DEFAULT path (any site with no credit assessment has no `DebtTerms`, hence no
  DSCR, hence no covenant-feasible candidate) ranked its fallback pool by gross priced-floor
  revenue: a metric with no capex term, correlated with size. The IRR-optimal small system the
  search ranked first lost to the biggest revenue earner in the cloud. The fallback now ranks on
  `objective.rank_key` — the same key the search optimised — and states which metric chose.
- **N7-CAPACITY** — "no viable configuration" is a FINAL claim that the asset does not work at
  any size. It must never stand in for a missing connection datum: with NO import capacity declared
  at all the solver's connection limit resolves to 0 kW, so charging is forced to zero and the £0
  floors are mechanical, not economic. That case is a DATA verdict.

  **Corrected 2026-08-12.** The rider originally fired on the `MIC ≈ peak within 1%` metadata
  signature as well, and returned `insufficient_data` for it. The engine's own run record refutes
  the premise (three live sites carry that signature and charged 121,777 kWh/yr at full rated power
  with median measured headroom 118.7 kW and priced floors to £18,510/yr), so that case is an
  ECONOMIC outcome and keeps `no_viable_configuration`; the signature rides alongside as a data ask.
  Full coverage of the corrected contract lives in `tests/test_engine_connection_basis.py`.
"""

from __future__ import annotations

import pytest

import gridq.schemas as s
from gridq.optimisation.engine import MilpEngine, _indicative_reason
from gridq.sizing.objective import SizingObjective
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, SizingRun

_OBJECTIVE = SizingObjective(min_p90_dscr_covenant=1.3)


def _result(power_kw: float, floor_revenue_gbp: float) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_n4",
        request_id="req_n4",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=power_kw,
        selected_energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=floor_revenue_gbp,
                firmness=s.Firmness.FIRM,
            )
        ],
        solver_status=s.SolverStatus.OPTIMAL,
    )


def _evaluation(
    power_kw: float,
    floor_revenue_gbp: float,
    *,
    project_irr_pct: float | None,
    levered_return_pct: float | None = None,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=SizingCandidate(power_kw=power_kw, duration_hours=2.0),
        optimisation_result=_result(power_kw, floor_revenue_gbp),
        priceable=True,
        bankable=False,
        min_p90_dscr=None,
        covenant=1.3,
        passes_covenant=None,
        levered_return_pct=levered_return_pct,
        project_irr_pct=project_irr_pct,
        financial=None,
        stage="confirm",
        proxy=False,
    )


def _run(candidates: list[CandidateEvaluation]) -> SizingRun:
    return SizingRun(
        search_space_size=len(candidates),
        candidates_evaluated=candidates,
        selected=None,  # no covenant-feasible candidate — the default, no-debt path
        selection_reason="no bankable candidate: no size meets the P90 DSCR covenant",
        rejected_by_covenant=[],
        bankable=False,
        assumption_version="gb_v0_1",
    )


# ----------------------------------------------------------------------------- N4


def test_n4_fallback_ranks_on_the_objective_not_on_gross_floor_revenue() -> None:
    """The review's illustration, pinned: an IRR-optimal small system vs a revenue-maximal big
    one. The old metric (£ priced floor) picks the 1,500 kW at 2.4% IRR — a ~4x quote for a worse
    return; `rank_key` picks the system the search itself ranked first."""
    small = _evaluation(350.0, 96_000.0, project_irr_pct=9.1)
    big = _evaluation(1_500.0, 188_000.0, project_irr_pct=2.4)
    run = _run([big, small])

    # The metric the fallback USED to rank on still prefers the big system — the defect was real.
    assert MilpEngine._floor_value(big) > MilpEngine._floor_value(small)

    chosen = MilpEngine()._choose(run, _OBJECTIVE)
    assert chosen is small
    assert chosen.candidate.power_kw == 350.0


def test_n4_levered_return_outranks_project_irr_exactly_as_the_search_ranks() -> None:
    """`rank_key`'s ordering is inherited whole: a candidate carrying the primary (levered)
    metric outranks one carrying only the fallback, whatever the priced-floor £ say."""
    levered = _evaluation(200.0, 10_000.0, project_irr_pct=4.0, levered_return_pct=6.0)
    unlevered_only = _evaluation(900.0, 500_000.0, project_irr_pct=30.0)
    assert MilpEngine()._choose(_run([unlevered_only, levered]), _OBJECTIVE) is levered


def test_n4_smaller_system_still_breaks_a_tie() -> None:
    small = _evaluation(200.0, 10_000.0, project_irr_pct=7.0)
    large = _evaluation(800.0, 90_000.0, project_irr_pct=7.0)
    assert MilpEngine()._choose(_run([large, small]), _OBJECTIVE) is small


def test_n4_unviable_candidates_are_still_never_recommended() -> None:
    """The `candidate_viable` gate (2026-07-07) is untouched: no positive metric, no spec."""
    run = _run([_evaluation(400.0, 120_000.0, project_irr_pct=-3.0)])
    assert MilpEngine()._choose(run, _OBJECTIVE) is None


def test_n4_no_debt_structure_still_yields_a_recommendation_on_the_unlevered_metric() -> None:
    """CLAUDE.md §2: never refuse a site for a FINANCING-data reason. With no credit assessment
    there is no DSCR and no levered return anywhere in the cloud — the run must still recommend,
    ranked on the unlevered project IRR, and must SAY that is what selected it."""
    best = _evaluation(300.0, 40_000.0, project_irr_pct=8.5)
    other = _evaluation(1_200.0, 150_000.0, project_irr_pct=1.2)
    assert all(e.min_p90_dscr is None and e.levered_return_pct is None for e in (best, other))

    chosen = MilpEngine()._choose(_run([other, best]), _OBJECTIVE)
    assert chosen is best
    reason = _indicative_reason(chosen)
    assert "UNLEVERED project IRR" in reason
    assert "levered (equity) IRR" in _indicative_reason(
        _evaluation(300.0, 40_000.0, project_irr_pct=8.5, levered_return_pct=11.0)
    )


# ------------------------------------------------------------------- N7-CAPACITY


def _site_with(make_site, *, mic_kw: float, peak_kw: float | None) -> s.SiteModel:
    site = make_site()
    load = site.load_data.model_copy(update={"max_import_kw": peak_kw})
    conn = s.ConnectionDetails(mic_kw=mic_kw)
    return site.model_copy(update={"load_data": load, "connection": conn})


def test_n7_capacity_mic_equal_to_the_observed_peak_is_a_data_verdict(make_site) -> None:
    note = MilpEngine._capacity_headroom_note(_site_with(make_site, mic_kw=420.0, peak_kw=420.0))
    assert note is not None
    assert note.startswith("connection capacity unknown")
    assert "no viable configuration" not in note
    assert "connection_mic_mec_asc" in note


def test_n7_capacity_undeclared_limit_is_a_data_verdict(make_site) -> None:
    note = MilpEngine._capacity_headroom_note(_site_with(make_site, mic_kw=0.0, peak_kw=420.0))
    assert note is not None and note.startswith("connection capacity unknown")


def test_n7_capacity_real_headroom_leaves_the_economics_verdict_alone(make_site) -> None:
    """A site with genuine headroom that simply does not pay still gets the FINAL economics
    verdict — the guard must not swallow honest no-viable results."""
    assert (
        MilpEngine._capacity_headroom_note(_site_with(make_site, mic_kw=900.0, peak_kw=420.0))
        is None
    )


def test_n7_capacity_verdict_is_the_data_ask_only_when_no_capacity_is_declared(
    make_site, monkeypatch
) -> None:
    """The engine path with NO import capacity declared: candidates SOLVED and PRICED, none viable,
    but the solver had no connection limit to charge through, so the £0 floors are mechanical. The
    verdict must be the data ask, not `NO_VIABLE_CONFIGURATION`.

    Corrected 2026-08-12: this used to be asserted for `MIC == the metered peak`, which is an
    ECONOMIC outcome (the dispatch charged and priced) and now keeps the economics verdict with the
    placeholder-MIC signature attached as a data ask — see `test_engine_connection_basis.py`."""
    from gridq.assumptions import load_assumptions

    site = _site_with(make_site, mic_kw=0.0, peak_kw=420.0)
    assumptions = load_assumptions("gb_v0_1")
    engine = MilpEngine()
    unviable = _evaluation(210.0, 0.0, project_irr_pct=-5.0)
    monkeypatch.setattr(MilpEngine, "_run_search", lambda *a, **k: _run([unviable]))

    result = engine.solve(engine.build_optimisation_request(site, assumptions))

    assert result.selection_status == s.SelectionStatus.INSUFFICIENT_DATA
    assert result.selected_power_kw is None
    joined = " ".join(result.warnings)
    assert "connection capacity unknown" in joined
    assert "no viable configuration" not in joined


def test_n7_capacity_guard_does_not_fire_when_the_connection_is_real(
    make_site, monkeypatch
) -> None:
    """The complement of the test above: with real headroom the FINAL economics verdict stands."""
    from gridq.assumptions import load_assumptions

    site = _site_with(make_site, mic_kw=900.0, peak_kw=420.0)
    assumptions = load_assumptions("gb_v0_1")
    engine = MilpEngine()
    unviable = _evaluation(210.0, 0.0, project_irr_pct=-5.0)
    monkeypatch.setattr(MilpEngine, "_run_search", lambda *a, **k: _run([unviable]))

    result = engine.solve(engine.build_optimisation_request(site, assumptions))

    assert result.selection_status == s.SelectionStatus.NO_VIABLE_CONFIGURATION
    assert any(w.startswith("no viable configuration") for w in result.warnings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
