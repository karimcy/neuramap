"""Generic base classes for value streams — market-agnostic core.

These implement the shared behaviour every stream needs, leaving only the per-stream gate
logic to the concrete (GB) streams in ``gb_streams.py``. There is **no GB content here** and
no ``market == "GB"`` branch.

Two invariants are enforced structurally:

- **No invented revenue.** ``value_function`` prices a stream **only over a supplied dispatch
  schedule**, and only ever from numbers in the assumption library × dispatched quantities.
  With **no** dispatch schedule it computes nothing (``annual_revenue_gbp = 0.0`` + a note) —
  firmness (will this be in the floor?) stays orthogonal to the priced number (what is it
  worth?). The £ formulas are GB *content* (``gb_streams._price``); this market-agnostic base
  only orchestrates — it never holds a market number, so the core has no ``market == "GB"``
  branch. A placeholder/``needs_review`` assumption flows through as a labelled,
  not-bankable number (it names the key), never a silent "bankable" figure.
- **Gated upside can never be firm.** ``GatedUpsideStream`` is restricted to the gated-upside
  registry ids and can never be ``FIRM`` — mirroring the ``ValueStreamResult`` / ``GateResult``
  validators. It refuses an OPEN gate **unless an explicit consent basis is supplied** (D43):
  with the customer's consent it may open to ``CONTRACTED``, never ``FIRM``.

Category bases differ only in the firmness an OPEN gate implies:
``FloorStream`` → ``FIRM`` · ``ContractedStream`` → ``CONTRACTED`` ·
``GatedUpsideStream`` → ``CONTRACTED`` (and only when consented; else it stays ``CONDITIONAL``).
"""

from __future__ import annotations

import functools
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from gridq.assumptions import AssumptionLibrary
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    Firmness,
    MarketStream,
    SiteModel,
    ValueStreamResult,
)
from gridq.value_stack.dispatch import DispatchReadError, DispatchSummary, read_dispatch
from gridq.value_stack.gates import build_gate_result, enforce_declared_inputs
from gridq.value_stack.interface import (
    DispatchConstraintDescriptor,
    DispatchSchedule,
    GateContext,
    GateResult,
    GateStatus,
    RequiredInput,
    Scenario,
)

_NO_SCHEDULE_NOTE = (
    "value-stack: no dispatch schedule supplied — nothing priced (annual_revenue_gbp = 0.0). "
    "Pass a dispatch schedule (e.g. DispatchSummary) to value this stream over it."
)


class PricedValue(NamedTuple):
    """The £/yr a stream's value function computes over a dispatch schedule.

    Returned by the per-stream ``_price`` hook; the base assembles it into a
    ``ValueStreamResult`` with the gate-derived firmness. The market-agnostic base default
    prices nothing — GB streams override ``_price`` with formulas reading the assumption
    library (so no market number ever lives in the core).
    """

    annual_revenue_gbp: float = 0.0
    annual_cost_gbp: float = 0.0
    warnings: tuple[str, ...] = ()
    assumptions_used: tuple[str, ...] = ()


_GUARD_FLAG = "_gridq_null_input_guarded"


