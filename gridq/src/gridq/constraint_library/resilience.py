"""Resilience floor: declared load-service obligations the battery must serve (issue 017 / D42).

Constraint ``resilience_load_service``. The shortfall the wire cannot carry is a CONSTANT forced
draw on the battery: it enters the SoE balance (drained at ``1/η_dis``, B-01), the inverter
headroom, the warranty throughput and the reservation aggregate as data. Before every window
occurrence the battery holds the WORST observed occurrence's energy, grossed up for discharge losses
and above the usable floor: ``soe_t ≥ soe_floor + level/η_dis`` (B-01 + D-169).

The forced-draw series and the pre-position levels are derived from ``SiteModel``'s provisioning
requirements by the caller (``gridq.sizing.provisioning``); this module holds the rows they
compile into. Moved verbatim from ``gridq.optimisation.milp_solver`` (WP-SIZE-02).
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np


def forced_draw_balance_rhs(forced_kw: np.ndarray, dt: float, eta_dis: float, coords: Any) -> Any:
    """The SoE-balance right-hand side of a forced draw: it drains stored energy at ``1/η_dis``."""
    import xarray as xr

    return xr.DataArray(-forced_kw * (dt / eta_dis), coords=[coords])


def add_load_service_prepositions(
    model: Any,
    soe: Any,
    prepos: list[tuple[str, np.ndarray, float]],
    pred: np.ndarray,
    soe_floor: float,
    eta_dis: float,
    n: int,
    coords: Any,
) -> None:
    """Pre-window SoE floors at the WORST-CASE exceedance level (issue 017 problem 2).

    ``prepos`` holds ``(requirement_id, occurrence_start_indices, level_kwh)``. The battery arrives
    at every window occurrence holding the worst observed occurrence's energy, not just what this
    trace's foresight needs. ``level`` is the energy the obligation must DELIVER at the AC
    terminals, so the stored energy required is ``soc_min·E + level/eta_dis`` — the reserve below
    the usable floor is not deliverable, and the discharge losses must be pre-stored. Above-usable
    floors are warned at compile; the row is added anyway (never silently relaxed).
    """
    import xarray as xr

    for _rid, _starts, _level in prepos:
        if _level <= 0.0 or _starts.size == 0:
            continue
        _pre = np.zeros(n, dtype=bool)
        _pre[pred[_starts]] = True
        model.add_constraints(
            soe >= soe_floor + _level / eta_dis,
            name=f"load_service_prepos_{re.sub(r'[^0-9A-Za-z_]', '_', _rid)}",
            mask=xr.DataArray(_pre, coords=[coords]),
        )
