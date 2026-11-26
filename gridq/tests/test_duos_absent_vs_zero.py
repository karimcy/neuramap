"""D45: an absent DUoS band rate is a MISSING INPUT, never a zero.

Why this file exists. `duos_rate_series` previously built its rate map with
``tariff.duos_red_rate or 0.0``, which conflated two different facts:

* the site genuinely faces no charge in that band (a real 0.0), and
* nobody has told us the rate yet (a None).

Both became 0.0, and that zero then deleted the band's component from the MILP objective's
``price_t`` and from the customer bill. `DUOS_RED_BAND_AVOIDANCE` is one of only TWO priced-floor
streams, so an empty field silently zeroed a floor stream and the zero entered P50, P90 and the
DSCR looking exactly like a measured result. Two of the five real Grundon sites (Colnbrook and
Tanhouse) shipped with all three band rates null, so this was live, not theoretical.

``or 0.0`` was wrong in the other direction too: it swallowed a genuine 0.0, so a site that really
faced no red-band charge was indistinguishable from one whose rate was unknown.
"""

from __future__ import annotations

import pandas as pd

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.optimisation.tariff_calendar import (
    duos_rate_series,
    duos_rate_series_with_absences,
)

_TS = pd.date_range("2025-01-06 00:00", periods=48, freq="30min", tz="Europe/London")


def test_absent_band_rates_are_reported_not_silently_zeroed() -> None:
    """The Colnbrook/Tanhouse case: all three rates null, so all three come back as absent."""
    series, absent = duos_rate_series_with_absences(
        s.TariffStack(basis=SYNTHETIC_BASIS, provenance=SYNTHETIC_PROVENANCE), _TS
    )
    assert set(absent) == {"red", "amber", "green"}
    # The array stays dense and the right length, so no caller breaks...
    assert series.shape == (len(_TS),)
    # ...but the caller can no longer mistake the zeros for a measured rate.
    assert series.max() == 0.0


def test_a_genuine_zero_rate_is_not_reported_as_absent() -> None:
    """The half of the defect that `or 0.0` hid: a real 0.0 is a fact, not a missing input."""
    tariff = s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        duos_red_rate=0.0,
        duos_amber_rate=0.0,
        duos_green_rate=0.0,
    )
    series, absent = duos_rate_series_with_absences(tariff, _TS)
    assert absent == ()
    assert series.max() == 0.0


def test_partial_absence_names_only_the_missing_bands() -> None:
    """A site with a sourced red rate and no amber/green reports exactly those two as absent."""
    tariff = s.TariffStack(
        basis=SYNTHETIC_BASIS, provenance=SYNTHETIC_PROVENANCE, duos_red_rate=0.07777
    )  # NGED East Midlands LV red, 2026/27
    series, absent = duos_rate_series_with_absences(tariff, _TS)
    assert set(absent) == {"amber", "green"}
    # The rate that IS known is still placed in time, so the objective keeps the signal it has.
    assert series.max() == 0.07777


def test_declared_rates_are_placed_in_time_unchanged() -> None:
    """The function places rates in time; it must never scale or reinterpret them."""
    tariff = s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        duos_red_rate=0.07777,
        duos_amber_rate=0.01,
        duos_green_rate=0.001,
    )
    series, absent = duos_rate_series_with_absences(tariff, _TS)
    assert absent == ()
    assert set(round(v, 5) for v in set(series)) <= {0.07777, 0.01, 0.001}


def test_the_wrapper_returns_the_same_series_as_the_two_value_form() -> None:
    """`duos_rate_series` must stay a pure projection of the new function, or callers diverge."""
    tariff = s.TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        duos_red_rate=0.07777,
        duos_amber_rate=0.01,
    )
    series, _absent = duos_rate_series_with_absences(tariff, _TS)
    assert (duos_rate_series(tariff, _TS) == series).all()
