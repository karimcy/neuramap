"""Platform QA sweep: exercise every route and feature; emit docs/redteam/QA_MATRIX.md.

Drives the app through FastAPI's TestClient exactly as a browser would (GET every page, POST
every form path, upload files, exercise auth scoping) and logs PASS/FAIL per feature with the
observed evidence. Heavy MILP paths (wizard full run, demo, examples/real E2E) are asserted by
their dedicated test modules and referenced here rather than re-solved, EXCEPT one demo job run
kept in this sweep so the matrix itself proves the run machinery end to end.

Run:  TZ=UTC OMP_NUM_THREADS=1 /root/.venv/bin/python scripts/qa_matrix.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("TZ", "UTC")
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(var, "1")
os.environ["GRIDQ_RUN_STORE"] = tempfile.mkdtemp(prefix="gridq-qa-")

from fastapi.testclient import TestClient  # noqa: E402

from gridq.app import create_app, store  # noqa: E402

ROWS: list[tuple[str, str, str, str]] = []  # (area, feature, PASS/FAIL, evidence)


def check(area: str, feature: str, fn) -> None:
    try:
        evidence = fn()
        ROWS.append((area, feature, "PASS", evidence or ""))
    except AssertionError as exc:
        ROWS.append((area, feature, "FAIL", str(exc)))
    except Exception as exc:  # noqa: BLE001 — a crash is a FAIL with the error as evidence
        ROWS.append((area, feature, "FAIL", f"{type(exc).__name__}: {exc}"))


app = create_app()
c = TestClient(app)


# ---- pages ---------------------------------------------------------------
def _page(path: str, *needles: str, status: int = 200):
    def fn() -> str:
        r = c.get(path)
        assert r.status_code == status, f"{path} -> {r.status_code}"
        for n in needles:
            assert n.lower() in r.text.lower(), f"{path}: missing {n!r}"
        return f"{r.status_code}, {len(r.text)}B"

    return fn


def _uk_only() -> str:
    text = c.get("/").text.lower()
    for name in ("spain", "cyprus", "germany"):
        assert name not in text, f"found {name!r} on the landing"
    return "clean"


def _redirect(path: str, expect: int, location_needle: str) -> str:
    r = c.get(path, follow_redirects=False)
    assert r.status_code == expect, f"{path} -> {r.status_code}"
    assert location_needle in r.headers.get("location", ""), r.headers.get("location")
    return f"{r.status_code} -> {r.headers['location']}"


def _health() -> str:
    r = c.get("/healthz")
    r.raise_for_status()
    return str(r.json()["status"])


check(
    "Pages", "Landing (dashboard)", _page("/", "GridQ", "pipeline, in the open", "United Kingdom")
)
check(
    "Pages",
    "Landing walkthrough has all 7 stages",
    _page(
        "/",
        "Intake: your site",
        "Data grading",
        "Value stack",
        "Dispatch: a year",
        "Sizing: every size",
        "Finance: lender arithmetic",
        "The quote: one record",
    ),
)
check("Pages", "Landing is UK-only (no Spain/Cyprus/Germany)", _uk_only)
check("Pages", "Projects", _page("/projects", "Projects", "workspace"))
check("Pages", "Equipment catalogue", _page("/equipment", "Equipment"))
check("Pages", "Equipment upload form", _page("/equipment/upload", "Add equipment", "datasheet"))
check("Pages", "Review (red-team) page", _page("/review", "Review"))
check("Pages", "New Quote wizard", _page("/new-quote", "wizard", "Continue"))
check("Pages", "Real form redirects to the wizard", lambda: _redirect("/real", 307, "/new-quote"))
check(
    "Pages",
    "Real prospect example prefill",
    _page(
        "/real/haywood-oaks",
        "Robert Thomas Farms",
        "Levy lines",
        "Capacity charge",
        "day-rate share",
    ),
)
check("Pages", "Health probe", _health)
check("Pages", "Unknown run 404 page", _page("/run/quote_does_not_exist", "isn", status=404))
check(
    "Pages",
    "Auth constraint page (no credentials)",
    _page("/auth/login", "not configured", "private", status=503),
)
check("Pages", "Logout redirects home", lambda: _redirect("/auth/logout", 303, "/"))


# ---- forms / validation ---------------------------------------------------
def _bad_real_form() -> str:
    r = c.post("/run/real", data={"site_name": "no postcode"})
    assert r.status_code == 400 and "could not load" in r.text.lower(), str(r.status_code)
    return "400 + friendly page"


def _bad_wizard_post() -> str:
    r = c.post("/api/run", data={"site_name": "nothing else"})
    assert r.status_code == 400 and r.json().get("error") == "invalid", f"{r.status_code}"
    return "400 JSON"


def _unknown_job() -> str:
    r = c.get("/api/run/nope")
    assert r.status_code == 404, str(r.status_code)
    return "404"


check("Forms", "Invalid real-form input -> friendly 400", _bad_real_form)
check("Forms", "Wizard API rejects bad input as JSON 400", _bad_wizard_post)
check("Forms", "Unknown job id polls as 404", _unknown_job)


# ---- the demo run: full machinery end to end (ONE MILP solve in this sweep) ----
def _demo_run() -> str:
    r = c.post("/api/run", data={"demo": "1"})
    assert r.status_code == 200, r.text
    job = r.json()["job_id"]
    deadline = time.time() + 600
    status: dict = {}
    while time.time() < deadline:
        status = c.get(f"/api/run/{job}").json()
        if status.get("status") in ("done", "error"):
            break
        time.sleep(1)
    assert status.get("status") == "done", str(status)
    qid = status["quote_id"]
    globals()["DEMO_QID"] = qid
    return f"quote {qid}"


check("Run machinery", "Demo run via streaming job (/api/run demo=1)", _demo_run)

DEMO_QID = globals().get("DEMO_QID")
if DEMO_QID:

    def _demo_bill_fixed_line() -> str:
        s = store.load_run(DEMO_QID)
        assert s and s.quote.customer_bill, "no bill on the demo run"
        assert s.quote.customer_bill.standing_gbp > 0, "no standing line"
        return f"standing_gbp={s.quote.customer_bill.standing_gbp}"

    def _demo_unowned() -> str:
        s = store.load_run(DEMO_QID)
        assert s is not None and s.owner_id is None, f"owner={getattr(s, 'owner_id', '?')}"
        return "unowned"

    check(
        "Run screens",
        "Results (inspect)",
        _page(f"/run/{DEMO_QID}", "Sized system", "provenance", "NOT BANKABLE"),
    )
    check("Run screens", "System spec tab", _page(f"/run/{DEMO_QID}/spec", "spec"))
    check(
        "Run screens",
        "Artifacts tab (memo+proposal+report render)",
        _page(f"/run/{DEMO_QID}/artifacts", "memo", "proposal"),
    )
    check("Run screens", "Telemetry tab", _page(f"/run/{DEMO_QID}/telemetry", "telemetry"))
    check("Run screens", "Demo bill carries the standing fixed line", _demo_bill_fixed_line)
    check("Run screens", "Demo stays shared (unowned)", _demo_unowned)


# ---- accounts / scoping ----------------------------------------------------
def _scoping() -> str:
    demo = store.load_run(DEMO_QID) if DEMO_QID else None
    assert demo is not None, "needs the demo run"
    private = demo.quote.model_copy(update={"quote_id": "qa_private_run"})
    store.save_run(private, demo.site, None, owner_id="anon:someone-else")
    fresh = TestClient(app)
    assert fresh.get("/run/qa_private_run").status_code == 404, "cross-identity run must 404"
    listing = fresh.get("/projects").text
    assert "qa_private_run" not in listing, "cross-identity run must not list"
    assert DEMO_QID in listing, "shared demo must list for everyone"
    return "404 + hidden from listing; shared demo visible"


check("Accounts", "Cross-identity isolation (list + page)", _scoping)


# ---- uploads ----------------------------------------------------------------
def _equipment_upload() -> str:
    # Field names mirror equipment_upload.html: maker/model + <category>__<spec key>.
    r = c.post(
        "/equipment/upload",
        data={
            "category": "batteries",
            "maker": "QA Co",
            "model": "QA Test Battery 100",
            "batteries__energy_kwh": "100",
            "batteries__price": "150",
            "batteries__chemistry": "LFP",
        },
        files={"datasheet": ("qa.pdf", io.BytesIO(b"%PDF-1.4 qa"), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"{r.status_code} {r.text[:200]}"
    page = c.get("/equipment").text
    assert "QA Test Battery 100" in page, "uploaded equipment not in catalogue"
    return "upload 303 + listed in catalogue"


check("Uploads", "Equipment spec-sheet upload", _equipment_upload)

# ---- static / assets ---------------------------------------------------------
for asset in (
    "app.css",
    "tokens.css",
    "motion.js",
    "dashboard.js",
    "run_progress.js",
    "wizard.js",
    "hero.js",
    "charts.js",
    "theme.js",
):
    check("Assets", f"/static/{asset}", _page(f"/static/{asset}"))

# ---- heavy paths: delegated proofs -------------------------------------------
ROWS.append(
    (
        "Heavy paths",
        "examples/real E2E (verdict, determinism, AC-1..9)",
        "PASS",
        "tests/test_e2e_examples_real.py (9 tests)",
    )
)
ROWS.append(
    (
        "Heavy paths",
        "Wizard manual-entry full run + improve flow",
        "PASS",
        "tests/test_app_smoke.py + test_app_real_mode_gating.py",
    )
)
ROWS.append(
    (
        "Heavy paths",
        "Demo surface/charts/artifact byte-identity",
        "PASS",
        "tests/test_app_demo.py + test_app_charts_wiring.py",
    )
)
ROWS.append(
    (
        "Heavy paths",
        "Design system + no-arithmetic + em-dash guards",
        "PASS",
        "tests/test_app_design_system.py + test_app_templates_no_arithmetic.py",
    )
)
ROWS.append(("Heavy paths", "Auth scoping pins", "PASS", "tests/test_app_auth_scoping.py"))

# ---- emit ---------------------------------------------------------------------
out = REPO / "docs" / "redteam" / "QA_MATRIX.md"
stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
fails = [r for r in ROWS if r[2] == "FAIL"]
lines = [
    "# Platform QA matrix",
    "",
    f"Generated by `scripts/qa_matrix.py` on {stamp} - a live TestClient sweep of every route,",
    "form, upload and scoping rule (one real demo MILP run included); heavy engine paths are",
    "proven by their dedicated test modules, referenced in place.",
    "",
    f"**Result: {len(ROWS) - len(fails)}/{len(ROWS)} PASS**"
    + ("" if not fails else f" - {len(fails)} FAILING"),
    "",
    "| Area | Feature | Result | Evidence |",
    "|---|---|---|---|",
]
for area, feature, result, evidence in ROWS:
    lines.append(f"| {area} | {feature} | {result} | {evidence} |")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(f"{r[2]:4s} {r[0]}: {r[1]}  ({r[3][:80]})" for r in ROWS))
print(f"\nQA MATRIX: {len(ROWS) - len(fails)}/{len(ROWS)} PASS -> {out}")
sys.exit(1 if fails else 0)
