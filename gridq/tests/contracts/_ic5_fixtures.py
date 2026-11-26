"""Builders for the IC-5 1.0.0 synthetic fixtures (WP-EDGE-01).

Run ``PYTHONPATH=src python tests/contracts/_ic5_fixtures.py`` to (re)write them; a conformance
test proves the committed files equal a fresh build.

**Every number here is a synthetic fixture value, not a recommendation.** IC-5 has no default for
any interval, threshold or latency bound (no published source sets them, D116 and D120).
Where a value echoes the spec it is Neura's own internal target, labelled as such: 120 000 ms
schedule age is spec §10A's "default 2 min"; the 5 000 ms p99 schedule bound is spec §10A's
"≤5 s p99". The rest are chosen only to satisfy the profile's invariants.

The story (site ``SYNTHETIC-SITE-HYBRID``, 2027-01-04, a Monday in January): the edge boots at
15:50Z into fail-safe (no cloud link, no schedule held); the cloud's first heartbeat arrives at
15:55:00Z; the edge holds the 30 s resume hold, clears ``cloud_link_lost``, accepts a closed-loop
schedule at 15:55:41Z and exits; the schedule goes active at 15:56Z and discharges 4 500 kW over the
16:00Z winter peak (merchant 1 500 + capacity market 1 000 + local flex 2 000), inside the IC-3
hybrid envelope. At 16:05Z the DNO's ANM sends a 1 500 kW import setpoint; the edge enforces it in
3 250 ms, locally, and reports it.

One frame per line, each line the frame's canonical JSON (so its SHA-256 is the frame hash).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _ic3_fixtures import IC1_HYBRID_V110_HASH  # noqa: E402

FIXTURES_IC5 = HERE / "fixtures" / "ic5"
SITE = "SYNTHETIC-SITE-HYBRID"
EDGE = "edge-synthetic-hybrid-01"
CLOUD = "cloud-synthetic-dispatch"
EPISODE = "ep-synthetic-0001"
GENESIS = "sha256:" + "0" * 64
HYBRID = HERE / "fixtures" / "ic3" / "ic3_envelope_hybrid_bess.synthetic.json"

CM = "svc-capacity-market"
FLEX = "svc-local-flex-winter-peak"


def _ic5() -> Any:
    from gridq.contracts import edge_cloud

    return edge_cloud


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic fixture: {reason}"}


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": f"synthetic fixture: {reason}"}


def build_profile() -> dict[str, Any]:
    return {
        "contract": "IC-5",
        "contract_version": "1.0.0",
        "kind": "link_profile",
        "profile_id": "ic5-synthetic-hybrid-link",
        "site_id": SITE,
        "record_class": "synthetic_fixture",
        "connection_agreement": {
            "kind": "connection_agreement_ref",
            "contract": "IC-1",
            "contract_version": "1.1.0",
            "agreement_hash": IC1_HYBRID_V110_HASH,
        },
        "anm_authority": "edge_local",
        "cloud_heartbeat_interval_ms": 10_000,
        "edge_heartbeat_interval_ms": 10_000,
        "control_lease_ms": 60_000,
        # Spec §10A's internal target ("default 2 min"), not a published requirement.
        "schedule_max_age_ms": 120_000,
        # Re-issue cadence of the synthetic cloud: below the schedule age, as the profile requires.
        "mpc_cadence_ms": 60_000,
        "schedule_required_lookahead_ms": 600_000,
        "edge_silent_after_ms": 60_000,
        "resume_hold_ms": 30_000,
        "max_clock_skew_ms": 250,
        "max_frame_bytes": 1_048_576,
        "latency_budgets": {
            # Spec §10A's internal target ("cloud→edge ≤5 s p99"), not a published requirement.
            "cloud_to_edge_schedule": {
                "kind": "latency_budget",
                "path": "cloud_to_edge_schedule",
                "quantile_per_mille": 990,
                "bound_ms": 5_000,
                "min_samples": 100,
            },
            "edge_to_cloud_ack": _na("no acknowledgement budget set for this site"),
            "heartbeat": _na("no heartbeat budget set for this site"),
        },
    }


def _hashes() -> tuple[str, str, str]:
    """The profile hash, the hybrid envelope's hash and its resolved (timeline) hash."""
    from gridq.contracts.dispatch_envelope import DispatchEnvelope

    ic5 = _ic5()
    profile = ic5.LinkProfile.model_validate_json(json.dumps(build_profile()))
    env = DispatchEnvelope.model_validate_json(HYBRID.read_text(encoding="utf-8"))
    return profile.content_hash(), env.content_hash(), env.resolved_hash


