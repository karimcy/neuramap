"""Tests for the W7-2 constraint compiler — the firming substrate in the solver.

The north-star Wave-7 acceptance criteria, verified on the real linopy/HiGHS solver over the
shared 7-day metered fixture: a dispatch with a committed window PROVABLY holds net-injection
headroom and state-of-energy through it (the unconstrained solve violates both — so the
constraints bind, they don't decorate); a payload-less declared descriptor changes nothing
(byte-identical schedule — the golden-preserving property every existing quote id relies on);
the dormant schedule fields populate only from compiled commitments; determinism holds.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver, _commitment_mask
from gridq.optimisation.valuation import _local_flex_call_rate
from gridq.schemas import AvailabilityCommitment, AvailabilityWindow, MarketStream
from gridq.value_stack.interface import DispatchConstraintDescriptor

POWER_KW = 200.0
ENERGY_KWH = 400.0


@pytest.fixture(scope="module")
def site_and_lib(tmp_path_factory: pytest.TempPathFactory) -> tuple:
    path = tmp_path_factory.mktemp("hh") / "load.csv"
    rows, _ = write_hh_csv(path)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _window() -> AvailabilityWindow:
    # Weekday 16:00–19:00 — exactly the red-band peak where the unconstrained solve discharges
    # hardest, so the reservation genuinely fights the arbitrage objective.
    return AvailabilityWindow(days_of_week=[0, 1, 2, 3, 4], start_time=time(16), end_time=time(19))


def _commitment(
    *,
    stream: MarketStream = MarketStream.CAPACITY_MARKET,
    committed_kw: float = 150.0,
    held_kwh: float | None = None,
    windows: list[AvailabilityWindow] | None = None,
) -> AvailabilityCommitment:
    return AvailabilityCommitment(
        commitment_id="cm_test",
        stream=stream,
        committed_power_kw=committed_kw,
        held_energy_kwh=held_kwh,
        windows=windows if windows is not None else [_window()],
        tenor_years=15.0,
        reference="TEST/AGR-001",
    )


def _descriptor(
    kind: str, commitment: AvailabilityCommitment | None
) -> DispatchConstraintDescriptor:
    return DispatchConstraintDescriptor(
        constraint_id=f"{kind}_test",
        description="test commitment descriptor",
        kind=kind,
        parameters=commitment,
    )


def _solve(
    site,
    lib,
    constraints: list[DispatchConstraintDescriptor],
    *,
    power_kw: float = POWER_KW,
    energy_kwh: float = ENERGY_KWH,
):
    """Solve at the module's 200 kW / 400 kWh size unless a test needs a different one.

    The overrides exist for the Capacity Market tests: CM prequalification requires a 1 MW unit
    (D-92), so a 200 kW candidate cannot reach the CM gate at all and a CM test built on it would
    assert revenue the asset is legally barred from earning.
    """
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=power_kw,
            energy_kwh=energy_kwh,
            duration_hours=energy_kwh / power_kw,
            constraints=constraints,
        )
    )
    return result, solver.last_schedule()


def _window_mask(schedule) -> np.ndarray:
    # The fixture's red band (weekday 16–19) coincides with the committed window by design.
    return schedule.red_band_mask


def test_reservation_holds_net_injection_headroom_through_windows(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    # Unconstrained: the arbitrage optimum discharges at full power through the red band —
    # a full-discharge dispatch inside the committed window, which the commitment must reject.
    base_result, base_schedule = _solve(site, lib, [])
    assert base_result.solver_status.value == "optimal"
    mask = _window_mask(base_schedule)
    base_net = base_schedule.discharge_kw[mask] - base_schedule.charge_kw[mask]
    assert float(base_net.max()) > POWER_KW - 150.0 + 1.0, (
        "fixture sanity: the unconstrained solve must use the headroom the commitment reserves"
    )

    committed_result, committed_schedule = _solve(
        site, lib, [_descriptor("availability_window", _commitment(committed_kw=150.0))]
    )
    assert committed_result.solver_status.value == "optimal"
    net = committed_schedule.discharge_kw[mask] - committed_schedule.charge_kw[mask]
    assert float(net.max()) <= POWER_KW - 150.0 + 1e-4, (
        "the compiled reservation must hold discharge−charge ≤ power − committed through every "
        "committed half-hour — the full-discharge dispatch is rejected"
    )
    assert "availability_reserve:cm_test" in committed_result.constraints_binding
    # The reservation costs dispatch value — the constraint binds economics, not just algebra.
    # (The window sits inside one energy-rate band, so the cost shows up in the DUoS red-band
    # component: reserved headroom means less red-band discharge.)
    committed_value = (
        committed_schedule.arbitrage_value_gbp + committed_schedule.duos_avoidance_value_gbp
    )
    base_value = base_schedule.arbitrage_value_gbp + base_schedule.duos_avoidance_value_gbp
    assert committed_value < base_value


def test_held_soe_floor_holds_through_windows(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    _, base_schedule = _solve(site, lib, [])
    mask = _window_mask(base_schedule)
    assert float(base_schedule.soe_kwh[mask].min()) < 300.0 - 1.0, (
        "fixture sanity: the unconstrained solve must drain below the hold during the window"
    )

    result, schedule = _solve(
        site, lib, [_descriptor("held_state_of_energy", _commitment(held_kwh=300.0))]
    )
    assert result.solver_status.value == "optimal"
    assert float(schedule.soe_kwh[mask].min()) >= 300.0 - 1e-4, (
        "the compiled hold must keep soe ≥ held_energy_kwh through every committed half-hour"
    )
    assert "held_soe:cm_test" in result.constraints_binding


def test_declared_without_payload_changes_nothing(site_and_lib: tuple) -> None:
    # Today's universal state (gb_streams descriptors carry no payload): the solve must be
    # byte-identical to the unconstrained one — the golden-preserving property.
    site, lib = site_and_lib
    base_result, base_schedule = _solve(site, lib, [])
    declared_result, declared_schedule = _solve(
        site, lib, [_descriptor("held_state_of_energy", None)]
    )
    assert declared_schedule.ref() == base_schedule.ref()
    assert declared_result.constraints_binding == base_result.constraints_binding
    assert declared_schedule.deliverable_capacity_mw is None
    assert declared_schedule.tender_window_discharge_mwh is None
    assert declared_schedule.tender_window_charge_mwh is None


def test_dormant_schedule_fields_populate_from_compiled_commitments(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    _, cm_schedule = _solve(
        site, lib, [_descriptor("availability_window", _commitment(committed_kw=150.0))]
    )
    assert cm_schedule.deliverable_capacity_mw == pytest.approx(0.15)  # 150 kW, reserved

    flex = _commitment(stream=MarketStream.LOCAL_FLEX_PICLO, committed_kw=50.0)
    _, flex_schedule = _solve(site, lib, [_descriptor("availability_window", flex)])
    mask = _window_mask(flex_schedule)
    # Per-instant contribution is CAPPED at the committed kW (adoption review W7-5): a 50 kW
    # award earns utilisation on at most 50 kW - never on every co-incident discharge.
    capped = np.minimum(flex_schedule.discharge_kw[mask], 50.0)
    expected_mwh = (
        float(capped.sum()) * flex_schedule.interval_hours * flex_schedule.annual_factor / 1000.0
    )
    assert flex_schedule.tender_window_discharge_mwh == pytest.approx(expected_mwh, rel=1e-6)
    uncapped_mwh = (
        float(flex_schedule.discharge_kw[mask].sum())
        * flex_schedule.interval_hours
        * flex_schedule.annual_factor
        / 1000.0
    )
    assert flex_schedule.tender_window_discharge_mwh < uncapped_mwh  # the cap genuinely bites
    assert flex_schedule.deliverable_capacity_mw is None  # no CM reservation in this run
    # D57 item 5b: the CHARGE-side mirror is populated by the SAME solve, capped at the same
    # committed kW. It is what a DEMAND TURN UP award's utilisation leg is measured on - NGED's Port
    # Ham clear pays the asset to CHARGE - and without it `valuation._local_flex_value` prices a
    # turn-up award off the discharge series and reads GBP 0. This assertion dies if
    # ``tender_window_charge_mwh=tender_charge_mwh`` is dropped from `_solved`'s schedule
    # construction, or if the charge series stops being capped at the awarded kW.
    capped_charge = np.minimum(flex_schedule.charge_kw[mask], 50.0)
    expected_charge_mwh = (
        float(capped_charge.sum())
        * flex_schedule.interval_hours
        * flex_schedule.annual_factor
        / 1000.0
    )
    assert flex_schedule.tender_window_charge_mwh is not None
    assert flex_schedule.tender_window_charge_mwh == pytest.approx(expected_charge_mwh, rel=1e-6)


def test_vacuous_window_warns_and_changes_nothing(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    _, base_schedule = _solve(site, lib, [])
    june_only = _commitment(
        windows=[
            AvailabilityWindow(months=[6], start_time=time(16), end_time=time(19))
        ]  # horizon is January
    )
    result, schedule = _solve(site, lib, [_descriptor("availability_window", june_only)])
    assert schedule.ref() == base_schedule.ref()
    assert any("never intersect the metered horizon" in w for w in result.warnings)


def test_referenced_deliverable_commitment_flips_cm_to_contracted(site_and_lib: tuple) -> None:
    # W7-4 end-to-end: a referenced CM commitment the candidate can honour at end-of-tenor
    # produces evidence-bearing gate context, flips the CM gate CONDITIONAL→CONTRACTED, and prices
    # the RESERVED capability — while the quote stays honestly not_bankable on placeholder content.
    #
    # D-92: this test now solves a 1.2 MW candidate on a 2 MW connection. The Capacity Market's
    # prequalification threshold is 1 MW of CONNECTION capacity (The Electricity Capacity
    # Regulations 2014, SI 2014/2043, reg 15), so the module's 200 kW candidate on a 500 kW
    # connection cannot reach the CM gate at all — asserting CONTRACTED CM revenue on it would be
    # asserting revenue the asset is barred from earning. Only the SIZE changed: the committed 50 kW
    # is still far below the degraded capability, so the W7-4 deliverability check this test exists
    # for still passes, and both CM figures below are functions of the reserved 50 kW, not of size.
    site, lib = site_and_lib
    site = site.model_copy(
        update={
            "connection": site.connection.model_copy(update={"mic_kw": 2_000.0, "asc_kw": 2_000.0})
        }
    )
    result, schedule = _solve(
        site,
        lib,
        [_descriptor("availability_window", _commitment(committed_kw=50.0))],
        power_kw=1_200.0,
        energy_kwh=2_400.0,
    )
    cm = next(v for v in result.value_streams if v.stream == s.MarketStream.CAPACITY_MARKET)
    assert cm.firmness == s.Firmness.CONTRACTED
    assert cm.gate_status == "open"
    assert schedule.deliverable_capacity_mw == pytest.approx(0.05)
    # £ = clearing (gb_v0_1 placeholder £30/kW/yr) × the reserved 50 kW — never nameplate.
    assert cm.annual_revenue_gbp == pytest.approx(30.0 * 50.0)
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE


def test_undeliverable_commitment_keeps_cm_conditional(site_and_lib: tuple) -> None:
    # 150 kW committed vs ≈88.9 kW degraded capability: the computed check FAILS, so the gate
    # must NOT flip — a reservation held in dispatch is not deliverability at end-of-tenor.
    site, lib = site_and_lib
    result, _ = _solve(
        site, lib, [_descriptor("availability_window", _commitment(committed_kw=150.0))]
    )
    cm = next(v for v in result.value_streams if v.stream == s.MarketStream.CAPACITY_MARKET)
    assert cm.firmness == s.Firmness.CONDITIONAL
    assert cm.gate_status == "conditional"


def test_referenced_flex_commitment_prices_the_tender_windows(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    site = site.model_copy(
        update={"connection": site.connection.model_copy(update={"dno_region": "NGED"})}
    )
    flex = _commitment(stream=s.MarketStream.LOCAL_FLEX_PICLO, committed_kw=50.0)
    result, schedule = _solve(site, lib, [_descriptor("availability_window", flex)])
    lf = next(v for v in result.value_streams if v.stream == s.MarketStream.LOCAL_FLEX_PICLO)
    assert lf.firmness == s.Firmness.CONTRACTED
    assert schedule.tender_window_discharge_mwh is not None
    assert schedule.tender_window_discharge_mwh > 0
    # £ = utilisation (gb_v0_1 placeholder £150/MWh) × the delivered tender-window MWh × the
    # sourced call rate. F-19: availability is not utilisation — paying every in-window discharge
    # as an instructed DNO call overstates the stream ~5-20x. The rate is read from the library so
    # this stays honest if a release later carries `local_flex_piclo.utilisation_call_rate`.
    # `abs` tolerance, not `rel`: the revenue is stored to 2dp, which is coarser than rel=1e-6.
    call_rate, _, _ = _local_flex_call_rate(lib)
    assert lf.annual_revenue_gbp == pytest.approx(
        150.0 * schedule.tender_window_discharge_mwh * call_rate, abs=0.01
    )
    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE


def test_committed_solve_is_deterministic(site_and_lib: tuple) -> None:
    site, lib = site_and_lib
    constraints = [
        _descriptor("availability_window", _commitment(committed_kw=150.0, held_kwh=300.0)),
        _descriptor("held_state_of_energy", _commitment(committed_kw=150.0, held_kwh=300.0)),
    ]
    _, a = _solve(site, lib, constraints)
    _, b = _solve(site, lib, constraints)
    assert a.ref() == b.ref()
    assert a.deliverable_capacity_mw == b.deliverable_capacity_mw


# --- Loop-03 fixes (adoption review W7-1/W7-2/W7-5) --------------------------


def _named_commitment(cid: str, committed_kw: float) -> AvailabilityCommitment:
    return AvailabilityCommitment(
        commitment_id=cid,
        stream=MarketStream.CAPACITY_MARKET,
        committed_power_kw=committed_kw,
        windows=[_window()],
        tenor_years=15.0,
        reference=f"TEST/{cid}",
    )


def test_overlapping_commitments_reserve_the_sum_never_the_max(site_and_lib: tuple) -> None:
    """W7-2: two commitments on the same windows reserve C1 + C2 of the deliverable envelope -
    kW is never double-sold. The solved net injection in the window respects the AGGREGATE."""
    site, lib = site_and_lib
    d1 = _descriptor("availability_window", _named_commitment("cm_a", 40.0))
    d2 = _descriptor("availability_window", _named_commitment("cm_b", 30.0))
    result, schedule = _solve(site, lib, [d1, d2])
    assert result.solver_status.value == "optimal"
    mask = _window_mask(schedule)
    net = schedule.discharge_kw[mask] - schedule.charge_kw[mask]
    envelope = np.minimum(schedule.power_kw, schedule.site_load_kw[mask])
    assert bool(np.all(net <= envelope - 70.0 + 1e-6))  # the SUM is reserved, not max(40, 30)


def test_commitment_above_the_in_window_load_is_warned_undeliverable(site_and_lib: tuple) -> None:
    """W7-1: on the no-export floor a response can only serve the site's own load - a
    commitment above the largest in-window deliverable envelope is loudly warned."""
    site, lib = site_and_lib
    huge = _named_commitment("cm_huge", 100000.0)  # far above any site load
    result, _ = _solve(site, lib, [_descriptor("availability_window", huge)])
    assert any("largest in-window deliverable response" in w for w in result.warnings)


def test_midnight_exclusive_end_window_is_representable_and_masks_to_day_end() -> None:
    """W7-5: end_time == 00:00 means the exclusive end of the day, so the pre-midnight half
    of an overnight window is representable."""
    w = AvailabilityWindow(start_time=time(23), end_time=time(0))  # 23:00 -> midnight
    c = AvailabilityCommitment(
        commitment_id="cm_mid",
        stream=MarketStream.CAPACITY_MARKET,
        committed_power_kw=10.0,
        windows=[w],
        tenor_years=1.0,
        reference="TEST/MID",
    )
    idx = pd.date_range("2025-01-01 22:00", periods=6, freq="30min")  # 22:00 .. 00:30
    mask = _commitment_mask(c, idx)
    assert mask.tolist() == [False, False, True, True, False, False]  # 23:00 + 23:30 only


def test_gaps_with_commitments_are_disclosed(site_and_lib: tuple, tmp_path) -> None:
    """Metered gaps + compiled commitments = a loud disclosure that contract constraints
    apply only to metered instants (never silently weakened)."""
    site, lib = site_and_lib
    path = tmp_path / "gappy.csv"
    write_hh_csv(path, days=7)
    frame = pd.read_csv(path)
    frame = pd.concat([frame.iloc[: 3 * 48], frame.iloc[4 * 48 :]])  # drop day 4 = a real gap
    frame.to_csv(path, index=False)
    gappy_site = multi_rate_site(path, rows=len(frame))
    result, _ = _solve(gappy_site, lib, [_descriptor("availability_window", _commitment())])
    assert any("unverifiable from this dataset" in w for w in result.warnings)


# --- D61 (2026-11-22): the solver records the local-flex reservation it compiled ---------------


def test_solver_records_the_reservation_it_compiled(site_and_lib: tuple) -> None:
    """T6 (D61): the schedule carries (commitment_id, committed kW) for every compiled award.

    `valuation._local_flex_value` bills this record, so it is what makes the invoiced MW the MW the
    dispatch reserved. It dies if `_solved` stops assigning ``flex_reserved`` or stops passing
    ``local_flex_reserved_kw=`` to the schedule (the record would read ``None`` and the valuation
    would fall back to the seam's default reservation).
    """
    site, lib = site_and_lib
    flex = _commitment(stream=MarketStream.LOCAL_FLEX_PICLO, committed_kw=50.0)
    descriptor = _descriptor("availability_window", flex)
    result, schedule = _solve(site, lib, [descriptor])
    assert result.solver_status.value == "optimal"
    assert descriptor.parameters is not None
    assert schedule.local_flex_reserved_kw == (
        (descriptor.parameters.commitment_id, descriptor.parameters.committed_power_kw),
    )
    assert schedule.local_flex_reserved_kw == (("cm_test", 50.0),)

    # No local-flex award compiled: no record. A Capacity Market reservation is a DIFFERENT
    # reservation (it sets `deliverable_capacity_mw`) and must not populate this one.
    _, cm_schedule = _solve(
        site, lib, [_descriptor("availability_window", _commitment(committed_kw=150.0))]
    )
    assert cm_schedule.deliverable_capacity_mw == pytest.approx(0.15)
    assert cm_schedule.local_flex_reserved_kw is None
    _, bare = _solve(site, lib, [])
    assert bare.local_flex_reserved_kw is None
