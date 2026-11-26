"""Workload orchestrator adapters (roadmap WP-SITE-08, B8-02): the pattern and the Slurm reference.

Acceptance:
1. The reference adapter produces envelopes that pass IC-9 conformance: every `Declared` is
   validated by the frozen `ComputeTurndownDeclaration` itself (never a copy), and here again by
   a JSON round trip, the committed 1.0.0 JSON Schema (`_schema_subset`, standard library only)
   and its compiled timeline (`resolve`, `timeline_matches`).
2. Property tests across varied orchestrator states: seeded random Slurm clusters and policies, and
   random damage to the reference payload. Every outcome is a conforming declaration or a refusal
   with reasons, never an exception; every kW is bounded by the measured node power behind it;
   every emitted number is traced to the fields it came from.
3. A state the adapter cannot map yields a refusal with a reason, never a fabricated envelope
   (``test_refuses_*``).

Plus the pattern (sealed ``declare``, registry, labels, no I/O) and mutation checks proving the
guards are load-bearing. Every fixture is synthetic (`tests/_orchestrator_support.py`); the
registered branch is exercised only with documents built inside a test, never a committed file.
"""

from __future__ import annotations

import ast
import copy
import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from tests._orchestrator_support import (
    BATCH,
    CAPTURED,
    FIXTURES,
    HIGH,
    INFER,
    JOBS,
    NODES,
    OPERATOR,
    POLICY_FILE,
    QOS,
    SCAVENGER,
    SITE,
    STATE_FILE,
    Cluster,
    Job,
    Node,
    Partition,
    Qos,
    class_terms,
    policy_dict,
    random_cluster,
    reference_cluster,
    reference_policy,
    reference_state,
    render,
    state_dict,
)
from tests.contracts._contracts_support import run_mutant
from tests.contracts._schema_subset import validate

import gridq.datacentre.orchestrators as orch
from gridq.contracts.__main__ import schema_path
from gridq.contracts.compute_turndown import (
    ComputeTurndownDeclaration,
    NoReturn,
    Offered,
    ReturnsLater,
    SyntheticPlaceholder,
    WorkloadAction,
    offered_at,
    resolve,
    stack_at,
    timeline_matches,
)
from gridq.datacentre.orchestrators import (
    Declared,
    Draft,
    RefusalCode,
    Refused,
    SealedAdapter,
    StateDocument,
    orchestrator_adapters,
)
from gridq.datacentre.orchestrators.ledger import SourceRef
from gridq.datacentre.orchestrators.registry import OWNER
from gridq.datacentre.orchestrators.slurm import (
    DOC_URL,
    SlurmAdapter,
    SlurmPolicy,
    encode_name,
)
from gridq.extensions.registry import CORE_OWNER, DuplicateRegistration

PACKAGE = Path(orch.__file__).parent
ADAPTER = SlurmAdapter()
SCHEMA = json.loads(
    schema_path("ic9_compute_turndown_declaration", "1.0.0").read_text(encoding="utf-8")
)


def _state(data: dict[str, Any]) -> StateDocument:
    return StateDocument.model_validate(data, strict=False)


def _policy(data: dict[str, Any]) -> SlurmPolicy:
    return SlurmPolicy.model_validate(data, strict=False)


def _declare(state: dict[str, Any], policy: dict[str, Any]) -> Declared | Refused:
    return orch.declare(_state(state), _policy(policy))


def _declared(state: dict[str, Any], policy: dict[str, Any]) -> Declared:
    out = _declare(state, policy)
    assert isinstance(out, Declared), out
    return out


def _refused(state: dict[str, Any], policy: dict[str, Any]) -> Refused:
    out = _declare(state, policy)
    assert isinstance(out, Refused), out
    assert out.reasons and all(r.detail for r in out.reasons)
    assert not hasattr(out, "declaration")
    return out


def _resolve_pointer(doc: Any, ptr: str) -> Any:
    node = doc
    for raw in ptr.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def _class(d: ComputeTurndownDeclaration, qos: str, outcome: str) -> str:
    return f"SYNTHETIC-slurm.gpu.{encode_name(qos)}.{outcome}"


def _envelopes(d: ComputeTurndownDeclaration, class_id: str) -> dict[str, list[tuple[int, int]]]:
    return {
        e.envelope_id: [(p.max_hold_min, p.turn_down_kw) for p in e.curve.points]
        for e in d.envelopes
        if e.sla_class_id == class_id
    }


