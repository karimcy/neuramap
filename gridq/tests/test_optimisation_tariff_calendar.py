"""Tests for the GB tariff band → half-hour time-window calendar (content, not prices)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.optimisation.tariff_calendar import (
    AMBER,
    GREEN,
    RED,
    duos_band_labels,
    duos_rate_series,
    energy_price_series,
    red_band_mask,
)
from gridq.schemas.tariff import TariffStack


def _week() -> pd.DatetimeIndex:
    # One week of half-hours from a Monday.
    return pd.date_range("2025-01-06 00:00", periods=7 * 48, freq="30min")


def test_red_band_is_weekday_1600_to_1900() -> None:
    ts = _week()
    mask = red_band_mask(ts)
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    expected = weekday & (hour >= 16) & (hour < 19)
    assert np.array_equal(mask, expected)
    # 5 weekdays × 6 half-hours (16:00–19:00) = 30 red half-hours in the week.
    assert int(mask.sum()) == 30


def test_duos_band_labels_partition_red_amber_green() -> None:
    ts = _week()
    bands = duos_band_labels(ts)
    assert set(bands.tolist()) <= {RED, AMBER, GREEN}
    # A Sunday half-hour is always green; a weekday 17:00 is red; a weekday 10:00 is amber.
    by_ts = dict(zip(ts, bands, strict=True))
    assert by_ts[pd.Timestamp("2025-01-12 17:00")] == GREEN  # Sunday
    assert by_ts[pd.Timestamp("2025-01-06 17:00")] == RED  # Monday peak
    assert by_ts[pd.Timestamp("2025-01-06 10:00")] == AMBER  # Monday daytime


def test_duos_rate_series_places_site_rates_in_time_and_zeroes_missing_bands() -> None:
    ts = _week()
    tariff = TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        import_rates={"single": 0.2},
        duos_red_rate=0.11,
    )  # only red set
    rates = duos_rate_series(tariff, ts)
    red = red_band_mask(ts)
    assert np.allclose(rates[red], 0.11)
    assert np.allclose(rates[~red], 0.0)  # amber/green absent -> 0.0, never invented


def test_energy_price_flat_tariff_returns_a_constant_price_not_none() -> None:
    """F-01. A flat tariff has no SPREAD, but it does have a PRICE — and charging costs money.

    This test previously asserted ``prices is None``, which was the defect: the caller
    (`milp_solver`) then substituted zeros into the objective, so round-trip losses were free and
    the MILP cycled daily to harvest DUoS avoidance it had never paid to obtain. On the worked
    example that fabricated ~95% of the reported stream and flipped the sign of the business case.

    The correct behaviour is a constant array. The energy term then evaluates to
    ``flat x (Σdischarge − Σcharge) x Δt``, which is negative by exactly the cost of the losses,
    so a cycle is only taken when another band genuinely covers it.
    """
    ts = _week()
    prices, reason = energy_price_series({"single": 0.24}, ts)

    assert prices is not None, "a flat tariff must yield a price, not None"
    assert np.allclose(prices, 0.24)
    assert len(prices) == len(ts)
    assert reason is not None and "flat" in reason
    assert "still costed" in reason, "the note must explain that charge energy is paid for"


def test_flat_tariff_makes_a_pointless_cycle_lose_money() -> None:
    """The economic consequence of the fix, stated as arithmetic rather than as a shape."""
    ts = _week()
    prices, _ = energy_price_series({"single": 0.24}, ts)
    # Charge 100 kWh, discharge 85 kWh (85% round trip). Under the old zero-substitution this
    # netted to £0 and the cycle looked free; it must now cost the losses.
    net_energy_kwh = 85.0 - 100.0
    assert prices is not None
    assert float(prices[0]) * net_energy_kwh == pytest.approx(-3.6, abs=0.01)


def test_energy_price_day_night_scheme_maps_to_windows() -> None:
    ts = _week()
    prices, reason = energy_price_series({"day": 0.30, "night": 0.10}, ts)
    assert reason is None and prices is not None
    hour = ts.hour.to_numpy()
    night = (hour >= 23) | (hour < 7)
    assert np.allclose(prices[night], 0.10)
    assert np.allclose(prices[~night], 0.30)


def test_energy_price_unrecognised_labels_degrade_without_inventing() -> None:
    """Labels with no windows and no sourced calendar: every half-hour UNRESOLVED (D105).

    Previously ``None`` (the dispatch refused). Now the bill side is the lowest quoted rate on
    every half-hour, labelled a lower bound, and no time placement or mean of bands is invented.
    """
    prices, reason = energy_price_series({"weird_a": 0.1, "weird_b": 0.3}, _week())
    assert prices is not None
    assert np.allclose(prices, 0.1), "the bill side is the lowest quoted rate, never a mean"
    assert reason is not None and "UNRESOLVED" in reason and "LOWER BOUND" in reason
    assert "no mean" in reason


def test_calendar_is_deterministic() -> None:
    ts = _week()
    a, _ = energy_price_series({"peak": 0.4, "offpeak": 0.1}, ts)
    b, _ = energy_price_series({"peak": 0.4, "offpeak": 0.1}, ts)
    assert a is not None and b is not None and np.array_equal(a, b)
