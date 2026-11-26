"""The no-selection verdict must state what the search MEASURED, never an unmeasured either/or.

**The defect (2026-08-15 campaign, eight client packs).** Every one of the eight runs persisted
``sizing_grid.selection_reason`` = *"no bankable candidate: no size meets the P90 DSCR covenant
(DSCR is unavailable without injected debt terms and a real dispatch, or every candidate fails the
covenant)."* On Tanhouse that sentence sat on a 162-point grid in which **zero** points carried a
``min_p90_dscr`` and **zero** carried a ``passes_covenant``. No size had been tested against the
covenant at all, because no debt structure was ever resolved — so the half of the sentence a reader
acts on ("no size meets the covenant") described an event that did not happen.

The two halves of that hedge are opposite verdicts:

* **no DSCR anywhere** is a financing-DATA absence. `docs/validation_requirements.md` §3 and
  CLAUDE.md §2 both require it be underwritten on an **unlevered** basis with the confidence band
  adjusted — explicitly *never* a refusal, and never "no viable configuration".
* **DSCRs measured and rejected** is an evidenced covenant failure, and a legitimate finding.

The evaluations always carry which one it was (``min_p90_dscr``/``passes_covenant`` are ``None``
when the gate never ran, populated when it did), so the hedge was never necessary.

**The same shape on returns.** `finance.returns.project_irr` *refuses* the metric when the series
never returns capital or is non-conventional, and the grid stores only the resulting null. So a
null ``project_irr_pct`` means either "measured, and negative" or "never computed" — and the
campaign's verdict prose ("no candidate produces a positive project IRR") asserted the first while
the record held the second. A claim about returns may only be made when a return was computed.

**Why these tests drive `enumerate_sizing` and not `_select`.** `_select` is the single choke point
BOTH production search paths funnel through — the legacy enumeration (`sizer.enumerate_sizing`,
reached from `MilpEngine._run_search`) and the two-stage search
(`optimisation.two_stage.two_stage_sizing`, which imports the very same function and calls it at
`two_stage.py:743`). Testing `_select` directly would leave the wiring untested, which is the
built-tested-never-called pattern `validation_requirements.md` §3 records seven instances of. These
tests therefore run the real sizing loop against the real `run_financial_model`, and assert on the
**persisted `SizingGrid`** the pipeline writes — so deleting the wiring, or reverting `_select`,
fails them.
"""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.finance.debt import DebtTerms
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing

COVENANT = 1.3

#: The exact claim the campaign published on evidence it did not hold. No verdict may contain it
#: unless a DSCR was actually measured for at least one candidate.
_UNMEASURED_COVENANT_CLAIM = "no size meets the P90 DSCR covenant"


def _objective() -> SizingObjective:
    return SizingObjective(min_p90_dscr_covenant=COVENANT)


class _RevenueSolver:
    """Synthetic solver emitting a constant FIRM revenue regardless of size (fixture only).

    ``annual_revenue_gbp=0.0`` reproduces the campaign condition exactly: the dispatch solves, the
    candidate prices, and every operating year is negative — so finance refuses the IRR rather than
    returning a negative one.
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


def _run(site: s.SiteModel, *, revenue_gbp: float, debt: DebtTerms | None, power_kw: float):
    """Drive the real sizing loop + real finance model, and return the PERSISTED grid."""
    return enumerate_sizing(
        site,
        load_assumptions("gb_v0_1"),
        SearchSpace(power_kw_candidates=[power_kw], duration_hour_candidates=[2.0]),
        dispatch_solver=_RevenueSolver(revenue_gbp),
        objective=_objective(),
        debt_terms=debt,
    ).sizing_grid()


# --------------------------------------------------------- the campaign condition


def test_verdict_never_claims_a_covenant_failure_that_was_never_measured(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The exact 2026-08-15 state: priced candidates, no debt structure, so no DSCR anywhere.

    This is the regression that blocked eight packs. The grid must not assert the covenant
    rejected anything, because nothing was tested.
    """
    grid = _run(make_site(), revenue_gbp=0.0, debt=None, power_kw=100.0)

    # The precondition — the campaign's own signature. If this stops holding the test is no
    # longer exercising the defect, so it is asserted rather than assumed.
    assert grid.selected is None
    assert all(p.min_p90_dscr is None for p in grid.points)
    assert all(p.passes_covenant is None for p in grid.points)
    assert all(p.priceable for p in grid.points), (
        "candidates must PRICE — otherwise this is a data refusal, not the campaign condition"
    )

    reason = grid.selection_reason
    assert _UNMEASURED_COVENANT_CLAIM not in reason, (
        "the verdict claims sizes failed a covenant that was never applied to any of them"
    )
    assert "COVENANT GATE DID NOT RUN" in reason
    assert "NOTHING HERE IS EVIDENCE THAT ANY SIZE FAILED THE COVENANT" in reason
    # validation_requirements.md §3: a financing-data absence underwrites UNLEVERED, never refuses.
    assert "UNLEVERED" in reason


