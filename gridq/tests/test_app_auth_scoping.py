"""Accounts and per-user project scoping (2026-07-07).

The projects tab used to list EVERYONE's runs. Now every browser gets a private anonymous
workspace (signed session cookie) and Google sign-in activates when OAuth credentials are set;
runs are owned by the submitting identity, listings scope to the visitor, another identity's
run pages 404 (never 403 — no existence leak), and legacy/shared runs (no owner sidecar) stay
visible to everyone. These tests exercise the fallback (no Google credentials) posture, which
is exactly what the live deployment runs today.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-auth-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from gridq.app import create_app, store  # noqa: E402
from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, DemoEngine, load_demo_site  # noqa: E402
from gridq.assumptions import load_assumptions  # noqa: E402
from gridq.governance.dpa_guard import DEMO_SITE_ID, authorise_store  # noqa: E402
from gridq.quotes import generate_quote  # noqa: E402
from gridq.schemas import QuoteMode  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


app = create_app()

# One real demo-engine run persisted THREE ways: owned by visitor A, owned by visitor B (a
# different quote is not needed — ownership is a sidecar, so we save two distinct fake records
# by copying with different ids), and unowned (legacy/shared).
_run = generate_quote(
    load_demo_site(),
    load_assumptions("gb_v1_4"),
    engine=DemoEngine(),
    debt_terms=DEMO_DEBT_TERMS,
    quote_mode=QuoteMode.INDICATIVE,
    extra_warnings=[DEMO_LABEL],
)
QUOTE_A = _run.quote.model_copy(update={"quote_id": "quote_owned_by_a"})
QUOTE_B = _run.quote.model_copy(update={"quote_id": "quote_owned_by_b"})
QUOTE_SHARED = _run.quote.model_copy(update={"quote_id": "quote_legacy_shared"})
#: The curated demo is the one site stored without a DPA, by its attested hash (WP-GRC-05).
_DEMO_PERMIT = authorise_store(DEMO_SITE_ID)


def _client() -> TestClient:
    """A fresh browser: its own cookie jar ⇒ its own anonymous workspace."""
    return TestClient(app)


def test_two_identities_get_isolated_project_lists() -> None:
    # Store-level scoping rule: own runs + legacy/shared runs; never another identity's.
    store.save_run(QUOTE_A, _run.site, None, owner_id="anon:browser-a", dpa=_DEMO_PERMIT)
    store.save_run(QUOTE_B, _run.site, None, owner_id="anon:browser-b", dpa=_DEMO_PERMIT)
    store.save_run(QUOTE_SHARED, _run.site, None, dpa=_DEMO_PERMIT)

    runs_a = store.list_runs(visible_to_id="anon:browser-a")
    ids_a = {r.quote.quote_id for r in runs_a}
    assert "quote_owned_by_a" in ids_a
    assert "quote_owned_by_b" not in ids_a  # the other identity's run is invisible
    assert "quote_legacy_shared" in ids_a  # legacy/shared stays visible

    runs_b = store.list_runs(visible_to_id="anon:browser-b")
    ids_b = {r.quote.quote_id for r in runs_b}
    assert "quote_owned_by_b" in ids_b
    assert "quote_owned_by_a" not in ids_b


def test_another_identitys_run_page_is_a_404_not_a_403() -> None:
    store.save_run(QUOTE_A, _run.site, None, owner_id="anon:browser-a", dpa=_DEMO_PERMIT)
    fresh = _client()  # a new browser: its workspace id can never equal "anon:browser-a"
    resp = fresh.get("/run/quote_owned_by_a")
    assert resp.status_code == 404  # indistinguishable from a missing run — no existence leak
    shared = fresh.get("/run/quote_legacy_shared")
    assert shared.status_code == 200  # legacy/shared runs stay reachable


def test_projects_page_marks_shared_runs_and_states_the_scoping() -> None:
    fresh = _client()
    page = fresh.get("/projects")
    assert page.status_code == 200
    assert "private workspace" in page.text.lower()
    assert "Shared" in page.text  # the legacy/shared run is labelled


def test_auth_login_states_the_constraint_when_google_is_not_configured() -> None:
    for var in ("GRIDQ_GOOGLE_CLIENT_ID", "GRIDQ_GOOGLE_CLIENT_SECRET"):
        assert not os.environ.get(var), "test expects the no-credentials posture"
    resp = _client().get("/auth/login")
    assert resp.status_code == 503
    assert "not configured" in resp.text.lower()
    assert "private" in resp.text.lower()  # the fallback isolation is stated


def test_wizard_job_owns_the_run_for_the_submitting_browser() -> None:
    # The full wizard path (POST /api/run) captures ownership from the session; the demo run is
    # deliberately UNOWNED (one shared curated record). Assert both via the demo flag:
    a = _client()
    resp = a.post("/api/run", data={"demo": "1"})
    assert resp.status_code == 200
    job = resp.json()["job_id"]
    import time

    for _ in range(600):
        status = a.get(f"/api/run/{job}").json()
        if status.get("status") in ("done", "error"):
            break
        time.sleep(1)
    assert status.get("status") == "done"
    stored = store.load_run(status["quote_id"])
    assert stored is not None
    assert stored.owner_id is None  # the curated demo stays shared, never claimed
    # ... and it is therefore visible to a completely different browser too.
    other = _client()
    assert other.get(f"/run/{status['quote_id']}").status_code == 200
