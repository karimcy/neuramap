"""D45 in the CONSUMERS: an absent DUoS band rate must be reported, never priced as a silent £0.

`tests/test_duos_absent_vs_zero.py` pins the PRODUCER — `duos_rate_series_with_absences` reports
which bands are absent and no longer mistakes a genuine ``0.0`` for absence. That fixed the source
but not the defect: the two consumers on the pricing path (`MilpDispatchSolver._solve_milp` and
`MpcDispatchSolver.solve`) still called the wrapper that DISCARDS the absence report, so an empty
``duos_red_rate`` still entered the objective's ``price_t`` and the reported
`DUOS_RED_BAND_AVOIDANCE` as a zero indistinguishable from a measured rate. This file pins the
consumer half.

Two properties, and the second is the one that makes the change safe:

1. **The absence is REPORTED** — a labelled warning names the missing bands, states that the zero
   is a missing input rather than a fact, and states which way the resulting error runs.
2. **Nothing else moves.** The returned rate array is dense either way, so an all-null tariff
   solves to the same £ as a tariff that declares all three rates as a genuine ``0.0``. The two
   runs differ ONLY in warnings. That is the D45 discipline applied to a price: making an absence
   visible must not itself change a number, or the fix cannot be told from a regression.

Live case, not theoretical: Colnbrook and Tanhouse (2 of the real sites in `examples/real/sites/`)
carry all three band rates null, while Bishops Cleeve, Ewelme, Harwell and Knowl Hill carry all
three. No real site is partially populated — but `tests/_milp_support.py` is (red only), which is
why the partial shape is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver, duos_absence_warning
from gridq.schemas import SiteModel, SolverStatus

_LABEL = "DUoS BAND RATE ABSENT, NOT ZERO (D45)"
_ALL_NULL = {"duos_red_rate": None, "duos_amber_rate": None, "duos_green_rate": None}
_ALL_ZERO = {"duos_red_rate": 0.0, "duos_amber_rate": 0.0, "duos_green_rate": 0.0}


# --- the shared warning text -------------------------------------------------


def test_no_absence_is_silent() -> None:
    """Every band declared → no warning. A run with complete data must stay quiet."""
    assert duos_absence_warning(()) is None


@pytest.mark.parametrize(
    "absent",
    [("red",), ("amber",), ("green",), ("red", "amber"), ("amber", "green")],
)
def test_partial_absence_names_the_bands_and_the_direction(absent: tuple[str, ...]) -> None:
    note = duos_absence_warning(absent)
    assert note is not None
    assert note.startswith(_LABEL)
    for band in absent:
        assert band in note
    # The partial shape's distinguishing claim: the modelled spread is too WIDE, so a declared
    # expensive band overstates the avoidance. This is the branch a reader must not confuse with
    # the all-absent one, so it is pinned on the text.
    assert "WIDER" in note and "OVERSTATED" in note


def test_all_absent_says_the_component_drops_out_and_is_conservative() -> None:
    note = duos_absence_warning(("red", "amber", "green"))
    assert note is not None
    assert note.startswith(_LABEL)
    assert "red, amber, green" in note  # fixed declaration order, never sorted or reordered
    assert "£0" in note and "conservative" in note
    # It must NOT claim the overstatement direction, which belongs to the partial case only.
    assert "OVERSTATED" not in note


def test_warning_points_at_the_offline_derivation_route_without_deriving() -> None:
    """D45: deriving a published DNO rate is sourcing, but it happens OFFLINE and is recorded."""
    note = duos_absence_warning(("red", "amber", "green"))
    assert note is not None
    assert "duos.unit_rates_p_per_kwh_by_dno" in note  # the release table to derive FROM
    assert "OFFLINE" in note
    assert "no rate is invented here" in note


def test_the_warning_is_deterministic() -> None:
    """Result content must be identical across calls — it feeds the run record."""
    assert duos_absence_warning(("red", "green")) == duos_absence_warning(("red", "green"))


# --- the MILP consumer -------------------------------------------------------


@pytest.fixture(scope="module")
def hh(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    path = tmp_path_factory.mktemp("duos_nulls") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return path, rows


@pytest.fixture(scope="module")
def site(hh: tuple[Path, int]) -> SiteModel:
    path, rows = hh
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def library() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def _solve(site: SiteModel, library: AssumptionLibrary, tariff_update: dict[str, object]):
    """``(result, schedule)`` for ``site`` with ``tariff_update`` applied to its tariff stack."""
    patched = site.model_copy(
        update={"tariff_stack": site.tariff_stack.model_copy(update=tariff_update)}
    )
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=patched,
            assumptions=library,
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
        )
    )
    return result, solver.last_schedule()


def test_milp_reports_an_all_null_tariff_and_a_genuine_zero_stays_silent(site, library) -> None:
    """The Colnbrook/Tanhouse shape warns; a site that really faces no DUoS charge does not."""
    null_result, _ = _solve(site, library, _ALL_NULL)
    zero_result, _ = _solve(site, library, _ALL_ZERO)

    assert null_result.solver_status == SolverStatus.OPTIMAL
    assert zero_result.solver_status == SolverStatus.OPTIMAL

    null_notes = [w for w in null_result.warnings if w.startswith(_LABEL)]
    assert len(null_notes) == 1, "the absence must be reported exactly once"
    assert "red, amber, green" in null_notes[0]
    # The half of the defect `or 0.0` hid in the other direction: a declared 0.0 is EVIDENCE.
    assert [w for w in zero_result.warnings if w.startswith(_LABEL)] == []


def test_an_absent_band_changes_no_reported_figure_only_the_warnings(site, library) -> None:
    """The safety property: making the absence visible must not move a £.

    An absent band contributes zeros to the dense rate array, exactly as a declared 0.0 does, so
    these two runs must agree on every number — dispatch, both floor components, the objective —
    and differ only in that one is labelled. If this ever fails, the absence report has started
    changing prices, which is a regression and not a fix.
    """
    null_result, null_schedule = _solve(site, library, _ALL_NULL)
    zero_result, zero_schedule = _solve(site, library, _ALL_ZERO)

    assert null_schedule is not None and zero_schedule is not None
    assert null_schedule.ref() == zero_schedule.ref()
    np.testing.assert_array_equal(null_schedule.discharge_kw, zero_schedule.discharge_kw)
    np.testing.assert_array_equal(null_schedule.charge_kw, zero_schedule.charge_kw)
    np.testing.assert_array_equal(
        null_schedule.duos_rate_gbp_per_kwh, zero_schedule.duos_rate_gbp_per_kwh
    )
    assert null_schedule.duos_avoidance_value_gbp == zero_schedule.duos_avoidance_value_gbp
    assert null_schedule.arbitrage_value_gbp == zero_schedule.arbitrage_value_gbp
    assert null_schedule.priced_floor_terms_gbp == zero_schedule.priced_floor_terms_gbp
    # ...and the DUoS stream really is £0 in both, so the all-null case cannot flatter the floor.
    assert null_schedule.duos_avoidance_value_gbp == 0.0

    # The ONLY difference is the label.
    assert set(null_result.warnings) - set(zero_result.warnings) == {
        w for w in null_result.warnings if w.startswith(_LABEL)
    }
    assert set(zero_result.warnings) - set(null_result.warnings) == set()


def test_milp_reports_the_partially_populated_fixture_shape(site, library) -> None:
    """`tests/_milp_support.py` declares red only, so the shared fixture is itself the partial case.

    The run continues (D44: a missing input must not block a validation run) but the overstatement
    is now labelled instead of silent. The stream gate only tests the red rate, so this tariff
    passes the gate and prices the overstatement — which is exactly why it has to be visible.
    """
    assert site.tariff_stack.duos_red_rate is not None, "fixture sanity: red IS declared"
    assert site.tariff_stack.duos_amber_rate is None, "fixture sanity: amber is NOT"

    result, _ = _solve(site, library, {})  # the fixture tariff as-is
    assert result.solver_status == SolverStatus.OPTIMAL
    notes = [w for w in result.warnings if w.startswith(_LABEL)]
    assert len(notes) == 1
    assert "amber, green" in notes[0]
    assert "OVERSTATED" in notes[0]
