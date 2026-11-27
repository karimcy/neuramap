"""Sub-half-hourly adequacy simulator with a resilience floor (WP-SITE-04, spec A3-01, A3-02).

- `size_floor` sizes the resilience reserve as a hard floor from the customer's autonomy
  requirement (``resilience_floor_policy``); `admit_floor_signoff` binds an IC-4
  ``critical_facilities_signoff`` to the exact floor statement it signs.
- `run_adequacy` simulates IC-2 restriction events chronologically against the battery's state of
  energy, IC-11 thermal flex and the load ensemble (``adequacy_simulator``), and reports LOLP, EUE,
  the minimum state-of-energy margin over the floor and recharge feasibility.
- WP-SITE-05: `OnsiteGenerationModel` (``onsite_generation_model``) declares gensets and co-located
  generation; `run_adequacy(..., generation=OnsiteGeneration(...))` plans them through a run ledger
  that never exceeds a run-hour limit and returns ``gridq.adequacy_result`` 1.1.0
  (`AdequacyResultV1_1`); `required_bess_energy` gives the BESS energy needed with and without the
  gensets; `size_floor(..., standby=model)` sizes a standby-derived handover floor.

Decisions D170 to D175, and the WP-SITE-05 decisions (docs/decisions.md).
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
from gridq.adequacy.onsite import MinuteOutputArray, OnsiteGeneration, OnsiteGenerationModel
from gridq.adequacy.required import RequiredBessEnergy, required_bess_energy
from gridq.adequacy.result import AdequacyConfig, AdequacyResult, StatedImportCapacity
from gridq.adequacy.result_v1_1 import AdequacyResultV1_1
from gridq.adequacy.simulator import render_summary, run_adequacy

__all__ = [
    "AdequacyConfig",
    "AdequacyResult",
    "AdequacyResultV1_1",
    "Approved",
    "AutonomyRequirement",
    "BessParameters",
    "FloorPartition",
    "LoadAnchor",
    "MinuteLoadArray",
    "MinuteOutputArray",
    "OnsiteGeneration",
    "OnsiteGenerationModel",
    "RequiredBessEnergy",
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
    "required_bess_energy",
    "resilience_terms",
    "run_adequacy",
    "size_floor",
]
