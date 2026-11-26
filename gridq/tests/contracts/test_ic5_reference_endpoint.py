"""The IC-5 reference simulator endpoint (WP-EDGE-01 acceptance 3, D121).

Both sides can develop against it: a cloud-side client and an edge-side client exchange frames
through the relay over real HTTP; a cloud client runs against the edge twin and an edge client
against the cloud twin. Then the fault paths a real link has (dropped frames past the lease, a
DNO signal while the cloud link is down), every refusal code, determinism (the in-process and the
HTTP transcript hash agree), the import footprint, and mutation checks on the relay's guards.

Everything here is synthetic: the link profile, envelope and frames are labelled fixtures, and the
simulator refuses any site that is not ``SYNTHETIC-``.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _ic5_fixtures as fx
import pytest
from _contracts_support import FIXTURES_IC5, run_mutant

from gridq.contracts.__main__ import schema_path
from gridq.contracts._base import canonical_json
from gridq.contracts.dispatch_envelope import DispatchEnvelope
from gridq.contracts.edge_cloud import (
    CONTRACT_VERSION,
    CloudToEdgeFrame,
    ControlStage,
    EdgeToCloudFrame,
    FailSafeEvent,
    LinkProfile,
    ScheduleAck,
    verify_episode,
    verify_frame_chain,
)
from gridq.edge_reference import CloudReference, EdgeReference, Simulation
from gridq.edge_reference.http import ReferenceServer, serve
from gridq.edge_reference.machines import Refusal, parse_frame

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PKG = SRC / "gridq" / "edge_reference"
BOOT = datetime(2027, 1, 4, 15, 50, tzinfo=UTC)
STEP_MS = 10_000
SOE_KWH = 9_000  # synthetic: the fixture story's state of energy
L2 = ControlStage.L2_BESS_CLOSED_LOOP


def _profile() -> LinkProfile:
    path = FIXTURES_IC5 / "ic5_link_profile_hybrid.synthetic.json"
    return LinkProfile.model_validate_json(path.read_text(encoding="utf-8"))


def _envelope() -> DispatchEnvelope:
    return DispatchEnvelope.model_validate_json(fx.HYBRID.read_text(encoding="utf-8"))


def _c2e_line(i: int = 0) -> dict[str, Any]:
    lines = (FIXTURES_IC5 / "ic5_cloud_to_edge_hybrid.synthetic.jsonl").read_text().splitlines()
    data: dict[str, Any] = json.loads(lines[i])
    return data


def _e2c_line(i: int = 0) -> dict[str, Any]:
    lines = (FIXTURES_IC5 / "ic5_edge_to_cloud_hybrid.synthetic.jsonl").read_text().splitlines()
    data: dict[str, Any] = json.loads(lines[i])
    return data


def _raw(data: dict[str, Any]) -> bytes:
    return json.dumps(data).encode("utf-8")


# ------------------------------------------------------------------ an HTTP client, stdlib only


class Client:
    """What either side's developer writes: plain HTTP against the endpoint."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.headers: dict[str, str] = {}

    def call(
        self,
        method: str,
        path: str,
        body: bytes | dict[str, Any] | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.headers = dict(resp.headers)
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            self.headers = dict(err.headers)
            return err.code, json.loads(err.read())

    def post_frame(self, frame: CloudToEdgeFrame | EdgeToCloudFrame) -> tuple[int, dict[str, Any]]:
        return self.call("POST", "/ic5/v1/frames", canonical_json(frame).encode("utf-8"))

    def frames(self, to: str, after: int) -> tuple[list[dict[str, Any]], int]:
        status, body = self.call("GET", f"/ic5/v1/frames?to={to}&after={after}&limit=1000")
        assert status == 200, body
        return body["frames"], body["cursor"]

    def advance(self, ms: int) -> None:
        status, body = self.call("POST", "/sim/v1/clock", {"advance_ms": ms})
        assert status == 200, body


@pytest.fixture
def endpoint(request: pytest.FixtureRequest) -> Iterator[tuple[Simulation, Client]]:
    role = request.param
    sim = Simulation(role, _profile(), boot_utc=BOOT, envelope=_envelope(), soe_kwh=SOE_KWH)
    server = serve(sim)
    try:
        yield sim, Client(server.url)
    finally:
        server.shutdown()
        server.server_close()


def _cloud() -> CloudReference:
    return CloudReference(_profile(), boot_utc=BOOT, envelope=_envelope(), soe_kwh=SOE_KWH)


def _edge() -> EdgeReference:
    return EdgeReference(_profile(), boot_utc=BOOT, stage=L2, envelopes=(_envelope(),))


def _c2e(data: dict[str, Any]) -> CloudToEdgeFrame:
    return CloudToEdgeFrame.model_validate_json(json.dumps(data))


def _e2c(data: dict[str, Any]) -> EdgeToCloudFrame:
    return EdgeToCloudFrame.model_validate_json(json.dumps(data))


# ------------------------------------------------------------------ 3. both sides develop on it


@pytest.mark.parametrize("endpoint", ["relay"], indirect=True)
def test_a_cloud_client_and_an_edge_client_talk_through_the_relay(
    endpoint: tuple[Simulation, Client],
) -> None:
    """The relay validates and forwards; each side is a separate client of the same endpoint."""
    sim, http = endpoint
    edge, cloud = _edge(), _cloud()
    sent = {"edge": 0, "cloud": 0}
    cursor = {"edge": 0, "cloud": 0}
    got: dict[str, list[Any]] = {"edge": [], "cloud": []}
    for _ in range(30):
        for name, machine in (("edge", edge), ("cloud", cloud)):
            for frame in machine.out[sent[name] :]:
                status, body = http.post_frame(frame)
                assert status == 202 and body["frame_hash"] == frame.frame_hash(), body
                assert http.headers["X-IC5-Reference"] == "simulator"
            sent[name] = len(machine.out)
        raw, cursor["edge"] = http.frames("edge", cursor["edge"])
        for data in raw:
            got["edge"].append(_c2e(data))
            edge.receive(got["edge"][-1])
        raw, cursor["cloud"] = http.frames("cloud", cursor["cloud"])
        for data in raw:
            got["cloud"].append(_e2c(data))
            cloud.receive(got["cloud"][-1])
        edge.advance(STEP_MS)
        cloud.advance(STEP_MS)
    hb = edge.heartbeat()
    assert hb.mode == "normal" and isinstance(hb.governing_schedule, str)
    # The reference schedules at the envelope limit itself: a tie binds the more authoritative term.
    assert hb.import_cap.binding == "envelope"
    assert isinstance(cloud.accepted, str) and not cloud.state()["edge_silent"]
    # What each side received is the other's chain, intact and in order.
    assert verify_frame_chain(got["edge"]).frame_hash == got["edge"][-1].frame_hash()
    assert verify_frame_chain(got["cloud"]).frame_hash == got["cloud"][-1].frame_hash()
    assert sim.state()["mailboxes"] == {"edge": len(got["edge"]), "cloud": len(got["cloud"])}
    # The edge's acks verify as a chain for the schedule that governs.
    governing = edge.governing()
    assert governing is not None
    acks = [
        f.body
        for f in got["cloud"]
        if isinstance(f.body, ScheduleAck) and f.body.schedule_hash == governing.content_hash()
    ]
    assert [a.state.kind for a in acks] == ["accepted", "active"]


@pytest.mark.parametrize("endpoint", ["edge-twin"], indirect=True)
def test_a_cloud_client_develops_against_the_edge_twin(endpoint: tuple[Simulation, Client]) -> None:
    sim, http = endpoint
    cloud = _cloud()
    sent = cursor = 0
    verdicts: list[str] = []
    for _ in range(30):
        for frame in cloud.out[sent:]:
            status, body = http.post_frame(frame)
            assert status == 202, body
            verdicts.append(body["verdict"])
        sent = len(cloud.out)
        raw, cursor = http.frames("cloud", cursor)
        for data in raw:
            cloud.receive(_e2c(data))
        http.advance(STEP_MS)
        cloud.advance(STEP_MS)
    assert verdicts[0] == "new_epoch" and set(verdicts[1:]) == {"in_order"}
    status, state = http.call("GET", "/ic5/v1/state")
    assert status == 200 and state["role"] == "edge-twin"
    hb = state["heartbeat"]
    assert hb["mode"] == "normal" and hb["active_triggers"] == []
    assert hb["governing_schedule"] in {
        f.body.content_hash() for f in cloud.out if f.body.kind == "setpoint_schedule"
    }
    assert state["c2e_head"]["frame_hash"] == cloud.out[sent - 1].frame_hash()  # the last posted
    assert cloud.acks and not cloud.state()["edge_silent"]


@pytest.mark.parametrize("endpoint", ["cloud-twin"], indirect=True)
def test_an_edge_client_develops_against_the_cloud_twin(
    endpoint: tuple[Simulation, Client],
) -> None:
    sim, http = endpoint
    edge = _edge()
    sent = cursor = 0
    for _ in range(30):
        raw, cursor = http.frames("edge", cursor)
        for data in raw:
            edge.receive(_c2e(data))
        for frame in edge.out[sent:]:
            status, body = http.post_frame(frame)
            assert status == 202 and body["verdict"] in ("new_epoch", "in_order"), body
        sent = len(edge.out)
        http.advance(STEP_MS)
        edge.advance(STEP_MS)
    assert edge.heartbeat().mode == "normal" and edge.governing() is not None
    _, state = http.call("GET", "/ic5/v1/state")
    assert state["role"] == "cloud-twin" and state["edge_silent"] is False
    assert state["accepted_schedule"].startswith("sha256:") and state["acks"] > 0


@pytest.mark.parametrize("endpoint", ["relay"], indirect=True)
def test_the_endpoint_serves_the_committed_schemas(endpoint: tuple[Simulation, Client]) -> None:
    _, http = endpoint
    status, body = http.call("GET", "/ic5/v1/schema")
    assert status == 200
    for stem in ("ic5_cloud_to_edge_frame", "ic5_edge_to_cloud_frame", "ic5_link_profile"):
        assert body[stem] == json.loads(schema_path(stem, CONTRACT_VERSION).read_text())
    assert http.call("GET", "/nowhere")[0] == 404


# ------------------------------------------------------------------ the fault paths


def _drive(sim: Simulation, cloud: CloudReference, steps: int) -> list[EdgeToCloudFrame]:
    """The cloud reference against the in-process edge twin, ``steps`` x 10 s."""
    for _ in range(steps):
        for frame in cloud.out[cloud.sent :]:  # type: ignore[attr-defined]
            sim.post_frame(canonical_json(frame).encode())
        cloud.sent = len(cloud.out)  # type: ignore[attr-defined]
        _, body = sim.get_frames("cloud", cloud.cursor, 10_000)  # type: ignore[attr-defined]
        cloud.cursor = body["cursor"]  # type: ignore[attr-defined]
        for data in body["frames"]:
            cloud.receive(_e2c(data))
        sim.advance(STEP_MS)
        cloud.advance(STEP_MS)
    return [_e2c(json.loads(r)) for r in sim.mail["cloud"]]


def _edge_twin() -> tuple[Simulation, CloudReference]:
    sim = Simulation("edge-twin", _profile(), boot_utc=BOOT, envelope=_envelope())
    cloud = _cloud()
    cloud.sent, cloud.cursor = 0, 0  # type: ignore[attr-defined]
    return sim, cloud


def _episodes(frames: list[EdgeToCloudFrame]) -> dict[str, list[FailSafeEvent]]:
    out: dict[str, list[FailSafeEvent]] = {}
    for f in frames:
        if isinstance(f.body, FailSafeEvent):
            out.setdefault(f.body.episode_id, []).append(f.body)
    return out


def test_dropped_frames_past_the_lease_enter_fail_safe_and_a_fresh_schedule_exits() -> None:
    sim, cloud = _edge_twin()
    _drive(sim, cloud, 12)
    assert sim.state()["heartbeat"]["mode"] == "normal"
    sim.faults(drop_to_edge=True, hold_to_cloud=False)
    frames = _drive(sim, cloud, 7)  # 70 s of silence, past the 60 s lease
    hb = sim.state()["heartbeat"]
    assert hb["mode"] == "fail_safe" and "cloud_link_lost" in hb["active_triggers"]
    assert hb["governing_schedule"]["kind"] == "not_applicable"
    aborted = [
        f.body for f in frames if isinstance(f.body, ScheduleAck) and f.body.state.kind == "aborted"
    ]
    assert [a.state.trigger_code for a in aborted] == ["cloud_link_lost"]  # type: ignore[union-attr]
    sim.faults(drop_to_edge=False, hold_to_cloud=False)
    frames = _drive(sim, cloud, 12)
    assert sim.state()["heartbeat"]["mode"] == "normal"
    episodes = _episodes(frames)
    assert sorted(episodes) == ["ep-0001", "ep-0002"]
    for events in episodes.values():
        assert verify_episode(events).exited
    verify_frame_chain(frames)
    # The edge resumed on a schedule it accepted after the last trigger cleared.
    exit_event = episodes["ep-0002"][-1].event
    assert exit_event.kind == "exited"
    assert exit_event.resume_accepted_at >= episodes["ep-0002"][-2].at  # type: ignore[union-attr]


def test_a_dno_signal_with_the_link_down_is_enforced_at_once_and_replayed_unchanged() -> None:
    sim, cloud = _edge_twin()
    _drive(sim, cloud, 12)
    sim.faults(drop_to_edge=True, hold_to_cloud=True)
    _drive(sim, cloud, 7)
    before = len(sim.mail["cloud"])
    injected_at = sim.now
    status, body = sim.anm({"kind": "anm_setpoint", "import_limit_kw": 1_500}, 3_000)
    assert status == 200
    state = sim.state()
    assert state["heartbeat"]["anm_state"] == {"kind": "limited", "import_limit_kw": 1_500}
    assert state["heartbeat"]["import_cap"] == {"kw": 1_500, "binding": "anm_setpoint"}
    assert len(sim.mail["cloud"]) == before and state["held_to_cloud"] > 0
    sim.faults(drop_to_edge=False, hold_to_cloud=False)
    frames = [_e2c(json.loads(r)) for r in sim.mail["cloud"]]
    verify_frame_chain(frames)  # the held frames arrive in their original order, unchanged
    (event,) = [f.body for f in frames if f.body.kind == "anm_event"]
    assert event.content_hash() == body["event_hash"]
    assert event.received_at == injected_at and event.cloud_link == "down"
    assert event.response_elapsed_ms == 3_000 and event.path == "edge_local"


def test_a_conflicting_cloud_frame_forks_the_chain_and_holds_the_edge_in_fail_safe() -> None:
    sim, cloud = _edge_twin()
    _drive(sim, cloud, 12)
    posted = cloud.out[cloud.sent - 1]  # type: ignore[attr-defined]
    honest = json.loads(canonical_json(posted))
    honest["body"] = {"kind": "cloud_heartbeat", "market_link": "down"}
    status, body = sim.post_frame(_raw(honest))
    assert status == 409 and body["code"] == "conflict"
    hb = sim.state()["heartbeat"]
    assert hb["mode"] == "fail_safe" and "chain_integrity_failure" in hb["active_triggers"]
    frames = _drive(sim, cloud, 3)
    assert sim.state()["heartbeat"]["mode"] == "fail_safe"  # automatic recovery is not allowed
    entered = _episodes(frames)["ep-0002"][0].event
    assert entered.primary == "chain_integrity_failure"  # type: ignore[union-attr]


# ------------------------------------------------------------------ refusals


@pytest.mark.parametrize("endpoint", ["relay"], indirect=True)
def test_every_refusal_has_its_status_and_code(endpoint: tuple[Simulation, Client]) -> None:
    _, http = endpoint

    def post(raw: bytes, content_type: str = "application/json") -> tuple[int, str]:
        status, body = http.call("POST", "/ic5/v1/frames", raw, content_type)
        assert http.headers["X-IC5-Reference"] == "simulator"
        return status, body.get("code", "")

    def edited(**fields: Any) -> bytes:
        return _raw({**_c2e_line(0), **fields})

    assert post(edited(site_id="SITE-HYBRID")) == (422, "non_synthetic_site")
    assert post(edited(site_id="SYNTHETIC-SITE-OTHER")) == (422, "wrong_site")
    assert post(edited(body={"kind": "anm_trip"})) == (422, "anm_not_local")
    assert post(edited(contract_version="1.1.0")) == (422, "unsupported_contract_version")
    assert post(edited(contract="IC-3")) == (422, "unsupported_contract_version")
    assert post(edited(seq=True)) == (422, "invalid_frame")
    assert post(edited(profile_hash="sha256:" + "8" * 64)) == (422, "profile_mismatch")
    assert post(b"{" + b" " * 1_048_576 + b"}") == (413, "frame_too_large")
    assert post(b"{not json") == (400, "malformed_json")
    assert post(b"[]") == (400, "malformed_json")
    assert post(edited(), "text/plain") == (415, "unsupported_media_type")
    assert post(edited()) == (202, "")
    status, body = http.call("POST", "/ic5/v1/frames", edited())
    assert status == 200 and body["replay"] is True
    assert post(edited(body={"kind": "cloud_heartbeat", "market_link": "down"})) == (
        409,
        "conflict",
    )
    assert http.call("POST", "/sim/v1/clock", {"advance_ms": -1})[0] == 400
    assert (
        http.call("POST", "/sim/v1/anm", {"signal": {"kind": "anm_trip"}, "response_ms": 1})[1][
            "code"
        ]
        == "not_an_edge"
    )


@pytest.mark.parametrize("endpoint", ["edge-twin"], indirect=True)
def test_a_twin_receives_only_frames_addressed_to_it(endpoint: tuple[Simulation, Client]) -> None:
    _, http = endpoint
    status, body = http.call("POST", "/ic5/v1/frames", _raw(_e2c_line(0)))
    assert (status, body["code"]) == (422, "wrong_direction")


def test_the_relay_refuses_anm_cloud_to_edge_anywhere_in_the_body() -> None:
    """The guard the mutation check below reverts: an ANM kind nested at any depth is refused."""
    profile = _profile()
    for body in (
        {"kind": "anm_setpoint", "import_limit_kw": 5},
        {"kind": "operator_command", "action": {"kind": "anm_release"}},
        {"kind": "envelope_install", "x": [{"kind": "anm_event"}]},
    ):
        with pytest.raises(Refusal) as exc:
            parse_frame(_raw({**_c2e_line(0), "body": body}), profile)
        assert (exc.value.status, exc.value.code) == (422, "anm_not_local")
    # The same kinds edge to cloud are the ANM path working as designed.
    assert parse_frame(_raw(_e2c_line(7)), profile).body.kind == "anm_event"


def test_the_simulator_serves_synthetic_sites_only() -> None:
    data = json.loads((FIXTURES_IC5 / "ic5_link_profile_hybrid.synthetic.json").read_text())
    data.update(site_id="SITE-HYBRID", record_class="live")
    live = LinkProfile.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError, match="SYNTHETIC-"):
        Simulation("relay", live, boot_utc=BOOT)


