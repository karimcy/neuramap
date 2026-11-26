"""Tests for the sizing SearchSpace + SizingCandidate (M4 grid)."""

from __future__ import annotations

import pytest

from gridq.sizing import DEFAULT_DURATION_HOURS, SearchSpace, SizingCandidate


def test_candidate_derives_energy() -> None:
    c = SizingCandidate(power_kw=100.0, duration_hours=2.0)
    assert c.energy_kwh == 200.0


@pytest.mark.parametrize("power", [0.0, -1.0])
def test_candidate_rejects_non_positive_power(power: float) -> None:
    with pytest.raises(ValueError):
        SizingCandidate(power_kw=power, duration_hours=2.0)


@pytest.mark.parametrize("duration", [0.0, -2.0])
def test_candidate_rejects_non_positive_duration(duration: float) -> None:
    with pytest.raises(ValueError):
        SizingCandidate(power_kw=100.0, duration_hours=duration)


def test_search_space_rejects_negative_and_zero() -> None:
    with pytest.raises(ValueError):
        SearchSpace(power_kw_candidates=[100.0, -50.0], duration_hour_candidates=[2.0])
    with pytest.raises(ValueError):
        SearchSpace(power_kw_candidates=[100.0], duration_hour_candidates=[0.0])


def test_search_space_dedupes_and_sorts() -> None:
    space = SearchSpace(
        power_kw_candidates=[200.0, 100.0, 100.0, 50.0],
        duration_hour_candidates=[4.0, 2.0, 2.0],
    )
    assert space.power_kw_candidates == [50.0, 100.0, 200.0]
    assert space.duration_hour_candidates == [2.0, 4.0]
    assert len(space) == 6


def test_candidates_are_deterministic_cartesian_order() -> None:
    space = SearchSpace(power_kw_candidates=[100.0, 50.0], duration_hour_candidates=[2.0, 1.0])
    cands = space.candidates()
    # ordered by (power_kw, duration_hours) ascending — not by derived energy
    assert [(c.power_kw, c.duration_hours) for c in cands] == [
        (50.0, 1.0),
        (50.0, 2.0),
        (100.0, 1.0),
        (100.0, 2.0),
    ]
    # re-running yields the identical order
    assert [(c.power_kw, c.duration_hours) for c in space.candidates()] == [
        (c.power_kw, c.duration_hours) for c in cands
    ]


def test_energy_ties_keep_distinct_candidates() -> None:
    # 100kW×4h and 200kW×2h both = 400 kWh but are distinct candidates, ordered by (power, duration)
    space = SearchSpace(power_kw_candidates=[100.0, 200.0], duration_hour_candidates=[2.0, 4.0])
    energies = {(c.power_kw, c.duration_hours): c.energy_kwh for c in space.candidates()}
    assert energies[(100.0, 4.0)] == 400.0
    assert energies[(200.0, 2.0)] == 400.0
    assert len(space.candidates()) == 4


def test_default_duration_grid_ties_to_library_keys() -> None:
    # structural default matches the assumption-library duration keys '1'/'2'/'4'
    assert DEFAULT_DURATION_HOURS == (1.0, 2.0, 4.0)
    space = SearchSpace(power_kw_candidates=[100.0])
    assert space.duration_hour_candidates == [1.0, 2.0, 4.0]


def test_empty_grid_yields_no_candidates() -> None:
    space = SearchSpace(power_kw_candidates=[], duration_hour_candidates=[2.0])
    assert len(space) == 0
    assert space.candidates() == []


def test_non_numeric_candidate_rejected() -> None:
    with pytest.raises(TypeError):
        SearchSpace(power_kw_candidates=["100"], duration_hour_candidates=[2.0])  # type: ignore[list-item]
