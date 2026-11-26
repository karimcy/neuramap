"""D-116 (WP-UW-16): the availability leg is tapered by a SOURCED ratio, or labelled gross.

The ENA Monthly Utilisation Performance Factor claws back the availability payment when the asset
under-delivers instructed utilisation. `local_flex_availability_value` applies it only when it is
handed a ``utilisation_performance_ratio``; with none, it holds the factor at 1.0 and the leg is an
UPPER BOUND. No assumption release carries a sourced ratio, and none may be invented, so the
acceptance rule is: the ratio comes from a sourced assumption on every awarded site, or the leg is
labelled gross in ``quote.warnings``.

One library read decides both halves: `gb_streams.sourced_utilisation_performance_ratio` feeds the
price (`valuation._local_flex_value`) and `gb_streams.local_flex_gross_label` (the quote label), so
the label cannot claim gross on a tapered leg or the reverse.

Each test names the call site whose deletion turns it red.
"""

from __future__ import annotations

from dataclasses import replace as _replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from tests._milp_support import multi_rate_site, synthetic_local_flex_award, write_hh_csv

import gridq.optimisation.valuation as valuation
import gridq.schemas as s
import gridq.value_stack.gb_streams as gb_streams
from gridq.app.demo import load_demo_site
from gridq.assumptions import Assumption, AssumptionStatus, load_assumptions
from gridq.optimisation.engine import MilpEngine
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.quotes import generate_quote
from gridq.value_stack.gb_streams import (
    LOCAL_FLEX_GROSS_LABEL,
    local_flex_gross_label,
    payable_delivery_fraction,
    sourced_utilisation_performance_ratio,
)

_REPO = Path(__file__).resolve().parents[1]
_NOT_APPLIED = "Monthly Utilisation Performance Factor NOT applied"
_LIB = "gb_v1_12"


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> s.SiteModel:
    """A 7-day January series in the SSEN SEPD licence area, with NO award on the record."""
    path = tmp_path_factory.mktemp("d116") / "january.csv"
    rows, _ts = write_hh_csv(path, days=7)
    base = multi_rate_site(path, rows=rows)
    return base.model_copy(
        update={"connection": base.connection.model_copy(update={"dno_region": "SSEN_SEPD"})}
    )


@pytest.fixture(scope="module")
def awarded(synthetic: s.SiteModel) -> s.SiteModel:
    """The same series carrying a SYNTHETIC attested award (200 kW, 1-year tenor, SEPD window).

    The labelling rule is about structure (an awarded site, priced gross unless a sourced ratio
    exists), so no customer record is needed: this replaced Harwell's own award, read from a
    quarantined file (WP-GRC-05), with `tests._milp_support.synthetic_local_flex_award`.
    """
    return synthetic.model_copy(update={"local_flex_awards": [synthetic_local_flex_award()]})


def _quote_warnings(site: s.SiteModel) -> tuple[list[str], s.ValueStreamResult | None]:
    run = generate_quote(
        site, load_assumptions(_LIB), engine=MilpEngine(use_two_stage_search=False)
    )
    flex = next(
        (v for v in run.result.value_streams if v.stream == s.MarketStream.LOCAL_FLEX_PICLO), None
    )
    return list(run.quote.warnings), flex


def test_awarded_quote_labels_the_leg_gross(awarded: s.SiteModel) -> None:
    """T13: `generate_quote` on an awarded site carries the gross label in ``quote.warnings``.

    Vacuity guard: the local-flex stream is priced (a label on a £0 leg would prove nothing).
    Dies if the pipeline's `local_flex_gross_label` call is deleted.
    """
    warnings, flex = _quote_warnings(awarded)
    assert flex is not None and flex.annual_revenue_gbp > 0.0
    assert warnings.count(LOCAL_FLEX_GROSS_LABEL) == 1, (
        "an awarded site's availability leg is priced gross, and the quote must say so once"
    )


def test_unawarded_quote_carries_no_gross_label(synthetic: s.SiteModel) -> None:
    """T14: no award, no availability leg, no label: on the demo site and on a real quote.

    Dies if the ``local_flex_awards`` guard in `local_flex_gross_label` is removed.
    """
    lib = load_assumptions(_LIB)
    demo = load_demo_site()
    assert not demo.local_flex_awards, "premise: the demo site holds no award"
    assert local_flex_gross_label(demo, lib) is None
    assert not synthetic.local_flex_awards
    warnings, _flex = _quote_warnings(synthetic)
    assert LOCAL_FLEX_GROSS_LABEL not in warnings


def _flex_schedule() -> HalfHourlyDispatchSchedule:
    """A minimal solved-shape schedule with in-window volumes (as the award module builds it)."""
    n = 48
    return HalfHourlyDispatchSchedule(
        power_kw=250.0,
        energy_kwh=500.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=np.zeros(n),
        discharge_kw=np.zeros(n),
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 50.0),
        duos_rate_gbp_per_kwh=np.zeros(n),
        red_band_mask=np.zeros(n, dtype=bool),
        tender_window_charge_mwh=5.0,
        tender_window_discharge_mwh=30.0,
    )


