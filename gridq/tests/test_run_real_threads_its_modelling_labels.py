"""The no-JS `POST /run/real` route must carry its OWN modelling labels onto the record.

The route builds a `real_warnings` list while it decides how to obtain a half-hourly series
(`app/app.py:1093-1108`): the archetype/constrained modelling notes go in via
`_synthesise_profile_if_needed`, then the SYNTHETIC or MODELLED headline label is appended. That
list only reaches a reader if it is threaded into `generate_quote`'s `extra_warnings` at
`app/app.py:1131`. It once was not, and every no-JS run silently dropped the disclosure that its
load profile was modelled rather than metered.

**Why this file exists at all.** The threading was restored and reported as fixed, but reverting it
turned NO test red - not in `test_app_smoke`, `test_app_real_mode_gating`, `test_app_examples`, nor
in `test_app_asc_autofill_and_release_resolution`, which posts this very route twice. Under
`docs/validation_requirements.md` section 3 that is the repo's chronic defect verbatim: correct
code, passing tests, nothing that notices the wiring disappearing. These tests are the missing
notice: delete `*real_warnings` from `app/app.py:1131` and the three POSITIVE tests below fail.
The fourth stays green under that mutation, correctly - it is a negative control asserting the
labels are ABSENT on a metered run, so it guards the opposite failure (stamping the labels
unconditionally, which is otherwise a way to turn the other three green while lying).

**The decoy, which is the whole difficulty of writing this.** A `/run/real` run that models its
profile emits TWO warnings that both say "MODELLED":

1. `Half-hourly basis: MODELLED - reference profile app business-archetype, ...` - the Gate-2
   admission warning, produced inside the quote pipeline from the site's own
   `load_data.half_hourly_basis`, which `_mark_modelled_load` stamps on a SEPARATE limb. It
   **survives the mutation**.
2. `_SYNTHETIC_LABEL` / `_CONSTRAINED_LABEL` - the route's own customer-facing labels, which are
   the limb under test and are the only things that disappear.

So a prose match on "MODELLED", "SYNTHETIC" or "not metered" would pass against a dropped limb and
prove nothing. Every assertion below therefore tests exact membership of the **production
constants** imported from the app module, never a needle authored here.

Asserted at the layer a reader reads: the persisted `QuoteRecord.warnings`, the rendered run screen
(`GET /run/{quote_id}`), and the artifacts screen (`GET /run/{quote_id}/artifacts`) which renders
the deliverable pack. The pipeline is NOT stubbed - `generate_quote` runs for real, because the
point is that the value flows, not that the call happens (instance 4 of the seven).
"""

from __future__ import annotations

import importlib
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-run-real-labels-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import shown_once  # noqa: E402

from gridq.app import create_app  # noqa: E402

#: `gridq.app.__init__` re-exports the built FastAPI instance as ``app``, so a plain
#: ``import gridq.app.app`` binds the instance and not the module. Fetch the module itself, because
#: the labels under test are module-level constants and must be read from production, not retyped.
app_mod = importlib.import_module("gridq.app.app")


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

#: A no-JS real-form submission with NO meter file but a stated annual consumption: exactly the
#: case `_synthesise_profile_if_needed` models a business-archetype profile for, so the run is
#: labelled SYNTHETIC. (The route's docstring still calls this "a T0 run"; the record it actually
#: writes is T1_PARTIAL_HALF_HOURLY on a modelled basis, asserted below so the two cannot drift.)
ARCHETYPE_SUBMISSION: dict[str, str] = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}

#: The same intake carrying observed shape facts off the bill, which switches the modelling from
#: the archetype to the constrained generator and changes which label is due. The postcode differs
#: DELIBERATELY: `input_hash` does not yet pin half-hourly content (F-06/F-09), so two submissions
#: differing only in shape facts can collide on `quote_id` and the second save would shadow the
#: first, silently making one of these tests assert against the other's record.
CONSTRAINED_SUBMISSION: dict[str, str] = {
    **ARCHETYPE_SUBMISSION,
    "postcode": "BS1 5TH",
    "max_import_kw": "180",
    "day_kwh_share": "0.72",
    "peak_period": "afternoon",
}

#: The same intake WITH a meter file attached, so nothing is modelled and no modelling label is
#: due. The negative control: without it, "append the label unconditionally" would turn every test
#: above green while telling a metered site its own meter data was synthetic.
METERED_SUBMISSION: dict[str, str] = {**ARCHETYPE_SUBMISSION, "postcode": "BS1 5TJ"}


