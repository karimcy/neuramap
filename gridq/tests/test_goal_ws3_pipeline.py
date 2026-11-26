"""WS3 macro pipeline trace (goal spec): T2 + T0 end to end, and the fingerprint identity.

- The sizing-vs-underwriting consistency contract must survive the M4 two-stage rework: the
  candidate the search confirms on the full series must reprice to IDENTICAL load-bearing
  numbers through the standalone `underwrite_spec` path (`financial_fingerprint` equality).
- A real T2-style CLI run persists the stage-labelled two-stage cloud (`m4-two-stage-v2`,
  the D34 default) through the actual `gridq run` pipeline (seams + hooks + persistence,
  not unit calls).
- The committed T0 example degrades honestly through the same pipeline: the legacy fallback
  grid persists with no invented dispatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests._dpa_support import register_fixture_dpas
from typer.testing import CliRunner

from _milp_support import multi_rate_site, write_hh_csv
from gridq.assumptions import load_assumptions
from gridq.cli import app
from gridq.finance.debt import DebtTerms
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.two_stage import two_stage_sizing
from gridq.sizing.objective import SizingObjective
from gridq.sizing.underwrite import financial_fingerprint, underwrite_spec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _fixture_dpas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gridq run` needs a DPA per site (WP-GRC-05): TEST FIXTURE ones for the fictitious sites."""
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_milp", "site_000900")


def _write_year_csv(path) -> int:
    ts = pd.date_range("2025-01-01 00:00", periods=365 * 48, freq="30min")
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    load = np.full(len(ts), 60.0)
    load[(hour >= 8) & (hour < 20)] = 220.0
    load[weekday & (hour >= 16) & (hour < 19)] = 320.0
    month = ts.month.to_numpy()
    load = load * (1.0 + 0.1 * np.cos((month - 1) / 12.0 * 2.0 * np.pi))
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    return len(ts)


def test_two_stage_selection_reprices_identically_through_underwriting(tmp_path):
    """Consistency contract #1 after D32: the confirmed selection cannot drift from underwriting."""
    path = tmp_path / "hh_year.csv"
    rows = _write_year_csv(path)
    site = multi_rate_site(path, rows=rows, max_import_kw=352.0)
    assumptions = load_assumptions("gb_v0_1")
    objective = SizingObjective(min_p90_dscr_covenant=1.2)
    debt = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)

    run = two_stage_sizing(
        site,
        assumptions,
        base_solver=MilpDispatchSolver(),
        objective=objective,
        binding_capacity_kw=352.0,
        debt_terms=debt,
        seed=0,
    )
    assert run.selected is not None and run.selected.stage == "confirm"

    reprice = underwrite_spec(
        site,
        assumptions,
        run.selected.candidate,
        dispatch_solver=MilpDispatchSolver(),
        objective=objective,
        debt_terms=debt,
    )
    assert financial_fingerprint(reprice.evaluation) == financial_fingerprint(run.selected)


def test_t2_style_cli_run_persists_the_two_stage_cloud(tmp_path):
    """A real `gridq run` over a metered series persists the stage-labelled D32 cloud —
    the engine default since D34 (C025 resolved: gb_v1_3 prices the capex energy component,
    so the wide-duration recommendation is a real economic trade-off)."""
    csv_path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(csv_path, days=30)
    site = multi_rate_site(csv_path, rows=rows)
    site_json = tmp_path / "site.json"
    site_json.write_text(site.model_dump_json(), encoding="utf-8")

    out = tmp_path / "out"
    res = runner.invoke(app, ["run", str(site_json), "--out", str(out)])
    assert res.exit_code == 0, res.output

    quote_files = sorted(out.rglob("quote.json"))
    assert quote_files, f"no quote.json under {out}"
    quote = json.loads(quote_files[0].read_text(encoding="utf-8"))
    grid = quote.get("sizing_grid")
    assert grid is not None
    assert grid["search_version"] == "m4-two-stage-v2"
    assert len(grid["points"]) >= 80
    stages = {p["stage"] for p in grid["points"]}
    assert {"coarse", "confirm"} <= stages
    assert all("proxy" in p for p in grid["points"])


def test_opted_in_engine_persists_the_two_stage_cloud(tmp_path):
    """`MilpEngine(use_two_stage_search=True)` produces the stage-labelled D32 cloud."""
    csv_path = tmp_path / "hh.csv"
    rows, _ = write_hh_csv(csv_path, days=30)
    site = multi_rate_site(csv_path, rows=rows)

    engine = MilpEngine(use_two_stage_search=True)
    engine.solve(engine.build_optimisation_request(site, load_assumptions("gb_v0_1")))
    run = engine.last_sizing_run()
    assert run is not None
    grid = run.sizing_grid()
    assert grid.search_version == "m4-two-stage-v2"
    assert len(grid.points) >= 80
    stages = {p.stage for p in grid.points}
    assert {"coarse", "confirm"} <= stages
    # Every persisted point carries its provenance; the record never hides the proxy split.
    assert all(p.proxy in (True, False) for p in grid.points)


def test_t0_cli_run_degrades_honestly_with_the_legacy_grid(tmp_path):
    """The committed T0 example runs the same pipeline and degrades without invention."""
    out = tmp_path / "out"
    res = runner.invoke(app, ["run", str(EXAMPLES / "site_t0.json"), "--out", str(out)])
    assert res.exit_code == 0, res.output

    quote_files = sorted(out.rglob("quote.json"))
    assert quote_files, f"no quote.json under {out}"
    quote = json.loads(quote_files[0].read_text(encoding="utf-8"))
    result = quote["optimisation_result"]
    # No metered series and no fabricated one: no spec, insufficient data, never bankable.
    assert result["selected_power_kw"] is None
    assert result["solver_status"] in ("insufficient_data", "infeasible")
    assert result["bankable_status"] == "not_bankable"
    grid = quote.get("sizing_grid")
    if grid is not None:  # engine exposes the honestly-unsolved legacy grid
        assert grid["search_version"] == "enumerate-v1"
        assert all(not p["proxy"] for p in grid["points"])
