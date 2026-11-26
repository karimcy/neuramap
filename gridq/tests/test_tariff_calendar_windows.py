"""Tests for the versioned DUoS charging calendar (W9 — the calendar is content, not code).

Invariants: an ABSENT `duos.band_windows_weekday` key yields the documented code fallback with
byte-identical behaviour and no noise (every current release predates the key — goldens safe);
a present override reshapes the bands and warns loudly with its assumption status; a malformed
value falls back with a warning, never a guess; the solver threads the override end-to-end.
"""

from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.tariff_calendar import (
    DEFAULT_DUOS_WINDOWS,
    DuosWindows,
    duos_band_labels,
    duos_windows_from_assumptions,
    red_band_mask,
)

# Monday 06:00 → covers night/shoulder/peak transitions on a weekday.
_TS = pd.date_range("2025-01-06 06:00", periods=32, freq="30min")


def _assumption(value: object) -> object:
    from gridq.assumptions import Assumption

    return Assumption(
        value=value,
        unit="hour windows",
        source="test",
        status="placeholder",
        owner_or_reviewer="test",
        confidence="low",
        last_reviewed=date(2026, 7, 2),
        notes="test-only",
    )


def _lib_with_windows(value: object):
    lib = copy.deepcopy(load_assumptions("gb_v0_1"))
    lib.duos["band_windows_weekday"] = _assumption(value)
    return lib


def test_absent_key_is_the_silent_documented_fallback() -> None:
    windows, note = duos_windows_from_assumptions(load_assumptions("gb_v0_1"))
    assert windows == DEFAULT_DUOS_WINDOWS
    assert note is None  # no key, no noise — byte-identical behaviour (goldens safe)
    # The fallback places Monday 16:00–19:00 red, 07:00–16:00 amber — the documented pattern.
    labels = duos_band_labels(_TS)
    assert labels[list(_TS).index(pd.Timestamp("2025-01-06 16:00"))] == "red"
    assert labels[list(_TS).index(pd.Timestamp("2025-01-06 12:00"))] == "amber"
    assert labels[list(_TS).index(pd.Timestamp("2025-01-06 06:00"))] == "green"


def test_override_reshapes_the_bands_and_names_its_status() -> None:
    lib = _lib_with_windows({"red": [[17, 20]], "amber": [[7, 17]]})
    windows, note = duos_windows_from_assumptions(lib)
    assert windows == DuosWindows(red=((17, 20),), amber=((7, 17),))
    assert note is not None and "status 'placeholder'" in note
    mask_default = red_band_mask(_TS)
    mask_override = red_band_mask(_TS, windows)
    assert not np.array_equal(mask_default, mask_override)
    idx_1630 = list(_TS).index(pd.Timestamp("2025-01-06 16:30"))
    assert bool(mask_default[idx_1630]) and not bool(mask_override[idx_1630])


def test_malformed_value_falls_back_with_a_warning_never_a_guess() -> None:
    for bad in ({"red": [[19, 16]]}, {"red": "16-19"}, {"amber": [[7, 16]]}, 42):
        windows, note = duos_windows_from_assumptions(_lib_with_windows(bad))
        assert windows == DEFAULT_DUOS_WINDOWS
        assert note is not None and "malformed" in note


def test_solver_threads_the_override_end_to_end(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    path = tmp_path_factory.mktemp("hh") / "load.csv"
    rows, _ = write_hh_csv(path)
    site = multi_rate_site(path, rows=rows)

    def _solve(lib):
        solver = MilpDispatchSolver()
        result = solver.solve(
            DispatchProblem(
                site_model=site,
                assumptions=lib,
                power_kw=200.0,
                energy_kwh=400.0,
                duration_hours=2.0,
            )
        )
        return result, solver.last_schedule()

    base_result, base_schedule = _solve(load_assumptions("gb_v0_1"))
    assert not any("band_windows_weekday" in w for w in base_result.warnings)

    override_result, override_schedule = _solve(
        _lib_with_windows({"red": [[17, 20]], "amber": [[7, 17]]})
    )
    assert any("overridden by duos.band_windows_weekday" in w for w in override_result.warnings)
    assert not np.array_equal(base_schedule.red_band_mask, override_schedule.red_band_mask)