def _metered_csv() -> bytes:
    """Two days of plainly-metered half-hourly import, in the loader's canonical column names."""
    rows = ["timestamp,import_kw"]
    start = datetime(2025, 1, 1)
    for i in range(96):
        rows.append(f"{(start + timedelta(minutes=30 * i)).isoformat()},{40 + (i % 48) * 2}")
    return ("\n".join(rows) + "\n").encode()


def _submit(data: dict[str, str], *, meter_file: bytes | None = None) -> tuple[Any, str]:
    """POST the real form as a no-JS browser does; return the shown run and the run screen HTML.

    No DPA exists for the visitor's site, so the run is held for this visitor and never stored
    (WP-GRC-05, owner decision (a)): `shown_once` asserts both.
    """
    files = {"hh_file": ("meter.csv", meter_file, "text/csv")} if meter_file else None
    resp = client.post("/run/real", data=data, files=files, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:400]
    quote_id = resp.headers["location"].rsplit("/", 1)[-1]
    stored = shown_once(quote_id)
    page = client.get(f"/run/{quote_id}").text
    return stored, page


def test_a_modelled_no_js_run_tells_the_reader_its_load_profile_is_synthetic() -> None:
    """The archetype label survives from the route onto the record and onto the run screen."""
    stored, page = _submit(ARCHETYPE_SUBMISSION)

    # The run really did model a profile, so the label is genuinely due (guards against the test
    # passing for the wrong reason if the synthesis step is ever removed from this route).
    assert stored.site is not None
    assert stored.site.load_data.half_hourly_basis == "modelled"

    assert app_mod._SYNTHETIC_LABEL in stored.quote.warnings, (
        "POST /run/real modelled this site's load profile and then dropped its own SYNTHETIC "
        "label: the record cannot be distinguished from a metered run. NOTE the Gate-2 "
        "'Half-hourly basis: MODELLED' warning is a different limb and is still present here - "
        f"warnings on the record: {stored.quote.warnings}"
    )
    assert app_mod._SYNTHETIC_LABEL in page, (
        "the SYNTHETIC label reached the record but not the run screen the customer reads"
    )


def test_the_synthetic_label_reaches_the_deliverable_pack_too() -> None:
    """Second reader surface: the artifacts screen, which renders the pack from the record."""
    stored, _ = _submit(ARCHETYPE_SUBMISSION)
    artifacts = client.get(f"/run/{stored.quote.quote_id}/artifacts")
    assert artifacts.status_code == 200, artifacts.text[:400]
    assert app_mod._SYNTHETIC_LABEL in artifacts.text, (
        "the pack a reader downloads presents a modelled load profile without saying so"
    )


def test_a_constrained_no_js_run_carries_its_label_and_its_modelling_notes() -> None:
    """The other branch: bill-derived shape facts earn the MODELLED label plus per-note detail.

    Covered separately because `real_warnings` is a LIST, not one string: the constrained generator
    extends it with a note per modelling decision inside `_synthesise_profile_if_needed`, and those
    notes are the only record of how the shape was placed. A fix that threaded the headline label
    alone would still lose them.
    """
    stored, page = _submit(CONSTRAINED_SUBMISSION)

    assert app_mod._CONSTRAINED_LABEL in stored.quote.warnings, (
        f"the constrained-shape run dropped its MODELLED label: {stored.quote.warnings}"
    )
    assert app_mod._CONSTRAINED_LABEL in page
    assert app_mod._SYNTHETIC_LABEL not in stored.quote.warnings, (
        "the archetype label was applied to a constrained-shape run: the two describe different "
        "provenance and are not interchangeable"
    )

    notes = [w for w in stored.quote.warnings if w.startswith("Modelled load profile:")]
    assert notes, (
        "the constrained generator's own modelling notes (day-band share, peak placement, "
        "residual re-balancing) never reached the record, so how the shape was built is "
        f"undisclosed: {stored.quote.warnings}"
    )
    assert "Modelled load profile:" in page


def test_a_run_on_the_customer_s_own_meter_data_is_given_no_modelling_label() -> None:
    """Negative control: the labels track the actual basis, they are not stamped unconditionally."""
    stored, _ = _submit(METERED_SUBMISSION, meter_file=_metered_csv())
    assert stored.site is not None
    assert stored.site.load_data.half_hourly_basis != "modelled", (
        "the meter file was ignored and a profile was modelled over it (Gate 2: real HH data is "
        "never displaced by generated data)"
    )
    assert app_mod._SYNTHETIC_LABEL not in stored.quote.warnings
    assert app_mod._CONSTRAINED_LABEL not in stored.quote.warnings
