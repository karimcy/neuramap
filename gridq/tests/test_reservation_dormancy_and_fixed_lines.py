"""An ABSENT constraint must never read as a SATISFIED one (LIT-E-01/LIT-E-02, C031 item 2, D45).

## The reservation half

The MILP's availability-reservation compiler is real: `_compile_commitments` holds reserved power
against the DELIVERABLE envelope (``min(power − aux, load)``, not nameplate), sums overlapping
commitments into ONE aggregate constraint so no kW is double-sold, and reserves stored energy net of
discharge losses (``soe ≥ floor + held/eta_dis``). `tests/test_optimisation_commitments.py` pins all
of that.

It is also, in production, entirely DORMANT — and that was invisible. Every descriptor in
`value_stack/gb_streams.py` carries a ``parameter_source`` string and no typed payload, and nothing
in `src/` constructs an `AvailabilityCommitment`, so on a real run the compiler receives five
payload-less descriptors and compiles nothing. A commitment with no compiled reservation means the
same kW and the same kWh back the dispatch AND the CM/response obligation at once, inside the priced
floor the DSCR covenant is measured on.

The old code skipped a payload-less descriptor with a bare ``continue``. That is the D45 failure
mode exactly — an absent input reading as a met constraint — so the skip now records itself. The
model is untouched (the dormancy is not fixed here; a payload PRODUCER is a separate piece of work),
but a reader of the result can now see that deliverability was never certified.

## The fixed-bill-lines half

`standing_charge_per_day_gbp or 0.0` and `capacity_charge_gbp_per_kva_day or 0.0` conflated three
different states into one silent zero: a declared rate of 0.0 (a fact), an absent line (unknown),
and — because ``or`` is falsy-triggered, not None-triggered — a genuine 0.0 was swallowed as if it
were the absent case. Split with ``is None``: a declared value including 0.0 prices silently, and a
null still contributes £0 but says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver, fixed_line_absence_warnings
from gridq.schemas import SiteModel, SolverStatus
from gridq.value_stack.interface import DispatchConstraintDescriptor

_DORMANT = "NO AVAILABILITY RESERVATION COMPILED"
_STANDING = "STANDING CHARGE NOT RECORDED (D45)"
_CAPACITY = "CAPACITY/AVAILABILITY CHARGE NOT RECORDED (D45)"


@pytest.fixture(scope="module")
def hh(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    path = tmp_path_factory.mktemp("dormancy") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return path, rows


@pytest.fixture(scope="module")
def site(hh: tuple[Path, int]) -> SiteModel:
    path, rows = hh
    return multi_rate_site(path, rows=rows)


@pytest.fixture(scope="module")
def library() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def _solve(site: SiteModel, library: AssumptionLibrary, constraints=None):
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=library,
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
            constraints=list(constraints or []),
        )
    )
    return result, solver.last_schedule()


def _descriptor(kind: str, constraint_id: str) -> DispatchConstraintDescriptor:
    """A compilable descriptor with NO payload — today's universal production shape."""
    return DispatchConstraintDescriptor(
        constraint_id=constraint_id,
        description="test descriptor: declared, not contracted",
        kind=kind,
        parameter_source="test fixture (no typed payload, exactly like gb_streams today)",
    )


# --- the reservation dormancy ------------------------------------------------


def test_payload_less_descriptors_are_reported_not_silently_skipped(site, library) -> None:
    """THE DEFECT. A declared-but-unpayloaded commitment used to vanish without trace."""
    result, _ = _solve(
        site,
        library,
        [
            _descriptor("availability_window", "cm_capacity"),
            _descriptor("held_state_of_energy", "cm_held_soe"),
        ],
    )
    assert result.solver_status == SolverStatus.OPTIMAL
    notes = [w for w in result.warnings if w.startswith(_DORMANT)]
    assert len(notes) == 1, "one aggregated warning, not one per descriptor"
    note = notes[0]
    # It must name every affected descriptor, so an auditor can see WHICH obligation is unbacked.
    assert "availability_window:cm_capacity" in note
    assert "held_state_of_energy:cm_held_soe" in note
    assert "2 declared dispatch commitment descriptor(s)" in note
    # ...and it must state the consequence, not just the fact.
    assert "OVERSTATES the priced floor" in note
    assert "Deliverability is NOT certified" in note
    assert "C031 item 2" in note


