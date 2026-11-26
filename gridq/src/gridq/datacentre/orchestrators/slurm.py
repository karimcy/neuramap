"""The reference adapter: Slurm, read from slurmrestd ``v0.0.46`` responses (WP-SITE-08, B8-02).

Decisions D158 and D160 to D162 (Fable 5.2). The adapter reads a `StateDocument` holding the
bodies of five slurmrestd GETs, in the shape Slurm 26.11 documents for data parser ``v0.0.46``
(`DOC_URL`; preemption semantics `PREEMPT_DOC_URL`):

======================================  =================================================
request path                            what it supplies
======================================  =================================================
``/slurm/v0.0.46/jobs/``                running jobs: partition, QOS, nodes, ``requeue``,
                                        ``preemptable_time``, ``start_time``, ``end_time``,
                                        ``time_limit``
``/slurm/v0.0.46/nodes/``               measured node power: ``energy.current_watts``,
                                        ``energy.last_collected``
``/slurm/v0.0.46/partitions/``          ``preempt_mode``, ``grace_time``
``/slurm/v0.0.46/reservations/``        ``MAINT`` reservations
``/slurmdb/v0.0.46/qos/``               ``preempt.mode``, ``preempt.list``,
                                        ``preempt.exempt_time``, ``limits.grace_time``
======================================  =================================================

It never connects to slurmrestd and never writes to it. What Slurm does not state (who the operator
is, notice, how long the operator lets work be held off, the residual draw of a node whose jobs are
stopped, the retained IT floor, prices, SLA limits) comes from the operator's `SlurmPolicy`, never
from a default.

**Classes (S3).** A workload class is ``(partition, QOS, outcome)``. The effective preempt mode
comes from the QOS (policy ``preempt_type = preempt/qos``; the QOS must be preemptable by another
QOS) or the partition (``preempt/partition_prio``); an empty or ``CLUSTER_GLOBAL`` mode is the
policy's cluster mode. ``SUSPEND`` is deferred work whose energy returns; ``REQUEUE`` is deferred
when the job is requeueable and dropped otherwise; ``CANCEL`` is dropped. ``GANG``, ``WITHIN`` and
``PRIORITY`` are modifiers and ``OFF``/``DISABLED`` protect the work: neither yields an envelope.
A mode naming two actions is unmappable.

**kW (S3, S5-C1).** Only RUNNING jobs count. A node counts for a class only when every running job
on it belongs to that class and is counted, its ``current_watts`` is measured and fresh (policy
``max_power_sample_age_s``), and it is not in a maintenance reservation active at capture. A node
sheds ``current_watts`` minus the residual the policy states for its partition (``suspended`` for a
suspend class, ``idle`` otherwise; or, when the policy says so, the idle draw measured on an
identical idle node). A class's kW is the floor of its nodes' shed watts / 1,000. Never estimated
from GPU counts.

**Time (S4, S5-C2/C3).** A job counts from ``p_j`` (``preemptable_time``, else ``start_time`` plus
the QOS exempt time, else plus the policy's cluster exempt time) until ``e_j`` (``end_time``, else
``start_time`` plus ``time_limit``; clamped to the start of any future ``MAINT`` reservation on its
nodes). The validity runs from capture, rounded up to the half-hour, for the policy's horizon. At
each half-hour ``t`` and each policy hold ``d``, the class curve ``K(t, d)`` counts the nodes whose
jobs are all preemptible by ``t`` and still running at ``t + 30 min + d`` (an event may start
anywhere in the half-hour). Each distinct curve is a rung envelope (an exclusion group per class);
a half-hour offers every rung its curve dominates, so an event can be called across a decay step.
Half-hours with nothing offered are a maintenance blackout or "no eligible workload".

**Response (S4).** ``min_notice_s`` is the policy's. ``ramp_s`` is the policy's settle time, plus,
for requeue and cancel, the largest grace time (QOS or partition) of the class's jobs. Holds the
ramp cannot finish inside are dropped.

Every number in the declaration is traced in the ledger (`gridq.datacentre.orchestrators.ledger`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Unknown
from gridq.contracts.compute_turndown import (
    MAX_HOLD_MIN,
    RESOLUTION_MIN,
    CallLimits,
    UnavailableReason,
)
from gridq.contracts.evidence import EvidenceRef
from gridq.datacentre.orchestrators.adapter import (
    SYNTHETIC_PREFIX,
    Draft,
    MappingRefused,
    RecordClass,
    SealedAdapter,
    reason,
)
from gridq.datacentre.orchestrators.hostlist import HostlistError, expand
from gridq.datacentre.orchestrators.ledger import LedgerBuilder, SourceRef, Traced, pointer
from gridq.datacentre.orchestrators.outcome import RefusalCode, RefusalReason
from gridq.datacentre.orchestrators.policy import AdapterPolicy, PolicyPrice
from gridq.datacentre.orchestrators.state import StateDocument

__all__ = [
    "ADAPTER_ID",
    "API_ID",
    "API_VERSION",
    "DOC_URL",
    "JOBS",
    "NODES",
    "PARTITIONS",
    "PREEMPT_DOC_URL",
    "QOS",
    "RESERVATIONS",
    "RESOURCE_PATHS",
    "PartitionResidual",
    "PolicyRebound",
    "SlurmAdapter",
    "SlurmClassTerms",
    "SlurmPolicy",
    "encode_name",
]

API_ID: Final = "slurmrestd"
API_VERSION: Final = "v0.0.46"
ADAPTER_ID: Final = "slurmrestd"
ADAPTER_VERSION: Final = "1.0.0"
DOC_URL: Final = "https://slurm.schedmd.com/archive/slurm-26.11.0/rest_api.html"
PREEMPT_DOC_URL: Final = "https://slurm.schedmd.com/preempt.html"

JOBS: Final = "/slurm/v0.0.46/jobs/"
NODES: Final = "/slurm/v0.0.46/nodes/"
PARTITIONS: Final = "/slurm/v0.0.46/partitions/"
RESERVATIONS: Final = "/slurm/v0.0.46/reservations/"
QOS: Final = "/slurmdb/v0.0.46/qos/"
RESOURCE_PATHS: Final = tuple(sorted((JOBS, NODES, PARTITIONS, RESERVATIONS, QOS)))

Outcome = Literal["suspend", "requeue", "cancel"]
_ACTIONS: Final = {"SUSPEND": "suspend", "REQUEUE": "requeue", "CANCEL": "cancel"}
_INHERIT: Final = frozenset({"CLUSTER_GLOBAL"})
_HALF_HOUR: Final = timedelta(minutes=RESOLUTION_MIN)
_PosInt = Annotated[int, Field(ge=1)]
_NonNeg = Annotated[int, Field(ge=0)]


# --------------------------------------------------------------------------------------------
# The policy: what Slurm does not state
# --------------------------------------------------------------------------------------------


class PartitionResidual(ContractModel):
    """The IT draw a node of ``partition`` keeps when its jobs are suspended or stopped, in W."""

    kind: Literal["partition_residual"]
    partition: NonEmptyStr
    suspended_w: _NonNeg | Unknown
    idle_w: _NonNeg | Unknown


class PolicyRebound(ContractModel):
    """IC-9 ``ReboundSize`` as the operator states it."""

    kind: Literal["policy_rebound"]
    recovery_permille: _PosInt | Unknown
    max_rebound_kw: _PosInt | Unknown


class SlurmClassTerms(ContractModel):
    """The operator's terms for one workload class ``(partition, qos, outcome)``."""

    kind: Literal["slurm_class_terms"]
    partition: NonEmptyStr
    qos: NonEmptyStr
    outcome: Outcome
    #: How the operator keeps the freed nodes from being backfilled during a hold (S3-05).
    hold_mechanism: NonEmptyStr | Unknown
    #: The holds the operator offers, in minutes (strictly ascending, 1 to 1,440).
    hold_points_min: tuple[Annotated[int, Field(ge=1, le=MAX_HOLD_MIN)], ...] | Unknown
    min_notice_s: _NonNeg | Unknown
    #: Seconds from the preempt signal (or the end of grace) to the node's power settling.
    stop_settle_s: _NonNeg | Unknown
    #: For suspend and requeue (work that returns): within how long of release.
    return_window_min: _PosInt | Unknown | NotApplicable
    #: For cancel (dropped work): the operator states its energy does not return here.
    dropped_energy_return: Literal["no_return"] | Unknown | NotApplicable
    rebound: PolicyRebound | Unknown | NotApplicable
    call_limits: CallLimits | Unknown
    price: PolicyPrice | Unknown
    sla_evidence: EvidenceRef | Unknown

    @model_validator(mode="after")
    def _holds(self) -> SlurmClassTerms:
        h = self.hold_points_min
        if not isinstance(h, Unknown) and (not h or list(h) != sorted(set(h))):
            raise ValueError("hold_points_min must be non-empty, strictly ascending")
        return self


