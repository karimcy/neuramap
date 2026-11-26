"""D61: every re-solve of the chosen spec reserves what the sizing loop reserved for it.

**Why this file exists.** The sizing loop's `_evaluate_spec` now reserves each candidate's OWN
end-of-tenor capability for a local-flex award (D61, 2026-11-22). Seven other call sites build their
own `DispatchProblem` for the chosen (or best) spec: five in `MilpEngine`, two in the demo engine.
If any of them kept the candidate-less default (the award clamped to the search floor), the chosen
spec's re-solve would reserve a different volume from the evaluation that chose it, and the record
would disagree with itself.

**How.** `MilpDispatchSolver.solve` (and `solve_with_export`, the export overlay's second solve) are
wrapped with a recorder that notes ``(problem.power_kw, award committed kW)`` and then delegates,
so every solve is real. One case per call site calls the method directly. A single `generate_quote`
run cannot reach all seven: the study-basis netting check and the export upside are mutually
exclusive by construction (a study basis refuses a market forecast), and the no-spec evidence runs
only when nothing is selected. Each case dies if its own ``candidate_power_kw=`` is deleted.

The site is a 7-day January synthetic series carrying a SYNTHETIC attested award (200 kW
declared, tenor 1 yr, 17:00-19:00 in Dec/Jan/Feb; `tests._milp_support.synthetic_local_flex_award`)
in the SSEN SEPD licence area, on gb_v1_12; the chosen spec is 100 kW / 2 h, so every recorded
reservation must be 100 x 0.995 x 0.975 = 97.0125 kW, where the candidate-less default on this
site would be the search floor's. (It used to copy Harwell's own award out of a quarantined
customer file; WP-GRC-05 forbids that read outside the legacy validation waiver.)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import time

import pandas as pd
import pytest
from tests._milp_support import ssen_awarded_site, write_hh_csv
from tests.test_study_basis_schema import study

import gridq.schemas as s
from gridq.app.demo import DemoEngine
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch import dispatch_constraints_for
from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
)
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.quotes import generate_quote
from gridq.schemas import ConsentState, ConsentStatus, ProvisioningRequirement
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation
from gridq.value_stack.registry import default_registry

LIB = load_assumptions("gb_v1_12")
POWER_KW = 100.0
DURATION_H = 2.0


def _expected_reserved_kw(declared_kw: float, power_kw: float, tenor: float) -> float:
    """reserved kW = min(declared kW, P x (1 - standby_kw_per_mw/1000) x (1 - fade%/100)^tenor)."""
    standby = float(LIB.get("parasitic_load", "standby_kw_per_mw").value)
    fade = float(LIB.get("degradation", "annual_capacity_fade_pct").value)
    return min(declared_kw, power_kw * (1.0 - standby / 1000.0) * (1.0 - fade / 100.0) ** tenor)


def _award_kw(constraints: list) -> float | None:
    awards = [d for d in constraints if d.constraint_id.startswith("local_flex_award_")]
    if not awards:
        return None
    assert len(awards) == 1
    return float(awards[0].parameters.committed_power_kw)


@pytest.fixture(scope="module")
def series(tmp_path_factory: pytest.TempPathFactory) -> tuple[s.SiteModel, pd.DatetimeIndex]:
    path = tmp_path_factory.mktemp("d61") / "january.csv"
    rows, ts = write_hh_csv(path, days=7)
    return ssen_awarded_site(path, rows=rows), ts


def _export_site(site: s.SiteModel) -> s.SiteModel:
    return site.model_copy(
        update={
            "consents": ConsentState(
                g98=ConsentStatus.APPROVED,
                g99=ConsentStatus.APPROVED,
                g100=ConsentStatus.APPROVED,
                export_mpan="1900000000000",
            )
        }
    )


def _forecast(ts: pd.DatetimeIndex) -> MarketForecast:
    points = [MarketForecastPoint(timestamp=t.to_pydatetime(), price_gbp_per_mwh=500.0) for t in ts]
    fc = MarketForecast(points=points, source="test-wholesale", forecast_version="v-test")
    fc.content_hash = hash_market_forecast(fc)
    return fc


def _chosen() -> CandidateEvaluation:
    candidate = SizingCandidate(power_kw=POWER_KW, duration_hours=DURATION_H)
    return CandidateEvaluation(
        candidate=candidate,
        optimisation_result=s.OptimisationResult(
            result_id="res_d61",
            request_id="req_d61",
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
        covenant=1.3,
        passes_covenant=None,
        levered_return_pct=None,
        project_irr_pct=None,
        financial=None,
    )


def _study_netting(site, ts, lib: AssumptionLibrary) -> None:
    engine = MilpEngine(use_two_stage_search=False, study_basis=study())
    engine._study_netting_check_note(site, lib, _chosen(), [])


def _engine_dispatch_energy(site, ts, lib: AssumptionLibrary) -> None:
    MilpEngine()._dispatch_energy_for(site, lib, _chosen(), [])


def _engine_provisioning(site, ts, lib: AssumptionLibrary) -> None:
    requirement = ProvisioningRequirement(
        requirement_id="hgv-1",
        windows=[
            s.AvailabilityWindow(
                days_of_week=[0, 1, 2, 3, 4], start_time=time(12), end_time=time(13)
            )
        ],
        additional_load_kw=20.0,
        tenor_years=10.0,
    )
    obligated = site.model_copy(update={"provisioning_requirements": [requirement]})
    MilpEngine()._provisioning_opportunity_cost(obligated, lib, _chosen(), [])


def _engine_export(site, ts, lib: AssumptionLibrary) -> None:
    MilpEngine(market_forecast=_forecast(ts))._export_upside(_export_site(site), lib, _chosen(), [])


def _engine_no_spec(site, ts, lib: AssumptionLibrary) -> None:
    MilpEngine()._no_spec_dispatch_evidence(site, lib, [_chosen()], [])


def _demo_dispatch_energy(site, ts, lib: AssumptionLibrary) -> None:
    DemoEngine(market_forecast=None)._dispatch_energy_for(site, lib, _chosen(), [])


def _demo_export(site, ts, lib: AssumptionLibrary) -> None:
    DemoEngine(market_forecast=_forecast(ts))._export_upside(_export_site(site), lib, _chosen(), [])


CASES: dict[str, Callable[..., None]] = {
    "engine._study_netting_check_note": _study_netting,
    "engine._dispatch_energy_for": _engine_dispatch_energy,
    "engine._provisioning_opportunity_cost": _engine_provisioning,
    "engine._export_upside": _engine_export,
    "engine._no_spec_dispatch_evidence": _engine_no_spec,
    "demo._dispatch_energy_for": _demo_dispatch_energy,
    "demo._export_upside": _demo_export,
}


@pytest.mark.parametrize("case", list(CASES))
def test_chosen_spec_resolves_reserve_what_sizing_reserved(
    case: str, series, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5 (D61): the re-solve reserves the chosen spec's own capability, never the floor's."""
    site, ts = series
    recorded: list[tuple[float, float | None]] = []
    real_solve = MilpDispatchSolver.solve
    real_export = MilpDispatchSolver.solve_with_export

    def _solve(self, problem, *args, **kwargs):
        recorded.append((float(problem.power_kw), _award_kw(problem.constraints)))
        return real_solve(self, problem, *args, **kwargs)

    def _solve_with_export(self, problem, *args, **kwargs):
        recorded.append((float(problem.power_kw), _award_kw(problem.constraints)))
        return real_export(self, problem, *args, **kwargs)

    monkeypatch.setattr(MilpDispatchSolver, "solve", _solve)
    monkeypatch.setattr(MilpDispatchSolver, "solve_with_export", _solve_with_export)

    CASES[case](site, ts, LIB)

    assert recorded, f"{case} never reached a dispatch solve, so this case proves nothing"
    assert site.local_flex_awards is not None
    award = site.local_flex_awards[0]
    declared, tenor = float(award.committed_power_kw), float(award.tenor_years or 0.0)
    expected = _expected_reserved_kw(declared, POWER_KW, tenor)
    assert expected == pytest.approx(97.0125, abs=1e-9)
    reg = default_registry()
    floor_kw = _award_kw(dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg))
    assert floor_kw is not None and floor_kw != pytest.approx(expected)
    for power_kw, reserved_kw in recorded:
        assert power_kw == POWER_KW
        assert reserved_kw == pytest.approx(_expected_reserved_kw(declared, power_kw, tenor)), (
            f"{case} re-solved the {power_kw:g} kW spec reserving {reserved_kw} kW; sizing "
            f"reserved {expected:.4f} kW for it (the candidate-less default is {floor_kw:.4f} kW)"
        )


