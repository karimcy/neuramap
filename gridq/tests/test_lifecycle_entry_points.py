"""WP-UW-17: the lifecycle inputs reach finance through every production entry point.

`generate_quote` (the pipeline), ``gridq run --lifecycle`` (the CLI) and ``POST /api/run`` /
``POST /run/real`` (the app, field ``lifecycle_json``); the COD-delay row through the standard
scenario set; and the lifecycle ledger in the underwriting pack. Every input is SYNTHETIC
(``source: synthetic``) and the engine is a stub, as in `test_dscr_on_every_run.py`: these tests
are about what reaches finance and the record, not about the MILP. The conftest site is fictitious
and its DPA a registered TEST FIXTURE (tests/_dpa_support.py); no customer record is read.
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-uw17-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import register_fixture_dpas  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import gridq.optimisation as optimisation_mod  # noqa: E402
import gridq.quotes as quotes_mod  # noqa: E402
from gridq import schemas as s  # noqa: E402
from gridq.app import create_app, store  # noqa: E402
from gridq.assumptions import load_assumptions  # noqa: E402
from gridq.cli import app as cli_app  # noqa: E402
from gridq.engine import BaselineEngine  # noqa: E402
from gridq.finance import DebtTerms  # noqa: E402
from gridq.outputs import build_view_model, render_underwriting_memo  # noqa: E402
from gridq.quotes import generate_quote  # noqa: E402
from gridq.quotes.sensitivities import NO_COD_DELAY_WARNING  # noqa: E402
from gridq.scenarios.engine import build_standard_scenario_set  # noqa: E402

app_mod = importlib.import_module("gridq.app.app")
REPO = Path(__file__).resolve().parents[1]
_LIB = "gb_v1_12"
_TERMS = DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10)

#: SYNTHETIC lifecycle inputs, as a CLI file or the app's JSON field would carry them.
LIFECYCLE_PAYLOAD: dict[str, Any] = {
    "provenance": {"source": "synthetic", "reference": "SYNTHETIC WP-UW-17 entry-point inputs"},
    "construction": {
        "construction_months": 9,
        "drawdown": [
            {"months_before_cod": 9, "capex_fraction": 0.5},
            {"months_before_cod": 0, "capex_fraction": 0.5},
        ],
        "idc_rate_pct": 8.0,
    },
    "cod_delay_months": 6,
    "decommissioning": {
        "cost_gbp": 40000.0,
        "year": "horizon_end",
        "net_of_salvage": True,
        "price_basis": "nominal_as_entered",
    },
}
LIFECYCLE = s.LifecycleInputs.model_validate(LIFECYCLE_PAYLOAD)


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


class _PricedStubEngine(BaselineEngine):
    """SYNTHETIC engine: BaselineEngine's request path with a fixed positive-revenue result."""

    def solve(self, request: s.OptimisationRequest) -> s.OptimisationResult:
        base = super().solve(request)
        return base.model_copy(
            update={
                "selected_power_kw": 1_000.0,
                "selected_energy_kwh": 2_000.0,
                "duration_hours": 2.0,
                "value_streams": [
                    s.ValueStreamResult(
                        stream=s.MarketStream.TARIFF_ARBITRAGE,
                        annual_revenue_gbp=220_000.0,
                        annual_cost_gbp=12_000.0,
                        firmness=s.Firmness.FIRM,
                    ),
                ],
            }
        )


def _quote(make_site: Any, **kwargs: Any) -> Any:
    return generate_quote(
        make_site(),
        load_assumptions(_LIB),
        engine=_PricedStubEngine(),
        debt_terms=_TERMS,
        **kwargs,
    )


# --- the pipeline ------------------------------------------------------------------------------


def test_generate_quote_carries_the_lifecycle_onto_the_record(make_site: Any) -> None:
    fc = _quote(make_site, lifecycle=LIFECYCLE).quote.financial_case
    assert fc is not None
    assert fc.lifecycle_inputs == LIFECYCLE
    assert fc.lifecycle is not None
    assert fc.lifecycle.construction.construction_months == 9
    assert fc.lifecycle.construction.accumulation_factor > 1.0
    decom = next(f for f in fc.lifecycle.flows if f.flow is s.LifecycleFlow.DECOMMISSIONING)
    assert decom.status is s.LifecycleFlowStatus.MODELLED


def test_the_stated_cod_delay_is_an_applied_zero_weight_row(make_site: Any) -> None:
    run = _quote(make_site, lifecycle=LIFECYCLE)
    rows = run.quote.scenario_sensitivities
    delayed = [r for r in rows if r.scenario_id == "cod_delay"]
    assert len(delayed) == 1
    row = delayed[0]
    central = next(r for r in rows if r.scenario_id == "central")
    assert row.cod_delay_months == 6 and row.label == "COD delay +6m"
    assert row.irr_pct is not None and row.irr_pct < central.irr_pct
    assert row.note is not None and "APPLIED" in row.note
    assert not any(NO_COD_DELAY_WARNING in w for w in run.quote.warnings)


def test_without_a_delay_the_set_says_none_is_assumed(make_site: Any) -> None:
    run = _quote(make_site)
    assert all(r.cod_delay_months == 0 for r in run.quote.scenario_sensitivities)
    assert any(NO_COD_DELAY_WARNING in w for w in run.quote.warnings)


def test_the_standard_set_adds_the_delay_row_at_zero_weight() -> None:
    lib = load_assumptions(_LIB)
    plain = build_standard_scenario_set(lib)
    delayed = build_standard_scenario_set(lib, cod_delay_months=6)
    extra = [x for x in delayed.scenarios if x.scenario_id == "cod_delay"]
    assert len(delayed.scenarios) == len(plain.scenarios) + 1 and len(extra) == 1
    assert extra[0].probability_weight == 0.0 and extra[0].cod_delay_months == 6


