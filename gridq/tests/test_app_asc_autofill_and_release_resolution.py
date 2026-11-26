"""Two live defects on the app's own production run paths, pinned where a READER sees them.

**1. The wizard invented an Agreed Supply Capacity.** `POST /api/run` ran
``if data.get("mic_kw") and not data.get("asc_kw"): data["asc_kw"] = data["mic_kw"]`` before
building the `SiteModel`. MIC and ASC are two DIFFERENT contractual capacities - the DNO's maximum
import capacity, and the agreed/available supply capacity the GBP/kVA/day availability charge is
billed on - so copying one into the other wrote a contractual fact the visitor never supplied, with
no `DerivedAssumption`, no warning and nothing on the record (D45: never invent silently). It also
contradicted this repo's own doctrine for that exact field, printed in the ranked data ask at
``ingestion/missing_data.py:139-141``: "confirm the actual MIC/ASC with the DNO (never
auto-filled)". Two reader surfaces then lied: the ASC leg of the ``connection_mic_mec_asc`` ask
disappeared from the run screen, and the underwriting pack printed a figure for ASC
(``outputs/consulting_report.py:911``) instead of "no agreed supply capacity on the site record".
The autofill is deleted; these tests go red if it comes back.

**2. The app was pinned five releases behind the library.** ``ASSUMPTION_VERSION = "gb_v1_4"`` was
read at every one of the app's three `generate_quote` call sites while `latest_version()` resolved
``gb_v1_9``. That is MD-F-15 through a second door: the CLI fix made `gridq run` resolve the newest
release from disk and warn on a superseded pin (`cli.py:248-265`), and nobody checked the app, so a
web run and a CLI run of the SAME site could disagree on viability with nothing on either record
saying why - gb_v1_7 alone re-derived the degradation cost from an unsourced illustrative
5.0 GBP/MWh to the levelised 21.3 GBP/MWh, either side of a real site's cycle break-even.

Both halves are asserted through the production routes (`POST /api/run`, `POST /run/real`) and read
back off the stamped record and the rendered pages, never off the helper functions alone.
"""

from __future__ import annotations

import os
import tempfile
import time
from types import SimpleNamespace
from typing import Any

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-asc-release-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import shown_once  # noqa: E402

import gridq.quotes as quotes_mod  # noqa: E402
from gridq.app import create_app  # noqa: E402
from gridq.assumptions import latest_version  # noqa: E402
from gridq.governance.dpa_guard import DEMO_SITE_ID  # noqa: E402
from gridq.outputs import generate_artifacts  # noqa: E402

#: `gridq.app.__init__` re-exports the built FastAPI instance under the name ``app``, so a plain
#: ``import gridq.app.app`` binds the instance, not the module. Fetch the module itself.
app_mod = importlib.import_module("gridq.app.app")


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

#: The wizard's own fields with ASC left blank - exactly the submission the autofill invented an
#: ASC for. ``grid_connection_kva`` is the wizard's connection field (mapped to ``mic_kw`` by the
#: route), so the MIC here is genuinely supplied and the ASC genuinely is not.
WIZARD_FORM: dict[str, str] = {
    "postcode": "BS1 5TF",
    "grid_connection_kva": "500",
    "annual_consumption_kwh": "900000",
    "max_import_kw": "420",
    "legal_name": "Wizard Intake Ltd",
    "company_number": "04567890",
    "business_type": "manufacturing",
    "tariff_type": "Half-Hourly C&I (DUoS RAG)",
}

#: A minimal valid site for the synchronous real form, used here only to get a real record cheaply
#: (the tariff is blank, so the run degrades to insufficient_data fast enough to assert on).
#: The name is historical and no longer literal: `/run/real` now models a profile from the stated
#: annual consumption, so the record this writes is **T1** on a MODELLED half-hourly basis, not T0.
#: The labels that disclose that modelling are guarded in
#: `tests/test_run_real_threads_its_modelling_labels.py`, not here - nothing below reads them.
T0_SITE: dict[str, str] = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}


