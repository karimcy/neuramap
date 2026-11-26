"""IC-13 audit events: frozen contract, version 1.0.0 (roadmap WP-PLAT-05, spec E-04 and B1-04).

One `AuditEvent` per read of customer data and per control write, allowed or denied. The log is
**append-only shaped**: each event carries its position (``seq``) and the content hash of the
event before it (``prev_hash``), so the events form a hash chain. Changing, removing, inserting or
reordering any event breaks every later link, and `verify_chain` reports where. The chain does
not stop a party that holds the whole log from rewriting all of it; anchoring the head hash
somewhere it cannot rewrite (WP-PLAT-06, e.g. the object store's write-once receipts, E-02) is
what does. Events are frozen and canonical-hashable with the same canonical JSON as every
contract here (`gridq.contracts._base.content_hash`).

Every event carries the five things E-04 / B1-04 ask for:

- **actor**: who, from their validated token (`actor_from_claims`): user, organisation, role,
  token id and issuer;
- **scope**: the `ScopeGrant` that allowed it (or `NotApplicable` when denied, with a reason);
- **target**: the `ResourceRef`, plus the `ControlCommand` for a control write;
- **action**: `AuditAction`, with ``outcome`` allowed or denied;
- **timestamp**: ``occurred_at``, UTC; non-decreasing along the chain.

The contract checks what can be checked from the event itself: an allowed event's scope really
covers its target and action, an allowed control write was made by Neura ops, and the actor's
role matches the organisation kind. Shares version and git tag with `gridq.contracts.identity`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, final

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    UtcInstant,
    content_hash,
    not_applicable,
)
from gridq.contracts.identity import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    GIT_TAG,
    ROLE_ORG_KIND,
    Action,
    IdentityClaims,
    OrgKind,
    ResourceKind,
    ResourceRef,
    Role,
    ScopeGrant,
    authorise,
)

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "CUSTOMER_DATA_KINDS",
    "GENESIS_PREV_HASH",
    "GIT_TAG",
    "Actor",
    "AuditAction",
    "AuditEvent",
    "ChainBroken",
    "ChainHead",
    "ControlCommand",
    "actor_from_claims",
    "audit_access",
    "next_event",
    "verify_chain",
]

#: ``prev_hash`` of the first event (``seq == 0``): there is no event before it.
GENESIS_PREV_HASH: Final = "sha256:" + "0" * 64

_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", max_length=200)]


class AuditAction(StrEnum):
    """What was attempted. The two classes spec E-04 requires to be logged."""

    CUSTOMER_DATA_READ = "customer_data_read"
    CONTROL_WRITE = "control_write"

    @property
    def requires(self) -> Action:
        return Action.READ if self is AuditAction.CUSTOMER_DATA_READ else Action.CONTROL_WRITE


#: Resource kinds that hold customer data. A device is a control target, not data.
CUSTOMER_DATA_KINDS: Final = frozenset(
    {
        ResourceKind.SITE,
        ResourceKind.CUSTOMER_DATA,
        ResourceKind.TELEMETRY,
        ResourceKind.LEDGER,
        ResourceKind.EVIDENCE,
        ResourceKind.SCENARIO_SET,
        ResourceKind.PACK,
    }
)


class Actor(ContractModel):
    """Who acted, copied from validated `IdentityClaims` (opaque ids; no personal data)."""

    subject: NonEmptyStr
    org_id: NonEmptyStr
    org_kind: OrgKind
    role: Role
    token_jti: NonEmptyStr
    token_issuer: NonEmptyStr

    @model_validator(mode="after")
    def _role_matches_org(self) -> Actor:
        if ROLE_ORG_KIND[self.role] is not self.org_kind:
            raise ValueError("actor role does not match its organisation kind")
        return self


def actor_from_claims(claims: IdentityClaims) -> Actor:
    return Actor(
        subject=claims.sub,
        org_id=claims.org.org_id,
        org_kind=claims.org.kind,
        role=claims.role,
        token_jti=claims.jti,
        token_issuer=claims.iss,
    )


class ControlCommand(ContractModel):
    """The command a control write carried (spec B1-04: full command audit trail)."""

    kind: Literal["control_command"]
    command_id: _ID
    point: NonEmptyStr
    value: float
    unit: NonEmptyStr


@final
class AuditEvent(ContractModel):
    """IC-13 v1.0.0 audit event. One link of an append-only hash chain."""

    contract: Literal["IC-13"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    event_id: _ID
    seq: Annotated[int, Field(ge=0)]
    #: Content hash of the event at ``seq - 1``; `GENESIS_PREV_HASH` when ``seq == 0``.
    prev_hash: Sha256
    occurred_at: UtcInstant
    actor: Actor
    action: AuditAction
    outcome: Literal["allowed", "denied"]
    scope: ScopeGrant | NotApplicable
    target: ResourceRef
    control: ControlCommand | NotApplicable = Field(discriminator="kind")
    reason: NonEmptyStr | NotApplicable

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AuditEvent is final: a new event shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> AuditEvent:
        if (self.seq == 0) != (self.prev_hash == GENESIS_PREV_HASH):
            raise ValueError("prev_hash is the genesis value exactly when seq == 0")
        if self.action is AuditAction.CONTROL_WRITE:
            if self.target.kind is not ResourceKind.DEVICE:
                raise ValueError("a control write targets a device")
            if not isinstance(self.control, ControlCommand):
                raise ValueError("a control write event carries its ControlCommand")
        else:
            if self.target.kind not in CUSTOMER_DATA_KINDS:
                raise ValueError(f"{self.target.kind.value} is not a customer-data resource")
            if not isinstance(self.control, NotApplicable):
                raise ValueError("only a control write carries a ControlCommand")
        if self.outcome == "allowed":
            if not isinstance(self.scope, ScopeGrant):
                raise ValueError("an allowed event names the scope that allowed it")
            if not self.scope.covers(self.action.requires, self.target):
                raise ValueError("the named scope does not cover this action on this target")
            if self.action is AuditAction.CONTROL_WRITE and self.actor.role is not Role.NEURA_OPS:
                raise ValueError("only neura_ops may make an allowed control write")
            if not isinstance(self.reason, NotApplicable):
                raise ValueError("an allowed event carries reason: not_applicable")
        else:
            if not isinstance(self.scope, NotApplicable):
                raise ValueError("a denied event carries scope: not_applicable")
            if isinstance(self.reason, NotApplicable):
                raise ValueError("a denied event states its reason")
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>``: the value the next event carries as ``prev_hash``."""
        return content_hash(self)


