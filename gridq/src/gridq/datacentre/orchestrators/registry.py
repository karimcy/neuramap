"""The orchestrator adapter registry (D159).

One `gridq.extensions.registry.Registry` instance, local to this plug-in package (no CORE path is
edited): an adapter is registered once under its API id and is never overridden, every
registration states an owner and a version, reads are ordered by id, and nothing is registered at
import time. The built-in adapters are registered by `gridq.datacentre.orchestrators.builtin`, which
the registry imports on first use; they register under this package's owner, never ``CORE_OWNER``,
because this is a WS-SITE plug-in, not CORE.

`adapter_for` picks the adapter for a state document by its ``source.api_id``; `declare` is the
one-call path (pick, then declare). A state no registered adapter reads is a `Refused` with
``NO_ADAPTER_REGISTERED``, never an exception.
"""

from __future__ import annotations

from typing import Final

from gridq.datacentre.orchestrators.adapter import OrchestratorAdapter, SealedAdapter
from gridq.datacentre.orchestrators.outcome import (
    Outcome,
    RefusalCode,
    RefusalReason,
    Refused,
)
from gridq.datacentre.orchestrators.policy import AdapterPolicy
from gridq.datacentre.orchestrators.state import StateDocument
from gridq.extensions.registry import Registry, UnknownExtension

__all__ = ["OWNER", "adapter_for", "declare", "orchestrator_adapters"]

#: The owner every adapter shipped with this package registers under.
OWNER: Final = "gridq.datacentre.orchestrators"


def _validate(key: str, adapter: OrchestratorAdapter) -> None:
    if not isinstance(adapter, SealedAdapter):
        raise TypeError(f"orchestrator adapter {key!r} must be a SealedAdapter")
    if adapter.api_id != key:
        raise ValueError(
            f"orchestrator adapter {key!r} is registered under its api_id ({adapter.api_id!r})"
        )


#: API id -> the adapter that reads it.
orchestrator_adapters: Final[Registry[OrchestratorAdapter]] = Registry(
    "orchestrator_adapters",
    validate=_validate,
    home="gridq.datacentre.orchestrators.builtin",
)


def adapter_for(state: StateDocument) -> OrchestratorAdapter | None:
    """The adapter registered for the state's API, or ``None`` when there is none."""
    try:
        return orchestrator_adapters.get(state.source.api_id)
    except UnknownExtension:
        return None


def declare(state: StateDocument, policy: AdapterPolicy) -> Outcome:
    """Declare with the adapter registered for ``state``'s API, or refuse when none is."""
    adapter = adapter_for(state)
    if adapter is None:
        return Refused.of(
            "none",
            (
                RefusalReason(
                    code=RefusalCode.NO_ADAPTER_REGISTERED,
                    json_pointer="/source/api_id",
                    detail=(
                        f"no adapter reads {state.source.api_id!r}; registered: "
                        f"{list(orchestrator_adapters.ids())}"
                    ),
                ),
            ),
            state_hash=state.content_hash(),
            policy_hash=policy.content_hash(),
        )
    return adapter.declare(state, policy)
