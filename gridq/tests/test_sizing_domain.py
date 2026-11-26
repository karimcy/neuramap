"""Stage 1 of the adaptive sizing search (issue 016): the continuous `SizingDomain`.

Covers the data-integrity invariants that matter for a wider, trustworthy search: invalid
bounds are rejected, the dense grid is deterministic / in-band / ordered, the Latin-hypercube
sample is deterministic (seeded) and stays in the domain, and energy round-trips through the
existing `SizingCandidate` (so the wider search feeds the same exact-LP evaluator unchanged).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from gridq.schemas import SiteModel
from gridq.sizing.domain import (
    DEFAULT_DURATION_BAND_H,
    DEFAULT_POWER_FRACTION_RANGE,
    SizingDomain,
)


def _domain() -> SizingDomain:
    # Band intersects the box across the whole power range, so every LHS sample is in-band.
    return SizingDomain(
        power_kw_min=50.0, power_kw_max=500.0, energy_kwh_min=50.0, energy_kwh_max=2000.0
    )


def test_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):  # min ≥ max
        SizingDomain(power_kw_min=500, power_kw_max=50, energy_kwh_min=50, energy_kwh_max=2000)
    with pytest.raises(ValueError):  # non-positive
        SizingDomain(power_kw_min=-1, power_kw_max=50, energy_kwh_min=50, energy_kwh_max=2000)
    with pytest.raises(ValueError):  # bad duration band
        SizingDomain(
            power_kw_min=50,
            power_kw_max=500,
            energy_kwh_min=50,
            energy_kwh_max=2000,
            duration_h_min=8.0,
            duration_h_max=0.5,
        )


def test_grid_is_deterministic_in_band_and_ordered() -> None:
    d = _domain()
    g1 = d.grid(8, 8)
    g2 = d.grid(8, 8)
    assert [(c.power_kw, c.duration_hours) for c in g1] == [
        (c.power_kw, c.duration_hours) for c in g2
    ], "grid must be deterministic"
    assert g1, "grid must produce candidates"
    assert len(g1) < 8 * 8, "duration band must exclude some out-of-band corners"
    for c in g1:
        assert d.contains(c.power_kw, c.energy_kwh)
        assert (
            DEFAULT_DURATION_BAND_H[0] - 1e-6
            <= c.duration_hours
            <= DEFAULT_DURATION_BAND_H[1] + 1e-6
        )
    keys = [(c.power_kw, c.energy_kwh) for c in g1]
    assert keys == sorted(keys), "grid must be in sorted (power, energy) order"


def test_latin_hypercube_deterministic_and_in_domain() -> None:
    d = _domain()
    s1 = d.latin_hypercube(32, seed=0)
    s2 = d.latin_hypercube(32, seed=0)
    s3 = d.latin_hypercube(32, seed=1)
    assert len(s1) == 32
    same_seed_equal = [(c.power_kw, c.duration_hours) for c in s1] == [
        (c.power_kw, c.duration_hours) for c in s2
    ]
    assert same_seed_equal, "same seed must give identical samples"
    diff_seed_differ = [(c.power_kw, c.duration_hours) for c in s1] != [
        (c.power_kw, c.duration_hours) for c in s3
    ]
    assert diff_seed_differ, "a different seed must give different samples"
    for c in s1:
        assert d.contains(c.power_kw, c.energy_kwh)


def test_energy_round_trips_through_candidate() -> None:
    d = _domain()
    for c in d.grid(5, 5):
        # energy = power × duration must reproduce the requested energy.
        assert abs(c.energy_kwh - c.power_kw * c.duration_hours) < 1e-3


def test_latin_hypercube_stays_in_band_when_box_clips_band() -> None:
    # At high power this box excludes the band (p·d_min > energy_max) — the old clamp branch
    # emitted out-of-band candidates here; LHS must now sample power only within the
    # band-feasible window, so every sample satisfies contains() (grid/LHS share one semantics).
    d = SizingDomain(
        power_kw_min=50.0, power_kw_max=1000.0, energy_kwh_min=100.0, energy_kwh_max=400.0
    )
    samples = d.latin_hypercube(64, seed=0)
    assert len(samples) == 64
    for c in samples:
        assert d.contains(c.power_kw, c.energy_kwh), (
            f"LHS sample ({c.power_kw} kW, {c.energy_kwh} kWh, {c.duration_hours} h) "
            "falls outside the domain"
        )
    # The feasible power window is [50, 400/0.5=800]; no sample may exceed it.
    assert max(c.power_kw for c in samples) <= 800.0 + 1e-6


def test_rejects_box_disjoint_from_duration_band() -> None:
    # No power in [10, 20] kW admits an energy in [1000, 2000] kWh within 0.5–8 h
    # (20 kW × 8 h = 160 kWh < 1000 kWh) — reject at construction, don't sample around it.
    with pytest.raises(ValueError, match="never intersects the duration band"):
        SizingDomain(
            power_kw_min=10.0, power_kw_max=20.0, energy_kwh_min=1000.0, energy_kwh_max=2000.0
        )


def test_from_site_scales_to_observed_peak(make_site: Callable[..., SiteModel]) -> None:
    # T2 fixture: observed peak 420 kW (binding), MIC 300 kW — peak wins, same basis as
    # MilpEngine._default_search_space.
    d = SizingDomain.from_site(make_site(with_max_import=True))
    assert d.power_kw_max == pytest.approx(420.0)
    assert d.power_kw_min == pytest.approx(DEFAULT_POWER_FRACTION_RANGE[0] * 420.0)
    # Energy box is the band-spanning envelope of the power box.
    assert d.energy_kwh_min == pytest.approx(d.power_kw_min * d.duration_h_min)
    assert d.energy_kwh_max == pytest.approx(d.power_kw_max * d.duration_h_max)
    assert d.grid(6, 6), "a site-scaled domain must yield usable candidates"


def test_from_site_falls_back_to_connection_limit(make_site: Callable[..., SiteModel]) -> None:
    # T0 fixture: no observed peak — the connection MIC (300 kW) is the honest fallback basis.
    d = SizingDomain.from_site(make_site(with_max_import=False))
    assert d.power_kw_max == pytest.approx(300.0)


def test_from_site_refuses_without_sizing_basis(make_site: Callable[..., SiteModel]) -> None:
    # No peak AND no connection limit → a data-ask refusal, never a fabricated default kW.
    site = make_site(with_max_import=False)
    site = site.model_copy(
        update={"connection": site.connection.model_copy(update={"mic_kw": 0.0})}
    )
    with pytest.raises(ValueError, match="no sizing basis"):
        SizingDomain.from_site(site)


def test_from_site_rejects_bad_fraction_range(make_site: Callable[..., SiteModel]) -> None:
    with pytest.raises(ValueError, match="power_fraction_range"):
        SizingDomain.from_site(make_site(with_max_import=True), power_fraction_range=(0.5, 0.5))