def test_verdict_never_implies_a_return_was_compared_against_zero_when_none_was_computed(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A null IRR means "not computed" here, not "computed and non-positive". The record says so."""
    grid = _run(make_site(), revenue_gbp=0.0, debt=None, power_kw=100.0)

    assert all(p.project_irr_pct is None for p in grid.points)
    assert all(p.levered_return_pct is None for p in grid.points)

    reason = grid.selection_reason
    assert "NO RETURN METRIC WAS COMPUTED" in reason
    assert "never compared against zero" in reason or "was ever compared against zero" in reason
    # The structural cause, read off the cashflows: undefined, not negative.
    assert "UNDEFINED" in reason
    # A £0 floor at every size is a statement about the PRICE INPUTS first (CLAUDE.md §2).
    assert "ANNUAL REVENUE OF £0.00" in reason


# --------------------------------------------------------- the evidenced branch


def test_a_measured_covenant_failure_is_still_reported_as_one(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The fix must not blunt a real finding: with debt injected the gate runs and says so.

    Guards the obvious wrong fix — deleting the covenant sentence — and pins that the two branches
    are genuinely discriminated rather than one message replacing the other.
    """
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    grid = _run(make_site(), revenue_gbp=1_000.0, debt=debt, power_kw=4_000.0)

    assert grid.selected is None
    measured = [p.min_p90_dscr for p in grid.points if p.min_p90_dscr is not None]
    assert measured, "precondition: injected debt must produce a real DSCR"

    reason = grid.selection_reason
    assert "covenant gate RAN on" in reason
    assert "rejected" in reason
    assert f"{COVENANT:g} covenant" in reason
    # The measured number is ON the record, so the claim is checkable against the grid.
    assert f"{max(measured):.4g}" in reason
    # ...and the unmeasured-claim guard does not fire here: nothing was over-claimed.
    assert "COVENANT GATE DID NOT RUN" not in reason


def test_a_computed_return_is_reported_as_computed(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """When an IRR exists the verdict reports the measured comparison, not the absence branch."""
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    grid = _run(make_site(), revenue_gbp=1_000.0, debt=debt, power_kw=4_000.0)

    reason = grid.selection_reason
    if any(p.project_irr_pct is not None for p in grid.points):
        assert "return metrics WERE computed" in reason
        assert "NO RETURN METRIC WAS COMPUTED" not in reason
    else:
        assert "NO RETURN METRIC WAS COMPUTED" in reason


# --------------------------------------------------------- reachability of the fix


def test_both_production_search_paths_share_one_selection_choke_point() -> None:
    """The two-stage search must keep using `sizer._select`, not a fork of it.

    The campaign ran on the two-stage path (`m4-two-stage-v2`), so a fix applied only to the legacy
    enumeration would be inert on exactly the runs that shipped. Identity — not similarity — is
    asserted, because a forked copy would drift silently and this is precisely how the seven
    built-tested-never-called defects survived their own passing tests.
    """
    from gridq.optimisation import two_stage
    from gridq.sizing import sizer

    assert two_stage._select is sizer._select


def test_the_reason_is_carried_onto_the_persisted_grid_verbatim(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """`SizingRun.selection_reason` must reach `SizingGrid.selection_reason` unedited.

    The pack quotes the GRID's field verbatim (`quotes/disclosures.py`), so a fix that changed only
    the run object and not the projection would never be read by a client.
    """
    run = enumerate_sizing(
        make_site(),
        load_assumptions("gb_v0_1"),
        SearchSpace(power_kw_candidates=[100.0], duration_hour_candidates=[2.0]),
        dispatch_solver=_RevenueSolver(0.0),
        objective=_objective(),
        debt_terms=None,
    )
    assert run.sizing_grid().selection_reason == run.selection_reason
    # ...and the determinism projection carries it too, so a golden test notices it changing.
    assert run.fingerprint()["selection_reason"] == run.selection_reason


def test_the_verdict_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    """Identical inputs → identical verdict prose (no counts drifting, no set iteration order)."""
    a = _run(make_site(), revenue_gbp=0.0, debt=None, power_kw=100.0)
    b = _run(make_site(), revenue_gbp=0.0, debt=None, power_kw=100.0)
    assert a.selection_reason == b.selection_reason
