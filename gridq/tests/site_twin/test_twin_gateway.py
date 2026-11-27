"""WP-TWIN-01 acceptance 1: the edge gateway connects to the twin with no twin-specific code
path (D196).

Proved structurally, not by one happy run:

1. the gateway package imports only the standard library and the contracts, and the twin never
   imports the gateway (both by AST, and in a fresh interpreter);
2. no gateway line names the twin, a synthetic label or the simulated protocol, and none compares
   a record class, protocol or tenant against a literal;
3. the same gateway drives an independent Modbus responder written here, sharing no code with the
   twin, from a ``live`` / ``modbus_tcp`` commissioning set, and its batches pass `check_batch` in
   both runs;
4. the request bytes it sends the twin and the responder are identical;
5. its constructor takes the address, the commissioning set, the allow-list and the clock, and
   nothing else;
6. a write the allow-list refuses sends no byte, and a write to a read-only point draws a Modbus
   exception from both servers.

Then the closed loop: the IC-5 reference edge and cloud agree a schedule, the gateway carries it
to the twin, and the twin's telemetry comes back as IC-14 batches.

Everything is synthetic. The responder's commissioning set is built in memory only, never written
to disk: its ``live`` label and the non-SYNTHETIC tenant ``NOTASITE-ORG`` / ``NOTASITE-RESPONDER``
exist solely to prove the gateway does not read labels (IC-14 allows the ``modbus_tcp`` protocol
on ``live`` records only).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _twin_support as ts
import pytest

from gridq.contracts.dispatch_envelope import DispatchEnvelope
from gridq.contracts.edge_cloud import ControlStage, LinkProfile, SetpointPeriod
from gridq.contracts.telemetry_series import (
    TelemetryBatch,
    ValueRun,
    check_batch,
    verify_batch_chain,
)
from gridq.edge_design.model import BmsConduitMode
from gridq.edge_design.query import write_bindings
from gridq.edge_reference import CloudReference, EdgeReference
from gridq.edge_southbound import (
    Commissioning,
    ModbusClient,
    ModbusError,
    SouthboundGateway,
    WriteRefused,
)

GATEWAY = ts.SRC / "gridq" / "edge_southbound"
TWIN = ts.SRC / "gridq" / "site_twin"
L2 = ControlStage.L2_BESS_CLOSED_LOOP
ALLOW_L2 = [str(b.point_class) for b in write_bindings(L2, BmsConduitMode.DIODE_READ_ONLY)]


# --------------------------------------------------------------------------- the responder


class Responder:
    """An independent Modbus TCP server (raw sockets, its own framing): a fixed table of four-word
    points and a holding table it accepts writes to. Shares no code with the twin."""

    def __init__(self, inputs: dict[tuple[int, int], int], holding: dict[tuple[int, int], int]):
        self.inputs, self.holding = dict(inputs), dict(holding)
        self.units = {u for u, _ in inputs} | {u for u, _ in holding}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.received: list[bytes] = []
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(conn,), daemon=True).start()

    def _conn(self, conn: socket.socket) -> None:
        with conn:
            while True:
                head = conn.recv(7, socket.MSG_WAITALL)
                if len(head) < 7:
                    return
                tid, _proto, length, unit = struct.unpack(">HHHB", head)
                pdu = conn.recv(length - 1, socket.MSG_WAITALL)
                self.received.append(head + pdu)
                reply = self._pdu(unit, pdu)
                conn.sendall(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)

    def _pdu(self, unit: int, pdu: bytes) -> bytes:
        fc = pdu[0]
        if unit not in self.units:
            return bytes([fc | 0x80, 0x0B])
        if fc in (3, 4):
            addr, n = struct.unpack(">HH", pdu[1:5])
            table = self.holding if fc == 3 else self.inputs
            if (unit, addr) not in table:
                return bytes([fc | 0x80, 0x02])
            if n != 4:
                return bytes([fc | 0x80, 0x03])
            raw = table[(unit, addr)].to_bytes(8, "big", signed=True)
            return bytes([fc, 8]) + raw
        if fc == 16:
            addr, n, nb = struct.unpack(">HHB", pdu[1:6])
            if (unit, addr) not in self.holding:
                return bytes([fc | 0x80, 0x02])
            if n != 4 or nb != 8:
                return bytes([fc | 0x80, 0x03])
            self.holding[(unit, addr)] = int.from_bytes(pdu[6:14], "big", signed=True)
            return pdu[:5]
        return bytes([fc | 0x80, 0x01])

    def close(self) -> None:
        self._stop = True
        self.sock.close()


@pytest.fixture
def twin_and_server() -> Iterator[tuple[Any, Any]]:
    twin = ts.twin()
    server = twin.serve()
    yield twin, server
    server.stop()


def _relabel(doc: str) -> str:
    """The twin's commissioning set as a live Modbus site would declare it: the same wire
    bindings and points, under a tenant that is not SYNTHETIC-, ``live``, ``modbus_tcp``."""
    data = json.loads(doc)
    org, site = "NOTASITE-ORG", "NOTASITE-RESPONDER"
    data["site_id"] = site
    data["sender_id"] = "NOTASITE-EDGE"
    for p in data["points"]:
        d = p["descriptor"]
        key = d["series_id"].split("/", 2)[2]
        d["tenant"] = {**d["tenant"], "org_id": org, "site_id": site}
        d["site_id"] = site
        d["series_id"] = f"{org}/{site}/{key}"
        d["record_class"] = "live"
        d["source"] = {**d["source"], "protocol": "modbus_tcp"}
    return json.dumps(data)


class FixedClock:
    """A clock the test moves by hand (wall and monotonic together)."""

    boot_id = "NOTASITE-BOOT"

    def __init__(self, start: datetime) -> None:
        self.start, self.ms = start, 0

    def wall(self) -> datetime:
        from datetime import timedelta

        return self.start + timedelta(milliseconds=self.ms)

    def mono_ms(self) -> int:
        return self.ms

    def offset_bound_ms(self) -> int | None:
        return 20


def _check_all(
    com: Commissioning, batches: list[TelemetryBatch]
) -> dict[str, list[TelemetryBatch]]:
    by: dict[str, list[TelemetryBatch]] = {}
    for b in batches:
        point = next(p for p in com.points if p.descriptor.series_id == b.series_id)
        check_batch(point.descriptor, b)
        by.setdefault(b.series_id, []).append(b)
    for chain in by.values():
        verify_batch_chain(chain)
    assert set(by) == {p.descriptor.series_id for p in com.points}
    return by


def _run(gw: SouthboundGateway, advance: Any, seconds: int) -> list[TelemetryBatch]:
    """The script both runs share: a write, then a poll a second, sealing as blocks close."""
    out: list[TelemetryBatch] = []
    gw.write("bess_setpoint", -400_000)
    gw.write("import_limit", 2_000_000)
    for _ in range(seconds):
        gw.poll()
        advance(1000)
        out += gw.seal()
    return out


# --------------------------------------------------------------------------- 1. imports


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append(node.module)
    return out


def test_the_gateway_imports_only_the_stdlib_and_the_contracts() -> None:
    files = sorted(GATEWAY.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "gateway.py", "modbus.py"}
    for path in files:
        for name in _imports(path):
            top = name.split(".")[0]
            ok = (
                top in sys.stdlib_module_names
                or name == "gridq.contracts"
                or name.startswith(("gridq.contracts.", "gridq.edge_southbound"))
            )
            assert ok, f"{path.name} imports {name}"


def test_the_twin_never_imports_the_gateway() -> None:
    for path in sorted(TWIN.glob("*.py")):
        for name in _imports(path):
            assert not name.startswith("gridq.edge_southbound"), f"{path.name} imports {name}"


def test_a_fresh_interpreter_loads_the_gateway_without_the_engine_or_the_twin() -> None:
    script = (
        "import json, sys\n"
        "import gridq.edge_southbound\n"
        "mods = sorted(m for m in sys.modules if m.startswith('gridq.')\n"
        "    and not m.startswith(('gridq.contracts', 'gridq.edge_southbound')))\n"
        "heavy = sorted(m for m in ('numpy', 'pandas', 'scipy') if m in sys.modules)\n"
        "print(json.dumps([mods, heavy]))\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ts.SRC)},
        check=True,
    )
    assert json.loads(res.stdout) == [[], []]


# --------------------------------------------------------------------------- 2. no branch


_FORBIDDEN = ("twin", "synthetic", "simulated")
_LABELS = {"record_class", "protocol", "org_kind", "data_class", "source"}
_TENANT = {"site_id", "org_id", "tenant", "series_id"}


def _docstrings(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _attr_names(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name)
    }


def test_no_gateway_line_names_the_twin_or_branches_on_a_label() -> None:
    for path in sorted(GATEWAY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docs:
                    continue
                low = node.value.lower()
                assert not any(w in low for w in _FORBIDDEN), (path.name, node.value)
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.FunctionDef | ast.ClassDef | ast.arg):
                name = getattr(node, "name", None) or getattr(node, "arg", None)
            if name is not None:
                assert not any(w in name.lower() for w in _FORBIDDEN), (path.name, name)
            if isinstance(node, ast.Compare):
                names = _attr_names(node)
                assert not names & _LABELS, (path.name, ast.unparse(node))
                literal = any(
                    isinstance(c, ast.Constant) and isinstance(c.value, str)
                    for c in [node.left, *node.comparators]
                )
                assert not (names & _TENANT and literal), (path.name, ast.unparse(node))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("startswith", "endswith"), (path.name,)


# --------------------------------------------------------------------------- 3, 4. same bytes


def test_the_same_gateway_drives_the_twin_and_an_independent_responder_byte_for_byte(
    twin_and_server: tuple[Any, Any],
) -> None:
    twin, server = twin_and_server
    com_twin = Commissioning.from_json(twin.commissioning(ts.SENDER))
    gw_twin = SouthboundGateway("127.0.0.1", server.port, com_twin, ALLOW_L2, ts.TwinClock(twin))
    seconds = 125
    try:
        batches_twin = _run(gw_twin, twin.advance, seconds)
    finally:
        gw_twin.close()

    com_live = Commissioning.from_json(_relabel(twin.commissioning(ts.SENDER)))
    inputs = {
        (p.wire.unit_id, p.wire.address): 1000 + i
        for i, p in enumerate(com_live.points)
        if p.wire.table == "input"
    }
    holding = {
        (p.wire.unit_id, p.wire.address): 0 for p in com_live.points if p.wire.table == "holding"
    }
    responder = Responder(inputs, holding)
    clock = FixedClock(ts.BOOT)
    gw_live = SouthboundGateway("127.0.0.1", responder.port, com_live, ALLOW_L2, clock)

    def tick(ms: int) -> None:
        clock.ms += ms

    try:
        batches_live = _run(gw_live, tick, seconds)
    finally:
        gw_live.close()
        responder.close()

    by_twin = _check_all(com_twin, batches_twin)
    by_live = _check_all(com_live, batches_live)
    assert len(batches_twin) == len(batches_live) == 2 * len(com_twin.points)
    assert {b.record_class for b in batches_twin} == {"synthetic_fixture"}
    assert {b.record_class for b in batches_live} == {"live"}
    # 4. Wire identity: the requests are the same bytes, transaction ids included.
    assert gw_twin.wire_log == gw_live.wire_log == responder.received
    assert len(gw_twin.wire_log) == 2 + seconds * len(com_twin.points)
    # The twin's battery followed the write; the responder stored it.
    assert responder.holding[(1, 0)] == -400_000
    soe = by_twin[f"{ts.ORG}/{ts.SITE}/bess.bess-1.active_power.device_reported.whole"]
    first = soe[0].segments[0]
    assert isinstance(first, ValueRun) and first.values[1:4] == (-400_000,) * 3
    # Every slot of every block was acquired: no gap, all good.
    for chain in (*by_twin.values(), *by_live.values()):
        for b in chain:
            assert all(isinstance(s, ValueRun) for s in b.segments), b.series_id


def test_the_gateway_reports_what_the_wire_says_and_nothing_else(
    twin_and_server: tuple[Any, Any],
) -> None:
    """A point the server does not have is a configuration error; a dead server is comms lost."""
    twin, server = twin_and_server
    data = json.loads(twin.commissioning(ts.SENDER))
    data["points"][0]["wire"]["address"] = 100  # not a point on the twin
    com = Commissioning.from_json(json.dumps(data))
    gw = SouthboundGateway("127.0.0.1", server.port, com, ALLOW_L2, ts.TwinClock(twin))
    try:
        for _ in range(60):
            gw.poll()
            twin.advance(1000)
        server.stop()
        gw.close()
        for _ in range(60):
            gw.poll()
            twin.advance(1000)
        batches = gw.seal()
    finally:
        gw.close()
    by = _check_all(com, batches)
    bad = by[com.points[0].descriptor.series_id]
    assert [s.model_dump()["reason"] for s in bad[0].segments] == ["configuration_error"]
    good = by[com.points[1].descriptor.series_id]
    assert isinstance(good[0].segments[0], ValueRun)
    assert [s.model_dump().get("reason") for s in good[1].segments] == ["comms_lost"]


# --------------------------------------------------------------------------- 5. opacity


def test_the_gateway_is_given_its_site_as_data_only() -> None:
    params = list(inspect.signature(SouthboundGateway.__init__).parameters)
    assert params == ["self", "host", "port", "commissioning", "allow_list", "clock"]


# --------------------------------------------------------------------------- 6. allow-list


def test_a_refused_write_sends_nothing_and_read_only_points_refuse_writes(
    twin_and_server: tuple[Any, Any],
) -> None:
    twin, server = twin_and_server
    com = Commissioning.from_json(twin.commissioning(ts.SENDER))
    allow_l1 = [str(b.point_class) for b in write_bindings(ControlStage.L1_SHADOW)]
    assert allow_l1 == []
    gw = SouthboundGateway("127.0.0.1", server.port, com, allow_l1, ts.TwinClock(twin))
    try:
        with pytest.raises(WriteRefused):
            gw.write("bess_setpoint", -100_000)
        assert gw.wire_log == []
        com_live = Commissioning.from_json(_relabel(twin.commissioning(ts.SENDER)))
        responder = Responder({(2, 0): 5}, {(1, 0): 0})
        try:
            for port in (server.port, responder.port):
                with ModbusClient("127.0.0.1", port) as c:
                    with pytest.raises(ModbusError) as err:
                        c.write_multiple(2, 0, (0, 0, 0, 1))  # the site meter: read only
                    assert err.value.code in (0x02, 0x03)
        finally:
            responder.close()
        assert com_live.writable("bess_setpoint") is not None
    finally:
        gw.close()
    twin.advance(2000)
    assert twin.setpoint_w == 0


# --------------------------------------------------------------------------- the closed loop


def _ic5() -> tuple[LinkProfile, DispatchEnvelope]:
    fix = ts.TESTS / "contracts" / "fixtures"
    profile = LinkProfile.model_validate_json(
        (fix / "ic5" / "ic5_link_profile_hybrid.synthetic.json").read_text(encoding="utf-8")
    )
    env = DispatchEnvelope.model_validate_json(
        (fix / "ic3" / "ic3_envelope_hybrid_bess.synthetic.json").read_text(encoding="utf-8")
    )
    return profile, env


def _period(start: str, end: str, kw: int, cap_kw: int) -> SetpointPeriod:
    return SetpointPeriod.model_validate_json(
        json.dumps(
            {
                "start": start,
                "end": end,
                "bess": {
                    "kind": "dispatch",
                    "direction": "discharge",
                    "kw": kw,
                    "merchant_kw": kw,
                    "service_delivery": [],
                },
                "site_import_cap_kw": cap_kw,
                "chiller_band": {"kind": "not_applicable", "reason": "synthetic: no band"},
                "expected": {
                    "soe_end_kwh": 0,
                    "site_import_kw": 0,
                    "site_export_kw": 0,
                    "temperature": {"kind": "not_applicable", "reason": "synthetic"},
                },
            }
        )
    )


def test_the_ic5_loop_reaches_the_twin_through_the_gateway() -> None:
    """The reference cloud schedules, the reference edge governs, the gateway actuates what the
    edge's heartbeat states, and the twin's telemetry returns as IC-14 batches. The twin's battery
    is the IC-3 hybrid envelope's (10 MWh, 5 MW each way) at the IC-5 story's 9 MWh."""
    profile, env = _ic5()
    assert profile.site_id == ts.SITE
    twin = ts.twin(
        spec=ts.bess_spec(usable=10_000, discharge_kw=5000, charge_kw=5000, initial=9000)
    )
    server = twin.serve()
    edge = EdgeReference(profile, boot_utc=ts.BOOT, stage=L2, envelopes=(env,))
    cloud = CloudReference(profile, boot_utc=ts.BOOT, envelope=env, soe_kwh=9000)
    com = Commissioning.from_json(twin.commissioning(ts.SENDER))
    gw = SouthboundGateway("127.0.0.1", server.port, com, ALLOW_L2, ts.TwinClock(twin))
    sent = [0, 0]
    batches: list[TelemetryBatch] = []
    caps: set[int] = set()
    try:
        for _ in range(180):
            for frame in cloud.out[sent[0] :]:
                edge.receive(frame)
            sent[0] = len(cloud.out)
            for frame in edge.out[sent[1] :]:
                cloud.receive(frame)
            sent[1] = len(edge.out)
            hb = edge.heartbeat()
            gov = edge.governing()
            period = (
                next((p for p in gov.periods if p.start <= edge.now < p.end), None)
                if gov is not None
                else None
            )
            gw.actuate(period, hb.import_cap)
            if isinstance(hb.import_cap.kw, int):
                caps.add(hb.import_cap.kw)
            gw.poll()
            for m in (edge, cloud, twin):
                m.advance(1000)
            batches += gw.seal()
        assert edge.governing() is not None, "the reference loop agreed a schedule"
        assert len(caps) == 1
        assert twin.import_limit_w == next(iter(caps)) * 1000
        assert twin.setpoint_w == 0  # the reference cloud schedules an idle battery
        # A discharge period, as the edge would hold one, reaches the battery at the next second.
        dispatch = _period("2027-01-04T16:03:00Z", "2027-01-04T16:30:00Z", 300, 9000)
        assert gw.actuate(dispatch, hb.import_cap) == {"bess_setpoint": -300_000}
        twin.advance(2000)
        assert twin.seconds[-1].bess.power_w == -300_000
    finally:
        gw.close()
        server.stop()
    by = _check_all(com, batches)
    assert all(len(chain) == 3 for chain in by.values())
    assert gw.refused == []
    assert batches[-1].origin == datetime(2027, 1, 4, 16, 2, tzinfo=UTC)
