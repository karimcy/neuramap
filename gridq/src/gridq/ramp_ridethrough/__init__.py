"""Ramp, RVC and ride-through screens with a PSAL simulation handoff (WP-SITE-06; A3-03, A3-04).

Releases ``ramp_ridethrough_screen``: `screen_ramp_ridethrough` returns one
`RampRidethroughScreen` record holding

- the **ramp screen** (`gridq.ramp_ridethrough.ramp`): the site import ramp checked against the
  ramp terms of the IC-1 `ConnectionAgreement`, the only source of a limit (G99 10.3.4);
- the **RVC screen** (`gridq.ramp_ridethrough.rvc`): BESS step events against the EREC P28
  steady-state limit at the PCC, in exact rationals, at nominal V0;
- the **ride-through flag** (`gridq.ramp_ridethrough.ridethrough`): the G99 fault ride-through and
  fast fault current obligations the OEM PCS/PPC must deliver, never a pass;
- the **PSAL handoff bundle** (`gridq.ramp_ridethrough.handoff`): every input of the closed,
  versioned RMS/EMT catalogue, each Provided, Asked or NotApplicable.

Every limit is a cited clause constant (`gridq.ramp_ridethrough.sources`). Decisions
D199 to D204 (docs/decisions.md; renumbered at merge).
"""

from gridq.ramp_ridethrough.handoff import (
    CATALOGUE,
    CATALOGUE_HASH,
    PsalBundle,
    build_bundle,
    case_readiness,
)
from gridq.ramp_ridethrough.inputs import (
    BessDeclaration,
    Declarant,
    EvidencedQuantity,
    NetImportSeries,
    ReactiveRange,
    StudyInputs,
)
from gridq.ramp_ridethrough.ramp import screen_ramp
from gridq.ramp_ridethrough.ridethrough import RideThroughFlag, screen_ridethrough
from gridq.ramp_ridethrough.rvc import RvcScreen, screen_rvc
from gridq.ramp_ridethrough.screen import (
    RampRidethroughScreen,
    render_summary,
    screen_ramp_ridethrough,
)
from gridq.ramp_ridethrough.sources import CONSTANTS, PERSON_ONLY_ASKS, SOURCES

__all__ = [
    "CATALOGUE",
    "CATALOGUE_HASH",
    "CONSTANTS",
    "PERSON_ONLY_ASKS",
    "SOURCES",
    "BessDeclaration",
    "Declarant",
    "EvidencedQuantity",
    "NetImportSeries",
    "PsalBundle",
    "RampRidethroughScreen",
    "ReactiveRange",
    "RideThroughFlag",
    "RvcScreen",
    "StudyInputs",
    "build_bundle",
    "case_readiness",
    "render_summary",
    "screen_ramp",
    "screen_ramp_ridethrough",
    "screen_ridethrough",
    "screen_rvc",
]