def _conforms(out: Declared) -> None:
    """IC-9 conformance, independently of the adapter: the frozen model's round trip, the
    committed schema, and the compiled timeline."""
    d = out.declaration
    text = d.model_dump_json()
    again = ComputeTurndownDeclaration.model_validate_json(text)
    assert again == d
    validate(json.loads(text), SCHEMA)
    assert timeline_matches(d, resolve(d))
    assert d.resolved_hash == resolve(d).content_hash()


def _numbers(node: Any, tokens: tuple[str, ...] = ()) -> list[str]:
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in _numbers(v, (*tokens, str(k)))]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _numbers(v, (*tokens, str(i)))]
    if isinstance(node, int) and not isinstance(node, bool):
        return ["".join("/" + t.replace("~", "~0").replace("/", "~1") for t in tokens)]
    return []


def _traced(out: Declared, state: dict[str, Any], policy: dict[str, Any]) -> None:
    """Every integer in the declaration has a ledger entry, and every state or policy fact the
    ledger cites is in that document, at that pointer, with that value."""
    body = out.declaration.model_dump(mode="json")
    del body["resolved_hash"]
    assert sorted(_numbers(body)) == [e.target_pointer for e in out.ledger.entries]
    for entry in out.ledger.entries:
        assert _resolve_pointer(body, entry.target_pointer) == entry.emitted
        for ref in entry.inputs:
            _check_ref(ref, state, policy)


def _check_ref(ref: SourceRef, state: dict[str, Any], policy: dict[str, Any]) -> None:
    if ref.doc == "rule":
        assert ref.json_pointer == ""
        return
    found = _resolve_pointer(state if ref.doc == "state" else policy, ref.json_pointer)
    if isinstance(found, list):
        assert ref.value in found, ref
    elif isinstance(found, dict):
        assert found.get("kind") == ref.value, ref
    else:
        assert found == ref.value, ref


# --------------------------------------------------------------------------------------------
# The committed fixtures: synthetic, labelled, in the documented shape
# --------------------------------------------------------------------------------------------


def test_committed_fixtures_equal_a_fresh_build() -> None:
    assert STATE_FILE.read_text(encoding="utf-8") == render(reference_state())
    assert POLICY_FILE.read_text(encoding="utf-8") == render(reference_policy())


def test_every_committed_fixture_is_synthetic_and_labelled() -> None:
    files = sorted(FIXTURES.iterdir())
    assert [f.name for f in files] == [POLICY_FILE.name, STATE_FILE.name]
    for f in files:
        assert f.name.endswith(".synthetic.json")
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["label"] == "synthetic"
        note = data.get("capture_note") or data.get("policy_note")
        assert note.startswith("SYNTHETIC")
        assert "recorded" not in json.dumps(data).replace("not recorded", "")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    assert state["source"]["doc_url"] == DOC_URL
    for q in state["payload"][QOS]["qos"]:
        assert q["name"].startswith("SYNTHETIC-")
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    assert policy["site_id"].startswith("SYNTHETIC-")
    assert policy["operator_id"].startswith("SYNTHETIC-")
    for c in policy["classes"]:
        assert c["price"]["stated_on"]["kind"] == "not_applicable"
        assert c["sla_evidence"]["evidence_hash"].startswith("sha256:")


def test_the_state_is_in_the_v0_0_46_response_shape() -> None:
    """The keys each body carries are the keys of the documented response objects."""
    payload = reference_state()["payload"]
    for path, key in ((JOBS, "jobs"), (NODES, "nodes"), (QOS, "qos")):
        body = payload[path]
        assert {"meta", "errors", "warnings", key} <= set(body)
        assert body["meta"]["plugin"]["data_parser"] == "data_parser/v0.0.46"
        assert body["meta"]["slurm"]["release"] == "26.11.0"
    job = payload[JOBS]["jobs"][0]
    for wrapped in ("start_time", "end_time", "time_limit", "preemptable_time"):
        assert set(job[wrapped]) == {"set", "infinite", "number"}
    node = payload[NODES]["nodes"][0]
    assert set(node["energy"]["current_watts"]) == {"set", "infinite", "number"}
    assert isinstance(node["energy"]["last_collected"], int)
    qos = payload[QOS]["qos"][0]
    assert {"mode", "list", "exempt_time"} <= set(qos["preempt"])
    assert isinstance(qos["limits"]["grace_time"], int)


