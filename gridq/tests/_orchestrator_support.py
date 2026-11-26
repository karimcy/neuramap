"""Synthetic slurmrestd ``v0.0.46`` state documents and adapter policies for the WP-SITE-08 tests.

**Everything here is synthetic.** The bodies follow the response shapes Slurm 26.11 documents for
data parser ``v0.0.46`` (https://slurm.schedmd.com/archive/slurm-26.11.0/rest_api.html:
``v0.0.46_openapi_job_info_resp``, ``v0.0.46_openapi_nodes_resp``,
``v0.0.46_openapi_partition_resp``, ``v0.0.46_openapi_reservation_resp``,
``v0.0.46_openapi_slurmdbd_qos_resp``), but no body was recorded from a cluster: the node names,
jobs, watts, grace and exempt times, residual draws, holds, notices, SLA limits and prices are
invented to exercise the adapter's rules. QOS, site and operator names start ``SYNTHETIC-``; each
state's ``capture_note`` and each policy's ``policy_note`` start ``SYNTHETIC``; every price is a
placeholder (the declaration says ``synthetic_placeholder``) and every evidence hash is the SHA-256
of a placeholder text. None describes a real operator, cluster, workload or tariff.

The reference state (`reference_state`) and policy (`reference_policy`) are committed as JSON in
``tests/fixtures/orchestrators/``; `test_orchestrator_adapters` asserts the committed files equal a
fresh build. Regenerate with ``python tests/_orchestrator_support.py``.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "orchestrators"
STATE_FILE = FIXTURES / "slurm_state_reference.synthetic.json"
POLICY_FILE = FIXTURES / "slurm_policy_reference.synthetic.json"

DOC_URL = "https://slurm.schedmd.com/archive/slurm-26.11.0/rest_api.html"
JOBS = "/slurm/v0.0.46/jobs/"
NODES = "/slurm/v0.0.46/nodes/"
PARTITIONS = "/slurm/v0.0.46/partitions/"
RESERVATIONS = "/slurm/v0.0.46/reservations/"
QOS = "/slurmdb/v0.0.46/qos/"
PATHS = sorted((JOBS, NODES, PARTITIONS, RESERVATIONS, QOS))

CAPTURED = datetime(2027, 3, 1, 9, 10, tzinfo=UTC)
SITE = "SYNTHETIC-DC-SLURM-CAMPUS"
_M = timedelta(minutes=1)
OPERATOR = "SYNTHETIC-OPERATOR-SLURM"


def epoch(t: datetime) -> int:
    return int(t.timestamp())


def num(n: int | None, *, infinite: bool = False) -> dict[str, Any]:
    """A slurmrestd ``{set, infinite, number}`` value; ``None`` is unset."""
    if infinite:
        return {"set": True, "infinite": True, "number": 0}
    if n is None:
        return {"set": False, "infinite": False, "number": 0}
    return {"set": True, "infinite": False, "number": n}


def placeholder_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC PLACEHOLDER: {label}".encode()).hexdigest()


def _meta(command: list[str], *, db: bool = False) -> dict[str, Any]:
    return {
        "plugin": {
            "type": "openapi/slurmdbd" if db else "openapi/slurmctld",
            "name": "Slurm OpenAPI slurmdbd" if db else "Slurm OpenAPI slurmctld",
            "data_parser": "data_parser/v0.0.46",
            "accounting_storage": "accounting_storage/slurmdbd",
        },
        "client": {"source": "[synthetic]:0", "user": "SYNTHETIC-reader", "group": "SYNTHETIC"},
        "command": command,
        "slurm": {
            "version": {"major": "26", "micro": "0", "minor": "11"},
            "release": "26.11.0",
            "cluster": "SYNTHETIC-cluster",
        },
    }


# --------------------------------------------------------------------------------------------
# A compact cluster description, rendered into the five response bodies
# --------------------------------------------------------------------------------------------


@dataclass
class Job:
    job_id: int
    partition: str
    qos: str
    nodes: str
    state: tuple[str, ...] = ("RUNNING",)
    requeue: bool = True
    start: datetime | None = None
    end: datetime | None = None
    time_limit_min: int | None = None
    time_limit_infinite: bool = False
    preemptable: datetime | None = None


@dataclass
class Node:
    name: str
    partitions: tuple[str, ...]
    watts: int | None
    state: tuple[str, ...] = ("ALLOCATED",)
    collected: datetime | None = None
    cpus: int = 64
    gres: str = "gpu:SYNTHETIC-gpu:4"
    tres: str = "cpu=64,mem=512000M,billing=64,gres/gpu=4"


@dataclass
class Partition:
    name: str
    preempt_mode: tuple[str, ...] = ("CLUSTER_GLOBAL",)
    grace_time: int = 0


@dataclass
class Qos:
    name: str
    mode: tuple[str, ...] = ("CLUSTER_GLOBAL",)
    preempt_list: tuple[str, ...] = ()
    exempt_s: int | None = None
    exempt_infinite: bool = False
    grace_time: int = 0


@dataclass
class Reservation:
    name: str
    nodes: str
    start: datetime
    end: datetime
    flags: tuple[str, ...] = ("MAINT", "SPEC_NODES")
    partition: str = "gpu"


@dataclass
class Cluster:
    captured: datetime
    jobs: list[Job] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    qos: list[Qos] = field(default_factory=list)
    reservations: list[Reservation] = field(default_factory=list)
    note: str = "SYNTHETIC slurmrestd v0.0.46 bodies written for the WP-SITE-08 tests"


def _job(j: Job) -> dict[str, Any]:
    start = j.start
    end = j.end
    return {
        "account": "SYNTHETIC-account",
        "end_time": num(epoch(end) if end else None),
        "gres_detail": [],
        "job_id": j.job_id,
        "job_state": list(j.state),
        "name": f"SYNTHETIC-job-{j.job_id}",
        "nodes": j.nodes,
        "partition": j.partition,
        "preemptable_time": num(epoch(j.preemptable) if j.preemptable else None),
        "qos": j.qos,
        "requeue": j.requeue,
        "start_time": num(epoch(start) if start else None),
        "time_limit": num(j.time_limit_min, infinite=j.time_limit_infinite),
        "tres_alloc_str": "cpu=64,mem=512000M,node=1,billing=64,gres/gpu=4",
        "user_name": "SYNTHETIC-user",
    }


def _node(n: Node, captured: datetime) -> dict[str, Any]:
    collected = n.collected or captured - timedelta(seconds=20)
    return {
        "alloc_cpus": n.cpus if "ALLOCATED" in n.state else 0,
        "cpus": n.cpus,
        "energy": {
            "average_watts": n.watts or 0,
            "base_consumed_energy": 0,
            "consumed_energy": 0,
            "current_watts": num(n.watts),
            "last_collected": epoch(collected),
            "previous_consumed_energy": 0,
        },
        "gres": n.gres,
        "gres_used": n.gres if "ALLOCATED" in n.state else "",
        "name": n.name,
        "partitions": list(n.partitions),
        "state": list(n.state),
        "tres": n.tres,
        "tres_used": n.tres if "ALLOCATED" in n.state else "",
    }


def _partition(p: Partition, nodes: list[Node]) -> dict[str, Any]:
    members = [n.name for n in nodes if p.name in n.partitions]
    return {
        "grace_time": p.grace_time,
        "name": p.name,
        "nodes": {
            "allowed_allocation": "",
            "configured": ",".join(members),
            "total": len(members),
        },
        "preempt_mode": list(p.preempt_mode),
        "priority": {"job_factor": 1, "tier": 1},
        "qos": {"allowed": "", "assigned": "", "deny": ""},
        "suspend_time": num(None),
        "timeouts": {"resume": num(None), "suspend": num(None)},
    }


def _qos(q: Qos) -> dict[str, Any]:
    return {
        "description": "SYNTHETIC QOS",
        "flags": [],
        "limits": {"factor": num(None), "grace_time": q.grace_time},
        "name": q.name,
        "preempt": {
            "exempt_time": num(q.exempt_s, infinite=q.exempt_infinite),
            "list": list(q.preempt_list),
            "mode": list(q.mode),
        },
        "priority": num(1),
    }


def _reservation(r: Reservation) -> dict[str, Any]:
    return {
        "end_time": num(epoch(r.end)),
        "flags": list(r.flags),
        "name": r.name,
        "node_list": r.nodes,
        "partition": r.partition,
        "start_time": num(epoch(r.start)),
    }


def payload(c: Cluster) -> dict[str, Any]:
    stamp = num(epoch(c.captured))
    return {
        JOBS: {
            "errors": [],
            "jobs": [_job(j) for j in c.jobs],
            "last_backfill": stamp,
            "last_update": stamp,
            "meta": _meta(["job", "list"]),
            "warnings": [],
        },
        NODES: {
            "errors": [],
            "last_update": stamp,
            "meta": _meta(["node", "list"]),
            "nodes": [_node(n, c.captured) for n in c.nodes],
            "warnings": [],
        },
        PARTITIONS: {
            "errors": [],
            "last_update": stamp,
            "meta": _meta(["partition", "list"]),
            "partitions": [_partition(p, c.nodes) for p in c.partitions],
            "warnings": [],
        },
        RESERVATIONS: {
            "errors": [],
            "last_update": stamp,
            "meta": _meta(["reservation", "list"]),
            "reservations": [_reservation(r) for r in c.reservations],
            "warnings": [],
        },
        QOS: {
            "errors": [],
            "meta": _meta(["qos", "list"], db=True),
            "qos": [_qos(q) for q in c.qos],
            "warnings": [],
        },
    }


def state_dict(c: Cluster, *, label: str = "synthetic") -> dict[str, Any]:
    return {
        "kind": "orchestrator_state",
        "label": label,
        "source": {
            "kind": "api_source",
            "api_id": "slurmrestd",
            "api_version": "v0.0.46",
            "doc_url": DOC_URL,
            "resource_paths": PATHS,
        },
        "captured_at": c.captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_note": c.note,
        "payload": payload(c),
    }


# --------------------------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------------------------


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic: {reason}"}


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": f"synthetic: {reason}"}


def evidence(label: str) -> dict[str, str]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": "1.1.0",
        "evidence_hash": placeholder_hash(label),
    }


def class_terms(
    partition: str,
    qos: str,
    outcome: str,
    *,
    holds: list[int] | None = None,
    notice: int | None = 300,
    settle: int | None = 60,
    mechanism: str | None = "SYNTHETIC: a MAINT reservation on the freed nodes for the hold",
    with_price: bool = True,
) -> dict[str, Any]:
    returns = outcome in ("suspend", "requeue")
    return {
        "kind": "slurm_class_terms",
        "partition": partition,
        "qos": qos,
        "outcome": outcome,
        "hold_mechanism": mechanism if mechanism is not None else _unknown("no hold mechanism"),
        "hold_points_min": holds if holds is not None else [30, 60, 120, 240],
        "min_notice_s": notice if notice is not None else _unknown("notice not stated"),
        "stop_settle_s": settle if settle is not None else _unknown("settle not stated"),
        "return_window_min": 720 if returns else _na("dropped work does not return"),
        "dropped_energy_return": _na("work that returns") if returns else "no_return",
        "rebound": (
            {"kind": "policy_rebound", "recovery_permille": 1000, "max_rebound_kw": 50}
            if returns
            else _na("no return")
        ),
        "call_limits": {
            "kind": "call_limits",
            "max_events": {"kind": "event_budget", "max_events": 4, "window_days": 7},
            "max_cumulative": {"kind": "duration_budget", "max_minutes": 600, "window_days": 7},
            "min_rest": {"kind": "minimum_rest", "minutes": 120},
        },
        "price": (
            {
                "kind": "policy_price",
                "availability_pence_per_mw_per_hour": 100,
                "utilisation_pence_per_mwh": 1000,
                "per_event_pence": _na("no per-event fee"),
                "stated_on": _na("a synthetic placeholder has no date"),
            }
            if with_price
            else _unknown("no price stated")
        ),
        "sla_evidence": evidence(f"SLA {qos} {outcome}"),
    }


def policy_dict(
    classes: list[dict[str, Any]],
    *,
    residuals: list[dict[str, Any]] | None = None,
    residual_basis: str = "stated",
    preempt_type: str = "preempt/qos",
    cluster_mode: list[str] | None = None,
    cluster_exempt_s: int | None = 0,
    horizon: int = 360,
    floor_kw: int | None = 10,
    cap_kw: int | None = None,
    merit: list[str] | None = None,
    label: str = "synthetic",
    site: str | None = SITE,
    operator: str | None = OPERATOR,
    max_age_s: int | None = 120,
) -> dict[str, Any]:
    return {
        "kind": "slurm_adapter_policy",
        "label": label,
        "policy_note": (
            "SYNTHETIC adapter policy for the WP-SITE-08 tests"
            if label == "synthetic"
            else "Operator policy (in-test document, never committed)"
        ),
        "site_id": site if site is not None else _unknown("site not stated"),
        "operator_id": operator if operator is not None else _unknown("operator not stated"),
        "retained_it_floor_kw": floor_kw if floor_kw is not None else _unknown("floor"),
        "max_concurrent_turn_down_kw": cap_kw if cap_kw is not None else _na("no own cap"),
        "merit_order": merit if merit is not None else _na("by class id"),
        "supersedes": _na("first declaration"),
        "supporting_evidence": [],
        "preempt_type": preempt_type,
        "cluster_preempt_mode": cluster_mode if cluster_mode is not None else ["REQUEUE"],
        "cluster_exempt_time_s": (
            cluster_exempt_s if cluster_exempt_s is not None else _unknown("exempt not stated")
        ),
        "max_power_sample_age_s": max_age_s if max_age_s is not None else _unknown("age"),
        "residual_basis": residual_basis,
        "residuals": (
            residuals
            if residuals is not None
            else [
                {
                    "kind": "partition_residual",
                    "partition": "gpu",
                    "suspended_w": 2500,
                    "idle_w": 1500,
                },
            ]
        ),
        "horizon_min": horizon,
        "classes": classes,
    }


# --------------------------------------------------------------------------------------------
# The reference cluster
# --------------------------------------------------------------------------------------------

HIGH = "SYNTHETIC-high"
BATCH = "SYNTHETIC-batch"
SCAVENGER = "SYNTHETIC-scavenger"
INFER = "SYNTHETIC-inference"


def reference_cluster() -> Cluster:
    """Eight busy GPU nodes and one idle one under QOS preemption (``preempt/qos``):

    - ``SYNTHETIC-batch`` jobs are requeued (grace 120 s): gpu[001-002] (until 14:00, but a MAINT
      reservation from 12:00 clamps it) and gpu003 (until 10:40);
    - ``SYNTHETIC-scavenger`` jobs are cancelled: gpu[004-005] (until 13:10), one non-requeueable;
    - ``SYNTHETIC-inference`` is suspended: gpu006 (unbounded time limit), exempt for 30 minutes
      from its start at 09:00;
    - gpu007 runs a batch and a scavenger job (shared: withheld); gpu008 has no power reading
      (withheld); a ``SYNTHETIC-high`` job on gpu010 is not preemptable (protected);
    - a pending job and a completed one are ignored.
    """
    t = CAPTURED
    c = Cluster(captured=t)
    c.partitions = [Partition("gpu", grace_time=0)]
    c.qos = [
        Qos(HIGH, mode=("CLUSTER_GLOBAL",), preempt_list=(BATCH, INFER, SCAVENGER)),
        Qos(BATCH, mode=("REQUEUE",), grace_time=120),
        Qos(SCAVENGER, mode=("CANCEL",), grace_time=60),
        Qos(INFER, mode=("SUSPEND", "GANG"), exempt_s=1800),
    ]
    c.nodes = [
        Node("gpu001", ("gpu",), 6400),
        Node("gpu002", ("gpu",), 6300),
        Node("gpu003", ("gpu",), 6200),
        Node("gpu004", ("gpu",), 5800),
        Node("gpu005", ("gpu",), 5900),
        Node("gpu006", ("gpu",), 6100),
        Node("gpu007", ("gpu",), 6000),
        Node("gpu008", ("gpu",), None),
        Node("gpu009", ("gpu",), 1400, state=("IDLE",)),
        Node("gpu010", ("gpu",), 6500),
    ]
    h = timedelta(hours=1)
    c.jobs = [
        Job(
            101,
            "gpu",
            BATCH,
            "gpu[001-002]",
            start=t - 2 * h,
            end=t + 4 * h + 50 * _M,
            preemptable=t - 2 * h,
        ),
        Job(102, "gpu", BATCH, "gpu003", start=t - h, end=t + 90 * _M, preemptable=t - h),
        Job(
            103,
            "gpu",
            SCAVENGER,
            "gpu004",
            start=t - h,
            end=t + 4 * h,
            requeue=False,
            preemptable=t - h,
        ),
        Job(104, "gpu", SCAVENGER, "gpu005", start=t - h, time_limit_min=300, requeue=False),
        Job(105, "gpu", INFER, "gpu006", start=t - 10 * _M, time_limit_infinite=True),
        Job(106, "gpu", BATCH, "gpu007", start=t - h, end=t + 3 * h, preemptable=t - h),
        Job(107, "gpu", SCAVENGER, "gpu007", start=t - h, end=t + 3 * h, preemptable=t - h),
        Job(108, "gpu", BATCH, "gpu008", start=t - h, end=t + 3 * h, preemptable=t - h),
        Job(109, "gpu", HIGH, "gpu010", start=t - h, end=t + 3 * h),
        Job(110, "gpu", BATCH, "", state=("PENDING",)),
        Job(111, "gpu", BATCH, "gpu003", state=("COMPLETED",), start=t - 3 * h, end=t - h),
    ]
    c.reservations = [
        Reservation(
            "SYNTHETIC-maint-gpu001-002", "gpu[001-002]", t + 2 * h + 50 * _M, t + 4 * h + 50 * _M
        ),
    ]
    c.note = (
        "SYNTHETIC slurmrestd v0.0.46 bodies (not recorded from any cluster): the WP-SITE-08 "
        "reference state, written to exercise requeue, cancel and suspend classes, a MAINT "
        "clamp, a shared node and an unmeasured node"
    )
    return c


def reference_classes() -> list[dict[str, Any]]:
    return [
        class_terms("gpu", BATCH, "requeue"),
        class_terms("gpu", SCAVENGER, "cancel"),
        class_terms("gpu", INFER, "suspend", holds=[30, 60, 120]),
    ]


def reference_state() -> dict[str, Any]:
    return state_dict(reference_cluster())


def reference_policy() -> dict[str, Any]:
    return policy_dict(reference_classes())


# --------------------------------------------------------------------------------------------
# Random clusters for the property tests (seeded; ``hypothesis`` is not a dependency)
# --------------------------------------------------------------------------------------------

_MODES = (
    ("REQUEUE",),
    ("CANCEL",),
    ("SUSPEND", "GANG"),
    ("OFF",),
    ("CLUSTER_GLOBAL",),
    (),
    ("REQUEUE", "WITHIN"),
    ("SUSPEND", "CANCEL"),
    ("GANG",),
)


def random_cluster(rng: random.Random) -> tuple[Cluster, dict[str, Any]]:
    """A varied synthetic cluster and policy: modes, exempt times, ends, watts (some unset or
    stale), shared nodes, idle twins, MAINT reservations, and policy facts sometimes unstated."""
    captured = CAPTURED + timedelta(minutes=rng.randrange(0, 24 * 60), seconds=rng.randrange(60))
    c = Cluster(captured=captured)
    parts = ["gpu", "cpu"][: rng.randint(1, 2)]
    c.partitions = [
        Partition(p, preempt_mode=rng.choice(_MODES), grace_time=rng.choice((0, 30, 300)))
        for p in parts
    ]
    names = [f"SYNTHETIC-q{i}" for i in range(rng.randint(1, 4))]
    c.qos = [
        Qos(
            q,
            mode=rng.choice(_MODES),
            preempt_list=tuple(o for o in names if o != q and rng.random() < 0.6),
            exempt_s=rng.choice((None, 0, 600, 3600)),
            exempt_infinite=rng.random() < 0.05,
            grace_time=rng.choice((0, 60, 900)),
        )
        for q in names
    ]
    n_nodes = rng.randint(1, 12)
    nodes: list[Node] = []
    for i in range(n_nodes):
        p = (rng.choice(parts),)
        idle = rng.random() < 0.2
        watts = rng.choice((None, 0)) if rng.random() < 0.15 else rng.randint(300, 9000)
        age = rng.choice((400, -30)) if rng.random() < 0.1 else rng.choice((5, 30, 50))
        nodes.append(
            Node(
                f"n{i:03d}",
                p,
                watts,
                state=("IDLE",) if idle else rng.choice((("ALLOCATED",), ("MIXED",))),
                collected=captured - timedelta(seconds=age),
                cpus=rng.choice((64, 64, 128)),
            )
        )
    c.nodes = nodes
    busy = [n for n in nodes if n.state != ("IDLE",)]
    h = timedelta(hours=1)
    for j in range(rng.randint(0, 2 * max(1, len(busy)))):
        if not busy:
            break
        on = rng.sample(busy, k=min(len(busy), rng.choice((1, 1, 1, 2))))
        start = captured - timedelta(minutes=rng.randrange(0, 600))
        end_kind = rng.random()
        c.jobs.append(
            Job(
                1000 + j,
                on[0].partitions[0],
                rng.choice(names),
                ",".join(n.name for n in on),
                state=rng.choice((("RUNNING",),) * 6 + (("PENDING",), ("SUSPENDED",))),
                requeue=rng.random() < 0.7,
                start=start if rng.random() < 0.97 else None,
                end=(
                    captured + timedelta(minutes=rng.randrange(-30, 1500))
                    if end_kind < 0.6
                    else None
                ),
                time_limit_min=rng.choice((60, 600, 2880)) if end_kind < 0.85 else None,
                time_limit_infinite=end_kind >= 0.95,
                preemptable=(
                    start + timedelta(minutes=rng.randrange(0, 90)) if rng.random() < 0.4 else None
                ),
            )
        )
    if rng.random() < 0.4 and nodes:
        r_on = rng.sample(nodes, k=min(len(nodes), 2))
        rs = captured + timedelta(minutes=rng.randrange(-120, 600))
        c.reservations.append(
            Reservation(
                "SYNTHETIC-maint",
                ",".join(n.name for n in r_on),
                rs,
                rs + h * rng.choice((1, 3)),
                flags=rng.choice((("MAINT",), ("MAINT", "IGNORE_JOBS"), ("DAILY",))),
                partition=parts[0],
            )
        )
    classes = []
    for p in parts:
        for q in names:
            for outcome in ("requeue", "cancel", "suspend"):
                if rng.random() < 0.9:
                    holds = sorted(rng.sample((15, 30, 60, 90, 120, 240, 480), k=rng.randint(1, 4)))
                    classes.append(
                        class_terms(
                            p,
                            q,
                            outcome,
                            holds=holds,
                            notice=rng.choice((0, 60, 900)) if rng.random() < 0.97 else None,
                            settle=rng.choice((0, 30, 120, 3000)),
                            mechanism=None if rng.random() < 0.05 else "SYNTHETIC: held",
                            with_price=rng.random() < 0.8,
                        )
                    )
    residuals = [
        {
            "kind": "partition_residual",
            "partition": p,
            "suspended_w": rng.choice((2000, 3500, _unknown("suspended draw"))),
            "idle_w": rng.choice((1200, 2500, _unknown("idle draw"))),
        }
        for p in parts
        if rng.random() < 0.95
    ]
    policy = policy_dict(
        classes,
        residuals=residuals,
        residual_basis=rng.choice(("stated", "idle_twin_measured")),
        preempt_type=rng.choice(("preempt/qos", "preempt/partition_prio")),
        cluster_mode=rng.choice((["REQUEUE"], ["CANCEL"], ["SUSPEND", "GANG"], ["OFF"])),
        cluster_exempt_s=rng.choice((0, 300)) if rng.random() < 0.9 else None,
        horizon=rng.choice((30, 120, 360, 1440)),
        floor_kw=rng.choice((0, 5)) if rng.random() < 0.97 else None,
        cap_kw=rng.choice((None, None, 1, 20)),
        max_age_s=rng.choice((60, 300)),
    )
    return c, policy


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def write_fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(render(reference_state()), encoding="utf-8")
    POLICY_FILE.write_text(render(reference_policy()), encoding="utf-8")


if __name__ == "__main__":
    write_fixtures()
