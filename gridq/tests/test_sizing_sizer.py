"""Tests for the sizing outer loop (enumerate_sizing) against the dispatch + finance contracts."""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from gridq.assumptions import (
    SECTION_NAMES,
    AssumptionLibrary,
    AssumptionStatus,
    load_assumptions,
)
from gridq.dispatch import DispatchProblem, DispatchResult, SimpleSolver
from gridq.finance.debt import DebtTerms
from gridq.sizing import (
    SearchSpace,
    SizingCandidate,
    SizingObjective,
    enumerate_sizing,
    underwrite_spec,
)
from gridq.sizing.objective import Evaluation
from gridq.sizing.underwrite import financial_fingerprint

COVENANT = 1.3


def _objective() -> SizingObjective:
    return SizingObjective(min_p90_dscr_covenant=COVENANT)


def _space() -> SearchSpace:
    return SearchSpace(power_kw_candidates=[50.0, 100.0], duration_hour_candidates=[1.0, 2.0])


def _confirmed_lib() -> AssumptionLibrary:
    """A deep copy of gb_v0_1 with every assumption flipped to ``confirmed`` — TEST ONLY.

    Mirrors the finance-test helper of the same name: used to prove the sizing bankability gate
    *releases* when nothing is a placeholder, so the gate is real logic, not a constant False.
    Never represents a real, confirmed library.
    """
    lib = load_assumptions("gb_v0_1")
    for section in SECTION_NAMES:
        for a in getattr(lib, section).values():
            a.status = AssumptionStatus.CONFIRMED
    return lib


# --- test-only dispatch doubles (never used in production) ------------------