# --------------------------------------------------------------------------------------------
# Acceptance 1: the reference adapter's envelopes pass IC-9 conformance
# --------------------------------------------------------------------------------------------


def test_reference_declaration_passes_ic9_conformance() -> None:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    out = _declared(state, policy)
    _conforms(out)
    _traced(out, state, policy)
    d = out.declaration
    assert d.record_class == "synthetic_fixture"
    assert (d.site_id, d.operator_id) == (SITE, OPERATOR)
    assert d.issued_at == CAPTURED
    assert d.valid_from == datetime(2027, 3, 1, 9, 30, tzinfo=UTC)
    assert d.valid_until == d.valid_from + timedelta(minutes=360)
    assert all(isinstance(e.price.price_basis, SyntheticPlaceholder) for e in d.envelopes)  # type: ignore[union-attr]


def test_reference_classes_map_preemption_to_ic9_semantics() -> None:
    d = _declared(reference_state(), reference_policy()).declaration
    by_id = {c.class_id: c for c in d.sla_classes}
    requeue = by_id[_class(d, BATCH, "requeue")]
    assert requeue.workload_action == WorkloadAction.DEFERRED
    assert isinstance(requeue.energy_return, ReturnsLater)
    cancel = by_id[_class(d, SCAVENGER, "cancel")]
    assert cancel.workload_action == WorkloadAction.DROPPED
    assert isinstance(cancel.energy_return, NoReturn)
    suspend = by_id[_class(d, INFER, "suspend")]
    assert suspend.workload_action == WorkloadAction.DEFERRED
    assert suspend.operator_label == INFER
    # SYNTHETIC-high preempts the others; nothing preempts it: protected, so no class.
    assert not any(HIGH in c.class_id for c in d.sla_classes)


def test_reference_kw_ramp_and_maintenance_clamp() -> None:
    """The arithmetic, by hand (synthetic watts; residual 1,500 W idle, 2,500 W suspended):

    - requeue: gpu001 4,900 + gpu002 4,800 + gpu003 4,700 W = 14 kW at 09:30 for 30 min; gpu003's
      job ends 10:40, so 9 kW from 60 min; gpu[001-002]'s job is clamped to its MAINT reservation
      at 12:00, so nothing for 240 min. Ramp = grace 120 s + settle 60 s.
    - cancel: gpu004 4,300 + gpu005 4,400 W = 8 kW. Ramp = grace 60 s + settle 60 s.
    - suspend: gpu006 3,600 W = 3 kW, from 09:30 (start 09:00 + exempt 30 min). Ramp = settle.
    - gpu007 (two classes) and gpu008 (no power reading) are withheld.
    """
    out = _declared(reference_state(), reference_policy())
    d = out.declaration
    requeue = _envelopes(d, _class(d, BATCH, "requeue"))
    assert sorted(requeue.values()) == [[(30, 9)], [(30, 14), (120, 9)], [(60, 9)]]
    assert sorted(_envelopes(d, _class(d, SCAVENGER, "cancel")).values()) == [
        [(30, 8)],
        [(60, 8)],
        [(120, 8)],
    ]
    assert list(_envelopes(d, _class(d, INFER, "suspend")).values()) == [[(120, 3)]]
    ramps = {e.sla_class_id: e.response.ramp_s for e in d.envelopes}
    assert ramps == {
        _class(d, BATCH, "requeue"): 180,
        _class(d, SCAVENGER, "cancel"): 120,
        _class(d, INFER, "suspend"): 60,
    }
    assert d.stacking.max_concurrent_turn_down_kw == 14 + 8 + 3
    assert d.it_reference.retained_it_floor_kw == 10
    withheld = {r.detail.split(":")[0] for r in out.withheld}
    assert withheld == {"node gpu007 is shared", "node gpu008"}
    assert out.skipped == ()
    # From 12:30 only the suspended class is left (the MAINT clamp and the jobs' ends).
    late = offered_at(d, datetime(2027, 3, 1, 13, 0, tzinfo=UTC)).state
    assert isinstance(late, Offered)
    assert all(INFER in e for e in late.envelope_ids)
    # IC-9's own stacking takes one rung per class, under the cap.
    stack = stack_at(d, datetime(2027, 3, 1, 9, 30, tzinfo=UTC), 30, 1000)
    assert stack.total_kw == 25
    assert len({layer.envelope_id.rsplit(".r", 1)[0] for layer in stack.layers}) == 3


