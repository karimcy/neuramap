"""End-to-end tests for `gridq run` — the stamped-pipeline CLI (the smoke-test enabler).

Drives the real command through `typer.testing.CliRunner`: a sized run on the T2 example with a
real (inline-built) half-hourly CSV + Modo-style forecast, and a degraded run on the T0 example
with no extra inputs. Asserts the artifacts are stamped, traceable, and `not_bankable`, and that
the degraded run still exits 0 with ranked data asks. No fixture data is committed — inputs are
built inline in `tmp_path`, mirroring the ingestion tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tests._dpa_support import register_fixture_dpas
from typer.testing import CliRunner

import gridq.schemas as s
from gridq.assumptions import latest_version
from gridq.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
START = datetime(2025, 1, 1, 0, 0)
runner = CliRunner()


@pytest.fixture(autouse=True)
def _fixture_dpas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gridq run` needs a DPA per site (WP-GRC-05): TEST FIXTURE ones for the example sites."""
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_000123", "site_000900")


def _hh_csv(tmp_path: Path, *, n: int = 300) -> Path:
    path = tmp_path / "hh.csv"
    lines = ["timestamp,import_kw,export_kw"]
    for i in range(n):
        ts = (START + timedelta(minutes=30 * i)).isoformat()
        lines.append(f"{ts},{100 + (i % 50)},0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _modo_csv(tmp_path: Path, *, n: int = 48) -> Path:
    path = tmp_path / "modo.csv"
    lines = ["timestamp,price_gbp_per_mwh,source,forecast_version,created_at"]
    for i in range(n):
        ts = (START + timedelta(minutes=30 * i)).isoformat()
        lines.append(f"{ts},{50 + i},Modo Energy,v2026.06,2026-06-01T00:00:00")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_run_sized_with_hh_and_forecast(tmp_path: Path):
    out = tmp_path / "out"
    res = runner.invoke(
        app,
        [
            "run",
            str(EXAMPLES / "site_t2.json"),
            "--hh",
            str(_hh_csv(tmp_path)),
            "--forecast",
            str(_modo_csv(tmp_path)),
            "--contributor",
            "Olly",
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "not_bankable" in res.output
    assert "Modo Energy" in res.output  # forecast pinned as gated-upside provenance

    quote = json.loads((out / "quote.json").read_text(encoding="utf-8"))
    assert quote["input_hash"].startswith("sha256:")
    # F-15: `run` with no `--assumptions` resolves the NEWEST release on disk (cli.py), so this
    # asserts the resolved latest rather than re-pinning a version that goes stale at the next
    # release cut — the exact defect the fix removed (every default run priced against gb_v0_1).
    assert quote["assumption_version"] == latest_version()
    assert quote["code_version"]
    q = s.QuoteRecord.model_validate(quote)
    assert q.optimisation_result is not None
    assert q.optimisation_result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert q.data_tier == s.DataTier.T1_PARTIAL_HALF_HOURLY  # re-graded from the partial HH

    telemetry = json.loads((out / "telemetry.json").read_text(encoding="utf-8"))
    assert telemetry["event_type"] == "quote_created"
    assert telemetry["quote_id"] == quote["quote_id"]


def test_run_degraded_no_inputs(tmp_path: Path):
    out = tmp_path / "out0"
    res = runner.invoke(
        app, ["run", str(EXAMPLES / "site_t0.json"), "--wholesale", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert "data ask" in res.output.lower()

    quote = json.loads((out / "quote.json").read_text(encoding="utf-8"))
    assert quote["financial_case"] is None  # degraded honestly, not invented
    q = s.QuoteRecord.model_validate(quote)
    assert q.quote_mode == s.QuoteMode.DRAFT
    assert q.optimisation_result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert q.input_hash and q.input_hash.startswith("sha256:")


def test_run_deterministic_quote_id(tmp_path: Path):
    """Identical inputs reproduce the same stamped quote id (deterministic provenance)."""
    args = ["run", str(EXAMPLES / "site_t2.json"), "--out", str(tmp_path / "a")]
    r1 = runner.invoke(app, args)
    r2 = runner.invoke(app, ["run", str(EXAMPLES / "site_t2.json"), "--out", str(tmp_path / "b")])
    assert r1.exit_code == 0 and r2.exit_code == 0
    q1 = json.loads((tmp_path / "a" / "quote.json").read_text(encoding="utf-8"))
    q2 = json.loads((tmp_path / "b" / "quote.json").read_text(encoding="utf-8"))
    assert q1["quote_id"] == q2["quote_id"]
    assert q1["input_hash"] == q2["input_hash"]


def test_run_missing_site_exits_nonzero(tmp_path: Path):
    res = runner.invoke(app, ["run", str(tmp_path / "does_not_exist.json")])
    assert res.exit_code != 0


def test_run_json_output(tmp_path: Path):
    res = runner.invoke(
        app, ["run", str(EXAMPLES / "site_t2.json"), "--out", str(tmp_path / "o"), "--json"]
    )
    assert res.exit_code == 0, res.output
    assert "quote_id" in res.output
    assert "sha256:" in res.output
