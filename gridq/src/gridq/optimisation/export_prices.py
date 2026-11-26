"""B2 — half-hourly wholesale price-series alignment for the export overlay (issue 018 / D43).

A single **pure, deterministic** helper. Given a loaded :class:`MarketForecast` (points carrying
``timestamp`` + ``price_gbp_per_mwh``) and the metered load's half-hourly timestamps, it returns a
per-instant wholesale **export price in £/kWh** (£/MWh ÷ 1000) aligned to those timestamps — or
``(None, reason)`` when the series does not cover the metered horizon.

Hard rules (mirroring the ingestion ``allow_interpolation`` discipline, design doc R7):

- **Full coverage or skip.** If ANY metered timestamp is uncovered by the forecast, the whole
  alignment is refused (``None`` + reason). The series is NEVER interpolated or extrapolated.
- **No invented prices.** Only forecast points that carry a real price are used; a forecast with
  no priced points aligns to ``None``.
- **Off the pricing floor.** This feeds ONLY the gated-upside export valuation (A3) — never the
  priced-floor objective, P50/P90 or DSCR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecast

_MWH_TO_KWH = 1000.0


def align_export_prices(
    forecast: MarketForecast, timestamps: pd.DatetimeIndex
) -> tuple[np.ndarray | None, str | None]:
    """Align ``forecast`` prices to ``timestamps`` → ``(£/kWh array, None)`` or ``(None, reason)``.

    Exact timestamp match only (no nearest/interpolation). Duplicate forecast timestamps collapse
    deterministically (last wins). Returns a float64 array the same length as ``timestamps`` with
    every value the covering forecast point's ``price_gbp_per_mwh`` ÷ 1000 — or ``None`` with a
    human-readable reason when coverage is incomplete or the timezone awareness of the two indices
    differs (refused rather than guessed).
    """
    n = len(timestamps)
    if n == 0:
        return None, "no metered timestamps to align a wholesale price series against."

    priced_ts = [p.timestamp for p in forecast.points if p.price_gbp_per_mwh is not None]
    priced_val = [
        float(p.price_gbp_per_mwh) for p in forecast.points if p.price_gbp_per_mwh is not None
    ]
    if not priced_ts:
        return None, "the supplied wholesale forecast has no priced points to align."

    fc_index = pd.DatetimeIndex(priced_ts)
    # Refuse a tz-awareness mismatch outright: silently coercing one side would be exactly the
    # timestamp-guessing this discipline forbids (a wall-clock vs instant mix could misalign a
    # whole horizon across DST). The caller supplies both from the same source in practice.
    if (timestamps.tz is None) != (fc_index.tz is None):
        return None, (
            "the wholesale price series and the metered load differ in timezone awareness "
            "(one is tz-aware, one is naive); alignment is refused rather than guessed "
            "(full-coverage-or-skip)."
        )

    series = pd.Series(np.asarray(priced_val, dtype=float), index=fc_index)
    # A forecast may legitimately repeat a settlement stamp; keep the last deterministically.
    series = series[~series.index.duplicated(keep="last")]

    try:
        aligned = series.reindex(timestamps)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive tz/dtype guard
        return None, f"wholesale price series could not be aligned to the metered horizon: {exc}"

    values = aligned.to_numpy(dtype=float)
    missing = int(np.isnan(values).sum())
    if missing:
        return None, (
            f"{missing} of {n} metered half-hours are not covered by the wholesale price series — "
            "full coverage is required (the series is never interpolated or extrapolated)."
        )
    return values / _MWH_TO_KWH, None
