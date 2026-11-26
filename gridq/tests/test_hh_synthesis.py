"""Half-hourly synthesis — the modelled branch of validation Gate 2.

Generating a shape where no meter export exists is standard practice, not a degradation. What
makes it defensible is that the *shape* is the only assumption: every unit of energy comes from the
site's own measured totals, and the result reconciles to them exactly.
"""

from __future__ import annotations

import pytest

from gridq.ingestion.hh_mandate import HalfHourlyBasis
from gridq.ingestion.hh_synthesis import (
    PROFILE_LIBRARY,
    ProfileSource,
    SynthesisError,
    synthesise_half_hourly,
)

MONTHLY_2025 = {
    f"2025-{m:02d}": v
    for m, v in zip(
        range(1, 13),
        [
            52_000,
            47_000,
            48_000,
            42_000,
            40_000,
            38_000,
            39_000,
            40_000,
            43_000,
            48_000,
            51_000,
            54_000,
        ],
        strict=True,
    )
}


def _synth(tmp_path, **kw):
    return synthesise_half_hourly(
        site_id="test_site", profile="workshop_depot", year=2025, out_dir=tmp_path, **kw
    )


# --- exact reconciliation: the property the whole module rests on ----------------------------


def test_monthly_totals_are_reproduced_exactly(tmp_path) -> None:
    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    assert result.total_kwh == pytest.approx(sum(MONTHLY_2025.values()), abs=0.01)


def test_each_individual_month_reconciles_not_just_the_year(tmp_path) -> None:
    """A year-total match could hide a seasonal shape that is wholly invented."""
    import csv
    from collections import defaultdict

    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    by_month: dict[str, float] = defaultdict(float)
    with result.path.open() as fh:
        for row in csv.DictReader(fh):
            by_month[row["timestamp"][:7]] += float(row["import_kwh"])

    assert set(by_month) == set(MONTHLY_2025)
    for month, measured in MONTHLY_2025.items():
        assert by_month[month] == pytest.approx(measured, abs=0.01), f"{month} did not reconcile"


def test_annual_only_reconciles_and_says_the_season_is_modelled(tmp_path) -> None:
    result = _synth(tmp_path, annual_kwh=542_000.0)
    assert result.total_kwh == pytest.approx(542_000.0, abs=0.01)
    assert "seasonal (month-to-month) variation" not in result.measured_components
    assert any("MODELLED" in w for w in result.warnings)


def test_monthly_input_marks_the_season_as_measured(tmp_path) -> None:
    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    assert "seasonal (month-to-month) variation" in result.measured_components


# --- refusals: never invent a total ----------------------------------------------------------


def test_neither_total_supplied_is_refused(tmp_path) -> None:
    with pytest.raises(SynthesisError, match="exactly one"):
        _synth(tmp_path)


def test_both_totals_supplied_is_refused(tmp_path) -> None:
    with pytest.raises(SynthesisError, match="exactly one"):
        _synth(tmp_path, monthly_kwh=MONTHLY_2025, annual_kwh=542_000.0)


def test_a_partial_year_is_refused_rather_than_scaled_up(tmp_path) -> None:
    """Scaling 4 months to 12 is exactly the Grundon defect. It must not be possible here."""
    partial = {k: v for k, v in MONTHLY_2025.items() if k <= "2025-04"}
    with pytest.raises(SynthesisError) as exc:
        _synth(tmp_path, monthly_kwh=partial)
    assert "missing 8 month" in str(exc.value)
    assert "invent" in str(exc.value)


# --- determinism -----------------------------------------------------------------------------


def test_identical_inputs_produce_an_identical_file(tmp_path) -> None:
    a = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    b = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    assert a.path == b.path
    assert a.path.read_text() == b.path.read_text()


def test_different_inputs_produce_a_different_file(tmp_path) -> None:
    a = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    changed = {**MONTHLY_2025, "2025-06": 99_000.0}
    b = _synth(tmp_path, monthly_kwh=changed)
    assert a.path != b.path


# --- shape sanity ----------------------------------------------------------------------------


def test_the_series_is_a_full_year_of_half_hours(tmp_path) -> None:
    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    assert result.intervals == 365 * 48


def test_weekday_load_exceeds_sunday_load_for_a_shift_profile(tmp_path) -> None:
    """The shape must actually carry the operating pattern, not just the totals."""
    import csv
    from datetime import datetime

    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    weekday = sunday = 0.0
    with result.path.open() as fh:
        for row in csv.DictReader(fh):
            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            if ts.weekday() == 6:
                sunday += float(row["import_kwh"])
            elif ts.weekday() < 5:
                weekday += float(row["import_kwh"])
    assert weekday / 5 > sunday, "a shift profile must load weekdays above Sundays"


def test_peak_cap_is_respected_and_energy_is_preserved(tmp_path) -> None:
    """A cap is a physical bound: energy moves, it is never deleted."""
    uncapped = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    cap = uncapped.peak_kw * 0.7
    capped = _synth(tmp_path, monthly_kwh=MONTHLY_2025, peak_kw_cap=cap)

    assert capped.peak_kw <= cap * 1.001
    assert capped.total_kwh == pytest.approx(uncapped.total_kwh, abs=0.01), (
        "capping must redistribute energy, never discard it"
    )


# --- provenance ------------------------------------------------------------------------------


def test_modelled_basis_caps_confidence_at_medium(tmp_path) -> None:
    admission = _synth(tmp_path, monthly_kwh=MONTHLY_2025).admission()
    assert admission.basis is HalfHourlyBasis.MODELLED
    assert admission.confidence_ceiling == "medium"
    assert not admission.is_metered
    assert admission.reference_profile is not None
    assert admission.scaled_to is not None


def test_an_unsourced_archetype_says_so(tmp_path) -> None:
    result = _synth(tmp_path, monthly_kwh=MONTHLY_2025)
    assert not result.profile.is_sourced
    assert any("archetype" in w.lower() for w in result.warnings), (
        "an unsourced shape must announce itself — it is an assumption, not a curve"
    )


def test_every_library_profile_is_structurally_valid() -> None:
    for profile_id, profile in PROFILE_LIBRARY.items():
        assert profile.profile_id == profile_id
        assert len(profile.weekday) == 48
        assert len(profile.month_factors) == 12
        assert profile.source in set(ProfileSource)
        assert profile.source_ref, f"{profile_id} has no source reference"