class SlurmPolicy(AdapterPolicy):
    """`AdapterPolicy` plus the Slurm facts the five responses do not carry."""

    kind: Literal["slurm_adapter_policy"]
    #: slurm.conf ``PreemptType``.
    preempt_type: Literal["preempt/qos", "preempt/partition_prio"] | Unknown
    #: slurm.conf ``PreemptMode`` (what an empty or ``CLUSTER_GLOBAL`` mode inherits).
    cluster_preempt_mode: tuple[NonEmptyStr, ...] | Unknown
    #: slurm.conf ``PreemptExemptTime`` in seconds, where neither the job nor the QOS states one.
    cluster_exempt_time_s: _NonNeg | NotApplicable | Unknown
    #: How old a node's power reading may be at capture.
    max_power_sample_age_s: _PosInt | Unknown
    #: ``stated``: residuals only from ``residuals``; ``idle_twin_measured``: an unstated idle
    #: residual is the draw of an identical idle node in the same state document.
    residual_basis: Literal["stated", "idle_twin_measured"]
    residuals: tuple[PartitionResidual, ...]
    #: How far ahead the snapshot is declared (a multiple of 30 minutes, 30 to 1,440).
    horizon_min: _PosInt | Unknown
    classes: tuple[SlurmClassTerms, ...]

    @model_validator(mode="after")
    def _slurm(self) -> SlurmPolicy:
        h = self.horizon_min
        if isinstance(h, int) and (h % RESOLUTION_MIN or not RESOLUTION_MIN <= h <= MAX_HOLD_MIN):
            raise ValueError("horizon_min is a multiple of 30 from 30 to 1440 (never clamped)")
        keys = [(c.partition, c.qos, c.outcome) for c in self.classes]
        if len(set(keys)) != len(keys):
            raise ValueError("one SlurmClassTerms per (partition, qos, outcome)")
        parts = [r.partition for r in self.residuals]
        if len(set(parts)) != len(parts):
            raise ValueError("one PartitionResidual per partition")
        return self


# --------------------------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------------------------


def encode_name(name: str) -> str:
    """A Slurm name in IC-9's id alphabet, injectively: every code point outside
    ``[A-Za-z0-9-]`` becomes ``:`` and six lower-case hex digits (S5-C8)."""
    return "".join(
        c if (c.isascii() and (c.isalnum() or c == "-")) else f":{ord(c):06x}" for c in name
    )


# --------------------------------------------------------------------------------------------
# Reading the payload (shape errors are MALFORMED_STATE, never guessed)
# --------------------------------------------------------------------------------------------


class _Malformed(Exception):
    def __init__(self, where: str, detail: str) -> None:
        super().__init__(detail)
        self.where, self.detail = where, detail


def _malformed(where: str, detail: str) -> _Malformed:
    return _Malformed(where, detail)


def _get(obj: Any, key: str, where: str, kind: type | tuple[type, ...]) -> Any:
    if not isinstance(obj, Mapping) or key not in obj:
        raise _malformed(where, f"missing {key!r}")
    v = obj[key]
    if isinstance(v, bool) and kind is not bool:
        raise _malformed(pointer_join(where, key), f"{key!r} is a boolean")
    if not isinstance(v, kind):
        raise _malformed(pointer_join(where, key), f"{key!r} is not {kind}")
    return v


def pointer_join(where: str, *tokens: str | int) -> str:
    return where + pointer(*tokens)


@dataclass(frozen=True)
class _Num:
    """A slurmrestd ``{set, infinite, number}`` value."""

    state: Literal["unset", "infinite", "value"]
    number: int
    where: str

    def ref(self) -> SourceRef:
        return SourceRef(doc="state", json_pointer=self.where + "/number", value=self.number)


def _wrapped(obj: Any, key: str, where: str) -> _Num:
    w = _get(obj, key, where, Mapping)
    here = pointer_join(where, key)
    is_set = _get(w, "set", here, bool)
    infinite = _get(w, "infinite", here, bool)
    number = _get(w, "number", here, int)
    if not is_set:
        return _Num("unset", number, here)
    if infinite:
        return _Num("infinite", number, here)
    return _Num("value", number, here)


def _list_of_str(obj: Any, key: str, where: str) -> tuple[str, ...]:
    v = _get(obj, key, where, list)
    if not all(isinstance(x, str) for x in v):
        raise _malformed(pointer_join(where, key), f"{key!r} is not a list of strings")
    return tuple(v)


def _epoch(n: int) -> datetime:
    return datetime.fromtimestamp(n, tz=UTC)


def _body(state: StateDocument, path: str, key: str) -> tuple[list[Any], str]:
    root = pointer("payload", path)
    body = state.payload[path]
    errors = _get(body, "errors", root, list)
    if errors:
        raise _malformed(pointer_join(root, "errors"), "the recorded response carries errors")
    items = _get(body, key, root, list)
    return items, pointer_join(root, key)


@dataclass(frozen=True)
class _Job:
    where: str
    job_id: int
    partition: str
    qos: str
    running: bool
    requeue: bool
    nodes: tuple[str, ...]
    preemptable: _Num
    start: _Num
    end: _Num
    time_limit: _Num


@dataclass(frozen=True)
class _Node:
    where: str
    name: str
    state: tuple[str, ...]
    partitions: tuple[str, ...]
    watts: _Num
    last_collected: int
    twin_key: tuple[Any, ...]


@dataclass(frozen=True)
class _Partition:
    where: str
    name: str
    preempt_mode: tuple[str, ...]
    grace_time: int