def test_the_descriptor_list_is_in_seam_order_and_deterministic(site, library) -> None:
    """Warnings are result content, so the order must come from the seam, not a set or a hash."""
    order = [
        "availability_window:a_first",
        "availability_window:b_second",
        "held_state_of_energy:c_third",
    ]
    constraints = [
        _descriptor("availability_window", "a_first"),
        _descriptor("availability_window", "b_second"),
        _descriptor("held_state_of_energy", "c_third"),
    ]
    first, _ = _solve(site, library, constraints)
    second, _ = _solve(site, library, constraints)
    note = next(w for w in first.warnings if w.startswith(_DORMANT))
    assert ", ".join(order) in note
    assert note == next(w for w in second.warnings if w.startswith(_DORMANT))


def test_recording_the_dormancy_does_not_change_the_dispatch(site, library) -> None:
    """Requirement 4: byte-identical behaviour. Making an absence visible must not move a number.

    This is the golden-preserving property. `test_optimisation_commitments.py` already pins that a
    payload-less descriptor changes nothing; this pins that ADDING THE WARNING did not break that.
    """
    base_result, base_schedule = _solve(site, library, [])
    declared_result, declared_schedule = _solve(
        site, library, [_descriptor("availability_window", "cm_capacity")]
    )

    assert base_schedule is not None and declared_schedule is not None
    assert declared_schedule.ref() == base_schedule.ref()
    assert declared_result.constraints_binding == base_result.constraints_binding
    assert declared_schedule.priced_floor_terms_gbp == base_schedule.priced_floor_terms_gbp
    # No reservation was compiled, so the dormant capability fields must stay unasserted — a
    # "capability-checked" claim that was never checked is the C031 item-2 trap.
    assert declared_schedule.deliverable_capacity_mw is None
    assert declared_schedule.tender_window_discharge_mwh is None
    # The one and only difference.
    assert set(declared_result.warnings) - set(base_result.warnings) == {
        w for w in declared_result.warnings if w.startswith(_DORMANT)
    }


def test_no_commitment_descriptors_means_no_dormancy_warning(site, library) -> None:
    """The warning must be evidence of a real absence, never boilerplate on every solve."""
    result, _ = _solve(site, library, [])
    assert [w for w in result.warnings if w.startswith(_DORMANT)] == []


def test_structural_and_deferred_kinds_do_not_trigger_the_warning(site, library) -> None:
    """Only COMPILED kinds reserve anything, so only they can be dormant.

    `connection_limit`/`throughput_cap`/`export_limit` are enforced by the model's own bound
    construction and `deliverability` is the sizing lane's closed-form check — none of them carries
    a payload by design, so warning about them would be noise that trains readers to ignore it.
    """
    result, _ = _solve(
        site,
        library,
        [
            _descriptor("connection_limit", "conn"),
            _descriptor("throughput_cap", "warranty"),
            _descriptor("export_limit", "mec"),
            _descriptor("deliverability", "cm_derated"),
        ],
    )
    assert [w for w in result.warnings if w.startswith(_DORMANT)] == []


# --- the fixed bill lines ----------------------------------------------------


def _retariff(site: SiteModel, **update: object) -> SiteModel:
    return site.model_copy(update={"tariff_stack": site.tariff_stack.model_copy(update=update)})


