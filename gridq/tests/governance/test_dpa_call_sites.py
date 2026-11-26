"""Every production call site of the DPA guard has a test that fails when the call is deleted.

WP-GRC-05 (CLAUDE.md section 4: nothing is fixed until it is reachable). One test per choke point:

- ``gridq/ingestion/hh_data.py`` `load_half_hourly_csv` and ``ingestion/loaders.py``
  `load_site_model_json_with_report`: tests/governance/test_dpa_guard.py;
- ``app/store.py`` `save_run` / `load_run`: tests/governance/test_dpa_guard.py;
- ``app/app.py`` the web intake (`_keep_run`, `TransientWorkspace`, `_run_context`): below;
- ``cli.py`` `run` and `render`: below;
- ``quotes/pipeline.py`` `_invoice_evidence_for`, `_measured_summary`,
  `_derive_half_hourly_if_asked`: below;
- ``value_stack/gb_streams.py`` `_min_in_window_load_kw`: below;
- ``app/demo.py`` `load_haywood_oaks_prefill`: tests/governance/test_dpa_guard.py.

No customer file is read: each refusal happens before the open.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from tests._dpa_support import register_fixture_dpas

import gridq.schemas as s
from gridq.app import ephemeral
from gridq.governance.customer_data import load_manifest
from gridq.governance.dpa_guard import (
    TRANSIENT_DIR_ENV,
    DpaEvidenceMissing,
    repo_root,
)

REAL_SITE = repo_root() / "examples" / "real" / "sites" / "ewelme.json"


def _quarantined_series() -> Path:
    entry = next(e for e in load_manifest().datasets if e.kind == "hh_series")
    return repo_root() / entry.path


# ---------------------------------------------------------------------------------------------
# quotes/pipeline.py and value_stack/gb_streams.py
# ---------------------------------------------------------------------------------------------
def test_invoice_evidence_is_refused_for_a_covered_site_and_unchanged_for_any_other() -> None:
    from gridq.quotes.pipeline import _invoice_evidence_for

    covered = _invoice_evidence_for("grundon_ewelme")
    assert covered.invoice_available is False
    assert "quarantined customer data" in (covered.absence_reason or "")
    other = _invoice_evidence_for("FIXTURE-SITE-ALPHA")
    assert other.absence_reason == (
        "no invoice evidence is recorded for site_id 'FIXTURE-SITE-ALPHA' in "
        "grundon_invoices_2025.json."
    )


def test_gate_3_does_not_quietly_skip_a_refused_series(
    make_site: Callable[..., s.SiteModel],
) -> None:
    from gridq.quotes.pipeline import _measured_summary

    site = make_site()
    load = site.load_data.model_copy(update={"half_hourly_file_ref": str(_quarantined_series())})
    with pytest.raises(DpaEvidenceMissing):
        _measured_summary(site.model_copy(update={"load_data": load}), None)


def test_deriving_a_series_to_disk_needs_a_permit(
    make_site: Callable[..., s.SiteModel], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridq.assumptions import latest_version, load_assumptions
    from gridq.quotes.pipeline import _derive_half_hourly_if_asked

    site = make_site(with_max_import=False)
    assert not site.load_data.half_hourly_file_ref
    assumptions = load_assumptions(latest_version())
    monkeypatch.setenv("GRIDQ_EVIDENCE_DIR", str(tmp_path / "empty"))
    with pytest.raises(DpaEvidenceMissing):
        _derive_half_hourly_if_asked(site, tmp_path / "derived", assumptions)
    assert not (tmp_path / "derived").exists()
    register_fixture_dpas(monkeypatch, tmp_path / "ev", site.site_id)
    _derive_half_hourly_if_asked(site, tmp_path / "derived", assumptions)


def test_the_local_flex_minimum_load_read_does_not_swallow_a_refusal(
    make_site: Callable[..., s.SiteModel],
) -> None:
    from gridq.value_stack.gb_streams import _min_in_window_load_kw

    site = make_site()
    load = site.load_data.model_copy(update={"half_hourly_file_ref": str(_quarantined_series())})
    with pytest.raises(DpaEvidenceMissing):
        _min_in_window_load_kw(site.model_copy(update={"load_data": load}), None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# cli.py
# ---------------------------------------------------------------------------------------------
def test_gridq_run_on_a_real_site_exits_3_and_writes_nothing(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gridq.cli import EXIT_DPA_REFUSED, app

    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["run", str(REAL_SITE), "--out", str(out)])
    assert result.exit_code == EXIT_DPA_REFUSED == 3
    assert "Refused (WP-GRC-05 DPA guard)" in result.output
    assert not out.exists()


def test_gridq_run_on_a_new_site_with_no_dpa_exits_3_before_reading_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site the manifest has never seen (so the load gate passes it) still needs a permit."""
    from typer.testing import CliRunner

    from gridq.cli import app

    monkeypatch.setenv("GRIDQ_EVIDENCE_DIR", str(tmp_path / "no_evidence"))
    out = tmp_path / "out"
    site = repo_root() / "examples" / "site_t0.json"  # fictitious, outside every customer root
    result = CliRunner().invoke(app, ["run", str(site), "--out", str(out)])
    assert result.exit_code == 3
    assert "site_000900" in result.output
    assert not out.exists()


