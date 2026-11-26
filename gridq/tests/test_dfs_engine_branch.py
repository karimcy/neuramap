"""D57 item 4 — the DFS ENGINE BRANCH, which did not exist.

``grep MarketStream.DFS src/gridq/optimisation/`` returned NOTHING before this change: no objective
term, no schedule field, no valuation branch. NESO's Demand Flexibility Service is the ONLY
direct NESO revenue route open to an asset below 1 MW, and every campaign site is sub-MW, so the
missing branch meant the model concluded that a 0.1-1 MW behind-the-meter battery earns zero NESO
revenue — false since the 9 April 2026 service expansion, and false in the direction that matters.

Four things had to be true for a DFS pound to reach a report, and each test below dies if one of
them is undone:

* ``dfs_service_period_mask`` must place NESO's published 236 procured settlement periods on the
  metered horizon — the SHAPE the MILP dispatches against.
* ``milp_solver._solve_milp`` must add that price array to the objective, so the optimiser can
  choose to deliver into a called period.
* ``HalfHourlyDispatchSchedule.dfs_price_gbp_per_kwh`` must survive onto the schedule, and
  ``dfs_utilisation_value_gbp`` must report the objective's own DFS term.
* ``valuation.build_value_streams`` must have a ``MarketStream.DFS`` branch that reads it, and the
  DFS gate must be OPEN — a CLOSED gate makes ``_priceable`` False and the branch is never reached.

**The double-book this suite guards against.** NESO publishes BOTH a settlement formula and a
per-1-MW annual scenario set (£7,558 central / £21,009 ceiling per MW per season). Those are two
estimates of the SAME money. Pricing the annuity AND the dispatch-measured settlement would book the
same pound twice, which is the one rule D57 explicitly does not relax, so the scenarios are quoted
BESIDE the measured £ as a published cross-check and never added to it.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import Firmness, MarketStream
from gridq.schemas.market import is_priced_floor
from gridq.value_stack.gb_streams import (
    _DFS_ZONE_OBSERVED,
    dfs_service_period_mask,
    dfs_utilisation_rate_gbp_per_kwh,
    dfs_zone_for_site,
)

LIB = load_assumptions("gb_v1_12")
#: The shared MILP fixture site sits in postcode district LE17, which resolves to DFS zone 6.
FIXTURE_ZONE = 6


# --- the SHAPE ---------------------------------------------------------------


def test_the_mask_reproduces_the_measured_2026_27_call_count_and_window() -> None:
    """The zone's OWN OBSERVED settlement-period count, and its own window shape.

    **Two corrections live in this one assertion (2026-08-19, owner instruction).** The first cut of
    this mask used the library's 236 procured settlement periods from winter 2024/25, a season that
    predates the 9 April 2026 zonal redesign. The second used zone 6's 407 accepted periods
    ANNUALISED by 365/127 to 1,170 a year, so 65% of the modelled calls were an extrapolation of a
    spring-and-summer observation across autumn and winter. What is modelled now is the count that
    was OBSERVED, for the site's own zone.

    Dies if the observed count is annualised again, if the histogram is replaced, or if the horizon
    scaling changes.
    """
    idx = pd.date_range("2025-08-13", periods=17_520, freq="30min")
    mask, notes = dfs_service_period_mask(idx, zone=FIXTURE_ZONE, assumptions=LIB)
    observed = _DFS_ZONE_OBSERVED[FIXTURE_ZONE].accepted_settlement_periods
    assert int(mask.sum()) == observed == 407
    # The superseded annualisation is 2.87x this count, and must not come back.
    assert round(observed * 365.0 / 127.0) == 1_170
    text = " ".join(notes)
    assert "DFS SERVICE PERIODS MODELLED" in text
    assert "SCALED TO THE OBSERVED PERIODS, AND THE DIRECTION OF EFFECT IS DOWN" in text
    # The source's own facts and the modelling choice are labelled separately.
    assert "OBSERVED COUNT" in text
    assert "SHAPE, NOT SOURCED" in text
    called = idx[mask]
    # THE MEASURED WINDOW: nothing before 11:00 and nothing after 23:00, in four months of data.
    assert min(called.hour) >= 11
    assert max(called.hour) <= 22
    # The mass follows the zone's own histogram: the 19:00-19:30 peak slots dominate the tails.
    per_slot = Counter(zip(called.hour, called.minute, strict=True))
    assert per_slot[(19, 0)] > per_slot[(16, 0)] * 4
    assert per_slot[(19, 30)] == max(per_slot.values())
    # And it is year-round, because the zonal service is: April-August alone carried 70 events.
    assert len(set(called.month)) == 12


def test_a_horizon_outside_the_event_window_has_no_called_period_and_says_why() -> None:
    """A calendar fact, reported as one - never as an eligibility refusal."""
    # Overnight-only half-hours: outside every measured event slot (nothing runs before 11:00).
    idx = pd.date_range("2026-06-01 00:00", periods=20, freq="30min")
    mask, notes = dfs_service_period_mask(idx, zone=FIXTURE_ZONE, assumptions=LIB)
    assert not mask.any()
    text = " ".join(notes)
    assert "CALENDAR reason" in text
    assert "not a finding that the site cannot deliver DFS" in text


def test_the_rate_is_this_sites_own_zones_accepted_price_at_full_delivery() -> None:
    """P x K / 1000, with P the SITE'S OWN zone's volume-weighted ACCEPTED price and K = 1.0.

    The zonal PAIR is what this pins: price and call count move in opposite directions across the
    12 zones, so taking one zone's count with another zone's price would manufacture a figure no
    zone could pay. Dies if the pair is split, and dies if the fleet's realised K = 0.3896 is
    multiplied onto a dispatch-MEASURED delivery (that statistic corrects offered against settled
    volume, which cannot differ in a measured figure). Dies too if the rate stops citing the
    library key, because the per-stream register would then print a traceability gap for the
    largest stream in the pack.
    """
    rate, used, notes = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=FIXTURE_ZONE)
    expected = _DFS_ZONE_OBSERVED[FIXTURE_ZONE].utilisation_price_gbp_per_mwh
    assert rate == pytest.approx(expected / 1000.0)
    assert used == ["ancillary_services.dfs_zonal_observed_2026_27"]
    text = " ".join(notes)
    assert "FULL DELIVERY OF THE OFFERED VOLUME" in text
    assert "0.3896" in text and "cannot exist in it" in text
    assert "PAY-AS-BID" in text
    assert "THE ZONAL PAIR IS TAKEN TOGETHER" in text
    # Both superseded library figures are named as not used.
    assert "NOT USED, AND DELIBERATELY" in text
    assert "4,559" in text
    assert "7.56" in text


# --- the OBJECTIVE TERM, and the £ it produces -------------------------------


@pytest.fixture(scope="module")
def solved(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """One real MILP solve on a January horizon — i.e. inside the DFS winter season."""
    path = tmp_path_factory.mktemp("dfs") / "load.csv"
    rows, _ = write_hh_csv(path)
    site = multi_rate_site(path, rows=rows)
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=LIB,
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
        )
    )
    return site, result, solver.last_schedule()


def test_the_schedule_carries_the_dfs_price_array_and_a_measured_value(solved: tuple) -> None:
    """The objective term reached the solve, and its £ is measurable off the schedule.

    Dies if ``dfs_price_gbp_per_kwh=`` is dropped from `_solved`'s schedule construction, or if the
    DFS array stops being added to ``price`` in `_solve_milp` (with no term in the objective the
    optimiser has no reason to deliver into a called half-hour, and the measured £ collapses).
    """
    site, _result, schedule = solved
    # The zone is the SITE'S own, resolved from its postcode - not a module default. Dies if the
    # resolver is short-circuited to a fixed zone.
    zone, resolved, zone_notes = dfs_zone_for_site(site)
    assert (zone, resolved) == (FIXTURE_ZONE, True)
    assert "point-in-polygon" in " ".join(zone_notes)
    assert schedule is not None
    assert schedule.dfs_price_gbp_per_kwh is not None
    called = schedule.dfs_price_gbp_per_kwh > 0.0
    assert called.any(), "the fixture horizon covers the measured 11:00-23:00 DFS window"
    # The rate is on the called half-hours and nowhere else.
    rate, _used, _notes = dfs_utilisation_rate_gbp_per_kwh(LIB, zone=FIXTURE_ZONE)
    assert np.allclose(schedule.dfs_price_gbp_per_kwh[called], rate)
    assert np.allclose(schedule.dfs_price_gbp_per_kwh[~called], 0.0)
    assert schedule.dfs_utilisation_value_gbp > 0.0
    assert schedule.dfs_delivered_mwh is not None and schedule.dfs_delivered_mwh > 0.0


def test_the_objective_actually_pays_for_delivery_into_a_called_period(solved: tuple) -> None:
    """Zeroing the DFS price must MOVE the dispatch, not merely the reported £.

    The distinction is the whole point: a price array that is reported but not in the objective
    would give the same schedule and a £ nobody earned. So the same site is re-solved on a library
    whose DFS rate is patched to zero, and the in-window delivery must fall.
    """
    site, _result, schedule = solved
    called = schedule.dfs_price_gbp_per_kwh > 0.0
    with_dfs = float(schedule.discharge_kw[called].sum())

    import gridq.optimisation.milp_solver as ms

    original = ms.dfs_utilisation_rate_gbp_per_kwh
    try:
        ms.dfs_utilisation_rate_gbp_per_kwh = lambda _a, zone: (  # type: ignore[assignment]
            0.0,
            [],
            [f"patched to zero for zone {zone}"],
        )
        solver = MilpDispatchSolver()
        solver.solve(
            DispatchProblem(
                site_model=site,
                assumptions=LIB,
                power_kw=200.0,
                energy_kwh=400.0,
                duration_hours=2.0,
            )
        )
        without = solver.last_schedule()
    finally:
        ms.dfs_utilisation_rate_gbp_per_kwh = original  # type: ignore[assignment]

    assert without is not None
    without_dfs = float(without.discharge_kw[called].sum())
    assert with_dfs > without_dfs + 1e-6, (
        "the DFS price is not in the objective: removing it changed no dispatch, so the reported "
        "DFS revenue would be money the optimiser never traded for"
    )


# --- the VALUATION BRANCH, and the gate that has to be open for it to run ----


def test_dfs_is_priced_into_the_floor_with_the_d57_assumptions_stated(solved: tuple) -> None:
    """The reader-facing end: a non-zero CONTRACTED DFS stream on the top-level value_streams.

    Dies if the ``sid == MarketStream.DFS`` branch is removed from `build_value_streams`, and dies
    if `DemandFlexibilityServiceStream.check_gates` goes back to CLOSED/CONDITIONAL — a non-OPEN
    gate makes ``valuation._priceable`` False, so the branch is never reached and no amount of
    dispatch earns anything.
    """
    site, _result, schedule = solved
    value_streams, gated, _used = build_value_streams(schedule, site, LIB)
    dfs = next((v for v in (*value_streams, *(gated or [])) if v.stream == MarketStream.DFS), None)
    assert dfs is not None
    assert dfs.annual_revenue_gbp > 0.0, "D57 requires DFS priced on every site"
    assert dfs.firmness == Firmness.CONTRACTED
    assert is_priced_floor(dfs)
    # THE LARGEST STREAM IN THE PACK CITES A LIBRARY KEY (2026-08-19). Before the zonal basis
    # existed this branch returned no assumption keys at all, so the D59 per-stream register printed
    # a traceability gap for DFS. Dies if `_dfs_value` stops returning the rate's own keys.
    assert "ancillary_services.dfs_zonal_observed_2026_27" in dfs.assumptions_used
    blob = " ".join(dfs.warnings)
    # The £ is the dispatch's own, and it says so.
    assert "MEASURED BY THE DISPATCH" in blob
    # And the SIZE FUNCTION is published beside it, with this candidate's own ceiling.
    assert "IT SCALES WITH THE BATTERY, AND HERE IS THE FUNCTION" in blob
    assert "DFS ZONE" in blob and "THIS SITE'S OWN" in blob
    assert "S = 0.5 x P x Q x K" in blob
    # NESO's per-MW scenarios are a cross-check and are NOT in the £ (the double-book guard).
    assert "PUBLISHED CROSS-CHECK, NOT ADDED TO THE £" in blob
    assert "booking both would count the same pound twice" in blob
    # Every assumption D57 supplies is enumerated, and the stacking waiver is stated separately
    # from the shared-power-budget waiver, because they are different rules.
    assert "DFS IS PRICED ON D57 ASSUMPTIONS, NOT ON A HELD REGISTRATION" in blob
    assert "DFS STACKING EXCLUSIVITY IS ASSUMED WAIVED (D57 limb 3), NOT CHECKED" in blob
    assert "4.7.6" in blob and "4.7.9" in blob
    assert "CONFIRMATION ASK" in blob


def test_the_dfs_pound_is_not_also_booked_as_arbitrage_or_duos(solved: tuple) -> None:
    """The arithmetic D57 preserves: no pound under two stream names.

    The DFS price array must be DISJOINT from the energy and DUoS arrays that the two floor streams
    are measured from. This is checked as arithmetic, not as prose: the floor identity
    ``arbitrage + duos == priced_floor_terms`` must still hold exactly, which it cannot if the DFS
    rate leaked into either price series.
    """
    _site, _result, s = solved
    assert s.arbitrage_value_gbp + s.duos_avoidance_value_gbp == pytest.approx(
        s.priced_floor_terms_gbp, abs=0.02
    )
    # And the DFS £ is a separate, additional amount — not a slice of the floor terms.
    assert s.dfs_utilisation_value_gbp > 0.0
    assert s.dfs_utilisation_value_gbp != pytest.approx(s.priced_floor_terms_gbp)
