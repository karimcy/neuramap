"""The twin's Modbus TCP server: real sockets, loopback only (WP-TWIN-01, D196).

Its own server-side framing (MBAP header, function codes 0x03, 0x04 and 0x10, exception replies),
written apart from the reference gateway's client so the twin never imports the gateway and a
shared bug cannot hide on both sides of the wire.

Answers: a read must start on a point and ask for exactly its four registers; a write must cover
exactly one holding point. Exceptions: 0x01 an unsupported function, 0x02 an address that is not
a point (or a write to a point that is not a holding register), 0x03 a quantity that is not four
registers, 0x0B a unit id the site does not have.
"""

from __future__ import annotations

import socketserver
import struct
import threading
from collections.abc import Callable
from typing import Final

__all__ = ["RegisterBank", "TwinModbusServer"]

_MBAP: Final = struct.Struct(">HHHB")
_ILLEGAL_FUNCTION: Final = 0x01
_ILLEGAL_ADDRESS: Final = 0x02
_ILLEGAL_VALUE: Final = 0x03
_NO_TARGET: Final = 0x0B

#: (unit, table, address) -> a reader of the point's current value.
Readers = dict[tuple[int, str, int], Callable[[], int]]
#: (unit, address) -> a writer of a holding point.
Writers = dict[tuple[int, int], Callable[[int], None]]


class RegisterBank:
    """What the server serves: the readers and writers, under the twin's lock."""

    def __init__(self, readers: Readers, writers: Writers, lock: threading.Lock) -> None:
        self.readers, self.writers, self.lock = readers, writers, lock
        self.units = frozenset(u for u, _, _ in readers) | frozenset(u for u, _ in writers)

    def answer(self, unit: int, pdu: bytes) -> bytes:
        if not pdu:
            return bytes((0x80, _ILLEGAL_FUNCTION))
        fc = pdu[0]
        if unit not in self.units:
            return bytes((fc | 0x80, _NO_TARGET))
        if fc in (0x03, 0x04) and len(pdu) == 5:
            address, count = struct.unpack(">HH", pdu[1:5])
            table = "holding" if fc == 0x03 else "input"
            reader = self.readers.get((unit, table, address))
            if reader is None:
                return bytes((fc | 0x80, _ILLEGAL_ADDRESS))
            if count != 4:
                return bytes((fc | 0x80, _ILLEGAL_VALUE))
            with self.lock:
                value = reader()
            return struct.pack(">BB4H", fc, 8, *struct.unpack(">4H", struct.pack(">q", value)))
        if fc == 0x10 and len(pdu) >= 6:
            address, count, nbytes = struct.unpack(">HHB", pdu[1:6])
            writer = self.writers.get((unit, address))
            if writer is None:
                return bytes((fc | 0x80, _ILLEGAL_ADDRESS))
            if count != 4 or nbytes != 8 or len(pdu) != 14:
                return bytes((fc | 0x80, _ILLEGAL_VALUE))
            (value,) = struct.unpack(">q", pdu[6:14])
            with self.lock:
                writer(value)
            return struct.pack(">BHH", fc, address, count)
        if fc in (0x03, 0x04, 0x10):
            return bytes((fc | 0x80, _ILLEGAL_VALUE))
        return bytes((fc | 0x80, _ILLEGAL_FUNCTION))


class _Handler(socketserver.BaseRequestHandler):
    server: TwinModbusServer

    def _recv(self, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def handle(self) -> None:
        while True:
            head = self._recv(_MBAP.size)
            if head is None:
                return
            tid, proto, length, unit = _MBAP.unpack(head)
            body = self._recv(length - 1) if length >= 1 else None
            if body is None or proto != 0:
                return
            reply = self.server.bank.answer(unit, body)
            self.request.sendall(_MBAP.pack(tid, 0, len(reply) + 1, unit) + reply)


class TwinModbusServer(socketserver.ThreadingTCPServer):
    """Serves a `RegisterBank` on 127.0.0.1 (an ephemeral port unless one is given)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, bank: RegisterBank, port: int = 0) -> None:
        self.bank = bank
        super().__init__(("127.0.0.1", port), _Handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> TwinModbusServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
