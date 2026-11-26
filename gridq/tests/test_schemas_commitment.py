"""Tests for the availability-commitment schema (W7-1, C026).

The invariants: the shape is validated hard (no degenerate windows, no non-positive
commitments, no unknown fields), it round-trips JSON deterministically, and the typed
`DispatchConstraintDescriptor.parameters` payload is purely additive — every pre-W7-1
descriptor constructs unchanged.
"""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from gridq.schemas import AvailabilityCommitment, AvailabilityWindow, MarketStream
from gridq.value_stack.interface import DispatchConstraintDescriptor


def _window(**overrides: object) -> AvailabilityWindow:
    base: dict = {
        "label": "winter evening peak",
        "months": [11, 12, 1, 2],
        "days_of_week": [0, 1, 2, 3, 4],
        "start_time": time(17, 0),
        "end_time": time(19, 0),
    }
    base.update(overrides)
    return AvailabilityWindow(**base)


def _commitment(**overrides: object) -> AvailabilityCommitment:
    base: dict = {
        "commitment_id": "cm_2027_t4",
        "stream": MarketStream.CAPACITY_MARKET,
        "committed_power_kw": 150.0,
        "held_energy_kwh": 300.0,
        "windows": [_window()],
        "tenor_years": 15.0,
        "counterparty": "EMR Delivery Body",
        "reference": "CM-T4-2029-30/AGR-00000",
    }
    base.update(overrides)
    return AvailabilityCommitment(**base)


def test_valid_commitment_round_trips_json() -> None:
    c = _commitment()
    assert AvailabilityCommitment.model_validate_json(c.model_dump_json()) == c
    assert c.windows[0].months == [1, 2, 11, 12]  # canonicalised sorted order


def test_window_rejects_degenerate_shapes() -> None:
    with pytest.raises(ValidationError, match="end_time must be after start_time"):
        _window(start_time=time(19, 0), end_time=time(17, 0))
    with pytest.raises(ValidationError, match="months must be in 1..12"):
        _window(months=[0, 13])
    with pytest.raises(ValidationError, match="months must be non-empty"):
        _window(months=[])
    with pytest.raises(ValidationError, match="months must not repeat"):
        _window(months=[1, 1])
    with pytest.raises(ValidationError, match="days_of_week must be in 0..6"):
        _window(days_of_week=[7])


def test_commitment_rejects_degenerate_shapes() -> None:
    with pytest.raises(ValidationError):
        _commitment(committed_power_kw=0.0)
    with pytest.raises(ValidationError):
        _commitment(held_energy_kwh=-1.0)
    with pytest.raises(ValidationError):
        _commitment(windows=[])
    with pytest.raises(ValidationError):
        _commitment(tenor_years=0.0)
    with pytest.raises(ValidationError):  # extra='forbid' — a typo is an error, not a no-op
        _commitment(commited_power_kw=100.0)


def test_optional_evidence_and_hold_are_never_defaulted() -> None:
    bare = _commitment(held_energy_kwh=None, tenor_years=None, counterparty=None, reference=None)
    assert bare.held_energy_kwh is None  # no invented energy hold
    assert bare.tenor_years is None  # no invented tenor
    assert bare.reference is None  # a commitment without provenance stays visibly bare


def test_descriptor_parameters_payload_is_additive() -> None:
    # Pre-W7-1 shape constructs unchanged (parameters defaults to None)…
    plain = DispatchConstraintDescriptor(
        constraint_id="connection_charge_limit",
        description="Charging headroom is bounded by min(MIC, ASC) minus site load.",
        kind="connection_limit",
        parameter_source="connection.mic_kw",
    )
    assert plain.parameters is None
    # …and a compilable firming descriptor carries the typed commitment for the W7-2 builder.
    c = _commitment()
    firming = DispatchConstraintDescriptor(
        constraint_id="cm_held_state_of_energy",
        description="Hold committed capability through Capacity Market windows.",
        kind="held_state_of_energy",
        parameters=c,
    )
    assert firming.parameters is not None
    assert firming.parameters.committed_power_kw == 150.0
    assert firming.parameters.windows[0].start_time == time(17, 0)
    round_tripped = DispatchConstraintDescriptor.model_validate_json(firming.model_dump_json())
    assert round_tripped.parameters == c