def _wait_terminal(job_id: str, tries: int = 400) -> dict[str, Any]:
    for _ in range(tries):
        status = client.get(f"/api/run/{job_id}").json()
        if status.get("status") in ("done", "error"):
            return status
        time.sleep(0.02)
    raise AssertionError(f"run job {job_id} never reached a terminal state")


def _capture_pipeline(monkeypatch: pytest.MonkeyPatch, rich_quote: Any) -> dict[str, Any]:
    """Stub the expensive tail and record exactly what the route handed `generate_quote`.

    All three app run paths import `generate_quote` at call time, so patching the module attribute
    reaches every one of them. The stub returns the illustrative fixture record so the route's own
    save/redirect still runs; nothing upstream of the call - form parsing, `_apply_reference_data`,
    `_site_from_form`, the release resolution - is stubbed.
    """
    captured: dict[str, Any] = {}

    def fake_generate_quote(site: Any, assumptions: Any, **kwargs: Any) -> Any:
        captured["site"] = site
        captured["assumptions"] = assumptions
        captured["extra_warnings"] = list(kwargs.get("extra_warnings") or [])
        return SimpleNamespace(quote=rich_quote.quote, site=site, telemetry=None)

    monkeypatch.setattr(quotes_mod, "generate_quote", fake_generate_quote)
    return captured


def _submit_wizard(
    monkeypatch: pytest.MonkeyPatch, rich_quote: Any, form: dict[str, str]
) -> tuple[dict[str, Any], str]:
    """Drive `POST /api/run`; return what the route handed the pipeline + the stored quote id."""
    captured = _capture_pipeline(monkeypatch, rich_quote)
    # Unrelated to either defect: the illustrative fixture record carries no dispatch profile and
    # no sizing grid, so the job's own completeness check would refuse to save it.
    monkeypatch.setattr(app_mod, "_quote_complete", lambda quote: True)

    resp = client.post("/api/run", data=form)
    assert resp.status_code == 200, resp.text[:400]
    status = _wait_terminal(resp.json()["job_id"])
    assert status["status"] == "done", status
    return captured, status["quote_id"]


def _run_real_t0() -> str:
    """Submit the T0 site through the synchronous real form and return its quote id."""
    resp = client.post("/run/real", data=T0_SITE, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:400]
    return resp.headers["location"].rsplit("/", 1)[-1]


# --------------------------------------------------------------------------------------------
# 1. ASC is never invented from MIC
# --------------------------------------------------------------------------------------------


def test_wizard_run_never_invents_an_agreed_supply_capacity(
    monkeypatch: pytest.MonkeyPatch, rich_quote: Any
) -> None:
    """A blank ASC stays blank all the way to the record, the run screen and the pack."""
    captured, quote_id = _submit_wizard(monkeypatch, rich_quote, WIZARD_FORM)

    site = captured["site"]
    assert site.connection.mic_kw == 500.0, "the visitor's own MIC must survive untouched"
    assert site.connection.asc_kw is None, (
        "POST /api/run invented an ASC from the MIC. MIC and ASC are different contractual "
        "capacities and ASC feeds a priced availability charge; a value the customer never "
        "supplied must be absent (or a labelled DerivedAssumption), never silently copied."
    )
    # Nothing was derived-and-declared either, so no register entry may claim one was.
    assert not [d for d in site.assumptions_requiring_confirmation if d.key == "connection.asc_kw"]

    stored = shown_once(quote_id)  # no DPA: shown, never stored (WP-GRC-05, decision (a))
    assert stored.site is not None
    assert stored.site.connection.asc_kw is None

    # READER SURFACE 1 - the run screen's ranked data-asks panel still ASKS for the ASC. The
    # autofill retired this ask silently, which is the whole harm: the reader stopped being told.
    inspect = client.get(f"/run/{quote_id}").text
    assert "Sharpen this quote" in inspect
    assert "ASC band" in inspect, "the ranked data ask stopped asking for the ASC"

    # READER SURFACE 2 - the underwriting pack states the absence rather than printing a figure
    # for a capacity nobody agreed. Rendered through the same `generate_artifacts` the app's
    # /run/{id}/artifacts route calls, with the same record and site.
    pack = generate_artifacts(stored.quote, stored.site).consulting_report_html
    assert "no agreed supply capacity on the site record" in pack, (
        "the pack printed an ASC figure for a site that never supplied one"
    )