def test_absent_standing_charge_warns_and_a_declared_zero_does_not(site, library) -> None:
    """``or 0.0`` could not tell these apart. ``is None`` can, and the £ is the same either way."""
    absent, absent_schedule = _solve(_retariff(site, standing_charge_per_day_gbp=None), library)
    zero, zero_schedule = _solve(_retariff(site, standing_charge_per_day_gbp=0.0), library)

    assert absent.solver_status == SolverStatus.OPTIMAL
    assert [w for w in absent.warnings if w.startswith(_STANDING)] != []
    assert [w for w in zero.warnings if w.startswith(_STANDING)] == []
    # A declared 0.0 is a FACT and must price exactly as the absent case does — the point is that
    # only one of them is labelled.
    assert absent_schedule is not None and zero_schedule is not None
    assert absent_schedule.ref() == zero_schedule.ref()


def test_absent_capacity_charge_warns_and_names_the_agreed_capacity(site, library) -> None:
    absent, _ = _solve(_retariff(site, capacity_charge_gbp_per_kva_day=None), library)
    notes = [w for w in absent.warnings if w.startswith(_CAPACITY)]
    assert len(notes) == 1
    # The kVA the missing rate would have been charged on, so the reader can size the gap.
    assert "kVA of agreed capacity" in notes[0]
    assert "battery-invariant" in notes[0]

    declared, _ = _solve(_retariff(site, capacity_charge_gbp_per_kva_day=0.0), library)
    assert [w for w in declared.warnings if w.startswith(_CAPACITY)] == []


def test_the_fixed_line_warnings_reach_the_reported_result_not_just_solve(site, library) -> None:
    """They must ride the channel the run record inherits, or the absence is invisible in the pack.

    Measured, not assumed: the production sizing path evaluates candidates WITHOUT going through
    `MilpDispatchSolver.solve`, so a warning raised only in `solve` never reaches the reported
    `OptimisationResult` (a demo engine run makes exactly two `solve` calls and discards their
    warnings). These warnings therefore come out of `_solve_milp`. `solve_window` shares that path,
    so the MPC rolling-horizon windows inherit them too.
    """
    bare = _retariff(site, standing_charge_per_day_gbp=None, capacity_charge_gbp_per_kva_day=None)
    # The pure function states both absences, in a fixed order (result content is deterministic)...
    notes = fixed_line_absence_warnings(bare)
    assert len(notes) == 2
    assert notes[0].startswith(_STANDING) and notes[1].startswith(_CAPACITY)
    assert fixed_line_absence_warnings(bare) == notes
    # ...and a declared pair states neither, including a declared 0.0.
    priced = _retariff(site, standing_charge_per_day_gbp=1.0, capacity_charge_gbp_per_kva_day=0.05)
    assert fixed_line_absence_warnings(priced) == []
    assert (
        fixed_line_absence_warnings(
            _retariff(site, standing_charge_per_day_gbp=0.0, capacity_charge_gbp_per_kva_day=0.0)
        )
        == []
    )

    # The emission point is what makes them visible: they must arrive on the SOLVE-MODEL warnings,
    # which the sizing candidates (and so the reported result) inherit — not on `solve`'s tail.
    # `_solve_milp` is the shared path for both `solve` and `solve_window`, so proving the text
    # appears on a full solve alongside a model-build warning pins the channel.
    result, _ = _solve(bare, library)
    assert [w for w in result.warnings if w.startswith(_STANDING)] != []
    assert [w for w in result.warnings if w.startswith(_CAPACITY)] != []
    model_build_warning = next(w for w in result.warnings if "UNITY POWER FACTOR ASSUMED" in w)
    standing_idx = next(i for i, w in enumerate(result.warnings) if w.startswith(_STANDING))
    assert result.warnings.index(model_build_warning) < standing_idx, (
        "the fixed-line absences must sit in the model-build warning block, not appended after it"
    )


def test_a_declared_standing_charge_is_priced_not_zeroed(site, library) -> None:
    """The regression guard for the `is None` rewrite: a real rate must still reach the bill."""
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=_retariff(site, standing_charge_per_day_gbp=2.0),
            assumptions=library,
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
        )
    )
    assert result.solver_status == SolverStatus.OPTIMAL
    bill = solver.last_customer_bill()
    assert bill is not None
    # 2.0 £/day x 365.25 = £730.50/yr, present on both sides of the bill.
    assert bill.standing_gbp == pytest.approx(730.5)
