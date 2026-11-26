"""The reference endpoint over HTTP: the standard library's `ThreadingHTTPServer`, one lock.

Routes (every response is JSON and carries ``X-IC5-Reference: simulator``):

==========================  =====================================================================
``POST /ic5/v1/frames``     a frame: 202 ``{frame_hash, cursor}``; 200 on an identical replay;
                            400 malformed; 409 the same ``(direction, epoch, seq)`` with another
                            hash; 413 oversize; 415 not JSON; 422 ``{code, loc, detail}``
``GET /ic5/v1/frames``      ``?to=edge|cloud&after=<cursor>&limit=<n>``: ``{frames, cursor}``
``GET /ic5/v1/schema``      the three committed IC-5 JSON Schemas
``GET /ic5/v1/state``       the role's state, the simulated clock and the transcript hash
``POST /sim/v1/clock``      ``{"advance_ms": n}``: the only way time moves
``POST /sim/v1/faults``     ``{"drop_to_edge": bool, "hold_to_cloud": bool}``
``POST /sim/v1/anm``        ``{"signal": {...}, "response_ms": n}`` (edge twin)
``POST /sim/v1/reset``      back to boot
==========================  =====================================================================
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from gridq.contracts import edge_cloud
from gridq.edge_reference.machines import Simulation

HOST = "127.0.0.1"
_SCHEMAS = Path(edge_cloud.__file__).resolve().parent / "schemas"
_STEMS = ("ic5_cloud_to_edge_frame", "ic5_edge_to_cloud_frame", "ic5_link_profile")


def _schemas() -> dict[str, Any]:
    v = edge_cloud.CONTRACT_VERSION
    return {
        stem: json.loads((_SCHEMAS / f"{stem}.v{v}.schema.json").read_text(encoding="utf-8"))
        for stem in _STEMS
    }


class ReferenceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, sim: Simulation, port: int = 0) -> None:
        self.sim, self.lock = sim, threading.Lock()
        super().__init__((HOST, port), _Handler)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: ReferenceServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-IC5-Reference", "simulator")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _json(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._body() or b"{}")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        url = urlparse(self.path)
        sim = self.server.sim
        with self.server.lock:
            if url.path == "/ic5/v1/frames":
                q = parse_qs(url.query)
                try:
                    after = int(q.get("after", ["0"])[0])
                    limit = int(q.get("limit", ["100"])[0])
                except ValueError:
                    self._reply(400, {"code": "bad_query", "loc": "after", "detail": "integers"})
                    return
                self._reply(*sim.get_frames(q.get("to", [""])[0], after, limit))
            elif url.path == "/ic5/v1/schema":
                self._reply(200, _schemas())
            elif url.path == "/ic5/v1/state":
                self._reply(200, sim.state())
            else:
                self._reply(404, {"code": "not_found", "loc": "path", "detail": url.path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        path = urlparse(self.path).path
        sim = self.server.sim
        with self.server.lock:
            if path == "/ic5/v1/frames":
                raw = self._body()
                self._reply(*sim.post_frame(raw, self.headers.get("Content-Type", "")))
                return
            if path == "/sim/v1/reset":
                self._body()
                sim.reset()
                self._reply(200, sim.state())
                return
            data = self._json()
            if data is None:
                self._reply(400, {"code": "malformed_json", "loc": "", "detail": "a JSON object"})
            elif path == "/sim/v1/clock":
                self._reply(*sim.advance(data.get("advance_ms")))  # type: ignore[arg-type]
            elif path == "/sim/v1/faults":
                self._reply(
                    *sim.faults(
                        bool(data.get("drop_to_edge", False)),
                        bool(data.get("hold_to_cloud", False)),
                    )
                )
            elif path == "/sim/v1/anm":
                self._reply(*sim.anm(data.get("signal") or {}, data.get("response_ms")))  # type: ignore[arg-type]
            else:
                self._reply(404, {"code": "not_found", "loc": "path", "detail": path})


def serve(sim: Simulation, port: int = 0) -> ReferenceServer:
    """Start the endpoint on 127.0.0.1 in a background thread; ``server.shutdown()`` stops it."""
    server = ReferenceServer(sim, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