def test_a_supplied_asc_is_still_carried_through_untouched(
    monkeypatch: pytest.MonkeyPatch, rich_quote: Any
) -> None:
    """Deleting the autofill must not lose a real ASC the visitor did type in."""
    captured, quote_id = _submit_wizard(
        monkeypatch, rich_quote, {**WIZARD_FORM, "asc_kw": "480", "postcode": "BS1 5TG"}
    )
    assert captured["site"].connection.asc_kw == 480.0

    stored = shown_once(quote_id)  # no DPA: shown, never stored (WP-GRC-05, decision (a))
    assert stored.site is not None
    pack = generate_artifacts(stored.quote, stored.site).consulting_report_html
    assert "no agreed supply capacity on the site record" not in pack


# --------------------------------------------------------------------------------------------
# 2. The assumption release is resolved from disk, and a superseded pin is declared
# --------------------------------------------------------------------------------------------


def test_wizard_run_is_priced_against_the_newest_release_on_disk(
    monkeypatch: pytest.MonkeyPatch, rich_quote: Any
) -> None:
    """The library object the job path hands the pipeline is the newest release, resolved on disk.

    Compared against `latest_version()` rather than a literal, so the assertion keeps working when
    gb_v2_0 lands instead of pinning this test to today's newest release.
    """
    captured, _ = _submit_wizard(monkeypatch, rich_quote, WIZARD_FORM)
    assert captured["assumptions"].metadata.version == latest_version()


def test_the_demo_route_also_resolves_the_release_from_disk(
    monkeypatch: pytest.MonkeyPatch, rich_quote: Any
) -> None:
    """The third `generate_quote` call site (`POST /run/demo`) resolves from disk too.

    Covered separately because a re-pin at one call site only is exactly the shape of defect this
    repo keeps shipping: fixed in two places, still live in the third.
    """
    # The curated demo is the one run stored without a DPA, under the demo's own attested site id
    # (WP-GRC-05): the stub's record must carry that id or the store rightly refuses it.
    demo = SimpleNamespace(quote=rich_quote.quote.model_copy(update={"site_id": DEMO_SITE_ID}))
    captured = _capture_pipeline(monkeypatch, demo)
    resp = client.post("/run/demo", follow_redirects=False)
    assert resp.status_code == 303, resp.text[:400]
    assert captured["assumptions"].metadata.version == latest_version()


def test_the_release_the_record_states_is_the_newest_release_on_disk() -> None:
    """Reader layer: the release stamped on a real record (and shown on the page) is the newest."""
    quote_id = _run_real_t0()
    stored = shown_once(quote_id)  # no DPA: shown, never stored (WP-GRC-05, decision (a))
    assert stored.quote.assumption_version == latest_version(), (
        "the app priced against a different release than `gridq run` would for the same site"
    )
    assert latest_version() in client.get(f"/run/{quote_id}").text


def test_an_explicitly_pinned_superseded_release_is_declared_on_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning an old release stays possible, but it can never pass as the current library."""
    monkeypatch.setattr(app_mod, "ASSUMPTION_VERSION_PIN", "gb_v1_4")
    quote_id = _run_real_t0()
    stored = shown_once(quote_id)  # no DPA: shown, never stored (WP-GRC-05, decision (a))
    assert stored.quote.assumption_version == "gb_v1_4"

    warned = [w for w in stored.quote.warnings if "SUPERSEDED ASSUMPTION RELEASE" in w]
    assert warned, f"a superseded pin was priced silently: {stored.quote.warnings}"
    assert "gb_v1_4" in warned[0] and latest_version() in warned[0]
    # And it reaches the reader, not just the record.
    assert "SUPERSEDED ASSUMPTION RELEASE" in client.get(f"/run/{quote_id}").text


def test_the_shipped_app_carries_no_hard_coded_release_pin() -> None:
    """The shipped default is 'resolve from disk'. Re-pinning it is the defect, not the fix."""
    assert app_mod.ASSUMPTION_VERSION_PIN is None
