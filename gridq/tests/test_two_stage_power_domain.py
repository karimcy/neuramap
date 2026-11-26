"""The two-anchor POWER DOMAIN of the two-stage sizing search.

Re-anchoring the sizing basis on the connection (`engine._sizing_basis`) was correct — a battery is
sized to the wire, and anchoring the box on the load hid 302 kW of evidenced headroom at Knowl Hill.
But the coarse floor, the refinement lower bound, the polish clamp and the N6 cover were all
``0.05 x basis``, so raising the basis also raised the SMALLEST searchable system:

| site | old min power | basis-anchored min power |
|---|---|---|
| Knowl Hill | 1.131 kW | 16.25 kW |
| Ewelme | 9.06 kW | 37.5 kW |
| Bishops Cleeve | 6.29 kW | 12.5 kW |

Behind-the-meter discharge is capped at ``min(power, load)`` (`milp_solver`'s ``discharge_ub``), so
at Knowl Hill — 22.6 kW peak, 325 kW connection — the economically relevant power band is roughly
``[0, 22.6] kW``, and that band went from seven coarse power points to ONE. The search was pressed
against a wall in precisely the region where a viable specification would first appear.

So the power axis has TWO anchors: its upper edge tracks the connection (the point of the sibling
fix), its lower edge the site's own load-relevant scale. These tests pin the domain itself — the
stages are exercised with `_evaluate_spec` stubbed, because this is about which candidates the
search decides to SOLVE, not about the dispatch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation import two_stage
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.two_stage import (
    COARSE_POWER_FRACTIONS,
    NO_VIABLE_COVER_DURATION_HOURS,
    NO_VIABLE_COVER_MAX_SOLVES,
    NO_VIABLE_COVER_POWER_FRACTIONS,
    POWER_FRACTION_BOUNDS,
    coarse_power_grid_kw,
    load_relevant_capacity_kw,
    no_viable_cover_pairs,
    power_search_floor_kw,
    two_stage_sizing,
)
from gridq.sizing.objective import SizingObjective
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation

F_LO, F_HI = POWER_FRACTION_BOUNDS

# The live Knowl Hill shape (`examples/real/sites/knowl_hill.json`): the site the regression was
# measured on. Not invented figures — the site's own declared connection and metered peak.
KH_BASIS_KW = 325.0
KH_PEAK_KW = 22.619


def _low_load_year_csv(path, *, peak_kw: float, days: int = 7) -> int:
    """A deterministic HH series whose PEAK is ``peak_kw`` — a low-load/big-connection analogue."""
    n = days * 48
    ts = pd.date_range("2025-01-06 00:00", periods=n, freq="30min")
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    load = np.full(n, peak_kw * 0.2)
    load[(hour >= 8) & (hour < 20)] = peak_kw * 0.7
    load[weekday & (hour >= 16) & (hour < 19)] = peak_kw
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    return n


def _site(make_site, *, mic_kw: float | None, asc_kw: float | None, peak_kw: float | None):
    """A site in a chosen connection/peak state (``mic_kw=0.0`` is the schema's "not declared")."""
    site = make_site()
    load = site.load_data.model_copy(update={"max_import_kw": peak_kw})
    conn = s.ConnectionDetails(mic_kw=0.0 if mic_kw is None else mic_kw, asc_kw=asc_kw)
    return site.model_copy(update={"load_data": load, "connection": conn})


# --------------------------------------------------------------- the lower anchor


def test_lower_anchor_is_the_load_peak_when_the_connection_is_larger(make_site) -> None:
    """The regression's own case: a low-load site with a large evidenced connection. The basis
    (and the top of the box) stays on the connection; the floor comes back down onto the load."""
    site = _site(make_site, mic_kw=KH_BASIS_KW, asc_kw=KH_BASIS_KW, peak_kw=KH_PEAK_KW)
    relevant = load_relevant_capacity_kw(site, KH_BASIS_KW)
    assert relevant == pytest.approx(KH_PEAK_KW)
    # Rounded to 3 dp, the same rounding every stage's candidate keys use.
    assert power_search_floor_kw(relevant) == round(F_LO * KH_PEAK_KW, 3)
    # The measured regression, stated as an assertion: 16.25 kW -> 1.131 kW.
    assert power_search_floor_kw(relevant) == pytest.approx(1.131)
    assert power_search_floor_kw(KH_BASIS_KW) == pytest.approx(16.25)


def test_lower_anchor_is_the_connection_when_the_peak_refutes_it(make_site) -> None:
    """Declared limit BELOW the metered peak: the engine anchors the BASIS on the larger of the two
    (the meter refutes the declaration), but the battery still charges through the smaller, so the
    smaller is the relevant scale for the small end."""
    site = _site(make_site, mic_kw=500.0, asc_kw=500.0, peak_kw=600.0)
    assert load_relevant_capacity_kw(site, 600.0) == pytest.approx(500.0)


def test_no_declared_limit_searches_exactly_as_before(make_site) -> None:
    """No MIC/ASC: the basis IS the peak, so both anchors coincide and nothing changes."""
    site = _site(make_site, mic_kw=None, asc_kw=None, peak_kw=180.0)
    assert load_relevant_capacity_kw(site, 180.0) == pytest.approx(180.0)


def test_no_declared_peak_searches_exactly_as_before(make_site) -> None:
    """No metered peak declared and no series in hand: the basis is the limit and stands alone."""
    site = _site(make_site, mic_kw=500.0, asc_kw=500.0, peak_kw=None)
    assert load_relevant_capacity_kw(site, 500.0) == pytest.approx(500.0)


def test_coincident_anchors_return_the_basis(make_site) -> None:
    site = _site(make_site, mic_kw=500.0, asc_kw=500.0, peak_kw=500.0)
    assert load_relevant_capacity_kw(site, 500.0) == pytest.approx(500.0)


def test_the_measured_series_peak_is_an_anchor_and_the_smallest_scale_wins(make_site) -> None:
    """``discharge_ub = min(power, load)`` caps against the SERIES, so the series peak counts. The
    anchor is the MINIMUM over every evidenced scale, which is what makes the change safe: the floor
    can only fall."""
    site = _site(make_site, mic_kw=400.0, asc_kw=400.0, peak_kw=200.0)
    assert load_relevant_capacity_kw(site, 400.0, series_peak_kw=90.0) == pytest.approx(90.0)
    # A series peak ABOVE the declared peak never raises the anchor.
    assert load_relevant_capacity_kw(site, 400.0, series_peak_kw=380.0) == pytest.approx(200.0)


@pytest.mark.parametrize(
    ("mic", "asc", "peak", "basis", "series"),
    [
        (325.0, 325.0, 22.619, 325.0, 22.619),
        (750.0, 750.0, 181.184, 750.0, 181.184),
        (250.0, 250.0, 125.871, 250.0, 125.871),
        (500.0, 500.0, 600.0, 600.0, 600.0),
        (None, None, 180.0, 180.0, 180.0),
        (500.0, 500.0, None, 500.0, None),
        (500.0, 500.0, 500.0, 500.0, 500.0),
        # Defensive: a basis SMALLER than every other scale must not raise the floor.
        (500.0, 500.0, 400.0, 40.0, 400.0),
    ],
)
def test_the_floor_can_only_ever_fall(make_site, mic, asc, peak, basis, series) -> None:
    """The invariant the whole change rests on: the new floor is never above the old one, so the
    candidate set can only WIDEN and no size any anchor evidences is ever excluded."""
    site = _site(make_site, mic_kw=mic, asc_kw=asc, peak_kw=peak)
    relevant = load_relevant_capacity_kw(site, basis, series_peak_kw=series)
    assert relevant <= basis + 1e-9
    assert power_search_floor_kw(relevant) <= F_LO * basis + 1e-9


# --------------------------------------------------------------- the coarse ladder


def test_coincident_anchors_give_the_identical_coarse_ladder() -> None:
    """No-op by construction: when the anchors coincide the ladder is the same set of powers,
    produced by the same expression — no float drift, no golden churn on any such site."""
    for basis in (320.0, 352.0, 500.0, 213.81, 915.077):
        assert coarse_power_grid_kw(basis, basis) == sorted(
            {round(f * basis, 3) for f in COARSE_POWER_FRACTIONS}
        )


def _fills_the_gap(fraction: float) -> bool:
    """Does this fraction of the Knowl Hill peak fall below the connection ladder's own floor?"""
    return fraction * KH_PEAK_KW < F_LO * KH_BASIS_KW - 1e-9


def test_the_coarse_ladder_is_a_superset_that_reaches_the_new_floor() -> None:
    powers = coarse_power_grid_kw(KH_BASIS_KW, KH_PEAK_KW)
    basis_ladder = sorted({round(f * KH_BASIS_KW, 3) for f in COARSE_POWER_FRACTIONS})
    assert set(basis_ladder) <= set(powers)  # the connection ladder is untouched
    assert min(powers) == power_search_floor_kw(KH_PEAK_KW)
    assert max(powers) == round(F_HI * KH_BASIS_KW, 3)
    assert powers == sorted(set(powers))  # ascending, deduplicated, deterministic
    # The added points fill ONLY the sub-domain the connection ladder cannot reach.
    added = [p for p in powers if p not in set(basis_ladder)]
    assert added and all(p < F_LO * KH_BASIS_KW for p in added)
    assert added == sorted(
        {round(f * KH_PEAK_KW, 3) for f in COARSE_POWER_FRACTIONS if _fills_the_gap(f)}
    )


def test_the_relevant_band_regains_its_resolution() -> None:
    """The point of the fix: coarse power points inside the site's own ``[0, peak]`` band, where
    behind-the-meter discharge is capped at the load and a viable spec would first appear."""
    in_band = [p for p in coarse_power_grid_kw(KH_BASIS_KW, KH_PEAK_KW) if p <= KH_PEAK_KW]
    basis_only = [
        p
        for p in coarse_power_grid_kw(KH_BASIS_KW, KH_BASIS_KW)
        if p <= KH_PEAK_KW  # the basis-anchored ladder: exactly one point in the whole band
    ]
    assert len(basis_only) == 1
    assert len(in_band) == 7


def test_the_upper_edge_is_deliberately_not_clipped_at_the_connection() -> None:
    """A deliberate decision, recorded as a test. Energy is power x duration and duration caps at
    8 h, so the 1.5x power headroom is the ONLY route to the box's high-energy corner; a candidate
    that cannot use its rating is bounded by the solver's own charge/discharge caps and just carries
    its capex into the ranking, where it loses. Clipping would move selections for no gain."""
    powers = coarse_power_grid_kw(KH_BASIS_KW, KH_PEAK_KW)
    assert max(powers) == pytest.approx(F_HI * KH_BASIS_KW)
    assert max(powers) > KH_BASIS_KW


# --------------------------------------------------------------- the N6 cover


def test_the_cover_defaults_to_the_legacy_span() -> None:
    """Positional callers and coincident anchors get byte-identical behaviour."""
    pairs = no_viable_cover_pairs(set(), 400.0, lambda p, kwh: True)
    powers = {p for p, _ in pairs}
    assert powers == {round(f * 400.0, 3) for f in NO_VIABLE_COVER_POWER_FRACTIONS}
    assert len(pairs) == len(NO_VIABLE_COVER_POWER_FRACTIONS) * len(NO_VIABLE_COVER_DURATION_HOURS)
    assert pairs == no_viable_cover_pairs(
        set(), 400.0, lambda p, kwh: True, relevant_capacity_kw=400.0
    )


def test_the_cover_spans_the_same_domain_as_the_grid_it_covers() -> None:
    """A cover reaching a different range from the search it vouches for is a defect in itself —
    the verdict it licenses is "this asset does not work at ANY size"."""
    grid = coarse_power_grid_kw(KH_BASIS_KW, KH_PEAK_KW)
    cover = no_viable_cover_pairs(
        set(), KH_BASIS_KW, lambda p, kwh: True, relevant_capacity_kw=KH_PEAK_KW
    )
    powers = {p for p, _ in cover}
    assert min(powers) == min(grid) == power_search_floor_kw(KH_PEAK_KW)
    assert max(powers) == max(grid) == round(F_HI * KH_BASIS_KW, 3)
    assert min(d for _, d in cover) == min(NO_VIABLE_COVER_DURATION_HOURS)
    assert max(d for _, d in cover) == max(NO_VIABLE_COVER_DURATION_HOURS)
    # Every connection-anchored corner the pre-fix cover reached is still reached.
    for fraction in NO_VIABLE_COVER_POWER_FRACTIONS:
        assert round(fraction * KH_BASIS_KW, 3) in powers


def test_the_cover_cap_can_never_truncate_a_complete_cover() -> None:
    """The cap is derived from the two-anchor cross product, so the worst case fits inside it: a
    silently truncated cover would drop the TOP of the power range, the very thing N6 exists to
    reach."""
    # Worst case: every cover fraction of the lower anchor falls below the upper ladder's floor.
    pairs = no_viable_cover_pairs(set(), 400.0, lambda p, kwh: True, relevant_capacity_kw=10.0)
    powers = {p for p, _ in pairs}
    assert len(powers) == 2 * len(NO_VIABLE_COVER_POWER_FRACTIONS)
    assert len(pairs) == len(powers) * len(NO_VIABLE_COVER_DURATION_HOURS)
    assert len(pairs) == NO_VIABLE_COVER_MAX_SOLVES
    for fraction in NO_VIABLE_COVER_POWER_FRACTIONS:
        assert round(fraction * 400.0, 3) in powers


def test_the_cover_still_skips_confirmed_specs_and_floor_rejects_in_extension_mode() -> None:
    already = {(round(F_LO * 40.0, 3), 2.0)}
    pairs = no_viable_cover_pairs(
        already, 400.0, lambda p, kwh: p >= 10.0, relevant_capacity_kw=40.0
    )
    assert (round(F_LO * 40.0, 3), 2.0) not in pairs
    assert all(p >= 10.0 for p, _ in pairs)


# --------------------------------------------------------------- end to end


def _stub_evaluate(*, project_irr_pct: float | None):
    """A `_evaluate_spec` double: prices every spec, none of them viable unless told otherwise."""

    def _evaluate(site, assumptions, candidate: SizingCandidate, **kwargs) -> CandidateEvaluation:
        return CandidateEvaluation(
            candidate=candidate,
            optimisation_result=s.OptimisationResult(
                result_id="res_stub",
                request_id="req_stub",
                implementation_level=s.ImplementationLevel.PRODUCTION,
                bankable_status=s.BankableStatus.NOT_BANKABLE,
                selected_power_kw=candidate.power_kw,
                selected_energy_kwh=candidate.energy_kwh,
                duration_hours=candidate.duration_hours,
                value_streams=[],
                solver_status=s.SolverStatus.OPTIMAL,
            ),
            priceable=True,
            bankable=False,
            min_p90_dscr=None,
            covenant=1.3,
            passes_covenant=None,
            levered_return_pct=None,
            project_irr_pct=project_irr_pct,
            financial=None,
        )

    return _evaluate


LOW_PEAK_KW = 30.0
BIG_CONNECTION_KW = 500.0


@pytest.fixture(scope="module")
def low_load_site_and_lib(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """A Knowl-Hill-shaped fixture: a 30 kW peak behind a 500 kW connection."""
    path = tmp_path_factory.mktemp("power_domain") / "hh.csv"
    rows = _low_load_year_csv(path, peak_kw=LOW_PEAK_KW)
    site = multi_rate_site(
        path, rows=rows, mic_kw=BIG_CONNECTION_KW, max_import_kw=LOW_PEAK_KW, site_id="site_lowload"
    )
    return site, load_assumptions("gb_v0_1")


def _run(site, lib, **kwargs):
    return two_stage_sizing(
        site,
        lib,
        base_solver=MilpDispatchSolver(),
        objective=SizingObjective(min_p90_dscr_covenant=1.3),
        binding_capacity_kw=BIG_CONNECTION_KW,
        seed=0,
        **kwargs,
    )


def test_the_search_reaches_the_small_systems_and_says_so(
    low_load_site_and_lib, monkeypatch
) -> None:
    site, lib = low_load_site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=-2.0))

    run = _run(site, lib)

    coarse = sorted({e.candidate.power_kw for e in run.candidates_evaluated if e.stage == "coarse"})
    assert coarse == coarse_power_grid_kw(BIG_CONNECTION_KW, LOW_PEAK_KW)
    assert min(coarse) == power_search_floor_kw(LOW_PEAK_KW)
    assert min(coarse) < F_LO * BIG_CONNECTION_KW  # the wall the regression put here is gone
    assert max(coarse) == round(F_HI * BIG_CONNECTION_KW, 3)  # the connection edge is untouched
    # Points inside the band where behind-the-meter discharge is not load-capped away.
    assert len([p for p in coarse if p <= LOW_PEAK_KW]) > 1
    # The two-anchor domain is DISCLOSED, never silent.
    assert any("sizing power axis spans TWO anchors" in w for w in run.warnings)