def test_gridq_render_for_a_site_without_a_dpa_exits_3_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from gridq.cli import app

    quote = s.QuoteRecord(
        quote_id="quote_fixture_render",
        quote_mode=s.QuoteMode.DRAFT,
        site_id="FIXTURE-SITE-RENDER",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        warnings=[],
        status_history=[],
    )
    quote_json = tmp_path / "quote.json"
    quote_json.write_text(quote.model_dump_json(), encoding="utf-8")
    out = tmp_path / "docs"
    empty = tmp_path / "no_evidence"
    monkeypatch.delenv("GRIDQ_EVIDENCE_DIR", raising=False)
    result = CliRunner().invoke(
        app, ["render", str(quote_json), "--out", str(out), "--evidence-dir", str(empty)]
    )
    assert result.exit_code == 3
    assert not out.exists()
    # `--evidence-dir` applied to that command only: nothing leaks into the calling process.
    import os

    assert "GRIDQ_EVIDENCE_DIR" not in os.environ


# ---------------------------------------------------------------------------------------------
# app/app.py: show once, store nothing (owner decision (a), 2026-11-23)
# ---------------------------------------------------------------------------------------------
_FORM = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Fixture Bakery Ltd (fictitious)",
    "site_id": "FIXTURE-SITE-WEB",
}


@pytest.fixture
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    from fastapi.testclient import TestClient

    from gridq.app import create_app

    store_dir = tmp_path / "runs"
    transient = tmp_path / "transient"
    monkeypatch.setenv("GRIDQ_RUN_STORE", str(store_dir))
    monkeypatch.setenv(TRANSIENT_DIR_ENV, str(transient))
    monkeypatch.setenv("GRIDQ_EVIDENCE_DIR", str(tmp_path / "no_evidence"))
    ephemeral.clear()
    app = create_app()
    yield {"app": app, "client": TestClient(app), "store": store_dir, "transient": transient}
    ephemeral.clear()


def _files_under(path: Path) -> list[Path]:
    return [p for p in path.rglob("*") if p.is_file()] if path.exists() else []


def _post_real(client: Any) -> str:
    resp = client.post("/run/real", data=_FORM, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:500]
    return str(resp.headers["location"]).rsplit("/", 1)[-1]


def test_an_intake_without_a_dpa_is_shown_once_and_nothing_is_stored(
    web: dict[str, Any],
) -> None:
    from fastapi.testclient import TestClient

    from gridq.app import store

    client = web["client"]
    quote_id = _post_real(client)
    page = client.get(f"/run/{quote_id}")
    assert page.status_code == 200
    assert "Not saved; expires in 15 minutes." in page.text
    assert _files_under(web["store"]) == []  # nothing under runs/
    assert _files_under(web["transient"]) == []  # the modelled profile went with the request
    assert store.load_run(quote_id) is None
    # Bound to the visitor who ran it: another browser gets the same 404 as an unknown run.
    assert TestClient(web["app"]).get(f"/run/{quote_id}").status_code == 404
    # Expired (or held by another worker, or lost to a restart): a 404, never a stored fallback.
    ephemeral.clear()
    assert client.get(f"/run/{quote_id}").status_code == 404


def test_a_signed_in_visitor_with_a_registered_dpa_stores_the_run_with_its_record(
    web: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridq.app import auth

    register_fixture_dpas(monkeypatch, tmp_path / "ev", _FORM["site_id"])
    me = {"id": "google:fixture-user", "email": None, "name": "Fixture", "picture": None}
    monkeypatch.setattr(
        auth,
        "current_user",
        lambda request: {**me, "signed_in": True, "google_available": False},
    )
    quote_id = _post_real(web["client"])
    record = json.loads((web["store"] / quote_id / "dpa_evidence.json").read_text("utf-8"))
    assert record["basis"] == "dpa" and record["site_id"] == _FORM["site_id"]
    page = web["client"].get(f"/run/{quote_id}")
    assert page.status_code == 200 and "Not saved" not in page.text
    assert _files_under(web["transient"]) == []


def test_an_anonymous_visitor_never_stores_even_under_a_dpa(
    web: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_fixture_dpas(monkeypatch, tmp_path / "ev", _FORM["site_id"])
    quote_id = _post_real(web["client"])
    assert _files_under(web["store"]) == []
    assert "Not saved" in web["client"].get(f"/run/{quote_id}").text


def test_a_held_run_expires_after_fifteen_minutes() -> None:
    quote = s.QuoteRecord(
        quote_id="quote_fixture_ttl",
        quote_mode=s.QuoteMode.DRAFT,
        site_id="FIXTURE-SITE-TTL",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        warnings=[],
        status_history=[],
    )
    ephemeral.hold(quote, None, None, owner_id="anon:x", now=1000.0)
    assert ephemeral.get(quote.quote_id, now=1000.0 + ephemeral.TTL_SECONDS - 1) is not None
    assert ephemeral.get(quote.quote_id, now=1000.0 + ephemeral.TTL_SECONDS) is None
    assert ephemeral.TTL_SECONDS == 15 * 60