def envelope_ref() -> dict[str, Any]:
    from gridq.contracts.dispatch_envelope import DispatchEnvelope

    env = DispatchEnvelope.model_validate_json(HYBRID.read_text(encoding="utf-8"))
    return {
        "kind": "dispatch_envelope_ref",
        "contract": "IC-3",
        "contract_version": "1.0.0",
        "envelope_hash": env.content_hash(),
        "input_hash": env.source.input_hash,
    }


def _period(start: str, end: str, bess: dict[str, Any], soe_end: int, export: int) -> dict:
    return {
        "start": start,
        "end": end,
        "bess": bess,
        "site_import_cap_kw": 2_000,
        "chiller_band": _na("no chiller authority at this site"),
        "expected": {
            "soe_end_kwh": soe_end,
            "site_import_kw": 0,
            "site_export_kw": export,
            "temperature": _na("no cooling plant"),
        },
    }


def schedule_body() -> dict[str, Any]:
    _, _, resolved = _hashes()
    return {
        "kind": "setpoint_schedule",
        "schedule_id": "sched-synthetic-20270104-1555",
        "site_id": SITE,
        "issued_at": "2027-01-04T15:55:40Z",
        "deliver_by": "2027-01-04T15:55:45Z",
        "envelope": envelope_ref(),
        "resolved_hash": resolved,
        "execution": "closed_loop",
        "supersedes": _na("the first schedule since the edge restarted"),
        "planned_against_anm": _na("no ANM event seen by the cloud yet"),
        "soe_start_kwh": 9_000,
        "periods": [
            _period("2027-01-04T15:56:00Z", "2027-01-04T16:00:00Z", {"kind": "idle"}, 9_000, 0),
            _period(
                "2027-01-04T16:00:00Z",
                "2027-01-04T16:30:00Z",
                {
                    "kind": "dispatch",
                    "direction": "discharge",
                    "kw": 4_500,
                    "merchant_kw": 1_500,
                    "service_delivery": [
                        {"service_id": CM, "direction": "discharge", "kw": 1_000},
                        {"service_id": FLEX, "direction": "discharge", "kw": 2_000},
                    ],
                },
                6_750,
                4_500,
            ),
        ],
    }


def _schedule_hash() -> str:
    return _ic5().SetpointSchedule.model_validate_json(json.dumps(schedule_body())).content_hash()


class _Chain:
    """Builds one direction's frames, linking each to the one before."""

    def __init__(self, direction: str, sender: str) -> None:
        self.direction, self.sender = direction, sender
        self.frames: list[Any] = []
        self.profile_hash = _hashes()[0]

    def add(self, sent_at: str, body: dict[str, Any], peer: Any = None) -> Any:
        ic5 = _ic5()
        model = ic5.CloudToEdgeFrame if self.direction == "cloud_to_edge" else ic5.EdgeToCloudFrame
        prev = self.frames[-1].frame_hash() if self.frames else GENESIS
        data = {
            "contract": "IC-5",
            "contract_version": "1.0.0",
            "direction": self.direction,
            "site_id": SITE,
            "sender_id": self.sender,
            "sent_at": sent_at,
            "link_epoch": 0,
            "seq": len(self.frames),
            "prev_hash": prev,
            "profile_hash": self.profile_hash,
            "peer_head": _na("nothing received from the other side yet")
            if peer is None
            else {
                "kind": "peer_head",
                "link_epoch": peer.link_epoch,
                "seq": peer.seq,
                "frame_hash": peer.frame_hash(),
            },
            "body": body,
        }
        frame = model.model_validate_json(json.dumps(data))
        self.frames.append(frame)
        return frame