def test_the_refinement_and_polish_stages_share_that_floor(
    low_load_site_and_lib, monkeypatch
) -> None:
    """Every stage reads one domain: a candidate reachable by the coarse ladder must not be
    unreachable by refinement or by the polish clamp (which used to teleport every probe around a
    small best candidate straight back up onto the connection-anchored floor)."""
    site, lib = low_load_site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=-2.0))

    run = _run(site, lib)

    floor = power_search_floor_kw(LOW_PEAK_KW)
    powers = [e.candidate.power_kw for e in run.candidates_evaluated]
    # Tolerance of half a 2-dp step: the polish stage clamps at the floor and THEN rounds its probe
    # to 2 dp, so a probe can land a fraction of a watt under a 3-dp floor. Pre-existing, harmless.
    assert min(powers) >= floor - 0.005 - 1e-9
    assert min(powers) <= F_LO * BIG_CONNECTION_KW
    refined = [e for e in run.candidates_evaluated if e.stage == "refine"]
    assert refined
    assert min(e.candidate.power_kw for e in refined) < F_LO * BIG_CONNECTION_KW


def test_the_no_viable_cover_reaches_both_anchors_end_to_end(
    low_load_site_and_lib, monkeypatch
) -> None:
    site, lib = low_load_site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=-2.0))

    run = _run(site, lib)

    assert run.selected is None  # the verdict is still honest, just properly covered
    confirmed = {
        round(e.candidate.power_kw, 3)
        for e in run.candidates_evaluated
        if e.stage == "confirm" and not e.proxy
    }
    for fraction in NO_VIABLE_COVER_POWER_FRACTIONS:
        assert round(fraction * BIG_CONNECTION_KW, 3) in confirmed
    assert power_search_floor_kw(LOW_PEAK_KW) in confirmed
    note = [w for w in run.warnings if "no-viable cover (N6)" in w]
    assert note and "load-relevant capacity" in note[0]


def test_the_widened_domain_stays_deterministic(low_load_site_and_lib, monkeypatch) -> None:
    """Identical inputs + assumption version + seed -> identical outputs, unchanged."""
    site, lib = low_load_site_and_lib
    monkeypatch.setattr(two_stage, "_evaluate_spec", _stub_evaluate(project_irr_pct=-2.0))

    first, second = _run(site, lib), _run(site, lib)

    assert first.fingerprint() == second.fingerprint()
    assert [
        (e.candidate.power_kw, e.candidate.duration_hours, e.stage)
        for e in first.candidates_evaluated
    ] == [
        (e.candidate.power_kw, e.candidate.duration_hours, e.stage)
        for e in second.candidates_evaluated
    ]
