"""IC-5 edge ↔ cloud protocol v1.0.0 conformance (roadmap WP-EDGE-01).

Acceptance:
1. The contract version is tagged and conformance tests pass.
2. The cloud-to-edge schedule latency budget is a tested contract field.
3. Both sides can develop against the reference endpoint (``tests/test_edge_reference.py``).
4. The gateway validates IC-5 from the published JSON Schema or a contracts-only install, with no
   engine or solver code (the two fresh-interpreter tests at the end of section 1).

Plus the guards each decision rests on (D114 to D121): frames are a hash chain and the
receiver's verdict on every arrival is fixed; a schedule is checked against the IC-3 timeline it
names by IC-3's own rule (a differential test proves it) and the authority granted; the ack chain
is a state machine; every threshold is a required profile field and equality is fail-safe; the
trigger table is frozen; ANM is edge-local and the cloud can only tighten it; latency is judged
by an exact nearest rank with the clock skew made explicit. Then mutation checks.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import _ic5_fixtures as fx
import pytest
from _contracts_support import (
    FIXTURES_IC3,
    FIXTURES_IC5,
    GOLDEN_HASHES,
    GOLDEN_HASHES_IC3,
    GOLDEN_HASHES_IC5,
    GOLDEN_HASHES_V110,
    IC5_FIXTURES,
    run_mutant,
)
from pydantic import ValidationError

from gridq.contracts import edge_cloud
from gridq.contracts.__main__ import FROZEN, render, schema_path
from gridq.contracts._base import NotApplicable, canonical_json, not_applicable, unknown
from gridq.contracts.audit import GENESIS_PREV_HASH as AUDIT_GENESIS
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.dispatch_envelope import (
    DispatchEnvelope,
    ResolvedTimeline,
    check_schedule,
    resolve,
)
from gridq.contracts.edge_cloud import (
    CONTRACT_VERSION,
    GENESIS_PREV_HASH,
    GIT_TAG,
    NEURA_OPS_RELEASABLE,
    SUPPORTED_VERSIONS,
    TRIGGERS,
    AckChainBroken,
    AnmEvent,
    AnmLimited,
    AnmTripped,
    AnmUnrestricted,
    BessDispatch,
    ChainStatus,
    ChainTracker,
    CloudToEdgeFrame,
    ControlStage,
    EdgeHeartbeat,
    EdgeToCloudFrame,
    FailSafeEvent,
    FrameChainBroken,
    LatencySample,
    LinkObservation,
    LinkProfile,
    OperatorCommand,
    RejectReason,
    ScheduleAck,
    SetpointSchedule,
    TriggerCleared,
    TriggerCode,
    admit_schedule,
    allowed_next,
    anm_triggers,
    check_frame_size,
    check_latency,
    check_setpoint_schedule,
    deliver_by,
    edge_silent,
    effective_import_cap,
    fail_safe_target,
    governing_timeline,
    latency_ms,
    link_state,
    nearest_rank,
    primary_trigger,
    to_scheduled_periods,
    verify_ack_chain,
    verify_anm_response,
    verify_episode,
    verify_frame_chain,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
HERE = Path(__file__).resolve().parent
PROFILE = "ic5_link_profile_hybrid.synthetic.json"
C2E = "ic5_cloud_to_edge_hybrid.synthetic.jsonl"
E2C = "ic5_edge_to_cloud_hybrid.synthetic.jsonl"
STEMS = (
    ("ic5_cloud_to_edge_frame", CloudToEdgeFrame),
    ("ic5_edge_to_cloud_frame", EdgeToCloudFrame),
    ("ic5_link_profile", LinkProfile),
)
L0, L1, L2, L3 = (
    ControlStage.L0_READ_ONLY,
    ControlStage.L1_SHADOW,
    ControlStage.L2_BESS_CLOSED_LOOP,
    ControlStage.L3_CHILLER_BAND,
)
DC = FIXTURES_IC3 / "ic3_envelope_dc_thermal.synthetic.json"
IC1_HYBRID = HERE / "fixtures" / "v1_1_0" / "ic1_hybrid_evidence_backed.synthetic.json"

# ------------------------------------------------------------------ helpers
# Fixtures are read inside tests, never at import: a mutant that breaks loading must turn its test
# red, not fail collection.


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _profile_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_IC5 / PROFILE).read_text(encoding="utf-8"))
    return data


def _profile(**edits: Any) -> LinkProfile:
    data = _profile_data()
    data.update(edits)
    return LinkProfile.model_validate_json(json.dumps(data))


def _lines(name: str) -> list[str]:
    return (FIXTURES_IC5 / name).read_text(encoding="utf-8").splitlines()


def _c2e() -> list[CloudToEdgeFrame]:
    return [CloudToEdgeFrame.model_validate_json(line) for line in _lines(C2E)]


def _e2c() -> list[EdgeToCloudFrame]:
    return [EdgeToCloudFrame.model_validate_json(line) for line in _lines(E2C)]


@cache
def _hybrid() -> DispatchEnvelope:
    return DispatchEnvelope.model_validate_json(fx.HYBRID.read_text(encoding="utf-8"))


@cache
def _timeline() -> ResolvedTimeline:
    return resolve(_hybrid())


def _schedule(**edits: Any) -> SetpointSchedule:
    data = fx.schedule_body()
    data.update(edits)
    return SetpointSchedule.model_validate_json(json.dumps(data))


def _schedule_frame(body: dict[str, Any] | None = None, **header: Any) -> CloudToEdgeFrame:
    data = json.loads(_lines(C2E)[1])
    if body is not None:
        data["body"] = body
    data.update(header)
    return CloudToEdgeFrame.model_validate_json(json.dumps(data))


def _bodies(kind: str) -> list[Any]:
    return [f.body for f in _e2c() if f.body.kind == kind]


def _docs(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines()]
    return [json.loads(text)]


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


# ------------------------------------------------------------------ 1. version tagged, conformance


def test_the_contract_version_is_tagged() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert SUPPORTED_VERSIONS == ("1.0.0",) and SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == f"contracts/ic5-edge-cloud/v{CONTRACT_VERSION}"
    assert "SUPPORTED_VERSIONS" in edge_cloud.__all__


def test_the_git_tag_is_annotated_and_carries_the_frozen_schemas() -> None:
    """Acceptance 1 against git itself. A checkout without the tag (a tagless CI clone) skips."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
        )

    try:
        kind = git("cat-file", "-t", f"refs/tags/{GIT_TAG}")
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if kind.returncode != 0:
        pytest.skip(f"{GIT_TAG} is not in this checkout")
    assert kind.stdout.strip() == "tag", "the tag must be annotated, not lightweight"
    for stem, _ in STEMS:
        path = schema_path(stem, CONTRACT_VERSION)
        rel = path.relative_to(ROOT).as_posix()
        at_tag = git("show", f"{GIT_TAG}:{rel}")
        assert at_tag.returncode == 0, rel
        assert at_tag.stdout == path.read_text(encoding="utf-8"), rel


@pytest.mark.parametrize("stem,model", STEMS)
def test_the_frozen_schema_is_committed_and_matches_the_model(stem: str, model: type) -> None:
    assert (stem, model, CONTRACT_VERSION) in FROZEN
    assert schema_path(stem, CONTRACT_VERSION).read_text(encoding="utf-8") == render(model)


def test_the_committed_fixtures_equal_a_fresh_build() -> None:
    built = fx.build_all()
    assert sorted(p.name for p in built) == sorted(GOLDEN_HASHES_IC5)
    assert sorted(p.name for p in IC5_FIXTURES) == sorted(GOLDEN_HASHES_IC5)
    for path, text in built.items():
        assert path.read_text(encoding="utf-8") == text, path.name


def test_every_fixture_validates_with_its_pinned_hash() -> None:
    profile = LinkProfile.model_validate_json((FIXTURES_IC5 / PROFILE).read_text(encoding="utf-8"))
    assert profile.content_hash() == GOLDEN_HASHES_IC5[PROFILE]
    for name, frames in ((C2E, _c2e()), (E2C, _e2c())):
        head = verify_frame_chain(frames)
        assert head.frame_hash == GOLDEN_HASHES_IC5[name], name
        assert all(f.profile_hash == profile.content_hash() for f in frames)
        assert all((f.contract, f.contract_version) == ("IC-5", "1.0.0") for f in frames)


def test_each_jsonl_line_is_the_frame_canonical_form_so_its_sha256_is_the_frame_hash() -> None:
    import hashlib

    for name, model in ((C2E, CloudToEdgeFrame), (E2C, EdgeToCloudFrame)):
        for line in _lines(name):
            frame = model.model_validate_json(line)
            assert canonical_json(frame) == line
            assert frame.frame_hash() == "sha256:" + hashlib.sha256(line.encode()).hexdigest()


