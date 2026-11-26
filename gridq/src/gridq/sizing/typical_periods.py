"""Deterministic typical-day selection for the sizing search proxy (M4 stage A/B, D32).

Per-candidate dispatch over a full metered year is the sizing search's cost driver. The search
therefore scores candidates on a **weighted subset of real metered days** — one medoid day per
(month, weekday/weekend) stratum, weighted by how many complete days that stratum holds — and the
final winner is always re-solved on the **full year** before anything is reported (the proxy
guides the search; it never authors a reported number).

Selection is deterministic and invents nothing: strata are calendar-defined, the medoid is the
day minimising the summed L1 distance to its stratum peers (earliest date on ties), and only
complete, gap-free days qualify (a day broken by missing intervals is excluded rather than
patched). The selected sub-series keeps its real timestamps, so the dispatch solver's gap
segmentation (D31) automatically gives each non-adjacent day its own state-of-energy wrap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TypicalDays:
    """A weighted, deterministic subset of real metered days for proxy dispatch scoring."""

    positions: np.ndarray  # flat indices into the source arrays (selected days, time order)
    interval_weights: np.ndarray  # per-position weight = complete days the stratum holds
    day_count: int  # number of representative days selected
    represented_days: int  # complete days represented across all strata
    note: str  # provenance note for the run record


def select_typical_days(
    timestamps: pd.DatetimeIndex,
    load: np.ndarray,
    *,
    interval_min: int,
    min_days: int = 14,
) -> TypicalDays | None:
    """One medoid day per (month, weekday/weekend) stratum, weighted by stratum size.

    Returns ``None`` when the series is too thin to stratify (< ``min_days`` complete days or a
    single stratum) — the caller then scores candidates on the full series instead. Only complete
    days with no internal timestamp discontinuity qualify; nothing is filled or invented.
    """
    n = len(timestamps)
    intervals_per_day = int(round(24 * 60 / interval_min))
    if n < intervals_per_day:
        return None

    step = np.timedelta64(int(interval_min), "m")
    dates = np.asarray(timestamps.date)
    # Complete, gap-free days: exactly intervals_per_day rows for the date, uniform expected step.
    day_positions: dict[object, np.ndarray] = {}
    order = np.arange(n)
    for date in pd.unique(dates):
        pos = order[dates == date]
        if len(pos) != intervals_per_day:
            continue
        if not bool(np.all(np.diff(pos) == 1)):
            continue
        deltas = np.diff(timestamps.values[pos])
        if not bool(np.all(deltas == step)):
            continue
        day_positions[date] = pos

    if len(day_positions) < min_days:
        return None

    # Stratify by (month, weekday/weekend); medoid = min summed L1 distance, earliest on ties.
    strata: dict[tuple[int, bool], list[object]] = {}
    for date in day_positions:
        ts = pd.Timestamp(date)
        strata.setdefault((ts.month, ts.dayofweek >= 5), []).append(date)
    if len(strata) < 2:
        return None

    selected: list[tuple[object, np.ndarray, int]] = []  # (date, positions, weight)
    for key in sorted(strata):
        dates_in = sorted(strata[key], key=lambda d: pd.Timestamp(d))
        profiles = np.stack([load[day_positions[d]] for d in dates_in])
        # Pairwise L1 distances; medoid minimises the row sum (argmin takes the earliest tie).
        dists = np.abs(profiles[:, None, :] - profiles[None, :, :]).sum(axis=2)
        medoid = int(np.argmin(dists.sum(axis=1)))
        date = dates_in[medoid]
        selected.append((date, day_positions[date], len(dates_in)))

    selected.sort(key=lambda item: pd.Timestamp(item[0]))
    positions = np.concatenate([pos for _, pos, _ in selected])
    weights = np.concatenate([np.full(len(pos), float(w)) for _, pos, w in selected])
    represented = int(sum(w for _, _, w in selected))
    return TypicalDays(
        positions=positions,
        interval_weights=weights,
        day_count=len(selected),
        represented_days=represented,
        note=(
            f"sizing-search proxy: {len(selected)} representative metered days "
            f"(one medoid per month x weekday/weekend stratum) weighted to represent "
            f"{represented} complete days; the selected size is re-solved on the full series "
            "before reporting (D32)"
        ),
    )
