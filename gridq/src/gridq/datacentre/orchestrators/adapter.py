"""The adapter interface: a sealed template method no adapter can route around (D159).

`OrchestratorAdapter` is the typed interface a consumer calls: ``declare(state, policy)`` answers
`Declared` or `Refused`. Every concrete adapter is a `SealedAdapter`, whose ``declare`` is final
(``__init_subclass__`` refuses a subclass that overrides it) and runs the same sequence for every
orchestrator:

1. the state's API and version are the adapter's (else ``NO_ADAPTER_REGISTERED`` /
   ``UNSUPPORTED_API_VERSION``);
2. the labels agree: a synthetic state with a synthetic policy gives a ``synthetic_fixture``
   declaration, a recorded state with an operator's policy a ``registered`` one, anything else is
   ``LABEL_MISMATCH``;
3. the site and operator are stated (``SITE_OR_OPERATOR_UNSTATED``);
4. the subclass's `_map` turns the state into a JSON-mode draft whose every number is a `Traced`
   value from the ledger (or raises `MappingRefused` with its reasons);
5. `seal` places the numbers and builds the ledger (a bare number is ``UNTRACED_VALUE``);
6. ``resolved_hash`` is filled by the contract's own `resolved_hash_of`, and the draft is validated
   by the frozen IC-9 model, `ComputeTurndownDeclaration` (``CONTRACT_REJECTED`` if it refuses).

So an adapter can only ever hand back a declaration the frozen contract accepted, whose every
integer has a stated derivation, and it never raises for a state it cannot map. Adapters read no
clock and open no connection (`tests/test_orchestrator_adapters.py` checks the package's imports).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal, Protocol, runtime_checkable

from pydantic import ValidationError

from gridq.contracts._base import Unknown
from gridq.contracts.compute_turndown import ComputeTurndownDeclaration, resolved_hash_of
from gridq.datacentre.orchestrators.ledger import LedgerBuilder, UntracedValue, seal
from gridq.datacentre.orchestrators.outcome import (
    Declared,
    Outcome,
    RefusalCode,
    RefusalReason,
    Refused,
    derivation_hash,
)
from gridq.datacentre.orchestrators.policy import AdapterPolicy
from gridq.datacentre.orchestrators.state import StateDocument
from gridq.provenance.hashing import hash_json

__all__ = [
    "SYNTHETIC_PREFIX",
    "Draft",
    "MappingRefused",
    "OrchestratorAdapter",
    "SealedAdapter",
    "record_class_of",
]

#: IC-9's label for synthetic ids and labels (`gridq.contracts.compute_turndown`).
SYNTHETIC_PREFIX: Final = "SYNTHETIC-"

RecordClass = Literal["registered", "synthetic_fixture"]


def reason(code: RefusalCode, json_pointer: str, detail: str) -> RefusalReason:
    return RefusalReason(code=code, json_pointer=json_pointer, detail=detail)


class MappingRefused(Exception):
    """Raised inside `_map` to decline the whole state; the base turns it into `Refused`."""

    def __init__(self, *reasons: RefusalReason) -> None:
        if not reasons:
            raise ValueError("a refusal states at least one reason")
        super().__init__("; ".join(r.detail for r in reasons))
        self.reasons = tuple(reasons)


@dataclass(frozen=True)
class Draft:
    """What `_map` returns: the JSON-mode declaration draft (numbers as `Traced`), the workload
    classes it left out and the resources it withheld."""

    body: Mapping[str, Any]
    skipped: tuple[RefusalReason, ...] = ()
    withheld: tuple[RefusalReason, ...] = ()


@runtime_checkable
class OrchestratorAdapter(Protocol):
    """Reads one orchestrator's state document; declares IC-9 turn-down or refuses."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    @property
    def api_id(self) -> str: ...

    @property
    def supported_api_versions(self) -> tuple[str, ...]: ...

    def declare(self, state: StateDocument, policy: AdapterPolicy) -> Outcome: ...


def record_class_of(state: StateDocument, policy: AdapterPolicy) -> RecordClass | None:
    """The IC-9 ``record_class`` the two labels give, or ``None`` when they disagree."""
    if state.label == "synthetic" and policy.label == "synthetic":
        return "synthetic_fixture"
    if state.label == "recorded" and policy.label == "operator_supplied":
        return "registered"
    return None


