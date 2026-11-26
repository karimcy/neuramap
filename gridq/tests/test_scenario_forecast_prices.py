"""W4-4 — real Modo forecasts → Low/Central/High → the dispatch price-scenario seam.

Wires a representative Modo CSV (``tests/fixtures/modo_sample_forecast.csv`` — a *format* sample
loaded through the real ingestion path and pinned with a real content hash; **not** real
proprietary data, layout unconfirmed per OPEN_COMMENTS C012) through:
  load + content-hash pin → forecast-backed ``ScenarioSet`` → the per-band price-series loader →
  the dispatch ``DispatchProblem.scenario`` seam, degrading honestly when the forecast is absent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult, SimpleSolver
from gridq.finance.financial_model import capital_recovery_account
from gridq.scenarios import (
    ScenarioName,
    ScenarioSet,
    build_forecast_backed_scenario_set,
    build_forecast_reference,
    price_series_for_set,
    run_scenario_set,
    scenario_price_series,
)
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing

FIXTURE = Path(__file__).parent / "fixtures" / "modo_sample_forecast.csv"


def _financial_case() -> s.FinancialCase:
    return s.FinancialCase(
        case_id="fc_w44",
        p50_cashflows=[-1000.0, 200.0, 200.0, 200.0],
        p90_cashflows=[-1000.0, 170.0, 170.0, 170.0],
        dscr_summary=s.DscrSummary(),
        capital_recovery=capital_recovery_account([-1000.0, 200.0, 200.0, 200.0]),
        debt_assumptions=s.DebtAssumptions(),
        price_recommendation=s.PriceRecommendation(),
        covenant_status=s.CovenantStatus(),
    )


def _forecast_backed_set() -> ScenarioSet:
    return build_forecast_backed_scenario_set(
        load_assumptions("gb_v0_1"),
        forecast_file=FIXTURE,
        source="Modo Energy",
        forecast_version="modo-2026-q2-sample",
    )


class _RecordingSolver:
    """A DispatchSolver spy: records the problem it is handed, then delegates to SimpleSolver."""

    def __init__(self) -> None:
        self.seen: list[DispatchProblem] = []

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        self.seen.append(problem)
        return SimpleSolver().solve(problem)


def test_real_modo_csv_loads_and_pins_each_band() -> None:
    scn = _forecast_backed_set()
    assert {sc.scenario_id for sc in scn.scenarios} == {"low", "central", "high"}
    for sc in scn.scenarios:
        ref = sc.forecast_ref
        assert ref is not None
        assert ref.source == "Modo Energy"
        assert ref.content_hash.startswith("sha256:")  # real, recomputed-from-curve hash
        assert ref.file_sha256 is not None and ref.file_sha256.startswith("sha256:")
        assert ref.verified is True  # loaded from a real file
        assert ref.price_unit == "GBP/MWh"
        # multipliers are NOT derived from the forecast — they stay explicit (here: identity)
        assert sc.price_multiplier == 1.0


def test_forecast_backed_set_is_deterministic() -> None:
    a = _forecast_backed_set().model_dump(mode="json")
    b = _forecast_backed_set().model_dump(mode="json")
    assert a == b
    h1 = _forecast_backed_set().scenarios[0].forecast_ref
    h2 = _forecast_backed_set().scenarios[0].forecast_ref
    assert h1 is not None and h2 is not None
    assert h1.content_hash == h2.content_hash  # same file → same content hash


def test_forecast_backed_scenario_reaches_dispatch_seam(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    scn = _forecast_backed_set()
    central = next(sc for sc in scn.scenarios if sc.name == ScenarioName.CENTRAL)
    assert central.forecast_ref is not None and central.forecast_ref.verified

    solver = _RecordingSolver()
    run = enumerate_sizing(
        make_site(),
        lib,
        SearchSpace(power_kw_candidates=[100.0], duration_hour_candidates=[2.0]),
        dispatch_solver=solver,
        objective=SizingObjective(min_p90_dscr_covenant=1.4),
        scenario=central,
    )
    assert solver.seen, "dispatch solver was never called"
    problem = solver.seen[0]
    # the forecast-backed scenario (with its hash-pinned, verified ref) reaches the dispatch seam
    assert problem.scenario is central
    assert problem.scenario.forecast_ref.verified is True
    # the placeholder dispatch still prices nothing → the run is never bankable
    assert run.bankable is False


def test_scenario_price_series_resolves_each_band() -> None:
    ref = _forecast_backed_set().scenarios[0].forecast_ref
    assert ref is not None
    low = scenario_price_series(ref, scenario_name=ScenarioName.LOW, file_path=FIXTURE)
    central = scenario_price_series(ref, scenario_name=ScenarioName.CENTRAL, file_path=FIXTURE)
    high = scenario_price_series(ref, scenario_name=ScenarioName.HIGH, file_path=FIXTURE)

    assert [p.price_gbp_per_mwh for p in low.points] == [-5.0, 10.0, 15.0, 20.0]
    assert [p.price_gbp_per_mwh for p in central.points] == [60.0, 72.0, 80.0, 85.0]
    assert [p.price_gbp_per_mwh for p in high.points] == [120.0, 130.0, 140.0, 150.0]
    # canonical timestamp order
    assert [p.timestamp for p in high.points] == sorted(p.timestamp for p in high.points)
    assert low.verified and central.verified and high.verified
    assert low.priced_point_count == 4


def test_price_series_for_set_covers_all_bands() -> None:
    scn = _forecast_backed_set()
    series = price_series_for_set(scn, file_path=FIXTURE)
    assert set(series) == {ScenarioName.LOW, ScenarioName.CENTRAL, ScenarioName.HIGH}
    assert all(v.verified for v in series.values())
    assert series[ScenarioName.HIGH].points[-1].price_gbp_per_mwh == 150.0


def test_absent_band_yields_empty_series_not_fabricated(tmp_path: Path) -> None:
    p = tmp_path / "central_only.csv"
    p.write_text(
        "timestamp,price_gbp_per_mwh,scenario\n"
        "2026-01-01T00:00:00,70.0,central\n"
        "2026-01-01T00:30:00,75.0,central\n",
        encoding="utf-8",
    )
    ref = build_forecast_reference(
        file_path=p,
        source="Modo Energy",
        forecast_version="v1",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 1),
        created_at=datetime(2026, 4, 1),
    )
    low = scenario_price_series(ref, scenario_name=ScenarioName.LOW, file_path=p)
    assert low.points == []
    assert low.verified is False
    assert any("not fabricated" in w.lower() for w in low.warnings)
    # the band that IS present still resolves
    central = scenario_price_series(ref, scenario_name=ScenarioName.CENTRAL, file_path=p)
    assert [pt.price_gbp_per_mwh for pt in central.points] == [70.0, 75.0]


def test_drifted_file_is_caught_and_not_served(tmp_path: Path) -> None:
    p = tmp_path / "modo.csv"
    p.write_text(
        "timestamp,price_gbp_per_mwh,scenario\n2026-01-01T00:00:00,70.0,central\n",
        encoding="utf-8",
    )
    ref = build_forecast_reference(
        file_path=p,
        source="Modo Energy",
        forecast_version="v1",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 1),
        created_at=datetime(2026, 4, 1),
    )
    # overwrite with a different curve → the content hash no longer matches the pin
    p.write_text(
        "timestamp,price_gbp_per_mwh,scenario\n2026-01-01T00:00:00,999.0,central\n",
        encoding="utf-8",
    )
    series = scenario_price_series(ref, scenario_name=ScenarioName.CENTRAL, file_path=p)
    assert series.points == []
    assert series.verified is False
    assert any("re-pin" in w for w in series.warnings)


def test_missing_forecast_degrades_to_ranked_data_ask() -> None:
    scn = build_forecast_backed_scenario_set(load_assumptions("gb_v0_1"), forecast_file=None)
    assert all(sc.forecast_ref is None for sc in scn.scenarios)
    assert any("ranked data ask" in w for w in scn.validation_warnings)
    # nothing to resolve, nothing fabricated
    assert price_series_for_set(scn) == {}


def test_unreadable_forecast_path_degrades_without_raise(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.csv"
    scn = build_forecast_backed_scenario_set(load_assumptions("gb_v0_1"), forecast_file=missing)
    assert all(sc.forecast_ref is None for sc in scn.scenarios)
    assert any(
        ("could not be loaded" in w) or ("ranked data ask" in w) for w in scn.validation_warnings
    )


def test_forecast_backed_run_stays_not_bankable_on_placeholder_weights() -> None:
    scn = _forecast_backed_set()
    # forecast is verified, but the weights assumption is a placeholder → still NOT bankable
    assert scn.bankable_status == s.BankableStatus.NOT_BANKABLE
    out = run_scenario_set(_financial_case(), scn)
    assert out.bankable_status == s.BankableStatus.NOT_BANKABLE
    # a VERIFIED forecast_ref must not trip the 'unverified' downgrade warning
    assert all("unverified" not in w for r in out.results for w in r.warnings)