def test_the_hashes_are_stable_under_another_string_hash_seed() -> None:
    script = (
        "import sys, json; from gridq.contracts.edge_cloud import *\n"
        "f = [EdgeToCloudFrame.model_validate_json(l) for l in open(sys.argv[1])]\n"
        "print(verify_frame_chain(f).frame_hash)"
    )
    for seed in ("0", "12345"):
        res = subprocess.run(
            [sys.executable, "-c", script, str(FIXTURES_IC5 / E2C)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONHASHSEED": seed},
            check=True,
        )
        assert res.stdout.strip() == GOLDEN_HASHES_IC5[E2C]


def test_the_ic5_goldens_are_their_own_table() -> None:
    for other in (GOLDEN_HASHES, GOLDEN_HASHES_V110, GOLDEN_HASHES_IC3):
        assert not set(GOLDEN_HASHES_IC5) & set(other)


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "1.0.1"),
        ("contract_version", "1.1.0"),
        ("contract_version", "2.0.0"),
        ("contract", "IC-3"),
    ],
)
def test_another_version_or_contract_is_refused(field: str, value: str) -> None:
    for name, model in ((C2E, CloudToEdgeFrame), (E2C, EdgeToCloudFrame)):
        data = json.loads(_lines(name)[0])
        data[field] = value
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(data))
    data = _profile_data()
    data[field] = value
    with pytest.raises(ValidationError):
        LinkProfile.model_validate_json(json.dumps(data))


def test_no_fixture_holds_a_null_anywhere() -> None:
    for path in IC5_FIXTURES:
        for doc in _docs(path):
            assert None not in list(_walk(doc)), path.name


@pytest.mark.parametrize("field", list(fx.build_profile()))
def test_null_is_refused_for_every_profile_field(field: str) -> None:
    data = _profile_data()
    data[field] = None
    with pytest.raises(ValidationError):
        LinkProfile.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field", list(fx.schedule_body()))
def test_null_is_refused_for_every_schedule_field(field: str) -> None:
    data = fx.schedule_body()
    data[field] = None
    with pytest.raises(ValidationError):
        SetpointSchedule.model_validate_json(json.dumps(data))


def test_every_fixture_is_labelled_synthetic() -> None:
    profile = _profile()
    assert profile.record_class == "synthetic_fixture" and profile.site_id.startswith("SYNTHETIC-")
    assert all(f.site_id == profile.site_id for f in [*_c2e(), *_e2c()])
    with pytest.raises(ValidationError, match="SYNTHETIC-"):
        _profile(record_class="live")
    with pytest.raises(ValidationError, match="SYNTHETIC-"):
        _profile(site_id="SITE-HYBRID")
    # Every free-text reason in the fixtures says it is a synthetic fixture.
    for path in IC5_FIXTURES:
        for doc in _docs(path):
            for node in _walk(doc):
                if isinstance(node, dict) and node.get("kind") in ("not_applicable", "unknown"):
                    assert "synthetic" in node["reason"], (path.name, node)


def test_the_models_are_final_and_frozen() -> None:
    for model in (CloudToEdgeFrame, EdgeToCloudFrame, LinkProfile):
        with pytest.raises(TypeError, match="final"):
            type("Sub", (model,), {})
    frame = _c2e()[0]
    with pytest.raises(ValidationError):
        frame.seq = 7  # type: ignore[misc]


def test_the_genesis_is_ic13s() -> None:
    assert GENESIS_PREV_HASH == AUDIT_GENESIS


# ------------------------------------------------------------------ 4. the gateway footprint

#: A gateway with no GridQ install: the committed schema files and the standard library only.
_SCHEMA_ONLY = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from _schema_subset import SchemaError, validate
schemas, fixtures = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
load = lambda p: json.loads(p.read_text(encoding="utf-8"))
c2e = load(schemas / "ic5_cloud_to_edge_frame.v1.0.0.schema.json")
e2c = load(schemas / "ic5_edge_to_cloud_frame.v1.0.0.schema.json")
prof = load(schemas / "ic5_link_profile.v1.0.0.schema.json")
validate(load(fixtures / "ic5_link_profile_hybrid.synthetic.json"), prof)
n = 0
for name, schema in (("cloud_to_edge", c2e), ("edge_to_cloud", e2c)):
    for line in (fixtures / f"ic5_{name}_hybrid.synthetic.jsonl").read_text().splitlines():
        validate(json.loads(line), schema)
        n += 1
refused = []
c2e_lines = (fixtures / "ic5_cloud_to_edge_hybrid.synthetic.jsonl").read_text().splitlines()
first = json.loads(c2e_lines[0])
for label, edit in (
    ("anm in a cloud-to-edge body", lambda f: f.update(body={"kind": "anm_trip"})),
    ("null", lambda f: f.update(seq=None)),
    ("unknown field", lambda f: f.update(extra=1)),
    ("naive time", lambda f: f.update(sent_at="2027-01-04T15:55:00")),
    ("another version", lambda f: f.update(contract_version="1.1.0")),
    ("bool for int", lambda f: f.update(seq=True)),
):
    bad = json.loads(json.dumps(first))
    edit(bad)
    try:
        validate(bad, c2e)
    except SchemaError:
        refused.append(label)
