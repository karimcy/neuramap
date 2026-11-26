"""Workload orchestrator adapters (WP-SITE-08, B8-02): orchestrator state to IC-9 declarations.

An adapter reads one orchestrator's public read-API state, captured in a labelled
`StateDocument` (it never connects to the orchestrator and never writes to it), together with the
operator's `AdapterPolicy` (the facts the orchestrator does not carry), and answers either

- `Declared`: an IC-9 1.0.0 `ComputeTurndownDeclaration` the frozen contract accepted, with a
  `DerivationLedger` tracing every number to the state and policy fields it came from, and the
  workload classes and resources it left out, each with a reason; or
- `Refused`: one or more `RefusalReason` (a closed `RefusalCode`, a JSON pointer, a detail), and
  never a fabricated envelope.

Every adapter is a `SealedAdapter` (the interface, `OrchestratorAdapter`, is the sealed
``declare``); adapters register in `orchestrator_adapters` under their API id. The reference
adapter is Slurm, read from slurmrestd ``v0.0.46`` responses
(`gridq.datacentre.orchestrators.slurm`). Decisions D158 to D162 in ``docs/decisions.md``.
Importing this package registers nothing.
"""

from __future__ import annotations

from gridq.datacentre.orchestrators.adapter import (
    SYNTHETIC_PREFIX,
    Draft,
    MappingRefused,
    OrchestratorAdapter,
    SealedAdapter,
    record_class_of,
)
from gridq.datacentre.orchestrators.ledger import (
    DerivationLedger,
    LedgerBuilder,
    LedgerEntry,
    SourceRef,
    Traced,
)
from gridq.datacentre.orchestrators.outcome import (
    Declared,
    Outcome,
    RefusalCode,
    RefusalReason,
    Refused,
)
from gridq.datacentre.orchestrators.policy import AdapterPolicy, PolicyPrice
from gridq.datacentre.orchestrators.registry import (
    OWNER,
    adapter_for,
    declare,
    orchestrator_adapters,
)
from gridq.datacentre.orchestrators.state import ApiSource, StateDocument

__all__ = [
    "OWNER",
    "SYNTHETIC_PREFIX",
    "AdapterPolicy",
    "ApiSource",
    "Declared",
    "DerivationLedger",
    "Draft",
    "LedgerBuilder",
    "LedgerEntry",
    "MappingRefused",
    "OrchestratorAdapter",
    "Outcome",
    "PolicyPrice",
    "RefusalCode",
    "RefusalReason",
    "Refused",
    "SealedAdapter",
    "SourceRef",
    "StateDocument",
    "Traced",
    "adapter_for",
    "declare",
    "orchestrator_adapters",
    "record_class_of",
]
