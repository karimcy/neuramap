"""Tests for BatteryState, BatteryModel Protocol, and ConstantEfficiencyBatteryModel."""

from __future__ import annotations

import pytest

from gridq.optimisation.battery_model import (
    BatteryModel,
    BatteryState,
    ConstantEfficiencyBatteryModel,
)

_RTE = 0.88
_ENERGY = 200.0  # kWh
_DT = 0.5  # 30-min interval


@pytest.fixture
def model():
    return ConstantEfficiencyBatteryModel(rte=_RTE, energy_kwh=_ENERGY, soc_min=0.05)


def test_protocol_satisfied(model):
    assert isinstance(model, BatteryModel)


def test_charge_step_advances_soe(model):
    state = model.step(charge_kw=100.0, discharge_kw=0.0, soe_kwh=50.0, dt_hours=_DT)
    # soe += 100 * 0.88 * 0.5 = +44 kWh
    assert state.actual_soe_kwh == pytest.approx(94.0, abs=1e-6)
    assert state.actual_charge_kw == pytest.approx(100.0, abs=1e-6)
    assert state.actual_discharge_kw == pytest.approx(0.0, abs=1e-6)
    assert state.warnings == []


def test_discharge_step_reduces_soe(model):
    state = model.step(charge_kw=0.0, discharge_kw=80.0, soe_kwh=100.0, dt_hours=_DT)
    # soe -= 80 * 0.5 = -40 kWh
    assert state.actual_soe_kwh == pytest.approx(60.0, abs=1e-6)
    assert state.actual_charge_kw == pytest.approx(0.0, abs=1e-6)
    assert state.actual_discharge_kw == pytest.approx(80.0, abs=1e-6)
    assert state.warnings == []


def test_idle_step_holds_soe(model):
    state = model.step(charge_kw=0.0, discharge_kw=0.0, soe_kwh=120.0, dt_hours=_DT)
    assert state.actual_soe_kwh == pytest.approx(120.0, abs=1e-6)
    assert state.warnings == []


def test_upper_soc_clip_on_overcharge(model):
    # Start near full: 190 kWh.  100 kW charge for 0.5 h → raw +44 → would overshoot 200.
    state = model.step(charge_kw=100.0, discharge_kw=0.0, soe_kwh=190.0, dt_hours=_DT)
    assert state.actual_soe_kwh == pytest.approx(200.0, abs=1e-6)
    # Actual charge back-computed from the clipped delta.
    assert state.actual_charge_kw < 100.0
    assert len(state.warnings) == 1
    assert "SOC upper bound" in state.warnings[0]


def test_lower_soc_clip_on_overdischarge(model):
    # Start near min: 12 kWh (> 5 % = 10 kWh).  100 kW discharge → would go negative.
    state = model.step(charge_kw=0.0, discharge_kw=100.0, soe_kwh=12.0, dt_hours=_DT)
    assert state.actual_soe_kwh == pytest.approx(10.0, abs=1e-6)  # clipped to soc_min
    assert state.actual_discharge_kw < 100.0
    assert len(state.warnings) == 1
    assert "SOC lower bound" in state.warnings[0]


def test_no_warning_when_exactly_at_bounds(model):
    # Clip to exactly full — no warning when delta is within tolerance.
    state = model.step(charge_kw=0.0, discharge_kw=0.0, soe_kwh=200.0, dt_hours=_DT)
    assert state.actual_soe_kwh == pytest.approx(200.0)
    assert state.warnings == []


def test_battery_state_frozen():
    s = BatteryState(actual_soe_kwh=100.0, actual_charge_kw=10.0, actual_discharge_kw=0.0)
    with pytest.raises((AttributeError, TypeError)):
        s.actual_soe_kwh = 0.0  # type: ignore[misc]
