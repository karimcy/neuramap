"""Demo-mode tests (W5-1): the real engine on the labelled synthetic dataset.

Prove the demo runs end-to-end, produces a populated sizing surface (a covenant-feasible region and
a marked optimum), renders all three artifacts, stays honestly **not bankable** and clearly labelled
illustrative, and that every figure traces to the stamped `QuoteRecord` (the served artifact is
byte-identical to a fresh render off the record). One MILP run for the whole module.
"""

from __future__ import annotations

import html as html_lib
import os
import re
import tempfile

import pytest

# Pin the run store for this module. Set at import (for the module-level demo run) and re-pinned
# before every test (other test modules also set this env; store reads it lazily at call time).
MODULE_STORE = tempfile.mkdtemp(prefix="gridq-demo-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402

from gridq.app import create_app, store  # noqa: E402
from gridq.app.demo import DEMO_LABEL  # noqa: E402
from gridq.outputs import generate_artifacts  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


client = TestClient(create_app())

# Run the demo once for the module (a real MILP solve over the synthetic load).
_resp = client.post("/run/demo", follow_redirects=False)
assert _resp.status_code == 303
QUOTE_ID = _resp.headers["location"].rsplit("/", 1)[-1]
STORED = store.load_run(QUOTE_ID)
assert STORED is not None


def test_sizing_grid_is_populated_with_a_covenant_feasible_confirmed_optimum() -> None:
    """The two-stage cloud persists (D32/D34) with a real z-axis, and on the D36-coherent cost
    stack the demo-scale site is genuinely viable: a covenant-feasible optimum exists, is
    full-series confirmed (never a proxy), and is what the result reports."""
    grid = STORED.quote.sizing_grid
    # The two-stage continuous search (D32/D34): at least the full coarse sweep persists.
    assert grid is not None and len(grid.points) >= 80
    assert grid.search_version == "m4-two-stage-v2"
    # The surface has a real z-axis (levered returns computed with the demo's illustrative debt).
    assert any(p.levered_return_pct is not None for p in grid.points)
    # A genuine P90-DSCR-feasible optimum exists and is a confirmed (non-proxy) evaluation.
    assert grid.selected is not None and grid.selected.passes_covenant
    assert grid.selected.stage == "confirm" and not grid.selected.proxy
    # The result reports exactly that selection.
    assert STORED.quote.optimisation_result.selected_power_kw == grid.selected.power_kw
    assert STORED.quote.optimisation_result.duration_hours == grid.selected.duration_hours


def test_demo_is_labelled_illustrative_and_not_bankable() -> None:
    assert DEMO_LABEL in STORED.quote.warnings
    assert STORED.quote.optimisation_result.bankable_status.value == "not_bankable"
    inspect = client.get(f"/run/{QUOTE_ID}").text
    assert "DEMO" in inspect and "ILLUSTRATIVE" in inspect.upper()
    assert "NOT BANKABLE" in inspect


def test_three_artifacts_render_and_match_the_record() -> None:
    page = client.get(f"/run/{QUOTE_ID}/artifacts").text
    match = re.search(r'srcdoc="(.*?)"></iframe>', page, re.S)
    assert match, "artifacts screen is missing the embedded report"
    served = html_lib.unescape(match.group(1))
    # Byte-identical to a fresh render off the stored record → the UI authors no number.
    fresh = generate_artifacts(STORED.quote, STORED.site).report_html
    assert served == fresh
    low = served.lower()
    assert "<!doctype html>" in low and "underwriting" in low and "proposal" in low


def test_surface_island_present_for_the_client_renderer() -> None:
    inspect = client.get(f"/run/{QUOTE_ID}").text
    assert "surface-data" in inspect and "Sizing surface" in inspect
