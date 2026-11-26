"""Deployment-surface tests for the Wave-5 web app (W5-3).

Cover the two additions the public deployment needs:

* a cheap, unauthenticated ``/healthz`` probe the host's load-balancer can poll, and
* an **optional** HTTP Basic gate that is dormant unless ``GRIDQ_BASIC_AUTH="user:pass"`` is set —
  so the default posture (and every other test) is unchanged, but a public URL can be password-gated
  without shipping a credential in the repo.

The gate is read when the app is built, so each scenario builds its own app via ``create_app()``.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from gridq.app import create_app

# Pin a throwaway run store so building an app here never touches a real one (re-pinned per test
# since the store reads the env lazily and other test modules set it too).
MODULE_STORE = tempfile.mkdtemp(prefix="gridq-deploy-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


# ---- health probe --------------------------------------------------------
def test_healthz_is_ok_and_unauthenticated() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---- gate is off by default ----------------------------------------------
def test_no_gate_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIDQ_BASIC_AUTH", raising=False)
    client = TestClient(create_app())
    assert client.get("/").status_code == 200  # open, no credentials needed


def test_blank_or_malformed_gate_env_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", "no-colon-here"):
        monkeypatch.setenv("GRIDQ_BASIC_AUTH", value)
        client = TestClient(create_app())
        assert client.get("/").status_code == 200


# ---- gate on -------------------------------------------------------------
@pytest.fixture()
def gated_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GRIDQ_BASIC_AUTH", "olly:s3cret")
    return TestClient(create_app())


def test_gate_challenges_without_credentials(gated_client: TestClient) -> None:
    resp = gated_client.get("/")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


def test_gate_rejects_wrong_credentials(gated_client: TestClient) -> None:
    assert gated_client.get("/", auth=("olly", "wrong")).status_code == 401
    assert gated_client.get("/", auth=("nope", "s3cret")).status_code == 401


def test_gate_admits_correct_credentials(gated_client: TestClient) -> None:
    assert gated_client.get("/", auth=("olly", "s3cret")).status_code == 200


def test_gate_exempts_healthz_and_static(gated_client: TestClient) -> None:
    # The host health check and the self-contained assets must work without credentials.
    assert gated_client.get("/healthz").status_code == 200
    assert gated_client.get("/static/app.css").status_code == 200
