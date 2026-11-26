"""A run carries a dispatch day whichever branch it takes, so the pack can draw how it traded.

The defect (Olly, 2026-08-18). `DispatchProfile`, its builder
(`optimisation/schedule.py:439 representative_day_profile`) and its renderer
(`charts.firming_diagram`, reached from `consulting_report._dispatch_exhibits`) have existed since
Wave 5. But `MilpEngine.last_dispatch_profile()` reads `self._dispatch_schedule`, which was only
assigned on the CHOSEN-candidate path. On a no-spec run it stayed None, so
`QuoteRecord.dispatch_profile` was None and every campaign pack rendered the dispatch exhibit as
unavailable - while `_no_spec_dispatch_evidence` had already re-solved the best candidate on a fresh
solver and was holding its full half-hourly schedule the whole time.

So the dispatch was solved, run and measured, and the reader was told it was unavailable: the same
measured-versus-unavailable confusion D45 exists to prevent, one exhibit further on.

These tests assert on the RECORD the renderer reads, and on the rendered pack bytes, because that is
the layer the reader sees. The record is built by running the real engine over a real site file, not
by loading a `runs/` directory - `runs/` is gitignored, so such a test would pass vacuously on a
fresh checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.assumptions import latest_version, load_assumptions
from gridq.optimisation.engine import MilpEngine
from gridq.quotes.pipeline import generate_quote

_SITE = Path("examples/real/sites/bishops_cleeve_ash_plant.json")


@pytest.fixture(scope="module")
def dispatch_run():
    """A real run on the smallest campaign site (fastest MILP in the set), either branch."""
    site = s.SiteModel.model_validate(json.loads(_SITE.read_text()))
    lib = load_assumptions(latest_version())
    run = generate_quote(site, lib, engine=MilpEngine())
    # NOTE (Olly, 2026-08-19): written when NO site selected a spec, this asserted
    # `selected_power_kw is None` as its premise. Under D57 the streams are open and
    # sites now DO select, so that premise broke - a success, not a regression. The
    # property under test was never really "no spec": it is that A DISPATCH DAY
    # REACHES THE RECORD EITHER WAY, because the renderer draws from
    # `quote.dispatch_profile` on both branches. Premise recorded, not asserted.
    return run


def test_the_record_carries_a_dispatch_day_on_either_branch(dispatch_run) -> None:
    """The fix: the profile reaches the record the renderer reads."""
    profile = dispatch_run.quote.dispatch_profile

    assert profile is not None, (
        "a no-spec run measured a dispatch but persisted no profile, so the pack cannot draw it"
    )
    assert profile.points, "a dispatch profile with no half-hours is not a dispatch day"
    # A real day, not an average: 48 half-hours in period order.
    assert len(profile.points) == 48
    assert [p.period for p in profile.points] == list(range(48))


def test_the_dispatch_day_is_a_real_solved_day_not_a_placeholder(dispatch_run) -> None:
    """Every half-hour carries solved values, and the battery actually moves on this day."""
    profile = dispatch_run.quote.dispatch_profile
    assert profile is not None

    # The day was chosen deterministically and the record says which day and on what basis.
    assert profile.selection_basis
    assert profile.representative_day_index >= 0

    # It discharges: this is the day the strategy is most legible, so it cannot be all zeros.
    assert sum(p.discharge_kw for p in profile.points) > 0
    # And it charges to do so - energy is bought, never conjured.
    assert sum(p.charge_kw for p in profile.points) > 0
    # State of energy stays inside the physical envelope.
    assert all(p.soe_kwh >= 0 for p in profile.points)
    # The tariff context the MILP traded against is present on every half-hour.
    assert all(p.duos_rate_gbp_per_kwh is not None for p in profile.points)
    assert any(p.red_band for p in profile.points), "no red band on the most-active day is suspect"


def test_the_dispatch_exhibit_reaches_the_rendered_pack(dispatch_run) -> None:
    """Assert at the layer a reader reads: the exhibit is drawn, not reported unavailable."""
    from gridq.outputs.consulting_report import render_report_for_quote

    html = render_report_for_quote(dispatch_run.quote, dispatch_run.site)

    assert "<svg" in html
    # The pack must NOT tell the reader the dispatch could not be shown, now that it can.
    assert "no dispatch solved" not in html.lower()
    # House style on anything a client reads.
    assert "—" not in html and "–" not in html
