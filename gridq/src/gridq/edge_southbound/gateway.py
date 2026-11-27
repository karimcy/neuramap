"""The reference southbound gateway: poll, write, seal (WP-TWIN-01, D196).

What an edge does on its device side, in the least code that is still the real thing: it opens
one Modbus TCP connection to a site, polls every commissioned point once per call, writes the
setpoints its allow-list permits, and seals one IC-14 `TelemetryBatch` per point per block,
hash-chained, stamped by the edge (``edge_acquisition``) from an injected clock.

It knows nothing of where its bytes go. Everything site-specific is data it is handed: the host
and port, the commissioning set (finished IC-14 descriptors plus wire bindings), the allow-list
of writable point classes (the harness passes the WP-EDGE-02 ``write_bindings(stage, mode)``
classes), and the clock. The same code, unchanged, polls the site twin and any other Modbus
server; `tests/site_twin/test_twin_gateway.py` proves it structurally.

A reference, not a product. Not here (each a later edge work package): reconnect and back-off,
store and forward, persisted chain state across restarts (each instance starts stream epoch 0),
the northbound IC-5 transport of sealed batches (``ic5`` is ``not_applicable``), fail-safe
actuation, and every protocol other than Modbus TCP.

How readings become a batch:

- one acquisition fills the slot its wall-clock instant falls in; a second acquisition in the
  same slot is ignored (the first stands);
- a value outside the descriptor's plausible bounds is kept, flagged ``out_of_range``;
- a value outside the quantity's hard range cannot be a reading: ``sensor_failure`` gap;
- a Modbus exception 0x02 or 0x03 (no such register, bad quantity) is ``configuration_error``;
  any other exception or a malformed reply is ``device_failure``; a socket error is
  ``comms_lost``;
- a slot no poll reached is ``not_recorded``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from gridq.contracts import NotApplicable, Unknown
from gridq.contracts.audit import GENESIS_PREV_HASH
from gridq.contracts.dispatch_envelope import Direction
from gridq.contracts.edge_cloud import BessDispatch, ImportCap, SetpointPeriod
from gridq.contracts.telemetry_series import (
    BLOCK_SPAN_MS,
    PERIOD_MS,
    QUANTITY_TABLE,
    ClockStatement,
    GapReason,
    GapRun,
    TelemetryBatch,
    ValueQuality,
    ValueRun,
    kw_to_w,
)
from gridq.edge_southbound.commissioning import CommissionedPoint, Commissioning
from gridq.edge_southbound.modbus import ModbusClient, ModbusError, decode_int64, encode_int64

__all__ = ["EdgeClock", "SouthboundGateway", "WriteRefused"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


class EdgeClock(Protocol):
    """The edge's clocks, injected. ``offset_bound_ms`` is ``None`` while the wall clock has not
    synchronised since boot."""

    @property
    def boot_id(self) -> str: ...

    def wall(self) -> datetime: ...

    def mono_ms(self) -> int: ...

    def offset_bound_ms(self) -> int | None: ...


class WriteRefused(PermissionError):
    """A write the allow-list does not permit, or to a class nothing is commissioned for. Raised
    before a byte is sent."""


def _ms(t: datetime) -> int:
    return (t - _EPOCH) // _MS


def _na(reason: str) -> NotApplicable:
    return NotApplicable(kind="not_applicable", reason=reason)


Reading = tuple[int, ValueQuality] | GapReason


@dataclass
class _Track:
    point: CommissionedPoint
    descriptor_hash: str
    period_ms: int
    span_ms: int
    next_block: int | None = None
    seq: int = 0
    prev_hash: str = GENESIS_PREV_HASH
    slots: dict[int, dict[int, Reading]] = field(default_factory=dict)
    mono: dict[int, tuple[int, int]] = field(default_factory=dict)
    bound: dict[int, int | None] = field(default_factory=dict)


class SouthboundGateway:
    """One site, one Modbus TCP connection. Construct with the address, the commissioning set,
    the allow-list and the clock; nothing else."""

    def __init__(
        self,
        host: str,
        port: int,
        commissioning: Commissioning,
        allow_list: Iterable[str],
        clock: EdgeClock,
    ) -> None:
        self.commissioning = commissioning
        self.allow_list = frozenset(str(c) for c in allow_list)
        self.clock = clock
        self.wire_log: list[bytes] = []
        self.client = ModbusClient(host, port, wire_log=self.wire_log)
        self.refused: list[str] = []
        self._written: dict[str, int] = {}
        self._tracks = [
            _Track(
                point=p,
                descriptor_hash=p.descriptor.content_hash(),
                period_ms=PERIOD_MS[p.descriptor.resolution],
                span_ms=BLOCK_SPAN_MS[p.descriptor.resolution],
            )
            for p in commissioning.points
        ]

    def close(self) -> None:
        self.client.close()

    # ---------------------------------------------------------------- reading

    def _read(self, p: CommissionedPoint) -> Reading:
        w = p.wire
        try:
            if w.table == "input":
                words = self.client.read_input(w.unit_id, w.address, w.words)
            else:
                words = self.client.read_holding(w.unit_id, w.address, w.words)
        except ModbusError as e:
            if e.code in (0x02, 0x03):
                return GapReason.CONFIGURATION_ERROR
            return GapReason.DEVICE_FAILURE
        except OSError:
            return GapReason.COMMS_LOST
        value = decode_int64(words)
        d = p.descriptor
        spec = QUANTITY_TABLE[d.name.quantity]
        if not spec.minimum <= value <= spec.maximum:
            return GapReason.SENSOR_FAILURE
        if not d.plausible_min <= value <= d.plausible_max:
            return value, ValueQuality.OUT_OF_RANGE
        return value, ValueQuality.GOOD

    def poll(self) -> None:
        """Acquire every commissioned point once, in commissioning order, stamped now."""
        now = _ms(self.clock.wall())
        mono = self.clock.mono_ms()
        bound = self.clock.offset_bound_ms()
        for t in self._tracks:
            block = now - now % t.span_ms
            if t.next_block is None:
                t.next_block = block
            if block < t.next_block:
                continue  # that block is sealed; a late acquisition cannot reopen it
            slot = (now - block) // t.period_ms
            reading = self._read(t.point)
            slots = t.slots.setdefault(block, {})
            if slot in slots:
                continue
            slots[slot] = reading
            first = t.mono.get(block, (mono, mono))[0]
            t.mono[block] = (first, mono)
            t.bound.setdefault(block, bound)

    # ---------------------------------------------------------------- writing

    def write(self, write_class: str, value: int) -> None:
        """Write ``value`` (the descriptor's canonical unit) to the point commissioned for
        ``write_class``. Refused, with no bytes sent, unless the allow-list permits the class."""
        if write_class not in self.allow_list:
            raise WriteRefused(f"{write_class} is not on the allow-list")
        point = self.commissioning.writable(write_class)
        if point is None:
            raise WriteRefused(f"no point is commissioned for {write_class}")
        self.client.write_multiple(point.wire.unit_id, point.wire.address, encode_int64(value))
        self._written[write_class] = value

    def actuate(self, period: SetpointPeriod | None, import_cap: ImportCap) -> dict[str, int]:
        """Carry one IC-5 period to the site: the battery setpoint (signed W, load convention:
        positive charges) and the effective import cap (W). No period governing means the battery
        rests. A class the allow-list refuses is recorded in ``refused`` and not written; an
        unchanged value is not re-sent. Returns what was written."""
        wanted: dict[str, int] = {}
        bess = period.bess if period is not None else None
        if isinstance(bess, BessDispatch):
            w = kw_to_w(bess.kw)
            wanted["bess_setpoint"] = w if bess.direction is Direction.CHARGE else -w
        else:
            wanted["bess_setpoint"] = 0
        if isinstance(import_cap.kw, int):
            wanted["import_limit"] = kw_to_w(import_cap.kw)
        done: dict[str, int] = {}
        for write_class, value in wanted.items():
            if self.commissioning.writable(write_class) is None:
                continue
            if write_class not in self.allow_list:
                self.refused.append(write_class)
                continue
            if self._written.get(write_class) == value:
                continue
            self.write(write_class, value)
            done[write_class] = value
        return done

    # ---------------------------------------------------------------- sealing

    def _segments(self, t: _Track, block: int) -> tuple[ValueRun | GapRun, ...]:
        n = t.span_ms // t.period_ms
        slots = t.slots.get(block, {})
        runs: list[tuple[int, ValueQuality | GapReason, list[int], int]] = []
        for i in range(n):
            r = slots.get(i, GapReason.NOT_RECORDED)
            key, value = (r, None) if isinstance(r, GapReason) else (r[1], r[0])
            if runs and runs[-1][1] is key:
                first, _, values, _ = runs[-1]
                runs[-1] = (first, key, values, i)
            else:
                values = []
                runs.append((i, key, values, i))
            if value is not None:
                values.append(value)
        out: list[ValueRun | GapRun] = []
        for first, key, values, last in runs:
            if isinstance(key, GapReason):
                out.append(GapRun(kind="gap", first_slot=first, last_slot=last, reason=key))
            else:
                out.append(
                    ValueRun(kind="values", first_slot=first, values=tuple(values), quality=key)
                )
        return tuple(out)

    def _batch(self, t: _Track, block: int, sealed_at: datetime) -> TelemetryBatch:
        d = t.point.descriptor
        mono_now = self.clock.mono_ms()
        first, last = t.mono.get(block, (mono_now, mono_now))
        bound = t.bound.get(block, self.clock.offset_bound_ms())
        clock = ClockStatement(
            kind="clock_statement",
            stamped_by="edge_acquisition",
            clock_state="synchronised" if bound is not None else "unsynchronised",
            clock_offset_bound_ms=(
                bound
                if bound is not None
                else Unknown(kind="unknown", reason="the clock had not synchronised since boot")
            ),
            boot_id=self.clock.boot_id,
            mono_origin_ms=first,
            mono_end_ms=last,
            receipt_lag_ms=_na("stamped at acquisition by the edge"),
        )
        return TelemetryBatch(
            tenant=d.tenant,
            site_id=d.site_id,
            series_id=d.series_id,
            data_class=d.data_class,
            record_class=d.record_class,
            kind="telemetry_batch",
            descriptor_hash=t.descriptor_hash,
            sender_id=self.commissioning.sender_id,
            stream_epoch=0,
            seq=t.seq,
            prev_batch_hash=t.prev_hash,
            prior_epoch_head=_na("stream epoch 0"),
            origin=_EPOCH + block * _MS,
            resolution=d.resolution,
            slot_count=t.span_ms // t.period_ms,
            segments=self._segments(t, block),
            clock=clock,
            sealed_at=sealed_at,
            ic5=_na("the reference gateway has no northbound IC-5 link"),
        )

    def seal(self) -> list[TelemetryBatch]:
        """Seal every block that has ended, for every point, in commissioning then time order.
        A block nothing was acquired in is sealed as one ``not_recorded`` gap, so a chain never
        skips a block."""
        sealed_at = self.clock.wall()
        now = _ms(sealed_at)
        out: list[TelemetryBatch] = []
        for t in self._tracks:
            while t.next_block is not None and t.next_block + t.span_ms <= now:
                block = t.next_block
                batch = self._batch(t, block, sealed_at)
                out.append(batch)
                t.prev_hash = batch.content_hash()
                t.seq += 1
                t.next_block = block + t.span_ms
                t.slots.pop(block, None)
                t.mono.pop(block, None)
                t.bound.pop(block, None)
        return out
