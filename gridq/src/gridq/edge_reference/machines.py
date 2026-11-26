"""Deterministic reference machines for IC-5 (D121).

Every decision is one of the contract's pure functions (`admit_schedule`, `link_state`,
`effective_import_cap`, `fail_safe_target`, `primary_trigger`, `ChainTracker`); the machines only
hold state and turn verdicts into frames. Same inputs, same frames, same transcript hash: the
in-process `Simulation` and the HTTP endpoint wrap the same object.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import ValidationError

from gridq.contracts._base import NotApplicable, Unknown, canonical_json, content_hash, unknown
from gridq.contracts.dispatch_envelope import (
    DispatchEnvelope,
    ResolvedTimeline,
    ThermalTerms,
    active_at,
    envelope_ref,
    resolve,
    timeline_terms_at,
)
from gridq.contracts.edge_cloud import (
    GENESIS_PREV_HASH,
    NEURA_OPS_RELEASABLE,
    SUPPORTED_VERSIONS,
    TRIGGERS,
    AckAborted,
    AckAccepted,
    AckActive,
    AckCompleted,
    AckLapsed,
    AckRejected,
    AckShadowing,
    AckSuperseded,
    AnmEvent,
    AnmLimited,
    AnmRelease,
    AnmSetpoint,
    AnmTrip,
    AnmTripped,
    AnmUnrestricted,
    ChainStatus,
    ChainTracker,
    CloudHeartbeat,
    CloudToEdgeFrame,
    ControlStage,
    EdgeHeartbeat,
    EdgeToCloudFrame,
    EnterFailSafe,
    EnvelopeInstall,
    EpisodeEntered,
    EpisodeExited,
    FailSafeEvent,
    LinkObservation,
    LinkProfile,
    OperatorCommand,
    Release,
    ScheduleAck,
    SetpointSchedule,
    TriggerCleared,
    TriggerCode,
    TriggerInstance,
    TriggerRaised,
    admit_schedule,
    anm_state_after,
    anm_triggers,
    check_frame_size,
    deliver_by,
    edge_silent,
    effective_import_cap,
    fail_safe_target,
    link_state,
    primary_trigger,
)

Role = Literal["relay", "edge-twin", "cloud-twin"]
_MS = timedelta(milliseconds=1)
_ORDER = {c: i for i, c in enumerate(TriggerCode)}
_ANM_KINDS = frozenset({"anm_setpoint", "anm_trip", "anm_release", "anm_event"})


def _na(reason: str) -> NotApplicable:
    return NotApplicable(kind="not_applicable", reason=reason)


def _sorted(codes: set[TriggerCode]) -> tuple[TriggerCode, ...]:
    return tuple(sorted(codes, key=_ORDER.__getitem__))


class Refusal(Exception):
    """A frame refused at the endpoint: an HTTP status and a body ``{code, loc, detail}``."""

    def __init__(self, status: int, code: str, detail: str, loc: str = "") -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.loc = status, code, detail, loc

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "loc": self.loc, "detail": self.detail}


def _walk(node: Any) -> Iterator[Any]:
    yield node
    children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    for child in children:
        yield from _walk(child)


def parse_frame(raw: bytes, profile: LinkProfile) -> CloudToEdgeFrame | EdgeToCloudFrame:
    """Validate raw bytes as an IC-5 frame, or raise `Refusal` (the relay's whole job)."""
    if not check_frame_size(raw, profile):
        raise Refusal(413, "frame_too_large", f"over max_frame_bytes ({profile.max_frame_bytes})")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(400, "malformed_json", str(exc)) from exc
    if not isinstance(data, dict):
        raise Refusal(400, "malformed_json", "a frame is a JSON object")
    if data.get("contract_version") not in SUPPORTED_VERSIONS or data.get("contract") != "IC-5":
        raise Refusal(
            422,
            "unsupported_contract_version",
            f"IC-5 {SUPPORTED_VERSIONS} only, not {data.get('contract')} "
            f"{data.get('contract_version')}",
            "contract_version",
        )
    site = data.get("site_id")
    if not isinstance(site, str) or not site.startswith("SYNTHETIC-"):
        raise Refusal(422, "non_synthetic_site", "the simulator serves SYNTHETIC- sites only")
    if site != profile.site_id:
        raise Refusal(422, "wrong_site", f"this endpoint serves {profile.site_id}", "site_id")
    direction = data.get("direction")
    if direction == "cloud_to_edge" and any(
        isinstance(n, dict) and n.get("kind") in _ANM_KINDS for n in _walk(data.get("body"))
    ):
        raise Refusal(422, "anm_not_local", "an ANM signal never travels cloud to edge", "body")
    model = CloudToEdgeFrame if direction == "cloud_to_edge" else EdgeToCloudFrame
    try:
        frame = model.model_validate_json(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        raise Refusal(422, "invalid_frame", err["msg"], loc) from exc
    if frame.profile_hash != profile.content_hash():
        raise Refusal(422, "profile_mismatch", "sent under another link profile", "profile_hash")
    return frame


# --------------------------------------------------------------------------------------------
# The reference edge
# --------------------------------------------------------------------------------------------


@dataclass
class _AckChain:
    schedule: SetpointSchedule
    acks: list[ScheduleAck] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.acks[-1].state.kind


class EdgeReference:
    """The edge side of IC-5, on a simulated clock (milliseconds since boot)."""

    def __init__(
        self,
        profile: LinkProfile,
        *,
        boot_utc: datetime,
        stage: ControlStage,
        envelopes: tuple[DispatchEnvelope, ...] = (),
        edge_id: str = "edge-reference",
    ) -> None:
        self.profile, self.boot_utc, self.stage, self.edge_id = profile, boot_utc, stage, edge_id
        self.mono = 0
        self.tracker = ChainTracker(profile.site_id, "cloud_to_edge", profile.content_hash())
        self.envelopes: dict[str, tuple[DispatchEnvelope, ResolvedTimeline]] = {}
        for env in envelopes:
            self.envelopes[env.content_hash()] = (env, resolve(env))
        self.out: list[EdgeToCloudFrame] = []
        self.last_rx: int | None = None
        self.lease_since: int | None = None
        self.market_link: Literal["up", "down"] = "up"
        self.held: SetpointSchedule | None = None
        self.held_rx: int | None = None
        self.held_accepted_at: datetime | None = None
        self.known: dict[str, str] = {}
        self.chains: dict[str, _AckChain] = {}
        self.manual: dict[TriggerCode, TriggerInstance] = {}
        self.episode: list[FailSafeEvent] | None = None
        self.episodes = 0
        self.active: set[TriggerCode] = set()
        self.last_clear: datetime | None = None
        self.releases: list[Release] = []
        self.anm_state: AnmUnrestricted | AnmLimited | AnmTripped | Unknown = AnmUnrestricted(
            kind="unrestricted"
        )
        self.anm_events: list[AnmEvent] = []
        self.last_hb: int | None = None
        self.step()

    # ---------------------------------------------------------------- clock and sending

    @property
    def now(self) -> datetime:
        return self.boot_utc + self.mono * _MS

    def _send(self, body: Any) -> EdgeToCloudFrame:
        prev = self.out[-1].frame_hash() if self.out else GENESIS_PREV_HASH
        frame = EdgeToCloudFrame(
            direction="edge_to_cloud",
            site_id=self.profile.site_id,
            sender_id=self.edge_id,
            sent_at=self.now,
            link_epoch=0,
            seq=len(self.out),
            prev_hash=prev,
            profile_hash=self.profile.content_hash(),
            peer_head=self.tracker.head_ref(),
            body=body,
        )
        self.out.append(frame)
        return frame

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("the simulated clock only moves forward")
        self.mono += ms
        self.step()

    def _lease_alive(self) -> bool:
        return self.last_rx is not None and self.mono - self.last_rx < self.profile.control_lease_ms

    # ---------------------------------------------------------------- receiving

    def receive(self, frame: CloudToEdgeFrame) -> ChainStatus:
        verdict = self.tracker.receive(frame)
        if verdict.status is ChainStatus.FORK:
            self._raise_manual(TriggerCode.CHAIN_INTEGRITY_FAILURE, "edge_local", verdict.detail)
        if verdict.status is ChainStatus.DUPLICATE and isinstance(frame.body, SetpointSchedule):
            chain = self.chains.get(frame.body.content_hash())
            if chain is not None:
                self._send(chain.acks[-1])
        if not verdict.deliver:
            self.step()
            return verdict.status
        if not self._lease_alive():
            self.lease_since = self.mono
        self.last_rx = self.mono
        body = frame.body
        if isinstance(body, CloudHeartbeat):
            self.market_link = body.market_link
        elif isinstance(body, EnvelopeInstall):
            self.envelopes[body.envelope.content_hash()] = (body.envelope, body.timeline)
        elif isinstance(body, OperatorCommand):
            self._operator(body)
        elif isinstance(body, SetpointSchedule):
            self._schedule(frame, body)
        self.step()
        return verdict.status

    def _timeline_for(self, schedule: SetpointSchedule) -> ResolvedTimeline | NotApplicable:
        for _, timeline in self.envelopes.values():
            if timeline.content_hash() == schedule.resolved_hash:
                return timeline
        return _na("the edge holds no timeline with this resolved_hash")

    def _schedule(self, frame: CloudToEdgeFrame, s: SetpointSchedule) -> None:
        h = s.content_hash()
        if h in self.chains:
            self._send(self.chains[h].acks[-1])
            return
        verdict = admit_schedule(
            frame,
            profile=self.profile,
            timeline=self._timeline_for(s),
            stage=self.stage,
            received_at=self.now,
            held=self.held if self.held is not None else _na("no schedule accepted since boot"),
            known_ids=self.known,
        )
        self.known.setdefault(s.schedule_id, h)
        chain = self.chains[h] = _AckChain(s)
        if verdict.accepted:
            state: Any = AckAccepted(
                kind="accepted", mode="closed_loop" if s.execution == "closed_loop" else "shadow"
            )
            for other in self.chains.values():
                if other is not chain and other.state == "accepted":
                    self._ack(other, AckSuperseded(kind="superseded", by_schedule_hash=h))
            self.held, self.held_rx, self.held_accepted_at = s, self.mono, self.now
        else:
            state = AckRejected(
                kind="rejected",
                reasons=verdict.reasons,
                violations=verdict.violations,
                detail="refused by the reference edge: "
                + ", ".join(r.value for r in verdict.reasons),
            )
        envelope = s.envelope if verdict.accepted else _na("rejected")
        self._ack(chain, state, late=verdict.late, envelope=envelope)

    def _ack(
        self, chain: _AckChain, state: Any, *, late: bool = False, envelope: Any = None
    ) -> None:
        prev = chain.acks[-1] if chain.acks else None
        ack = ScheduleAck(
            kind="schedule_ack",
            site_id=chain.schedule.site_id,
            edge_id=self.edge_id,
            schedule_id=chain.schedule.schedule_id,
            schedule_hash=chain.schedule.content_hash(),
            seq=len(chain.acks),
            prev_ack_hash=prev.content_hash() if prev else _na("the first ack"),
            received_at=prev.received_at if prev else self.now,
            at=self.now,
            late=prev.late if prev else late,
            clock_offset_bound_ms=0,
            envelope=prev.envelope if prev else envelope,
            state=state,
        )
        chain.acks.append(ack)
        self._send(ack)

    def _operator(self, cmd: OperatorCommand) -> None:
        action = cmd.action
        if isinstance(action, EnterFailSafe):
            self._raise_manual(TriggerCode.OPERATOR_COMMAND, "neura_ops", action.reason)
        elif action.code in self.manual and action.code in NEURA_OPS_RELEASABLE:
            del self.manual[action.code]
            release = Release(
                code=action.code, command_id=cmd.command_id, audit_event_hash=cmd.audit_event_hash
            )
            self._clear(action.code, release)

    def _raise_manual(self, code: TriggerCode, source: Any, detail: str) -> None:
        if code not in self.manual:
            self.manual[code] = TriggerInstance(
                code=code, source=source, detected_at=self.now, detail=detail
            )

    def inject_anm(self, signal: AnmSetpoint | AnmTrip | AnmRelease, response_ms: int) -> AnmEvent:
        """A DNO signal at the edge: enforced locally at once, logged, sent when the link allows."""
        self.anm_state = anm_state_after(signal)
        prev = self.anm_events[-1].content_hash() if self.anm_events else GENESIS_PREV_HASH
        event = AnmEvent(
            kind="anm_event",
            site_id=self.profile.site_id,
            event_id=f"anm-{len(self.anm_events):04d}",
            seq=len(self.anm_events),
            prev_event_hash=prev,
            signal=signal,
            received_at=self.now,
            enforced_at=self.now + response_ms * _MS,
            response_elapsed_ms=response_ms,
            dno_acknowledged_at=unknown("the simulator has no DNO to acknowledge to"),
            agreement_hash=self.profile.connection_agreement.agreement_hash,
            path="edge_local",
            cloud_link="up" if self._lease_alive() else "down",
        )
        self.anm_events.append(event)
        self._send(event)
        self.step()
        return event

    # ---------------------------------------------------------------- the step

    def _in_force(self) -> tuple[DispatchEnvelope, ResolvedTimeline] | None:
        envs = [e for e, _ in self.envelopes.values()]
        if not envs:
            return None
        live = active_at(envs, self.now)
        if isinstance(live, NotApplicable):
            return None
        return self.envelopes[live.content_hash()]

    def _desired(self) -> dict[TriggerCode, TriggerInstance]:
        obs = LinkObservation(
            now_mono_ms=self.mono,
            now_utc=self.now,
            last_valid_rx_mono_ms=self.last_rx if self.last_rx is not None else _na("none"),
            lease_live_since_mono_ms=(
                self.lease_since if self.lease_since is not None else _na("none")
            ),
            schedule_rx_mono_ms=self.held_rx if self.held_rx is not None else _na("none"),
            schedule_covered_until=self.held.covered_until if self.held else _na("none"),
            wall_clock_trusted=True,
        )
        codes = set(link_state(obs, self.profile).triggers) | set(anm_triggers(self.anm_state))
        if not self.envelopes:
            codes.add(TriggerCode.NO_ENVELOPE_HELD)
        elif self._in_force() is None:
            codes.add(TriggerCode.NO_ENVELOPE_IN_FORCE)
        if self.market_link == "down":
            codes.add(TriggerCode.MARKET_LINK_LOST)
        found = {
            c: TriggerInstance(
                code=c, source="edge_local", detected_at=self.now, detail=f"reference edge: {c}"
            )
            for c in codes
        }
        return {**found, **self.manual}

    def _fs(self, body: Any) -> None:
        assert self.episode is not None
        prev = self.episode[-1].content_hash() if self.episode else GENESIS_PREV_HASH
        event = FailSafeEvent(
            kind="fail_safe_event",
            site_id=self.profile.site_id,
            episode_id=f"ep-{self.episodes:04d}",
            seq=len(self.episode),
            prev_hash=prev,
            at=self.now,
            clock_state="synchronised",
            monotonic_ms=self.mono,
            event=body,
        )
        self.episode.append(event)
        self._send(event)

    def _primary(self) -> TriggerCode | NotApplicable:
        return primary_trigger(_sorted(self.active)) if self.active else _na("none active")

    def _clear(self, code: TriggerCode, release: Release | NotApplicable) -> None:
        self.active.discard(code)
        if isinstance(release, Release):
            self.releases.append(release)
        self.last_clear = self.now
        self._fs(
            TriggerCleared(
                kind="trigger_cleared", code=code, release=release, primary=self._primary()
            )
        )

    def _last_envelope(self) -> DispatchEnvelope | NotApplicable:
        live = self._in_force()
        if live is not None:
            return live[0]
        if self.envelopes:
            return list(self.envelopes.values())[-1][0]
        return _na("no envelope installed")

    def step(self) -> None:
        desired = self._desired()
        if self.episode is None and desired:
            self.episodes += 1
            self.episode, self.active, self.releases = [], set(desired), []
            self.last_clear = None
            env = self._last_envelope()
            self._fs(
                EpisodeEntered(
                    kind="entered",
                    triggers=tuple(desired[c] for c in _sorted(set(desired))),
                    primary=primary_trigger(_sorted(set(desired))),
                    envelope=envelope_ref(env) if isinstance(env, DispatchEnvelope) else env,
                    target=fail_safe_target(env, self.anm_state, self.stage),
                    closes_orphan=_na("no episode left open"),
                )
            )
            first = primary_trigger(_sorted(self.active))
            for chain in self.chains.values():
                if chain.state in ("active", "shadowing"):
                    self._ack(chain, AckAborted(kind="aborted", trigger_code=first))
        elif self.episode is not None:
            for code in _sorted(set(desired) - self.active):
                self.active.add(code)
                self._fs(
                    TriggerRaised(
                        kind="trigger_raised",
                        trigger=desired[code],
                        primary=primary_trigger(_sorted(self.active)),
                    )
                )
            for code in _sorted(self.active - set(desired)):
                if TRIGGERS[code].release == "automatic":
                    self._clear(code, _na("clears by itself"))
            accepted = self.held_accepted_at
            if (
                not self.active
                and self.held is not None
                and accepted is not None
                and (self.last_clear is None or accepted >= self.last_clear)
            ):
                self._fs(
                    EpisodeExited(
                        kind="exited",
                        entered_hash=self.episode[0].content_hash(),
                        resume_schedule_hash=self.held.content_hash(),
                        resume_accepted_at=accepted,
                        releases=tuple(self.releases),
                    )
                )
                self.episode = None
        self._lifecycle()
        if (
            self.last_hb is None
            or self.mono - self.last_hb >= self.profile.edge_heartbeat_interval_ms
        ):
            self.last_hb = self.mono
            self._send(self.heartbeat())

    def _lifecycle(self) -> None:
        normal = self.episode is None
        for chain in list(self.chains.values()):
            s, st = chain.schedule, chain.state
            if st == "accepted" and s.covered_until <= self.now:
                self._ack(chain, AckLapsed(kind="lapsed"))
            elif st == "accepted" and normal and s.periods[0].start <= self.now:
                if s.execution == "closed_loop":
                    for other in self.chains.values():
                        if other is not chain and other.state == "active":
                            by = s.content_hash()
                            self._ack(other, AckSuperseded(kind="superseded", by_schedule_hash=by))
                    self._ack(chain, AckActive(kind="active"))
                else:
                    self._ack(chain, AckShadowing(kind="shadowing"))
            elif st in ("active", "shadowing") and s.covered_until <= self.now:
                self._ack(chain, AckCompleted(kind="completed"))

    # ---------------------------------------------------------------- what it reports

    def governing(self) -> SetpointSchedule | None:
        return next((c.schedule for c in self.chains.values() if c.state == "active"), None)

    def heartbeat(self) -> EdgeHeartbeat:
        gov = self.governing()
        sched_kw: int | NotApplicable = _na("no schedule governing")
        if gov is not None:
            period = next((p for p in gov.periods if p.start <= self.now < p.end), None)
            if period is not None:
                sched_kw = period.site_import_cap_kw
        live = self._in_force()
        env_kw: int | NotApplicable = (
            timeline_terms_at(live[1], self.now).import_limit_kw
            if live is not None
            else _na("no envelope in force")
        )
        last = self._last_envelope()
        firm: int | NotApplicable = (
            last.fail_safe.firm_import_limit_kw if isinstance(last, DispatchEnvelope) else last
        )
        lease = (
            max(0, self.profile.control_lease_ms - (self.mono - self.last_rx))
            if self.last_rx is not None
            else 0
        )
        return EdgeHeartbeat(
            kind="edge_heartbeat",
            mode="normal" if self.episode is None else "fail_safe",
            episode_id=f"ep-{self.episodes:04d}" if self.episode is not None else _na("normal"),
            active_triggers=_sorted(self.active) if self.episode is not None else (),
            lease_remaining_ms=lease,
            held_schedule=self.held.content_hash() if self.held else _na("none since boot"),
            governing_schedule=gov.content_hash() if gov else _na("none governing"),
            held_envelopes=tuple(sorted(self.envelopes)),
            anm_state=self.anm_state,
            last_anm_event=(
                self.anm_events[-1].content_hash() if self.anm_events else _na("none since boot")
            ),
            import_cap=effective_import_cap(
                schedule_kw=sched_kw,
                envelope_limit_kw=env_kw,
                fail_safe_firm_kw=firm,
                anm_state=self.anm_state,
                in_fail_safe=self.episode is not None,
            ),
        )

    def state(self) -> dict[str, Any]:
        return {
            "role": "edge-twin",
            "now_utc": self.now.isoformat().replace("+00:00", "Z"),
            "mono_ms": self.mono,
            "heartbeat": json.loads(canonical_json(self.heartbeat())),
            "c2e_head": json.loads(canonical_json(self.tracker.head_ref())),
        }


# --------------------------------------------------------------------------------------------
# The reference cloud
# --------------------------------------------------------------------------------------------


def _ceil_minute(t: datetime) -> datetime:
    floor = t.replace(second=0, microsecond=0)
    return floor if floor == t else floor + timedelta(minutes=1)


class CloudReference:
    """The cloud side of IC-5: heartbeats on its interval, and an idle schedule (battery at rest,
    import capped at the envelope's limit) re-issued on the MPC cadence. It plans nothing; it
    exists so an edge can be built and tested against a cloud that speaks the protocol."""

    def __init__(
        self,
        profile: LinkProfile,
        *,
        boot_utc: datetime,
        envelope: DispatchEnvelope,
        soe_kwh: int,
        cloud_id: str = "cloud-reference",
    ) -> None:
        self.profile, self.boot_utc, self.cloud_id = profile, boot_utc, cloud_id
        self.envelope, self.timeline, self.soe = envelope, resolve(envelope), soe_kwh
        self.mono = 0
        self.tracker = ChainTracker(profile.site_id, "edge_to_cloud", profile.content_hash())
        self.out: list[CloudToEdgeFrame] = []
        self.last_rx: int | None = None
        self.last_hb: int | None = None
        self.last_schedule: int | None = None
        self.accepted: str | NotApplicable = _na("no schedule accepted yet")
        self.acks: list[ScheduleAck] = []
        self.step()

    @property
    def now(self) -> datetime:
        return self.boot_utc + self.mono * _MS

    def _send(self, body: Any) -> CloudToEdgeFrame:
        prev = self.out[-1].frame_hash() if self.out else GENESIS_PREV_HASH
        frame = CloudToEdgeFrame(
            direction="cloud_to_edge",
            site_id=self.profile.site_id,
            sender_id=self.cloud_id,
            sent_at=self.now,
            link_epoch=0,
            seq=len(self.out),
            prev_hash=prev,
            profile_hash=self.profile.content_hash(),
            peer_head=self.tracker.head_ref(),
            body=body,
        )
        self.out.append(frame)
        return frame

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("the simulated clock only moves forward")
        self.mono += ms
        self.step()

    def receive(self, frame: EdgeToCloudFrame) -> ChainStatus:
        verdict = self.tracker.receive(frame)
        if verdict.deliver:
            self.last_rx = self.mono
            body = frame.body
            if isinstance(body, ScheduleAck):
                self.acks.append(body)
                if isinstance(body.state, AckAccepted):
                    self.accepted = body.schedule_hash
        return verdict.status

    def schedule(self) -> SetpointSchedule:
        """An idle schedule from the next whole minute, over the look-ahead plus two cadences."""
        p = self.profile
        start = _ceil_minute(self.now)
        until = self.now + (p.schedule_required_lookahead_ms + 2 * p.mpc_cadence_ms) * _MS
        periods: list[dict[str, Any]] = []
        t = start
        while t < until:
            nxt = t.replace(minute=t.minute - t.minute % 30, second=0) + timedelta(minutes=30)
            terms = timeline_terms_at(self.timeline, t)
            temp: dict[str, Any] = {"kind": "not_applicable", "reason": "no thermal band"}
            if isinstance(terms.thermal, ThermalTerms):
                band = terms.thermal.band
                temp = {
                    "point": band.point.value,
                    "low_tenths_c": band.min_tenths_c,
                    "high_tenths_c": band.max_tenths_c,
                }
            periods.append(
                {
                    "start": t.isoformat(),
                    "end": nxt.isoformat(),
                    "bess": {"kind": "idle"},
                    "site_import_cap_kw": terms.import_limit_kw,
                    "chiller_band": {"kind": "not_applicable", "reason": "no chiller command"},
                    "expected": {
                        "soe_end_kwh": self.soe,
                        "site_import_kw": 0,
                        "site_export_kw": 0,
                        "temperature": temp,
                    },
                }
            )
            t = nxt
        supersedes: Any = self.accepted
        if isinstance(supersedes, NotApplicable):
            supersedes = supersedes.model_dump(mode="json")
        data = {
            "kind": "setpoint_schedule",
            "schedule_id": f"sched-ref-{len(self.out):06d}",
            "site_id": p.site_id,
            "issued_at": self.now.isoformat(),
            "deliver_by": deliver_by(self.now, p).isoformat(),
            "envelope": envelope_ref(self.envelope).model_dump(mode="json"),
            "resolved_hash": self.envelope.resolved_hash,
            "execution": "closed_loop",
            "supersedes": supersedes,
            "planned_against_anm": {
                "kind": "not_applicable",
                "reason": "the reference plans no ANM",
            },
            "soe_start_kwh": self.soe,
            "periods": periods,
        }
        return SetpointSchedule.model_validate_json(json.dumps(data))

    def step(self) -> None:
        p = self.profile
        if self.last_hb is None or self.mono - self.last_hb >= p.cloud_heartbeat_interval_ms:
            self.last_hb = self.mono
            self._send(CloudHeartbeat(kind="cloud_heartbeat", market_link="up"))
        if self.last_schedule is None or self.mono - self.last_schedule >= p.mpc_cadence_ms:
            self.last_schedule = self.mono
            self._send(self.schedule())

    def state(self) -> dict[str, Any]:
        last: int | NotApplicable = self.last_rx if self.last_rx is not None else _na("none")
        return {
            "role": "cloud-twin",
            "now_utc": self.now.isoformat().replace("+00:00", "Z"),
            "mono_ms": self.mono,
            "edge_silent": edge_silent(self.mono, last, self.profile),
            "accepted_schedule": json.loads(canonical_json(self.accepted))
            if isinstance(self.accepted, NotApplicable)
            else self.accepted,
            "acks": len(self.acks),
            "e2c_head": json.loads(canonical_json(self.tracker.head_ref())),
        }


# --------------------------------------------------------------------------------------------
# The simulation behind the endpoint
# --------------------------------------------------------------------------------------------


class Simulation:
    """One role's state, mailboxes and transcript. The HTTP server calls exactly these methods
    under one lock; tests may call them in-process. Every call appends one canonical line to the
    transcript, so two runs of the same calls have the same `transcript_hash`."""

    def __init__(
        self,
        role: Role,
        profile: LinkProfile,
        *,
        boot_utc: datetime,
        envelope: DispatchEnvelope | None = None,
        stage: ControlStage = ControlStage.L2_BESS_CLOSED_LOOP,
        soe_kwh: int = 0,
    ) -> None:
        if not profile.site_id.startswith("SYNTHETIC-"):
            raise ValueError("the simulator serves SYNTHETIC- sites only")
        self.role, self.profile = role, profile
        self._args = (boot_utc, envelope, stage, soe_kwh)
        self.reset()

    def reset(self) -> None:
        boot_utc, envelope, stage, soe_kwh = self._args
        self.mono = 0
        self.boot_utc = boot_utc
        self.mail: dict[str, list[str]] = {"edge": [], "cloud": []}
        self.ids: dict[tuple[str, int, int], str] = {}
        self.drop_to_edge = False
        self.hold_to_cloud = False
        self.held: list[str] = []
        self.transcript: list[str] = []
        self.edge: EdgeReference | None = None
        self.cloud: CloudReference | None = None
        self._emitted = 0
        if self.role == "edge-twin":
            envs = (envelope,) if envelope is not None else ()
            self.edge = EdgeReference(self.profile, boot_utc=boot_utc, stage=stage, envelopes=envs)
        elif self.role == "cloud-twin":
            if envelope is None:
                raise ValueError("the cloud twin needs the envelope it schedules inside")
            self.cloud = CloudReference(
                self.profile, boot_utc=boot_utc, envelope=envelope, soe_kwh=soe_kwh
            )
        self._collect()

    # ---------------------------------------------------------------- bookkeeping

    def _log(self, entry: dict[str, Any]) -> None:
        self.transcript.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))

    def transcript_hash(self) -> str:
        text = "\n".join(self.transcript)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _deliver(self, to: str, raw: str, frame_hash: str) -> None:
        if to == "cloud" and self.hold_to_cloud:
            self.held.append(raw)
            self._log({"op": "held", "to": to, "frame_hash": frame_hash})
            return
        self.mail[to].append(raw)
        self._log({"op": "emit", "to": to, "frame_hash": frame_hash})

    def _collect(self) -> None:
        """Move frames the twin's machine produced into the mailbox addressed to its peer."""
        machine: Any = self.edge or self.cloud
        if machine is None:
            return
        to = "cloud" if self.edge is not None else "edge"
        for frame in machine.out[self._emitted :]:
            self._deliver(to, canonical_json(frame), frame.frame_hash())
        self._emitted = len(machine.out)

    @property
    def now(self) -> datetime:
        return self.boot_utc + self.mono * _MS

    # ---------------------------------------------------------------- the operations

    def post_frame(self, raw: bytes, content_type: str = "application/json") -> tuple[int, dict]:
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        try:
            status, body = self._post(raw, content_type)
        except Refusal as r:
            status, body = r.status, r.body()
        self._log({"op": "post", "raw": digest, "status": status, "body": body})
        self._collect()
        return status, body

    def _post(self, raw: bytes, content_type: str) -> tuple[int, dict]:
        if content_type.split(";")[0].strip().lower() != "application/json":
            raise Refusal(415, "unsupported_media_type", "frames are application/json")
        frame = parse_frame(raw, self.profile)
        key = (frame.direction, frame.link_epoch, frame.seq)
        h = frame.frame_hash()
        seen = self.ids.get(key)
        if seen is not None and seen != h:
            if self.edge is not None and isinstance(frame, CloudToEdgeFrame):
                self.edge.receive(frame)
            raise Refusal(409, "conflict", f"another frame already holds {key}", "seq")
        if seen == h:
            replay: dict[str, Any] = {"frame_hash": h, "cursor": None, "replay": True}
            if self.edge is not None and isinstance(frame, CloudToEdgeFrame):
                replay["verdict"] = self.edge.receive(frame).value
            return 200, replay
        to = "edge" if frame.direction == "cloud_to_edge" else "cloud"
        if self.role == "edge-twin" and to != "edge" or self.role == "cloud-twin" and to != "cloud":
            raise Refusal(422, "wrong_direction", f"the {self.role} only receives frames to it")
        self.ids[key] = h
        if to == "edge" and self.drop_to_edge:
            self._log({"op": "dropped", "frame_hash": h})
            return 202, {"frame_hash": h, "cursor": None, "verdict": "dropped"}
        if self.edge is not None and isinstance(frame, CloudToEdgeFrame):
            return 202, {"frame_hash": h, "cursor": None, "verdict": self.edge.receive(frame).value}
        if self.cloud is not None and isinstance(frame, EdgeToCloudFrame):
            return 202, {
                "frame_hash": h,
                "cursor": None,
                "verdict": self.cloud.receive(frame).value,
            }
        self.mail[to].append(raw.decode("utf-8"))
        return 202, {"frame_hash": h, "cursor": len(self.mail[to])}

    def get_frames(self, to: str, after: int = 0, limit: int = 100) -> tuple[int, dict]:
        if to not in self.mail or after < 0 or limit < 1:
            return 400, {"code": "bad_query", "loc": "to", "detail": "to=edge|cloud, after>=0"}
        box = self.mail[to]
        chunk = box[after : after + limit]
        self._log({"op": "get", "to": to, "after": after, "n": len(chunk)})
        return 200, {"frames": [json.loads(f) for f in chunk], "cursor": after + len(chunk)}

    def advance(self, ms: int) -> tuple[int, dict]:
        if not isinstance(ms, int) or isinstance(ms, bool) or ms < 0:
            return 400, {"code": "bad_clock", "loc": "advance_ms", "detail": "a whole ms >= 0"}
        self.mono += ms
        for machine in (self.edge, self.cloud):
            if machine is not None:
                machine.advance(ms)
        self._log({"op": "clock", "advance_ms": ms})
        self._collect()
        return 200, {"now_utc": self.now.isoformat().replace("+00:00", "Z"), "mono_ms": self.mono}

    def faults(self, drop_to_edge: bool, hold_to_cloud: bool) -> tuple[int, dict]:
        self.drop_to_edge = drop_to_edge
        was_holding, self.hold_to_cloud = self.hold_to_cloud, hold_to_cloud
        self._log({"op": "faults", "drop_to_edge": drop_to_edge, "hold_to_cloud": hold_to_cloud})
        if was_holding and not hold_to_cloud:
            for raw in self.held:
                self._deliver("cloud", raw, "sha256:" + hashlib.sha256(raw.encode()).hexdigest())
            self.held = []
        return 200, {"drop_to_edge": drop_to_edge, "hold_to_cloud": hold_to_cloud}

    def anm(self, signal: dict[str, Any], response_ms: int) -> tuple[int, dict]:
        if self.edge is None:
            return 422, {"code": "not_an_edge", "loc": "", "detail": "ANM is injected at an edge"}
        kinds: dict[str, type[AnmSetpoint] | type[AnmTrip] | type[AnmRelease]] = {
            "anm_setpoint": AnmSetpoint,
            "anm_trip": AnmTrip,
            "anm_release": AnmRelease,
        }
        model = kinds.get(str(signal.get("kind")))
        if model is None or not isinstance(response_ms, int) or response_ms < 0:
            return 400, {"code": "bad_signal", "loc": "signal", "detail": "an ANM signal"}
        try:
            parsed = model.model_validate_json(json.dumps(signal))
        except ValidationError as exc:
            return 400, {"code": "bad_signal", "loc": "signal", "detail": str(exc.errors()[0])}
        event = self.edge.inject_anm(parsed, response_ms)
        self._log({"op": "anm", "event": content_hash(event)})
        self._collect()
        return 200, {"event_hash": content_hash(event)}

    def state(self) -> dict[str, Any]:
        machine: Any = self.edge or self.cloud
        base: dict[str, Any] = {
            "role": self.role,
            "now_utc": self.now.isoformat().replace("+00:00", "Z"),
        }
        if machine is not None:
            base.update(machine.state())
        base["mailboxes"] = {k: len(v) for k, v in self.mail.items()}
        base["held_to_cloud"] = len(self.held)
        base["transcript_hash"] = self.transcript_hash()
        return base
