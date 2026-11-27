"""The commissioning set the reference gateway is handed (D-NEW-TWIN01-1).

A site's point list is data, never gateway code: for each point, a finished IC-14
`SeriesDescriptor` (copied verbatim into every batch's head, so the gateway never builds a
`SourceAddress` or chooses a ``record_class``) and a wire binding (where the point sits on the
Modbus server: unit id, register table, first register, four words of a big-endian signed
64-bit integer in the descriptor's canonical unit). A writable point names the WP-EDGE-02 point
class it belongs to (``bess_setpoint``, ``import_limit``); whether a write is allowed is a
separate allow-list the gateway is given.

The JSON form::

    {"kind": "southbound_commissioning", "site_id": "...", "sender_id": "...",
     "points": [{"descriptor": {...IC-14 series descriptor...},
                 "wire": {"unit_id": 1, "table": "input", "address": 0, "words": 4,
                          "encoding": "int64_be"},
                 "write_class": null}, ...]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from gridq.contracts.telemetry_series import SeriesDescriptor

__all__ = ["Commissioning", "CommissionedPoint", "WireBinding"]

Table = Literal["input", "holding"]


@dataclass(frozen=True)
class WireBinding:
    unit_id: int
    table: Table
    address: int
    words: int = 4
    encoding: Literal["int64_be"] = "int64_be"

    def __post_init__(self) -> None:
        if not 0 <= self.unit_id <= 255 or not 0 <= self.address <= 0xFFFF - 3:
            raise ValueError("unit id 0..255 and a register address that fits four words")
        if self.table not in ("input", "holding"):
            raise ValueError("table is input or holding")
        if self.words != 4 or self.encoding != "int64_be":
            raise ValueError("this gateway reads four-word big-endian signed 64-bit points only")


@dataclass(frozen=True)
class CommissionedPoint:
    descriptor: SeriesDescriptor
    wire: WireBinding
    write_class: str | None

    def __post_init__(self) -> None:
        if self.write_class is not None and self.wire.table != "holding":
            raise ValueError("a writable point is a holding register")


@dataclass(frozen=True)
class Commissioning:
    site_id: str
    #: The edge's IC-14 ``sender_id``: which acquisition device the site's batches come from.
    sender_id: str
    points: tuple[CommissionedPoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("a commissioning set names at least one point")
        ids = [p.descriptor.series_id for p in self.points]
        if len(ids) != len(set(ids)):
            raise ValueError("one commissioned point per series")
        for p in self.points:
            if p.descriptor.site_id != self.site_id:
                raise ValueError("every point belongs to the commissioned site")
        classes = [p.write_class for p in self.points if p.write_class is not None]
        if len(classes) != len(set(classes)):
            raise ValueError("one writable point per write class")

    @classmethod
    def from_json(cls, text: str) -> Commissioning:
        data: dict[str, Any] = json.loads(text)
        if data.get("kind") != "southbound_commissioning":
            raise ValueError("not a southbound commissioning document")
        points = tuple(
            CommissionedPoint(
                descriptor=SeriesDescriptor.model_validate_json(json.dumps(p["descriptor"])),
                wire=WireBinding(**p["wire"]),
                write_class=p.get("write_class"),
            )
            for p in data["points"]
        )
        return cls(site_id=str(data["site_id"]), sender_id=str(data["sender_id"]), points=points)

    def writable(self, write_class: str) -> CommissionedPoint | None:
        return next((p for p in self.points if p.write_class == write_class), None)
