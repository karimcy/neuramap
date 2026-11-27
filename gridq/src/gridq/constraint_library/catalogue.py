"""The catalogue: every constraint the library defines, what parameterises it and who applies it.

One entry per constraint id. ``rows`` names the linopy bound or row the constraint becomes (a
``<…>`` part is filled per instance); ``parameters`` names the `SiteModel` field, release key or
contract member that sets its numbers at solve time (the library holds no number of its own);
``applied_by`` names the solver paths that compile it today:

- ``annual_milp``: `MilpDispatchSolver` (the sizer's per-candidate dispatch, its typical-period
  proxy and the export overlay all build through it);
- ``mpc_window``: each receding-horizon window of `MpcDispatchSolver`, which solves through the same
  model build with the same problem (its descriptors included) but no export overlay.

An empty ``applied_by`` is a catalogued constraint no solver compiles yet, stated rather than
implied (D179).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Family(StrEnum):
    """The roadmap's constraint families (WP-SIZE-02), plus the inverter's power rating."""

    CONNECTION = "connection"
    POWER_RATING = "power_rating"
    RESILIENCE_FLOOR = "resilience_floor"
    THERMAL_LIMIT = "thermal_limit"
    SOC_WINDOW = "soc_window"
    WARRANTY = "warranty"
    COMMITMENT = "commitment"


ANNUAL_MILP: Final = "annual_milp"
MPC_WINDOW: Final = "mpc_window"
_BOTH: Final = (ANNUAL_MILP, MPC_WINDOW)


@dataclass(frozen=True)
class ConstraintSpec:
    constraint_id: str
    family: Family
    module: str
    summary: str
    rows: tuple[str, ...]
    parameters: tuple[str, ...]
    applied_by: tuple[str, ...]


CATALOGUE: Final[tuple[ConstraintSpec, ...]] = (
    ConstraintSpec(
        "commitment_availability_window",
        Family.COMMITMENT,
        "commitments",
        "net injection + summed committed kW <= the deliverable envelope inside the windows",
        ("availability_reserve_aggregate",),
        ("dispatch descriptor parameters: AvailabilityCommitment (availability_window)",),
        _BOTH,
    ),
    ConstraintSpec(
        "commitment_held_state_of_energy",
        Family.COMMITMENT,
        "commitments",
        "soe >= soe_floor + held_energy_kwh / eta_dis inside the windows",
        ("held_state_of_energy_<commitment_id>",),
        ("dispatch descriptor parameters: AvailabilityCommitment (held_state_of_energy)",),
        _BOTH,
    ),
    ConstraintSpec(
        "connection_kva_polygon",
        Family.CONNECTION,
        "connection",
        "active import and residual reactive inside the inscribed octagon of min(MIC, ASC) kVA",
        ("reactive_connection_kva_p", "reactive_connection_kva_q"),
        ("connection.mic_kw", "connection.asc_kw", "half-hourly reactive_import/export channels"),
        _BOTH,
    ),
    ConstraintSpec(
        "discharge_rating",
        Family.POWER_RATING,
        "connection",
        "discharge <= min(power - forced load-service draw, site load) behind the meter",
        ("discharge (upper bound)",),
        ("candidate power_kw", "half-hourly load"),
        _BOTH,
    ),
    ConstraintSpec(
        "export_limit",
        Family.CONNECTION,
        "connection",
        "export <= min(power, MEC); discharge + export <= power - forced draw",
        ("export (upper bound)", "export_inverter"),
        ("connection.mec_kw", "or an explicit labelled study export limit"),
        (ANNUAL_MILP,),
    ),
    ConstraintSpec(
        "import_limit",
        Family.CONNECTION,
        "connection",
        "charge <= min(power, max(0, min(MIC, ASC) x pf - net load - aux - load-service addition))",
        ("charge (upper bound)",),
        (
            "connection.mic_kw",
            "connection.asc_kw",
            "mic_asc_headroom.assumed_power_factor",
            "battery_opex.parasitic_load_fraction_of_power",
        ),
        _BOTH,
    ),
    ConstraintSpec(
        "inverter_pq_octagon",
        Family.POWER_RATING,
        "connection",
        "inverter real and reactive power inside the inscribed octagon of its rated kVA",
        ("reactive_inverter_octagon_p", "reactive_inverter_octagon_q"),
        ("candidate power_kw", "half-hourly reactive_import/export channels"),
        _BOTH,
    ),
    ConstraintSpec(
        "resilience_load_service",
        Family.RESILIENCE_FLOOR,
        "resilience",
        "forced load-service draw in the SoE balance; soe >= floor + worst level / eta_dis "
        "before each window occurrence",
        ("soe_balance (forced-draw right-hand side)", "load_service_prepos_<requirement_id>"),
        ("SiteModel.provisioning_requirements",),
        _BOTH,
    ),
    ConstraintSpec(
        "soc_window",
        Family.SOC_WINDOW,
        "soc",
        "soc_min x E <= soe <= soc_max x E; a rolling-horizon start state inside the window",
        ("soe (bounds)",),
        ("warranty_cycle_limits.soc_min_fraction", "warranty_cycle_limits.soc_max_fraction"),
        _BOTH,
    ),
    ConstraintSpec(
        "thermal_turn_down_limit",
        Family.THERMAL_LIMIT,
        "thermal",
        "cooling turn-down <= the IC-11 surface's holdable kW for the event duration",
        ("thermal_turn_down_limit",),
        ("IC-11 ThermalFlexAvailability surface",),
        (),
    ),
    ConstraintSpec(
        "warranty_throughput_cap",
        Family.WARRANTY,
        "warranty",
        "sum (discharge + export) x w x dt <= cycles(duration) x E / annual_factor "
        "- ancillary response - load-service throughput",
        ("throughput_cap",),
        ("warranty_cycle_limits.max_cycles_per_year_by_duration_h",),
        _BOTH,
    ),
)


def constraint_ids() -> tuple[str, ...]:
    """Every constraint id the library defines, sorted (the IC-3 pin's order)."""
    return tuple(sorted(spec.constraint_id for spec in CATALOGUE))


def spec(constraint_id: str) -> ConstraintSpec:
    """The catalogue entry for ``constraint_id`` (``KeyError`` when the library has none)."""
    for entry in CATALOGUE:
        if entry.constraint_id == constraint_id:
            return entry
    raise KeyError(constraint_id)