@dataclass(frozen=True)
class _Qos:
    where: str
    name: str
    mode: tuple[str, ...]
    preempt_list: tuple[str, ...]
    exempt: _Num
    grace_time: int


@dataclass(frozen=True)
class _Reservation:
    where: str
    name: str
    maint: bool
    start: _Num
    end: _Num
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class _State:
    jobs: tuple[_Job, ...]
    nodes: dict[str, _Node]
    partitions: dict[str, _Partition]
    qos: dict[str, _Qos]
    reservations: tuple[_Reservation, ...]


def _hosts(expr: str, where: str) -> tuple[str, ...]:
    try:
        return expand(expr)
    except HostlistError as exc:
        raise _malformed(where, str(exc)) from exc


def _unique(items: Iterable[Any], what: str, where: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        if it.name in out:
            raise MappingRefused(
                reason(RefusalCode.AMBIGUOUS_STATE, it.where, f"two {what} named {it.name!r}")
            )
        out[it.name] = it
    return out


def _read(state: StateDocument) -> _State:
    jobs: list[_Job] = []
    items, base = _body(state, JOBS, "jobs")
    for i, j in enumerate(items):
        w = pointer_join(base, i)
        name_ok = isinstance(j, Mapping)
        if not name_ok:
            raise _malformed(w, "a job is not an object")
        states = _list_of_str(j, "job_state", w)
        jobs.append(
            _Job(
                where=w,
                job_id=_get(j, "job_id", w, int),
                partition=_get(j, "partition", w, str),
                qos=_get(j, "qos", w, str),
                running=states == ("RUNNING",),
                requeue=_get(j, "requeue", w, bool),
                nodes=_hosts(_get(j, "nodes", w, str), pointer_join(w, "nodes")),
                preemptable=_wrapped(j, "preemptable_time", w),
                start=_wrapped(j, "start_time", w),
                end=_wrapped(j, "end_time", w),
                time_limit=_wrapped(j, "time_limit", w),
            )
        )
    nodes: list[_Node] = []
    items, base = _body(state, NODES, "nodes")
    for i, n in enumerate(items):
        w = pointer_join(base, i)
        energy = _get(n, "energy", w, Mapping)
        ew = pointer_join(w, "energy")
        nodes.append(
            _Node(
                where=w,
                name=_get(n, "name", w, str),
                state=_list_of_str(n, "state", w),
                partitions=_list_of_str(n, "partitions", w),
                watts=_wrapped(energy, "current_watts", ew),
                last_collected=_get(energy, "last_collected", ew, int),
                twin_key=(
                    _get(n, "cpus", w, int),
                    _get(n, "gres", w, str),
                    _get(n, "tres", w, str),
                    _list_of_str(n, "partitions", w),
                ),
            )
        )
    parts: list[_Partition] = []
    items, base = _body(state, PARTITIONS, "partitions")
    for i, p in enumerate(items):
        w = pointer_join(base, i)
        parts.append(
            _Partition(
                where=w,
                name=_get(p, "name", w, str),
                preempt_mode=_list_of_str(p, "preempt_mode", w),
                grace_time=_get(p, "grace_time", w, int),
            )
        )
    qos: list[_Qos] = []
    items, base = _body(state, QOS, "qos")
    for i, q in enumerate(items):
        w = pointer_join(base, i)
        pre = _get(q, "preempt", w, Mapping)
        pw = pointer_join(w, "preempt")
        limits = _get(q, "limits", w, Mapping)
        qos.append(
            _Qos(
                where=w,
                name=_get(q, "name", w, str),
                mode=_list_of_str(pre, "mode", pw),
                preempt_list=_list_of_str(pre, "list", pw),
                exempt=_wrapped(pre, "exempt_time", pw),
                grace_time=_get(limits, "grace_time", pointer_join(w, "limits"), int),
            )
        )
    resv: list[_Reservation] = []
    items, base = _body(state, RESERVATIONS, "reservations")
    for i, r in enumerate(items):
        w = pointer_join(base, i)
        resv.append(
            _Reservation(
                where=w,
                name=_get(r, "name", w, str),
                maint="MAINT" in _list_of_str(r, "flags", w),
                start=_wrapped(r, "start_time", w),
                end=_wrapped(r, "end_time", w),
                nodes=_hosts(_get(r, "node_list", w, str), pointer_join(w, "node_list")),
            )
        )
    named: list[tuple[str, tuple[str, ...]]] = [(j.where, (j.partition, j.qos)) for j in jobs]
    named += [(n.where, (n.name,)) for n in nodes]
    named += [(x.where, (x.name,)) for x in parts]
    named += [(x.where, (x.name,)) for x in qos]
    named += [(x.where, (x.name,)) for x in resv]
    for where, names in named:
        if "" in names:
            raise _malformed(where, "an empty name")
    return _State(
        jobs=tuple(jobs),
        nodes=_unique(nodes, "nodes", NODES),
        partitions=_unique(parts, "partitions", PARTITIONS),
        qos=_unique(qos, "QOS", QOS),
        reservations=tuple(resv),
    )


# --------------------------------------------------------------------------------------------
# The mapping
# --------------------------------------------------------------------------------------------

ClassKey = tuple[str, str, Outcome]


def _policy_ref(ptr: str, value: Any) -> SourceRef:
    return SourceRef(doc="policy", json_pointer=ptr, value=value)


def _rule_ref(rule: str, value: Any) -> SourceRef:
    return SourceRef(doc="rule", json_pointer="", value=value)


@dataclass
class _Admitted:
    """A running job in a class, with its preemptible window ``[p, e)`` (``e`` None: unbounded)."""

    job: _Job
    key: ClassKey
    p: datetime
    p_refs: tuple[SourceRef, ...]
    e: datetime | None
    e_refs: tuple[SourceRef, ...]
    grace_s: int
    grace_refs: tuple[SourceRef, ...]


@dataclass
class _ClassNodes:
    """The homogeneous, measured nodes of one class, with their shed watts."""

    key: ClassKey
    terms: SlurmClassTerms
    terms_ptr: str
    nodes: list[tuple[_Node, int, tuple[SourceRef, ...]]] = field(default_factory=list)
    jobs_on: dict[str, list[_Admitted]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Curve:
    """A cleaned class curve at one half-hour: (hold, kW, traced kW) points, shortest hold first."""

    points: tuple[tuple[int, int], ...]
    traced: tuple[Traced, ...]

    def step(self, holds: Sequence[int]) -> tuple[int, ...]:
        """IC-9 ``turn_down_kw`` at each hold (no load clamp: the load is the nodes')."""
        out = []
        for d in holds:
            out.append(next((kw for h, kw in self.points if h >= d), 0))
        return tuple(out)


class SlurmAdapter(SealedAdapter):
    """slurmrestd ``v0.0.46`` state documents to IC-9 1.0.0 declarations."""

    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    api_id = API_ID
    supported_api_versions = (API_VERSION,)
    policy_type = SlurmPolicy

    def _map(
        self,
        state: StateDocument,
        policy: SlurmPolicy,
        ledger: LedgerBuilder,
        record_class: RecordClass,
    ) -> Draft:
        if tuple(state.source.resource_paths) != RESOURCE_PATHS:
            raise MappingRefused(
                reason(
                    RefusalCode.MALFORMED_STATE,
                    "/source/resource_paths",
                    f"the Slurm adapter reads exactly {list(RESOURCE_PATHS)}",
                )
            )
        try:
            st = _read(state)
        except _Malformed as exc:
            raise MappingRefused(
                reason(RefusalCode.MALFORMED_STATE, exc.where, exc.detail)
            ) from exc
        return _Mapper(state, policy, ledger, record_class, st).run()


class _Mapper:
    def __init__(
        self,
        state: StateDocument,
        policy: SlurmPolicy,
        ledger: LedgerBuilder,
        record_class: RecordClass,
        st: _State,
    ) -> None:
        self.state, self.policy, self.ledger, self.st = state, policy, ledger, st
        self.synthetic = record_class == "synthetic_fixture"
        self.record_class = record_class
        self.captured = state.captured_at
        self.skipped: list[RefusalReason] = []
        self.withheld: list[RefusalReason] = []

    # ---------------------------------------------------------------- labels and policy facts

    def _check_labels(self) -> None:
        p = self.policy
        labels = [("policy:/site_id", p.site_id), ("policy:/operator_id", p.operator_id)]
        bad = [
            reason(
                RefusalCode.LABEL_MISMATCH,
                where,
                f"{text!r}: a {'synthetic' if self.synthetic else 'registered'} record's site "
                f"and operator {'start' if self.synthetic else 'never start'} {SYNTHETIC_PREFIX}",
            )
            for where, text in labels
            if isinstance(text, str) and text.startswith(SYNTHETIC_PREFIX) != self.synthetic
        ]
        if bad:
            raise MappingRefused(*bad)

    def _declaration_facts(self) -> tuple[int, int]:
        p = self.policy
        missing = [
            reason(RefusalCode.POLICY_FACT_MISSING, f"policy:/{name}", v.reason)
            for name, v in (
                ("horizon_min", p.horizon_min),
                ("retained_it_floor_kw", p.retained_it_floor_kw),
            )
            if isinstance(v, Unknown)
        ]
        if missing:
            raise MappingRefused(*missing)
        assert isinstance(p.horizon_min, int) and isinstance(p.retained_it_floor_kw, int)
        return p.horizon_min, p.retained_it_floor_kw

    # ---------------------------------------------------------------- preempt mode

    def _outcome(self, job: _Job) -> tuple[Outcome | None, RefusalReason | None]:
        """The job's preemption outcome; ``(None, None)`` for protected work, ``(None, why)``
        when the mode cannot be resolved."""
        p = self.policy
        if isinstance(p.preempt_type, Unknown):
            return None, reason(
                RefusalCode.UNMAPPABLE_WORKLOAD, "policy:/preempt_type", p.preempt_type.reason
            )
        if p.preempt_type == "preempt/qos":
            q = self.st.qos.get(job.qos)
            if q is None:
                return None, reason(
                    RefusalCode.AMBIGUOUS_STATE, job.where, f"QOS {job.qos!r} is not in the state"
                )
            if not any(job.qos in o.preempt_list for o in self.st.qos.values() if o is not q):
                return None, None  # no QOS can preempt it: protected work
            mode, where = q.mode, pointer_join(q.where, "preempt", "mode")
        else:
            part = self.st.partitions.get(job.partition)
            if part is None:
                return None, reason(
                    RefusalCode.AMBIGUOUS_STATE,
                    job.where,
                    f"partition {job.partition!r} is not in the state",
                )
            mode, where = part.preempt_mode, pointer_join(part.where, "preempt_mode")
        if not mode or set(mode) <= _INHERIT:
            if isinstance(p.cluster_preempt_mode, Unknown):
                return None, reason(
                    RefusalCode.UNMAPPABLE_WORKLOAD,
                    "policy:/cluster_preempt_mode",
                    f"{where} inherits the cluster mode, which the policy does not state",
                )
            mode = p.cluster_preempt_mode
        actions = sorted({_ACTIONS[m] for m in mode if m in _ACTIONS})
        if not actions:
            return None, None  # OFF, DISABLED or modifiers only: protected work
        if len(actions) > 1:
            return None, reason(
                RefusalCode.UNMAPPABLE_WORKLOAD, where, f"the mode names {actions}: two actions"
            )
        action = actions[0]
        if action == "requeue" and not job.requeue:
            return "cancel", None
        return action, None  # type: ignore[return-value]

    # ---------------------------------------------------------------- job windows

    def _window(
        self, job: _Job
    ) -> tuple[datetime, tuple[SourceRef, ...], datetime | None, tuple[SourceRef, ...]] | None:
        """``(p, p_refs, e, e_refs)``, or ``None`` (the job is withheld, with the reason
        recorded)."""
        start = job.start
        p_refs: tuple[SourceRef, ...]
        if job.preemptable.state == "value" and job.preemptable.number > 0:
            p, p_refs = _epoch(job.preemptable.number), (job.preemptable.ref(),)
        else:
            if start.state != "value":
                self._withhold(
                    job.where, RefusalCode.AMBIGUOUS_STATE, "a running job's start_time is not set"
                )
                return None
            exempt: int | None = None
            refs: tuple[SourceRef, ...] = ()
            q = self.st.qos.get(job.qos)
            if q is not None and q.exempt.state == "infinite":
                self._withhold(
                    job.where,
                    RefusalCode.NO_FLEXIBLE_WORKLOAD,
                    "the QOS exempts the job from preemption without limit",
                )
                return None
            if q is not None and q.exempt.state == "value":
                exempt, refs = q.exempt.number, (q.exempt.ref(),)
            else:
                c = self.policy.cluster_exempt_time_s
                if isinstance(c, Unknown):
                    self._withhold(
                        job.where,
                        RefusalCode.POLICY_FACT_MISSING,
                        "no exempt time is stated: " + c.reason,
                    )
                    return None
                if isinstance(c, NotApplicable):
                    exempt = 0
                    refs = (_policy_ref("/cluster_exempt_time_s", "not_applicable"),)
                else:
                    exempt, refs = c, (_policy_ref("/cluster_exempt_time_s", c),)
            p = _epoch(start.number) + timedelta(seconds=exempt)
            p_refs = (start.ref(), *refs)
        if job.end.state == "value":
            e: datetime | None = _epoch(job.end.number)
            e_refs: tuple[SourceRef, ...] = (job.end.ref(),)
        elif job.time_limit.state == "infinite":
            e, e_refs = None, (job.time_limit.ref(),)
        elif job.time_limit.state == "value" and start.state == "value":
            e = _epoch(start.number) + timedelta(minutes=job.time_limit.number)
            e_refs = (start.ref(), job.time_limit.ref())
        else:
            self._withhold(
                job.where,
                RefusalCode.AMBIGUOUS_STATE,
                "neither end_time nor start_time plus time_limit is stated",
            )
            return None
        for r in self.st.reservations:
            if not r.maint or not set(r.nodes) & set(job.nodes):
                continue
            if r.end.state == "value" and _epoch(r.end.number) <= self.captured:
                continue
            if r.start.state != "value":
                self._withhold(
                    r.where,
                    RefusalCode.AMBIGUOUS_STATE,
                    "a MAINT reservation on the job's nodes has no start_time",
                )
                return None
            rs = _epoch(r.start.number)
            if rs > self.captured and (e is None or rs < e):
                e, e_refs = rs, (*e_refs, r.start.ref())
        return p, p_refs, e, e_refs

    def _withhold(self, where: str, code: RefusalCode, detail: str) -> None:
        self.withheld.append(reason(code, where, detail))

    # ---------------------------------------------------------------- the run

    def run(self) -> Draft:
        self._check_labels()
        horizon, floor_kw = self._declaration_facts()
        terms_by_key = {
            (c.partition, c.qos, c.outcome): (c, pointer("classes", i))
            for i, c in enumerate(self.policy.classes)
        }

        admitted: dict[int, _Admitted] = {}
        uncounted_on: dict[str, list[str]] = {}
        classes_seen: set[ClassKey] = set()
        unresolved: list[RefusalReason] = []
        for job in self.st.jobs:
            if not job.running:
                continue
            outcome, why = self._outcome(job)
            if outcome is None:
                if why is not None:
                    unresolved.append(why)
                for n in job.nodes:
                    uncounted_on.setdefault(n, []).append(
                        f"job {job.job_id} " + ("unresolved" if why else "protected")
                    )
                continue
            key: ClassKey = (job.partition, job.qos, outcome)
            classes_seen.add(key)
            missing_node = [n for n in job.nodes if n not in self.st.nodes]
            window = None if missing_node else self._window(job)
            if missing_node:
                self._withhold(
                    job.where,
                    RefusalCode.AMBIGUOUS_STATE,
                    f"nodes {missing_node} are not in the state",
                )
            if window is None:
                for n in job.nodes:
                    uncounted_on.setdefault(n, []).append(f"job {job.job_id} uncounted")
                continue
            p, p_refs, e, e_refs = window
            grace, grace_refs = 0, tuple[SourceRef, ...]()
            if outcome != "suspend":
                q, part = self.st.qos.get(job.qos), self.st.partitions.get(job.partition)
                cands = []
                if q is not None:
                    cands.append((q.grace_time, pointer_join(q.where, "limits", "grace_time")))
                if part is not None:
                    cands.append((part.grace_time, pointer_join(part.where, "grace_time")))
                if cands:
                    grace = max(v for v, _ in cands)
                    grace_refs = tuple(
                        SourceRef(doc="state", json_pointer=w, value=v) for v, w in cands
                    )
            admitted[job.job_id] = _Admitted(job, key, p, p_refs, e, e_refs, grace, grace_refs)

        if not classes_seen:
            raise MappingRefused(
                *(
                    unresolved
                    or [
                        reason(
                            RefusalCode.NO_FLEXIBLE_WORKLOAD,
                            pointer("payload", JOBS),
                            "no running job is preemptible",
                        )
                    ]
                )
            )
        self.skipped.extend(unresolved)

        # Class terms (S5-C7: a missing fact skips the class).
        classes: dict[ClassKey, _ClassNodes] = {}
        for key in sorted(classes_seen):
            found = terms_by_key.get(key)
            label = f"class {key}"
            if found is None:
                self.skipped.append(
                    reason(
                        RefusalCode.POLICY_FACT_MISSING,
                        "policy:/classes",
                        f"{label}: the policy states no terms",
                    )
                )
                continue
            terms, tptr = found
            gaps = [
                name
                for name in ("hold_mechanism", "hold_points_min", "min_notice_s", "stop_settle_s")
                if isinstance(getattr(terms, name), Unknown)
            ]
            if gaps:
                self.skipped.append(
                    reason(
                        RefusalCode.POLICY_FACT_MISSING,
                        "policy:" + tptr,
                        f"{label}: unstated {gaps}",
                    )
                )
                continue
            classes[key] = _ClassNodes(key, terms, tptr)

        # Nodes (S3-08 to S3-11, S5-C9).
        jobs_on: dict[str, list[_Admitted]] = {}
        for a in admitted.values():
            for n in a.job.nodes:
                jobs_on.setdefault(n, []).append(a)
        active_maint: dict[str, str] = {}
        for r in self.st.reservations:
            if r.maint and r.start.state == "value" and _epoch(r.start.number) <= self.captured:
                if r.end.state != "value" or _epoch(r.end.number) > self.captured:
                    for n in r.nodes:
                        active_maint.setdefault(n, r.where)
        residual_cache: dict[tuple[str, str], tuple[int, tuple[SourceRef, ...]] | None] = {}
        for name in sorted(jobs_on):
            node = self.st.nodes[name]
            on = jobs_on[name]
            keys = {a.key for a in on}
            if name in uncounted_on or len(keys) != 1:
                shared = uncounted_on.get(name, []) + (
                    [f"classes {sorted(keys)}"] if len(keys) > 1 else []
                )
                self._withhold(
                    node.where, RefusalCode.UNMAPPABLE_WORKLOAD, f"node {name} is shared: {shared}"
                )
                continue
            (key,) = keys
            cls = classes.get(key)
            if cls is None:
                continue
            if name in active_maint:
                self._withhold(
                    node.where,
                    RefusalCode.NO_FLEXIBLE_WORKLOAD,
                    f"node {name} is in maintenance at capture",
                )
                continue
            watts = self._measured(node)
            if watts is None:
                continue
            kind = "suspended" if key[2] == "suspend" else "idle"
            if (key[0], kind) not in residual_cache:
                residual_cache[key[0], kind] = self._residual(key[0], kind)
            res = residual_cache[key[0], kind]
            if res is None:
                continue
            res_w, res_refs = res
            shed = max(0, node.watts.number - res_w)
            refs = (node.watts.ref(), *res_refs)
            cls.nodes.append((node, shed, refs))
            cls.jobs_on[name] = on
        for key, cls in list(classes.items()):
            kind = "suspended" if key[2] == "suspend" else "idle"
            if (key[0], kind) in residual_cache and residual_cache[key[0], kind] is None:
                gap = reason(
                    RefusalCode.POWER_BASIS_MISSING,
                    "policy:/residuals",
                    f"class {key}: no {kind} residual for partition {key[0]!r}",
                )
            elif not cls.nodes:
                gap = reason(
                    RefusalCode.POWER_BASIS_MISSING,
                    "policy:" + cls.terms_ptr,
                    f"class {key}: no node of the class is wholly its own and measured",
                )
            else:
                continue
            self.skipped.append(gap)
            del classes[key]

        if not classes:
            raise MappingRefused(*self._first_skip_code())

        return self._declare(classes, horizon, floor_kw)

    def _first_skip_code(self) -> tuple[RefusalReason, ...]:
        order = (
            RefusalCode.POLICY_FACT_MISSING,
            RefusalCode.POWER_BASIS_MISSING,
            RefusalCode.UNMAPPABLE_WORKLOAD,
            RefusalCode.NO_FLEXIBLE_WORKLOAD,
        )
        for code in order:
            hit = tuple(r for r in self.skipped if r.code == code)
            if hit:
                return hit
        return tuple(self.skipped) or (
            reason(RefusalCode.NO_FLEXIBLE_WORKLOAD, "", "no class can be offered"),
        )

    def _measured(self, node: _Node) -> int | None:
        age = self.policy.max_power_sample_age_s
        w = node.watts
        if w.state != "value" or w.number <= 0:
            self._withhold(
                w.where,
                RefusalCode.POWER_BASIS_MISSING,
                f"node {node.name}: current_watts is not measured",
            )
            return None
        if isinstance(age, Unknown):
            self._withhold(
                node.where,
                RefusalCode.POWER_BASIS_MISSING,
                "policy max_power_sample_age_s is unstated: " + age.reason,
            )
            return None
        collected = _epoch(node.last_collected)
        if collected > self.captured or (self.captured - collected).total_seconds() > age:
            self._withhold(
                pointer_join(node.where, "energy", "last_collected"),
                RefusalCode.POWER_BASIS_MISSING,
                f"node {node.name}: the power reading is not within {age} s before capture",
            )
            return None
        return w.number

    def _residual(self, partition: str, kind: str) -> tuple[int, tuple[SourceRef, ...]] | None:
        for i, r in enumerate(self.policy.residuals):
            if r.partition != partition:
                continue
            value = r.suspended_w if kind == "suspended" else r.idle_w
            if isinstance(value, int):
                ptr = pointer("residuals", i, "suspended_w" if kind == "suspended" else "idle_w")
                return value, (_policy_ref(ptr, value),)
        if kind == "idle" and self.policy.residual_basis == "idle_twin_measured":
            return self._idle_twin(partition)
        return None

    def _idle_twin(self, partition: str) -> tuple[int, tuple[SourceRef, ...]] | None:
        """The highest measured draw of an IDLE node in ``partition`` whose (cpus, gres, tres,
        partitions) match every node of the partition with jobs (S3-10)."""
        members = [n for n in self.st.nodes.values() if partition in n.partitions]
        idle = [
            n
            for n in members
            if n.state == ("IDLE",) and n.watts.state == "value" and n.watts.number > 0
        ]
        busy_keys = {n.twin_key for n in members if n.state != ("IDLE",)}
        if not idle or len(busy_keys) > 1:
            return None
        twins = [n for n in idle if n.twin_key in busy_keys] if busy_keys else idle
        if not twins:
            return None
        best = max(twins, key=lambda n: (n.watts.number, n.name))
        return best.watts.number, (
            best.watts.ref(),
            _policy_ref("/residual_basis", "idle_twin_measured"),
        )

    # ---------------------------------------------------------------- curves, rungs, availability

    def _declare(self, classes: dict[ClassKey, _ClassNodes], horizon: int, floor_kw: int) -> Draft:
        start = _ceil_grid(self.captured)
        halves = [start + k * _HALF_HOUR for k in range(horizon // RESOLUTION_MIN)]
        valid_until = halves[-1] + _HALF_HOUR
        prefix = SYNTHETIC_PREFIX if self.synthetic else ""

        sla_classes: list[dict[str, Any]] = []
        envelopes: list[dict[str, Any]] = []
        offered: dict[datetime, set[str]] = {t: set() for t in halves}
        top_first_kw: dict[datetime, int] = dict.fromkeys(halves, 0)
        class_order: list[tuple[str, list[tuple[tuple[int, ...], str]]]] = []
        for key in sorted(classes):
            cls = classes[key]
            terms = cls.terms
            assert not isinstance(terms.hold_points_min, Unknown)
            assert isinstance(terms.stop_settle_s, int) and isinstance(terms.min_notice_s, int)
            class_id = prefix + "slurm." + ".".join(encode_name(x) for x in key)
            if self.synthetic != key[1].startswith(SYNTHETIC_PREFIX):
                raise MappingRefused(
                    reason(
                        RefusalCode.LABEL_MISMATCH,
                        "policy:" + cls.terms_ptr,
                        f"QOS {key[1]!r} is the class's operator label: it starts "
                        f"{SYNTHETIC_PREFIX} exactly when the record is synthetic",
                    )
                )
            ramp = self._ramp(cls, halves)
            holds = [d for d in terms.hold_points_min if ramp.value < d * 60]
            if not holds:
                self.skipped.append(
                    reason(
                        RefusalCode.UNMAPPABLE_WORKLOAD,
                        "policy:" + cls.terms_ptr,
                        f"class {key}: ramp {ramp.value} s does not finish inside any stated hold",
                    )
                )
                continue
            curves: dict[datetime, _Curve] = {t: self._curve(cls, t, holds) for t in halves}
            rungs: dict[tuple[tuple[int, int], ...], _Curve] = {}
            for t in halves:
                c = curves[t]
                if c.points and c.points not in rungs:
                    rungs[c.points] = c
            if not rungs:
                self.skipped.append(
                    reason(
                        RefusalCode.ZERO_OFFER,
                        "policy:" + cls.terms_ptr,
                        f"class {key}: every hold rounds to 0 kW",
                    )
                )
                continue
            ids = _rung_ids(class_id, list(rungs))
            group: Any = (
                "xg." + class_id
                if len(rungs) >= 2
                else {"kind": "not_applicable", "reason": "the class has one rung"}
            )
            response = {
                "kind": "response_terms",
                "min_notice_s": self.ledger.derive(
                    "S4-1 min_notice_s from policy",
                    terms.min_notice_s,
                    "none",
                    [_policy_ref(cls.terms_ptr + "/min_notice_s", terms.min_notice_s)],
                ),
                "ramp_s": ramp,
            }
            sla_class_draft, energy = self._sla_class(cls, class_id)
            sla_classes.append(sla_class_draft)
            order: list[tuple[tuple[int, ...], str]] = []
            for pts, curve in rungs.items():
                eid = ids[pts]
                order.append((curve.step(holds), eid))
                envelopes.append(
                    {
                        "envelope_id": eid,
                        "sla_class_id": class_id,
                        "response": response,
                        "price": self._price(cls),
                        "curve": {
                            "kind": "absolute_kw",
                            "points": [
                                {
                                    "max_hold_min": self.ledger.derive(
                                        "S3-14 hold from policy",
                                        h,
                                        "none",
                                        [_policy_ref(cls.terms_ptr + "/hold_points_min", h)],
                                    ),
                                    "turn_down_kw": tr,
                                }
                                for (h, _), tr in zip(curve.points, curve.traced, strict=True)
                            ],
                        },
                        "rebound": self._rebound(cls, energy),
                        "exclusion_group": group,
                    }
                )
            for t in halves:
                v = curves[t].step(holds)
                hit = [
                    ids[pts]
                    for pts, c in rungs.items()
                    if all(a <= b for a, b in zip(c.step(holds), v, strict=True))
                ]
                offered[t].update(hit)
                if curves[t].points:
                    top_first_kw[t] += curves[t].points[0][1]
            order.sort(key=lambda x: (tuple(-k for k in x[0]), x[1]))
            class_order.append((class_id, order))

        if not envelopes:
            raise MappingRefused(*self._zero_or_skips())

        overrides = self._overrides(halves, offered, classes)
        if not any(o["state"]["kind"] == "offered" for o in overrides):
            raise MappingRefused(*self._zero_or_skips())
        envelopes.sort(key=lambda e: e["envelope_id"])
        sla_classes.sort(key=lambda c: c["class_id"])
        merit = self._merit(class_order)
        peak_t = max(halves, key=lambda t: (top_first_kw[t], -halves.index(t)))
        peak = top_first_kw[peak_t]
        cap_refs = [
            _rule_ref(
                "S5-C5", f"max over half-hours of the summed first-point kW at {peak_t.isoformat()}"
            )
        ]
        cap_exact = peak
        cap_rule = "S5-C5 cap = max over half-hours of the summed first-point kW"
        pc = self.policy.max_concurrent_turn_down_kw
        if isinstance(pc, int) and pc < peak:
            cap_exact, cap_rule = pc, "S5-C5 cap = the policy cap (below the summed kW)"
            cap_refs = [_policy_ref("/max_concurrent_turn_down_kw", pc)]
        return Draft(
            body={
                "contract": "IC-9",
                "contract_version": "1.0.0",
                "record_class": self.record_class,
                "declaration_id": "slurm-" + self.state.content_hash()[7:23],
                "site_id": self.policy.site_id,
                "operator_id": self.policy.operator_id,
                "issued_at": _iso(self.captured),
                "valid_from": _iso(start),
                "valid_until": _iso(valid_until),
                "supersedes": (
                    self.policy.supersedes
                    if isinstance(self.policy.supersedes, str)
                    else self.policy.supersedes.model_dump(mode="json")
                ),
                "measurement_point": "it_load",
                "clock": "Europe/London",
                "sla_classes": sla_classes,
                "envelopes": envelopes,
                "availability": self._template(overrides),
                "resolved_hash": "",
                "stacking": {
                    "kind": "stacking",
                    "merit_order": merit,
                    "max_concurrent_turn_down_kw": self.ledger.derive(
                        cap_rule, cap_exact, "none", cap_refs
                    ),
                },
                "it_reference": {
                    "kind": "it_reference",
                    "retained_it_floor_kw": self.ledger.derive(
                        "S3-20 retained IT floor from policy",
                        floor_kw,
                        "none",
                        [_policy_ref("/retained_it_floor_kw", floor_kw)],
                    ),
                },
                "basis": {
                    "kind": "operator_declared",
                    "supporting_evidence": [
                        r.model_dump(mode="json")
                        for r in sorted(
                            self.policy.supporting_evidence, key=lambda r: r.evidence_hash
                        )
                    ],
                },
            },
            skipped=tuple(self.skipped),
            withheld=tuple(self.withheld),
        )

    def _zero_or_skips(self) -> tuple[RefusalReason, ...]:
        zero = tuple(r for r in self.skipped if r.code == RefusalCode.ZERO_OFFER)
        return zero or self._first_skip_code()

    def _ramp(self, cls: _ClassNodes, halves: list[datetime]) -> Traced:
        terms = cls.terms
        assert isinstance(terms.stop_settle_s, int)
        settle_ref = _policy_ref(cls.terms_ptr + "/stop_settle_s", terms.stop_settle_s)
        if cls.key[2] == "suspend":
            return self.ledger.derive(
                "S4-2 ramp = stop_settle_s (suspend: no grace)",
                terms.stop_settle_s,
                "up",
                [settle_ref],
            )
        grace, refs = 0, tuple[SourceRef, ...]()
        first, last = halves[0], halves[-1] + _HALF_HOUR
        for jobs in cls.jobs_on.values():
            for a in jobs:
                live = a.p < last and (a.e is None or a.e >= first + _HALF_HOUR)
                if live and (a.grace_s > grace or not refs):
                    grace, refs = max(grace, a.grace_s), a.grace_refs or refs
        return self.ledger.derive(
            "S4-2 ramp = max job grace (QOS or partition) + stop_settle_s",
            grace + terms.stop_settle_s,
            "up",
            [*refs, settle_ref],
        )

    def _curve(self, cls: _ClassNodes, t: datetime, holds: list[int]) -> _Curve:
        """K(t, d) for each hold, cleaned (S3-15): zeros dropped, equal kW keeps the longest."""
        raw: list[tuple[int, int, Traced]] = []
        for d in holds:
            need = t + _HALF_HOUR + timedelta(minutes=d)
            shed, refs = 0, list[SourceRef]()
            for node, w, node_refs in cls.nodes:
                jobs = cls.jobs_on[node.name]
                if all(a.p <= t and (a.e is None or a.e >= need) for a in jobs):
                    shed += w
                    refs.extend(node_refs)
                    for a in jobs:
                        refs.extend((*a.p_refs, *a.e_refs))
            if not refs:
                refs.append(_rule_ref("S5-C2", "no node qualifies"))
            traced = self.ledger.derive(
                f"S5-C2 K(t={_iso(t)}, d={d}) = floor(sum shed W / 1000)",
                Fraction(shed, 1000),
                "down",
                _dedup(refs),
            )
            raw.append((d, traced.value, traced))
        kept: list[tuple[int, int, Traced]] = []
        for d, kw, tr in raw:
            if kw <= 0:
                continue
            if kept and kept[-1][1] == kw:
                kept[-1] = (d, kw, tr)
            else:
                kept.append((d, kw, tr))
        return _Curve(
            points=tuple((d, kw) for d, kw, _ in kept), traced=tuple(tr for _, _, tr in kept)
        )

    def _sla_class(self, cls: _ClassNodes, class_id: str) -> tuple[dict[str, Any], str]:
        terms, ptr = cls.terms, cls.terms_ptr
        outcome = cls.key[2]
        energy: dict[str, Any]
        if outcome in ("suspend", "requeue"):
            action = "deferred"
            w = terms.return_window_min
            energy = {
                "kind": "returns_later",
                "return_window_min": (
                    self.ledger.derive(
                        "S3-03 return window from policy",
                        w,
                        "none",
                        [_policy_ref(ptr + "/return_window_min", w)],
                    )
                    if isinstance(w, int)
                    else _unknown(w, "the policy states no return window")
                ),
            }
        else:
            action = "dropped"
            r = terms.dropped_energy_return
            energy = (
                {"kind": "no_return"}
                if r == "no_return"
                else _unknown(r, "the policy does not state that dropped work never returns")
            )
        limits = terms.call_limits
        return (
            {
                "class_id": class_id,
                "operator_label": cls.key[1],
                "workload_action": action,
                "energy_return": energy,
                "call_limits": (
                    self._limits(limits, ptr + "/call_limits")
                    if isinstance(limits, CallLimits)
                    else {
                        "kind": "call_limits",
                        "max_events": _unknown(limits, ""),
                        "max_cumulative": _unknown(limits, ""),
                        "min_rest": _unknown(limits, ""),
                    }
                ),
                "sla_evidence": terms.sla_evidence.model_dump(mode="json"),
            },
            energy["kind"],
        )

    def _limits(self, limits: CallLimits, ptr: str) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "call_limits"}
        for name in ("max_events", "max_cumulative", "min_rest"):
            v = getattr(limits, name).model_dump(mode="json")
            for k, x in list(v.items()):
                if isinstance(x, int) and not isinstance(x, bool):
                    v[k] = self.ledger.derive(
                        f"S3 call limit {name}.{k} from policy",
                        x,
                        "none",
                        [_policy_ref(f"{ptr}/{name}/{k}", x)],
                    )
            out[name] = v
        return out

    def _rebound(self, cls: _ClassNodes, energy: str) -> dict[str, Any]:
        if energy == "no_return":
            return {"kind": "not_applicable", "reason": "dropped work's energy does not return"}
        if energy == "unknown":
            return {"kind": "unknown", "reason": "whether the class's energy returns is unknown"}
        r, ptr = cls.terms.rebound, cls.terms_ptr + "/rebound"
        if not isinstance(r, PolicyRebound):
            return {"kind": "unknown", "reason": "the policy states no rebound size"}

        def part(name: str, v: int | Unknown) -> Any:
            if isinstance(v, int):
                return self.ledger.derive(
                    f"S3-04 rebound {name} from policy",
                    v,
                    "none",
                    [_policy_ref(f"{ptr}/{name}", v)],
                )
            return v.model_dump(mode="json")

        return {
            "kind": "rebound_size",
            "recovery_permille": part("recovery_permille", r.recovery_permille),
            "max_rebound_kw": part("max_rebound_kw", r.max_rebound_kw),
        }

    def _price(self, cls: _ClassNodes) -> dict[str, Any]:
        p, ptr = cls.terms.price, cls.terms_ptr + "/price"
        if not isinstance(p, PolicyPrice):
            return p.model_dump(mode="json")
        if self.synthetic:
            basis: dict[str, Any] = {"kind": "synthetic_placeholder"}
        elif isinstance(p.stated_on, NotApplicable):
            return {"kind": "unknown", "reason": "the operator's price has no stated date"}
        else:
            basis = {"kind": "indicative", "stated_on": p.stated_on.isoformat()}
        parts: dict[str, Any] = {}
        for name, field_name, kind, key in (
            (
                "availability",
                "availability_pence_per_mw_per_hour",
                "availability_price",
                "pence_per_mw_per_hour",
            ),
            ("utilisation", "utilisation_pence_per_mwh", "utilisation_price", "pence_per_mwh"),
            ("per_event", "per_event_pence", "event_fee", "pence"),
        ):
            v = getattr(p, field_name)
            if isinstance(v, int):
                parts[name] = {
                    "kind": kind,
                    key: self.ledger.derive(
                        f"S3 price {name} from policy",
                        v,
                        "none",
                        [_policy_ref(f"{ptr}/{field_name}", v)],
                    ),
                }
            else:
                parts[name] = v.model_dump(mode="json")
        if all(v.get("kind") == "unknown" for v in parts.values()):
            return {"kind": "unknown", "reason": "the policy states no price component"}
        return {"kind": "price_offer", "currency": "GBP", **parts, "price_basis": basis}

    def _merit(self, class_order: list[tuple[str, list[tuple[tuple[int, ...], str]]]]) -> list[str]:
        listed = self.policy.merit_order
        rank = {c: i for i, c in enumerate(listed)} if isinstance(listed, tuple) else {}
        ordered = sorted(class_order, key=lambda x: (x[0] not in rank, rank.get(x[0], 0), x[0]))
        return [eid for _, order in ordered for _, eid in order]

    def _overrides(
        self,
        halves: list[datetime],
        offered: dict[datetime, set[str]],
        classes: dict[ClassKey, _ClassNodes],
    ) -> list[dict[str, Any]]:
        contributing = {n.name for c in classes.values() for n, _, _ in c.nodes}
        maint = [
            r
            for r in self.st.reservations
            if r.maint and set(r.nodes) & contributing and r.start.state == "value"
        ]
        states: list[tuple[dict[str, Any], str]] = []
        for t in halves:
            if offered[t]:
                states.append(
                    (
                        {"kind": "offered", "envelope_ids": sorted(offered[t])},
                        f"offered on slurmrestd state captured {_iso(self.captured)}",
                    )
                )
                continue
            hit = sorted(
                r.name
                for r in maint
                if _epoch(r.start.number) < t + _HALF_HOUR
                and (r.end.state != "value" or _epoch(r.end.number) > t)
            )
            if hit:
                states.append(
                    (
                        {"kind": "unavailable", "reason_code": UnavailableReason.MAINTENANCE.value},
                        f"Slurm MAINT reservation(s) {hit}",
                    )
                )
            else:
                states.append(
                    (
                        {
                            "kind": "unavailable",
                            "reason_code": UnavailableReason.WORKLOAD_PROTECTED.value,
                        },
                        "no eligible workload in the captured state",
                    )
                )
        out: list[dict[str, Any]] = []
        k = 0
        while k < len(halves):
            j = k
            while j + 1 < len(halves) and states[j + 1][0] == states[k][0]:
                j += 1
            reasons = sorted({states[i][1] for i in range(k, j + 1)})
            out.append(
                {
                    "override_id": "ov." + halves[k].strftime("%Y%m%dT%H%MZ"),
                    "start": _iso(halves[k]),
                    "end": _iso(halves[j] + _HALF_HOUR),
                    "state": states[k][0],
                    "reason": "; ".join(reasons),
                }
            )
            k = j + 1
        return out

    def _template(self, overrides: list[dict[str, Any]]) -> dict[str, Any]:
        def const(rule: str, v: int) -> Traced:
            return self.ledger.derive(rule, v, "none", [_rule_ref(rule, v)])

        return {
            "seasons": [
                {
                    "season_id": "all-year",
                    "months": [const("S4-4 one season: every month", m) for m in range(1, 13)],
                }
            ],
            "day_groups": [
                {
                    "day_group_id": "all-week",
                    "iso_weekdays": [
                        const("S4-4 one day group: every weekday", d) for d in range(1, 8)
                    ],
                }
            ],
            "bands": [
                {
                    "band_id": "all-day",
                    "start_minute": const("S4-4 one band: the whole day", 0),
                    "end_minute": const("S4-4 one band: the whole day", 1440),
                }
            ],
            "cells": [
                {
                    "season_id": "all-year",
                    "day_group_id": "all-week",
                    "band_id": "all-day",
                    "state": {
                        "kind": "unknown",
                        "reason": "a snapshot evidences no recurring "
                        "pattern: every offered half-hour is a dated override",
                    },
                }
            ],
            "overrides": overrides,
        }


def _unknown(v: Any, fallback: str) -> dict[str, Any]:
    if isinstance(v, Unknown):
        return v.model_dump(mode="json")
    return {"kind": "unknown", "reason": fallback or "not stated"}


def _dedup(refs: Iterable[SourceRef]) -> list[SourceRef]:
    seen: dict[str, SourceRef] = {}
    for r in refs:
        seen.setdefault(r.model_dump_json(), r)
    return [seen[k] for k in sorted(seen)]


def _rung_ids(class_id: str, rungs: list[tuple[tuple[int, int], ...]]) -> dict[Any, str]:
    """Content-addressed rung ids (S5-C2), the hex extended until unique."""
    digests = {
        pts: hashlib.sha256(json.dumps([list(p) for p in pts]).encode()).hexdigest()
        for pts in rungs
    }
    n = 8
    while True:
        ids = {pts: f"{class_id}.r{pts[0][1]:06d}.{digests[pts][:n]}" for pts in rungs}
        if len(set(ids.values())) == len(ids):
            return ids
        n += 1


def _ceil_grid(t: datetime) -> datetime:
    t = t.astimezone(UTC)
    floor = t.replace(minute=t.minute - t.minute % RESOLUTION_MIN, second=0, microsecond=0)
    return floor if floor == t else floor + _HALF_HOUR


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
