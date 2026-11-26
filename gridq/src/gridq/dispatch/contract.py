"""The dispatch-solver contract — the inner platform↔engine seam (M3).

This is the contract a half-hourly dispatch solver implements: given **one fixed system
size** plus the site, the assumption release and the dispatch constraints the value stack
imposes, return a `DispatchResult` — the energy accounting and per-stream value for that one
spec. It is deliberately *finer-grained* than the outer `OptimisationEngine` seam
(`gridq.engine`): the production `OptimisationEngine` will compose the **sizing outer loop**
(`gridq.sizing`) with a `DispatchSolver`, calling the solver once per candidate size.

The real MILP (linopy/HiGHS — Olly's research, deferred) implements `DispatchSolver` and drops
in behind this contract with no change to the sizer, finance or outputs. Until then
`SimpleSolver` is the labelled placeholder (see `simple_solver.py`): contract-valid, always
``not_bankable``, prices nothing and **invents no charge/discharge volume**.

What a real dispatch solver reads off this seam (so it is genuinely MILP-ready):
- the half-hourly load profile from ``problem.site_model.load_data`` (and the connection
  limits ``connection.mic_kw`` / ``connection.asc_kw`` the charge load is counted against),
- price / forecast inputs from the opaque ``problem.scenario`` (kept ``Any`` per the
  value-stack ``Scenario`` decoupling),
- the hard/soft dispatch constraints in ``problem.constraints`` (connection charge limit,
  warranty-cycle + throughput caps, export limit, …) — *descriptors* that name the binding
  SiteModel field / assumption key; the solver resolves the numbers at solve time.

Knowingly **absent** at this seam today (named so the producer does not read it as complete):
the half-hourly *price* series shape, and the Capacity-Market final-year deliverability
horizon (a sizing/degradation output — see decision D12). These land with the real engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, model_validator

from gridq.assumptions import AssumptionLibrary
from gridq.extensions import constraints as extension_constraint_registry
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    BankableStatus,
    Firmness,
    ImplementationLevel,
    MarketStream,
    SiteModel,
    SolverStatus,
    ValueStreamResult,
)
from gridq.schemas.provenance import GridQModel
from gridq.value_stack.interface import DispatchConstraintDescriptor
from gridq.value_stack.registry import StreamRegistry, default_registry


@dataclass(frozen=True)
class DispatchProblem:
    """One dispatch problem: a fixed system spec to dispatch for a site.

    A frozen dataclass (not a pydantic model) because it carries the live `AssumptionLibrary`
    and an opaque ``scenario`` (``Any``, per the value-stack decoupling) — neither is a wire
    value. The candidate spec (``power_kw`` / ``energy_kwh`` / ``duration_hours``) is an
    *input* to dispatch, not something the solver chooses.
    """

    site_model: SiteModel
    assumptions: AssumptionLibrary
    power_kw: float
    energy_kwh: float
    duration_hours: float
    requested_streams: list[MarketStream] = field(default_factory=list)
    # The pre-resolved, de-duplicated union of dispatch constraints the requested streams
    # impose (built via :func:`dispatch_constraints_for`). Descriptors only — no numbers.
    constraints: list[DispatchConstraintDescriptor] = field(default_factory=list)
    scenario: Any = None
    # Set only on the solve whose schedule is published as the customer bill. The solver then
    # re-solves once to return the minimum-peak schedule among the £-optimal ones. Never set in
    # the sizing loop.
    minimise_published_peak: bool = False


class DispatchResult(GridQModel):
    """The result of dispatching one fixed system spec.

    Mirrors the shape of `OptimisationResult` (so the sizer can map it straight onto the frozen
    contract) but is the **inner** M3/M4-lane artifact, not the frozen seam itself. It carries
    two fields the frozen `OptimisationResult` does **not** yet have — ``annual_charge_kwh`` and
    ``annual_discharge_kwh`` — using the names decision **D13** reserved for their eventual,
    jointly-owned promotion onto `OptimisationResult`, so that promotion is a rename-free copy.

    No ``created_at``: the result is kept pure/deterministic (the timestamp lives on the
    `OptimisationResult` the sizer assembles, where it is metadata excluded from every hash).
    """

    power_kw: float | None = Field(default=None, ge=0)
    energy_kwh: float | None = Field(default=None, ge=0)
    duration_hours: float | None = Field(default=None, ge=0)
    implementation_level: ImplementationLevel
    bankable_status: BankableStatus
    solver_status: SolverStatus
    dispatch_schedule_ref: str | None = None
    value_streams: list[ValueStreamResult] = Field(default_factory=list)
    gated_upside: list[ValueStreamResult] = Field(default_factory=list)
    constraints_binding: list[str] = Field(default_factory=list)
    # Annual energy accounting (the C010 producer seam). ``None`` = unavailable, never zero by
    # assumption: charge volume already includes round-trip losses by construction; if only
    # discharge is known, finance grosses it up by the round-trip-efficiency assumption.
    annual_charge_kwh: float | None = Field(default=None, ge=0)
    annual_discharge_kwh: float | None = Field(default=None, ge=0)
    annual_throughput_mwh: float | None = Field(default=None, ge=0)
    equivalent_full_cycles: float | None = Field(default=None, ge=0)
    #: The £/yr the dispatch avoided on the site's TIME-WINDOWED levy lines (in GB, the Capacity
    #: Market supplier obligation charged on 16:00–19:00 working-day November–February consumption).
    #: **Signed and NOT ``ge=0``**: it is normally a credit (positive — the battery discharges
    #: inside the peak window and charges outside it, where the rate is £0) but a window the optimum
    #: charged in makes it a real cost, and clamping that away would report more avoidance than
    #: the objective earned. ``None`` = the site carries no windowed levy, so finance's line is
    #: absent rather than zero — the same "unavailable, never invented" convention as the two
    #: volumes above. Charged exactly ONCE, in `finance.cashflows`' ``windowed_levies_avoided``
    #: cost line; it is deliberately absent from ``value_streams``, whose £ comes from the
    #: separate energy/DUoS arrays.
    windowed_levy_avoidance_gbp: float | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checks(self) -> DispatchResult:
        # A baseline-implementation dispatch can never be a bankable candidate (same rule the
        # frozen OptimisationResult enforces).
        if (
            self.implementation_level == ImplementationLevel.BASELINE
            and self.bankable_status == BankableStatus.BANKABLE_CANDIDATE
        ):
            raise ValueError(
                "a baseline-implementation dispatch result cannot be marked bankable_candidate."
            )
        # gated_upside may only carry gated-upside streams (mirrors OptimisationResult).
        off_register = [
            v.stream.value for v in self.gated_upside if v.stream not in GATED_UPSIDE_STREAMS
        ]
        if off_register:
            raise ValueError(
                f"gated_upside may only contain gated-upside streams; got {off_register}."
            )
        # A gated-upside stream can never be FIRM (ValueStreamResult enforces that) and, by
        # default, never sits in value_streams. The ONE exception (Revenue-Data Drive / D43): a
        # stream the customer explicitly consented to may be moved into the priced floor as
        # CONTRACTED (never FIRM) — such an entry carries a ``consent_status`` basis. An
        # unconsented gated stream in value_streams is still a leak.
        leaked = [
            v.stream.value
            for v in self.value_streams
            if v.stream in GATED_UPSIDE_STREAMS and not v.consent_status
        ]
        if leaked:
            raise ValueError(
                f"value_streams may not contain UNCONSENTED gated-upside streams (they belong in "
                f"gated_upside; a consented stream may sit here as contracted per D43); got "
                f"{leaked}."
            )
        return self

    @property
    def priced_revenue_gbp(self) -> float:
        """Total priced-floor revenue (firm/contracted only). Always 0.0 on the placeholder."""
        return round(
            sum(
                v.annual_revenue_gbp
                for v in self.value_streams
                if v.firmness in (Firmness.FIRM, Firmness.CONTRACTED)
            ),
            2,
        )


@runtime_checkable
class DispatchSolver(Protocol):
    """A half-hourly dispatch solver: dispatch one fixed spec to a `DispatchResult`.

    Implementations must return a schema-valid `DispatchResult` and **must not raise** on
    insufficient site data — they report it via ``solver_status``/``warnings`` (the
    `SimpleSolver` placeholder and the real MILP both honour this).
    """

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        """Solve one dispatch problem into a `DispatchResult`."""
        ...


def dispatch_constraints_for(
    requested_streams: Iterable[MarketStream],
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    registry: StreamRegistry | None = None,
    candidate_power_kw: float | None = None,
    extension_constraints: Iterable[str] = (),
) -> list[DispatchConstraintDescriptor]:
    """The de-duplicated union of dispatch constraints the requested streams impose.

    Reads the constraints off the value-stack registry (``default_registry()`` by default) —
    each stream names the constraints it imposes (connection charge limit, warranty/throughput
    cap, export limit, …). The descriptors carry **no fabricated numbers**: each points at the
    SiteModel field or assumption key that parameterises it at solve time. De-duplication is by
    ``constraint_id``, preserving first-seen (registry) order so the union is deterministic.

    ``candidate_power_kw`` (D61): the power of the candidate the dispatch is built for. Passed to
    every stream so a candidate-dependent descriptor (the local-flex award reservation) resolves at
    that candidate's own capability. ``None`` is the pre-candidate default, and the streams are
    then called exactly as before.

    ``extension_constraints`` (IC-12 2.0.0): ids of constraints registered through
    `gridq.extensions.constraints`, appended after the streams' own in the order given. The
    default is none, so a run that names none gets exactly the list it always got. An id that is
    not registered raises; a registered constraint whose ``describe`` returns ``None`` does not
    apply to this site and adds nothing; one that returns another id, or the id of a constraint
    a stream already imposes, is refused, because an extension adds a constraint and never
    replaces one.
    """
    reg = registry if registry is not None else default_registry()
    seen: dict[str, DispatchConstraintDescriptor] = {}
    for stream in reg.requested(requested_streams):
        descriptors = (
            stream.constraints(site_model, assumptions)
            if candidate_power_kw is None
            else stream.constraints(site_model, assumptions, candidate_power_kw=candidate_power_kw)
        )
        for descriptor in descriptors:
            seen.setdefault(descriptor.constraint_id, descriptor)
    for constraint_id in extension_constraints:
        spec = extension_constraint_registry.get(constraint_id)
        described = spec.describe(site_model, assumptions)
        if described is None:
            continue
        if described.constraint_id != constraint_id:
            raise ValueError(
                f"extension constraint {constraint_id!r} described {described.constraint_id!r}; "
                "a registered constraint describes only itself."
            )
        if constraint_id in seen:
            raise ValueError(
                f"extension constraint {constraint_id!r} is already imposed; an extension adds a "
                "constraint and never replaces one."
            )
        seen[constraint_id] = described
    return list(seen.values())


def candidate_dispatch_constraints(
    constraints: list[DispatchConstraintDescriptor],
    requested_streams: Iterable[MarketStream],
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    candidate_power_kw: float,
    registry: StreamRegistry | None = None,
) -> list[DispatchConstraintDescriptor]:
    """`constraints` with every candidate-dependent descriptor re-resolved at this candidate (D61).

    Order and any caller-supplied extra descriptors are preserved: a descriptor is replaced only
    when the per-candidate resolution produces one with the same ``constraint_id``.
    """
    per = {
        d.constraint_id: d
        for d in dispatch_constraints_for(
            requested_streams,
            site_model,
            assumptions,
            registry=registry,
            candidate_power_kw=candidate_power_kw,
        )
    }
    return [per.get(d.constraint_id, d) for d in constraints]
