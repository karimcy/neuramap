"""`UnderwritingEngine`: an optimisation engine priced on `underwriting_basis_v1`.

It builds the evidence-gated registry, hands it to the inner engine (a plain `MilpEngine` by
default, so every sizing candidate and the final valuation read the gated streams), and reports the
basis through ``last_study_basis()`` so `gridq.quotes.pipeline.generate_quote` caps the band, leads
the record with the basis note and puts the basis in ``input_hash``. The basis is deliberately
**not** passed to `MilpEngine` as ``study_basis=``: that argument selects the export-in-floor study
solver (D46), which this basis does not use (D90).

After the inner solve, a post-pass:

1. **guards the floor**: a stream the evidence does not admit is held out of the priced floor even
   when the inner engine did not read the gated registry;
2. **holds the shared power budget** (D100): when two or more services that share the
   inverter budget are in the floor, each one the default valuation paid in full under D57 is
   demoted whole to conditional, because the solver does not yet enforce the budget (WP-SIZE-03);
3. **records the evidence**: `OptimisationResult.evidence_refs` names, per floor stream, the
   objects its admission rests on, so the result's hash moves when the evidence does;
4. **labels** the result with the basis note and holds it not bankable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Final

from gridq.assumptions import AssumptionLibrary
from gridq.contracts.evidence import EvidenceObject, evidence_ref
from gridq.engine.interface import OptimisationEngine
from gridq.extensions import load_plugin
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    BankableStatus,
    EvidenceLink,
    Firmness,
    OptimisationRequest,
    OptimisationResult,
    SiteModel,
    ValueStreamResult,
    is_priced_floor,
)
from gridq.underwriting.basis import UnderwritingBasis
from gridq.underwriting.gate import (
    DEFAULT_NOTES_DELIMITER,
    UnderwritingGate,
    demote_result,
    underwriting_registry,
)
from gridq.value_stack.capacity_budget import POWER_BUDGET_STREAMS
from gridq.value_stack.registry import StreamRegistry

__all__ = ["D57_BUDGET_NOTE_PREFIX", "UnderwritingEngine"]

#: The opening of the note `optimisation.valuation._apply_capacity_budget` attaches to a stream
#: the shared power budget would have cut but D57 paid in full. No constant exists for it in
#: `value_stack.capacity_budget`, so it is matched here, and the match fails CLOSED: a floor with
#: two or more budget-sharing services and no recognisable note is demoted whole (D100).
D57_BUDGET_NOTE_PREFIX: Final = (
    "D57 ASSUMED APPROVED - STACKING EXCLUSIVITY AND THE SHARED POWER/ENERGY BUDGET"
)

_PLUGIN: Final = "gridq.underwriting"

EngineFactory = Callable[[StreamRegistry], OptimisationEngine]


def _milp_factory(seed: int) -> EngineFactory:
    def build(registry: StreamRegistry) -> OptimisationEngine:
        from gridq.optimisation.engine import MilpEngine  # heavy: imported only when built

        return MilpEngine(registry=registry, seed=seed)

    return build


class UnderwritingEngine:
    """An `OptimisationEngine` that prices on `underwriting_basis_v1`."""

    def __init__(
        self,
        basis: UnderwritingBasis,
        evidence: Iterable[EvidenceObject] = (),
        *,
        engine_factory: EngineFactory | None = None,
        base_registry: StreamRegistry | None = None,
        seed: int = 0,
    ) -> None:
        if not isinstance(basis, UnderwritingBasis):
            raise TypeError("UnderwritingEngine prices only on an UnderwritingBasis")
        # D102: any process that can produce an underwriting record has the basis
        # registered, so `quotes.assembly` can identify it and every renderer can label it.
        # Idempotent: `load_plugin` returns the first registration on a second call.
        load_plugin(_PLUGIN)
        self._basis = basis
        self._gate = UnderwritingGate(basis, evidence)
        self._registry = underwriting_registry(self._gate, base_registry)
        factory = engine_factory if engine_factory is not None else _milp_factory(seed)
        self._inner = factory(self._registry)

    # ------------------------------------------------------------------ the engine protocol

    def build_optimisation_request(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario_set: str | None = None,
    ) -> OptimisationRequest:
        return self._inner.build_optimisation_request(site_model, assumptions, scenario_set)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return self.apply(self._inner.solve(request), request.site_model)

    # ------------------------------------------------------------------ hooks the pipeline reads

    def last_study_basis(self) -> UnderwritingBasis:
        return self._basis

    @property
    def registry(self) -> StreamRegistry:
        return self._registry

    @property
    def gate(self) -> UnderwritingGate:
        return self._gate

    def __getattr__(self, name: str) -> Any:
        # The pipeline's other duck-typed hooks (`last_sizing_run`, `last_customer_bill`, ...) are
        # the inner engine's: it solved the run. A hook the inner engine lacks stays absent.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    # ------------------------------------------------------------------ the post-pass

    def apply(self, result: OptimisationResult, site: SiteModel) -> OptimisationResult:
        """Gate, budget-check, evidence-stamp and label one inner result. Idempotent."""
        floor: list[ValueStreamResult] = []
        upside: list[ValueStreamResult] = list(result.gated_upside)
        for v in result.value_streams:
            held = demote_result(v, self._gate.verdict(v.stream, site))
            if held.stream in GATED_UPSIDE_STREAMS and not is_priced_floor(held):
                upside.append(held)
            else:
                floor.append(held)
        upside = [demote_result(v, self._gate.verdict(v.stream, site)) for v in upside]

        floor, upside = self._hold_shared_budget(floor, upside)

        links: dict[tuple[str, str], EvidenceLink] = {
            (link.supports, link.evidence.evidence_hash): link
            for link in result.evidence_refs or ()
        }
        for v in floor:
            if not is_priced_floor(v):
                continue
            for obj in self._gate.verdict(v.stream, site).relied_on:
                ref = evidence_ref(obj)
                links.setdefault(
                    (v.stream.value, ref.evidence_hash),
                    EvidenceLink(supports=v.stream.value, evidence=ref),
                )

        note = self._basis.note()
        warnings = list(result.warnings)
        if note not in warnings:
            warnings.insert(0, note)
        bankable = result.bankable_status
        if bankable is not BankableStatus.NOT_BANKABLE:
            warnings.append(
                f"underwriting_basis_v1 holds this result NOT BANKABLE (the inner engine reported "
                f"{bankable.value}): the basis is not yet a lender-ready output (D98)."
            )
            bankable = BankableStatus.NOT_BANKABLE
        return OptimisationResult.model_validate(
            {
                **dict(result),
                "value_streams": floor,
                "gated_upside": upside,
                "evidence_refs": tuple(links.values()) or None,
                "warnings": warnings,
                "bankable_status": bankable,
            }
        )

    @staticmethod
    def _hold_shared_budget(
        floor: list[ValueStreamResult], upside: list[ValueStreamResult]
    ) -> tuple[list[ValueStreamResult], list[ValueStreamResult]]:
        """D100: no floor knowingly holds MW the shared budget would have cut."""
        sharing = [v for v in floor if v.stream in POWER_BUDGET_STREAMS and is_priced_floor(v)]
        if len(sharing) < 2:
            return floor, upside
        marked = [
            v for v in sharing if any(w.startswith(D57_BUDGET_NOTE_PREFIX) for w in v.warnings)
        ]
        targets = {v.stream for v in (marked or sharing)}
        names = ", ".join(sorted(v.stream.value for v in sharing))
        reason = (
            "the default valuation paid this stream in full under D57 where the shared inverter "
            "budget would have cut it"
            if marked
            else "the record carries no shared-budget note, which cannot be told apart from a "
            "reworded one, so the check fails closed"
        )
        lead = (
            "UNDERWRITING BASIS (underwriting_basis_v1): HELD OUT OF THE PRICED FLOOR AS "
            f"CONDITIONAL UPSIDE because {names} all hold the same inverter capacity and {reason}. "
            "The whole stream is held out, not the 'cut to' fraction, because the de-rated MW is "
            "not on the result. Released when WP-SIZE-03 enforces the shared power budget in the "
            "solver. The D57 assumed-approval register is not adopted on underwriting_basis_v1."
        )
        unlock = "underwriting_basis_v1: WP-SIZE-03 solver-level shared power budget"
        new_floor: list[ValueStreamResult] = []
        for v in floor:
            if v.stream not in targets:
                new_floor.append(v)
                continue
            held = ValueStreamResult.model_validate(
                {
                    **dict(v),
                    "firmness": Firmness.CONDITIONAL,
                    "gate_status": "conditional",
                    "unlock_condition": "; ".join(u for u in (unlock, v.unlock_condition) if u),
                    "warnings": [lead, DEFAULT_NOTES_DELIMITER, *v.warnings],
                }
            )
            (upside if held.stream in GATED_UPSIDE_STREAMS else new_floor).append(held)
        return new_floor, upside