class ChainHead(ContractModel):
    """The last event of a chain: what the next event links to, and what gets anchored."""

    seq: Annotated[int, Field(ge=0)]
    event_hash: Sha256


class ChainBroken(ValueError):
    def __init__(self, seq: int, detail: str) -> None:
        super().__init__(f"audit chain broken at seq {seq}: {detail}")
        self.seq = seq


def next_event(
    head: ChainHead | None,
    *,
    event_id: str,
    occurred_at: datetime,
    actor: Actor,
    action: AuditAction,
    outcome: Literal["allowed", "denied"],
    scope: ScopeGrant | NotApplicable,
    target: ResourceRef,
    control: ControlCommand | NotApplicable,
    reason: str | NotApplicable,
) -> AuditEvent:
    """Build the event that follows ``head`` (``None`` starts a new chain)."""
    return AuditEvent(
        event_id=event_id,
        seq=0 if head is None else head.seq + 1,
        prev_hash=GENESIS_PREV_HASH if head is None else head.event_hash,
        occurred_at=occurred_at,
        actor=actor,
        action=action,
        outcome=outcome,
        scope=scope,
        target=target,
        control=control,
        reason=reason,
    )


def audit_access(
    head: ChainHead | None,
    claims: IdentityClaims,
    *,
    event_id: str,
    occurred_at: datetime,
    action: AuditAction,
    target: ResourceRef,
    control: ControlCommand | NotApplicable,
) -> AuditEvent:
    """Authorise ``action`` on ``target`` for validated ``claims`` and return the event to append.

    Allowed when a scope covers it (and, for a control write, the role is Neura ops); otherwise
    a denied event with the reason. The consumer appends it whichever the outcome.
    """
    grant = authorise(claims, action.requires, target)
    if action is AuditAction.CONTROL_WRITE and claims.role is not Role.NEURA_OPS:
        grant = None
    if grant is None:
        outcome: Literal["allowed", "denied"] = "denied"
        scope: ScopeGrant | NotApplicable = not_applicable(
            "no scope in the token permits this action on this target"
        )
        reason: str | NotApplicable = (
            f"no {action.requires.value} scope covers {target.kind.value} "
            f"{target.org_id}/{target.resource_id} for role {claims.role.value}"
        )
    else:
        outcome, scope, reason = "allowed", grant, not_applicable("allowed")
    return next_event(
        head,
        event_id=event_id,
        occurred_at=occurred_at,
        actor=actor_from_claims(claims),
        action=action,
        outcome=outcome,
        scope=scope,
        target=target,
        control=control,
        reason=reason,
    )


def verify_chain(events: Iterable[AuditEvent], *, after: ChainHead | None = None) -> ChainHead:
    """Check every link; return the head. ``after`` continues a chain verified earlier.

    Raises `ChainBroken` naming the first bad ``seq``. An empty iterable is refused: there is no
    head to return.
    """
    head = after
    last_time: datetime | None = None
    for e in events:
        want_seq = 0 if head is None else head.seq + 1
        want_prev = GENESIS_PREV_HASH if head is None else head.event_hash
        if e.seq != want_seq:
            raise ChainBroken(e.seq, f"expected seq {want_seq}")
        if e.prev_hash != want_prev:
            raise ChainBroken(e.seq, "prev_hash does not match the previous event's hash")
        if last_time is not None and e.occurred_at < last_time:
            raise ChainBroken(e.seq, "occurred_at goes backwards")
        last_time = e.occurred_at
        head = ChainHead(seq=e.seq, event_hash=e.content_hash())
    if head is None or head is after:
        raise ValueError("no events to verify")
    return head
