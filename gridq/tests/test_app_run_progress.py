"""The run-job status endpoint surfaces REAL, forward-only progress (C035).

`api_run_start` seeds the job with ``stage``/``stage_done``/``stage_total`` and hands the
deterministic pipeline a ``progress`` sink that writes those facts into the in-process job dict;
``GET /api/run/{job_id}`` returns the dict verbatim. These tests drive that seam with a
monkeypatched pipeline so they are fast and hermetic (no MILP): a running job reflects the live
stage the pipeline emitted, and terminal states (done / error) still land as before. The real
demo-run poll is covered by ``test_app_auth_scoping``.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time

import pytest
from fastapi.testclient import TestClient

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-run-progress-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

import gridq.quotes as quotes_mod  # noqa: E402
from gridq.app import create_app, store  # noqa: E402
from gridq.schemas import SelectionStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


app = create_app()


def _client() -> TestClient:
    return TestClient(app)


class _StubOpt:
    selection_status = SelectionStatus.NO_VIABLE_CONFIGURATION


class _StubQuote:
    quote_id = "quote_stub_progress"
    optimisation_result = _StubOpt()
    dispatch_profile = None
    sizing_grid = None


class _StubRun:
    quote = _StubQuote()
    site = None
    telemetry = None


def _wait_terminal(client: TestClient, job: str, tries: int = 100) -> dict:
    status: dict = {}
    for _ in range(tries):
        status = client.get(f"/api/run/{job}").json()
        if status.get("status") in ("done", "error"):
            return status
        time.sleep(0.02)
    return status


def test_status_reflects_live_stage_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted = threading.Event()
    release = threading.Event()

    def fake_generate_quote(site, assumptions, *, progress=None, **kwargs):  # type: ignore[no-untyped-def]
        assert progress is not None, "the app must hand the pipeline a progress sink"
        progress("coarse", 3, 10)  # a mid-search phase fact, with a known total
        emitted.set()
        release.wait(timeout=5)
        return _StubRun()

    monkeypatch.setattr(quotes_mod, "generate_quote", fake_generate_quote)
    monkeypatch.setattr(store, "save_run", lambda *a, **k: None)

    client = _client()
    resp = client.post("/api/run", data={"demo": "1"})
    assert resp.status_code == 200
    job = resp.json()["job_id"]

    assert emitted.wait(timeout=5)
    running = client.get(f"/api/run/{job}").json()
    assert running["status"] == "running"
    assert running["stage"] == "coarse"
    assert running["stage_done"] == 3
    assert running["stage_total"] == 10

    release.set()
    done = _wait_terminal(client, job)
    assert done["status"] == "done"
    assert done["stage"] == "done"
    assert done["quote_id"] == "quote_stub_progress"


def test_initial_job_carries_stage_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed write must include the progress fields so an early poll never sees them missing.
    release = threading.Event()

    def fake_generate_quote(site, assumptions, *, progress=None, **kwargs):  # type: ignore[no-untyped-def]
        release.wait(timeout=5)
        return _StubRun()

    monkeypatch.setattr(quotes_mod, "generate_quote", fake_generate_quote)
    monkeypatch.setattr(store, "save_run", lambda *a, **k: None)

    client = _client()
    job = client.post("/api/run", data={"demo": "1"}).json()["job_id"]
    seeded = client.get(f"/api/run/{job}").json()
    assert seeded["stage"] == "optimise"
    assert "stage_done" in seeded and "stage_total" in seeded
    assert seeded["stage_done"] is None and seeded["stage_total"] is None
    release.set()
    _wait_terminal(client, job)


def test_status_reflects_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom in the pipeline")

    monkeypatch.setattr(quotes_mod, "generate_quote", boom)

    client = _client()
    job = client.post("/api/run", data={"demo": "1"}).json()["job_id"]
    status = _wait_terminal(client, job)
    assert status["status"] == "error"
    assert "kaboom" in status["message"]