# ------------------------------------------------------------------ determinism


def _script() -> list[tuple[str, Any]]:
    cloud = _cloud()
    ops: list[tuple[str, Any]] = []
    for i in range(8):
        for frame in cloud.out[len([o for o in ops if o[0] == "post"]) :]:
            ops.append(("post", canonical_json(frame).encode()))
        ops.append(("get", 0))
        if i == 3:
            ops.append(("faults", (True, True)))
            ops.append(("anm", ({"kind": "anm_trip"}, 1_200)))
        if i == 5:
            ops.append(("faults", (False, False)))
        ops.append(("clock", STEP_MS))
        cloud.advance(STEP_MS)
    return ops


def test_the_http_endpoint_and_the_in_process_simulation_write_the_same_transcript() -> None:
    ops = _script()
    local = Simulation("edge-twin", _profile(), boot_utc=BOOT, envelope=_envelope())
    remote = Simulation("edge-twin", _profile(), boot_utc=BOOT, envelope=_envelope())
    server = serve(remote)
    http = Client(server.url)
    try:
        for op, arg in ops:
            if op == "post":
                assert local.post_frame(arg) == http.call("POST", "/ic5/v1/frames", arg)
            elif op == "get":
                assert (
                    local.get_frames("cloud", arg, 1000)[1]
                    == http.call("GET", f"/ic5/v1/frames?to=cloud&after={arg}&limit=1000")[1]
                )
            elif op == "faults":
                local.faults(*arg)
                http.call(
                    "POST", "/sim/v1/faults", {"drop_to_edge": arg[0], "hold_to_cloud": arg[1]}
                )
            elif op == "anm":
                assert local.anm(*arg) == http.call(
                    "POST", "/sim/v1/anm", {"signal": arg[0], "response_ms": arg[1]}
                )
            else:
                local.advance(arg)
                http.advance(arg)
        assert local.transcript_hash() == remote.transcript_hash()
        assert http.call("GET", "/ic5/v1/state")[1] == json.loads(json.dumps(local.state()))
        # The same calls on a reset simulation give the same transcript again.
        first = local.transcript_hash()
        local.reset()
        for op, arg in ops:
            {
                "post": lambda a: local.post_frame(a),
                "get": lambda a: local.get_frames("cloud", a, 1000),
                "faults": lambda a: local.faults(*a),
                "anm": lambda a: local.anm(*a),
                "clock": lambda a: local.advance(a),
            }[op](arg)
        assert local.transcript_hash() == first
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ the import footprint