def _target(firm: int) -> dict[str, Any]:
    return {
        "import_limit_kw": firm,
        "resilience_floor_kwh": 1_000,
        "markets": "declared_unavailable",
        "chillers": _na("no chiller authority granted: the BMS keeps its own schedule"),
        "writes": "enforce",
    }


def _fs_event(seq: int, prev: str, at: str, mono: int, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "fail_safe_event",
        "site_id": SITE,
        "episode_id": EPISODE,
        "seq": seq,
        "prev_hash": prev,
        "at": at,
        "clock_state": "synchronised",
        "monotonic_ms": mono,
        "event": event,
    }


def _heartbeat(**fields: Any) -> dict[str, Any]:
    _, env_hash, _ = _hashes()
    return {
        "kind": "edge_heartbeat",
        "mode": "normal",
        "episode_id": _na("not in fail-safe"),
        "active_triggers": [],
        "held_schedule": _schedule_hash(),
        "governing_schedule": _schedule_hash(),
        "held_envelopes": [env_hash],
        "anm_state": {"kind": "unrestricted"},
        "last_anm_event": _na("no ANM signal since boot"),
        **fields,
    }


def _ack(seq: int, prev: str | None, at: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "schedule_ack",
        "site_id": SITE,
        "edge_id": EDGE,
        "schedule_id": "sched-synthetic-20270104-1555",
        "schedule_hash": _schedule_hash(),
        "seq": seq,
        "prev_ack_hash": _na("the first ack") if prev is None else prev,
        "received_at": "2027-01-04T15:55:41.200000Z",
        "at": at,
        "late": False,
        "clock_offset_bound_ms": 20,
        "envelope": envelope_ref(),
        "state": state,
    }


