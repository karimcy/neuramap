"""What an orchestrator adapter answers: `Declared` or `Refused`, never an exception for a state
it cannot map (D159).

A refusal is an expected, hashable answer, like `NO_ENVELOPE_DECLARED`: "this state does not
support a declaration, for these reasons". It never carries a declaration, so a consumer that gets
one has nothing to bind and the load model's IT load stays untouched.

`RefusalCode` is closed so every reason can be tested and audited. A refusal may be partial: an
adapter that maps some workload classes and not others returns `Declared` with the unmapped classes
in ``skipped`` (under-offering is the safe side). With nothing left to offer it returns `Refused`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gridq.contracts._base import ContractModel, NonEmptyStr
from gridq.contracts.compute_turndown import ComputeTurndownDeclaration
from gridq.datacentre.orchestrators.ledger import DerivationLedger
from gridq.provenance.hashing import hash_json

__all__ = [
    "Declared",
    "Outcome",
    "RefusalCode",
    "RefusalReason",
    "Refused",
    "derivation_hash",
    "refusal_hash",
]


class RefusalCode(StrEnum):
    """Why an adapter declines a state, or a workload class within it. Closed."""

    #: No registered adapter reads the state's ``source.api_id``.
    NO_ADAPTER_REGISTERED = "no_adapter_registered"
    #: The adapter does not read this API version.
    UNSUPPORTED_API_VERSION = "unsupported_api_version"
    #: The payload is not the API's documented shape (a resource is missing, a field mistyped).
    MALFORMED_STATE = "malformed_state"
    #: The state and policy labels disagree (recorded state with a synthetic policy, or the
    #: reverse), or a synthetic record does not carry the SYNTHETIC- labels IC-9 requires.
    LABEL_MISMATCH = "label_mismatch"
    #: The policy does not state the site, the operator, or a fact IC-9 requires of them.
    SITE_OR_OPERATOR_UNSTATED = "site_or_operator_unstated"
    #: The operator's policy does not state a fact the offer needs (notice, settle time, holds,
    #: horizon, retained IT floor): the class is skipped, or the whole state refused.
    POLICY_FACT_MISSING = "policy_fact_missing"
    #: The measured power an offer would be sized from is absent (never estimated).
    POWER_BASIS_MISSING = "power_basis_missing"
    #: Nothing in the state can be turned down.
    NO_FLEXIBLE_WORKLOAD = "no_flexible_workload"
    #: Everything that could be turned down rounds to zero kW (IC-9: refuse, never emit zero).
    ZERO_OFFER = "zero_offer"
    #: A workload class whose orchestrator semantics have no IC-9 mapping, or whose policy
    #: lacks a fact the mapping needs.
    UNMAPPABLE_WORKLOAD = "unmappable_workload"
    #: The state contradicts itself (a job on a node the state does not list, two readings).
    AMBIGUOUS_STATE = "ambiguous_state"
    #: The frozen IC-9 validator refused the draft.
    CONTRACT_REJECTED = "contract_rejected"
    #: The draft holds a number with no derivation in the ledger.
    UNTRACED_VALUE = "untraced_value"


class RefusalReason(ContractModel):
    """One reason, located by an RFC 6901 pointer into the state (or ``""`` for the whole)."""

    code: RefusalCode
    json_pointer: str
    detail: NonEmptyStr


def _sorted_reasons(reasons: tuple[RefusalReason, ...]) -> tuple[RefusalReason, ...]:
    return tuple(sorted(reasons, key=lambda r: (r.code.value, r.json_pointer, r.detail)))


def refusal_hash(
    adapter_id: str, state_hash: str, policy_hash: str, reasons: tuple[RefusalReason, ...]
) -> str:
    return hash_json(
        {
            "adapter_id": adapter_id,
            "state_hash": state_hash,
            "policy_hash": policy_hash,
            "reasons": [r.model_dump(mode="json") for r in _sorted_reasons(reasons)],
        }
    )


def derivation_hash(
    *,
    adapter_id: str,
    adapter_version: str,
    state_hash: str,
    policy_hash: str,
    ledger_hash: str,
    declaration_hash: str,
) -> str:
    return hash_json(
        {
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "state_hash": state_hash,
            "policy_hash": policy_hash,
            "ledger_hash": ledger_hash,
            "declaration_hash": declaration_hash,
        }
    )


@dataclass(frozen=True)
class Refused:
    """The adapter declines: no declaration, at least one reason (sorted)."""

    adapter_id: str
    reasons: tuple[RefusalReason, ...]
    state_hash: str
    policy_hash: str
    refusal_hash: str

    @classmethod
    def of(
        cls,
        adapter_id: str,
        reasons: tuple[RefusalReason, ...],
        *,
        state_hash: str,
        policy_hash: str,
    ) -> Refused:
        if not reasons:
            raise ValueError("a refusal states at least one reason")
        ordered = _sorted_reasons(reasons)
        return cls(
            adapter_id=adapter_id,
            reasons=ordered,
            state_hash=state_hash,
            policy_hash=policy_hash,
            refusal_hash=refusal_hash(adapter_id, state_hash, policy_hash, ordered),
        )

    @property
    def codes(self) -> tuple[RefusalCode, ...]:
        return tuple(r.code for r in self.reasons)


@dataclass(frozen=True)
class Declared:
    """A declaration that passed the frozen IC-9 validator, with the ledger of every number in it,
    the workload classes left out, the resources withheld from the offer, and the hashes that
    bind them. ``skipped`` and ``withheld`` are diagnostics: they never appear in IC-9."""

    adapter_id: str
    adapter_version: str
    declaration: ComputeTurndownDeclaration
    ledger: DerivationLedger
    #: Workload classes not offered, each with its reason.
    skipped: tuple[RefusalReason, ...]
    #: Resources (e.g. nodes) left out of an offered class, each with its reason.
    withheld: tuple[RefusalReason, ...]
    state_hash: str
    policy_hash: str
    ledger_hash: str
    derivation_hash: str


Outcome = Declared | Refused