def test_lifecycle_inputs_enter_the_quote_identity(make_site: Any) -> None:
    without = _quote(make_site).quote
    with_life = _quote(make_site, lifecycle=LIFECYCLE).quote
    other = _quote(make_site, lifecycle=LIFECYCLE.model_copy(update={"cod_delay_months": 3})).quote
    assert len({without.input_hash, with_life.input_hash, other.input_hash}) == 3


def test_the_ledger_reaches_the_underwriting_pack(make_site: Any) -> None:
    with_life = _quote(make_site, lifecycle=LIFECYCLE)
    pack = render_underwriting_memo(build_view_model(with_life.quote, with_life.site))
    ledger = pack[pack.index("## Lifecycle capital flows") :]
    ledger = ledger[: ledger.index("## Warnings")]
    assert "| decommissioning | modelled |" in ledger
    assert "| terminal_value | excluded_no_input |" in ledger
    assert "Construction: modelled; return basis dated_monthly" in ledger
    assert "LIFECYCLE" in pack and "TERMINAL VALUE EXCLUDED (N14)" in pack
    # Without inputs the pack still lists all three flows, excluded, with their data requests.
    plain = _quote(make_site)
    pack = render_underwriting_memo(build_view_model(plain.quote, plain.site))
    ledger = pack[pack.index("## Lifecycle capital flows") :]
    for flow in ("terminal_value", "decommissioning", "replacement"):
        assert f"| {flow} | excluded_no_input |" in ledger
    assert "CONSTRUCTION PHASE EXCLUDED" in pack


# --- the CLI -----------------------------------------------------------------------------------


def _cli_run(tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    site = make_site()
    site_json = tmp_path / "site.json"
    site_json.write_text(site.model_dump_json(), encoding="utf-8")
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", site.site_id)
    monkeypatch.setattr(
        optimisation_mod, "MilpEngine", lambda market_forecast=None: _PricedStubEngine()
    )
    monkeypatch.chdir(REPO)
    out = tmp_path / "out"
    return (
        CliRunner().invoke(
            cli_app,
            ["run", str(site_json), "--assumptions", _LIB, "--out", str(out), *extra],
            catch_exceptions=False,
        ),
        out,
    )


def test_cli_run_with_lifecycle_records_and_prints_it(
    tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lifecycle.json"
    path.write_text(json.dumps(LIFECYCLE_PAYLOAD), encoding="utf-8")
    result, out = _cli_run(tmp_path, make_site, monkeypatch, "--lifecycle", str(path))
    assert result.exit_code == 0, result.output
    record = json.loads((out / "quote.json").read_text(encoding="utf-8"))
    fc = record["financial_case"]
    assert fc["lifecycle_inputs"]["provenance"]["source"] == "synthetic"
    assert fc["lifecycle"]["construction"]["status"] == "modelled"
    assert "lifecycle inputs" in result.output and "SYNTHETIC" in result.output


def test_cli_refuses_an_invalid_lifecycle_file_before_solving(
    tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "lifecycle.json"
    bad.write_text(json.dumps({"provenance": {"source": "synthetic", "reference": "r"}}), "utf-8")
    result, out = _cli_run(tmp_path, make_site, monkeypatch, "--lifecycle", str(bad))
    assert result.exit_code == 2
    assert "Could not load lifecycle inputs" in result.output
    assert not out.exists(), "no run is written on refused lifecycle inputs"


# --- the app -----------------------------------------------------------------------------------

_T0_SITE = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}

_client = TestClient(create_app())


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_generate_quote(site: Any, assumptions: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            quote=SimpleNamespace(quote_id="quote_stub_uw17", site_id=site.site_id),
            site=site,
            telemetry=None,
        )

    monkeypatch.setattr(quotes_mod, "generate_quote", fake_generate_quote)
    monkeypatch.setattr(store, "save_run", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "_quote_complete", lambda quote: True)
    return captured


def _wait(job_id: str) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(250):
        status = _client.get(f"/api/run/{job_id}").json()
        if status.get("status") in ("done", "error"):
            return status
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {status}")


def test_app_wizard_hands_the_lifecycle_to_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    data = {**_T0_SITE, "lifecycle_json": json.dumps(LIFECYCLE_PAYLOAD)}
    resp = _client.post("/api/run", data=data)
    assert resp.status_code == 200, resp.text[:400]
    assert _wait(resp.json()["job_id"])["status"] == "done"
    assert captured["lifecycle"] == LIFECYCLE


def test_app_wizard_without_lifecycle_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    resp = _client.post("/api/run", data={**_T0_SITE, "lifecycle_json": "  "})
    assert resp.status_code == 200, resp.text[:400]
    assert _wait(resp.json()["job_id"])["status"] == "done"
    assert captured["lifecycle"] is None


def test_app_refuses_invalid_lifecycle_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    resp = _client.post("/api/run", data={**_T0_SITE, "lifecycle_json": "{not json"})
    assert resp.status_code == 400
    assert "lifecycle inputs" in resp.json()["message"]
    assert captured == {}, "no run starts on refused lifecycle inputs"


def test_app_real_form_hands_the_lifecycle_to_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture(monkeypatch)
    data = {**_T0_SITE, "lifecycle_json": json.dumps(LIFECYCLE_PAYLOAD)}
    resp = _client.post("/run/real", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:400]
    assert captured["lifecycle"] == LIFECYCLE


def test_both_forms_offer_the_lifecycle_field_empty() -> None:
    for page in ("/new-quote",):
        html = _client.get(page).text
        assert 'name="lifecycle_json"' in html
        block = html[html.index('name="lifecycle_json"') :]
        assert block[block.index(">") + 1 : block.index("</textarea>")].strip() == ""
