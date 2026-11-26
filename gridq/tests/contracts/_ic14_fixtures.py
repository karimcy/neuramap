"""Builders for the IC-14 1.0.0 synthetic fixtures (WP-TEL-01), in ``fixtures/ic14/``.

Run ``PYTHONPATH=src python tests/contracts/_ic14_fixtures.py`` to (re)write them; a conformance
test proves the committed files equal a fresh build.

**Every value here is synthetic.** The tenant is ``SYNTHETIC-ORG-HYBRID``, the site is IC-5's
``SYNTHETIC-SITE-HYBRID``, every record is ``record_class: synthetic_fixture``, the source protocol
is ``simulated``, and the meter certificate is an IC-4 reference to the SHA-256 of a synthetic
placeholder text, not of any real certificate. The watts are an arithmetic pattern, not a
measurement. Freshness thresholds, plausible bounds, ``max_batch_bytes`` and the curation method's
``min_usable_ms`` are chosen only to satisfy the contract's invariants; IC-14 has no default for
any of them (no published source sets them).

The story (2027-01-04, 16:00Z to 16:30Z, one half-hour of IC-5's hybrid-site afternoon): the
edge acquires the site boundary's revenue-meter active power at 1 s and seals one 60-slot batch a
minute, bound to the IC-5 link profile by hash.

- 16:00 to 16:18, stream epoch 0 (edge boot ``SYNTHETIC-BOOT-0``, booted 15:50Z as in IC-5), seq 0
  to 18. At 16:05 the meter repeats its last value for 10 s (``held``); at 16:07 the meter link
  drops for 20 s (IC-5 trigger ``site_meter_lost``, a ``comms_lost`` gap); 16:10 is read while
  the meter flags itself ``inaccurate``. The edge's monotonic clock runs 1 ms a minute slow
  against its wall clock (`clock_drift_ms`).
- 16:19: the edge reboots; that minute's buffer is lost and no batch exists.
- 16:20 to 16:29, stream epoch 1 (boot ``SYNTHETIC-BOOT-1``), seq 0 to 9, naming epoch 0's head.
  The 16:20 block is stamped before the clock synchronised (``unsynchronised``).
- A data-quality monitor finds a flatline over 16:15 (one annotation citing epoch 0, seq 15).
- The curated 1-minute record of the half-hour is computed twice: revision 0 when only epoch 0 had
  arrived, and revision 1 (``late_raw``) after epoch 1 arrived late, by store and forward.

Files: three descriptors (the revenue-meter point, its SCADA counterpart, and a battery
state-of-charge point at 1 min), the batch chain as JSONL (one canonical batch per line, epoch 0
then epoch 1), the annotation, and the two curated revisions.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES_IC14 = HERE / "fixtures" / "ic14"

ORG = "SYNTHETIC-ORG-HYBRID"
SITE = "SYNTHETIC-SITE-HYBRID"
SENDER = "SYNTHETIC-EDGE-HYBRID-01"
METER_POINT = "site_boundary.main.active_power.revenue_meter.whole"
SCADA_POINT = "site_boundary.main.active_power.scada.whole"
SOC_POINT = "bess.bess-1.state_of_charge.device_reported.whole"
#: The IC-5 hybrid link profile's content hash (`GOLDEN_HASHES_IC5`), which every batch names.
IC5_PROFILE_HASH = "sha256:441fdb77bb83ae7b394eef59bbe6b4d5279193a21340880aaa43ff44c599f09f"

WINDOW = datetime(2027, 1, 4, 16, 0, tzinfo=UTC)
EPOCH0_MINUTES = range(0, 19)  # 16:00 .. 16:18
EPOCH1_MINUTES = range(20, 30)  # 16:20 .. 16:29
EDGE_BOOT0 = datetime(2027, 1, 4, 15, 50, tzinfo=UTC)
EDGE_BOOT1 = datetime(2027, 1, 4, 16, 19, 30, tzinfo=UTC)

DESCRIPTOR_METER = FIXTURES_IC14 / "ic14_descriptor_site_power_meter.synthetic.json"
DESCRIPTOR_SCADA = FIXTURES_IC14 / "ic14_descriptor_site_power_scada.synthetic.json"
DESCRIPTOR_SOC = FIXTURES_IC14 / "ic14_descriptor_bess_soc.synthetic.json"
BATCHES = FIXTURES_IC14 / "ic14_batches_site_power_meter.synthetic.jsonl"
ANNOTATION = FIXTURES_IC14 / "ic14_annotation_flatline.synthetic.json"
CURATED_REV0 = FIXTURES_IC14 / "ic14_curated_site_power_1min_rev0.synthetic.json"
CURATED_REV1 = FIXTURES_IC14 / "ic14_curated_site_power_1min_rev1.synthetic.json"


def _ts() -> Any:
    from gridq.contracts import telemetry_series

    return telemetry_series


def placeholder_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC PLACEHOLDER: {label}".encode()).hexdigest()


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic fixture: {reason}"}


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": f"synthetic fixture: {reason}"}


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def tenant() -> dict[str, str]:
    return {"kind": "tenant_key", "org_id": ORG, "org_kind": "customer", "site_id": SITE}


def _head(point: str) -> dict[str, Any]:
    return {
        "contract": "IC-14",
        "contract_version": "1.0.0",
        "tenant": tenant(),
        "site_id": SITE,
        "series_id": f"{ORG}/{SITE}/{point}",
        "data_class": "customer_commercial",
        "record_class": "synthetic_fixture",
    }


def _name(point: str) -> dict[str, str]:
    kind, asset_id, quantity, role, aspect = point.split(".")
    return {
        "asset_kind": kind,
        "asset_id": asset_id,
        "quantity": quantity,
        "role": role,
        "aspect": aspect,
    }


def build_descriptor_meter() -> dict[str, Any]:
    return {
        **_head(METER_POINT),
        "kind": "series_descriptor",
        "name": _name(METER_POINT),
        "unit": "W",
        "resolution": "operational_1s",
        "source": {"kind": "source_address", "protocol": "simulated", "address": "sim:meter/p"},
        "source_scaling": {
            "kind": "source_scaling",
            "source_unit_ucum": "kW",
            "scale_num": 1000,
            "scale_den": 1,
            "offset": 0,
        },
        "brick_class": "Active_Power_Sensor",
        "plausible_min": -20_000_000,
        "plausible_max": 20_000_000,
        "meter": {
            "kind": "meter_binding",
            "meter_id": "SYNTHETIC-METER-HYBRID-BOUNDARY",
            "certificate": {
                "kind": "evidence_ref",
                "contract": "IC-4",
                "contract_version": "1.1.0",
                "evidence_hash": placeholder_hash("meter certificate, site boundary"),
            },
        },
        "freshness": {
            "kind": "freshness_policy",
            "stale_after_ms": 120_000,
            "lost_after_ms": 600_000,
        },
        "max_batch_bytes": 65_536,
    }


def build_descriptor_scada() -> dict[str, Any]:
    d = build_descriptor_meter()
    return {
        **d,
        **_head(SCADA_POINT),
        "name": _name(SCADA_POINT),
        "source": {"kind": "source_address", "protocol": "simulated", "address": "sim:scada/p"},
        "meter": _na("a SCADA reading names no meter"),
    }


def build_descriptor_soc() -> dict[str, Any]:
    return {
        **_head(SOC_POINT),
        "kind": "series_descriptor",
        "name": _name(SOC_POINT),
        "unit": "basis_point",
        "resolution": "standard_1min",
        "source": {"kind": "source_address", "protocol": "simulated", "address": "sim:bms/soc"},
        "source_scaling": {
            "kind": "source_scaling",
            "source_unit_ucum": "%",
            "scale_num": 100,
            "scale_den": 1,
            "offset": 0,
        },
        "brick_class": _na("no Brick class chosen for this synthetic point"),
        "plausible_min": 0,
        "plausible_max": 10_000,
        "meter": _na("device-reported, not metered"),
        "freshness": {
            "kind": "freshness_policy",
            "stale_after_ms": 3_600_000,
            "lost_after_ms": 7_200_000,
        },
        "max_batch_bytes": 16_384,
    }


def _value(minute: int, slot: int) -> int:
    """A synthetic pattern in W: never a measurement."""
    return -1_500_000 + (minute * 7_919 + slot * 104_729) % 40_001 - 20_000


def _segments(minute: int) -> list[dict[str, Any]]:
    values = [_value(minute, s) for s in range(60)]

    def run(first: int, last: int, quality: str) -> dict[str, Any]:
        return {
            "kind": "values",
            "first_slot": first,
            "values": values[first : last + 1],
            "quality": quality,
        }

    if minute == 5:  # the meter repeats its last value for 10 s
        held = dict(run(30, 39, "held"))
        held["values"] = [values[29]] * 10
        return [run(0, 29, "good"), held, run(40, 59, "good")]
    if minute == 7:  # site_meter_lost for 20 s
        gap = {"kind": "gap", "first_slot": 10, "last_slot": 29, "reason": "comms_lost"}
        return [run(0, 9, "good"), gap, run(30, 59, "good")]
    if minute == 10:
        return [run(0, 59, "inaccurate")]
    if minute == 15:  # a flatline the monitor will find; the source still reports it good
        return [{**run(0, 59, "good"), "values": [values[0]] * 60}]
    return [run(0, 59, "good")]


def build_batches() -> list[Any]:
    from gridq.contracts.audit import GENESIS_PREV_HASH

    ts = _ts()
    descriptor = ts.SeriesDescriptor.model_validate_json(json.dumps(build_descriptor_meter()))
    dh = descriptor.content_hash()
    out: list[Any] = []
    for epoch, minutes, boot, boot_id in (
        (0, EPOCH0_MINUTES, EDGE_BOOT0, "SYNTHETIC-BOOT-0"),
        (1, EPOCH1_MINUTES, EDGE_BOOT1, "SYNTHETIC-BOOT-1"),
    ):
        prev = GENESIS_PREV_HASH
        # Every batch of an epoch names the head of the epoch before it.
        prior = _na("stream epoch 0") if epoch == 0 else out[-1].content_hash()
        for seq, minute in enumerate(minutes):
            origin = WINDOW + timedelta(minutes=minute)
            # The edge's monotonic clock runs 1 ms a minute slow against its wall clock.
            mono = (origin - boot) // timedelta(milliseconds=1) - (minute if epoch == 0 else 0)
            synced = not (epoch == 1 and seq == 0)
            data = {
                **_head(METER_POINT),
                "kind": "telemetry_batch",
                "descriptor_hash": dh,
                "sender_id": SENDER,
                "stream_epoch": epoch,
                "seq": seq,
                "prev_batch_hash": prev,
                "prior_epoch_head": prior,
                "origin": _iso(origin),
                "resolution": "operational_1s",
                "slot_count": 60,
                "segments": _segments(minute),
                "clock": {
                    "kind": "clock_statement",
                    "stamped_by": "edge_acquisition",
                    "clock_state": "synchronised" if synced else "unsynchronised",
                    "clock_offset_bound_ms": (
                        20 if synced else _unknown("the clock had not synchronised since boot")
                    ),
                    "boot_id": boot_id,
                    "mono_origin_ms": mono,
                    "mono_end_ms": mono + 59_000,
                    "receipt_lag_ms": _na("stamped at acquisition by the edge"),
                },
                "sealed_at": _iso(origin + timedelta(seconds=60, milliseconds=200)),
                "ic5": {
                    "kind": "ic5_binding",
                    "link_profile_hash": IC5_PROFILE_HASH,
                    "link_epoch": epoch,
                },
            }
            batch = ts.TelemetryBatch.model_validate_json(json.dumps(data))
            out.append(batch)
            prev = batch.content_hash()
    return out


def build_annotation() -> dict[str, Any]:
    batches = build_batches()
    b = next(x for x in batches if x.stream_epoch == 0 and x.seq == 15)
    start = WINDOW + timedelta(minutes=15)
    return {
        **_head(METER_POINT),
        "kind": "dq_annotation",
        "annotation_id": "SYNTHETIC-DQ-0001",
        "monitor_id": "SYNTHETIC-MONITOR-FLATLINE",
        "monitor_version": "0.0.0-synthetic",
        "detector_hash": placeholder_hash("flatline detector"),
        "finding": "flatline",
        "disposition": "asserted",
        "start": _iso(start),
        "end": _iso(start + timedelta(minutes=1)),
        "evidence": [
            {
                "kind": "raw_span",
                "series_id": b.series_id,
                "sender_id": SENDER,
                "stream_epoch": 0,
                "first_seq": 15,
                "last_seq": 15,
                "first_prev_hash": b.prev_batch_hash,
                "last_batch_hash": b.content_hash(),
                "start": _iso(start),
                "end": _iso(start + timedelta(minutes=1)),
            }
        ],
        "supersedes": _na("first assertion"),
        "issued_at": _iso(WINDOW + timedelta(minutes=17)),
    }


def method() -> dict[str, Any]:
    return {
        "kind": "curation_method",
        "method_id": "SYNTHETIC-METHOD-MEAN-1MIN",
        "aggregation": "mean",
        "min_usable_ms": 45_000,
        "usable": ["good", "local_override", "inaccurate"],
        "applied_findings": ["flatline"],
        "rounding": "half_even",
    }


def build_curated() -> tuple[Any, Any]:
    ts = _ts()
    descriptor = ts.SeriesDescriptor.model_validate_json(json.dumps(build_descriptor_meter()))
    batches = build_batches()
    ann = ts.DqAnnotation.model_validate_json(json.dumps(build_annotation()))
    m = ts.CurationMethod.model_validate_json(json.dumps(method()))
    na = ts.not_applicable
    early = [b for b in batches if b.stream_epoch == 0]
    rev0 = ts.curate(descriptor, early, [ann], m, WINDOW, "standard_1min", na("first"))
    rev1 = ts.curate(descriptor, batches, [ann], m, WINDOW, "standard_1min", rev0)
    return rev0, rev1


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def render_model(model: Any) -> str:
    return render(model.model_dump(mode="json"))


def render_chain(records: list[Any]) -> str:
    from gridq.contracts._base import canonical_json

    return "".join(canonical_json(r) + "\n" for r in records)


def build_all() -> dict[Path, str]:
    rev0, rev1 = build_curated()
    return {
        DESCRIPTOR_METER: render(build_descriptor_meter()),
        DESCRIPTOR_SCADA: render(build_descriptor_scada()),
        DESCRIPTOR_SOC: render(build_descriptor_soc()),
        BATCHES: render_chain(build_batches()),
        ANNOTATION: render(build_annotation()),
        CURATED_REV0: render_model(rev0),
        CURATED_REV1: render_model(rev1),
    }


def main() -> None:
    FIXTURES_IC14.mkdir(parents=True, exist_ok=True)
    for path, text in build_all().items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(HERE)}")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parents[1] / "src"))
    main()