print(json.dumps({
    "validated": n,
    "refused": refused,
    "gridq": sorted(m for m in sys.modules if m == "gridq" or m.startswith("gridq.")),
    "pydantic": "pydantic" in sys.modules,
}))
"""


def test_a_gateway_validates_ic5_from_the_json_schema_files_alone() -> None:
    """Acceptance 4, schema path: no GridQ, no pydantic, no jsonschema; the stdlib only."""
    res = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _SCHEMA_ONLY,
            str(HERE),
            str(schema_path("ic5_link_profile", CONTRACT_VERSION).parent),
            str(FIXTURES_IC5),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["validated"] == len(_lines(C2E)) + len(_lines(E2C))
    assert out["refused"] == [
        "anm in a cloud-to-edge body",
        "null",
        "unknown field",
        "naive time",
        "another version",
        "bool for int",
    ]
    assert out["gridq"] == [] and out["pydantic"] is False


#: A gateway with the contracts only: validates, chains, admits and reasons with `gridq.contracts`.
_CONTRACTS_ONLY = """
import json, pathlib, sys
from datetime import datetime
from gridq.contracts.dispatch_envelope import DispatchEnvelope, resolve
from gridq.contracts.edge_cloud import (
    ChainTracker, CloudToEdgeFrame, ControlStage, EdgeToCloudFrame, LinkObservation, LinkProfile,
    admit_schedule, link_state, not_applicable, verify_ack_chain, verify_episode,
)
fx, ic3 = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
read = lambda p: p.read_text(encoding="utf-8")
lines = lambda name: read(fx / f"ic5_{name}_hybrid.synthetic.jsonl").splitlines()
profile = LinkProfile.model_validate_json(read(fx / "ic5_link_profile_hybrid.synthetic.json"))
c2e = [CloudToEdgeFrame.model_validate_json(l) for l in lines("cloud_to_edge")]
e2c = [EdgeToCloudFrame.model_validate_json(l) for l in lines("edge_to_cloud")]
tracker = ChainTracker(profile.site_id, "cloud_to_edge", profile.content_hash())
verdicts = [tracker.receive(f).status.value for f in c2e]
hybrid = read(ic3 / "ic3_envelope_hybrid_bess.synthetic.json")
timeline = resolve(DispatchEnvelope.model_validate_json(hybrid))
verdict = admit_schedule(
    c2e[1], profile=profile, timeline=timeline, stage=ControlStage.L2_BESS_CLOSED_LOOP,
    received_at=datetime.fromisoformat("2027-01-04T15:55:41.2+00:00"),
    held=not_applicable("boot"), known_ids={},
)
acks = [f.body for f in e2c if f.body.kind == "schedule_ack"]
episode = verify_episode([f.body for f in e2c if f.body.kind == "fail_safe_event"])
print(json.dumps({
    "verdicts": verdicts,
    "accepted": verdict.accepted,
    "ack_head": verify_ack_chain(c2e[1].body, acks).state,
    "exited": episode.exited,
    "engine_modules": sorted(
        m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
    ),
    "heavy": sorted(
        m for m in ("numpy", "pandas", "linopy", "highspy", "xarray", "scipy") if m in sys.modules
    ),
}))
"""


def test_a_gateway_runs_ic5_on_the_contracts_package_alone() -> None:
    """Acceptance 4, contracts path: a fresh interpreter imports no engine, sizer or solver."""
    res = subprocess.run(
        [sys.executable, "-c", _CONTRACTS_ONLY, str(FIXTURES_IC5), str(FIXTURES_IC3)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["engine_modules"] == [] and out["heavy"] == [], out
    assert out["verdicts"] == ["new_epoch", "in_order", "in_order"]
    assert out["accepted"] is True and out["ack_head"] == "active" and out["exited"] is True


# ------------------------------------------------------------------ 2. the latency budget


def test_the_schedule_latency_budget_is_a_required_contract_field() -> None:
    """Acceptance 2: no profile without it, no default, and the schema says so."""
    for value in (None, {"kind": "not_applicable", "reason": "x"}):
        data = _profile_data()
        data["latency_budgets"]["cloud_to_edge_schedule"] = value
        with pytest.raises(ValidationError):
            LinkProfile.model_validate_json(json.dumps(data))
    data = _profile_data()
    del data["latency_budgets"]["cloud_to_edge_schedule"]
    with pytest.raises(ValidationError):
        LinkProfile.model_validate_json(json.dumps(data))
    schema = json.loads(schema_path("ic5_link_profile", CONTRACT_VERSION).read_text())
    budgets = schema["$defs"]["LatencyBudgets"]
    assert "cloud_to_edge_schedule" in budgets["required"]
    assert "default" not in budgets["properties"]["cloud_to_edge_schedule"]
    budget = schema["$defs"]["LatencyBudget"]
    assert set(budget["required"]) >= {"bound_ms", "quantile_per_mille", "min_samples", "path"}
    assert not any("default" in p for p in budget["properties"].values())


def test_no_profile_number_has_a_default() -> None:
    """No published source sets these, so none may default (the no-invented-data rule)."""
    schema = json.loads(schema_path("ic5_link_profile", CONTRACT_VERSION).read_text())
    numbers = [k for k, v in schema["properties"].items() if v.get("type") == "integer"]
    assert len(numbers) == 10
    for name in numbers:
        assert name in schema["required"] and "default" not in schema["properties"][name], name


def test_a_budget_on_another_path_or_with_an_anm_path_is_refused() -> None:
    data = _profile_data()
    data["latency_budgets"]["cloud_to_edge_schedule"]["path"] = "heartbeat"
    with pytest.raises(ValidationError, match="path"):
        LinkProfile.model_validate_json(json.dumps(data))
    data["latency_budgets"]["cloud_to_edge_schedule"]["path"] = "anm"
    with pytest.raises(ValidationError):
        LinkProfile.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "q,n,ok", [(990, 99, False), (990, 100, True), (999, 999, False), (1000, 1, True)]
)
def test_a_quantile_needs_enough_samples_to_mean_itself(q: int, n: int, ok: bool) -> None:
    data = _profile_data()
    budget = data["latency_budgets"]["cloud_to_edge_schedule"]
    budget.update(quantile_per_mille=q, min_samples=n)
    if ok:
        LinkProfile.model_validate_json(json.dumps(data))
    else:
        with pytest.raises(ValidationError, match="min_samples"):
            LinkProfile.model_validate_json(json.dumps(data))


def test_the_clock_skew_must_be_below_every_bound() -> None:
    with pytest.raises(ValidationError, match="max_clock_skew_ms"):
        _profile(max_clock_skew_ms=5_000)
    _profile(max_clock_skew_ms=4_999)


def test_deliver_by_is_sent_at_plus_the_bound_and_the_edge_checks_it() -> None:
    frame = _c2e()[1]
    assert frame.body.deliver_by == deliver_by(frame.sent_at, _profile())
    assert deliver_by(frame.sent_at, _profile()) - frame.sent_at == timedelta(milliseconds=5_000)
    body = fx.schedule_body()
    body["deliver_by"] = "2027-01-04T15:55:46Z"
    verdict = _admit(_schedule_frame(body))
    assert verdict.reasons == (RejectReason.DELIVER_BY_MISMATCH,)


def test_a_late_schedule_is_flagged_never_refused() -> None:
    frame = _c2e()[1]
    on_time = _admit(frame, received_at=_at("2027-01-04T15:55:45Z"))
    late = _admit(frame, received_at=_at("2027-01-04T15:55:45.001Z"))
    assert on_time.accepted and not on_time.late
    assert late.accepted and late.late


def test_nearest_rank_is_exact() -> None:
    assert nearest_rank(990, 100) == 99
    assert nearest_rank(990, 101) == 100
    assert nearest_rank(500, 1) == 1 and nearest_rank(1000, 7) == 7 and nearest_rank(1, 1000) == 1
    for n in range(1, 300):
        for q in (1, 500, 950, 990, 999, 1000):
            r = nearest_rank(q, n)
            assert 1 <= r <= n and r * 1000 >= q * n and (r - 1) * 1000 < q * n


def _samples(ms: list[int], *, synced: bool = True) -> list[LatencySample]:
    t0 = _at("2027-01-04T16:00:00Z")
    return [
        LatencySample(
            path="cloud_to_edge_schedule",
            sent_at=t0 + timedelta(seconds=i),
            received_at=t0 + timedelta(seconds=i, milliseconds=m),
            clocks_synchronised=synced,
        )
        for i, m in enumerate(ms)
    ]


@pytest.mark.parametrize(
    "ms,verdict,observed",
    [
        ([1_000] * 99 + [9_000], "pass", 1_000),  # the p99 of 100 is the 99th value
        ([1_000] * 98 + [9_000] * 2, "fail", 9_000),
        ([4_750] * 100, "pass", 4_750),  # observed + skew = bound: a pass, exactly
        ([4_751] * 100, "indeterminate_within_skew", 4_751),
        ([5_250] * 100, "indeterminate_within_skew", 5_250),  # observed - skew = bound
        ([5_251] * 100, "fail", 5_251),
        ([1_000] * 99, "insufficient_samples", None),
    ],
)
def test_check_latency_verdicts(ms: list[int], verdict: str, observed: int | None) -> None:
    report = check_latency(_profile(), "cloud_to_edge_schedule", _samples(ms))
    assert report.verdict == verdict
    assert (report.observed_ms if isinstance(report.observed_ms, int) else None) == observed
    assert report.late_count == sum(1 for m in ms if m > 5_000)
    assert report.profile_hash == _profile().content_hash()


def test_unsynchronised_clocks_beyond_the_quantile_tail_are_unverifiable() -> None:
    one = _samples([1_000] * 100) + _samples([1_000], synced=False)
    assert check_latency(_profile(), "cloud_to_edge_schedule", one).verdict == "pass"
    two = _samples([1_000] * 100) + _samples([1_000] * 2, synced=False)
    report = check_latency(_profile(), "cloud_to_edge_schedule", two)
    assert report.verdict == "unverifiable_clock" and report.excluded_clock_count == 2


def test_a_sample_is_floored_to_the_millisecond_and_negative_within_skew_reads_zero() -> None:
    t0 = _at("2027-01-04T16:00:00Z")

    def sample(us: int) -> LatencySample:
        return LatencySample(
            path="cloud_to_edge_schedule",
            sent_at=t0,
            received_at=t0 + timedelta(microseconds=us),
            clocks_synchronised=True,
        )

    assert latency_ms(sample(4_999_999), 250) == 4_999
    assert latency_ms(sample(-250_000), 250) == 0
    assert latency_ms(sample(-250_001), 250) is None
    with pytest.raises(ValueError, match="no budget"):
        check_latency(_profile(), "heartbeat", [])


# ------------------------------------------------------------------ the profile invariants


@pytest.mark.parametrize(
    "edits,match",
    [
        ({"cloud_heartbeat_interval_ms": 60_000}, "lease"),
        ({"control_lease_ms": 120_001}, "control_lease_ms"),
        ({"mpc_cadence_ms": 120_000}, "mpc_cadence_ms"),
        ({"schedule_required_lookahead_ms": 59_999}, "lookahead"),
        ({"edge_heartbeat_interval_ms": 60_000}, "silence"),
        ({"anm_authority": "cloud"}, "anm_authority"),
        ({"resume_hold_ms": -1}, "resume_hold_ms"),
        ({"control_lease_ms": 0}, "control_lease_ms"),
    ],
)
def test_the_profile_is_internally_consistent(edits: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _profile(**edits)


def test_the_spec_two_minute_staleness_and_five_minute_mpc_cannot_both_hold() -> None:
    """Spec §10A's "default 2 min" staleness with B2-02's 5-minute MPC cadence would make every
    schedule stale before the next: the profile refuses it (open item for the owner)."""
    with pytest.raises(ValidationError, match="mpc_cadence_ms"):
        _profile(schedule_max_age_ms=120_000, mpc_cadence_ms=300_000)


# ------------------------------------------------------------------ frames and the chain


def test_seq_zero_exactly_at_genesis() -> None:
    data = json.loads(_lines(C2E)[1])
    data["prev_hash"] = GENESIS_PREV_HASH
    with pytest.raises(ValidationError, match="genesis"):
        CloudToEdgeFrame.model_validate_json(json.dumps(data))
    data = json.loads(_lines(C2E)[0])
    data["prev_hash"] = "sha256:" + "1" * 64
    with pytest.raises(ValidationError, match="genesis"):
        CloudToEdgeFrame.model_validate_json(json.dumps(data))


def test_a_body_for_another_site_is_refused() -> None:
    body = fx.schedule_body()
    body["site_id"] = "SYNTHETIC-SITE-OTHER"
    with pytest.raises(ValidationError, match="another site"):
        _schedule_frame(body)


def test_verify_frame_chain_finds_a_tampered_frame() -> None:
    frames = _e2c()
    data = json.loads(_lines(E2C)[4])
    data["sent_at"] = "2027-01-04T15:55:41.400000Z"
    tampered = EdgeToCloudFrame.model_validate_json(json.dumps(data))
    with pytest.raises(FrameChainBroken, match="seq 5"):
        verify_frame_chain([*frames[:4], tampered, *frames[5:]])
    with pytest.raises(FrameChainBroken, match="seq 3"):
        verify_frame_chain([*frames[:2], *frames[3:]])


def _tracker() -> ChainTracker:
    return ChainTracker("SYNTHETIC-SITE-HYBRID", "edge_to_cloud", _profile().content_hash())


def test_the_receiver_verdicts_in_order_duplicate_gap_and_late_fill() -> None:
    f = _e2c()
    t = _tracker()
    assert t.receive(f[0]).status is ChainStatus.NEW_EPOCH
    assert t.receive(f[1]).status is ChainStatus.IN_ORDER
    dup = t.receive(f[1])
    assert dup.status is ChainStatus.DUPLICATE and not dup.deliver
    gap = t.receive(f[4])
    assert gap.status is ChainStatus.GAP and gap.deliver
    assert [(r.first, r.last) for r in gap.missing] == [(2, 3)]
    fill = t.receive(f[3])
    assert fill.status is ChainStatus.LATE_FILL and [(r.first, r.last) for r in fill.missing] == [
        (2, 2)
    ]
    assert t.receive(f[2]).status is ChainStatus.LATE_FILL and t.missing == ()
    assert [t.receive(x).status for x in f[5:]] == [ChainStatus.IN_ORDER] * 4


def test_a_fork_is_refused_and_the_epoch_stays_refused() -> None:
    f = _e2c()
    t = _tracker()
    for x in f[:3]:
        t.receive(x)
    data = json.loads(_lines(E2C)[2])
    data["sent_at"] = "2027-01-04T15:55:41.250000Z"
    other = EdgeToCloudFrame.model_validate_json(json.dumps(data))
    fork = t.receive(other)
    assert fork.status is ChainStatus.FORK and not fork.deliver
    assert t.receive(f[3]).status is ChainStatus.FORK  # even the honest next frame
    # The next seq, on time, but linked to something other than the head: a fork.
    t1 = _tracker()
    for x in f[:3]:
        t1.receive(x)
    data = json.loads(_lines(E2C)[3])
    data["prev_hash"] = "sha256:" + "7" * 64
    unlinked = t1.receive(EdgeToCloudFrame.model_validate_json(json.dumps(data)))
    assert unlinked.status is ChainStatus.FORK and "head" in unlinked.detail
    # A late fill whose successor does not link to it is a fork too.
    t2 = _tracker()
    for x in (f[0], f[1], f[3]):
        t2.receive(x)
    assert t2.receive(other).status is ChainStatus.FORK


def test_a_new_epoch_resyncs_a_lower_one_is_stale_and_another_profile_is_refused() -> None:
    f = _e2c()
    t = _tracker()
    t.receive(f[0])
    data = json.loads(_lines(E2C)[0])
    data["link_epoch"] = 1
    epoch1 = EdgeToCloudFrame.model_validate_json(json.dumps(data))
    assert t.receive(epoch1).status is ChainStatus.NEW_EPOCH and t.epoch == 1
    assert t.receive(f[1]).status is ChainStatus.STALE_EPOCH
    data["profile_hash"] = "sha256:" + "2" * 64
    other = EdgeToCloudFrame.model_validate_json(json.dumps(data))
    v = t.receive(other)
    assert v.status is ChainStatus.PROFILE_MISMATCH and not v.deliver


def test_a_frame_too_large_for_the_profile_is_refused() -> None:
    raw = _lines(E2C)[0].encode()
    assert check_frame_size(raw, _profile())
    assert not check_frame_size(raw, _profile(max_frame_bytes=len(raw) - 1))
    assert check_frame_size(raw, _profile(max_frame_bytes=len(raw)))


def test_an_envelope_install_carries_its_own_compiled_timeline() -> None:
    env, timeline = _hybrid(), _timeline()
    base = json.loads(_lines(C2E)[0])
    base["body"] = {
        "kind": "envelope_install",
        "envelope": json.loads(canonical_json(env)),
        "timeline": json.loads(canonical_json(timeline)),
    }
    frame = CloudToEdgeFrame.model_validate_json(json.dumps(base))
    assert frame.body.timeline.content_hash() == env.resolved_hash
    week = resolve(
        DispatchEnvelope.model_validate_json(
            (FIXTURES_IC3 / "ic3_envelope_clock_change_week.synthetic.json").read_text()
        )
    )
    base["body"]["timeline"] = json.loads(canonical_json(week))
    with pytest.raises(ValidationError, match="compilation"):
        CloudToEdgeFrame.model_validate_json(json.dumps(base))


# ------------------------------------------------------------------ the setpoint schedule


def test_the_fixture_schedule_passes_the_envelope() -> None:
    v = check_setpoint_schedule(_timeline(), _schedule(), L2)
    assert v.accepted and v.reasons == () and v.violations == ()


@pytest.mark.parametrize(
    "execution,chiller,stage,ok",
    [
        ("shadow", False, L0, False),
        ("closed_loop", False, L0, False),
        ("shadow", False, L1, True),
        ("closed_loop", False, L1, False),
        ("closed_loop", False, L2, True),
        ("shadow", True, L1, True),
        ("closed_loop", True, L2, False),
        ("closed_loop", True, L3, True),
    ],
)
def test_authority_is_staged(execution: str, chiller: bool, stage: ControlStage, ok: bool) -> None:
    """ADR-D2: L0 read-only, L1 shadow, L2 battery closed loop, L3 chiller band."""
    dc = DispatchEnvelope.model_validate_json(DC.read_text())
    timeline = resolve(dc)
    band = {"point": "it_inlet", "low_tenths_c": 200, "high_tenths_c": 240}
    period = {
        "start": "2027-01-04T12:00:00Z",
        "end": "2027-01-04T12:30:00Z",
        "bess": {"kind": "idle"},
        "site_import_cap_kw": 1_000,
        "chiller_band": band if chiller else {"kind": "not_applicable", "reason": "x"},
        "expected": {
            "soe_end_kwh": 1_000,
            "site_import_kw": 0,
            "site_export_kw": 0,
            "temperature": band,
        },
    }
    data = fx.schedule_body()
    data.update(
        site_id=dc.site_id,
        execution=execution,
        resolved_hash=dc.resolved_hash,
        soe_start_kwh=1_000,
        periods=[period],
    )
    data["envelope"]["envelope_hash"] = dc.content_hash()
    verdict = check_setpoint_schedule(
        timeline, SetpointSchedule.model_validate_json(json.dumps(data)), stage
    )
    assert verdict.accepted is ok
    assert verdict.reasons == (() if ok else (RejectReason.AUTHORITY_NOT_GRANTED,))


def test_a_schedule_naming_another_timeline_is_envelope_unknown() -> None:
    v = check_setpoint_schedule(_timeline(), _schedule(resolved_hash="sha256:" + "3" * 64), L2)
    assert v.reasons == (RejectReason.ENVELOPE_UNKNOWN,) and v.violations == ()


def test_one_bad_period_rejects_the_whole_schedule() -> None:
    data = fx.schedule_body()
    data["periods"][1]["expected"]["soe_end_kwh"] = 5_999  # below the 6 000 kWh service floor
    v = check_setpoint_schedule(
        _timeline(), SetpointSchedule.model_validate_json(json.dumps(data)), L2
    )
    assert not v.accepted and v.reasons == (RejectReason.ENVELOPE_VIOLATION,)
    assert [(x.period, x.code.value) for x in v.violations] == [(1, "soe_below_service_floor")]


def test_the_import_cap_is_checked_as_the_envelope_import_limit() -> None:
    data = fx.schedule_body()
    data["periods"][0]["site_import_cap_kw"] = 10_001
    v = check_setpoint_schedule(
        _timeline(), SetpointSchedule.model_validate_json(json.dumps(data)), L2
    )
    assert [x.code.value for x in v.violations] == ["import_limit"]


def test_a_chiller_band_must_sit_inside_the_cell_band() -> None:
    dc = DispatchEnvelope.model_validate_json(DC.read_text())
    timeline = resolve(dc)
    band = {"point": "it_inlet", "low_tenths_c": 200, "high_tenths_c": 240}

    def verdict(chiller: dict[str, Any], start: str = "2027-01-04T19:00:00Z") -> list[str]:
        data = fx.schedule_body()
        data.update(site_id=dc.site_id, resolved_hash=dc.resolved_hash, soe_start_kwh=1_000)
        data["periods"] = [
            {
                "start": start,
                "end": _iso(_at(start) + timedelta(minutes=30)),
                "bess": {"kind": "idle"},
                "site_import_cap_kw": 1_000,
                "chiller_band": chiller,
                "expected": {
                    "soe_end_kwh": 1_000,
                    "site_import_kw": 0,
                    "site_export_kw": 0,
                    "temperature": band,
                },
            }
        ]
        s = SetpointSchedule.model_validate_json(json.dumps(data))
        return [v.code.value for v in check_setpoint_schedule(timeline, s, L3).violations]

    assert verdict(band) == []
    assert verdict({**band, "high_tenths_c": 245}) == ["thermal_band"]  # the 18.0-24.0 window
    # No band in the hybrid's cells, so no chiller band may be commanded there.
    data = fx.schedule_body()
    data["periods"][0]["chiller_band"] = band
    s = SetpointSchedule.model_validate_json(json.dumps(data))
    assert [v.code.value for v in check_setpoint_schedule(_timeline(), s, L3).violations] == [
        "thermal_band"
    ]


def test_the_timeline_check_is_ic3s_own_rule() -> None:
    """Differential: on random schedules, the timeline check equals IC-3 `check_schedule` on the
    IC-3 projection (drift guard: IC-5 re-reads the rule from the timeline, not the envelope)."""
    rng = random.Random(20271104)
    env, timeline = _hybrid(), _timeline()
    services = [None, "svc-capacity-market", "svc-local-flex-winter-peak"]
    for _ in range(300):
        start = env.valid_from + timedelta(minutes=30 * rng.randrange(17_520 + 4) - 30 * 2)
        periods = []
        soe = rng.choice([900, 1_000, 2_000, 6_000, 9_000, 10_000, 10_001])
        for k in range(rng.randint(1, 3)):
            s = start + timedelta(minutes=30 * k)
            direction = rng.choice(["discharge", "charge"])
            deliveries = []
            for svc in sorted({rng.choice(services) for _ in range(2)} - {None}):
                deliveries.append(
                    {"service_id": svc, "direction": direction, "kw": rng.choice([1, 1_000, 2_001])}
                )
            merchant = rng.choice([0, 1_500, 4_501])
            kw = merchant + sum(d["kw"] for d in deliveries)
            bess: dict[str, Any] = (
                {"kind": "idle"}
                if kw == 0
                else {
                    "kind": "dispatch",
                    "direction": direction,
                    "kw": kw,
                    "merchant_kw": merchant,
                    "service_delivery": deliveries,
                }
            )
            end_soe = rng.choice([900, 1_999, 5_999, 6_000, 9_000, 10_001])
            periods.append(
                {
                    "start": _iso(s),
                    "end": _iso(s + timedelta(minutes=rng.choice([5, 30]))),
                    "bess": bess,
                    "site_import_cap_kw": rng.choice([0, 10_000, 10_001, 25_001]),
                    "chiller_band": {"kind": "not_applicable", "reason": "x"},
                    "expected": {
                        "soe_end_kwh": end_soe,
                        "site_import_kw": 0,
                        "site_export_kw": rng.choice([0, 5_000, 5_001]),
                        "temperature": {"kind": "not_applicable", "reason": "x"},
                    },
                }
            )
        # contiguous: each next period starts where the last ended
        for a, b in zip(periods, periods[1:], strict=False):
            length = _at(b["end"]) - _at(b["start"])
            b["start"] = a["end"]
            b["end"] = _iso(_at(a["end"]) + length)
        data = fx.schedule_body()
        data.update(soe_start_kwh=soe, periods=periods)
        try:
            schedule = SetpointSchedule.model_validate_json(json.dumps(data))
        except ValidationError:
            continue  # a period crossing a half-hour: not a schedule at all
        mine = check_setpoint_schedule(timeline, schedule, L2).violations
        ic3 = check_schedule(env, to_scheduled_periods(schedule)).violations
        assert mine == ic3, schedule


def test_a_dispatch_command_closes_exactly() -> None:
    def dispatch(**fields: Any) -> BessDispatch:
        data = {
            "kind": "dispatch",
            "direction": "discharge",
            "kw": 3_000,
            "merchant_kw": 2_000,
            "service_delivery": [
                {"service_id": "svc-capacity-market", "direction": "discharge", "kw": 1_000}
            ],
            **fields,
        }
        return BessDispatch.model_validate_json(json.dumps(data))

    dispatch()
    with pytest.raises(ValidationError, match="exactly"):
        dispatch(kw=3_001)
    with pytest.raises(ValidationError, match="direction"):
        dispatch(
            service_delivery=[
                {"service_id": "svc-capacity-market", "direction": "charge", "kw": 1_000}
            ]
        )


def test_periods_are_contiguous_inside_half_hours_and_under_their_own_cap() -> None:
    data = fx.schedule_body()
    data["periods"][1]["start"] = "2027-01-04T16:01:00Z"
    with pytest.raises(ValidationError, match="contiguous"):
        SetpointSchedule.model_validate_json(json.dumps(data))
    data = fx.schedule_body()
    data["periods"][1]["end"] = "2027-01-04T16:31:00Z"
    with pytest.raises(ValidationError, match="one half-hour"):
        SetpointSchedule.model_validate_json(json.dumps(data))
    data = fx.schedule_body()
    data["periods"][0]["expected"]["site_import_kw"] = 2_001
    with pytest.raises(ValidationError, match="cap"):
        SetpointSchedule.model_validate_json(json.dumps(data))


def _admit(
    frame: CloudToEdgeFrame,
    *,
    received_at: datetime | None = None,
    held: SetpointSchedule | NotApplicable | None = None,
    known: dict[str, str] | None = None,
    profile: LinkProfile | None = None,
) -> Any:
    return admit_schedule(
        frame,
        profile=profile or _profile(),
        timeline=_timeline(),
        stage=L2,
        received_at=received_at or _at("2027-01-04T15:55:41.2Z"),
        held=held if held is not None else not_applicable("boot"),
        known_ids=known or {},
    )


def test_admit_refuses_a_reused_id_an_older_schedule_and_a_broken_supersession() -> None:
    frame = _c2e()[1]
    first = _schedule()
    assert _admit(frame, known={first.schedule_id: "sha256:" + "4" * 64}).reasons == (
        RejectReason.ID_REUSED,
    )
    assert _admit(frame, known={first.schedule_id: first.content_hash()}).accepted
    newer = _schedule(issued_at="2027-01-04T15:55:41Z", schedule_id="later")
    assert set(_admit(frame, held=newer).reasons) == {
        RejectReason.OUT_OF_ORDER,
        RejectReason.SUPERSEDES_MISMATCH,
    }
    body = fx.schedule_body()
    body["supersedes"] = "sha256:" + "5" * 64
    assert _admit(_schedule_frame(body)).reasons == (RejectReason.SUPERSEDES_MISMATCH,)
    body["supersedes"] = _schedule(schedule_id="before").content_hash()
    assert _admit(_schedule_frame(body), held=_schedule(schedule_id="before")).accepted


def test_stale_on_arrival_is_at_the_max_age_exactly_and_an_elapsed_horizon_is_refused() -> None:
    frame = _c2e()[1]
    just = _at("2027-01-04T15:57:39.999Z")
    assert RejectReason.STALE_ON_ARRIVAL not in _admit(frame, received_at=just).reasons
    stale = _admit(frame, received_at=_at("2027-01-04T15:57:40Z"))
    assert RejectReason.STALE_ON_ARRIVAL in stale.reasons
    gone = _admit(frame, received_at=_at("2027-01-04T16:30:00Z"))
    assert RejectReason.HORIZON_ELAPSED in gone.reasons


def test_without_the_named_timeline_the_envelope_is_unknown() -> None:
    v = admit_schedule(
        _c2e()[1],
        profile=_profile(),
        timeline=not_applicable("none held"),
        stage=L2,
        received_at=_at("2027-01-04T15:55:41Z"),
        held=not_applicable("boot"),
        known_ids={},
    )
    assert v.reasons == (RejectReason.ENVELOPE_UNKNOWN,)


# ------------------------------------------------------------------ acknowledgements


def test_the_fixture_ack_chain_verifies_and_governs_from_its_active_ack() -> None:
    schedule = _c2e()[1].body
    acks = _bodies("schedule_ack")
    head = verify_ack_chain(schedule, acks)
    assert head.state == "active" and head.seq == 1
    (span,) = governing_timeline([(schedule, acks)])
    assert span.start == _at("2027-01-04T15:56:00Z") and span.ended_by == "open"


def test_the_ack_state_machine() -> None:
    assert allowed_next("start") == {"accepted", "rejected"}
    assert allowed_next("accepted") == {"active", "shadowing", "superseded", "lapsed"}
    assert allowed_next("active") == {"superseded", "completed", "aborted"}
    for terminal in ("rejected", "superseded", "completed", "lapsed", "aborted"):
        assert allowed_next(terminal) == frozenset()


def _ack_data(i: int = 1) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(canonical_json(_bodies("schedule_ack")[i]))
    return data


def test_a_bad_transition_or_late_flag_breaks_the_ack_chain() -> None:
    schedule = _c2e()[1].body
    first = _bodies("schedule_ack")[0]
    completed = _ack_data()
    completed["state"] = {"kind": "lapsed"}
    completed["at"] = "2027-01-04T16:30:00Z"
    lapsed = ScheduleAck.model_validate_json(json.dumps(completed))
    assert verify_ack_chain(schedule, [first, lapsed]).state == "lapsed"
    shadow = _ack_data()
    shadow["state"] = {"kind": "shadowing"}
    with pytest.raises(AckChainBroken, match="cannot be shadowing"):
        verify_ack_chain(schedule, [first, ScheduleAck.model_validate_json(json.dumps(shadow))])
    # Transitions the state machine forbids, every other check satisfied.
    accepted_to_completed = _chain_for(
        schedule,
        [
            ("2027-01-04T15:55:41.3Z", {"kind": "accepted", "mode": "closed_loop"}),
            ("2027-01-04T16:30:00Z", {"kind": "completed"}),
        ],
    )
    with pytest.raises(AckChainBroken, match="accepted cannot move to completed"):
        verify_ack_chain(schedule, accepted_to_completed)
    active_twice = _chain_for(
        schedule,
        [
            ("2027-01-04T15:55:41.3Z", {"kind": "accepted", "mode": "closed_loop"}),
            ("2027-01-04T15:56:00Z", {"kind": "active"}),
            ("2027-01-04T15:57:00Z", {"kind": "active"}),
        ],
    )
    with pytest.raises(AckChainBroken, match="active cannot move to active"):
        verify_ack_chain(schedule, active_twice)
    early = _ack_data()
    early["at"] = "2027-01-04T15:55:50Z"
    with pytest.raises(AckChainBroken, match="before the first period"):
        verify_ack_chain(schedule, [first, ScheduleAck.model_validate_json(json.dumps(early))])
    late = _ack_data(0)
    late["late"] = True
    with pytest.raises(AckChainBroken, match="late"):
        verify_ack_chain(schedule, [ScheduleAck.model_validate_json(json.dumps(late))])
    with pytest.raises(ValidationError, match="first ack"):
        bad = _ack_data(0)
        bad["state"] = {"kind": "active"}
        ScheduleAck.model_validate_json(json.dumps(bad))


def test_a_rejection_lists_its_reasons_and_violations_together() -> None:
    data = _ack_data(0)
    data["envelope"] = {"kind": "not_applicable", "reason": "x"}
    data["state"] = {
        "kind": "rejected",
        "reasons": ["envelope_violation"],
        "violations": [],
        "detail": "x",
    }
    with pytest.raises(ValidationError, match="envelope_violation"):
        ScheduleAck.model_validate_json(json.dumps(data))
    data["state"]["reasons"] = ["wrong_site", "id_reused"]
    with pytest.raises(ValidationError, match="sorted"):
        ScheduleAck.model_validate_json(json.dumps(data))


def _chain_for(
    schedule: SetpointSchedule, states: list[tuple[str, dict[str, Any]]]
) -> list[ScheduleAck]:
    acks: list[ScheduleAck] = []
    for at, state in states:
        data = _ack_data(0)
        data.update(
            schedule_id=schedule.schedule_id,
            schedule_hash=schedule.content_hash(),
            seq=len(acks),
            prev_ack_hash=acks[-1].content_hash()
            if acks
            else {"kind": "not_applicable", "reason": "x"},
            at=at,
            state=state,
        )
        acks.append(ScheduleAck.model_validate_json(json.dumps(data)))
    return acks


def test_governing_timeline_refuses_overlap_and_a_supersession_that_did_not_happen() -> None:
    a = _schedule()
    b = _schedule(schedule_id="sched-b", supersedes=a.content_hash())
    accepted = {"kind": "accepted", "mode": "closed_loop"}
    a_acks = _chain_for(
        a,
        [
            ("2027-01-04T15:55:41.3Z", accepted),
            ("2027-01-04T15:56:00Z", {"kind": "active"}),
            ("2027-01-04T16:10:00Z", {"kind": "superseded", "by_schedule_hash": b.content_hash()}),
        ],
    )
    b_acks = _chain_for(
        b, [("2027-01-04T16:05:00Z", accepted), ("2027-01-04T16:10:00Z", {"kind": "active"})]
    )
    spans = governing_timeline([(a, a_acks), (b, b_acks)])
    assert [(s.schedule_hash, s.ended_by) for s in spans] == [
        (a.content_hash(), "superseded"),
        (b.content_hash(), "open"),
    ]
    b_early = _chain_for(
        b, [("2027-01-04T16:05:00Z", accepted), ("2027-01-04T16:06:00Z", {"kind": "active"})]
    )
    with pytest.raises(ValueError, match="did not go active"):
        governing_timeline([(a, a_acks), (b, b_early)])
    a_open = a_acks[:2]
    with pytest.raises(ValueError, match="at once"):
        governing_timeline([(a, a_open), (b, b_acks)])


# ------------------------------------------------------------------ heartbeat and staleness


def _obs(**fields: Any) -> LinkObservation:
    base: dict[str, Any] = {
        "now_mono_ms": 1_000_000,
        "now_utc": "2027-01-04T16:00:00Z",
        "last_valid_rx_mono_ms": 990_000,
        "lease_live_since_mono_ms": 0,
        "schedule_rx_mono_ms": 950_000,
        "schedule_covered_until": "2027-01-04T17:00:00Z",
        "wall_clock_trusted": True,
        **fields,
    }
    return LinkObservation.model_validate_json(json.dumps(base))


def _codes(**fields: Any) -> list[str]:
    return [c.value for c in link_state(_obs(**fields), _profile()).triggers]


def test_a_healthy_link_is_normal() -> None:
    v = link_state(_obs(), _profile())
    assert v.mode == "normal" and v.triggers == ()


@pytest.mark.parametrize(
    "fields,codes",
    [
        ({"last_valid_rx_mono_ms": 940_001}, []),
        ({"last_valid_rx_mono_ms": 940_000}, ["cloud_link_lost"]),  # lease exactly: fail-safe
        ({"last_valid_rx_mono_ms": 1_000_001}, ["cloud_link_lost"]),  # a reading from the future
        ({"lease_live_since_mono_ms": 970_001}, ["cloud_link_lost"]),  # inside the resume hold
        ({"lease_live_since_mono_ms": 970_000}, []),
        ({"schedule_rx_mono_ms": 880_001}, []),
        ({"schedule_rx_mono_ms": 880_000}, ["schedule_stale"]),  # max age exactly
        ({"schedule_covered_until": "2027-01-04T16:10:00.001Z"}, []),
        ({"schedule_covered_until": "2027-01-04T16:10:00Z"}, ["schedule_exhausted"]),
        ({"wall_clock_trusted": False}, ["clock_unsynchronised"]),
        (
            {
                "schedule_rx_mono_ms": {"kind": "not_applicable", "reason": "x"},
                "schedule_covered_until": {"kind": "not_applicable", "reason": "x"},
            },
            ["edge_restart"],
        ),
        (
            {
                "last_valid_rx_mono_ms": {"kind": "not_applicable", "reason": "x"},
                "lease_live_since_mono_ms": {"kind": "not_applicable", "reason": "x"},
            },
            ["cloud_link_lost"],
        ),
    ],
)
def test_each_threshold_and_its_equality_resolves_to_fail_safe(
    fields: dict[str, Any], codes: list[str]
) -> None:
    assert _codes(**fields) == codes


def test_the_cloud_declares_the_edge_silent_at_the_threshold() -> None:
    p = _profile()
    assert not edge_silent(160_000, 100_001, p)
    assert edge_silent(160_000, 100_000, p)
    assert edge_silent(0, not_applicable("never heard"), p)


def test_the_heartbeat_mode_and_triggers_agree() -> None:
    hb = json.loads(canonical_json(_bodies("edge_heartbeat")[0]))
    hb["active_triggers"] = ["cloud_link_lost"]
    with pytest.raises(ValidationError, match="fail-safe"):
        EdgeHeartbeat.model_validate_json(json.dumps(hb))
    hb["mode"] = "fail_safe"
    with pytest.raises(ValidationError, match="episode id"):
        EdgeHeartbeat.model_validate_json(json.dumps(hb))
    hb["episode_id"] = "ep-1"
    hb["active_triggers"] = ["edge_restart", "cloud_link_lost"]
    with pytest.raises(ValidationError, match="order"):
        EdgeHeartbeat.model_validate_json(json.dumps(hb))


# ------------------------------------------------------------------ fail-safe trigger codes


def test_the_trigger_table_is_frozen() -> None:
    assert [c.value for c in TriggerCode] == [
        "host_override",
        "operator_command",
        "control_authority_withdrawn",
        "no_envelope_in_force",
        "no_envelope_held",
        "clock_unsynchronised",
        "chain_integrity_failure",
        "thermal_comms_lost",
        "dno_trip",
        "dno_signal_lost",
        "site_meter_lost",
        "power_device_comms_lost",
        "cloud_link_lost",
        "market_link_lost",
        "schedule_stale",
        "schedule_exhausted",
        "edge_restart",
    ]
    assert set(TRIGGERS) == set(TriggerCode)
    assert [TRIGGERS[c].rank for c in TriggerCode] == [0] * 7 + [1] + [2] * 4 + [4] * 5
    assert NEURA_OPS_RELEASABLE == {
        TriggerCode.OPERATOR_COMMAND,
        TriggerCode.CHAIN_INTEGRITY_FAILURE,
    }
    assert TRIGGERS[TriggerCode.HOST_OVERRIDE].release == "host_release"
    assert TRIGGERS[TriggerCode.CONTROL_AUTHORITY_WITHDRAWN].release == "new_grant"


def test_the_primary_trigger_is_the_most_severe() -> None:
    C = TriggerCode
    assert primary_trigger([C.EDGE_RESTART, C.CLOUD_LINK_LOST]) is C.CLOUD_LINK_LOST
    assert primary_trigger([C.SCHEDULE_STALE, C.DNO_TRIP]) is C.DNO_TRIP
    assert primary_trigger([C.THERMAL_COMMS_LOST, C.NO_ENVELOPE_HELD]) is C.NO_ENVELOPE_HELD
    with pytest.raises(ValueError):
        primary_trigger([])


def test_a_trigger_that_needs_a_release_cannot_clear_without_one() -> None:
    na = {"kind": "not_applicable", "reason": "x"}
    release = {
        "code": "chain_integrity_failure",
        "command_id": "cmd-1",
        "audit_event_hash": "sha256:" + "6" * 64,
    }
    data = {"kind": "trigger_cleared", "code": "chain_integrity_failure", "primary": na}
    with pytest.raises(ValidationError, match="audited release"):
        TriggerCleared.model_validate_json(json.dumps({**data, "release": na}))
    TriggerCleared.model_validate_json(json.dumps({**data, "release": release}))
    with pytest.raises(ValidationError, match="no release"):
        TriggerCleared.model_validate_json(
            json.dumps(
                {**data, "code": "edge_restart", "release": {**release, "code": "edge_restart"}}
            )
        )


def test_the_cloud_may_release_only_neura_ops_codes() -> None:
    cmd = {
        "kind": "operator_command",
        "command_id": "cmd-1",
        "issued_at": "2027-01-04T16:00:00Z",
        "action": {"kind": "release_trigger", "code": "chain_integrity_failure"},
        "operator": "synthetic fixture operator",
        "audit_event_hash": "sha256:" + "6" * 64,
    }
    OperatorCommand.model_validate_json(json.dumps(cmd))
    for code in ("host_override", "dno_trip", "control_authority_withdrawn"):
        cmd["action"]["code"] = code
        with pytest.raises(ValidationError, match="not released by Neura ops"):
            OperatorCommand.model_validate_json(json.dumps(cmd))


def test_the_fixture_episode_verifies_and_exits_on_a_fresh_schedule() -> None:
    summary = verify_episode(_bodies("fail_safe_event"))
    assert summary.exited and summary.active == () and summary.head_seq == 3


def _episode_data() -> list[dict[str, Any]]:
    return [json.loads(canonical_json(e)) for e in _bodies("fail_safe_event")]


def _relink(events: list[dict[str, Any]]) -> list[FailSafeEvent]:
    out: list[FailSafeEvent] = []
    for i, e in enumerate(events):
        e["seq"] = i
        e["prev_hash"] = out[-1].content_hash() if out else GENESIS_PREV_HASH
        if e["event"]["kind"] == "exited":
            e["event"]["entered_hash"] = out[0].content_hash()
        out.append(FailSafeEvent.model_validate_json(json.dumps(e)))
    return out


def test_an_exit_needs_every_trigger_cleared_and_a_schedule_after_the_last_clear() -> None:
    events = _episode_data()
    with pytest.raises(ValueError, match="triggers active"):
        verify_episode(_relink([events[0], events[1], events[3]]))
    early = _episode_data()
    early[3]["event"]["resume_accepted_at"] = "2027-01-04T15:55:41.299999Z"
    with pytest.raises(ValueError, match="before the last clear"):
        verify_episode(_relink(early))
    wrong = _episode_data()
    wrong[1]["event"]["primary"] = "cloud_link_lost"
    with pytest.raises(ValueError, match="primary"):
        verify_episode(_relink(wrong))


def test_the_fail_safe_target_is_b1_02() -> None:
    env = _hybrid()
    full = fail_safe_target(env, AnmUnrestricted(kind="unrestricted"), L2)
    assert (full.import_limit_kw, full.resilience_floor_kwh, full.writes) == (
        10_000,
        1_000,
        "enforce",
    )
    assert full.markets == "declared_unavailable" and isinstance(full.chillers, NotApplicable)
    dno = fail_safe_target(env, AnmLimited(kind="limited", import_limit_kw=1_500), L3)
    assert dno.import_limit_kw == 1_500 and dno.chillers == "bms_default"
    assert fail_safe_target(env, AnmTripped(kind="tripped"), L2).import_limit_kw == 0
    assert fail_safe_target(env, AnmUnrestricted(kind="unrestricted"), L1).writes == "none"
    none = fail_safe_target(
        not_applicable("never installed"), AnmUnrestricted(kind="unrestricted"), L2
    )
    assert none.writes == "none" and isinstance(none.import_limit_kw, NotApplicable)


# ------------------------------------------------------------------ ANM, local only

#: Every ANM-ish word reachable in the cloud-to-edge schema, and why each may be there.
C2E_ANM_VOCABULARY = {
    "dno": "IC-4 IssuerRole (an evidence issuer) inside an embedded envelope",
    "idno": "IC-4 IssuerRole inside an embedded envelope",
    "dno_trip": "TriggerCode, reachable through ReleaseTrigger (whose validator refuses it)",
    "dno_signal_lost": "TriggerCode, reachable through ReleaseTrigger (refused the same way)",
    "planned_against_anm": "a hash the schedule carries for information, never a signal",
    "scheduled_dno_setpoint": "IC-3 ImportBasis inside an embedded envelope",
}


def _vocabulary(schema: dict[str, Any]) -> set[str]:
    import re

    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("properties", "$defs"):
                    for name, sub in v.items():
                        found.add(name)
                        walk(sub)
                elif k in ("const", "enum"):
                    found.update(
                        x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)
                    )
                elif k not in ("description", "title"):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return {w for w in found if re.search(r"(?i)anm|dno|trip", w)}


def test_no_cloud_to_edge_message_can_carry_an_anm_signal_or_state() -> None:
    schema = json.loads(schema_path("ic5_cloud_to_edge_frame", CONTRACT_VERSION).read_text())
    assert not [d for d in schema["$defs"] if d.startswith("Anm")]
    assert _vocabulary(schema) == set(C2E_ANM_VOCABULARY)
    e2c = json.loads(schema_path("ic5_edge_to_cloud_frame", CONTRACT_VERSION).read_text())
    assert {"AnmEvent", "AnmSetpoint", "AnmTrip", "AnmRelease"} <= set(e2c["$defs"])
    profile = json.loads(schema_path("ic5_link_profile", CONTRACT_VERSION).read_text())
    assert profile["properties"]["anm_authority"] == {
        "const": "edge_local",
        "title": "Anm Authority",
        "type": "string",
    }
    assert not [k for k in profile["properties"] if "anm" in k and k != "anm_authority"]
    # The latency path set has no ANM member.
    paths = profile["$defs"]["LatencyBudget"]["properties"]["path"]["enum"]
    assert not [p for p in paths if "anm" in p or "dno" in p]


def test_an_anm_event_is_edge_local() -> None:
    event = json.loads(canonical_json(_bodies("anm_event")[0]))
    event["path"] = "cloud"
    with pytest.raises(ValidationError):
        AnmEvent.model_validate_json(json.dumps(event))


def test_the_cloud_can_only_tighten_anm() -> None:
    """Property: whatever the cloud asks, the cap never exceeds an ANM limit; a trip is 0 kW."""
    rng = random.Random(5)
    values: list[int | NotApplicable] = [not_applicable("none"), 0, 1, 1_500, 10_000, 25_000]
    for _ in range(2_000):
        limit = rng.choice([0, 1, 1_499, 1_500, 30_000])
        state = rng.choice(
            [
                AnmUnrestricted(kind="unrestricted"),
                AnmLimited(kind="limited", import_limit_kw=limit),
                AnmTripped(kind="tripped"),
                unknown("signal lost"),
            ]
        )
        cap = effective_import_cap(
            schedule_kw=rng.choice(values),
            envelope_limit_kw=rng.choice(values),
            fail_safe_firm_kw=rng.choice(values),
            anm_state=state,
            in_fail_safe=rng.random() < 0.5,
        )
        if isinstance(state, AnmLimited):
            assert isinstance(cap.kw, int) and cap.kw <= state.import_limit_kw
        if isinstance(state, AnmTripped):
            assert cap.kw == 0 and cap.binding == "anm_trip"
    assert anm_triggers(AnmTripped(kind="tripped")) == (TriggerCode.DNO_TRIP,)
    assert anm_triggers(unknown("lost")) == (TriggerCode.DNO_SIGNAL_LOST,)
    assert anm_triggers(AnmLimited(kind="limited", import_limit_kw=5)) == ()


def test_the_anm_response_is_judged_exactly_against_the_ic1_agreement() -> None:
    agreement = ConnectionAgreement.model_validate_json(IC1_HYBRID.read_text())
    assert agreement.anm_response_time_s == 15.0  # synthetic IC-1 fixture value
    base = json.loads(canonical_json(_bodies("anm_event")[0]))

    def check(ms: Any) -> str:
        return verify_anm_response(
            AnmEvent.model_validate_json(json.dumps({**base, "response_elapsed_ms": ms})), agreement
        ).status

    assert check(3_250) == "within_required_time"
    assert check(15_000) == "within_required_time"
    assert check(15_001) == "late"
    assert check({"kind": "unknown", "reason": "not measured"}) == "unverifiable"
    firm = ConnectionAgreement.model_validate_json(
        (HERE / "fixtures" / "ic1_archetype_firm.synthetic.json").read_text()
    )
    with pytest.raises(ValueError, match="another agreement"):
        verify_anm_response(_bodies("anm_event")[0], firm)


# ------------------------------------------------------------------ mutation checks

#: (guard, exact source text, its in-memory replacement, the test that must go red).
MUTATIONS = [
    (
        "the schedule latency budget is required",
        "    cloud_to_edge_schedule: LatencyBudget\n",
        '    cloud_to_edge_schedule: LatencyBudget | NotApplicable = Field(discriminator="kind")\n',
        "test_the_schedule_latency_budget_is_a_required_contract_field",
    ),
    (
        "deliver_by is sent_at plus the bound",
        "    if s.deliver_by != deliver_by(frame.sent_at, profile):\n",
        "    if False:\n",
        "test_deliver_by_is_sent_at_plus_the_bound_and_the_edge_checks_it",
    ),
    (
        "late is strictly after deliver_by",
        "        late=received_at > s.deliver_by,\n",
        "        late=received_at >= s.deliver_by,\n",
        "test_a_late_schedule_is_flagged_never_refused",
    ),
    (
        "a pass needs observed plus skew within the bound",
        "        if obs + skew <= budget.bound_ms:\n",
        "        if obs <= budget.bound_ms:\n",
        "test_check_latency_verdicts",
    ),
    (
        "the lease expires at equality",
        "        if age < 0 or age >= profile.control_lease_ms:\n",
        "        if age < 0 or age > profile.control_lease_ms:\n",
        "test_each_threshold_and_its_equality_resolves_to_fail_safe",
    ),
    (
        "the resume hold holds",
        "        elif now - since < profile.resume_hold_ms:\n",
        "        elif False:\n",
        "test_each_threshold_and_its_equality_resolves_to_fail_safe",
    ),
    (
        "mpc cadence below the schedule age",
        "        if not self.mpc_cadence_ms < self.schedule_max_age_ms:\n",
        "        if False:\n",
        "test_the_spec_two_minute_staleness_and_five_minute_mpc_cannot_both_hold",
    ),
    (
        "a frame links to the head",
        "                if frame.prev_hash != self._hash[self.head]:\n",
        "                if False:\n",
        "test_a_fork_is_refused_and_the_epoch_stays_refused",
    ),
    (
        "a forked epoch stays refused",
        "        if self.broken:\n",
        "        if False:\n",
        "test_a_fork_is_refused_and_the_epoch_stays_refused",
    ),
    (
        "closed loop needs L2",
        "        or (closed and level < _STAGE[ControlStage.L2_BESS_CLOSED_LOOP])\n",
        "        or False\n",
        "test_authority_is_staged",
    ),
    (
        "the schedule is checked against the timeline it names",
        "    if timeline.content_hash() != schedule.resolved_hash:\n",
        "    if False:\n",
        "test_a_schedule_naming_another_timeline_is_envelope_unknown",
    ),
    (
        "the resilience floor is checked from the timeline",
        "    if low < t.energy.resilience_floor_kwh:\n",
        "    if False:\n",
        "test_the_timeline_check_is_ic3s_own_rule",
    ),
    (
        "a commanded chiller band sits inside the cell band",
        '        for what, temp in (("expected", p.temperature), ("chiller_band", chiller)):\n',
        '        for what, temp in (("expected", p.temperature),):\n',
        "test_a_chiller_band_must_sit_inside_the_cell_band",
    ),
    (
        "an ack chain follows the state machine",
        "        if kind not in allowed_next(state):\n",
        "        if False:\n",
        "test_a_bad_transition_or_late_flag_breaks_the_ack_chain",
    ),
    (
        "supersession is checked against the successor's activation",
        "        if active_at.get(successor) != at:\n",
        "        if False:\n",
        "test_governing_timeline_refuses_overlap_and_a_supersession_that_did_not_happen",
    ),
    (
        "no exit with a trigger active",
        '            if active:\n                raise bad(e, f"exit with triggers active',
        '            if False:\n                raise bad(e, f"exit with triggers active',
        "test_an_exit_needs_every_trigger_cleared_and_a_schedule_after_the_last_clear",
    ),
    (
        "a non-automatic trigger needs an audited release",
        "        if automatic != isinstance(self.release, NotApplicable):\n",
        "        if False:\n",
        "test_a_trigger_that_needs_a_release_cannot_clear_without_one",
    ),
    (
        "the cloud releases only neura_ops codes",
        "        if self.code not in NEURA_OPS_RELEASABLE:\n",
        "        if False:\n",
        "test_the_cloud_may_release_only_neura_ops_codes",
    ),
    (
        "ANM caps import whatever the cloud asks",
        '        candidates.append((anm_state.import_limit_kw, 1, "anm_setpoint"))\n',
        "        pass\n",
        "test_the_cloud_can_only_tighten_anm",
    ),
    (
        "the ANM response is judged exactly",
        "    if Fraction(elapsed, 1000) <= limit:\n",
        "    if Fraction(elapsed, 1000) <= limit + 1:\n",
        "test_the_anm_response_is_judged_exactly_against_the_ic1_agreement",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    return run_mutant("gridq.contracts.edge_cloud", old, new, f"{Path(__file__).resolve()}::{test}")


@pytest.mark.parametrize("test", sorted({m[3] for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(test: str) -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = Path(edge_cloud.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    assert _run_mutant(old, new, test) == 1, guard
