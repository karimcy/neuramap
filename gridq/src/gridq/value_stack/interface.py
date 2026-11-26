"""The generic value-stream interface — the most important abstraction in M2.

Every market value stream (import-side floor, Capacity Market, local flex, the gated
wholesale/BM/ancillary upside) implements **one** interface, and the core consumes it
generically. The rule from the System Brief is absolute: *if the core ever branches on
``market == "GB"``, the abstraction has failed.* GB specifics are content (concrete
streams in ``gb_streams.py`` + numbers in the assumption library), never core branches.

This module holds only the **type contracts** of that interface:

- ``GateStatus`` — open / closed / conditional / unknown.
- ``RequiredInput`` — one input a stream needs (for the ranked data ask).
- ``DispatchConstraintDescriptor`` — a dispatch constraint a stream imposes.
- ``GateResult`` — the deterministic outcome of a stream's availability gate.
- ``ValueStream`` — the Protocol each concrete stream satisfies.

The per-stream £/yr result is the existing frozen ``ValueStreamResult`` contract in
``gridq.schemas`` — this framework reuses it rather than inventing a parallel shape.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import Field, model_validator

from gridq.assumptions import AssumptionLibrary
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    AvailabilityCommitment,
    CommitmentEvidence,
    Firmness,
    MarketStream,
    SiteModel,
    ValueStreamResult,
)
from gridq.schemas.provenance import GridQModel

# ---------------------------------------------------------------------------
# Opaque, cross-lane types.
#
# The concrete half-hourly dispatch schedule is M3's to define (it lands behind the
# OptimisationEngine seam); the Scenario object is M6's. The value-stream interface must
# not bind either representation, so v1 keeps them opaque (``Any``). A value function
# receives whatever the dispatch engine / scenario engine produce and prices against it
# once those lanes are wired — until then nothing is priced (see ``gb_streams``).
# ---------------------------------------------------------------------------
DispatchSchedule: TypeAlias = Any
Scenario: TypeAlias = Any


class GateStatus(StrEnum):
    """Whether a stream's availability gate is open for this site, right now.

    - ``OPEN`` — every required input is present; the stream can be priced into the floor.
    - ``CLOSED`` — a required input is wholly absent; the stream is excluded (not priceable).
    - ``CONDITIONAL`` — the stream is unlockable but a precondition is unmet (e.g. a
      route-to-market partner, confirmed deliverability, an open tender); it is listed as
      conditional upside with its unlock condition, never priced into the floor.
    - ``UNKNOWN`` — the gate could not be determined; treated conservatively (not priced).
    """

    OPEN = "open"
    CLOSED = "closed"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class RequiredInput(GridQModel):
    """One input a value stream needs in order to be assessed or priced.

    These compose into the site's ranked data ask: a missing required input becomes a
    ``missing_requirements`` line on the ``GateResult`` and a candidate data ask — never
    an invented value.

    **This declaration is enforced, not documentation (D45).** Where ``key`` names a dotted
    ``SiteModel`` path, :func:`gridq.value_stack.gates.enforce_declared_inputs` reads it on every
    gate evaluation and refuses to let an OPEN gate stand on a ``None``. So a stream that declares
    an input it forgets to check in ``check_gates`` still cannot price a null as £0 — see the
    module docstring of ``gates`` for why that mattered.
    """

    key: str = Field(description="Canonical input key, e.g. 'tariff_stack.import_rates'.")
    description: str = Field(description="Human-readable description of the input.")
    blocks_pricing: bool = Field(
        default=True,
        description="True if the stream cannot be priced into the floor without this input.",
    )
    alternatives: tuple[str, ...] = Field(
        default=(),
        description="Other dotted SiteModel paths that satisfy this requirement instead of "
        "``key`` — the requirement is a DISJUNCTION (e.g. connection.mic_kw OR connection.asc_kw; "
        "a measured peak OR half-hourly coverage). Declared because the D45 null guard reads this "
        "declaration literally: without the alternatives it would close a gate whose real "
        "requirement is satisfied by a sibling field, which is a false refusal, not a guard.",
    )


class DispatchConstraintDescriptor(GridQModel):
    """A dispatch constraint a stream imposes on the optimiser.

    A *descriptor* — it names the constraint and points at the input/assumption that
    parameterises it. It carries **no fabricated numbers**: the binding values come from
    the SiteModel (e.g. ``connection.mic_kw``) or the assumption library at solve time.
    """

    constraint_id: str = Field(description="Stable id, e.g. 'connection_charge_limit'.")
    description: str
    kind: str = Field(
        description="Constraint family, e.g. 'connection_limit' | 'throughput_cap' | "
        "'deliverability' | 'export_limit' | 'availability_window'."
    )
    parameter_source: str | None = Field(
        default=None,
        description="The SiteModel field or assumption key that parameterises it "
        "(e.g. 'connection.mic_kw', 'warranty_cycle_limits').",
    )
    parameters: AvailabilityCommitment | None = Field(
        default=None,
        description="Typed payload for compilable kinds ('availability_window' / "
        "'held_state_of_energy'): the contracted commitment the solver's kind→builder table "
        "(W7-2) compiles into window constraints. None for descriptors parameterised via "
        "parameter_source only — every pre-W7-1 descriptor is unchanged. Additive (C026); "
        "future compilable kinds may widen this to a union.",
    )


class GateResult(GridQModel):
    """The deterministic outcome of running one stream's availability gate for a site.

    A pure function of the site data + assumption library — no randomness, no wall-clock,
    no invented value. ``firmness`` is the priced-case firmness implied by the gate, and
    ``can_be_priced_now`` is true **iff** the gate is OPEN (only OPEN streams enter the
    priced floor). Gated-upside streams can never be firm (mirrors ``ValueStreamResult``).
    """

    stream: MarketStream
    gate_status: GateStatus
    can_be_priced_now: bool
    firmness: Firmness
    missing_requirements: list[str] = Field(default_factory=list)
    unlock_condition: str | None = None
    warnings: list[str] = Field(default_factory=list)
    consent_status: str | None = Field(
        default=None,
        description="The orthogonal 'consent given' label (Revenue-Data Drive / D43): "
        "'given' when the customer explicitly consented to this stream at intake, else None. "
        "Additive and optional — it never affects firmness or priceability; it authorises the "
        "gated-upside contracted case and drives the proposal's 'consent given' line.",
    )

    @model_validator(mode="after")
    def _consistency(self) -> GateResult:
        # Only an OPEN gate may be priced now; everything else is held out of the floor.
        if self.can_be_priced_now != (self.gate_status == GateStatus.OPEN):
            raise ValueError(
                "can_be_priced_now must be True iff gate_status is OPEN "
                f"(status={self.gate_status.value!r}, can_be_priced_now={self.can_be_priced_now})."
            )
        # A non-open gate cannot be firm; an open gate cannot be excluded.
        if self.gate_status != GateStatus.OPEN and self.firmness == Firmness.FIRM:
            raise ValueError(
                f"a {self.gate_status.value!r} gate cannot imply FIRM firmness "
                f"for {self.stream.value!r}."
            )
        if self.gate_status == GateStatus.OPEN and self.firmness == Firmness.EXCLUDED:
            raise ValueError(
                f"an OPEN gate cannot imply EXCLUDED firmness for {self.stream.value!r}."
            )
        # Gated-upside streams are structurally barred from FIRM (same rule the result enforces).
        if self.stream in GATED_UPSIDE_STREAMS and self.firmness == Firmness.FIRM:
            raise ValueError(f"gated-upside stream {self.stream.value!r} cannot be marked firm.")
        # A conditional gate must say how it unlocks.
        if self.gate_status == GateStatus.CONDITIONAL and not self.unlock_condition:
            raise ValueError(
                f"a conditional gate for {self.stream.value!r} must carry an unlock_condition."
            )
        return self


class GateContext(GridQModel):
    """Generic, market-agnostic, pre-solve facts a gate may need that the SiteModel cannot carry.

    Three gate inputs are **not** the site's own data, so they do not belong on the frozen
    ``SiteModel`` (decision D12, OPEN_COMMENTS C006): de-rated **deliverability** is
    engine/sizing-derived, local-flex **tender availability** is time-varying external
    market state, and the aggregated **portfolio MW** is fleet/run-level. They flow through
    this context instead — threaded into :meth:`ValueStream.check_gates` and
    :meth:`ValueStream.value_function` as ``context`` — populated by the producing lanes
    (the sized engine M3/M4, a confirmed local-flex source, portfolio runs) when they exist.

    Every field defaults to ``None`` (unknown), so a ``context=None`` call is identical to
    the data-ask-only v1 behaviour: the affected streams stay CONDITIONAL/missing. This is
    **not** a ``market == "GB"`` branch and **not** the assumption library — it is generic
    run/engine context the core threads through unread.
    """

    deliverability_confirmed: bool | None = Field(
        default=None,
        description="True iff de-rated capability is verified to hold to the final contract "
        "year at degraded (end-of-life) capability (engine/sizing-derived).",
    )
    local_flex_tender_open: bool | None = Field(
        default=None,
        description="True iff an open local-flex tender in the site's DNO area is held/awarded.",
    )
    portfolio_mw: float | None = Field(
        default=None,
        ge=0,
        description="Aggregated portfolio power (MW) for portfolio-gated streams; a single-site "
        "run carries none.",
    )
    portfolio_threshold_mw: float | None = Field(
        default=None,
        ge=0,
        description="Minimum aggregated portfolio MW a portfolio-gated stream requires "
        "(run/market context — not an invented assumption-library number).",
    )
    evidence: list[CommitmentEvidence] = Field(
        default_factory=list,
        description="The provenance behind the asserted gate facts (W7-4): who confirmed "
        "what, on what basis, against which contract reference. A context whose booleans are "
        "set without matching evidence should be treated as unattributed — producers built "
        "here always pair them.",
    )


@runtime_checkable
class ValueStream(Protocol):
    """The generic market value-stream interface. The core consumes any stream via this.

    A stream knows its identity, the inputs it needs, how to gate itself for a site, how
    to value a dispatch schedule, and the dispatch constraints it imposes. Concrete GB
    streams live in ``gb_streams.py``; the base machinery is in ``streams.py``.
    """

    @property
    def stream_id(self) -> MarketStream:
        """The registry id of this stream."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for outputs."""
        ...

    def required_inputs(self) -> list[RequiredInput]:
        """The inputs this stream needs to be assessed/priced (drives the data ask)."""
        ...

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        """Deterministically evaluate this stream's availability gate for a site.

        ``context`` carries generic, market-agnostic run/engine facts (deliverability,
        tender availability, portfolio MW) the site data cannot; ``None`` ⇒ data-ask-only.
        """
        ...

    def value_function(
        self,
        dispatch_schedule: DispatchSchedule,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None = None,
    ) -> ValueStreamResult:
        """Value this stream over a dispatch schedule. Never invents revenue (see module)."""
        ...

    def constraints(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        *,
        candidate_power_kw: float | None = None,
    ) -> list[DispatchConstraintDescriptor]:
        """The dispatch constraints this stream imposes on the optimiser.

        ``candidate_power_kw`` (D61) is the power of the candidate the dispatch is being built
        for; a stream whose constraint volume depends on the battery's own capability resolves it
        there. ``None`` is the pre-candidate default.
        """
        ...