def build_chains() -> tuple[list[Any], list[Any]]:
    """The cloud-to-edge and edge-to-cloud frames of the story, in sending order."""
    from gridq.contracts._base import content_hash

    ic5 = _ic5()
    c2e, e2c = _Chain("cloud_to_edge", CLOUD), _Chain("edge_to_cloud", EDGE)
    events: list[Any] = []

    def fs(at: str, mono: int, event: dict[str, Any]) -> dict[str, Any]:
        prev = events[-1].content_hash() if events else GENESIS
        data = _fs_event(len(events), prev, at, mono, event)
        events.append(ic5.FailSafeEvent.model_validate_json(json.dumps(data)))
        return data

    boot = "2027-01-04T15:50:00Z"
    entered = fs(
        boot,
        2_000,
        {
            "kind": "entered",
            "triggers": [
                {
                    "code": "cloud_link_lost",
                    "source": "edge_local",
                    "detected_at": boot,
                    "detail": "synthetic fixture: no cloud frame since boot",
                },
                {
                    "code": "edge_restart",
                    "source": "edge_local",
                    "detected_at": boot,
                    "detail": "synthetic fixture: no schedule held since boot",
                },
            ],
            "primary": "cloud_link_lost",
            "envelope": envelope_ref(),
            "target": _target(10_000),
            "closes_orphan": _na("the previous episode exited before the restart"),
        },
    )
    e2c.add(boot, entered)
    hb0 = c2e.add("2027-01-04T15:55:00Z", {"kind": "cloud_heartbeat", "market_link": "up"})
    e2c.add(
        "2027-01-04T15:55:30Z",
        fs(
            "2027-01-04T15:55:30Z",
            332_000,
            {
                "kind": "trigger_cleared",
                "code": "cloud_link_lost",
                "release": _na("clears by itself"),
                "primary": "edge_restart",
            },
        ),
        hb0,
    )
    sched = c2e.add("2027-01-04T15:55:40Z", schedule_body(), e2c.frames[-1])
    accepted = _ack(
        0, None, "2027-01-04T15:55:41.300000Z", {"kind": "accepted", "mode": "closed_loop"}
    )
    e2c.add("2027-01-04T15:55:41.300000Z", accepted, sched)
    e2c.add(
        "2027-01-04T15:55:41.300000Z",
        fs(
            "2027-01-04T15:55:41.300000Z",
            343_300,
            {
                "kind": "trigger_cleared",
                "code": "edge_restart",
                "release": _na("clears by itself"),
                "primary": _na("no trigger left active"),
            },
        ),
        sched,
    )
    e2c.add(
        "2027-01-04T15:55:41.300000Z",
        fs(
            "2027-01-04T15:55:41.300000Z",
            343_300,
            {
                "kind": "exited",
                "entered_hash": events[0].content_hash(),
                "resume_schedule_hash": _schedule_hash(),
                "resume_accepted_at": "2027-01-04T15:55:41.300000Z",
                "releases": [],
            },
        ),
        sched,
    )
    acked = ic5.ScheduleAck.model_validate_json(json.dumps(accepted))
    e2c.add(
        "2027-01-04T15:56:00Z",
        _ack(1, acked.content_hash(), "2027-01-04T15:56:00Z", {"kind": "active"}),
        sched,
    )
    e2c.add(
        "2027-01-04T15:56:00Z",
        _heartbeat(
            lease_remaining_ms=60_000 - 20_000, import_cap={"kw": 2_000, "binding": "schedule"}
        ),
        sched,
    )
    anm = {
        "kind": "anm_event",
        "site_id": SITE,
        "event_id": "anm-synthetic-0001",
        "seq": 0,
        "prev_event_hash": GENESIS,
        "signal": {"kind": "anm_setpoint", "import_limit_kw": 1_500},
        "received_at": "2027-01-04T16:05:00Z",
        "enforced_at": "2027-01-04T16:05:03.250000Z",
        "response_elapsed_ms": 3_250,
        "dno_acknowledged_at": _unknown("the DNO's acknowledgement time is not reported back"),
        "agreement_hash": IC1_HYBRID_V110_HASH,
        "path": "edge_local",
        "cloud_link": "up",
    }
    hb1 = c2e.add(
        "2027-01-04T16:05:00Z", {"kind": "cloud_heartbeat", "market_link": "up"}, e2c.frames[-1]
    )
    e2c.add("2027-01-04T16:05:03.250000Z", anm, hb1)
    anm_hash = content_hash(ic5.AnmEvent.model_validate_json(json.dumps(anm)))
    e2c.add(
        "2027-01-04T16:05:10Z",
        _heartbeat(
            lease_remaining_ms=50_000,
            anm_state={"kind": "limited", "import_limit_kw": 1_500},
            last_anm_event=anm_hash,
            import_cap={"kw": 1_500, "binding": "anm_setpoint"},
        ),
        hb1,
    )
    return c2e.frames, e2c.frames


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def render_chain(frames: list[Any]) -> str:
    from gridq.contracts._base import canonical_json

    return "".join(canonical_json(f) + "\n" for f in frames)


PROFILE = FIXTURES_IC5 / "ic5_link_profile_hybrid.synthetic.json"
CLOUD_TO_EDGE = FIXTURES_IC5 / "ic5_cloud_to_edge_hybrid.synthetic.jsonl"
EDGE_TO_CLOUD = FIXTURES_IC5 / "ic5_edge_to_cloud_hybrid.synthetic.jsonl"


def build_all() -> dict[Path, str]:
    c2e, e2c = build_chains()
    return {
        PROFILE: render(build_profile()),
        CLOUD_TO_EDGE: render_chain(c2e),
        EDGE_TO_CLOUD: render_chain(e2c),
    }


def main() -> None:
    FIXTURES_IC5.mkdir(parents=True, exist_ok=True)
    for path, text in build_all().items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
