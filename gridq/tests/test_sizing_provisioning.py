"""Tests for the provisioning derivation layer (issue 017 stage 2).

Pure and solver-free, but pinned to the solver where it matters: `window_mask` must stay
byte-identical to `MilpDispatchSolver`'s `_commitment_mask` (one calendar semantics for every
firming product — the F-003/C031-#3 tz bug class), and the energy floors are in the solver's
own SoE terms (losses booked on the charge side — no discharge-side η division).
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
import pytest

from gridq.assumptions import load_assumptions
from gridq.schemas import (
    AvailabilityCommitment,
    AvailabilityWindow,
    MarketStream,
    ProvisioningRequirement,
)
from gridq.sizing.provisioning import (
    assess_provisioning,
    end_of_tenor_floors,
    exceedance_levels,
    occurrences,
    recharge_scan,
    unmet_series,
    window_mask,
)

INTERVAL_H = 0.5


def _index(days: int, start: str = "2025-06-02", tz: str | None = None) -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=days * 48, freq="30min", tz=tz)


def _evening_window() -> AvailabilityWindow:
    return AvailabilityWindow(start_time=time(18, 0), end_time=time(19, 0))


def _requirement(**overrides) -> ProvisioningRequirement:
    kwargs: dict = {
        "requirement_id": "prov-t",
        "windows": [_evening_window()],
        "additional_load_kw": 100.0,
        "import_cap_kw": 200.0,
        "tenor_years": 10.0,
    }
    kwargs.update(overrides)
    return ProvisioningRequirement(**kwargs)


# ---------------------------------------------------------------- window_mask


def test_window_mask_parity_with_the_solver_calendar() -> None:
    """One calendar semantics: byte-identical to `_commitment_mask` on a tz-aware summer
    horizon (BST offset live), with month + day-of-week filters and an end-of-day window."""
    from gridq.optimisation.milp_solver import _commitment_mask

    windows = [
        AvailabilityWindow(
            months=[6, 7],
            days_of_week=[0, 1, 2, 3, 4],
            start_time=time(17, 30),
            end_time=time(19, 0),
        ),
        AvailabilityWindow(start_time=time(23, 0), end_time=time(0, 0)),  # end-of-day (W7-5)
    ]
    commitment = AvailabilityCommitment(
        commitment_id="parity",
        stream=MarketStream.CAPACITY_MARKET,
        committed_power_kw=1.0,
        windows=windows,
    )
    for tz in (None, "UTC", "Europe/London"):
        idx = _index(9, tz=tz)  # spans a weekend; June, BST when tz-aware
        ours = window_mask(windows, idx)
        solvers = _commitment_mask(commitment, idx)
        assert np.array_equal(ours, solvers), f"mask diverged from the solver calendar (tz={tz})"
    # and the tz-aware mask reads the LOCAL clock: 17:30 BST is 16:30 UTC
    idx_utc = _index(1, tz="UTC")
    mask = window_mask(windows, idx_utc)
    hit = idx_utc[mask].tz_convert("Europe/London")
    assert all(t.time() >= time(17, 30) for t in hit if t.time() < time(23, 0))


# --------------------------------------------------------------- unmet_series


def test_unmet_is_derived_jointly_and_zero_outside_windows() -> None:
    idx = _index(1)
    mask = window_mask([_evening_window()], idx)
    load = np.full(48, 150.0)  # 50 kW headroom below the 200 kW cap
    unmet = unmet_series(
        load, aux_kw=10.0, additional_load_kw=100.0, import_cap_kw=200.0, mask=mask
    )
    # in-window: 150 + 10 + 100 - 200 = 60 kW (joint shortfall, not the 100 kW addition alone)
    assert unmet[mask].min() == unmet[mask].max() == pytest.approx(60.0)
    assert np.all(unmet[~mask] == 0.0)


def test_no_shortfall_when_the_cap_covers_everything() -> None:
    idx = _index(1)
    mask = window_mask([_evening_window()], idx)
    unmet = unmet_series(
        np.full(48, 50.0), aux_kw=0.0, additional_load_kw=20.0, import_cap_kw=200.0, mask=mask
    )
    assert np.all(unmet == 0.0)


# --------------------------------------------------------- occurrences/floors


def test_occurrences_group_contiguous_runs_with_hand_computed_stats() -> None:
    idx = _index(2)
    mask = window_mask([_evening_window()], idx)  # one 2-interval run per day
    unmet = np.zeros(96)
    unmet[mask] = [40.0, 60.0, 80.0, 20.0]  # day 1: 40/60 kW, day 2: 80/20 kW
    occs = occurrences(unmet, mask, idx, INTERVAL_H)
    assert [o.label for o in occs] == ["2025-06-02", "2025-06-03"]
    assert occs[0].peak_unmet_kw == 60.0
    assert occs[0].unmet_energy_kwh == pytest.approx((40.0 + 60.0) * INTERVAL_H)
    peak, energy = exceedance_levels(occs)
    assert peak == 80.0  # worst occurrence peak
    assert energy == pytest.approx(50.0)  # worst occurrence energy (day 1)


def test_end_of_tenor_floors_apply_the_w7_closed_form() -> None:
    power, energy = end_of_tenor_floors(60.0, 50.0, fade_pct=2.0, tenor_years=10.0)
    retention = 0.98**10
    assert power == pytest.approx(60.0 / retention)
    # SoE terms: NO discharge-side efficiency division (solver books losses on charge)
    assert energy == pytest.approx(50.0 / retention)


def test_unknown_exceedance_basis_is_an_error_not_a_default() -> None:
    with pytest.raises(ValueError, match="exceedance basis"):
        exceedance_levels([], basis="p95")


# -------------------------------------------------------------- recharge_scan


def _scan(load: np.ndarray, days: int, rte: float = 0.88, cap: float = 200.0):
    idx = _index(days)
    mask = window_mask([_evening_window()], idx)
    unmet = unmet_series(load, 0.0, 100.0, cap, mask)
    return recharge_scan(load, 0.0, cap, unmet, mask, idx, INTERVAL_H, rte)


def test_peaky_load_recharges_through_the_gaps() -> None:
    # low base load all day -> huge gap headroom; the 6-7pm shortfall is easily refillable
    result = _scan(np.full(2 * 48, 120.0), days=2)
    assert result.feasible
    assert result.worst_margin_kwh >= 0.0
    assert result.worst_at is None


def test_flat_overload_is_infeasible_at_any_size() -> None:
    # load rides AT the cap all day: zero gap headroom, in-window shortfall = the addition
    result = _scan(np.full(2 * 48, 200.0), days=2)
    assert not result.feasible
    assert result.worst_margin_kwh < 0.0
    assert result.worst_at is not None  # the bottoming-out instant is named


def test_multi_day_running_deficit_is_caught() -> None:
    # each day's gaps refill LESS than the window draws: fine on day 1's margin alone only
    # if you ignore the carry -- the running prefix balance must catch the drift
    idx = _index(4)
    mask = window_mask([_evening_window()], idx)
    load = np.full(4 * 48, 199.0)  # 1 kW headroom in the gaps
    unmet = unmet_series(load, 0.0, 100.0, 200.0, mask)
    result = recharge_scan(load, 0.0, 200.0, unmet, mask, idx, INTERVAL_H, rte=0.88)
    assert not result.feasible
    # daily gap gain 1 kW x 23h x 0.88 = 20.24 kWh vs a 99 kWh window draw -> drifts down;
    # the running balance bottoms out at the CLOSE of day 4's window (before that evening's
    # refill): 3 full days' gain + day 4's pre-window gain (36 intervals) minus 4 draws
    assert result.worst_margin_kwh == pytest.approx(
        3 * (46 * 1.0 * INTERVAL_H * 0.88)
        + 36 * (1.0 * INTERVAL_H * 0.88)
        - 4 * (2 * 99.0 * INTERVAL_H),
        rel=1e-6,
    )


def test_recharge_scan_rejects_a_nonsense_rte() -> None:
    idx = _index(1)
    mask = window_mask([_evening_window()], idx)
    with pytest.raises(ValueError, match="rte"):
        recharge_scan(np.zeros(48), 0.0, 200.0, np.zeros(48), mask, idx, INTERVAL_H, rte=1.3)


# ---------------------------------------------------------- assess (orchestr.)


def test_assessment_happy_path_on_a_real_release() -> None:
    idx = _index(3)
    load = np.full(3 * 48, 150.0)
    req = _requirement()
    a = assess_provisioning(req, load, 0.0, idx, INTERVAL_H, load_assumptions("gb_v0_1"))
    assert a.assessable
    assert a.floors is not None and a.recharge is not None
    # 150 + 100 - 200 = 50 kW shortfall, 1h window -> 50 kWh, floored up by 10y fade
    assert a.floors.worst_peak_kw == pytest.approx(50.0)
    assert a.floors.worst_energy_kwh == pytest.approx(50.0)
    assert a.floors.power_floor_kw > 50.0  # end-of-tenor uplift applied
    assert a.recharge.feasible
    # determinism: identical inputs -> identical outputs
    b = assess_provisioning(req, load, 0.0, idx, INTERVAL_H, load_assumptions("gb_v0_1"))
    assert a == b


def test_assessment_without_any_import_cap_is_honestly_unassessable() -> None:
    req = _requirement(import_cap_kw=None)
    a = assess_provisioning(
        req, np.full(48, 150.0), 0.0, _index(1), INTERVAL_H, load_assumptions("gb_v0_1")
    )
    assert not a.assessable
    assert a.floors is None and a.recharge is None
    assert any("never defaulted" in w for w in a.warnings)


def test_assessment_uses_the_site_cap_when_the_requirement_leaves_it_none() -> None:
    req = _requirement(import_cap_kw=None)
    a = assess_provisioning(
        req,
        np.full(48, 150.0),
        0.0,
        _index(1),
        INTERVAL_H,
        load_assumptions("gb_v0_1"),
        site_max_import_kw=200.0,
    )
    assert a.assessable
    assert a.floors is not None and a.floors.worst_peak_kw == pytest.approx(50.0)


def test_no_tenor_means_day_one_floors_with_a_note() -> None:
    req = _requirement(tenor_years=None)
    a = assess_provisioning(
        req, np.full(48, 150.0), 0.0, _index(1), INTERVAL_H, load_assumptions("gb_v0_1")
    )
    assert a.floors is not None
    assert a.floors.power_floor_kw == pytest.approx(a.floors.worst_peak_kw)
    assert any("day-one" in n for n in a.floors.notes)


def test_no_observed_shortfall_is_a_warning_not_an_invention() -> None:
    req = _requirement(additional_load_kw=None, serve_metered_shortfall=True)
    a = assess_provisioning(
        req, np.full(48, 50.0), 0.0, _index(1), INTERVAL_H, load_assumptions("gb_v0_1")
    )
    assert a.assessable
    assert a.floors is not None and a.floors.power_floor_kw == 0.0
    assert any("demands nothing on this horizon" in w for w in a.warnings)


def test_recharge_infeasibility_recommends_reinforcement() -> None:
    req = _requirement()
    a = assess_provisioning(
        req, np.full(2 * 48, 200.0), 0.0, _index(2), INTERVAL_H, load_assumptions("gb_v0_1")
    )
    assert a.recharge is not None and not a.recharge.feasible
    assert any("connection reinforcement" in w for w in a.warnings)


# ------------------------------------------------- manual daily curve (D42)


def test_daily_profile_maps_by_local_clock_slot() -> None:
    from gridq.sizing.provisioning import daily_profile_series

    profile = [0.0] * 48
    profile[36] = 700.0  # 18:00 local
    for tz in (None, "UTC"):
        idx = _index(1, tz=tz)  # June: 18:00 BST = 17:00 UTC when tz-aware
        series = daily_profile_series(profile, idx)
        local = idx if tz is None else idx.tz_convert("Europe/London")
        hit = np.flatnonzero(series == 700.0)
        assert len(hit) == 1
        assert local[hit[0]].hour == 18 and local[hit[0]].minute == 0


def test_assessment_with_an_hgv_daily_curve() -> None:
    """The HGV-depot shape: 2x350 kW chargers 18:00-20:00 tapering to one 20:00-21:00,
    on a 200 kW cap with 150 kW base load — hand-computed floors."""
    profile = [0.0] * 48
    for slot in range(36, 40):
        profile[slot] = 700.0
    for slot in (40, 41):
        profile[slot] = 350.0
    req = ProvisioningRequirement(
        requirement_id="prov-hgv",
        windows=[AvailabilityWindow(start_time=time(18, 0), end_time=time(21, 0))],
        additional_load_profile_daily_kw=profile,
        import_cap_kw=200.0,
    )
    idx = _index(2)
    a = assess_provisioning(
        req, np.full(2 * 48, 150.0), 0.0, idx, INTERVAL_H, load_assumptions("gb_v0_1")
    )
    assert a.assessable and a.floors is not None
    # slots 36-39: 150+700-200 = 650 kW; slots 40-41: 150+350-200 = 300 kW
    assert a.floors.worst_peak_kw == pytest.approx(650.0)
    assert a.floors.worst_energy_kwh == pytest.approx((4 * 650.0 + 2 * 300.0) * INTERVAL_H)
