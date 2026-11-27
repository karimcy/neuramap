"""Warranty: the annual throughput cap (HARD), from the release's per-duration cycle table.

Constraint ``warranty_throughput_cap``:
``Σ (discharge_t + export_t)·w_t·Δt ≤ cycles(duration)·E / annual_factor − ancillary − forced``,
the horizon share of the annual warranty cap, less the throughput a priced response product and a
load-service obligation consume before any priced dispatch may (rule 2 of the exclusivity matrix;
issue 017 / D42). The cycle count is the CEILING node of a table that decreases with duration (D35).

Moved verbatim from ``gridq.optimisation`` (WP-SIZE-02): the lookup from ``_assumptions`` (which
re-exports it), the row from ``milp_solver``, the cap formula shared with ``mpc_solver``.
"""

from __future__ import annotations

from typing import Any

WARRANTY_KEY = "warranty_cycle_limits.max_cycles_per_year_by_duration_h"


def ceiling_value_for_duration(table: object, duration_h: float) -> tuple[float | None, str | None]:
    """Per-duration table lookup taking the CEILING node — the conservative direction for a
    table that DECREASES with duration (D35, resolves F-002/C030).

    Warranty cycle caps fall as duration rises ('1' > '2' > '4' cycles/yr), so the floor lookup
    RELAXES the cap between nodes (a 3 h system inheriting the 2 h cap may cycle more than
    either bracketing warranty point allows). This lookup picks the smallest tabulated duration
    >= ``duration_h`` instead: the value never exceeds what any bracketing contract point
    allows, and no intermediate value is invented (north-star D28's no-interpolation stance is
    kept). On a tabulated node the node's own value is returned exactly (existing results
    unchanged). Above the largest tabulated point, uses that point's value and warns (the true
    cap for a longer duration is likely lower — needs_review until sourced warranty terms
    land). Returns ``(value, warning|None)``.
    """
    if not isinstance(table, dict) or not table:
        return None, "per-duration table is missing or empty"
    try:
        pairs = sorted((float(k), float(v)) for k, v in table.items())
    except (TypeError, ValueError):
        return None, "per-duration table is malformed"
    for d, val in pairs:
        if d >= duration_h:
            note = None
            if d != duration_h:
                note = (
                    f"duration {duration_h}h is between tabulated points; used the {d}h "
                    "node's value (ceiling lookup, the conservative direction for a "
                    "decreasing table — D35)"
                )
            return val, note
    last_d, last_val = pairs[-1]
    return last_val, (
        f"duration {duration_h}h is above the largest tabulated point ({last_d}h); used that "
        "point's value — likely optimistic for a decreasing table, needs_review until the "
        "sourced warranty terms land"
    )


def throughput_cap_kwh(cap_cycles: float, energy_kwh: float, annual_factor: float) -> float:
    """The horizon share of the annual warranty cap, in kWh discharged (nameplate basis, C-31)."""
    return cap_cycles * energy_kwh / annual_factor


def obligation_exceeds_cap_note(obligation_kwh: float, cap_kwh: float) -> str:
    """The warning when load-service obligations alone need more throughput than the cap allows."""
    return (
        f"load-service obligations alone require {round(obligation_kwh, 1)} "
        f"kWh of throughput vs the warranty cap {round(cap_kwh, 1)} "
        "kWh — the obligation exceeds the warranty by construction (never "
        "silently relaxed)."
    )


def add_throughput_cap_row(model: Any, export_flow: Any, w_da: Any, dt: float, rhs: float) -> None:
    """The dense annual row ``Σ export_flow·w·Δt ≤ rhs`` (``throughput_cap``)."""
    model.add_constraints((export_flow * w_da * dt).sum() <= rhs, name="throughput_cap")