def _availability_net(lib, site) -> tuple[float, list[str]]:
    """The availability leg's NET £, isolated by difference (an empty reservation bills none)."""
    schedule = _flex_schedule()
    full, _u, notes = valuation._local_flex_value(schedule, lib, site)
    unreserved, _u2, _n2 = valuation._local_flex_value(
        _replace(schedule, local_flex_reserved_kw=()), lib, site
    )
    return full - unreserved, notes


def test_a_sourced_ratio_is_applied_and_removes_the_label(
    awarded: s.SiteModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T15: the day a sourced ratio exists it tapers the price AND retires the label.

    A ratio of 0.9 is injected through the one accessor (in both modules that read it); it is a
    test input, never a library value. The availability leg must equal the gross leg times
    ``payable_delivery_fraction(0.9)`` (0.80 on the ENA taper), the "NOT applied" note must go,
    and `local_flex_gross_label` must return None. Dies if `valuation._local_flex_value` stops
    passing ``utilisation_performance_ratio=`` to `local_flex_availability_value`.
    """
    lib = load_assumptions(_LIB)
    site = awarded
    gross, gross_notes = _availability_net(lib, site)
    assert gross > 0.0 and any(_NOT_APPLIED in n for n in gross_notes)
    assert local_flex_gross_label(site, lib) == LOCAL_FLEX_GROSS_LABEL

    monkeypatch.setattr(gb_streams, "sourced_utilisation_performance_ratio", lambda _lib: 0.9)
    monkeypatch.setattr(valuation, "sourced_utilisation_performance_ratio", lambda _lib: 0.9)
    mupf = payable_delivery_fraction(0.9)
    assert mupf == pytest.approx(0.80), "premise: 0.9 sits on the taper, below the 5% grace band"
    tapered, tapered_notes = _availability_net(lib, site)
    assert tapered == pytest.approx(gross * mupf, rel=1e-12)
    assert not any(_NOT_APPLIED in n for n in tapered_notes)
    assert local_flex_gross_label(site, lib) is None


@pytest.mark.parametrize(
    "release", sorted(p.stem for p in (_REPO / "assumptions").glob("gb_v*.yaml"))
)
def test_no_release_carries_an_unsourced_ratio(release: str) -> None:
    """T16: a ratio reaches the price only with its provenance: None, or CONFIRMED and sourced.

    Today every release returns None. If a ratio is ever released and applied, its entry must be
    CONFIRMED with a non-empty source (D68): an unsourced ratio is exactly the fabricated number
    D-116 forbids, and it would silently retire the gross label.
    """
    lib = load_assumptions(release)
    ratio = sourced_utilisation_performance_ratio(lib)
    if ratio is None:
        return
    entry = lib.get("local_flex_piclo", "utilisation_performance_ratio")
    assert entry.status is AssumptionStatus.CONFIRMED, f"{release}: an unconfirmed ratio applied"
    assert entry.source.strip(), f"{release}: a utilisation performance ratio with no source"
    assert 0.0 <= ratio <= 1.0


def _ratio_entry(status: AssumptionStatus, source: str) -> Assumption:
    """A 0.9 ratio entry for T17: a test input, never a library value."""
    return Assumption(
        value=0.9,
        unit="ratio",
        source=source,
        status=status,
        owner_or_reviewer="test",
        confidence=s.Confidence.LOW,
        last_reviewed=date(2026, 11, 26),
        notes="T17 test input",
    )


def test_a_placeholder_ratio_is_not_applied_and_keeps_the_label(
    awarded: s.SiteModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T17: only a CONFIRMED, sourced entry counts; anything else leaves the leg gross (D68).

    The library read (`gb_streams._maybe`) is made to return a 0.9 entry. A placeholder, a
    needs-review entry and a confirmed entry with a blank source must each read as None and keep
    the gross label. The positive control (confirmed AND sourced) must read 0.9 and drop the label,
    so the injection is proved to reach the accessor. Dies if the status or source check in
    `sourced_utilisation_performance_ratio` is deleted.
    """
    lib = load_assumptions(_LIB)
    site = awarded
    cases = {
        "placeholder": _ratio_entry(AssumptionStatus.PLACEHOLDER, "a cited source"),
        "needs_review": _ratio_entry(AssumptionStatus.NEEDS_REVIEW, "a cited source"),
        "confirmed, blank source": _ratio_entry(AssumptionStatus.CONFIRMED, "   "),
    }
    for label, entry in cases.items():
        monkeypatch.setattr(gb_streams, "_maybe", lambda _l, _s, _n, _e=entry: _e)
        assert sourced_utilisation_performance_ratio(lib) is None, f"{label}: ratio applied"
        assert local_flex_gross_label(site, lib) == LOCAL_FLEX_GROSS_LABEL, f"{label}: label lost"

    sourced = _ratio_entry(AssumptionStatus.CONFIRMED, "DNO settlement statement (test input)")
    monkeypatch.setattr(gb_streams, "_maybe", lambda _l, _s, _n: sourced)
    assert sourced_utilisation_performance_ratio(lib) == pytest.approx(0.9)
    assert local_flex_gross_label(site, lib) is None
