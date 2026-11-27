"""Thermal limits: the cooling turn-down a site's IC-11 surface allows (WP-THERM-02, NEW-IC-11).

Constraint ``thermal_turn_down_limit``: ``turn_down_t ≤ deliverable_kw(surface, t, duration)``, the
kW of cooling reduction the site's `ThermalFlexAvailability` surface says can be held for
``duration`` minutes from a start at ``t``, read on the GB clock by the contract's own
`thermal_flex.query_at`. The surface is the sole source of the number: a ``flex`` cell gives its
curve's kW, a ``zero_flex`` cell gives 0, and an ``unavailable`` or ``unknown`` cell also gives 0,
because an unassessed cell has no number and a limit is never invented for it (the status travels
beside the limit so the caller can say why).

**Catalogued, not yet applied (stated, WP-SIZE-02).** The battery MILP has no cooling variable, so
no solver compiles this row today. It lives here so the sizer's co-optimisation (A5) and the live
MPC (B2) take it from one place when a cooling resource enters the dispatch model; the catalogue
records ``applied_by=()`` for it until then.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np

from gridq.contracts.thermal_flex import AvailabilityStatus, ThermalFlexAvailability, query_at


def turn_down_limits_kw(
    surface: ThermalFlexAvailability, starts: Sequence[datetime], duration_min: int
) -> tuple[np.ndarray, tuple[AvailabilityStatus, ...]]:
    """Per-start holdable cooling turn-down (kW) and the surface status behind each value.

    ``starts`` must be timezone-aware (the contract refuses a naive instant). A non-integer kW is
    impossible: IC-11 kW are integers.
    """
    limits = np.zeros(len(starts), dtype=float)
    statuses: list[AvailabilityStatus] = []
    for i, start in enumerate(starts):
        answer = query_at(surface, start, duration_min)
        statuses.append(answer.status)
        if isinstance(answer.deliverable_kw, int):
            limits[i] = float(answer.deliverable_kw)
    return limits, tuple(statuses)


def add_turn_down_rows(model: Any, turn_down: Any, limits_kw: np.ndarray, coords: Any) -> None:
    """``turn_down_t ≤ limit_t`` (``thermal_turn_down_limit``) on a cooling-reduction variable."""
    import xarray as xr

    model.add_constraints(
        turn_down <= xr.DataArray(limits_kw, coords=[coords]), name="thermal_turn_down_limit"
    )
