"""The reference southbound gateway (WP-TWIN-01, D196).

The device side of an edge: a Modbus TCP client (`modbus`), the commissioning set it is handed
(`commissioning`: finished IC-14 descriptors plus wire bindings), and the gateway that polls,
writes allow-listed setpoints and seals IC-14 batches (`gateway`). Standard library and
`gridq.contracts` only, so it can run on an edge box and so it cannot reach for a twin, a model or
a site's data: what it connects to is decided by its arguments, never by its code.
"""

from __future__ import annotations

from gridq.edge_southbound.commissioning import CommissionedPoint, Commissioning, WireBinding
from gridq.edge_southbound.gateway import EdgeClock, SouthboundGateway, WriteRefused
from gridq.edge_southbound.modbus import ModbusClient, ModbusError, decode_int64, encode_int64

__all__ = [
    "CommissionedPoint",
    "Commissioning",
    "EdgeClock",
    "ModbusClient",
    "ModbusError",
    "SouthboundGateway",
    "WireBinding",
    "WriteRefused",
    "decode_int64",
    "encode_int64",
]