def test_only_the_bill_producing_solve_minimises_the_published_peak(
    series, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P4 (D67): only the bill-producing solve asks for the peak-minimising second pass.

    One `generate_quote` on this module's site, with `MilpDispatchSolver.solve` wrapped to record
    `problem.minimise_published_peak` and whether the solve came from `_dispatch_energy_for` (the
    chosen-spec re-solve whose schedule becomes the customer bill). Every sizing solve must leave
    the flag unset, or each candidate would pay for a second solve (D67). The demo engine's own
    `_dispatch_energy_for` is checked by direct call. Dies if either call site's
    ``minimise_published_peak=True`` is turned off (M-P1 for the demo, M-P5 for the engine).
    """
    site, _ts = series
    recorded: list[tuple[bool, bool]] = []
    inside = {"bill": False}
    real_solve = MilpDispatchSolver.solve
    real_engine = MilpEngine._dispatch_energy_for
    real_demo = DemoEngine._dispatch_energy_for

    def _solve(self, problem, *args, **kwargs):
        recorded.append((inside["bill"], bool(problem.minimise_published_peak)))
        return real_solve(self, problem, *args, **kwargs)

    def _bill_resolve(real):
        def _wrapped(self, *args, **kwargs):
            inside["bill"] = True
            try:
                return real(self, *args, **kwargs)
            finally:
                inside["bill"] = False

        return _wrapped

    monkeypatch.setattr(MilpDispatchSolver, "solve", _solve)
    monkeypatch.setattr(MilpEngine, "_dispatch_energy_for", _bill_resolve(real_engine))
    monkeypatch.setattr(DemoEngine, "_dispatch_energy_for", _bill_resolve(real_demo))

    generate_quote(site, LIB, engine=MilpEngine(use_two_stage_search=False))
    bill = [flag for in_bill, flag in recorded if in_bill]
    sizing = [flag for in_bill, flag in recorded if not in_bill]
    assert bill and sizing, f"the quote did not reach both kinds of solve: {recorded}"
    assert bill == [True] * len(bill), "the engine's bill-producing solve did not ask for the pass"
    assert sizing == [False] * len(sizing), "a sizing (or other) solve asked for the second pass"

    recorded.clear()
    DemoEngine(market_forecast=None)._dispatch_energy_for(site, LIB, _chosen(), [])
    assert recorded and recorded == [(True, True)] * len(recorded), (
        f"the demo's bill-producing solve did not ask for the second pass: {recorded}"
    )
