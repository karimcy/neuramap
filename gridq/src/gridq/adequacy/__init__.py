"""Sub-half-hourly adequacy simulator with a resilience floor (WP-SITE-04, spec A3-01, A3-02).

- `size_floor` sizes the resilience reserve as a hard floor from the customer's autonomy
  requirement (``resilience_floor_policy``); `admit_floor_signoff` binds an IC-4
  ``critical_facilities_signoff`` to the exact floor statement it signs.
- `run_adequacy` simulates IC-2 restriction events chronologically against the battery's state of
  energy, IC-11 thermal flex and the load ensemble (``adequacy_simulator``), and reports LOLP, EUE,
  the minimum state-of-energy margin over the floor and recharge feasibility.

Decisions D170 to D175 (docs/decisions.md).
"""

from gridq.adequacy.floor import (
    Approved,
    FloorPartition,
    ResilienceFloorPolicy,
    Unsigned,
    UnsignedReason,
    admit_floor_signoff,
    approval_label,
    floor_statement_hash,
    render_floor_statement,
    resilience_terms,
    size_floor,
)
from gridq.adequacy.inputs import AutonomyRequirement, BessParameters
from gridq.adequacy.load import LoadAnchor, MinuteLoadArray, SynthesisLoad
from gridq.adequacy.result import AdequacyConfig, AdequacyResult, StatedImportCapacity
from gridq.adequacy.simulator import render_summary, run_adequacy

__all__ = [
    "AdequacyConfig",
    "AdequacyResult",
    "Approved",
    "AutonomyRequirement",
    "BessParameters",
    "FloorPartition",
    "LoadAnchor",
    "MinuteLoadArray",
    "ResilienceFloorPolicy",
    "StatedImportCapacity",
    "SynthesisLoad",
    "Unsigned",
    "UnsignedReason",
    "admit_floor_signoff",
    "approval_label",
    "floor_statement_hash",
    "render_floor_statement",
    "render_summary",
    "resilience_terms",
    "run_adequacy",
    "size_floor",
]