class _FixedRevenueSolver:
    """Synthetic solver emitting a constant FIRM revenue regardless of size (test fixture only).

    Fixed revenue + capex/debt that scale with power means DSCR strictly decreases as power grows,
    so a wide power grid yields a clean pass/fail split — proving the covenant gate discriminates.
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


class _BankableRevenueSolver:
    """Like `_FixedRevenueSolver` but emits a PRODUCTION / BANKABLE_CANDIDATE result (TEST ONLY).

    Used only to prove the sizing bankability gate *releases* (the analogue of the finance test's
    bankable-candidate result), never a spoof real engine: it runs no dispatch and is never used in
    production. Paired with `_confirmed_lib()` + injected `DebtTerms` it is the one configuration in
    which `SizingRun.bankable` can be True.
    """

    def __init__(self, annual_revenue_gbp: float) -> None:
        self._rev = annual_revenue_gbp

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        return DispatchResult(
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.PRODUCTION,
            bankable_status=s.BankableStatus.BANKABLE_CANDIDATE,
            solver_status=s.SolverStatus.OPTIMAL,
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


class _UnsizedSolver:
    """Synthetic solver returning no positive sizing (insufficient data) — exercises the catch."""

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        return DispatchResult(
            power_kw=None,
            energy_kwh=None,
            duration_hours=None,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.INSUFFICIENT_DATA,
        )


# --- placeholder behaviour --------------------------------------------------


def test_placeholder_run_is_not_bankable_with_no_selection(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    run = enumerate_sizing(
        make_site(), lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    assert run.search_space_size == 4
    assert run.bankable is False
    assert run.selected is None  # DSCR unavailable (no debt) → nothing feasible
    assert run.rejected_by_covenant == []  # nothing was even evaluated against the covenant
    assert any("not bankable" in w.lower() for w in run.warnings)
    # every candidate is priced (placeholder gives positive sizing) but values are 0
    assert all(e.priceable for e in run.candidates_evaluated)
    assert all(e.project_irr_pct is None for e in run.candidates_evaluated)


def test_run_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()
    a = enumerate_sizing(
        site, lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    b = enumerate_sizing(
        site, lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    assert a.fingerprint() == b.fingerprint()


def test_empty_search_space_is_clean_not_bankable_run(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    empty = SearchSpace(power_kw_candidates=[], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(), lib, empty, dispatch_solver=SimpleSolver(), objective=_objective()
    )
    assert run.search_space_size == 0
    assert run.candidates_evaluated == []
    assert run.selected is None
    assert run.bankable is False


def test_unpriceable_candidate_is_caught_not_raised(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    run = enumerate_sizing(
        make_site(), lib, _space(), dispatch_solver=_UnsizedSolver(), objective=_objective()
    )
    # finance raises InsufficientInputsError per candidate; the sizer records, does not abort
    assert len(run.candidates_evaluated) == 4
    assert all(not e.priceable for e in run.candidates_evaluated)
    assert all(e.financial is None for e in run.candidates_evaluated)
    assert run.selected is None
    assert run.bankable is False


# --- progress side channel (C035) ------------------------------------------


def test_progress_sink_ticks_once_per_candidate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    ticks: list[tuple[str, int | None, int | None]] = []
    run = enumerate_sizing(
        make_site(),
        lib,
        _space(),
        dispatch_solver=SimpleSolver(),
        objective=_objective(),
        progress=lambda phase, done, total: ticks.append((phase, done, total)),
    )
    n = run.search_space_size
    assert n == 4
    # one "sizing" tick per candidate, 1..N (1-based), with the total pinned to N throughout
    assert [t[0] for t in ticks] == ["sizing"] * n
    assert [t[1] for t in ticks] == list(range(1, n + 1))
    assert all(t[2] == n for t in ticks)


def test_progress_sink_never_changes_the_result(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()

    def _boom(phase: str, done: int | None, total: int | None) -> None:
        raise RuntimeError("sink blew up")

    baseline = enumerate_sizing(
        site, lib, _space(), dispatch_solver=SimpleSolver(), objective=_objective()
    )
    recording = enumerate_sizing(
        site,
        lib,
        _space(),
        dispatch_solver=SimpleSolver(),
        objective=_objective(),
        progress=lambda *a: None,
    )
    raising = enumerate_sizing(
        site,
        lib,
        _space(),
        dispatch_solver=SimpleSolver(),
        objective=_objective(),
        progress=_boom,
    )
    # With-sink (recording or raising) selects the identical candidate + evaluation count as
    # without-sink: the side channel is byte-neutral and a raising sink is swallowed.
    assert recording.fingerprint() == baseline.fingerprint()
    assert raising.fingerprint() == baseline.fingerprint()
    assert (
        len(recording.candidates_evaluated)
        == len(raising.candidates_evaluated)
        == len(baseline.candidates_evaluated)
    )


# --- the covenant gate genuinely fires (not just decorates) -----------------


def test_covenant_gate_rejects_on_zero_revenue_with_debt(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Placeholder (zero revenue) + injected debt → negative DSCR → every candidate rejected."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    run = enumerate_sizing(
        make_site(),
        lib,
        _space(),
        dispatch_solver=SimpleSolver(),
        objective=_objective(),
        debt_terms=debt,
    )
    assert len(run.rejected_by_covenant) == 4  # all fail the covenant
    assert all(e.passes_covenant is False for e in run.candidates_evaluated)
    assert run.selected is None
    assert run.bankable is False


def test_covenant_gate_admits_and_rejects_in_one_run(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A fixed-revenue double + debt over a wide power grid → some pass, some fail (gate fires)."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    # power spans tiny→large; fixed revenue means small systems clear the covenant, large ones don't
    space = SearchSpace(power_kw_candidates=[25.0, 4000.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=120_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    passed = [e for e in run.candidates_evaluated if e.passes_covenant is True]
    failed = [e for e in run.candidates_evaluated if e.passes_covenant is False]
    assert passed, "expected at least one candidate to clear the P90 DSCR covenant"
    assert failed, "expected at least one candidate to be rejected by the P90 DSCR covenant"
    assert run.rejected_by_covenant  # the gate fired
    # A feasible candidate is selected with a real levered (equity) return, but the run stays NOT
    # bankable because gb_v0_1 assumptions are placeholders (and the dispatch is not production).
    assert run.selected is not None
    assert run.selected.passes_covenant is True
    assert run.selected.levered_return_pct is not None  # equity IRR is now modelled
    assert run.bankable is False


# --- the optimiser maximises levered return s.t. the covenant (contract #2) -


def test_real_site_returns_bankable_by_construction_spec(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The selected spec is covenant-satisfying *by construction* and maximises the levered (equity)
    return among the covenant-passers — i.e. a spec underwriting's covenant would accept (System
    Brief contract #2). On gb_v0_1 placeholders the run stays honestly NOT bankable, however good
    the equity return looks."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    space = SearchSpace(power_kw_candidates=[25.0, 50.0, 100.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=150_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    assert run.selected is not None
    assert run.selected.passes_covenant is True
    assert run.selected.levered_return_pct is not None  # equity IRR is real when debt is injected
    # The selected candidate is the covenant-passer with the greatest levered (equity) return.
    passers = [
        e
        for e in run.candidates_evaluated
        if e.passes_covenant is True and e.levered_return_pct is not None
    ]
    assert passers
    assert run.selected.levered_return_pct == max(
        p.levered_return_pct for p in passers if p.levered_return_pct is not None
    )
    # Honest: placeholder assumptions ⇒ never bankable, no matter how good the return looks.
    assert run.bankable is False


def test_bankable_gate_releases_on_confirmed_lib(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The one configuration where `SizingRun.bankable` is True — confirmed assumptions + a
    bankable-candidate dispatch + injected debt that clears the covenant — proving the bankable gate
    is real logic, not a constant False. TEST ONLY (mirrors the finance gate-release test); never a
    spoof real engine."""
    lib = _confirmed_lib()
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    space = SearchSpace(power_kw_candidates=[25.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_BankableRevenueSolver(annual_revenue_gbp=150_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    assert run.selected is not None
    assert run.selected.passes_covenant is True
    assert run.selected.levered_return_pct is not None
    assert run.bankable is True


def test_covenant_violating_spec_is_never_selected(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A spec the P90 DSCR covenant rejects can never be the selected size."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    # Fixed revenue: the tiny system clears the covenant, the huge one cannot (capex scales).
    space = SearchSpace(power_kw_candidates=[25.0, 4000.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(
        make_site(),
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=120_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    big = next(e for e in run.candidates_evaluated if e.candidate.power_kw == 4000.0)
    assert big.passes_covenant is False
    assert big.candidate in run.rejected_by_covenant
    assert run.selected is not None
    assert run.selected.candidate != big.candidate
    assert run.selected.candidate.power_kw == 25.0  # the covenant-passing size


def test_sizing_eval_equals_underwrite_reprice(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Contract #1 ("one engine, called twice"): the chosen spec, re-priced standalone through the
    same engine, is identical to the sizing evaluation — a genuine recomputation (distinct objects,
    equal input hash), not a comparison of an object to itself."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    site = make_site()
    obj = _objective()
    solver = _FixedRevenueSolver(annual_revenue_gbp=150_000.0)
    space = SearchSpace(power_kw_candidates=[25.0, 50.0], duration_hour_candidates=[2.0])
    run = enumerate_sizing(site, lib, space, dispatch_solver=solver, objective=obj, debt_terms=debt)
    assert run.selected is not None

    under = underwrite_spec(
        site, lib, run.selected.candidate, dispatch_solver=solver, objective=obj, debt_terms=debt
    )
    # The standalone re-price reconstructs the call path independently, yet matches every
    # load-bearing number AND the timestamp-free input hash — so the two seams cannot drift.
    assert financial_fingerprint(under.evaluation) == financial_fingerprint(run.selected)
    assert under.evaluation.financial is not None and run.selected.financial is not None
    assert under.evaluation.financial is not run.selected.financial  # genuinely recomputed
    assert (
        under.evaluation.financial.run_context.input_hash
        == run.selected.financial.run_context.input_hash
    )


def test_levered_path_is_deterministic(make_site: Callable[..., s.SiteModel]) -> None:
    """Determinism holds on the now-live levered path (equity IRR + end-of-life in fingerprint)."""
    lib = load_assumptions("gb_v0_1")
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
    site = make_site()
    space = SearchSpace(power_kw_candidates=[25.0, 50.0], duration_hour_candidates=[2.0])
    a = enumerate_sizing(
        site,
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=150_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    b = enumerate_sizing(
        site,
        lib,
        space,
        dispatch_solver=_FixedRevenueSolver(annual_revenue_gbp=150_000.0),
        objective=_objective(),
        debt_terms=debt,
    )
    assert a.fingerprint() == b.fingerprint()
    assert a.selected is not None and a.selected.levered_return_pct is not None


# --- objective threshold (unit) ---------------------------------------------


class _StubEval:
    def __init__(
        self,
        dscr: float | None,
        levered: float | None,
        irr: float | None,
        end_of_life_deliverable: bool | None = None,
    ) -> None:
        self.candidate = SizingCandidate(power_kw=100.0, duration_hours=2.0)
        self.min_p90_dscr = dscr
        self.levered_return_pct = levered
        self.project_irr_pct = irr
        self.end_of_life_deliverable = end_of_life_deliverable


def test_objective_feasibility_threshold() -> None:
    obj = SizingObjective(min_p90_dscr_covenant=1.3)
    assert obj.feasible(_StubEval(1.31, None, 8.0)) is True  # type: ignore[arg-type]
    assert obj.feasible(_StubEval(1.30, None, 8.0)) is True  # type: ignore[arg-type]
    assert obj.feasible(_StubEval(1.29, None, 8.0)) is False  # type: ignore[arg-type]
    assert (
        obj.feasible(_StubEval(None, None, 8.0)) is False
    )  # DSCR unavailable  # type: ignore[arg-type]
    # an unknown covenant cannot prove feasibility
    assert SizingObjective(None).feasible(_StubEval(5.0, None, 8.0)) is False  # type: ignore[arg-type]


def test_feasible_rejects_known_non_deliverable_spec() -> None:
    """End-of-life deliverability is a gate: a known-False verdict rejects an otherwise-passing
    spec; an unassessed (None) verdict is a data-ask that does not block (decision D12)."""
    obj = SizingObjective(min_p90_dscr_covenant=1.3)
    # Comfortably clears the covenant, but is known non-deliverable to end-of-life ⇒ infeasible.
    assert obj.feasible(_StubEval(2.0, 11.0, 8.0, end_of_life_deliverable=False)) is False  # type: ignore[arg-type]
    # Same spec, unassessed deliverability (the v1/placeholder case) ⇒ feasible (data-ask only).
    assert obj.feasible(_StubEval(2.0, 11.0, 8.0, end_of_life_deliverable=None)) is True  # type: ignore[arg-type]
    # Explicitly verified deliverable ⇒ feasible.
    assert obj.feasible(_StubEval(2.0, 11.0, 8.0, end_of_life_deliverable=True)) is True  # type: ignore[arg-type]


def test_objective_primary_metric_available_when_levered_present() -> None:
    obj = SizingObjective(min_p90_dscr_covenant=1.3)
    # No levered return (no debt) ⇒ falls back, primary metric unavailable.
    assert obj.primary_metric_available(_StubEval(2.0, None, 8.0)) is False  # type: ignore[arg-type]
    # Levered (equity) return present ⇒ the primary metric is live.
    assert obj.primary_metric_available(_StubEval(2.0, 11.0, 8.0)) is True  # type: ignore[arg-type]


def test_stub_eval_satisfies_evaluation_protocol() -> None:
    assert isinstance(_StubEval(1.0, None, 2.0), Evaluation)
