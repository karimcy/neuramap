"""B2 — the pure wholesale price-series alignment helper (issue 018 / D43).

Exact alignment, £/MWh→£/kWh, full-coverage-or-skip (a gap returns None + reason, NEVER
interpolated), and the tz-awareness / empty / no-price guards. No solver, no IO.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
)
from gridq.optimisation.export_prices import align_export_prices

_START = "2025-01-06 00:00"


def _forecast(timestamps: pd.DatetimeIndex, prices) -> MarketForecast:
    points = [
        MarketForecastPoint(timestamp=t.to_pydatetime(), price_gbp_per_mwh=p)
        for t, p in zip(timestamps, prices, strict=True)
    ]
    fc = MarketForecast(points=points, source="test", forecast_version="v1")
    fc.content_hash = hash_market_forecast(fc)
    return fc


def test_exact_alignment_and_mwh_to_kwh() -> None:
    ts = pd.date_range(_START, periods=48, freq="30min")
    # £/MWh values; the helper must divide by 1000 to £/kWh, aligned in the load's exact order.
    mwh_prices = np.linspace(50.0, 500.0, 48)
    fc = _forecast(ts, mwh_prices)
    aligned, reason = align_export_prices(fc, ts)
    assert reason is None
    assert aligned is not None
    assert np.allclose(aligned, mwh_prices / 1000.0)


def test_forecast_row_order_is_reindexed_to_the_load_order() -> None:
    ts = pd.date_range(_START, periods=6, freq="30min")
    # forecast supplied in a shuffled order; the value must still land on the matching load stamp.
    order = [3, 0, 5, 1, 4, 2]
    shuffled_ts = ts[order]
    prices = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]  # £/MWh keyed to shuffled_ts positions
    fc = _forecast(shuffled_ts, prices)
    aligned, reason = align_export_prices(fc, ts)
    assert reason is None and aligned is not None
    # load stamp i gets the price whose forecast stamp equals ts[i]
    expected = np.array([prices[order.index(i)] for i in range(6)]) / 1000.0
    assert np.allclose(aligned, expected)


def test_any_uncovered_metered_stamp_returns_none_never_interpolates() -> None:
    ts = pd.date_range(_START, periods=48, freq="30min")
    # drop ONE interior forecast stamp -> not full coverage -> refuse (no interpolation)
    fc = _forecast(ts, np.full(48, 100.0))
    fc_gap = MarketForecast(
        points=[p for i, p in enumerate(fc.points) if i != 20],
        source="test",
        forecast_version="v1",
    )
    aligned, reason = align_export_prices(fc_gap, ts)
    assert aligned is None
    assert reason is not None and "not covered" in reason
    assert "never interpolated" in reason


def test_null_priced_points_do_not_count_as_coverage() -> None:
    ts = pd.date_range(_START, periods=10, freq="30min")
    prices = [100.0] * 10
    fc = _forecast(ts, prices)
    # blank out one point's price -> that metered stamp is uncovered -> None
    fc.points[4].price_gbp_per_mwh = None
    aligned, reason = align_export_prices(fc, ts)
    assert aligned is None and reason is not None


def test_no_priced_points_returns_none() -> None:
    ts = pd.date_range(_START, periods=4, freq="30min")
    fc = MarketForecast(
        points=[MarketForecastPoint(timestamp=t.to_pydatetime()) for t in ts],
        source="test",
        forecast_version="v1",
    )
    aligned, reason = align_export_prices(fc, ts)
    assert aligned is None
    assert reason is not None and "no priced points" in reason


def test_empty_timestamps_returns_none() -> None:
    fc = _forecast(pd.date_range(_START, periods=2, freq="30min"), [100.0, 200.0])
    aligned, reason = align_export_prices(fc, pd.DatetimeIndex([]))
    assert aligned is None and reason is not None


def test_timezone_awareness_mismatch_is_refused_not_guessed() -> None:
    ts_naive = pd.date_range(_START, periods=4, freq="30min")
    ts_aware = ts_naive.tz_localize("UTC")
    fc = _forecast(ts_naive, [100.0, 200.0, 300.0, 400.0])
    aligned, reason = align_export_prices(fc, ts_aware)
    assert aligned is None
    assert reason is not None and "timezone awareness" in reason


def test_duplicate_forecast_timestamps_collapse_last_wins_deterministically() -> None:
    ts = pd.date_range(_START, periods=3, freq="30min")
    dup = pd.DatetimeIndex([ts[0], ts[0], ts[1], ts[2]])
    fc = _forecast(dup, [111.0, 222.0, 300.0, 400.0])  # ts[0] repeated: last (222) wins
    aligned, reason = align_export_prices(fc, ts)
    assert reason is None and aligned is not None
    assert aligned[0] == pytest.approx(0.222)
    assert aligned[1] == pytest.approx(0.300)


def test_aligned_output_is_a_plain_float_array_length_of_timestamps() -> None:
    ts = pd.date_range(_START, periods=12, freq="30min")
    fc = _forecast(ts, np.full(12, 250.0))
    aligned, _ = align_export_prices(fc, ts)
    assert isinstance(aligned, np.ndarray)
    assert aligned.dtype == np.float64
    assert aligned.shape == (12,)
