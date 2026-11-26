"""W5-2: the Inspect screen wires the vendored-Plotly charts to the data islands, offline.

Pins the visualisation seam at the page boundary: the Inspect HTML loads Plotly from a **vendored**
``/static`` asset (no external CDN — so it renders offline and under a strict CSP), mounts every
chart div, emits every JSON island, and hands them to the client renderer. It also pins the honest
degrade (a run with no half-hourly dispatch shows the labelled seam, never an invented series) and
that the rendered page is deterministic.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-charts-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402

from gridq.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

_resp = client.post("/run/demo", follow_redirects=False)
assert _resp.status_code == 303
QUOTE_ID = _resp.headers["location"].rsplit("/", 1)[-1]
INSPECT = client.get(f"/run/{QUOTE_ID}").text


def _island(html: str, ident: str) -> dict:
    match = re.search(rf'id="{ident}" type="application/json">(.*?)</script>', html, re.S)
    assert match, f"missing island {ident}"
    return json.loads(match.group(1))


def test_plotly_is_vendored_and_no_external_cdn_is_referenced() -> None:
    assert "/static/plotly.min.js" in INSPECT
    # No external CDN / remote script — the whole surface is self-hosted (offline / CSP-safe).
    assert "cdn.plot.ly" not in INSPECT and "cdnjs" not in INSPECT
    assert not re.search(r'<script[^>]+src="https?://', INSPECT)


def test_every_chart_div_and_island_is_present() -> None:
    for div in ("surface", "chart-dispatch", "chart-cashflows", "chart-dscr", "chart-valuestack"):
        assert f'id="{div}"' in INSPECT, div
    for ident in ("surface-data", "finance-data", "dispatch-data", "valuestack-data"):
        _island(INSPECT, ident)  # parses as JSON
    assert "GridQCharts.init(" in INSPECT


def test_demo_islands_are_populated_from_the_record() -> None:
    assert _island(INSPECT, "surface-data")["available"] is True
    dispatch = _island(INSPECT, "dispatch-data")
    assert dispatch["available"] is True and len(dispatch["labels"]) == 48
    vs = _island(INSPECT, "valuestack-data")
    assert vs["available"] is True and len(vs["segments"]) >= 1
    fin = _island(INSPECT, "finance-data")
    assert fin["cashflows"]["available"] is True and fin["dscr"]["available"] is True


def test_inspect_page_is_deterministic() -> None:
    # The clock is not an output (the golden-quote test's convention): the page legitimately
    # renders today's date, so a suite run that straddles midnight diffed on it (observed
    # 2026-07-07→08). Normalise rendered ISO dates out of both renders; every other byte —
    # all the numbers — must still be identical.
    date = re.compile(r"\d{4}-\d{2}-\d{2}")
    again = client.post("/run/demo", follow_redirects=True)
    assert date.sub("<date>", again.text) == date.sub("<date>", INSPECT)


def test_run_without_dispatch_shows_the_honest_seam_not_an_invented_series() -> None:
    form = {
        "postcode": "LE17 4XN",
        "mic_kw": "500",
        "annual_consumption_kwh": "900000",
        "legal_name": "Acme Ltd",
    }
    html = client.post("/run/real", data=form, follow_redirects=True).text
    assert _island(html, "dispatch-data") == {"available": False}
    assert "no half-hourly dispatch was persisted" in html
    # The chart div is suppressed (no empty plot), the seam message stands in its place.
    assert 'id="chart-dispatch"' not in html
