"""WP-SITE-01 acceptance 2: half-hourly and one-minute series, with stated uncertainty bands.

- The half-hourly ensemble: 48 intervals a day, an expected (mean) series, ordered pointwise bands
  P05…P95, and a simulation-error interval that brackets every band edge.
- The bands come from the **stated** input uncertainty: with every input stated as certain they
  have zero width, and widening one stated range widens them. No width is invented.
- The one-minute series reproduces every half-hour of the half-hourly series exactly (the
  conditioning), is the same series whatever window is asked for, keeps every minute inside
  ``[0, energised IT capacity]``, and reports its Jensen gap and realised SD.
- Too few draws for the stated simulation-error interval are refused.
- The files carry CORE's ``timestamp,import_kwh`` format and a manifest.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from tests._datacentre_support import (
    DRAWS,
    SEED,
    day_shape,
    fixed,
    make_inputs,
    uniform,
)

from gridq.datacentre import (
    CoolingRegime,
    CoolingResponse,
    Synthesis,
    Utilisation,
    Variability,
    synthesise,
)
from gridq.datacentre.derive import write_outputs
from gridq.datacentre.synthesiser import LEVEL_NAMES, LEVELS, minimum_draws

LEVEL_ORDER = [LEVEL_NAMES[p] for p in LEVELS]


@pytest.fixture(scope="module")
def ensemble() -> Synthesis:
    return synthesise(make_inputs(), seed=SEED, draws=DRAWS)


def _certain_inputs():  # type: ignore[no-untyped-def]
    """Every input of the shared fixture restated as certain, and no one-minute variability."""
    base = make_inputs()
    phases = tuple(
        p.model_copy(update={"fill_days": fixed(2.0), "slip_increment_days": fixed(0.5)})
        for p in base.phases
    )
    ups = base.ups.model_copy(
        update={
            "efficiency": base.ups.efficiency.model_copy(
                update={
                    "points": tuple(
                        pt.model_copy(update={"efficiency": fixed(0.95)})
                        for pt in base.ups.efficiency.points  # type: ignore[union-attr]
                    )
                }
            )
        }
    )
    cooling = base.cooling.model_copy(
        update={
            "distribution_heat_fraction": fixed(0.8),
            "switch_temperature_c": fixed(14.0),
            "regimes": (
                CoolingRegime(
                    coefficients=(fixed(8.0), fixed(0.06), fixed(0.5), fixed(0.0)),
                    duty_points=None,
                ),
                CoolingRegime(
                    coefficients=(fixed(10.0), fixed(0.18), fixed(0.9), fixed(0.002)),
                    duty_points=None,
                ),
            ),
        }
    )
    return base.model_copy(
        update={
            "phases": phases,
            "utilisation": base.utilisation.model_copy(update={"level_scale": fixed(1.0)}),
            "ups": ups,
            "distribution_loss": base.distribution_loss.model_copy(
                update={"fraction": fixed(0.02)}
            ),
            "auxiliaries": tuple(
                a.model_copy(update={"value": fixed(15.0 if a.basis == "fixed_kw" else 0.005)})
                for a in base.auxiliaries
            ),
            "cooling": cooling,
            "variability": Variability(
                mode="none", sd_fraction=None, correlation_minutes=None, dips=None
            ),
        }
    )


# --------------------------------------------------------------------------- half-hourly


def test_half_hourly_ensemble_shape_and_expected(ensemble: Synthesis) -> None:
    inputs = ensemble.inputs
    intervals = inputs.days * 48
    assert ensemble.members_kw.shape == (DRAWS, intervals)
    assert len(ensemble.timestamps) == intervals
    assert ensemble.timestamps[0] == datetime(2027, 3, 1)
    steps = zip(ensemble.timestamps, ensemble.timestamps[1:], strict=False)
    assert all(b - a == timedelta(minutes=30) for a, b in steps)
    # The central series is the per-interval mean, and it is labelled expected, never P50.
    np.testing.assert_allclose(ensemble.expected_kw, ensemble.members_kw.mean(axis=0))
    assert "P50" in ensemble.bands_kw and not np.allclose(
        ensemble.bands_kw["P50"], ensemble.expected_kw
    )
    # Facility power is the sum of its components.
    total = sum(ensemble.components_kw.values())
    np.testing.assert_allclose(total, ensemble.members_kw)
    # Before the first energisation (1 March, P1 is planned for the 2nd) there is no load at all.
    assert np.all(ensemble.members_kw[:, :48] == 0.0)
    assert ensemble.expected_kw[-48:].min() > 0.0


def test_bands_are_ordered_and_bracketed_by_their_simulation_error(ensemble: Synthesis) -> None:
    bands = [ensemble.bands_kw[name] for name in LEVEL_ORDER]
    for lower, upper in zip(bands, bands[1:], strict=False):
        assert np.all(lower <= upper + 1e-9)
    for name in LEVEL_ORDER:
        lo, hi = ensemble.band_ci_kw[name]
        assert np.all(lo <= ensemble.bands_kw[name] + 1e-9)
        assert np.all(ensemble.bands_kw[name] <= hi + 1e-9)
    assert ensemble.bands_kw["P05"].max() < ensemble.bands_kw["P95"].max()
    assert 0 <= ensemble.medoid_draw < DRAWS


def test_aggregates_are_per_draw_then_quantiled(ensemble: Synthesis) -> None:
    by_name = {a.name: a for a in ensemble.aggregates}
    energy = ensemble.members_kw.sum(axis=1) * 0.5 / 1000
    horizon = by_name["energy_horizon"]
    assert horizon.unit == "MWh"
    assert horizon.expected == pytest.approx(float(energy.mean()))
    assert horizon.quantiles["P95"] == pytest.approx(
        float(np.quantile(energy, 0.95, method="inverted_cdf"))
    )
    for name in LEVEL_ORDER:
        lo, hi = horizon.quantile_ci[name]
        assert lo <= horizon.quantiles[name] <= hi
    # A partial month says so, and the one-minute peak is at least the half-hourly peak.
    assert "energy_month_2027-03 (10 of 31 days)" in by_name
    assert by_name["peak_one_minute"].expected >= by_name["peak_half_hourly"].expected


def test_attribution_ranks_the_data_ask(ensemble: Synthesis) -> None:
    for attribution in ensemble.attribution:
        labels = [s[0] for s in attribution.shares]
        assert sorted(labels) == sorted(ensemble.dimensions)
        adjusted = [s[1] for s in attribution.shares]
        assert adjusted == sorted(adjusted, reverse=True)
        assert attribution.binning_bias == pytest.approx((attribution.bins - 1) / DRAWS)
    peak = next(a for a in ensemble.attribution if a.output == "peak_half_hourly")
    # The utilisation level scales the whole IT load; nothing else comes close for the peak.
    assert peak.shares[0][0] == "utilisation.level_scale"


def test_certain_inputs_give_zero_width_bands() -> None:
    """The bands are the stated uncertainty propagated, and nothing else: state none, get none."""
    certain = synthesise(_certain_inputs(), seed=SEED, draws=DRAWS)
    assert certain.dimensions == ()
    for name in LEVEL_ORDER:
        np.testing.assert_allclose(certain.bands_kw[name], certain.expected_kw, atol=1e-9)
    assert all(a.quantiles["P05"] == pytest.approx(a.quantiles["P95"]) for a in certain.aggregates)


def test_widening_a_stated_range_widens_the_band() -> None:
    narrow = _certain_inputs()
    narrow = narrow.model_copy(
        update={
            "utilisation": narrow.utilisation.model_copy(update={"level_scale": uniform(0.98, 1.0)})
        }
    )
    wide = narrow.model_copy(
        update={
            "utilisation": narrow.utilisation.model_copy(update={"level_scale": uniform(0.8, 1.0)})
        }
    )
    a = synthesise(narrow, seed=SEED, draws=DRAWS)
    b = synthesise(wide, seed=SEED, draws=DRAWS)
    width_a = (a.bands_kw["P95"] - a.bands_kw["P05"]).max()
    width_b = (b.bands_kw["P95"] - b.bands_kw["P05"]).max()
    assert width_a > 0
    assert width_b > 5 * width_a


def test_too_few_draws_are_refused() -> None:
    assert minimum_draws() == 72
    with pytest.raises(ValueError, match="at least 72 draws"):
        synthesise(make_inputs(), seed=SEED, draws=71)


# --------------------------------------------------------------------------- one minute


def test_one_minute_reproduces_every_half_hour_exactly(ensemble: Synthesis) -> None:
    day = 7
    start = datetime(2027, 3, 1) + timedelta(days=day)
    window = ensemble.one_minute(start, start + timedelta(days=1), jensen_gap_flag_kw=None)
    assert window.members_kw.shape == (DRAWS, 1440)
    block_means = window.members_kw.reshape(DRAWS, 48, 30).mean(axis=2)
    np.testing.assert_allclose(
        block_means, ensemble.members_kw[:, day * 48 : (day + 1) * 48], rtol=0, atol=1e-6
    )
    it_means = window.it_members_kw.reshape(DRAWS, 48, 30).mean(axis=2)
    np.testing.assert_allclose(
        it_means, ensemble.components_kw["it"][:, day * 48 : (day + 1) * 48], rtol=0, atol=1e-6
    )
    # ...and it is not just the step: the stated variability is there, and so is its SD.
    assert np.abs(window.members_kw - np.repeat(block_means, 30, axis=1)).max() > 1.0
    assert window.realised_sd_fraction is not None and 0.0 < window.realised_sd_fraction < 0.05
    assert window.cooling_cap_residual_kw < 1e-6
    assert set(window.bands_kw) == set(LEVEL_ORDER)


def test_any_window_is_a_slice_of_the_same_series(ensemble: Synthesis) -> None:
    day = datetime(2027, 3, 8)
    whole = ensemble.one_minute(day, day + timedelta(days=2), jensen_gap_flag_kw=None)
    part = ensemble.one_minute(
        day + timedelta(hours=23, minutes=17),
        day + timedelta(days=1, hours=2, minutes=3),
        jensen_gap_flag_kw=None,
    )
    lo = 23 * 60 + 17
    width = part.members_kw.shape[1]
    np.testing.assert_array_equal(part.members_kw, whole.members_kw[:, lo : lo + width])


def test_jensen_gap_is_reported_and_flagged_only_against_a_stated_threshold(
    ensemble: Synthesis,
) -> None:
    start = datetime(2027, 3, 9)
    unflagged = ensemble.one_minute(start, start + timedelta(hours=6), jensen_gap_flag_kw=None)
    assert unflagged.jensen_flagged_blocks is None
    assert unflagged.jensen_gap_kw.shape == (DRAWS, 12)
    assert np.abs(unflagged.jensen_gap_kw).max() > 0
    flagged = ensemble.one_minute(start, start + timedelta(hours=6), jensen_gap_flag_kw=0.0)
    assert flagged.jensen_flagged_blocks == int(np.count_nonzero(np.abs(flagged.jensen_gap_kw) > 0))
    none = ensemble.one_minute(start, start + timedelta(hours=6), jensen_gap_flag_kw=1e9)
    assert none.jensen_flagged_blocks == 0


def test_minutes_stay_inside_energised_capacity_by_shrinking_and_counting() -> None:
    """Utilisation at 100% leaves no headroom above the half-hour: the deviations must shrink."""
    full = make_inputs(
        utilisation=Utilisation(
            workload="flat out",
            weekday=(1.0,) * 48,
            saturday=(1.0,) * 48,
            sunday=(1.0,) * 48,
            level_scale=fixed(1.0),
        )
    )
    result = synthesise(full, seed=SEED, draws=DRAWS)
    assert result.flags["one_minute_shrunk_blocks"] > 0
    rated = sum(p.rated_it_kw for p in full.phases)
    window = result.one_minute(datetime(2027, 3, 9), datetime(2027, 3, 10), jensen_gap_flag_kw=None)
    assert window.it_members_kw.max() <= rated + 1e-6
    assert window.it_members_kw.min() >= 0.0
    np.testing.assert_allclose(
        window.it_members_kw.reshape(DRAWS, 48, 30).mean(axis=2),
        result.components_kw["it"][:, 8 * 48 : 9 * 48],
        atol=1e-6,
    )


def test_no_variability_is_a_step_and_has_no_jensen_gap() -> None:
    step = make_inputs(
        variability=Variability(mode="none", sd_fraction=None, correlation_minutes=None, dips=None),
        cooling=make_inputs().cooling.model_copy(
            update={"response": CoolingResponse(mode="instantaneous", time_constant_minutes=None)}
        ),
    )
    result = synthesise(step, seed=SEED, draws=DRAWS)
    window = result.one_minute(datetime(2027, 3, 9), datetime(2027, 3, 10), jensen_gap_flag_kw=None)
    np.testing.assert_allclose(
        window.members_kw, np.repeat(result.members_kw[:, 8 * 48 : 9 * 48], 30, axis=1), atol=1e-9
    )
    assert np.abs(window.jensen_gap_kw).max() < 1e-9
    assert window.realised_sd_fraction is None


def test_windows_outside_the_horizon_are_refused(ensemble: Synthesis) -> None:
    with pytest.raises(ValueError, match="not inside the horizon"):
        ensemble.one_minute(datetime(2027, 2, 28), datetime(2027, 3, 1, 1), jensen_gap_flag_kw=None)
    with pytest.raises(ValueError, match="whole minutes"):
        ensemble.one_minute(
            datetime(2027, 3, 2, 0, 0, 30), datetime(2027, 3, 2, 1), jensen_gap_flag_kw=None
        )


# --------------------------------------------------------------------------- files


def test_files_carry_cores_format_and_a_manifest(ensemble: Synthesis, tmp_path: Path) -> None:
    files = write_outputs(ensemble, tmp_path)
    lines = files.expected_csv.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "timestamp,import_kwh"
    assert len(lines) == 1 + ensemble.inputs.days * 48
    assert lines[1].startswith("2027-03-01 00:00:00,")
    assert float(lines[-1].split(",")[1]) == pytest.approx(ensemble.expected_kw[-1] * 0.5, abs=1e-6)
    header = files.bands_csv.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header[:2] == ["timestamp", "expected_kw"]
    assert "P05_ci_low_kw" in header and "P95_kw" in header
    members = np.load(files.members_npy)
    np.testing.assert_array_equal(members, ensemble.members_kw)
    manifest = json.loads(files.manifest_json.read_text(encoding="utf-8"))
    assert manifest["series_hash"] == ensemble.series_hash
    assert manifest["method"]["seed"] == SEED and manifest["method"]["draws"] == DRAWS
    definition = manifest["method"]["quantile_definition"]
    assert definition.startswith("numpy.quantile(method='inverted_cdf'")
    assert manifest["expected_label"].startswith("expected (per-interval mean")
    assert set(manifest["data_requests"]) >= {"utilisation", "cooling", "ups", "variability"}


def test_utilisation_shape_must_be_stated_whole() -> None:
    with pytest.raises(ValueError, match="needs 48 half-hourly values"):
        Utilisation(
            workload="x",
            weekday=day_shape(0.5, 0.1)[:47],
            saturday=day_shape(0.5, 0.1),
            sunday=day_shape(0.5, 0.1),
            level_scale=fixed(1.0),
        )