def _guard_check_gates(own: Callable[..., GateResult]) -> Callable[..., GateResult]:
    """Wrap one subclass's ``check_gates`` so the D45 null guard runs after it — always.

    The wrapper is applied by :meth:`BaseValueStream.__init_subclass__`, so a stream author cannot
    forget it and a future stream gets it without knowing it exists. That is the whole design: the
    honest behaviour already existed in several streams and the failure mode was **omission**, so
    the fix has to be structural rather than another convention.

    Wrapping rather than a template method (``check_gates`` calling an abstract ``_check_gates``)
    is deliberate: eleven concrete streams and four test-only subclasses define ``check_gates``
    directly, and a rename would leave the abstract hook unimplemented on subclasses this lane does
    not own. Wrapping keeps every existing override working untouched.

    ``context`` is threaded only to overrides that declare it — the pre-``GateContext`` two-argument
    signature is still in use (`tests/test_value_stack_interface.py`), so the arity is read once at
    class-creation time rather than guessed per call.
    """
    params = list(inspect.signature(own).parameters.values())
    takes_context = any(
        p.name == "context" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params
    )

    @functools.wraps(own)
    def _guarded(
        self: BaseValueStream,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        gate = (
            own(self, site_model, assumptions, context)
            if takes_context
            else own(self, site_model, assumptions)
        )
        return enforce_declared_inputs(self._stream_id, self._required, site_model, gate)

    setattr(_guarded, _GUARD_FLAG, True)
    return _guarded


class BaseValueStream(ABC):
    """Shared implementation of the :class:`ValueStream` interface.

    Subclasses supply only ``check_gates``; identity, required inputs, dispatch-constraint
    descriptors and the (revenue-free) value function are handled here.

    **Every subclass's ``check_gates`` is automatically wrapped in the D45 null-input guard**
    (:func:`gridq.value_stack.gates.enforce_declared_inputs`) at class-creation time. A stream
    whose declared ``blocks_pricing`` input reads ``None`` on the site cannot report an OPEN gate,
    whatever its own gate logic concluded — so a null can never be priced into the floor as a
    silent £0. Opting out is not a code path that exists.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get("check_gates")
        # Only wrap a class's OWN override: an intermediate base's already-wrapped method is
        # inherited as-is, so a chain like GatedUpsideStream → _SizeGatedUpsideStream →
        # BalancingMechanismStream applies the guard exactly once.
        if own is None or getattr(own, _GUARD_FLAG, False) or not callable(own):
            return
        cls.check_gates = _guard_check_gates(own)  # type: ignore[method-assign]

    def __init__(
        self,
        *,
        stream_id: MarketStream,
        display_name: str,
        open_firmness: Firmness,
        required_inputs: Sequence[RequiredInput],
        constraints: Sequence[DispatchConstraintDescriptor] = (),
        assumption_keys: Sequence[str] = (),
    ) -> None:
        self._stream_id = stream_id
        self._display_name = display_name
        self._open_firmness = open_firmness
        self._required = list(required_inputs)
        self._constraints = list(constraints)
        self._assumption_keys = list(assumption_keys)

    # --- identity ----------------------------------------------------------

    @property
    def stream_id(self) -> MarketStream:
        return self._stream_id

    @property
    def display_name(self) -> str:
        return self._display_name

    # --- declarations ------------------------------------------------------

    def required_inputs(self) -> list[RequiredInput]:
        return list(self._required)

    def constraints(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        *,
        candidate_power_kw: float | None = None,
    ) -> list[DispatchConstraintDescriptor]:
        """Dispatch constraints this stream imposes.

        v1 descriptors are static (they name the constraint and the input that
        parameterises it); the binding *values* come from the SiteModel/assumptions at
        solve time, never fabricated here. ``candidate_power_kw`` (D61) is accepted for the
        streams whose volume depends on the candidate, and ignored here: a static descriptor does
        not depend on it.
        """
        return list(self._constraints)

    # --- gating (subclass responsibility) ---------------------------------

    @abstractmethod
    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        """Deterministically evaluate this stream's availability gate for a site.

        ``context`` (default ``None``) carries generic run/engine facts the SiteModel cannot
        (deliverability, tender availability, portfolio MW — decision D12); ``None`` keeps
        the data-ask-only v1 behaviour.
        """
        raise NotImplementedError

    def _gate(
        self,
        status: GateStatus,
        *,
        missing_requirements: list[str] | None = None,
        unlock_condition: str | None = None,
        warnings: list[str] | None = None,
        consent_status: str | None = None,
    ) -> GateResult:
        """Build a consistent GateResult using this stream's open-firmness.

        ``consent_status`` is the orthogonal "consent given" label (D43): it is stamped onto the
        result regardless of the gate status and never changes the firmness.
        """
        return build_gate_result(
            self._stream_id,
            status,
            open_firmness=self._open_firmness,
            missing_requirements=missing_requirements,
            unlock_condition=unlock_condition,
            warnings=warnings,
            consent_status=consent_status,
        )

    # --- valuation (priced only over a supplied dispatch schedule) --------

    def value_function(
        self,
        dispatch_schedule: DispatchSchedule = None,
        site_model: SiteModel | None = None,
        assumptions: AssumptionLibrary | None = None,
        scenario: Scenario = None,
        context: GateContext | None = None,
    ) -> ValueStreamResult:
        """Value this stream over a dispatch schedule — **without inventing revenue**.

        The firmness and gate status always come from :meth:`check_gates` (the real gate
        outcome). The £/yr is computed by the per-stream :meth:`_price` hook **only** when a
        dispatch schedule is supplied *and* the gate is priceable or conditional upside; with
        no schedule, or a closed/excluded gate, nothing is priced (``0.0`` + a note). Requires
        ``site_model`` and ``assumptions``.
        """
        if site_model is None or assumptions is None:
            raise ValueError("value_function requires site_model and assumptions")
        gate = self.check_gates(site_model, assumptions, context)
        # Price only over a real schedule, and only for an OPEN (floor) or CONDITIONAL
        # (upside) gate — a closed/excluded stream is worth nothing to value.
        if dispatch_schedule is None or gate.gate_status in (
            GateStatus.CLOSED,
            GateStatus.UNKNOWN,
        ):
            return self._result_from_gate(gate)
        try:
            dispatch = read_dispatch(dispatch_schedule)
        except DispatchReadError as exc:
            return self._result_from_gate(
                gate, extra_warnings=[f"not priced: dispatch schedule unreadable — {exc}"]
            )
        priced = self._price(dispatch, site_model, assumptions, scenario, context, gate)
        return self._priced_result(gate, priced)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        """Compute the £/yr of this stream over ``dispatch``. Market-agnostic default: nothing.

        GB streams override this with their value formula (numbers from the assumption
        library × dispatched quantities). Keeping the default revenue-free here is what lets
        the core stay free of any market-specific number.
        """
        return PricedValue()

    def _result_from_gate(
        self, gate: GateResult, *, extra_warnings: Sequence[str] = ()
    ) -> ValueStreamResult:
        """Revenue-free result carrying the real firmness/gate (no dispatch schedule priced)."""
        warnings = list(gate.warnings)
        if gate.can_be_priced_now:
            warnings.append(_NO_SCHEDULE_NOTE)
        else:
            detail = ", ".join(gate.missing_requirements) or (
                gate.unlock_condition or "gate not open"
            )
            warnings.append(f"not priced: gate {gate.gate_status.value} — {detail}.")
        warnings.extend(extra_warnings)
        return ValueStreamResult(
            stream=self._stream_id,
            annual_revenue_gbp=0.0,
            annual_cost_gbp=0.0,
            firmness=gate.firmness,
            gate_status=gate.gate_status.value,
            unlock_condition=gate.unlock_condition,
            assumptions_used=list(self._assumption_keys),
            warnings=warnings,
            consent_status=gate.consent_status,
        )

    def _priced_result(self, gate: GateResult, priced: PricedValue) -> ValueStreamResult:
        """Assemble the priced result: computed £/yr + the gate-derived firmness/status."""
        assumptions_used = list(self._assumption_keys) + [
            k for k in priced.assumptions_used if k not in self._assumption_keys
        ]
        return ValueStreamResult(
            stream=self._stream_id,
            annual_revenue_gbp=priced.annual_revenue_gbp,
            annual_cost_gbp=priced.annual_cost_gbp,
            firmness=gate.firmness,
            gate_status=gate.gate_status.value,
            unlock_condition=gate.unlock_condition,
            assumptions_used=assumptions_used,
            warnings=list(gate.warnings) + list(priced.warnings),
            consent_status=gate.consent_status,
        )


class FloorStream(BaseValueStream):
    """An import-side floor stream — needs no external partner; OPEN ⇒ FIRM."""

    def __init__(
        self,
        *,
        stream_id: MarketStream,
        display_name: str,
        required_inputs: Sequence[RequiredInput],
        constraints: Sequence[DispatchConstraintDescriptor] = (),
        assumption_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(
            stream_id=stream_id,
            display_name=display_name,
            open_firmness=Firmness.FIRM,
            required_inputs=required_inputs,
            constraints=constraints,
            assumption_keys=assumption_keys,
        )


class ContractedStream(BaseValueStream):
    """A stream that is contracted when its gate opens (e.g. CM, local flex); OPEN ⇒ CONTRACTED."""

    def __init__(
        self,
        *,
        stream_id: MarketStream,
        display_name: str,
        required_inputs: Sequence[RequiredInput],
        constraints: Sequence[DispatchConstraintDescriptor] = (),
        assumption_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(
            stream_id=stream_id,
            display_name=display_name,
            open_firmness=Firmness.CONTRACTED,
            required_inputs=required_inputs,
            constraints=constraints,
            assumption_keys=assumption_keys,
        )


class GatedUpsideStream(BaseValueStream):
    """A gated-upside stream (wholesale/P415, BM, ancillary) — never in the merchant floor.

    Restricted to the gated-upside registry ids. An OPEN gate is refused **unless an explicit
    consent basis is supplied** (Revenue-Data Drive / D43): only when the customer has consented
    to this stream may it open — and then only to ``CONTRACTED``, never ``FIRM`` (the four
    never-FIRM layers stay intact). Without a consent basis it still cannot open, so it remains
    conditional upside with an unlock condition — exactly the pre-D43 behaviour.
    """

    def __init__(
        self,
        *,
        stream_id: MarketStream,
        display_name: str,
        required_inputs: Sequence[RequiredInput],
        constraints: Sequence[DispatchConstraintDescriptor] = (),
        assumption_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(
            stream_id=stream_id,
            display_name=display_name,
            open_firmness=Firmness.CONTRACTED,
            required_inputs=required_inputs,
            constraints=constraints,
            assumption_keys=assumption_keys,
        )
        if self._stream_id not in GATED_UPSIDE_STREAMS:
            raise ValueError(
                f"{self._stream_id.value!r} is not a gated-upside stream; "
                f"use FloorStream/ContractedStream instead."
            )

    def _gate(
        self,
        status: GateStatus,
        *,
        missing_requirements: list[str] | None = None,
        unlock_condition: str | None = None,
        warnings: list[str] | None = None,
        consent_status: str | None = None,
    ) -> GateResult:
        # A gated-upside stream may only open when the customer has explicitly consented to it
        # (consent_status supplied). An OPEN gate WITHOUT a consent basis is still forbidden — it
        # would put a merchant stream into the priced floor with no authorisation.
        if status == GateStatus.OPEN and consent_status is None:
            raise ValueError(
                f"gated-upside stream {self._stream_id.value!r} cannot open into the priced "
                f"floor without an explicit consent basis."
            )
        return super()._gate(
            status,
            missing_requirements=missing_requirements,
            unlock_condition=unlock_condition,
            warnings=warnings,
            consent_status=consent_status,
        )
