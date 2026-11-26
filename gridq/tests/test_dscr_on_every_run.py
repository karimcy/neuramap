"""WP-UW-03 acceptance: every run with debt terms shows a DSCR; every run without says why (B001).

B001 (findings register, 2026-11-16): ``min_dscr=None`` on all 12 latest gb_v1_12 runs, because no
real site has a credit band and neither the CLI nor the app could supply debt terms. This module
pins the close through the production entry points: `generate_quote` (the pipeline), ``gridq run``
(the CLI) and ``POST /api/run`` / ``POST /run/real`` (the app), plus the identity half (D78): a
run without terms keeps its quote identity byte-stable, and a run with terms gets its own.

Every lender term set here is SYNTHETIC (``source: synthetic``) and labelled so; none is a lender's
offer. The engine is a stub so the module stays in the fast tier: these tests are about what
reaches finance and the record, not about the MILP.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-uw03-")
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
from gridq.finance.lender_terms import load_lender_terms  # noqa: E402
from gridq.outputs import build_view_model, render_underwriting_memo  # noqa: E402
from gridq.quotes import generate_quote  # noqa: E402
from gridq.quotes.assembly import _canonical_financial_case_dump  # noqa: E402

app_mod = importlib.import_module("gridq.app.app")
REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "lender_terms.synthetic.json"
SYNTHETIC_TERMS = load_lender_terms(FIXTURE)
_LIB = "gb_v1_12"


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


class _PricedStubEngine(BaselineEngine):
    """SYNTHETIC engine: BaselineEngine's request path, a fixed positive-revenue result.

    The baseline result on the conftest site is too small to cover its own costs, which would make
    every DSCR here negative. A positive stack keeps the assertions about a measured ratio
    readable; the figures are synthetic and stand for nothing.
    """

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
                    s.ValueStreamResult(
                        stream=s.MarketStream.CAPACITY_MARKET,
                        annual_revenue_gbp=45_000.0,
                        annual_cost_gbp=0.0,
                        firmness=s.Firmness.CONTRACTED,
                    ),
                ],
            }
        )


def _quote(make_site: Any, **kwargs: Any) -> Any:
    return generate_quote(make_site(), load_assumptions(_LIB), engine=_PricedStubEngine(), **kwargs)


# --- criterion 1, the pipeline ----------------------------------------------------------------


def test_a_run_with_lender_terms_shows_a_measured_dscr(make_site: Any) -> None:
    run = _quote(make_site, lender_terms=SYNTHETIC_TERMS)
    fc = run.quote.financial_case
    assert fc is not None
    assert fc.dscr_summary.status is s.DscrStatus.MEASURED
    assert isinstance(fc.dscr_summary.min_dscr, float) and fc.dscr_summary.min_dscr > 0
    assert fc.lender_terms == SYNTHETIC_TERMS
    # The sensitivity rows measure the same loan: the Central row reproduces the base DSCR.
    central = [r for r in run.quote.scenario_sensitivities if r.label == "Central"]
    assert central and central[0].min_dscr == fc.dscr_summary.min_dscr


def test_a_run_without_debt_terms_shows_dscr_unavailable_with_the_reason(make_site: Any) -> None:
    """Never a silent None and never a default number: the status and reason are on the record."""
    run = _quote(make_site)
    fc = run.quote.financial_case
    assert fc is not None
    d = fc.dscr_summary
    assert d.min_dscr is None and d.by_year == []
    assert d.status is s.DscrStatus.NO_DEBT_TERMS
    assert d.unavailable_reason is not None
    assert d.unavailable_reason.startswith("No lender terms supplied")
    assert fc.covenant_status.passes is None
    assert fc.covenant_status.not_evaluated_reason == (
        "Not evaluated: there is no measured DSCR to test."
    )
    # ...and the reason reaches the reader: the underwriting pack prints the record's own words.
    pack = render_underwriting_memo(build_view_model(run.quote, run.site))
    assert "No lender terms supplied" in pack


def test_the_synthetic_label_reaches_the_pack(make_site: Any) -> None:
    run = _quote(make_site, lender_terms=SYNTHETIC_TERMS)
    pack = render_underwriting_memo(build_view_model(run.quote, run.site))
    assert "SYNTHETIC lender terms (not a lender's offer)" in pack
    assert "Lock-up threshold" in pack


def test_generate_quote_refuses_two_debt_structures(make_site: Any) -> None:
    from gridq.finance import DebtTerms

    with pytest.raises(ValueError, match="not both"):
        _quote(
            make_site,
            lender_terms=SYNTHETIC_TERMS,
            debt_terms=DebtTerms(gearing_pct=50.0, annual_rate_pct=7.0, tenor_years=10),
        )


# --- identity (D78): stable without terms, distinct with them --------------------------------


def test_a_run_without_terms_keeps_its_pre_wp_uw_03_quote_identity(make_site: Any) -> None:
    """The fields WP-UW-03 added never enter the canonical dump of a run without terms.

    Reconstructed literally: the dump must equal the record's JSON with exactly those fields
    removed, so the input hash (and quote_id) is byte-identical to the pre-change shape.
    """
    fc = _quote(make_site).quote.financial_case
    assert fc is not None
    dump = _canonical_financial_case_dump(fc)
    expected = fc.model_dump(mode="json", exclude={"case_id"})
    expected.pop("lender_terms")
    for key in ("status", "unavailable_reason", "cashflow_case"):
        expected["dscr_summary"].pop(key)
    for key in ("source", "lock_up_dscr", "lock_up_passes", "not_evaluated_reason"):
        expected["covenant_status"].pop(key)
    for key in ("principal_gbp", "sized_by"):
        expected["debt_assumptions"].pop(key)
    # WP-UW-17 (D132): the lifecycle inputs (unset here), the derived lifecycle record and
    # the three derived price fields are kept out of the dump under the same rule.
    for key in ("lifecycle_inputs", "lifecycle"):
        expected.pop(key)
    for key in (
        "levelisation_basis",
        "measured_irr_with_price_pct",
        "equity_closed_loop_gbp_per_kwh",
    ):
        expected["price_recommendation"].pop(key)
    assert dump == expected
    assert "lender_terms" not in dump and "lifecycle_inputs" not in dump


def test_lender_terms_enter_the_quote_identity(make_site: Any) -> None:
    without = _quote(make_site).quote
    with_terms = _quote(make_site, lender_terms=SYNTHETIC_TERMS).quote
    other = _quote(
        make_site, lender_terms=SYNTHETIC_TERMS.model_copy(update={"gearing_pct": 40.0})
    ).quote
    assert len({without.input_hash, with_terms.input_hash, other.input_hash}) == 3
    assert len({without.quote_id, with_terms.quote_id, other.quote_id}) == 3


# --- criterion 1, the CLI ---------------------------------------------------------------------


def _cli_run(tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    site = make_site()
    site_json = tmp_path / "site.json"
    site_json.write_text(site.model_dump_json(), encoding="utf-8")
    # `gridq run` needs a DPA for the site (WP-GRC-05): a TEST FIXTURE one for the fictitious
    # conftest site, through the real guard.
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", site.site_id)
    monkeypatch.setattr(
        optimisation_mod, "MilpEngine", lambda market_forecast=None: _PricedStubEngine()
    )
    monkeypatch.chdir(REPO)  # the conftest site's half-hourly ref is repo-relative
    out = tmp_path / "out"
    return (
        CliRunner().invoke(
            cli_app,
            ["run", str(site_json), "--assumptions", _LIB, "--out", str(out), *extra],
            catch_exceptions=False,
        ),
        out,
    )


def test_cli_run_with_lender_terms_records_and_prints_the_dscr(
    tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, out = _cli_run(tmp_path, make_site, monkeypatch, "--lender-terms", str(FIXTURE))
    assert result.exit_code == 0, result.output
    record = json.loads((out / "quote.json").read_text(encoding="utf-8"))
    dscr = record["financial_case"]["dscr_summary"]
    assert dscr["status"] == "measured" and dscr["min_dscr"] > 0
    assert record["financial_case"]["lender_terms"]["provenance"]["source"] == "synthetic"
    assert re.search(r"min DSCR\s*│\s*\d+\.\d+x \(P90 PRE-TAX\)", result.output), result.output


def test_cli_run_without_lender_terms_prints_dscr_unavailable_with_the_reason(
    tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, out = _cli_run(tmp_path, make_site, monkeypatch)
    assert result.exit_code == 0, result.output
    record = json.loads((out / "quote.json").read_text(encoding="utf-8"))
    assert record["financial_case"]["dscr_summary"]["status"] == "no_debt_terms"
    assert "unavailable: No lender terms" in result.output
    assert "none supplied" in result.output


def test_cli_refuses_an_invalid_lender_terms_file_before_solving(
    tmp_path: Path, make_site: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "terms.json"
    bad.write_text(json.dumps({"tenor_years": 10}), encoding="utf-8")
    result, out = _cli_run(tmp_path, make_site, monkeypatch, "--lender-terms", str(bad))
    assert result.exit_code == 2
    assert "Could not load lender terms" in result.output
    assert not out.exists(), "no run is written on a refused term sheet"


# --- criterion 1, the app ---------------------------------------------------------------------

_T0_SITE = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}
#: SYNTHETIC terms as the app's lender-terms block posts them.
_LENDER_FIELDS = {
    "lender_source": "synthetic",
    "lender_reference": "SYNTHETIC app test terms",
    "lender_tenor_years": "10",
    "lender_interest_rate_pct": "7",
    "lender_gearing_pct": "50",
    "lender_amortisation": "annuity",
    "lender_cashflow_case": "P90",
    "lender_calculation_period": "annual",
    "lender_default_dscr": "1.2",
}

_client = TestClient(create_app())


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_generate_quote(site: Any, assumptions: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        # The stub quote carries its site id: the app decides store-or-show-once on it (WP-GRC-05).
        return SimpleNamespace(
            quote=SimpleNamespace(quote_id="quote_stub_uw03", site_id=site.site_id),
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


def test_app_wizard_hands_the_lender_terms_to_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    resp = _client.post("/api/run", data={**_T0_SITE, **_LENDER_FIELDS})
    assert resp.status_code == 200, resp.text[:400]
    assert _wait(resp.json()["job_id"])["status"] == "done"
    terms = captured["lender_terms"]
    assert isinstance(terms, s.LenderTerms) and terms.is_synthetic
    assert terms.tenor_years == 10 and terms.covenant == s.DscrCovenantTerms(default_dscr=1.2)


def test_app_wizard_without_lender_terms_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    resp = _client.post("/api/run", data=_T0_SITE)
    assert resp.status_code == 200, resp.text[:400]
    assert _wait(resp.json()["job_id"])["status"] == "done"
    assert captured["lender_terms"] is None


def test_app_refuses_a_half_entered_term_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    partial = {k: v for k, v in _LENDER_FIELDS.items() if k != "lender_interest_rate_pct"}
    resp = _client.post("/api/run", data={**_T0_SITE, **partial})
    assert resp.status_code == 400
    assert "interest_rate_pct" in resp.json()["message"]
    assert captured == {}, "no run starts on a refused term sheet"


def test_app_real_form_hands_the_lender_terms_to_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture(monkeypatch)
    resp = _client.post("/run/real", data={**_T0_SITE, **_LENDER_FIELDS}, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:400]
    assert isinstance(captured["lender_terms"], s.LenderTerms)


def test_the_wizard_offers_the_lender_terms_block_with_nothing_preselected() -> None:
    html = _client.get("/new-quote").text
    for name in ("lender_tenor_years", "lender_interest_rate_pct", "lender_cashflow_case"):
        assert f'name="{name}"' in html
    block = html[html.index('name="lender_cashflow_case"') :]
    block = block[: block.index("</select>")]
    assert "selected" not in block, "the form must never default a lender term"


# --- criterion 2: B001 is closed in the register, citing tests that exist ---------------------


def test_b001_is_closed_in_the_findings_register_and_its_citations_resolve() -> None:
    """The row is closed in the register's own format and every pointer in it is live.

    CLAUDE.md §4: nothing is FIXED until the row names the production call site AND the test that
    fails if it is deleted. A pointer that drifts (a renamed test, a moved line) turns this red, so
    the register cannot quietly start citing code that no longer does the job.
    """
    register = (REPO / "docs/reviews/2026-11-16-findings-register-triage.md").read_text(
        encoding="utf-8"
    )
    row = next(line for line in register.splitlines() if line.startswith("| B001"))
    assert "**C** (WP-UW-03" in row, row
    cited = re.findall(r"(tests/[\w/]+\.py)::(\w+)", row)
    assert cited, "the closed row must name its guarding test"
    for path, name in cited:
        source = (REPO / path).read_text(encoding="utf-8")
        assert f"def {name}(" in source, f"{path}::{name} cited by B001 does not exist"
    sites = re.findall(r"(src/gridq/[\w/]+\.py):(\d+)", row)
    assert sites, "the closed row must name its production call site"
    for path, line in sites:
        text = (REPO / path).read_text(encoding="utf-8").splitlines()
        cited_line = text[int(line) - 1]
        assert re.search(r"lender_terms|unavailable_reason", cited_line), (
            f"{path}:{line} is not a lender-terms call site: {cited_line.strip()!r}"
        )