_ALLOWED_THIRD_PARTY = {"pydantic"}


def test_the_reference_package_imports_only_the_stdlib_pydantic_and_the_contracts() -> None:
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                ok = (
                    top in sys.stdlib_module_names
                    or top in _ALLOWED_THIRD_PARTY
                    or name == "gridq.contracts"
                    or name.startswith(("gridq.contracts.", "gridq.edge_reference"))
                )
                assert ok, f"{path.name} imports {name}"


def test_a_fresh_interpreter_runs_the_endpoint_without_the_engine() -> None:
    script = (
        "import json, sys\n"
        "import gridq.edge_reference.http, gridq.edge_reference.__main__\n"
        "mods = sorted(m for m in sys.modules if m.startswith('gridq.')\n"
        "    and not m.startswith(('gridq.contracts', 'gridq.edge_reference')))\n"
        "heavy = sorted(m for m in ('numpy','pandas','linopy','highspy','xarray','scipy')\n"
        "    if m in sys.modules)\n"
        "print(json.dumps([mods, heavy]))\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    assert json.loads(res.stdout) == [[], []]


def test_the_command_line_starts_an_endpoint() -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gridq.edge_reference",
            "--role",
            "edge-twin",
            "--profile",
            str(FIXTURES_IC5 / "ic5_link_profile_hybrid.synthetic.json"),
            "--envelope",
            str(fx.HYBRID),
            "--start",
            "2027-01-04T15:50:00Z",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    try:
        assert proc.stdout is not None
        hello = json.loads(proc.stdout.readline())
        assert hello["role"] == "edge-twin" and hello["url"].startswith("http://127.0.0.1:")
        status, state = Client(hello["url"]).call("GET", "/ic5/v1/state")
        assert status == 200 and state["heartbeat"]["mode"] == "fail_safe"  # booted, no cloud yet
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_the_server_binds_loopback_only() -> None:
    sim = Simulation("relay", _profile(), boot_utc=BOOT)
    server = ReferenceServer(sim, 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


# ------------------------------------------------------------------ mutation checks

MUTATIONS = [
    (
        "the relay refuses ANM cloud to edge",
        '        isinstance(n, dict) and n.get("kind") in _ANM_KINDS'
        ' for n in _walk(data.get("body"))\n',
        "        False for n in ()\n",
        "test_the_relay_refuses_anm_cloud_to_edge_anywhere_in_the_body",
    ),
    (
        "the relay refuses non-synthetic sites",
        '    if not isinstance(site, str) or not site.startswith("SYNTHETIC-"):\n',
        "    if not isinstance(site, str):\n",
        "test_every_refusal_has_its_status_and_code",
    ),
    (
        "the relay refuses a conflicting frame",
        "        if seen is not None and seen != h:\n",
        "        if False:\n",
        "test_every_refusal_has_its_status_and_code",
    ),
    (
        "the edge holds fail-safe on a forked cloud chain",
        "        if verdict.status is ChainStatus.FORK:\n",
        "        if False:\n",
        "test_a_conflicting_cloud_frame_forks_the_chain_and_holds_the_edge_in_fail_safe",
    ),
    (
        "a held frame is released when the hold clears",
        "            for raw in self.held:\n",
        "            for raw in ():\n",
        "test_a_dno_signal_with_the_link_down_is_enforced_at_once_and_replayed_unchanged",
    ),
]


def _run_mutant(old: str, new: str, test: str) -> int:
    nodeid = f"{Path(__file__).resolve()}::{test}"
    return run_mutant("gridq.edge_reference.machines", old, new, nodeid)


@pytest.mark.parametrize("test", sorted({m[3] for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(test: str) -> None:
    assert _run_mutant("", "", test) == 0, test


@pytest.mark.parametrize("guard,old,new,test", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_reverting_a_guard_turns_its_test_red(guard: str, old: str, new: str, test: str) -> None:
    source = (PKG / "machines.py").read_text(encoding="utf-8")
    assert source.count(old) == 1, guard
    assert _run_mutant(old, new, test) == 1, guard
