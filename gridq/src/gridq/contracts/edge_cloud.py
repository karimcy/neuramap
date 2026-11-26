"""IC-5 edge ↔ cloud protocol: frozen contract 1.0.0 (roadmap WP-EDGE-01, spec §10A, B1/B2).

What the cloud (live optimisation, B2) and the site edge (gateway, B1) say to each other, and what
each must conclude from it. Spec §8: "The edge holds the safety-critical constraints autonomously.
The cloud optimises within them." Every rule here that decides safety is a **pure function** the
edge runs on what it holds; nothing a message says can loosen a constraint the edge enforces.

Spec §10A → this contract:

=====================================  =======================================================
spec §10A IC-5                         here
=====================================  =======================================================
setpoint schema                        `SetpointSchedule` / `SetpointPeriod`, checked by
                                       `check_setpoint_schedule` against the IC-3 timeline
heartbeat                              `CloudHeartbeat`, `EdgeHeartbeat`, `peer_head` on every
                                       frame
staleness thresholds                   `LinkProfile` (all required, integer ms), `link_state`
acknowledgement semantics              `ScheduleAck` (a hash-chained state machine),
                                       `verify_ack_chain`, `governing_timeline`
fail-safe trigger codes                `TriggerCode` and `TRIGGERS`, `FailSafeEvent` (a
                                       hash-chained episode), `verify_episode`
latency budget                         `LinkProfile.latency_budgets` (`LatencyBudget`), the
                                       schedule's ``deliver_by``, `check_latency`
DNO ANM path local-only                `AnmEvent` is edge-to-cloud only; `anm_authority` is
                                       ``edge_local``; `effective_import_cap` only tightens
=====================================  =======================================================

**Frames (D114).** Two frame models, one per direction (`CloudToEdgeFrame`,
`EdgeToCloudFrame`), each a header plus a body discriminated on ``kind``. A frame is identified by
``(site_id, direction, link_epoch, seq)`` and bound by its content hash; each frame carries the hash
of the one before it (``prev_hash``, genesis at ``seq == 0``), so a direction's frames are a hash
chain like IC-13's audit log. A retransmission repeats the identical bytes. `ChainTracker` is the
receiver's verdict on each arriving frame (in order, duplicate, gap, late fill, fork, stale epoch,
profile mismatch, new epoch). Transport-agnostic: the same frames go over HTTP, MQTT or OPC UA.

**Numbers (D116, D120).** Every interval, threshold and latency bound is a field of the
site's `LinkProfile`, required and with no default: no published source sets a cloud-to-edge
schedule latency or a staleness threshold for this link, and the spec's "default 2 min" and
"≤5 s p99" are Neura's own targets, which a profile may adopt but the contract does not assume.
Durations are integer milliseconds (suffix ``_ms``); power integer kW, energy integer kWh and
temperature integer tenths of a degree, as IC-3.

**ANM (D119).** The DNO's ANM signal is received, enforced and acknowledged at the edge,
with the cloud link up or down. No cloud-to-edge body can carry an ANM signal or state (a schema
walk proves it), and the cloud's import cap can only tighten what ANM sets.

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every frame and profile); JSON Schemas frozen at
``schemas/ic5_cloud_to_edge_frame.v1.0.0.schema.json``, ``ic5_edge_to_cloud_frame`` and
``ic5_link_profile``; git tag ``GIT_TAG``. Reads IC-3 (`DispatchEnvelope`, `ResolvedTimeline`,
`EnvelopeRef`, `Violation`), IC-1 (`ConnectionAgreement`, by `ConnectionAgreementRef`) and IC-13
(`GENESIS_PREV_HASH`), all 1.x.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Final, Literal, final

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    UtcInstant,
    content_hash,
    not_applicable,
)
from gridq.contracts.audit import GENESIS_PREV_HASH
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.curtailment import ConnectionAgreementRef
from gridq.contracts.dispatch_envelope import (
    RANK,
    RESOLUTION_MIN,
    Direction,
    DispatchEnvelope,
    EnvelopeRef,
    ResolvedTimeline,
    ScheduledPeriod,
    ServiceDelivery,
    TemperatureRange,
    ThermalTerms,
    Violation,
    ViolationCode,
    flat_limits,
    timeline_matches,
    timeline_terms_at,
)

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DELIVERED",
    "GENESIS_PREV_HASH",
    "GIT_TAG",
    "NEURA_OPS_RELEASABLE",
    "SUPPORTED_VERSIONS",
    "TERMINAL_ACK_STATES",
    "TRIGGERS",
    "AckAborted",
    "AckAccepted",
    "AckActive",
    "AckChainBroken",
    "AckChainHead",
    "AckCompleted",
    "AckLapsed",
    "AckRejected",
    "AckShadowing",
    "AckSuperseded",
    "AnmEvent",
    "AnmLimited",
    "AnmRelease",
    "AnmResponseCheck",
    "AnmSetpoint",
    "AnmTrip",
    "AnmTripped",
    "AnmUnrestricted",
    "BessDispatch",
    "BessIdle",
    "ChainStatus",
    "ChainTracker",
    "ChainVerdict",
    "CloudHeartbeat",
    "CloudToEdgeFrame",
    "ControlStage",
    "EdgeHeartbeat",
    "EdgeToCloudFrame",
    "EnterFailSafe",
    "EnvelopeInstall",
    "EpisodeEntered",
    "EpisodeExited",
    "EpisodeSummary",
    "ExpectedOutcome",
    "FailSafeEvent",
    "FailSafeTarget",
    "FrameChainBroken",
    "GoverningSpan",
    "ImportCap",
    "LatencyBudget",
    "LatencyBudgets",
    "LatencyComplianceReport",
    "LatencySample",
    "LinkObservation",
    "LinkProfile",
    "LinkVerdict",
    "OperatorCommand",
    "PeerHead",
    "Release",
    "RejectReason",
    "ReleaseTrigger",
    "ScheduleAck",
    "ScheduleVerdict",
    "SeqRange",
    "SetpointPeriod",
    "SetpointSchedule",
    "TargetChanged",
    "TriggerCleared",
    "TriggerCode",
    "TriggerInstance",
    "TriggerRaised",
    "TriggerSpec",
    "admit_schedule",
    "allowed_next",
    "anm_state_after",
    "anm_triggers",
    "check_frame_size",
    "check_latency",
    "check_setpoint_schedule",
    "deliver_by",
    "edge_silent",
    "effective_import_cap",
    "fail_safe_target",
    "governing_timeline",
    "latency_ms",
    "link_state",
    "nearest_rank",
    "primary_trigger",
    "to_scheduled_periods",
    "verify_ack_chain",
    "verify_anm_response",
    "verify_episode",
    "verify_frame_chain",
]

CONTRACT_ID: Final = "IC-5"
CONTRACT_VERSION: Final = "1.0.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0",)
GIT_TAG: Final = "contracts/ic5-edge-cloud/v1.0.0"

_HALF_HOUR: Final = timedelta(minutes=RESOLUTION_MIN)
_MS: Final = timedelta(milliseconds=1)
_US: Final = timedelta(microseconds=1)

Kw = Annotated[int, Field(ge=0)]
PosKw = Annotated[int, Field(ge=1)]
Kwh = Annotated[int, Field(ge=0)]
#: A duration or a monotonic reading, integer milliseconds.
Ms = Annotated[int, Field(ge=0)]
PosMs = Annotated[int, Field(ge=1)]
Seq = Annotated[int, Field(ge=0)]
_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def _ms(delta: timedelta) -> int:
    """Whole milliseconds in ``delta``, rounded toward minus infinity."""
    return delta // _MS


# --------------------------------------------------------------------------------------------
# The link profile: every number, required (D116, D120)
# --------------------------------------------------------------------------------------------

LatencyPath = Literal["cloud_to_edge_schedule", "edge_to_cloud_ack", "heartbeat"]


class LatencyBudget(ContractModel):
    """At least ``quantile_per_mille``/1000 of a path's deliveries take at most ``bound_ms``,
    judged over at least ``min_samples`` deliveries (nearest-rank quantile, `check_latency`).

    The path set has no ANM member: the ANM path never crosses the cloud link."""

    kind: Literal["latency_budget"]
    path: LatencyPath
    quantile_per_mille: Annotated[int, Field(ge=1, le=1000)]
    bound_ms: PosMs
    min_samples: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _enough_samples(self) -> LatencyBudget:
        q = self.quantile_per_mille
        if q < 1000 and self.min_samples * (1000 - q) < 1000:
            raise ValueError(
                f"a p{q / 10:g} needs min_samples of at least {-(-1000 // (1000 - q))}: with "
                "fewer, the quantile is the slowest delivery and the budget means something else"
            )
        return self


class LatencyBudgets(ContractModel):
    #: Required: the spec §10A cloud-to-edge schedule budget is the one tested contract field.
    cloud_to_edge_schedule: LatencyBudget
    edge_to_cloud_ack: LatencyBudget | NotApplicable = Field(discriminator="kind")
    heartbeat: LatencyBudget | NotApplicable = Field(discriminator="kind")

    @model_validator(mode="after")
    def _paths(self) -> LatencyBudgets:
        for name in ("cloud_to_edge_schedule", "edge_to_cloud_ack", "heartbeat"):
            b = getattr(self, name)
            if isinstance(b, LatencyBudget) and b.path != name:
                raise ValueError(f"latency_budgets.{name} must be the budget for path {name}")
        return self

    def bounds(self) -> tuple[LatencyBudget, ...]:
        return tuple(
            b
            for b in (self.cloud_to_edge_schedule, self.edge_to_cloud_ack, self.heartbeat)
            if isinstance(b, LatencyBudget)
        )


@final
class LinkProfile(ContractModel):
    """IC-5 v1.0.0: one site's link parameters. Bound by hash: every frame names it
    (``profile_hash``) and both ends refuse a frame sent under another profile.

    Every number is required with no default (no published source sets them). Equality at a
    threshold resolves to fail-safe (`link_state`)."""

    contract: Literal["IC-5"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["link_profile"]

    profile_id: _ID
    site_id: NonEmptyStr
    record_class: Literal["live", "synthetic_fixture"]
    #: The IC-1 agreement whose ANM terms the edge enforces locally.
    connection_agreement: ConnectionAgreementRef
    #: Who acts on a DNO ANM signal. One value: the edge, locally (spec §10A, B1-03).
    anm_authority: Literal["edge_local"]

    cloud_heartbeat_interval_ms: PosMs
    edge_heartbeat_interval_ms: PosMs
    #: The edge's control lease: renewed by every valid cloud-to-edge frame; at expiry, fail-safe.
    control_lease_ms: PosMs
    #: A schedule older than this (measured from receipt, edge monotonic clock) is stale.
    schedule_max_age_ms: PosMs
    #: The held schedule must cover more than this far ahead, else fail-safe.
    schedule_required_lookahead_ms: PosMs
    #: How often the cloud re-issues a schedule.
    mpc_cadence_ms: PosMs
    #: The cloud declares the edge silent after this long without a valid frame from it.
    edge_silent_after_ms: PosMs
    #: After the lease returns, the edge stays in fail-safe at least this long (no flapping).
    resume_hold_ms: Ms
    #: The bound on the offset between the two clocks that latency is judged under.
    max_clock_skew_ms: Ms
    max_frame_bytes: Annotated[int, Field(ge=1)]
    latency_budgets: LatencyBudgets

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("LinkProfile is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> LinkProfile:
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")
        if not self.cloud_heartbeat_interval_ms < self.control_lease_ms:
            raise ValueError("the cloud heartbeat must come more often than the lease expires")
        if not self.control_lease_ms <= self.schedule_max_age_ms:
            raise ValueError("control_lease_ms must not exceed schedule_max_age_ms")
        if not self.mpc_cadence_ms < self.schedule_max_age_ms:
            raise ValueError(
                "mpc_cadence_ms must be below schedule_max_age_ms, or every schedule goes stale "
                "before the next arrives"
            )
        if not self.schedule_required_lookahead_ms >= self.mpc_cadence_ms:
            raise ValueError("schedule_required_lookahead_ms must cover one MPC cadence")
        if not self.edge_heartbeat_interval_ms < self.edge_silent_after_ms:
            raise ValueError("the edge heartbeat must come more often than the silence threshold")
        for b in self.latency_budgets.bounds():
            if not self.max_clock_skew_ms < b.bound_ms:
                raise ValueError(
                    f"max_clock_skew_ms must be below every latency bound ({b.path}: "
                    f"{b.bound_ms} ms), or no delivery could be judged"
                )
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def deliver_by(sent_at: datetime, profile: LinkProfile) -> datetime:
    """When a schedule sent at ``sent_at`` is due at the edge: ``sent_at`` plus the bound."""
    return sent_at + profile.latency_budgets.cloud_to_edge_schedule.bound_ms * _MS


# --------------------------------------------------------------------------------------------
# Fail-safe trigger codes (D118)
# --------------------------------------------------------------------------------------------


class TriggerCode(StrEnum):
    """Why the edge is in fail-safe. Enum order breaks ties of rank (`primary_trigger`)."""

    HOST_OVERRIDE = "host_override"
    OPERATOR_COMMAND = "operator_command"
    CONTROL_AUTHORITY_WITHDRAWN = "control_authority_withdrawn"
    NO_ENVELOPE_IN_FORCE = "no_envelope_in_force"
    NO_ENVELOPE_HELD = "no_envelope_held"
    CLOCK_UNSYNCHRONISED = "clock_unsynchronised"
    CHAIN_INTEGRITY_FAILURE = "chain_integrity_failure"
    THERMAL_COMMS_LOST = "thermal_comms_lost"
    DNO_TRIP = "dno_trip"
    DNO_SIGNAL_LOST = "dno_signal_lost"
    SITE_METER_LOST = "site_meter_lost"
    POWER_DEVICE_COMMS_LOST = "power_device_comms_lost"
    CLOUD_LINK_LOST = "cloud_link_lost"
    MARKET_LINK_LOST = "market_link_lost"
    SCHEDULE_STALE = "schedule_stale"
    SCHEDULE_EXHAUSTED = "schedule_exhausted"
    EDGE_RESTART = "edge_restart"


ReleaseKind = Literal["automatic", "host_release", "neura_ops_release", "new_grant"]


class TriggerSpec(ContractModel):
    """A trigger's place: its category, its rank in the spec §8 hierarchy (0 most severe), who
    detects it and what clears it. A trigger that is not ``automatic`` stays raised until an
    audited release (IC-13 command id and audit event hash) clears it."""

    category: Literal[
        "authority", "envelope", "integrity", "observability", "connection", "link", "schedule"
    ]
    rank: Annotated[int, Field(ge=0, le=5)]
    detector: Literal["edge_local", "external_input", "either"]
    release: ReleaseKind


def _spec(category: str, rank: int, detector: str, release: str) -> TriggerSpec:
    return TriggerSpec.model_validate(
        {"category": category, "rank": rank, "detector": detector, "release": release},
        strict=False,
    )


_C = TriggerCode
#: The frozen table. Rank 0: no safe authority to act; 1: thermal limits; 2: connection
#: compliance; 4: the link and the schedule (contracted delivery can no longer be assured).
TRIGGERS: Final[Mapping[TriggerCode, TriggerSpec]] = {
    _C.HOST_OVERRIDE: _spec("authority", 0, "edge_local", "host_release"),
    _C.OPERATOR_COMMAND: _spec("authority", 0, "external_input", "neura_ops_release"),
    _C.CONTROL_AUTHORITY_WITHDRAWN: _spec("authority", 0, "edge_local", "new_grant"),
    _C.NO_ENVELOPE_IN_FORCE: _spec("envelope", 0, "edge_local", "automatic"),
    _C.NO_ENVELOPE_HELD: _spec("envelope", 0, "edge_local", "automatic"),
    _C.CLOCK_UNSYNCHRONISED: _spec("integrity", 0, "edge_local", "automatic"),
    _C.CHAIN_INTEGRITY_FAILURE: _spec("integrity", 0, "edge_local", "neura_ops_release"),
    _C.THERMAL_COMMS_LOST: _spec("observability", 1, "edge_local", "automatic"),
    _C.DNO_TRIP: _spec("connection", 2, "edge_local", "automatic"),
    _C.DNO_SIGNAL_LOST: _spec("connection", 2, "edge_local", "automatic"),
    _C.SITE_METER_LOST: _spec("observability", 2, "edge_local", "automatic"),
    _C.POWER_DEVICE_COMMS_LOST: _spec("observability", 2, "edge_local", "automatic"),
    _C.CLOUD_LINK_LOST: _spec("link", 4, "edge_local", "automatic"),
    _C.MARKET_LINK_LOST: _spec("link", 4, "either", "automatic"),
    _C.SCHEDULE_STALE: _spec("schedule", 4, "edge_local", "automatic"),
    _C.SCHEDULE_EXHAUSTED: _spec("schedule", 4, "edge_local", "automatic"),
    _C.EDGE_RESTART: _spec("schedule", 4, "edge_local", "automatic"),
}
#: The codes a Neura ops `OperatorCommand` may release (and only these).
NEURA_OPS_RELEASABLE: Final = frozenset(
    c for c, s in TRIGGERS.items() if s.release == "neura_ops_release"
)
_TRIGGER_ORDER: Final = {c: i for i, c in enumerate(TriggerCode)}


def _sorted_codes(codes: Sequence[TriggerCode]) -> bool:
    keys = [_TRIGGER_ORDER[c] for c in codes]
    return keys == sorted(set(keys))


def primary_trigger(codes: Sequence[TriggerCode]) -> TriggerCode:
    """The most severe active trigger: lowest rank, ties broken by enum order."""
    if not codes:
        raise ValueError("no trigger is active")
    return min(codes, key=lambda c: (TRIGGERS[c].rank, _TRIGGER_ORDER[c]))


# --------------------------------------------------------------------------------------------
# ANM, local only (D119)
# --------------------------------------------------------------------------------------------


class AnmSetpoint(ContractModel):
    kind: Literal["anm_setpoint"]
    import_limit_kw: Kw


class AnmTrip(ContractModel):
    kind: Literal["anm_trip"]


class AnmRelease(ContractModel):
    kind: Literal["anm_release"]


class AnmUnrestricted(ContractModel):
    kind: Literal["unrestricted"]


class AnmLimited(ContractModel):
    kind: Literal["limited"]
    import_limit_kw: Kw


class AnmTripped(ContractModel):
    kind: Literal["tripped"]


AnmState = Annotated[
    AnmUnrestricted | AnmLimited | AnmTripped | Unknown, Field(discriminator="kind")
]
AnmSignal = Annotated[AnmSetpoint | AnmTrip | AnmRelease, Field(discriminator="kind")]


class AnmEvent(ContractModel):
    """One DNO ANM signal, as the edge received and enforced it: edge to cloud only, a hash chain
    of its own (``prev_event_hash``), sent whenever the link allows and carrying its original
    times (store and forward, B1-03 "log with timestamps")."""

    kind: Literal["anm_event"]
    site_id: NonEmptyStr
    event_id: _ID
    seq: Seq
    prev_event_hash: Sha256
    signal: AnmSignal
    received_at: UtcInstant
    enforced_at: UtcInstant | Unknown
    #: Monotonic milliseconds from receipt to enforcement, as the edge measured it.
    response_elapsed_ms: Ms | Unknown
    dno_acknowledged_at: UtcInstant | NotApplicable | Unknown
    #: The IC-1 agreement the signal was enforced under (its response time is the requirement).
    agreement_hash: Sha256
    path: Literal["edge_local"]
    cloud_link: Literal["up", "down"]

    @model_validator(mode="after")
    def _consistent(self) -> AnmEvent:
        if (self.seq == 0) != (self.prev_event_hash == GENESIS_PREV_HASH):
            raise ValueError("prev_event_hash is the genesis value exactly when seq == 0")
        if isinstance(self.enforced_at, datetime) and self.enforced_at < self.received_at:
            raise ValueError("enforced_at must not be before received_at")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def anm_state_after(
    signal: AnmSetpoint | AnmTrip | AnmRelease,
) -> AnmUnrestricted | AnmLimited | AnmTripped:
    """The ANM state a signal puts the edge in. Only a local signal changes it."""
    if isinstance(signal, AnmSetpoint):
        return AnmLimited(kind="limited", import_limit_kw=signal.import_limit_kw)
    if isinstance(signal, AnmTrip):
        return AnmTripped(kind="tripped")
    return AnmUnrestricted(kind="unrestricted")


def anm_triggers(
    state: AnmUnrestricted | AnmLimited | AnmTripped | Unknown,
) -> tuple[TriggerCode, ...]:
    """A trip and a lost signal are fail-safe triggers; an ANM setpoint is not (it is enforced
    as a cap in normal operation)."""
    if isinstance(state, AnmTripped):
        return (TriggerCode.DNO_TRIP,)
    if isinstance(state, Unknown):
        return (TriggerCode.DNO_SIGNAL_LOST,)
    return ()


class AnmResponseCheck(ContractModel):
    status: Literal["within_required_time", "late", "unverifiable"]
    detail: NonEmptyStr


def verify_anm_response(event: AnmEvent, agreement: ConnectionAgreement) -> AnmResponseCheck:
    """The edge's measured response against the IC-1 agreement's required ANM response time,
    exactly (the agreement's seconds read as their decimal text). An unknown time on either side
    is ``unverifiable``: reported as a finding, never refused here."""
    if agreement.content_hash() != event.agreement_hash or agreement.site_id != event.site_id:
        raise ValueError("the event was enforced under another agreement or site")
    required = agreement.anm_response_time_s
    if not isinstance(required, float):
        return AnmResponseCheck(
            status="unverifiable",
            detail=f"the agreement states no response time: {required.reason}",
        )
    elapsed = event.response_elapsed_ms
    if isinstance(elapsed, Unknown):
        return AnmResponseCheck(
            status="unverifiable", detail=f"the response time was not measured: {elapsed.reason}"
        )
    limit = Fraction(repr(required))
    if Fraction(elapsed, 1000) <= limit:
        return AnmResponseCheck(
            status="within_required_time", detail=f"{elapsed} ms within {required} s"
        )
    return AnmResponseCheck(status="late", detail=f"{elapsed} ms exceeds {required} s")


CapBinding = Literal["anm_trip", "anm_setpoint", "fail_safe_firm", "envelope", "schedule", "none"]


class ImportCap(ContractModel):
    """The import cap the edge enforces and the term that binds it."""

    kw: Kw | NotApplicable
    binding: CapBinding

    @model_validator(mode="after")
    def _consistent(self) -> ImportCap:
        if isinstance(self.kw, NotApplicable) != (self.binding == "none"):
            raise ValueError("a cap is not applicable exactly when nothing binds it")
        return self


def effective_import_cap(
    *,
    schedule_kw: int | NotApplicable,
    envelope_limit_kw: int | NotApplicable,
    fail_safe_firm_kw: int | NotApplicable,
    anm_state: AnmUnrestricted | AnmLimited | AnmTripped | Unknown,
    in_fail_safe: bool,
) -> ImportCap:
    """The tightest of what applies: in normal operation the schedule's cap and the envelope's
    limit, in fail-safe the firm limit; always ANM's limit, and a trip suspends import (0 kW).
    Nothing from the cloud can raise it above ANM: only a local release or a higher local
    setpoint loosens ANM. Ties name the more authoritative term."""
    candidates: list[tuple[int, int, CapBinding]] = []
    if isinstance(anm_state, AnmTripped):
        candidates.append((0, 0, "anm_trip"))
    elif isinstance(anm_state, AnmLimited):
        candidates.append((anm_state.import_limit_kw, 1, "anm_setpoint"))
    if in_fail_safe:
        if isinstance(fail_safe_firm_kw, int):
            candidates.append((fail_safe_firm_kw, 2, "fail_safe_firm"))
    else:
        if isinstance(envelope_limit_kw, int):
            candidates.append((envelope_limit_kw, 3, "envelope"))
        if isinstance(schedule_kw, int):
            candidates.append((schedule_kw, 4, "schedule"))
    if not candidates:
        return ImportCap(kw=not_applicable("nothing caps import: no envelope held"), binding="none")
    kw, _, binding = min(candidates)
    return ImportCap(kw=kw, binding=binding)


# --------------------------------------------------------------------------------------------
# The setpoint schedule (D115)
# --------------------------------------------------------------------------------------------


class ControlStage(StrEnum):
    """ADR-D2's staged control authority granted at a site (L4 is advisory, not edge control)."""

    L0_READ_ONLY = "l0_read_only"
    L1_SHADOW = "l1_shadow"
    L2_BESS_CLOSED_LOOP = "l2_bess_closed_loop"
    L3_CHILLER_BAND = "l3_chiller_band"


_STAGE: Final = {s: i for i, s in enumerate(ControlStage)}


class BessIdle(ContractModel):
    kind: Literal["idle"]


class BessDispatch(ContractModel):
    """One direction at ``kw``: the merchant share plus each service's delivery, exactly."""

    kind: Literal["dispatch"]
    direction: Direction
    kw: PosKw
    merchant_kw: Kw
    service_delivery: tuple[ServiceDelivery, ...]

    @model_validator(mode="after")
    def _closed(self) -> BessDispatch:
        ids = [d.service_id for d in self.service_delivery]
        if ids != sorted(set(ids)):
            raise ValueError("service_delivery must be sorted by service_id, unique")
        if any(d.direction is not self.direction for d in self.service_delivery):
            raise ValueError("every service delivery is in the command's direction")
        if self.merchant_kw + sum(d.kw for d in self.service_delivery) != self.kw:
            raise ValueError("merchant_kw plus the service deliveries must equal kw exactly")
        return self


class ExpectedOutcome(ContractModel):
    """What the cloud expects at the end of the period (for the envelope check and telemetry
    reconciliation); the edge does not actuate these."""

    soe_end_kwh: Kwh
    site_import_kw: Kw
    site_export_kw: Kw
    temperature: TemperatureRange | NotApplicable


class SetpointPeriod(ContractModel):
    """One period of setpoints, inside one half-hour of the grid, on whole minutes."""

    start: UtcInstant
    end: UtcInstant
    bess: BessIdle | BessDispatch = Field(discriminator="kind")
    #: The site import cap the edge enforces for the period (the cloud may only tighten).
    site_import_cap_kw: Kw
    #: The chiller supply band commanded (L3 only), inside the cell's band.
    chiller_band: TemperatureRange | NotApplicable
    expected: ExpectedOutcome

    @model_validator(mode="after")
    def _shape(self) -> SetpointPeriod:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        for t in (self.start, self.end):
            if t.second or t.microsecond:
                raise ValueError("a period starts and ends on whole minutes")
        first = self.start.replace(minute=self.start.minute - self.start.minute % RESOLUTION_MIN)
        if self.end - first > _HALF_HOUR:
            raise ValueError("a period lies inside one half-hour of the grid")
        if self.expected.site_import_kw > self.site_import_cap_kw:
            raise ValueError("expected.site_import_kw exceeds the period's own import cap")
        return self


class SetpointSchedule(ContractModel):
    """A schedule for one site, executed inside one IC-3 envelope (checked against its compiled
    timeline, trusted by ``resolved_hash``). It names what it supersedes by content hash."""

    kind: Literal["setpoint_schedule"]
    schedule_id: _ID
    site_id: NonEmptyStr
    issued_at: UtcInstant
    #: The frame's ``sent_at`` plus the profile's schedule latency bound (`deliver_by`).
    deliver_by: UtcInstant
    envelope: EnvelopeRef
    resolved_hash: Sha256
    execution: Literal["shadow", "closed_loop"]
    supersedes: Sha256 | NotApplicable
    #: Informational: the hash of the last ANM event the cloud had seen when it planned.
    planned_against_anm: Sha256 | NotApplicable
    soe_start_kwh: Kwh
    periods: tuple[SetpointPeriod, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self) -> SetpointSchedule:
        for a, b in zip(self.periods, self.periods[1:], strict=False):
            if a.end != b.start:
                raise ValueError("periods must be sorted and contiguous")
        if self.deliver_by <= self.issued_at:
            raise ValueError("deliver_by must be after issued_at")
        return self

    @property
    def covered_until(self) -> datetime:
        return self.periods[-1].end

    def content_hash(self) -> str:
        return content_hash(self)


def to_scheduled_periods(schedule: SetpointSchedule) -> tuple[ScheduledPeriod, ...]:
    """The IC-3 view of a setpoint schedule: the import cap as the site's import, the expected
    export, state of energy chained from ``soe_start_kwh``, and the expected temperature."""
    out: list[ScheduledPeriod] = []
    soe = schedule.soe_start_kwh
    for p in schedule.periods:
        b = p.bess
        discharge = charge = 0
        services: tuple[ServiceDelivery, ...] = ()
        if isinstance(b, BessDispatch):
            services = b.service_delivery
            if b.direction is Direction.DISCHARGE:
                discharge = b.merchant_kw
            else:
                charge = b.merchant_kw
        out.append(
            ScheduledPeriod(
                start=p.start,
                end=p.end,
                merchant_discharge_kw=discharge,
                merchant_charge_kw=charge,
                service_delivery=services,
                site_import_kw=p.site_import_cap_kw,
                site_export_kw=p.expected.site_export_kw,
                soe_start_kwh=soe,
                soe_end_kwh=p.expected.soe_end_kwh,
                temperature=p.expected.temperature,
            )
        )
        soe = p.expected.soe_end_kwh
    return tuple(out)


_CODE_ORDER: Final = {c: i for i, c in enumerate(ViolationCode)}


def _timeline_violations(
    timeline: ResolvedTimeline, p: ScheduledPeriod, chiller: TemperatureRange | NotApplicable
) -> list[tuple[ViolationCode, str]]:
    """IC-3's per-period rule (`check_schedule`), read from the compiled timeline instead of the
    envelope, plus the commanded chiller band inside the cell's band."""
    if not (timeline.valid_from <= p.start and p.end <= timeline.valid_until):
        return [(ViolationCode.OUTSIDE_VALIDITY, "the period is outside the envelope's validity")]
    t = timeline_terms_at(timeline, p.start)
    out: list[tuple[ViolationCode, str]] = []
    if isinstance(t.thermal, ThermalTerms):
        band = t.thermal.band
        for what, temp in (("expected", p.temperature), ("chiller_band", chiller)):
            if isinstance(temp, NotApplicable):
                if what == "expected":
                    out.append((ViolationCode.THERMAL_NOT_STATED, "the cell has a thermal band"))
            elif (
                temp.point != band.point.value
                or temp.low_tenths_c < band.min_tenths_c
                or temp.high_tenths_c > band.max_tenths_c
            ):
                out.append(
                    (
                        ViolationCode.THERMAL_BAND,
                        f"{what} outside {band.min_tenths_c}-{band.max_tenths_c}",
                    )
                )
    elif not isinstance(chiller, NotApplicable):
        out.append((ViolationCode.THERMAL_BAND, "chiller_band where the cell has no thermal band"))
    if p.site_import_kw > t.import_limit_kw:
        out.append((ViolationCode.IMPORT_LIMIT, f"{p.site_import_kw} > {t.import_limit_kw} kW"))
    if p.site_export_kw > t.export_limit_kw:
        out.append((ViolationCode.EXPORT_LIMIT, f"{p.site_export_kw} > {t.export_limit_kw} kW"))
    limits = flat_limits(t)
    low, high = min(p.soe_start_kwh, p.soe_end_kwh), max(p.soe_start_kwh, p.soe_end_kwh)
    if low < t.energy.resilience_floor_kwh:
        out.append(
            (
                ViolationCode.SOE_BELOW_RESILIENCE_FLOOR,
                f"{low} < {t.energy.resilience_floor_kwh} kWh",
            )
        )
    elif low < limits.soe_floor_kwh:
        out.append((ViolationCode.SOE_BELOW_SERVICE_FLOOR, f"{low} < {limits.soe_floor_kwh} kWh"))
    if high > limits.soe_ceiling_kwh:
        out.append((ViolationCode.SOE_ABOVE_CEILING, f"{high} > {limits.soe_ceiling_kwh} kWh"))
    for d in p.service_delivery:
        part = t.discharge if d.direction is Direction.DISCHARGE else t.charge
        allowed = part.slice_kw(d.service_id)
        if d.kw > allowed:
            out.append(
                (
                    ViolationCode.SERVICE_OVER_SLICE,
                    f"{d.service_id} {d.direction.value} {d.kw} > {allowed} kW",
                )
            )
    if p.merchant_discharge_kw > t.discharge.merchant_kw:
        out.append(
            (
                ViolationCode.MERCHANT_OVER_SLICE,
                f"discharge {p.merchant_discharge_kw} > {t.discharge.merchant_kw} kW",
            )
        )
    if p.merchant_charge_kw > t.charge.merchant_kw:
        out.append(
            (
                ViolationCode.MERCHANT_OVER_SLICE,
                f"charge {p.merchant_charge_kw} > {t.charge.merchant_kw} kW",
            )
        )
    return out


class RejectReason(StrEnum):
    """Why the edge refused a schedule. Several may hold; an ack lists them sorted."""

    WRONG_SITE = "wrong_site"
    NOT_AUTHORISED = "not_authorised"
    AUTHORITY_NOT_GRANTED = "authority_not_granted"
    OUTSIDE_WRITE_SCOPE = "outside_write_scope"
    ID_REUSED = "id_reused"
    OUT_OF_ORDER = "out_of_order"
    SUPERSEDES_MISMATCH = "supersedes_mismatch"
    STALE_ON_ARRIVAL = "stale_on_arrival"
    HORIZON_ELAPSED = "horizon_elapsed"
    DELIVER_BY_MISMATCH = "deliver_by_mismatch"
    ENVELOPE_UNKNOWN = "envelope_unknown"
    ENVELOPE_VIOLATION = "envelope_violation"


def _sorted_reasons(reasons: Sequence[RejectReason]) -> tuple[RejectReason, ...]:
    return tuple(sorted(set(reasons)))


class ScheduleVerdict(ContractModel):
    """The edge's verdict on a schedule: accepted only with no reason; any envelope violation
    rejects the whole schedule. ``late`` is reported, never a reason to refuse."""

    accepted: bool
    reasons: tuple[RejectReason, ...]
    violations: tuple[Violation, ...]
    late: bool

    @model_validator(mode="after")
    def _consistent(self) -> ScheduleVerdict:
        if self.accepted == bool(self.reasons):
            raise ValueError("accepted exactly when there is no reason to reject")
        if bool(self.violations) != (RejectReason.ENVELOPE_VIOLATION in self.reasons):
            raise ValueError("envelope_violation is a reason exactly when violations are listed")
        if list(self.reasons) != sorted(set(self.reasons)):
            raise ValueError("reasons must be sorted and unique")
        return self


def check_setpoint_schedule(
    timeline: ResolvedTimeline, schedule: SetpointSchedule, stage: ControlStage
) -> ScheduleVerdict:
    """The schedule against the compiled IC-3 timeline it names and the authority granted.

    Refused: a timeline other than the one named (``envelope_unknown``), another site, any
    schedule at L0, a closed-loop schedule below L2, a commanded chiller band below L3 (closed
    loop), and any envelope violation in any period (IC-3's rule, `check_schedule`, read from the
    timeline)."""
    reasons: list[RejectReason] = []
    if timeline.content_hash() != schedule.resolved_hash:
        reasons.append(RejectReason.ENVELOPE_UNKNOWN)
    if timeline.site_id != schedule.site_id:
        reasons.append(RejectReason.WRONG_SITE)
    level = _STAGE[stage]
    closed = schedule.execution == "closed_loop"
    chiller = any(not isinstance(p.chiller_band, NotApplicable) for p in schedule.periods)
    if (
        level == 0
        or (closed and level < _STAGE[ControlStage.L2_BESS_CLOSED_LOOP])
        or (closed and chiller and level < _STAGE[ControlStage.L3_CHILLER_BAND])
    ):
        reasons.append(RejectReason.AUTHORITY_NOT_GRANTED)
    found: list[Violation] = []
    if RejectReason.ENVELOPE_UNKNOWN not in reasons:
        for i, (p, sp) in enumerate(
            zip(schedule.periods, to_scheduled_periods(schedule), strict=True)
        ):
            for code, detail in _timeline_violations(timeline, sp, p.chiller_band):
                found.append(Violation(period=i, code=code, rank=RANK[code], detail=detail))
    found.sort(key=lambda v: (v.rank, v.period, _CODE_ORDER[v.code], v.detail))
    if found:
        reasons.append(RejectReason.ENVELOPE_VIOLATION)
    return ScheduleVerdict(
        accepted=not reasons, reasons=_sorted_reasons(reasons), violations=tuple(found), late=False
    )


# --------------------------------------------------------------------------------------------
# Cloud-to-edge bodies
# --------------------------------------------------------------------------------------------


class EnvelopeInstall(ContractModel):
    """An IC-3 envelope and its compiled timeline, delivered to the edge to hold. The timeline
    must be the envelope's compilation (by hash), so the edge needs no clock logic."""

    kind: Literal["envelope_install"]
    envelope: DispatchEnvelope
    timeline: ResolvedTimeline

    @model_validator(mode="after")
    def _matches(self) -> EnvelopeInstall:
        if not timeline_matches(self.envelope, self.timeline):
            raise ValueError("the timeline is not the envelope's compilation (resolved_hash)")
        return self


class EnterFailSafe(ContractModel):
    kind: Literal["enter_fail_safe"]
    reason: NonEmptyStr


class ReleaseTrigger(ContractModel):
    kind: Literal["release_trigger"]
    code: TriggerCode

    @model_validator(mode="after")
    def _releasable(self) -> ReleaseTrigger:
        if self.code not in NEURA_OPS_RELEASABLE:
            raise ValueError(
                f"{self.code.value} is not released by Neura ops from the cloud "
                f"({TRIGGERS[self.code].release})"
            )
        return self


class OperatorCommand(ContractModel):
    """A Neura ops command to the edge, audited under IC-13 (the command id and the hash of the
    audit event that allowed it)."""

    kind: Literal["operator_command"]
    command_id: _ID
    issued_at: UtcInstant
    action: EnterFailSafe | ReleaseTrigger = Field(discriminator="kind")
    operator: NonEmptyStr
    audit_event_hash: Sha256


class CloudHeartbeat(ContractModel):
    """The cloud is alive (renews the edge's lease) and says whether the market link is up."""

    kind: Literal["cloud_heartbeat"]
    market_link: Literal["up", "down"]


CloudToEdgeBody = Annotated[
    SetpointSchedule | EnvelopeInstall | OperatorCommand | CloudHeartbeat,
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------------------------
# Acknowledgements (D117)
# --------------------------------------------------------------------------------------------


class AckAccepted(ContractModel):
    kind: Literal["accepted"]
    mode: Literal["closed_loop", "shadow"]


class AckRejected(ContractModel):
    kind: Literal["rejected"]
    reasons: tuple[RejectReason, ...] = Field(min_length=1)
    violations: tuple[Violation, ...]
    detail: NonEmptyStr

    @model_validator(mode="after")
    def _consistent(self) -> AckRejected:
        if list(self.reasons) != sorted(set(self.reasons)):
            raise ValueError("reasons must be sorted and unique")
        if bool(self.violations) != (RejectReason.ENVELOPE_VIOLATION in self.reasons):
            raise ValueError("envelope_violation is a reason exactly when violations are listed")
        return self


class AckActive(ContractModel):
    kind: Literal["active"]


class AckShadowing(ContractModel):
    kind: Literal["shadowing"]


class AckSuperseded(ContractModel):
    kind: Literal["superseded"]
    by_schedule_hash: Sha256


class AckCompleted(ContractModel):
    kind: Literal["completed"]


class AckLapsed(ContractModel):
    kind: Literal["lapsed"]


class AckAborted(ContractModel):
    kind: Literal["aborted"]
    trigger_code: TriggerCode


AckState = Annotated[
    AckAccepted
    | AckRejected
    | AckActive
    | AckShadowing
    | AckSuperseded
    | AckCompleted
    | AckLapsed
    | AckAborted,
    Field(discriminator="kind"),
]

_NEXT: Final[Mapping[str, frozenset[str]]] = {
    "start": frozenset({"accepted", "rejected"}),
    "accepted": frozenset({"active", "shadowing", "superseded", "lapsed"}),
    "active": frozenset({"superseded", "completed", "aborted"}),
    "shadowing": frozenset({"superseded", "completed", "aborted"}),
}
TERMINAL_ACK_STATES: Final = frozenset({"rejected", "superseded", "completed", "lapsed", "aborted"})


def allowed_next(state: str) -> frozenset[str]:
    """The states an acknowledgement chain may move to from ``state`` (``"start"`` before any)."""
    return _NEXT.get(state, frozenset())


class ScheduleAck(ContractModel):
    """One step of a schedule's life at the edge. The acks of one schedule are a hash chain
    (``seq``, ``prev_ack_hash``); a redelivered identical schedule re-emits the latest ack."""

    kind: Literal["schedule_ack"]
    site_id: NonEmptyStr
    edge_id: _ID
    schedule_id: _ID
    schedule_hash: Sha256
    seq: Seq
    prev_ack_hash: Sha256 | NotApplicable
    #: When the edge received the schedule (repeated on every ack of the chain).
    received_at: UtcInstant
    at: UtcInstant
    #: ``received_at`` strictly after the schedule's ``deliver_by``. Flagged, never refused.
    late: bool
    clock_offset_bound_ms: Ms | Unknown
    envelope: EnvelopeRef | NotApplicable
    state: AckState

    @model_validator(mode="after")
    def _consistent(self) -> ScheduleAck:
        if (self.seq == 0) != isinstance(self.prev_ack_hash, NotApplicable):
            raise ValueError("prev_ack_hash is not applicable exactly when seq == 0")
        if (self.seq == 0) != (self.state.kind in ("accepted", "rejected")):
            raise ValueError("the first ack accepts or rejects, and only the first")
        if self.at < self.received_at:
            raise ValueError("an ack cannot precede the schedule's receipt")
        if self.state.kind != "rejected" and isinstance(self.envelope, NotApplicable):
            raise ValueError("an ack of an accepted schedule names the envelope it ran under")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


class AckChainHead(ContractModel):
    seq: Seq
    ack_hash: Sha256
    state: NonEmptyStr


class AckChainBroken(ValueError):
    def __init__(self, seq: int, detail: str) -> None:
        super().__init__(f"ack chain broken at seq {seq}: {detail}")
        self.seq = seq


def verify_ack_chain(schedule: SetpointSchedule, acks: Sequence[ScheduleAck]) -> AckChainHead:
    """Check a schedule's whole ack chain: links, transitions, times and the late flag."""
    if not acks:
        raise ValueError("no acks to verify")
    h = schedule.content_hash()
    state, prev, last_at = "start", None, None
    first_start, last_end = schedule.periods[0].start, schedule.covered_until
    for i, a in enumerate(acks):
        if a.seq != i:
            raise AckChainBroken(a.seq, f"expected seq {i}")
        if (a.schedule_hash, a.schedule_id, a.site_id) != (
            h,
            schedule.schedule_id,
            schedule.site_id,
        ):
            raise AckChainBroken(a.seq, "the ack is for another schedule")
        if prev is not None and a.prev_ack_hash != prev:
            raise AckChainBroken(a.seq, "prev_ack_hash does not match the previous ack's hash")
        if a.received_at != acks[0].received_at or a.edge_id != acks[0].edge_id:
            raise AckChainBroken(a.seq, "received_at and edge_id are fixed for a chain")
        if last_at is not None and a.at < last_at:
            raise AckChainBroken(a.seq, "at goes backwards")
        if a.late != (a.received_at > schedule.deliver_by):
            raise AckChainBroken(a.seq, "late must be received_at > deliver_by, exactly")
        kind = a.state.kind
        if kind not in allowed_next(state):
            raise AckChainBroken(a.seq, f"{state} cannot move to {kind}")
        if isinstance(a.state, AckAccepted):
            want = "closed_loop" if schedule.execution == "closed_loop" else "shadow"
            if a.state.mode != want:
                raise AckChainBroken(a.seq, f"a {schedule.execution} schedule is accepted {want}")
            if a.envelope != schedule.envelope:
                raise AckChainBroken(a.seq, "accepted under an envelope the schedule does not name")
        if (kind == "active" and schedule.execution != "closed_loop") or (
            kind == "shadowing" and schedule.execution != "shadow"
        ):
            raise AckChainBroken(a.seq, f"a {schedule.execution} schedule cannot be {kind}")
        if kind in ("active", "shadowing") and a.at < first_start:
            raise AckChainBroken(a.seq, f"{kind} before the first period starts")
        if kind == "completed" and a.at < last_end:
            raise AckChainBroken(a.seq, "completed before the last period ends")
        state, prev, last_at = kind, a.content_hash(), a.at
    return AckChainHead(seq=len(acks) - 1, ack_hash=prev or "", state=state)


class GoverningSpan(ContractModel):
    """The interval a closed-loop schedule governed the site: from its ``active`` ack to its
    ``superseded``, ``completed`` or ``aborted`` ack, or still open."""

    schedule_hash: Sha256
    start: UtcInstant
    end: UtcInstant | NotApplicable
    ended_by: Literal["superseded", "completed", "aborted", "open"]


def governing_timeline(
    chains: Sequence[tuple[SetpointSchedule, Sequence[ScheduleAck]]],
) -> tuple[GoverningSpan, ...]:
    """Which schedule governed when, from one site's ack chains (each verified first).

    Refused (``ValueError``): two schedules governing at once, an open span before another, and a
    ``superseded`` whose successor did not go active at that same instant."""
    spans: list[GoverningSpan] = []
    active_at: dict[str, datetime] = {}
    supersessions: list[tuple[str, datetime]] = []
    for schedule, acks in chains:
        verify_ack_chain(schedule, acks)
        h = schedule.content_hash()
        start: datetime | None = None
        for a in acks:
            if isinstance(a.state, AckActive):
                start = active_at[h] = a.at
            elif start is not None and a.state.kind in ("superseded", "completed", "aborted"):
                if isinstance(a.state, AckSuperseded):
                    supersessions.append((a.state.by_schedule_hash, a.at))
                spans.append(
                    GoverningSpan(schedule_hash=h, start=start, end=a.at, ended_by=a.state.kind)
                )
                start = None
        if start is not None:
            spans.append(
                GoverningSpan(
                    schedule_hash=h,
                    start=start,
                    end=not_applicable("still governing"),
                    ended_by="open",
                )
            )
    for successor, at in supersessions:
        if active_at.get(successor) != at:
            raise ValueError(
                f"superseded by {successor} at {at.isoformat()}, but that schedule did not go "
                "active at that instant"
            )
    spans.sort(key=lambda s: (s.start, s.schedule_hash))
    for earlier, later in zip(spans, spans[1:], strict=False):
        if isinstance(earlier.end, NotApplicable) or earlier.end > later.start:
            raise ValueError(
                f"{earlier.schedule_hash} and {later.schedule_hash} govern at once from "
                f"{later.start.isoformat()}"
            )
    return tuple(spans)


def admit_schedule(
    frame: CloudToEdgeFrame,
    *,
    profile: LinkProfile,
    timeline: ResolvedTimeline | NotApplicable,
    stage: ControlStage,
    received_at: datetime,
    held: SetpointSchedule | NotApplicable,
    known_ids: Mapping[str, str],
) -> ScheduleVerdict:
    """The edge's whole verdict on a schedule frame: the transport and sequence checks, then
    `check_setpoint_schedule`. ``timeline`` is the held timeline whose hash the schedule names
    (not applicable if the edge holds none); ``held`` the last accepted schedule; ``known_ids``
    every schedule id seen since boot, with its hash. Lateness is reported, not refused."""
    s = frame.body
    if not isinstance(s, SetpointSchedule):
        raise ValueError("admit_schedule reads a setpoint_schedule frame")
    reasons: list[RejectReason] = []
    if s.site_id != profile.site_id:
        reasons.append(RejectReason.WRONG_SITE)
    h = s.content_hash()
    if known_ids.get(s.schedule_id, h) != h:
        reasons.append(RejectReason.ID_REUSED)
    if isinstance(held, SetpointSchedule):
        if s.issued_at < held.issued_at:
            reasons.append(RejectReason.OUT_OF_ORDER)
        if s.supersedes != held.content_hash():
            reasons.append(RejectReason.SUPERSEDES_MISMATCH)
    elif not isinstance(s.supersedes, NotApplicable):
        reasons.append(RejectReason.SUPERSEDES_MISMATCH)
    if _ms(received_at - s.issued_at) >= profile.schedule_max_age_ms:
        reasons.append(RejectReason.STALE_ON_ARRIVAL)
    if s.covered_until <= received_at:
        reasons.append(RejectReason.HORIZON_ELAPSED)
    if s.deliver_by != deliver_by(frame.sent_at, profile):
        reasons.append(RejectReason.DELIVER_BY_MISMATCH)
    violations: tuple[Violation, ...] = ()
    if isinstance(timeline, NotApplicable):
        reasons.append(RejectReason.ENVELOPE_UNKNOWN)
    else:
        inner = check_setpoint_schedule(timeline, s, stage)
        reasons.extend(inner.reasons)
        violations = inner.violations
    return ScheduleVerdict(
        accepted=not reasons,
        reasons=_sorted_reasons(reasons),
        violations=violations,
        late=received_at > s.deliver_by,
    )


# --------------------------------------------------------------------------------------------
# Heartbeat and staleness (D116)
# --------------------------------------------------------------------------------------------


class EdgeHeartbeat(ContractModel):
    """The edge is alive, and what it is doing: mode, active triggers, lease, the schedule and
    envelopes it holds, its ANM state and the import cap it enforces."""

    kind: Literal["edge_heartbeat"]
    mode: Literal["normal", "fail_safe"]
    episode_id: _ID | NotApplicable
    active_triggers: tuple[TriggerCode, ...]
    lease_remaining_ms: Ms
    held_schedule: Sha256 | NotApplicable
    governing_schedule: Sha256 | NotApplicable
    held_envelopes: tuple[Sha256, ...]
    anm_state: AnmState
    last_anm_event: Sha256 | NotApplicable
    import_cap: ImportCap

    @model_validator(mode="after")
    def _consistent(self) -> EdgeHeartbeat:
        if not _sorted_codes(self.active_triggers):
            raise ValueError("active_triggers must be in TriggerCode order, unique")
        if (self.mode == "fail_safe") != isinstance(self.episode_id, str):
            raise ValueError("an episode id is given exactly in fail-safe")
        if self.mode == "normal" and self.active_triggers:
            raise ValueError("an active trigger means fail-safe")
        if list(self.held_envelopes) != sorted(set(self.held_envelopes)):
            raise ValueError("held_envelopes must be sorted and unique")
        return self


class LinkObservation(ContractModel):
    """What the edge observes at one instant, on its monotonic clock (milliseconds since boot)
    and its wall clock. Nothing here survives a reboot: the lease is not persisted."""

    now_mono_ms: Ms
    now_utc: UtcInstant
    #: Last valid cloud-to-edge frame (any body): renews the lease.
    last_valid_rx_mono_ms: Ms | NotApplicable
    #: When the lease last became live after being lost (for the resume hold).
    lease_live_since_mono_ms: Ms | NotApplicable
    #: Receipt of the held schedule, and how far it covers.
    schedule_rx_mono_ms: Ms | NotApplicable
    schedule_covered_until: UtcInstant | NotApplicable
    wall_clock_trusted: bool

    @model_validator(mode="after")
    def _paired(self) -> LinkObservation:
        if isinstance(self.last_valid_rx_mono_ms, int) != isinstance(
            self.lease_live_since_mono_ms, int
        ):
            raise ValueError("a live lease has a start; no frame received means neither")
        if isinstance(self.schedule_rx_mono_ms, int) != isinstance(
            self.schedule_covered_until, datetime
        ):
            raise ValueError("a held schedule has a receipt time and a horizon, or neither")
        return self


class LinkVerdict(ContractModel):
    mode: Literal["normal", "fail_safe"]
    triggers: tuple[TriggerCode, ...]

    @model_validator(mode="after")
    def _consistent(self) -> LinkVerdict:
        if not _sorted_codes(self.triggers):
            raise ValueError("triggers must be in TriggerCode order, unique")
        if (self.mode == "fail_safe") != bool(self.triggers):
            raise ValueError("fail-safe exactly when a trigger holds")
        return self


def link_state(obs: LinkObservation, profile: LinkProfile) -> LinkVerdict:
    """The link and schedule triggers at one instant. Pure; equality at a threshold is fail-safe.

    - lease: nothing received, a reading in the future, or ``now - last >= control_lease_ms``,
      or still inside the resume hold → ``cloud_link_lost``;
    - no schedule held since boot → ``edge_restart``;
    - ``now - schedule receipt >= schedule_max_age_ms`` → ``schedule_stale``;
    - ``covered_until - now_utc <= schedule_required_lookahead_ms`` → ``schedule_exhausted``;
    - an untrusted wall clock → ``clock_unsynchronised``.
    """
    found: set[TriggerCode] = set()
    now = obs.now_mono_ms
    last, since = obs.last_valid_rx_mono_ms, obs.lease_live_since_mono_ms
    if not isinstance(last, int) or not isinstance(since, int):
        found.add(TriggerCode.CLOUD_LINK_LOST)
    else:
        age = now - last
        if age < 0 or age >= profile.control_lease_ms:
            found.add(TriggerCode.CLOUD_LINK_LOST)
        elif now - since < profile.resume_hold_ms:
            found.add(TriggerCode.CLOUD_LINK_LOST)
    rx, until = obs.schedule_rx_mono_ms, obs.schedule_covered_until
    if not isinstance(rx, int) or not isinstance(until, datetime):
        found.add(TriggerCode.EDGE_RESTART)
    else:
        if now - rx < 0 or now - rx >= profile.schedule_max_age_ms:
            found.add(TriggerCode.SCHEDULE_STALE)
        if _ms(until - obs.now_utc) <= profile.schedule_required_lookahead_ms:
            found.add(TriggerCode.SCHEDULE_EXHAUSTED)
    if not obs.wall_clock_trusted:
        found.add(TriggerCode.CLOCK_UNSYNCHRONISED)
    codes = tuple(sorted(found, key=_TRIGGER_ORDER.__getitem__))
    return LinkVerdict(mode="fail_safe" if codes else "normal", triggers=codes)


def edge_silent(
    now_mono_ms: int, last_rx_mono_ms: int | NotApplicable, profile: LinkProfile
) -> bool:
    """The cloud's view: the edge is silent after ``edge_silent_after_ms`` with no valid frame."""
    if not isinstance(last_rx_mono_ms, int):
        return True
    age = now_mono_ms - last_rx_mono_ms
    return age < 0 or age >= profile.edge_silent_after_ms


# --------------------------------------------------------------------------------------------
# Fail-safe episodes (D118)
# --------------------------------------------------------------------------------------------


class FailSafeTarget(ContractModel):
    """What the edge reverts to (spec B1-02): import within the firm limit or the latest DNO
    setpoint, state of energy at or above the resilience floor, markets declared unavailable,
    chillers to the BMS default. ``writes`` is ``none`` below L2 or with no envelope held."""

    import_limit_kw: Kw | NotApplicable
    resilience_floor_kwh: Kwh | NotApplicable
    markets: Literal["declared_unavailable"]
    chillers: Literal["bms_default"] | NotApplicable
    writes: Literal["enforce", "none"]


def fail_safe_target(
    envelope: DispatchEnvelope | NotApplicable,
    anm_state: AnmUnrestricted | AnmLimited | AnmTripped | Unknown,
    stage: ControlStage,
) -> FailSafeTarget:
    """The fail-safe target from the last envelope held, the ANM state and the authority."""
    chillers: Literal["bms_default"] | NotApplicable = (
        "bms_default"
        if _STAGE[stage] >= _STAGE[ControlStage.L3_CHILLER_BAND]
        else not_applicable("no chiller authority granted: the BMS keeps its own schedule")
    )
    if isinstance(envelope, NotApplicable):
        na = not_applicable(f"no envelope held: {envelope.reason}")
        return FailSafeTarget(
            import_limit_kw=na,
            resilience_floor_kwh=na,
            markets="declared_unavailable",
            chillers=chillers,
            writes="none",
        )
    firm = envelope.fail_safe.firm_import_limit_kw
    cap = effective_import_cap(
        schedule_kw=not_applicable("fail-safe"),
        envelope_limit_kw=not_applicable("fail-safe"),
        fail_safe_firm_kw=firm,
        anm_state=anm_state,
        in_fail_safe=True,
    )
    writes: Literal["enforce", "none"] = (
        "enforce" if _STAGE[stage] >= _STAGE[ControlStage.L2_BESS_CLOSED_LOOP] else "none"
    )
    return FailSafeTarget(
        import_limit_kw=cap.kw,
        resilience_floor_kwh=envelope.fail_safe.resilience_floor_kwh,
        markets="declared_unavailable",
        chillers=chillers,
        writes=writes,
    )


class TriggerInstance(ContractModel):
    code: TriggerCode
    source: Literal["edge_local", "cloud", "host", "neura_ops"]
    detected_at: UtcInstant
    detail: NonEmptyStr


class Release(ContractModel):
    """An audited release of a trigger that does not clear itself (IC-13)."""

    code: TriggerCode
    command_id: _ID
    audit_event_hash: Sha256


class EpisodeEntered(ContractModel):
    kind: Literal["entered"]
    triggers: tuple[TriggerInstance, ...] = Field(min_length=1)
    primary: TriggerCode
    envelope: EnvelopeRef | NotApplicable
    target: FailSafeTarget
    #: An episode a reboot left open, closed by this one (its entered event's hash).
    closes_orphan: Sha256 | NotApplicable

    @model_validator(mode="after")
    def _consistent(self) -> EpisodeEntered:
        if not _sorted_codes([t.code for t in self.triggers]):
            raise ValueError("triggers must be in TriggerCode order, unique")
        if self.primary != primary_trigger([t.code for t in self.triggers]):
            raise ValueError("primary must be the most severe trigger")
        return self


class TriggerRaised(ContractModel):
    kind: Literal["trigger_raised"]
    trigger: TriggerInstance
    primary: TriggerCode


class TriggerCleared(ContractModel):
    kind: Literal["trigger_cleared"]
    code: TriggerCode
    release: Release | NotApplicable
    primary: TriggerCode | NotApplicable

    @model_validator(mode="after")
    def _released(self) -> TriggerCleared:
        automatic = TRIGGERS[self.code].release == "automatic"
        if automatic != isinstance(self.release, NotApplicable):
            raise ValueError(
                f"{self.code.value} clears "
                + ("by itself: no release" if automatic else "only with an audited release")
            )
        if isinstance(self.release, Release) and self.release.code != self.code:
            raise ValueError("the release is for another trigger")
        return self


class TargetChanged(ContractModel):
    kind: Literal["target_changed"]
    target: FailSafeTarget
    reason: NonEmptyStr


class EpisodeExited(ContractModel):
    kind: Literal["exited"]
    entered_hash: Sha256
    resume_schedule_hash: Sha256
    #: When the edge accepted the schedule it resumed on: at or after the last clear.
    resume_accepted_at: UtcInstant
    releases: tuple[Release, ...]


EpisodeBody = Annotated[
    EpisodeEntered | TriggerRaised | TriggerCleared | TargetChanged | EpisodeExited,
    Field(discriminator="kind"),
]


class FailSafeEvent(ContractModel):
    """One step of a fail-safe episode: a hash chain from ``entered`` to ``exited``."""

    kind: Literal["fail_safe_event"]
    site_id: NonEmptyStr
    episode_id: _ID
    seq: Seq
    prev_hash: Sha256
    at: UtcInstant
    clock_state: Literal["synchronised", "unsynchronised"]
    monotonic_ms: Ms
    event: EpisodeBody

    @model_validator(mode="after")
    def _consistent(self) -> FailSafeEvent:
        if (self.seq == 0) != (self.prev_hash == GENESIS_PREV_HASH):
            raise ValueError("prev_hash is the genesis value exactly when seq == 0")
        if (self.seq == 0) != isinstance(self.event, EpisodeEntered):
            raise ValueError("an episode starts with entered, and only there")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


class EpisodeSummary(ContractModel):
    episode_id: _ID
    head_seq: Seq
    head_hash: Sha256
    active: tuple[TriggerCode, ...]
    exited: bool


def verify_episode(events: Sequence[FailSafeEvent]) -> EpisodeSummary:
    """Check one episode: the chain, the active set, each primary, and the exit rule (every
    trigger cleared, each non-automatic one by an audited release, and a schedule accepted at or
    after the last clear). Raises ``ValueError`` naming the first bad ``seq``."""
    if not events:
        raise ValueError("no events to verify")
    active: set[TriggerCode] = set()
    prev, last_at, last_mono, last_clear = GENESIS_PREV_HASH, None, -1, None
    entered_hash, exited = "", False
    releases: list[Release] = []
    first = events[0]

    def bad(e: FailSafeEvent, detail: str) -> ValueError:
        return ValueError(f"fail-safe episode {e.episode_id} broken at seq {e.seq}: {detail}")

    for i, e in enumerate(events):
        if e.seq != i or e.prev_hash != prev:
            raise bad(e, "seq or prev_hash does not continue the chain")
        if (e.episode_id, e.site_id) != (first.episode_id, first.site_id):
            raise bad(e, "another episode or site")
        if exited:
            raise bad(e, "an event after exit")
        if (last_at is not None and e.at < last_at) or e.monotonic_ms < last_mono:
            raise bad(e, "time goes backwards")
        body = e.event
        if isinstance(body, EpisodeEntered):
            active = {t.code for t in body.triggers}
            entered_hash = e.content_hash()
        elif isinstance(body, TriggerRaised):
            if body.trigger.code in active:
                raise bad(e, f"{body.trigger.code.value} is already active")
            active.add(body.trigger.code)
            if body.primary != primary_trigger(sorted(active, key=_TRIGGER_ORDER.__getitem__)):
                raise bad(e, "primary is not the most severe active trigger")
        elif isinstance(body, TriggerCleared):
            if body.code not in active:
                raise bad(e, f"{body.code.value} is not active")
            active.discard(body.code)
            want: TriggerCode | None = (
                primary_trigger(sorted(active, key=_TRIGGER_ORDER.__getitem__)) if active else None
            )
            if (body.primary if isinstance(body.primary, TriggerCode) else None) != want:
                raise bad(e, "primary is not the most severe active trigger")
            if isinstance(body.release, Release):
                releases.append(body.release)
            last_clear = e.at
        elif isinstance(body, EpisodeExited):
            if active:
                raise bad(e, f"exit with triggers active: {sorted(c.value for c in active)}")
            if body.entered_hash != entered_hash:
                raise bad(e, "entered_hash is not the episode's entered event")
            if last_clear is not None and body.resume_accepted_at < last_clear:
                raise bad(e, "the resume schedule was accepted before the last clear")
            if body.resume_accepted_at > e.at:
                raise bad(e, "exit before the resume schedule was accepted")
            if list(body.releases) != releases:
                raise bad(e, "releases must list every audited release of the episode in order")
            exited = True
        prev, last_at, last_mono = e.content_hash(), e.at, e.monotonic_ms
    return EpisodeSummary(
        episode_id=first.episode_id,
        head_seq=events[-1].seq,
        head_hash=prev,
        active=tuple(sorted(active, key=_TRIGGER_ORDER.__getitem__)),
        exited=exited,
    )


EdgeToCloudBody = Annotated[
    ScheduleAck | EdgeHeartbeat | FailSafeEvent | AnmEvent, Field(discriminator="kind")
]


# --------------------------------------------------------------------------------------------
# Frames and the chain (D114)
# --------------------------------------------------------------------------------------------


class PeerHead(ContractModel):
    """The last frame the sender received from the other side: an implicit acknowledgement."""

    kind: Literal["peer_head"]
    link_epoch: Seq
    seq: Seq
    frame_hash: Sha256


def _check_header(frame: CloudToEdgeFrame | EdgeToCloudFrame) -> None:
    if (frame.seq == 0) != (frame.prev_hash == GENESIS_PREV_HASH):
        raise ValueError("prev_hash is the genesis value exactly when seq == 0")
    site = getattr(frame.body, "site_id", frame.site_id)
    if isinstance(frame.body, EnvelopeInstall):
        site = frame.body.envelope.site_id
    if site != frame.site_id:
        raise ValueError("the body is for another site than the frame")


@final
class CloudToEdgeFrame(ContractModel):
    """IC-5 v1.0.0: one frame from the cloud to a site's edge."""

    contract: Literal["IC-5"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    direction: Literal["cloud_to_edge"]
    site_id: NonEmptyStr
    sender_id: _ID
    sent_at: UtcInstant
    link_epoch: Seq
    seq: Seq
    prev_hash: Sha256
    profile_hash: Sha256
    peer_head: PeerHead | NotApplicable = Field(discriminator="kind")
    body: CloudToEdgeBody

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("CloudToEdgeFrame is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> CloudToEdgeFrame:
        _check_header(self)
        b = self.body
        if isinstance(b, SetpointSchedule):
            if b.issued_at > self.sent_at:
                raise ValueError("a schedule is sent after it is issued")
            if b.deliver_by <= self.sent_at:
                raise ValueError("deliver_by must be after sent_at")
        if isinstance(b, OperatorCommand) and b.issued_at > self.sent_at:
            raise ValueError("a command is sent after it is issued")
        return self

    def frame_hash(self) -> str:
        return content_hash(self)


@final
class EdgeToCloudFrame(ContractModel):
    """IC-5 v1.0.0: one frame from a site's edge to the cloud."""

    contract: Literal["IC-5"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    direction: Literal["edge_to_cloud"]
    site_id: NonEmptyStr
    sender_id: _ID
    sent_at: UtcInstant
    link_epoch: Seq
    seq: Seq
    prev_hash: Sha256
    profile_hash: Sha256
    peer_head: PeerHead | NotApplicable = Field(discriminator="kind")
    body: EdgeToCloudBody

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EdgeToCloudFrame is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> EdgeToCloudFrame:
        _check_header(self)
        if isinstance(self.body, ScheduleAck) and self.body.edge_id != self.sender_id:
            raise ValueError("an ack is sent by the edge that made it")
        return self

    def frame_hash(self) -> str:
        return content_hash(self)


Frame = CloudToEdgeFrame | EdgeToCloudFrame


def check_frame_size(raw: bytes, profile: LinkProfile) -> bool:
    """True when a received frame's bytes are within the profile's ``max_frame_bytes``."""
    return len(raw) <= profile.max_frame_bytes


class FrameChainBroken(ValueError):
    def __init__(self, seq: int, detail: str) -> None:
        super().__init__(f"frame chain broken at seq {seq}: {detail}")
        self.seq = seq


def verify_frame_chain(frames: Sequence[Frame], *, after: PeerHead | None = None) -> PeerHead:
    """Check a complete, in-order run of one direction's frames in one epoch; return its head.
    ``after`` continues a run verified earlier."""
    if not frames:
        raise ValueError("no frames to verify")
    first = frames[0]
    head, last_sent = after, None
    for f in frames:
        if (f.site_id, f.direction, f.profile_hash) != (
            first.site_id,
            first.direction,
            first.profile_hash,
        ):
            raise FrameChainBroken(f.seq, "another site, direction or profile in one chain")
        epoch = first.link_epoch if head is None else head.link_epoch
        if f.link_epoch != epoch:
            raise FrameChainBroken(f.seq, "another link epoch in one chain")
        want_seq = 0 if head is None else head.seq + 1
        want_prev = GENESIS_PREV_HASH if head is None else head.frame_hash
        if f.seq != want_seq:
            raise FrameChainBroken(f.seq, f"expected seq {want_seq}")
        if f.prev_hash != want_prev:
            raise FrameChainBroken(f.seq, "prev_hash does not match the previous frame's hash")
        if last_sent is not None and f.sent_at < last_sent:
            raise FrameChainBroken(f.seq, "sent_at goes backwards")
        last_sent = f.sent_at
        head = PeerHead(
            kind="peer_head", link_epoch=f.link_epoch, seq=f.seq, frame_hash=f.frame_hash()
        )
    assert head is not None
    return head


class ChainStatus(StrEnum):
    IN_ORDER = "in_order"
    DUPLICATE = "duplicate"
    GAP = "gap"
    LATE_FILL = "late_fill"
    FORK = "fork"
    STALE_EPOCH = "stale_epoch"
    PROFILE_MISMATCH = "profile_mismatch"
    NEW_EPOCH = "new_epoch"


#: The verdicts whose frame is delivered to the application (and renews the lease).
DELIVERED: Final = frozenset(
    {ChainStatus.IN_ORDER, ChainStatus.GAP, ChainStatus.LATE_FILL, ChainStatus.NEW_EPOCH}
)


class SeqRange(ContractModel):
    first: Seq
    last: Seq

    @model_validator(mode="after")
    def _ordered(self) -> SeqRange:
        if self.last < self.first:
            raise ValueError("last must not be below first")
        return self


class ChainVerdict(ContractModel):
    """The receiver's verdict on one frame. ``deliver`` exactly for `DELIVERED`. A duplicate is
    answered with the earlier response; a fork raises ``chain_integrity_failure``."""

    status: ChainStatus
    deliver: bool
    frame_hash: Sha256
    missing: tuple[SeqRange, ...]
    detail: NonEmptyStr

    @model_validator(mode="after")
    def _consistent(self) -> ChainVerdict:
        if self.deliver != (self.status in DELIVERED):
            raise ValueError("deliver exactly for in_order, gap, late_fill and new_epoch")
        return self


class ChainTracker:
    """The receiving end of one direction of one site's link (not a contract model: state).

    Within an epoch: a frame at the next ``seq`` must link to the head (``prev_hash``); a jump is
    a ``gap`` (delivered, the missing range recorded); a missing frame that arrives later is a
    ``late_fill``, checked against both neighbours; the same bytes again are a ``duplicate``; any
    other frame at a seen ``seq``, a broken link or ``sent_at`` going backwards is a ``fork``,
    after which every frame of the epoch is refused until the sender starts a higher epoch. A
    lower epoch is ``stale_epoch``; another profile hash is ``profile_mismatch`` (refused, so it
    does not renew the lease)."""

    def __init__(
        self, site_id: str, direction: Literal["cloud_to_edge", "edge_to_cloud"], profile_hash: str
    ) -> None:
        self.site_id, self.direction, self.profile_hash = site_id, direction, profile_hash
        self.epoch: int | None = None
        self.broken = False
        self.head = -1
        self._hash: dict[int, str] = {}
        self._prev: dict[int, str] = {}
        self._sent: dict[int, datetime] = {}
        self._missing: list[tuple[int, int]] = []

    @property
    def missing(self) -> tuple[SeqRange, ...]:
        return tuple(SeqRange(first=a, last=b) for a, b in self._missing)

    def head_ref(self) -> PeerHead | NotApplicable:
        if self.epoch is None or self.head < 0:
            return not_applicable("nothing received yet")
        return PeerHead(
            kind="peer_head", link_epoch=self.epoch, seq=self.head, frame_hash=self._hash[self.head]
        )

    def _verdict(self, status: ChainStatus, h: str, detail: str) -> ChainVerdict:
        return ChainVerdict(
            status=status,
            deliver=status in DELIVERED,
            frame_hash=h,
            missing=self.missing,
            detail=detail,
        )

    def _record(self, f: Frame, h: str) -> None:
        self._hash[f.seq], self._prev[f.seq], self._sent[f.seq] = h, f.prev_hash, f.sent_at

    def _fork(self, h: str, detail: str) -> ChainVerdict:
        self.broken = True
        return self._verdict(ChainStatus.FORK, h, detail)

    def _remove_missing(self, seq: int) -> bool:
        for i, (a, b) in enumerate(self._missing):
            if a <= seq <= b:
                parts = [(a, seq - 1)] if a < seq else []
                parts += [(seq + 1, b)] if seq < b else []
                self._missing[i : i + 1] = parts
                return True
        return False

    def receive(self, frame: Frame) -> ChainVerdict:
        if frame.site_id != self.site_id or frame.direction != self.direction:
            raise ValueError("a frame for another site or direction")
        h = frame.frame_hash()
        if frame.profile_hash != self.profile_hash:
            return self._verdict(ChainStatus.PROFILE_MISMATCH, h, "sent under another link profile")
        if self.epoch is None or frame.link_epoch > self.epoch:
            self.epoch, self.broken, self.head = frame.link_epoch, False, frame.seq
            self._hash, self._prev, self._sent = {}, {}, {}
            self._missing = [(0, frame.seq - 1)] if frame.seq else []
            self._record(frame, h)
            if frame.seq == 0:
                return self._verdict(ChainStatus.NEW_EPOCH, h, f"epoch {frame.link_epoch}")
            return self._verdict(ChainStatus.GAP, h, f"epoch {frame.link_epoch} joined late")
        if frame.link_epoch < self.epoch:
            return self._verdict(ChainStatus.STALE_EPOCH, h, f"epoch {self.epoch} is current")
        if self.broken:
            return self._verdict(ChainStatus.FORK, h, "the epoch forked; only a new epoch resyncs")
        known = self._hash.get(frame.seq)
        if known is not None:
            if known == h:
                return self._verdict(ChainStatus.DUPLICATE, h, f"seq {frame.seq} again")
            return self._fork(h, f"a different frame at seq {frame.seq}")
        if frame.seq > self.head:
            if frame.sent_at < self._sent[self.head]:
                return self._fork(h, "sent_at goes backwards")
            if frame.seq == self.head + 1:
                if frame.prev_hash != self._hash[self.head]:
                    return self._fork(h, "prev_hash does not link to the head")
                status = ChainStatus.IN_ORDER
            else:
                self._missing.append((self.head + 1, frame.seq - 1))
                status = ChainStatus.GAP
            self._record(frame, h)
            self.head = frame.seq
            return self._verdict(status, h, f"seq {frame.seq}")
        before, after = self._hash.get(frame.seq - 1), self._prev.get(frame.seq + 1)
        if before is not None and frame.prev_hash != before:
            return self._fork(h, "a late frame that does not link to its predecessor")
        if after is not None and after != h:
            return self._fork(h, "a late frame its successor does not link to")
        lo, hi = self._sent.get(frame.seq - 1), self._sent.get(frame.seq + 1)
        if (lo is not None and frame.sent_at < lo) or (hi is not None and frame.sent_at > hi):
            return self._fork(h, "a late frame's sent_at is out of order")
        if not self._remove_missing(frame.seq):
            return self._fork(h, f"seq {frame.seq} was never missing")
        self._record(frame, h)
        return self._verdict(ChainStatus.LATE_FILL, h, f"seq {frame.seq} filled")


# --------------------------------------------------------------------------------------------
# Latency (D120)
# --------------------------------------------------------------------------------------------


class LatencySample(ContractModel):
    """One delivery on a path: when it was sent (sender's clock) and received (receiver's)."""

    path: LatencyPath
    sent_at: UtcInstant
    received_at: UtcInstant
    clocks_synchronised: bool


def latency_ms(sample: LatencySample, max_clock_skew_ms: int) -> int | None:
    """Whole milliseconds (floor of the microseconds over 1000). Negative within the skew bound
    reads as 0; beyond it, or with clocks not synchronised, ``None`` (a clock violation)."""
    if not sample.clocks_synchronised:
        return None
    ms = ((sample.received_at - sample.sent_at) // _US) // 1000
    if ms < 0:
        return 0 if -ms <= max_clock_skew_ms else None
    return ms


def nearest_rank(quantile_per_mille: int, n: int) -> int:
    """The 1-based nearest rank of a per-mille quantile among ``n`` sorted values."""
    if n < 1:
        raise ValueError("no values")
    return (quantile_per_mille * n + 999) // 1000


class LatencyComplianceReport(ContractModel):
    path: LatencyPath
    profile_hash: Sha256
    window_start: UtcInstant | NotApplicable
    window_end: UtcInstant | NotApplicable
    sample_count: Annotated[int, Field(ge=0)]
    eligible_count: Annotated[int, Field(ge=0)]
    excluded_clock_count: Annotated[int, Field(ge=0)]
    late_count: Annotated[int, Field(ge=0)]
    observed_ms: Ms | NotApplicable
    bound_ms: PosMs
    quantile_per_mille: Annotated[int, Field(ge=1, le=1000)]
    verdict: Literal[
        "insufficient_samples", "unverifiable_clock", "pass", "fail", "indeterminate_within_skew"
    ]


def check_latency(
    profile: LinkProfile, path: LatencyPath, samples: Sequence[LatencySample]
) -> LatencyComplianceReport:
    """A path's deliveries against its budget. In order: fewer eligible samples than
    ``min_samples`` is ``insufficient_samples``; more clock-excluded samples than the quantile's
    tail allows is ``unverifiable_clock``; then ``pass`` when observed + skew ≤ bound, ``fail``
    when observed − skew > bound, else ``indeterminate_within_skew``. Lateness is reported here,
    never raised as a fail-safe trigger."""
    budget = getattr(profile.latency_budgets, path)
    if not isinstance(budget, LatencyBudget):
        raise ValueError(f"the profile sets no budget for {path}")
    if any(s.path != path for s in samples):
        raise ValueError("every sample must be on the path checked")
    skew, q = profile.max_clock_skew_ms, budget.quantile_per_mille
    values = [latency_ms(s, skew) for s in samples]
    eligible = sorted(v for v in values if v is not None)
    excluded = len(values) - len(eligible)
    observed: int | NotApplicable = not_applicable("too few eligible samples")
    if len(eligible) < budget.min_samples:
        verdict = "insufficient_samples"
    elif excluded * 1000 > (1000 - q) * len(values):
        verdict = "unverifiable_clock"
    else:
        obs = eligible[nearest_rank(q, len(eligible)) - 1]
        observed = obs
        if obs + skew <= budget.bound_ms:
            verdict = "pass"
        elif obs - skew > budget.bound_ms:
            verdict = "fail"
        else:
            verdict = "indeterminate_within_skew"
    if isinstance(observed, NotApplicable) and verdict == "unverifiable_clock":
        observed = not_applicable("too many samples were taken on unsynchronised clocks")
    window = (
        (min(s.sent_at for s in samples), max(s.received_at for s in samples))
        if samples
        else (not_applicable("no samples"), not_applicable("no samples"))
    )
    return LatencyComplianceReport(
        path=path,
        profile_hash=profile.content_hash(),
        window_start=window[0],
        window_end=window[1],
        sample_count=len(values),
        eligible_count=len(eligible),
        excluded_clock_count=excluded,
        late_count=sum(1 for v in eligible if v > budget.bound_ms),
        observed_ms=observed,
        bound_ms=budget.bound_ms,
        quantile_per_mille=q,
        verdict=verdict,  # type: ignore[arg-type]
    )