class SealedAdapter(ABC):
    """Base of every adapter. Subclasses set the class attributes and implement `_map` only."""

    adapter_id: ClassVar[str]
    adapter_version: ClassVar[str]
    api_id: ClassVar[str]
    supported_api_versions: ClassVar[tuple[str, ...]]
    policy_type: ClassVar[type[AdapterPolicy]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "declare" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} overrides declare: the sequence is sealed; implement _map"
            )

    @abstractmethod
    def _map(
        self,
        state: StateDocument,
        policy: Any,
        ledger: LedgerBuilder,
        record_class: RecordClass,
    ) -> Draft:
        """The JSON-mode IC-9 draft, without ``resolved_hash``. Every number is a `Traced`
        value from ``ledger``. Raise `MappingRefused` to decline the whole state."""

    def declare(self, state: StateDocument, policy: AdapterPolicy) -> Outcome:
        if not isinstance(state, StateDocument):
            raise TypeError(f"state must be a StateDocument, not {type(state).__name__}")
        if not isinstance(policy, self.policy_type):
            raise TypeError(
                f"{self.adapter_id} reads a {self.policy_type.__name__}, "
                f"not {type(policy).__name__}"
            )
        state_hash, policy_hash = state.content_hash(), policy.content_hash()

        def refused(*reasons: RefusalReason) -> Refused:
            return Refused.of(
                self.adapter_id, tuple(reasons), state_hash=state_hash, policy_hash=policy_hash
            )

        if state.source.api_id != self.api_id:
            return refused(
                reason(
                    RefusalCode.NO_ADAPTER_REGISTERED,
                    "/source/api_id",
                    f"{self.adapter_id} reads {self.api_id}, not {state.source.api_id}",
                )
            )
        if state.source.api_version not in self.supported_api_versions:
            return refused(
                reason(
                    RefusalCode.UNSUPPORTED_API_VERSION,
                    "/source/api_version",
                    f"{self.adapter_id} reads {list(self.supported_api_versions)}, "
                    f"not {state.source.api_version}",
                )
            )
        record_class = record_class_of(state, policy)
        if record_class is None:
            return refused(
                reason(
                    RefusalCode.LABEL_MISMATCH,
                    "/label",
                    f"a {state.label} state with a {policy.label} policy: a declaration is "
                    "synthetic only from synthetic inputs, an operator's only from a recording",
                )
            )
        unstated = [
            reason(RefusalCode.SITE_OR_OPERATOR_UNSTATED, f"policy:/{field}", value.reason)
            for field, value in (("site_id", policy.site_id), ("operator_id", policy.operator_id))
            if isinstance(value, Unknown)
        ]
        if unstated:
            return refused(*unstated)

        try:
            draft = self._map(state, policy, LedgerBuilder(), record_class)
        except MappingRefused as exc:
            return refused(*exc.reasons)
        try:
            body, ledger = seal(draft.body)
        except UntracedValue as exc:
            return refused(
                reason(
                    RefusalCode.UNTRACED_VALUE,
                    f"declaration:{exc.target}",
                    "a number with no derivation in the ledger",
                )
            )
        try:
            body["resolved_hash"] = resolved_hash_of(body)
            declaration = ComputeTurndownDeclaration.model_validate(body, strict=False)
        except ValidationError as exc:
            return refused(
                *(
                    reason(
                        RefusalCode.CONTRACT_REJECTED,
                        "declaration:/" + "/".join(str(p) for p in err["loc"]),
                        err["msg"],
                    )
                    for err in exc.errors(include_url=False)
                )
            )
        ledger_hash = hash_json(ledger.model_dump(mode="json"))
        return Declared(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            declaration=declaration,
            ledger=ledger,
            skipped=tuple(sorted(draft.skipped, key=_key)),
            withheld=tuple(sorted(draft.withheld, key=_key)),
            state_hash=state_hash,
            policy_hash=policy_hash,
            ledger_hash=ledger_hash,
            derivation_hash=derivation_hash(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                state_hash=state_hash,
                policy_hash=policy_hash,
                ledger_hash=ledger_hash,
                declaration_hash=declaration.content_hash(),
            ),
        )


def _key(r: RefusalReason) -> tuple[str, str, str]:
    return (r.json_pointer, r.code.value, r.detail)
