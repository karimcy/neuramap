"""Value stack & market-gating framework (M2 — value & constraint stack).

Every market value stream implements **one** generic interface and the core consumes it
generically — *if the core ever branches on ``market == "GB"``, the abstraction has failed*
(System Brief). This package provides:

- ``interface`` — the type contracts: :class:`ValueStream`, :class:`GateResult`,
  :class:`GateStatus`, :class:`RequiredInput`, :class:`DispatchConstraintDescriptor`.
- ``gates`` — deterministic, market-agnostic gate predicates + the gate-result builder.
- ``streams`` — generic base classes (floor / contracted / gated-upside) with a
  **revenue-free** value function (no invented numbers in v1).
- ``gb_streams`` — the GB **content**: the nine concrete GB value streams.
- ``registry`` — :class:`StreamRegistry` + :func:`default_registry` (the GB value stack).

Strict rules (SKILLS.md / parallel_development_plan §5): no invented revenue or data;
gated upside (P415/BM/ancillary) is never firm; missing data becomes a missing requirement,
never a guess; GB specifics live only in ``gb_streams`` + the assumption library.

See docs/value_stack.md.
"""

from __future__ import annotations

from gridq.value_stack.dispatch import (
    DispatchReadError,
    DispatchScheduleLike,
    DispatchSummary,
    read_dispatch,
)
from gridq.value_stack.gates import (
    D45_NULL_INPUT_LABEL,
    build_gate_result,
    enforce_declared_inputs,
    null_input_report,
)
from gridq.value_stack.gb_streams import gb_value_streams

# Imported in dependency order (interface → dispatch → gates → streams → gb_streams →
# registry) so the package initialises cleanly without circular-import surprises.
from gridq.value_stack.interface import (
    DispatchConstraintDescriptor,
    DispatchSchedule,
    GateContext,
    GateResult,
    GateStatus,
    RequiredInput,
    Scenario,
    ValueStream,
)
from gridq.value_stack.registry import StreamRegistry, default_registry
from gridq.value_stack.streams import (
    BaseValueStream,
    ContractedStream,
    FloorStream,
    GatedUpsideStream,
    PricedValue,
)

__all__ = [
    # interface / type contracts
    "ValueStream",
    "GateContext",
    "GateResult",
    "GateStatus",
    "RequiredInput",
    "DispatchConstraintDescriptor",
    "DispatchSchedule",
    "Scenario",
    # dispatch read-model (what a value function prices over)
    "DispatchScheduleLike",
    "DispatchSummary",
    "read_dispatch",
    "DispatchReadError",
    # gate helpers
    "build_gate_result",
    # the D45 null-input guard (applied automatically to every stream's check_gates)
    "D45_NULL_INPUT_LABEL",
    "enforce_declared_inputs",
    "null_input_report",
    # base stream classes
    "BaseValueStream",
    "FloorStream",
    "ContractedStream",
    "GatedUpsideStream",
    "PricedValue",
    # GB content + registry
    "gb_value_streams",
    "StreamRegistry",
    "default_registry",
]
