"""Smoke tests for the Wave-5 web UI (W5-1).

Prove the app boots, both run modes work end-to-end, the navigable screens render, the figures shown
trace to the stamped `QuoteRecord` (the UI computes nothing), the output is deterministic, and bad
input is handled gracefully (a real 400 page, never a 500/stack trace). The expensive demo MILP run
lives in `test_app_demo.py`; here we use the fast real-mode T0 path (which degrades to
insufficient_data without a meter file, so no dispatch is solved).
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Pin the run store for this module; re-pinned before each test since store reads the env lazily and
# other test modules set it too.
MODULE_STORE = tempfile.mkdtemp(prefix="gridq-smoke-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402

from gridq.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

# A minimal, valid T0 site (no half-hourly file → honest insufficient_data + ranked data asks).
T0_SITE = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}


def _run_t0() -> str:
    """Submit the T0 site and return the quote_id it redirected to."""
    resp = client.post("/run/real", data=T0_SITE, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    return location.rsplit("/", 1)[-1]


def test_landing_boots_with_both_modes() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "GridQ" in resp.text
    assert "/real" in resp.text and "/demo" in resp.text  # both modes offered, neither auto-run


def test_real_run_reaches_inspect_with_provenance() -> None:
    quote_id = _run_t0()
    resp = client.get(f"/run/{quote_id}")
    assert resp.status_code == 200
    from tests._dpa_support import shown_once

    # No DPA for this visitor's site: the run is shown, never stored (WP-GRC-05, decision (a)).
    stored = shown_once(quote_id)
    # The traceable provenance from the record is shown on the page itself (UI authors nothing).
    for token in (stored.quote.input_hash, stored.quote.assumption_version, stored.quote.site_id):
        assert token and token in resp.text


def test_real_run_surfaces_ranked_data_asks_and_degrades_honestly() -> None:
    quote_id = _run_t0()
    text = client.get(f"/run/{quote_id}").text
    assert "Sharpen this quote" in text  # ranked data-asks panel
    assert "NOT BANKABLE" in text
    assert "unavailable" in text.lower()  # missing figures labelled, not zero-filled


def test_run_is_deterministic() -> None:
    first_id = _run_t0()
    second_id = _run_t0()
    assert first_id == second_id  # deterministic quote_id from identical inputs
    assert client.get(f"/run/{first_id}").text == client.get(f"/run/{second_id}").text


def test_navigable_screens_render() -> None:
    quote_id = _run_t0()
    for suffix in ("/spec", "/artifacts", "/telemetry"):
        assert client.get(f"/run/{quote_id}{suffix}").status_code == 200


def test_invalid_input_is_handled_gracefully() -> None:
    resp = client.post("/run/real", data={"site_name": "no postcode or mic"})
    assert resp.status_code == 400
    assert "could not load" in resp.text.lower()  # friendly message
    assert "<!doctype html>" in resp.text.lower()  # a real page, not a stack trace


def test_unknown_run_is_404_not_500() -> None:
    resp = client.get("/run/quote_does_not_exist")
    assert resp.status_code == 404
    assert "isn" in resp.text.lower()  # the friendly "that run isn't here" page


def test_landing_hero_mount_keeps_svg_fallback_and_invents_nothing() -> None:
    """Cinematic redesign: the WebGL hero is progressive enhancement over a static SVG.

    The mount and its SVG fallback are always in the DOM; the `hero-surface-data` island
    exists ONLY when a solved run's persisted sizing grid backs it. This store holds no
    solved run, so the island must be absent - the hero never draws an invented surface.
    """
    text = client.get("/").text
    assert 'id="hero-3d"' in text  # the mount hero.js swaps its canvas into
    assert "lp-mesh" in text  # the static SVG fallback inside the mount
    assert "hero-surface-data" not in text
    assert "/static/hero.js" in text  # the module loads lazily; FCP never waits on it


def test_run_page_wires_console_chrome_and_motion() -> None:
    """Cinematic redesign: run panels carry the console header + reveal/count-up hooks."""
    quote_id = _run_t0()
    text = client.get(f"/run/{quote_id}").text
    assert 'class="console-head"' in text
    assert "console-status" in text and "status-dot" in text
    assert "data-reveal" in text  # panels opt into the single-observer reveal system
