"""Real-mode honest-gating tests (W5-1).

When a real site is missing inputs, the UI must surface the ranked data-asks and degrade honestly
(insufficient_data / not bankable) — never fabricate a value. These use the fast T0
path (no meter file → the dispatch MILP solves nothing, by design).
"""

from __future__ import annotations

import os
import tempfile

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-gating-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import shown_once  # noqa: E402

from gridq.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

T0_SITE = {
    "postcode": "BS1 5TF",
    "mic_kw": "300",
    "annual_consumption_kwh": "420000",
    "legal_name": "Riverside Bakery Ltd",
    "company_number": "04567890",
}


def _run(data: dict[str, str]) -> str:
    resp = client.post("/run/real", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:300]
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_missing_inputs_surface_ranked_data_asks() -> None:
    quote_id = _run(T0_SITE)
    text = client.get(f"/run/{quote_id}").text
    assert "Sharpen this quote" in text  # the ranked data-asks panel
    # The headline ask for a T0 site is the 12-month half-hourly series.
    assert "half-hourly" in text.lower()


def test_degrades_without_inventing_numbers() -> None:
    quote_id = _run(T0_SITE)
    stored = shown_once(quote_id)  # no DPA: shown, never stored (WP-GRC-05, decision (a))
    result = stored.quote.optimisation_result
    # No meter file → no dispatch → no spec, honestly; nothing bankable, no priced floor invented.
    assert result.solver_status.value == "insufficient_data"
    assert result.selected_power_kw is None
    assert result.bankable_status.value == "not_bankable"
    assert all(v.annual_revenue_gbp == 0.0 for v in result.value_streams)
    # The site carries ranked data-asks rather than guessed fields.
    assert stored.site is not None and stored.site.missing_data


def test_required_fields_rejected_with_message_not_500() -> None:
    resp = client.post("/run/real", data={"annual_consumption_kwh": "420000"})  # no postcode/mic
    assert resp.status_code == 400
    assert "required" in resp.text.lower()