def test_reference_ledger_traces_every_number() -> None:
    state, policy = reference_state(), reference_policy()
    out = _declared(state, policy)
    _traced(out, state, policy)
    cap = out.ledger.at("/stacking/max_concurrent_turn_down_kw")
    assert cap.emitted == 25 and cap.rule_id.startswith("S5-C5")
    kw = [e for e in out.ledger.entries if e.target_pointer.endswith("/turn_down_kw")]
    assert kw and all(e.rounding == "down" for e in kw)
    first = out.ledger.at("/envelopes/2/curve/points/0/turn_down_kw")
    assert first.exact == "72/5" and first.emitted == 14  # 14,400 W


def test_declaring_is_deterministic_and_bound_by_hash() -> None:
    a = _declared(reference_state(), reference_policy())
    b = _declared(reference_state(), reference_policy())
    assert a.derivation_hash == b.derivation_hash
    assert a.declaration.content_hash() == b.declaration.content_hash()
    changed = reference_policy()
    changed["classes"][0]["min_notice_s"] = 600
    c = _declared(reference_state(), changed)
    assert c.derivation_hash != a.derivation_hash
    assert c.policy_hash != a.policy_hash


# --------------------------------------------------------------------------------------------
# Acceptance 2: properties across varied orchestrator states
# --------------------------------------------------------------------------------------------

N_RANDOM = 400


def _bound_kw(cluster: Cluster, class_id: str) -> int:
    """An upper bound independent of the adapter: the measured watts of every node running a job
    of the class's (partition, QOS), whatever its residual or timing."""
    names: set[str] = set()
    for j in cluster.jobs:
        if j.state == ("RUNNING",) and class_id.split(".")[1:3] == [
            encode_name(j.partition),
            encode_name(j.qos),
        ]:
            names.update(j.nodes.split(","))
    return sum((n.watts or 0) for n in cluster.nodes if n.name in names) // 1000


def test_property_random_clusters_declare_conforming_or_refuse() -> None:
    rng = random.Random(20261126)
    seen: dict[str, int] = {}
    for i in range(N_RANDOM):
        cluster, policy = random_cluster(rng)
        state = state_dict(cluster)
        out = _declare(state, policy)
        if isinstance(out, Refused):
            assert out.reasons, i
            for r in out.reasons:
                assert isinstance(r.code, RefusalCode) and r.detail
                seen[r.code.value] = seen.get(r.code.value, 0) + 1
            continue
        seen["declared"] = seen.get("declared", 0) + 1
        _conforms(out)
        _traced(out, state, policy)
        d = out.declaration
        bound_total = 0
        for c in d.sla_classes:
            bound = _bound_kw(cluster, c.class_id)
            bound_total += bound
            for e in d.envelopes:
                if e.sla_class_id == c.class_id:
                    assert 1 <= e.curve.points[0].turn_down_kw <= bound, (i, e.envelope_id)
        assert d.stacking.max_concurrent_turn_down_kw <= bound_total
        assert d.valid_from >= cluster.captured
        assert d.valid_from - cluster.captured < timedelta(minutes=30)
    # The sample reaches both outcomes and several refusal codes (a vacuous pass is a failure).
    assert seen.get("declared", 0) >= 40, seen
    assert len([k for k in seen if k != "declared"]) >= 4, seen


_DAMAGE: tuple[Callable[[Any, random.Random], Any], ...] = (
    lambda v, r: None,
    lambda v, r: "x" if not isinstance(v, str) else 7,
    lambda v, r: [] if not isinstance(v, list) else {},
    lambda v, r: -v if isinstance(v, int) and not isinstance(v, bool) else v,
    lambda v, r: True,
)


