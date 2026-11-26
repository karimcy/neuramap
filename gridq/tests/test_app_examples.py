"""Real-mode example prefill (Robert Thomas Farms) — the upload-first real-data entry.

The example pre-fills the *real-mode* intake form from a private, gitignored facts file. It carries
no half-hourly series (the visitor uploads their own), invents nothing, and degrades to a blank
form when the file is absent — so these tests are hermetic and never depend on private data.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gridq.app.app import create_app
from gridq.app.demo import load_haywood_oaks_prefill


def test_prefill_loader_absent_returns_none(tmp_path: Path) -> None:
    assert load_haywood_oaks_prefill(tmp_path / "nope.json") is None


def test_prefill_loader_parses_strips_meta_and_stringifies(tmp_path: Path) -> None:
    p = tmp_path / "prefill.json"
    p.write_text(json.dumps({"_comment": "ignore me", "postcode": "NG21 0QA", "mic_kw": 250}))
    out = load_haywood_oaks_prefill(p)
    assert out == {"postcode": "NG21 0QA", "mic_kw": "250"}  # meta dropped; values are strings


def test_prefill_loader_bad_json_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ not json")
    assert load_haywood_oaks_prefill(p) is None


def test_real_example_route_is_the_intake_form_with_upload() -> None:
    client = TestClient(create_app())
    resp = client.get("/real/haywood-oaks")
    assert resp.status_code == 200
    # Always the real intake form (posts to /run/real) whether or not the private example is
    # installed — and the half-hourly upload field is present (the front-end upload path).
    assert 'action="/run/real"' in resp.text
    assert 'name="hh_file"' in resp.text
