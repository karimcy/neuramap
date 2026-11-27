"""A Modbus TCP client: the MBAP header and the three function codes the reference gateway uses.

Modbus Application Protocol over TCP (the Modbus Organization's published specification): a
7-byte MBAP header (transaction id, protocol id 0, length of what follows, unit id) and a PDU of
one function code and its data. Read holding registers (0x03), read input registers (0x04) and
write multiple registers (0x10); an exception reply sets the function code's top bit and carries
one exception code. Registers are 16-bit big-endian words.

Standard library only. Every request is a blocking request and reply on one connection, so a
caller that advances a simulated clock and then polls sees a deterministic sequence of bytes.
"""

from __future__ import annotations

import socket
import struct
from typing import Final

__all__ = [
    "READ_HOLDING",
    "READ_INPUT",
    "WRITE_MULTIPLE",
    "ModbusClient",
    "ModbusError",
    "decode_int64",
    "encode_int64",
]

READ_HOLDING: Final = 0x03
READ_INPUT: Final = 0x04
WRITE_MULTIPLE: Final = 0x10
_MBAP: Final = struct.Struct(">HHHB")


class ModbusError(Exception):
    """The server answered with a Modbus exception (``code``), or the reply was malformed
    (``code`` 0)."""

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"modbus exception {code:#04x}: {detail}")
        self.code = code


def encode_int64(value: int) -> tuple[int, int, int, int]:
    """A signed 64-bit integer as four big-endian 16-bit words."""
    words = struct.unpack(">4H", struct.pack(">q", value))
    return (words[0], words[1], words[2], words[3])


def decode_int64(words: tuple[int, ...]) -> int:
    """Four big-endian 16-bit words as a signed 64-bit integer."""
    if len(words) != 4:
        raise ValueError("an int64 is four registers")
    return int(struct.unpack(">q", struct.pack(">4H", *words))[0])


class ModbusClient:
    """One TCP connection to a Modbus server. ``wire_log``, when given, receives every request
    exactly as sent (for byte-level comparison in tests)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_s: float = 5.0,
        wire_log: list[bytes] | None = None,
    ) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout_s)
        self._tid = 0
        self.wire_log = wire_log

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> ModbusClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("the server closed the connection")
            buf += chunk
        return buf

    def _call(self, unit: int, pdu: bytes) -> bytes:
        self._tid = (self._tid + 1) % 0x10000
        frame = _MBAP.pack(self._tid, 0, len(pdu) + 1, unit) + pdu
        if self.wire_log is not None:
            self.wire_log.append(frame)
        self._sock.sendall(frame)
        tid, proto, length, r_unit = _MBAP.unpack(self._recv(_MBAP.size))
        body = self._recv(length - 1)
        if tid != self._tid or proto != 0 or r_unit != unit or not body:
            raise ModbusError(0, "reply does not match the request")
        if body[0] == pdu[0] | 0x80:
            raise ModbusError(body[1] if len(body) > 1 else 0, "exception reply")
        if body[0] != pdu[0]:
            raise ModbusError(0, "reply has another function code")
        return body

    def _read(self, function: int, unit: int, address: int, count: int) -> tuple[int, ...]:
        body = self._call(unit, struct.pack(">BHH", function, address, count))
        if len(body) < 2 or body[1] != 2 * count or len(body) != 2 + 2 * count:
            raise ModbusError(0, "read reply has the wrong byte count")
        return tuple(struct.unpack(f">{count}H", body[2:]))

    def read_holding(self, unit: int, address: int, count: int) -> tuple[int, ...]:
        return self._read(READ_HOLDING, unit, address, count)

    def read_input(self, unit: int, address: int, count: int) -> tuple[int, ...]:
        return self._read(READ_INPUT, unit, address, count)

    def write_multiple(self, unit: int, address: int, words: tuple[int, ...]) -> None:
        n = len(words)
        pdu = struct.pack(f">BHHB{n}H", WRITE_MULTIPLE, address, n, 2 * n, *words)
        body = self._call(unit, pdu)
        if body[1:] != struct.pack(">HH", address, n):
            raise ModbusError(0, "write reply does not echo the request")