def _paths(node: Any, tokens: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out = [tokens] if tokens else []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _paths(v, (*tokens, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _paths(v, (*tokens, i))
    return out


def test_fuzz_damaged_payloads_never_raise_and_never_fabricate() -> None:
    """Random damage to the reference payload (a field deleted, retyped or negated): the adapter
    answers a conforming declaration or a refusal with reasons, and never raises."""
    rng = random.Random(8)
    base = reference_state()
    paths = [p for p in _paths(base["payload"]) if len(p) >= 2]  # a body itself is the wrapper's
    policy = reference_policy()
    codes: set[str] = set()
    for _ in range(300):
        state = copy.deepcopy(base)
        path = rng.choice(paths)
        parent = state["payload"]
        for t in path[:-1]:
            parent = parent[t]
        if rng.random() < 0.3 and isinstance(parent, dict):
            del parent[path[-1]]
        else:
            new = rng.choice(_DAMAGE)(parent[path[-1]], rng)
            if new is None:
                new = {"kind": "unknown"}
            parent[path[-1]] = new
        out = _declare(state, policy)
        if isinstance(out, Declared):
            _conforms(out)
            _traced(out, state, policy)
        else:
            assert out.reasons
            codes |= {r.code.value for r in out.reasons}
    assert "malformed_state" in codes


# --------------------------------------------------------------------------------------------
# Acceptance 3: what the adapter cannot map is refused, with a reason, never fabricated
# --------------------------------------------------------------------------------------------


def _only_codes(out: Refused) -> set[RefusalCode]:
    return {r.code for r in out.reasons}


def test_refuses_two_preemption_actions_as_unmappable() -> None:
    c = reference_cluster()
    for q in c.qos:
        if q.name in (BATCH, SCAVENGER, INFER):
            q.mode = ("SUSPEND", "CANCEL")
    out = _refused(state_dict(c), reference_policy())
    assert _only_codes(out) == {RefusalCode.UNMAPPABLE_WORKLOAD}
    assert any("two actions" in r.detail for r in out.reasons)


def test_refuses_an_inherited_mode_the_policy_does_not_state() -> None:
    c = reference_cluster()
    for q in c.qos:
        q.mode = ("CLUSTER_GLOBAL",)
    policy = reference_policy()
    policy["cluster_preempt_mode"] = {"kind": "unknown", "reason": "synthetic: not stated"}
    out = _refused(state_dict(c), policy)
    assert _only_codes(out) == {RefusalCode.UNMAPPABLE_WORKLOAD}


def test_refuses_when_no_node_power_is_measured() -> None:
    c = reference_cluster()
    for n in c.nodes:
        n.watts = None
    out = _refused(state_dict(c), reference_policy())
    assert _only_codes(out) == {RefusalCode.POWER_BASIS_MISSING}


def test_refuses_stale_power_readings() -> None:
    c = reference_cluster()
    for n in c.nodes:
        n.collected = CAPTURED - timedelta(seconds=121)
    out = _refused(state_dict(c), reference_policy())
    assert _only_codes(out) == {RefusalCode.POWER_BASIS_MISSING}


def test_refuses_when_no_running_work_is_preemptible() -> None:
    c = reference_cluster()
    for j in c.jobs:
        j.qos = HIGH
    out = _refused(state_dict(c), reference_policy())
    assert _only_codes(out) == {RefusalCode.NO_FLEXIBLE_WORKLOAD}


def test_refuses_a_shed_that_rounds_to_zero() -> None:
    c = reference_cluster()
    for n in c.nodes:
        if n.watts:
            n.watts = 1600  # 100 W above the idle residual, 0 W above the suspended one
    out = _refused(state_dict(c), reference_policy())
    assert _only_codes(out) == {RefusalCode.ZERO_OFFER}


def test_refuses_missing_declaration_facts() -> None:
    policy = reference_policy()
    policy["horizon_min"] = {"kind": "unknown", "reason": "synthetic: not stated"}
    policy["retained_it_floor_kw"] = {"kind": "unknown", "reason": "synthetic: not stated"}
    out = _refused(reference_state(), policy)
    assert _only_codes(out) == {RefusalCode.POLICY_FACT_MISSING}
    assert len(out.reasons) == 2


def test_a_class_missing_a_fact_is_skipped_and_the_rest_declared() -> None:
    policy = reference_policy()
    policy["classes"][0]["min_notice_s"] = {"kind": "unknown", "reason": "synthetic: unstated"}
    out = _declared(reference_state(), policy)
    assert [r.code for r in out.skipped] == [RefusalCode.POLICY_FACT_MISSING]
    assert not any(BATCH in c.class_id for c in out.declaration.sla_classes)
    policy["classes"] = [
        class_terms("gpu", q, o, notice=None)
        for q, o in ((BATCH, "requeue"), (SCAVENGER, "cancel"), (INFER, "suspend"))
    ]
    assert _only_codes(_refused(reference_state(), policy)) == {RefusalCode.POLICY_FACT_MISSING}


def test_a_ramp_longer_than_every_hold_is_not_offered() -> None:
    policy = reference_policy()
    for c in policy["classes"]:
        c["stop_settle_s"] = 240 * 60  # the longest stated hold
    out = _refused(reference_state(), policy)
    assert _only_codes(out) == {RefusalCode.UNMAPPABLE_WORKLOAD}
    assert all("ramp" in r.detail for r in out.reasons)


def test_refuses_a_response_that_carries_errors() -> None:
    state = reference_state()
    state["payload"][NODES]["errors"] = [
        {"description": "SYNTHETIC", "source": "x", "error": "y", "error_number": 1}
    ]
    out = _refused(state, reference_policy())
    assert _only_codes(out) == {RefusalCode.MALFORMED_STATE}


@pytest.mark.parametrize(
    "path",
    [
        (JOBS, "jobs", 0, "job_state"),
        (JOBS, "jobs", 0, "end_time", "number"),
        (NODES, "nodes", 0, "energy"),
        (QOS, "qos", 1, "preempt"),
    ],
)
def test_refuses_a_missing_field_as_malformed(path: tuple[Any, ...]) -> None:
    state = reference_state()
    node = state["payload"]
    for t in path[:-1]:
        node = node[t]
    del node[path[-1]]
    out = _refused(state, reference_policy())
    assert _only_codes(out) == {RefusalCode.MALFORMED_STATE}
    assert out.reasons[0].json_pointer.startswith("/payload/")


def test_refuses_duplicate_node_names_as_ambiguous() -> None:
    c = reference_cluster()
    c.nodes.append(Node("gpu001", ("gpu",), 6000))
    assert _only_codes(_refused(state_dict(c), reference_policy())) == {RefusalCode.AMBIGUOUS_STATE}


def test_refuses_labels_that_disagree() -> None:
    recorded = reference_state()
    recorded["label"] = "recorded"
    out = _refused(recorded, reference_policy())
    assert _only_codes(out) == {RefusalCode.LABEL_MISMATCH}
    operator = reference_policy()
    operator["label"] = "operator_supplied"
    operator["policy_note"] = "Operator policy (in-test document)"
    assert _only_codes(_refused(reference_state(), operator)) == {RefusalCode.LABEL_MISMATCH}
    unlabelled_site = reference_policy()
    unlabelled_site["site_id"] = "DC-SLURM-CAMPUS"
    assert _only_codes(_refused(reference_state(), unlabelled_site)) == {RefusalCode.LABEL_MISMATCH}


def test_refuses_an_unstated_site_or_operator() -> None:
    policy = reference_policy()
    policy["operator_id"] = {"kind": "unknown", "reason": "synthetic: not stated"}
    assert _only_codes(_refused(reference_state(), policy)) == {
        RefusalCode.SITE_OR_OPERATOR_UNSTATED
    }


def test_refuses_an_api_version_or_orchestrator_it_does_not_read() -> None:
    state = reference_state()
    state["source"]["api_version"] = "v0.0.45"
    assert _only_codes(_refused(state, reference_policy())) == {RefusalCode.UNSUPPORTED_API_VERSION}
    other = reference_state()
    other["source"]["api_id"] = "kubernetes"
    out = _refused(other, reference_policy())
    assert _only_codes(out) == {RefusalCode.NO_ADAPTER_REGISTERED}
    assert out.adapter_id == "none"


# --------------------------------------------------------------------------------------------
# The registered branch: in-test documents only (no recorded file is ever committed)
# --------------------------------------------------------------------------------------------


def _operator_inputs(qos_prefix: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    t = CAPTURED
    h = timedelta(hours=1)
    batch = qos_prefix + "batch"
    c = Cluster(captured=t, note="In-test document standing in for a recording")
    c.partitions = [Partition("gpu")]
    c.qos = [Qos("high", preempt_list=(batch,)), Qos(batch, mode=("REQUEUE",))]
    c.nodes = [Node("a1", ("gpu",), 5000), Node("a2", ("gpu",), 5200)]
    c.jobs = [Job(1, "gpu", batch, "a[1-2]", start=t - h, end=t + 3 * h, preemptable=t - h)]
    terms = class_terms("gpu", batch, "requeue")
    terms["price"]["stated_on"] = "2027-02-01"
    policy = policy_dict(
        [terms], label="operator_supplied", site="DC-TEST-SITE", operator="OPERATOR-TEST"
    )
    return state_dict(c, label="recorded"), policy


def test_a_recording_with_an_operator_policy_declares_a_registered_record() -> None:
    state, policy = _operator_inputs()
    out = _declared(state, policy)
    _conforms(out)
    _traced(out, state, policy)
    d = out.declaration
    assert d.record_class == "registered"
    assert [c.class_id for c in d.sla_classes] == ["slurm.gpu.batch.requeue"]
    price = d.envelopes[0].price
    assert price.price_basis.kind == "indicative"  # type: ignore[union-attr]


def test_a_registered_record_never_carries_a_synthetic_label() -> None:
    state, policy = _operator_inputs(qos_prefix="SYNTHETIC-")
    assert _only_codes(_refused(state, policy)) == {RefusalCode.LABEL_MISMATCH}


# --------------------------------------------------------------------------------------------
# The pattern: sealed declare, ledger-only numbers, the registry, no I/O
# --------------------------------------------------------------------------------------------


def test_declare_is_sealed() -> None:
    with pytest.raises(TypeError, match="sealed"):

        class Bypass(SealedAdapter):  # noqa: F841
            adapter_id = "bypass"
            adapter_version = "0"
            api_id = "bypass"
            supported_api_versions = ("v1",)
            policy_type = SlurmPolicy

            def _map(self, *a: Any) -> Draft:
                raise AssertionError

            def declare(self, *a: Any) -> Any:  # type: ignore[override]
                return None


class _Toy(SealedAdapter):
    adapter_id = "toy"
    adapter_version = "0"
    api_id = "slurmrestd"
    supported_api_versions = ("v0.0.46",)
    policy_type = SlurmPolicy
    body: ClassVar[dict[str, Any]] = {}

    def _map(self, state: Any, policy: Any, ledger: Any, record_class: Any) -> Draft:
        return Draft(body=self.body)


def test_a_bare_number_in_a_draft_is_refused_as_untraced() -> None:
    _Toy.body = {"stacking": {"max_concurrent_turn_down_kw": 25}}
    out = _Toy().declare(_state(reference_state()), _policy(reference_policy()))
    assert isinstance(out, Refused)
    assert _only_codes(out) == {RefusalCode.UNTRACED_VALUE}
    assert out.reasons[0].json_pointer == "declaration:/stacking/max_concurrent_turn_down_kw"


def test_a_draft_the_contract_rejects_is_refused_with_its_errors() -> None:
    _Toy.body = {"site_id": "SYNTHETIC-x"}
    out = _Toy().declare(_state(reference_state()), _policy(reference_policy()))
    assert isinstance(out, Refused)
    assert _only_codes(out) <= {RefusalCode.CONTRACT_REJECTED}


def test_the_registry_holds_the_slurm_adapter_under_the_package_owner() -> None:
    reg = orchestrator_adapters.registration("slurmrestd")
    assert isinstance(reg.item, SlurmAdapter)
    assert reg.owner == OWNER != CORE_OWNER
    assert isinstance(reg.item, orch.OrchestratorAdapter)
    with pytest.raises(DuplicateRegistration):
        orchestrator_adapters.register("slurmrestd", SlurmAdapter(), owner=OWNER, version="9")
    snap = orchestrator_adapters._snapshot()
    try:
        with pytest.raises(ValueError, match="api_id"):
            orchestrator_adapters.register("other", SlurmAdapter(), owner=OWNER, version="1")
        with pytest.raises(TypeError, match="SealedAdapter"):
            orchestrator_adapters.register("thing", object(), owner=OWNER, version="1")  # type: ignore[arg-type]
    finally:
        orchestrator_adapters._restore(snap)


_BANNED = {
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "subprocess",
    "asyncio",
    "kubernetes",
    "ray",
    "pyslurm",
    "time",
}
_BANNED_CALLS = {"open", "now", "utcnow", "today", "time", "getenv"}


def test_the_adapters_open_no_connection_read_no_clock_and_write_nothing() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in _BANNED, f"{path.name} imports {name}"
            if isinstance(node, ast.Call):
                f = node.func
                called = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                assert called not in _BANNED_CALLS, f"{path.name} calls {called}"


def test_importing_the_package_registers_nothing() -> None:
    import subprocess
    import sys

    code = (
        "import gridq.datacentre.orchestrators as o\n"
        "import sys\n"
        "assert 'gridq.datacentre.orchestrators.builtin' not in sys.modules\n"
        "assert o.orchestrator_adapters._snapshot() == {}\n"
    )
    src = str(PACKAGE.parents[2])
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": src, "PATH": "/usr/bin:/bin"},
    )
    assert res.returncode == 0, res.stderr


def test_encode_name_is_injective_on_the_id_alphabet() -> None:
    assert encode_name("gpu-a100") == "gpu-a100"
    assert encode_name("a.b") == "a:00002eb"
    assert encode_name("a_b") != encode_name("a.b")
    assert encode_name("é") == ":0000e9"


# --------------------------------------------------------------------------------------------
# Mutation checks: each guard above is load-bearing
# --------------------------------------------------------------------------------------------

_T = "tests/test_orchestrator_adapters.py::"
SLURM = "gridq.datacentre.orchestrators.slurm"
MUTANTS = [
    (  # kW rounded up instead of down: an offer larger than the facts allow
        "gridq.datacentre.orchestrators.ledger",
        'if rounding == "down":\n        return math.floor(exact)',
        'if rounding == "down":\n        return math.ceil(exact)',
        _T + "test_reference_kw_ramp_and_maintenance_clamp",
    ),
    (  # a job's end ignored: work that finishes mid-hold is still offered
        SLURM,
        "if all(a.p <= t and (a.e is None or a.e >= need) for a in jobs):",
        "if all(a.p <= t for a in jobs):",
        _T + "test_reference_kw_ramp_and_maintenance_clamp",
    ),
    (  # every job counted as running
        SLURM,
        'running=states == ("RUNNING",),',
        "running=True,",
        _T + "test_reference_kw_ramp_and_maintenance_clamp",
    ),
    (  # the suspended residual not used for a suspend class
        SLURM,
        'kind = "suspended" if key[2] == "suspend" else "idle"\n            if (key[0], kind) not',
        'kind = "idle"\n            if (key[0], kind) not',
        _T + "test_reference_kw_ramp_and_maintenance_clamp",
    ),
    (  # grace time left out of the ramp
        SLURM,
        "grace + terms.stop_settle_s,",
        "terms.stop_settle_s,",
        _T + "test_reference_kw_ramp_and_maintenance_clamp",
    ),
    (  # two actions resolved to one: a fabricated envelope
        SLURM,
        "if len(actions) > 1:",
        "if False:",
        _T + "test_refuses_two_preemption_actions_as_unmappable",
    ),
    (  # an unmeasured node read as measured
        SLURM,
        'if w.state != "value" or w.number <= 0:',
        "if w.number < 0:",
        _T + "test_refuses_when_no_node_power_is_measured",
    ),
    (  # the declare sequence no longer sealed
        "gridq.datacentre.orchestrators.adapter",
        'if "declare" in cls.__dict__:',
        "if False:",
        _T + "test_declare_is_sealed",
    ),
    (  # a bare number let through
        "gridq.datacentre.orchestrators.ledger",
        "raise UntracedValue(pointer(*tokens))",
        "return node",
        _T + "test_a_bare_number_in_a_draft_is_refused_as_untraced",
    ),
    (  # a synthetic state's record class taken without the policy's label
        "gridq.datacentre.orchestrators.adapter",
        'if state.label == "synthetic" and policy.label == "synthetic":',
        'if state.label == "synthetic":',
        _T + "test_refuses_labels_that_disagree",
    ),
]


def test_mutation_control_passes_unmutated() -> None:
    assert run_mutant(SLURM, "", "", _T + "test_reference_kw_ramp_and_maintenance_clamp") == 0


@pytest.mark.parametrize(("module", "old", "new", "nodeid"), MUTANTS)
def test_mutant_is_caught(module: str, old: str, new: str, nodeid: str) -> None:
    assert run_mutant(module, old, new, nodeid) == 1
