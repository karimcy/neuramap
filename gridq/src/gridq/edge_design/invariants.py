"""The design's machine-checked rules (D154, D155 and D156).

Each invariant is a named, pure function over one BMS mode's conduit set (the base conduits plus
that mode's variant). `check` runs all of them on every mode and returns every violation, so a
design that breaks one cannot be released: `EdgeZoneConduitDesign` refuses it on construction.
What the reference design must make impossible is stated here once, and later edge packages test
their own artefacts against the same functions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from gridq.contracts.edge_cloud import ControlStage, TriggerCode
from gridq.edge_design.model import (
    COMMS_LOSS_TRIGGERS,
    PURDUE_ORDER,
    BmsConduitMode,
    BmsVariant,
    Conduit,
    ControlKind,
    NoWrite,
    Org,
    PayloadKind,
    PointClass,
    Protocol,
    PurdueLevel,
    WriteScope,
    Zone,
)

__all__ = [
    "INVARIANTS",
    "PERMITTED_PAYLOADS",
    "WIRED_PROTOCOLS",
    "WRITE_MIN_STAGE",
    "WRITABLE_BY_ZONE",
    "ModeView",
    "Violation",
    "check",
    "mode_view",
]

K = ControlKind


@dataclass(frozen=True)
class ModeView:
    """One BMS mode's whole design: the zones and the conduits in force."""

    mode: BmsConduitMode
    zones: Mapping[str, Zone]
    conduits: tuple[Conduit, ...]
    variant: BmsVariant


@dataclass(frozen=True, order=True)
class Violation:
    invariant: str
    mode: str
    detail: str


def mode_view(zones: Sequence[Zone], base: Sequence[Conduit], variant: BmsVariant) -> ModeView:
    return ModeView(
        mode=variant.mode,
        zones={z.zone_id: z for z in zones},
        conduits=tuple(base) + tuple(variant.conduits),
        variant=variant,
    )


#: The lowest IC-5 control stage at which each point class is written. Chiller classes need L3 and
#: battery classes L2 (IC-5 `check_setpoint_schedule`, D115); the DNO's ANM signal is received at
#: every stage, L0 included, because the edge must log and enforce it whatever authority it holds.
WRITE_MIN_STAGE: Final[Mapping[PointClass, ControlStage]] = {
    PointClass.BESS_SETPOINT: ControlStage.L2_BESS_CLOSED_LOOP,
    PointClass.IMPORT_LIMIT: ControlStage.L2_BESS_CLOSED_LOOP,
    PointClass.CHILLER_SETPOINT: ControlStage.L3_CHILLER_BAND,
    PointClass.CHILLER_SCHEDULE: ControlStage.L3_CHILLER_BAND,
    PointClass.CHILLER_BAND_OFFER: ControlStage.L3_CHILLER_BAND,
    PointClass.ANM_SIGNAL: ControlStage.L0_READ_ONLY,
}

#: The point classes each zone may be written with. A zone not listed is never written.
WRITABLE_BY_ZONE: Final[Mapping[str, frozenset[PointClass]]] = {
    "bess_oem": frozenset({PointClass.BESS_SETPOINT, PointClass.IMPORT_LIMIT}),
    "thermaflex_adapter": frozenset(
        {PointClass.CHILLER_SETPOINT, PointClass.CHILLER_SCHEDULE, PointClass.CHILLER_BAND_OFFER}
    ),
    "facility_cooling": frozenset(
        {PointClass.CHILLER_SETPOINT, PointClass.CHILLER_SCHEDULE, PointClass.CHILLER_BAND_OFFER}
    ),
    "edge_control": frozenset({PointClass.ANM_SIGNAL}),
}

#: The protocols a conduit may use, listed one by one: all wired. A protocol added to `Protocol`
#: without being listed here breaks `no_wireless_conduit`, which is the point: a wireless path
#: needs a new design decision.
WIRED_PROTOCOLS: Final = frozenset(
    {
        Protocol.MODBUS_TCP,
        Protocol.SUNSPEC_MODBUS,
        Protocol.IEC61850_MMS,
        Protocol.DNP3,
        Protocol.IEC60870_5_104,
        Protocol.BACNET_IP,
        Protocol.SNMPV3,
        Protocol.OPC_UA,
        Protocol.MQTT,
        Protocol.HTTPS,
        Protocol.HARDWIRED_IO,
    }
)
#: The payloads a conduit may carry, listed one by one: machine messages, plus a brokered human
#: session that ends at the DMZ. There is no general person-to-person payload (mail, chat, file
#: share); one added to `PayloadKind` breaks `no_person_to_person_channel`.
PERMITTED_PAYLOADS: Final = frozenset(
    {
        PayloadKind.IC5_FRAMES,
        PayloadKind.IC11_MESSAGES,
        PayloadKind.DEVICE_POINTS,
        PayloadKind.TELEMETRY,
        PayloadKind.ANM_SIGNAL,
        PayloadKind.MARKET_SIGNALS,
        PayloadKind.CHILLER_BAND_OFFER,
        PayloadKind.BROKERED_HUMAN_SESSION,
        PayloadKind.CODE_IMAGE,
    }
)

_BMS_ZONES: Final = frozenset({"thermaflex_adapter", "facility_cooling"})


def _written(c: Conduit) -> str | None:
    if isinstance(c.authority, WriteScope):
        return c.side("b" if c.authority.writer == "a" else "a")
    return None


def _flows(c: Conduit, src: str, dst: str) -> bool:
    if c.data_flow == "both":
        return True
    return (c.zone_a, c.zone_b) == ((src, dst) if c.data_flow == "a_to_b" else (dst, src))


def _gap(a: Zone, b: Zone) -> int:
    """How many Purdue positions separate the nearest levels of two zones (0 when they overlap)."""
    a_lo, a_hi = PURDUE_ORDER[a.level_bottom], PURDUE_ORDER[a.level_top]
    b_lo, b_hi = PURDUE_ORDER[b.level_bottom], PURDUE_ORDER[b.level_top]
    return max(0, b_lo - a_hi, a_lo - b_hi)


# ------------------------------------------------------------------------------- the invariants


def conduit_ids_unique(v: ModeView) -> Iterable[str]:
    ids = [c.conduit_id for c in v.conduits]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        yield f"{i} appears more than once"


def endpoints_are_zones(v: ModeView) -> Iterable[str]:
    for c in v.conduits:
        for z in c.zones:
            if z not in v.zones:
                yield f"{c.conduit_id} names undeclared zone {z}"


def purdue_adjacent(v: ModeView) -> Iterable[str]:
    """A conduit joins zones whose Purdue levels overlap or are adjacent (NIST SP 800-82r3
    5.2.3.1: rules only between adjacent levels; the DMZ sits between levels 3 and 4)."""
    for c in v.conduits:
        if _gap(v.zones[c.zone_a], v.zones[c.zone_b]) > 1:
            yield f"{c.conduit_id} skips a Purdue level ({c.zone_a} to {c.zone_b})"


def enterprise_reaches_only_dmz(v: ModeView) -> Iterable[str]:
    """A zone at level 4 or 5 (the cloud, remote operations, the market partner) connects only to
    the DMZ: no cloud conduit lands on edge control or anything below it."""
    for c in v.conduits:
        for here, there in ((c.zone_a, c.zone_b), (c.zone_b, c.zone_a)):
            zh, zt = v.zones[here], v.zones[there]
            if PURDUE_ORDER[zh.level_bottom] >= PURDUE_ORDER[PurdueLevel.L4] and (
                zt.level_top is not PurdueLevel.DMZ or zt.level_bottom is not PurdueLevel.DMZ
            ):
                yield f"{c.conduit_id} joins enterprise zone {here} to non-DMZ zone {there}"


def boundary_outbound_only(v: ModeView) -> Iterable[str]:
    """Every conduit across the site boundary is opened from the site: nothing on site listens."""
    for c in v.conduits:
        a_on, b_on = v.zones[c.zone_a].on_site, v.zones[c.zone_b].on_site
        if a_on != b_on and not v.zones[c.side(c.initiator)].on_site:
            yield f"{c.conduit_id} is opened from off site"


def off_site_reaches_control_only_through_a_break(v: ModeView) -> Iterable[str]:
    """With every protocol-break zone removed, no off-site zone reaches any on-site zone: every
    route from the internet to a controller is terminated and re-originated at least once."""
    keep = {z for z, zone in v.zones.items() if not zone.protocol_break}
    adj: dict[str, set[str]] = {z: set() for z in keep}
    for c in v.conduits:
        if c.zone_a in keep and c.zone_b in keep:
            adj[c.zone_a].add(c.zone_b)
            adj[c.zone_b].add(c.zone_a)
    for start in sorted(z for z in keep if not v.zones[z].on_site):
        seen, todo = {start}, [start]
        while todo:
            for n in adj[todo.pop()]:
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        for z in sorted(seen):
            if v.zones[z].on_site:
                yield f"{start} reaches {z} without a protocol break"


def anm_stays_on_site(v: ModeView) -> Iterable[str]:
    """The DNO's ANM signal travels only on site and lands on the edge (D119): no conduit carrying
    it touches the cloud or any off-site zone."""
    for c in v.conduits:
        carries = PayloadKind.ANM_SIGNAL in c.payloads or (
            isinstance(c.authority, WriteScope)
            and PointClass.ANM_SIGNAL in c.authority.point_classes
        )
        if not carries:
            continue
        if not all(v.zones[z].on_site for z in c.zones):
            yield f"{c.conduit_id} carries the ANM signal off site"
        if set(c.zones) != {"dno_anm", "edge_control"}:
            yield f"{c.conduit_id} carries the ANM signal between {c.zone_a} and {c.zone_b}"


def writes_only_into_writable_zones(v: ModeView) -> Iterable[str]:
    """A write lands only in a zone that admits that point class: never the UPS, EPMS or DCIM,
    the meters, the substation, the DNO's RTU or any off-site zone."""
    for c in v.conduits:
        target = _written(c)
        if target is None:
            continue
        assert isinstance(c.authority, WriteScope)
        allowed = WRITABLE_BY_ZONE.get(target, frozenset())
        for pc in c.authority.point_classes:
            if pc not in allowed:
                yield f"{c.conduit_id} writes {pc.value} into {target}"


def writer_opens_and_flows(v: ModeView) -> Iterable[str]:
    """The writer opens the session, and data can flow from the writer to the written zone."""
    for c in v.conduits:
        target = _written(c)
        if target is None:
            continue
        assert isinstance(c.authority, WriteScope)
        if c.authority.writer != c.initiator:
            yield f"{c.conduit_id}: the writer does not open the session"
        if not _flows(c, c.side(c.authority.writer), target):
            yield f"{c.conduit_id}: data cannot flow from the writer to {target}"


def write_stage_matches_point_class(v: ModeView) -> Iterable[str]:
    """A write's lowest stage is the highest `WRITE_MIN_STAGE` of its point classes, and every
    chiller or battery write needs the B1-09 safety case."""
    order = list(ControlStage)
    for c in v.conduits:
        if not isinstance(c.authority, WriteScope):
            continue
        need = max((WRITE_MIN_STAGE[p] for p in c.authority.point_classes), key=order.index)
        if c.authority.min_stage is not need:
            yield f"{c.conduit_id}: min_stage {c.authority.min_stage.value} != {need.value}"
        controls_plant = any(p is not PointClass.ANM_SIGNAL for p in c.authority.point_classes)
        if controls_plant and not c.authority.requires_safety_case:
            yield f"{c.conduit_id}: a plant write without the B1-09 safety case"


def authority_matches_controls(v: ModeView) -> Iterable[str]:
    """A write carries the write allow-list and IC-13 audit; a read-only conduit carries
    read-only-by-construction; a diode conduit carries the unidirectional gateway and is one way."""
    for c in v.conduits:
        a, ctl = c.authority, set(c.controls)
        if isinstance(a, WriteScope) and not {K.WRITE_ALLOW_LIST, K.AUDIT_IC13} <= ctl:
            yield f"{c.conduit_id}: a write without the allow-list and IC-13 audit"
        if isinstance(a, NoWrite):
            if K.WRITE_ALLOW_LIST in ctl:
                yield f"{c.conduit_id}: a write allow-list on a conduit that cannot write"
            if a.reason == "read_only_by_construction" and K.READ_ONLY_BY_CONSTRUCTION not in ctl:
                yield f"{c.conduit_id}: read-only without read_only_by_construction"
            if a.reason == "unidirectional_hardware" and K.UNIDIRECTIONAL_GATEWAY not in ctl:
                yield f"{c.conduit_id}: unidirectional_hardware without the gateway"
        if K.UNIDIRECTIONAL_GATEWAY in ctl and c.data_flow == "both":
            yield f"{c.conduit_id}: a unidirectional gateway on a two-way conduit"
        if K.READ_ONLY_BY_CONSTRUCTION in ctl and isinstance(a, WriteScope):
            yield f"{c.conduit_id}: read_only_by_construction on a write conduit"


def read_only_context_is_one_way(v: ModeView) -> Iterable[str]:
    """Facility power, metering and the substation are read: data flows only out of them."""
    for c in v.conduits:
        for z in ("facility_power_ro", "metering", "site_substation"):
            if z in c.zones:
                other = c.zone_b if c.zone_a == z else c.zone_a
                if _flows(c, other, z):
                    yield f"{c.conduit_id}: data can flow into read-only zone {z}"


def humans_use_mfa(v: ModeView) -> Iterable[str]:
    """A person reaches the site only by a brokered session that ends at the DMZ, with MFA and
    session recording."""
    for c in v.conduits:
        human = PayloadKind.BROKERED_HUMAN_SESSION in c.payloads
        if human != c.human_initiated:
            yield f"{c.conduit_id}: human_initiated and the brokered-session payload disagree"
        if human:
            if set(c.zones) != {"site_dmz", "remote_ops"}:
                yield f"{c.conduit_id}: a human session beyond the DMZ"
            if not {K.MFA_HUMAN, K.SESSION_RECORDING} <= set(c.controls):
                yield f"{c.conduit_id}: a human session without MFA and recording"


def code_enters_only_signed(v: ModeView) -> Iterable[str]:
    """Executable content travels only as a signed image, and only into a zone that admits code
    through the signed update channel alone (B1-06)."""
    for c in v.conduits:
        if PayloadKind.CODE_IMAGE in c.payloads:
            if K.SIGNED_PAYLOAD not in c.controls:
                yield f"{c.conduit_id}: code without signed_payload"
            for z in c.zones:
                zone = v.zones[z]
                if zone.on_site and z != "site_dmz" and K.SIGNED_UPDATE_ONLY not in zone.controls:
                    yield f"{c.conduit_id}: code into {z}, which lacks signed_update_only"


def no_wireless_conduit(v: ModeView) -> Iterable[str]:
    """Every conduit is wired: no protocol outside the listed wired set."""
    for c in v.conduits:
        for p in c.protocols:
            if p not in WIRED_PROTOCOLS:
                yield f"{c.conduit_id}: {p} is not a listed wired protocol"


def no_person_to_person_channel(v: ModeView) -> Iterable[str]:
    """No conduit carries mail, chat or file sharing: only the listed machine payloads and the
    brokered human session."""
    for c in v.conduits:
        for y in c.payloads:
            if y not in PERMITTED_PAYLOADS:
                yield f"{c.conduit_id}: {y} is not a permitted payload"


def loss_triggers_cover_ic5(v: ModeView) -> Iterable[str]:
    """Every IC-5 comms-loss trigger names a conduit, so WP-EDGE-04 has a conduit to break for each
    fault-injection test. A mode with no BMS conduit has no thermal link to lose."""
    mapped = {c.loss_trigger for c in v.conduits if isinstance(c.loss_trigger, TriggerCode)}
    expected = set(COMMS_LOSS_TRIGGERS)
    if v.mode is BmsConduitMode.NO_BMS_CONDUIT:
        expected.discard(TriggerCode.THERMAL_COMMS_LOST)
    for t in sorted(expected - mapped, key=list(TriggerCode).index):
        yield f"IC-5 trigger {t.value} maps to no conduit"


def every_zone_is_reached(v: ModeView) -> Iterable[str]:
    used = {z for c in v.conduits for z in c.zones}
    for z in sorted(v.zones):
        if z in _BMS_ZONES and v.mode is BmsConduitMode.NO_BMS_CONDUIT:
            continue
        if z not in used:
            yield f"zone {z} has no conduit"


def bms_mode_is_what_it_says(v: ModeView) -> Iterable[str]:
    """D156. The variant's conduits touch only the BMS chain; L3 exactly when a chiller
    write exists; the diode mode writes nothing and lets nothing into the BMS; the band-offer mode
    has exactly two opposed one-way paths whose inbound one carries only the band offer into the
    host's zone."""
    var = v.variant
    for c in var.conduits:
        if not set(c.zones) & _BMS_ZONES or not set(c.zones) <= _BMS_ZONES | {"edge_control"}:
            yield f"{c.conduit_id} is not a BMS-chain conduit"
    chiller_write = any(
        isinstance(c.authority, WriteScope)
        and set(c.authority.point_classes) & WRITABLE_BY_ZONE["facility_cooling"]
        for c in var.conduits
    )
    if (var.max_stage is ControlStage.L3_CHILLER_BAND) != chiller_write:
        yield f"{var.mode.value}: max_stage L3 must mean a chiller write path exists"
    if var.max_stage not in (ControlStage.L2_BESS_CLOSED_LOOP, ControlStage.L3_CHILLER_BAND):
        yield f"{var.mode.value}: max_stage must be L2 or L3"
    bms = [c for c in var.conduits if "facility_cooling" in c.zones]
    into_bms = [c for c in bms if _flows(c, _other(c, "facility_cooling"), "facility_cooling")]
    if var.mode in (BmsConduitMode.NO_BMS_CONDUIT, BmsConduitMode.DIODE_READ_ONLY):
        if chiller_write or into_bms:
            yield f"{var.mode.value}: nothing may be written or flow into the BMS"
    if var.mode is BmsConduitMode.DIODE_READ_ONLY and (
        len(bms) != 1 or K.UNIDIRECTIONAL_GATEWAY not in bms[0].controls
    ):
        yield "diode_read_only: exactly one BMS conduit, through a unidirectional gateway"
    if var.mode is BmsConduitMode.DIODE_WITH_BAND_OFFER:
        one_way = [
            c for c in bms if c.data_flow != "both" and K.UNIDIRECTIONAL_GATEWAY in c.controls
        ]
        if len(bms) != 2 or len(one_way) != 2 or len(into_bms) != 1:
            yield "diode_with_band_offer: exactly two opposed one-way BMS conduits"
        for c in into_bms:
            a = c.authority
            if c.payloads != (PayloadKind.CHILLER_BAND_OFFER,) or not (
                isinstance(a, WriteScope) and a.point_classes == (PointClass.CHILLER_BAND_OFFER,)
            ):
                yield f"{c.conduit_id}: the inbound path carries the band offer and nothing else"
            if v.zones["facility_cooling"].responsible is not Org.HOST:
                yield f"{c.conduit_id}: the band offer must terminate in a host-owned zone"


def adapter_relays_only_what_reaches_the_bms(v: ModeView) -> Iterable[str]:
    """What the edge writes into the ThermaFlex adapter is exactly what the adapter may write into
    the BMS in that mode: the adapter is a relay, never a place where a write widens or dies."""
    into: set[PointClass] = set()
    onward: set[PointClass] = set()
    for c in v.conduits:
        a, target = c.authority, _written(c)
        if not isinstance(a, WriteScope):
            continue
        if target == "thermaflex_adapter":
            into |= set(a.point_classes)
        if target == "facility_cooling" and c.side(a.writer) == "thermaflex_adapter":
            onward |= set(a.point_classes)
    for pc in sorted(into ^ onward, key=list(PointClass).index):
        side = "into the adapter only" if pc in into else "onward to the BMS only"
        yield f"{pc.value} is written {side}"


def _other(c: Conduit, zone: str) -> str:
    return c.zone_b if c.zone_a == zone else c.zone_a


Invariant = Callable[[ModeView], Iterable[str]]

#: Every invariant by name, with its one-line statement for the design document.
INVARIANTS: Final[Mapping[str, tuple[str, Invariant]]] = {
    f.__name__: ((f.__doc__ or f.__name__.replace("_", " ")).split("\n\n")[0], f)
    for f in (
        conduit_ids_unique,
        endpoints_are_zones,
        purdue_adjacent,
        enterprise_reaches_only_dmz,
        boundary_outbound_only,
        off_site_reaches_control_only_through_a_break,
        anm_stays_on_site,
        writes_only_into_writable_zones,
        writer_opens_and_flows,
        write_stage_matches_point_class,
        authority_matches_controls,
        read_only_context_is_one_way,
        humans_use_mfa,
        code_enters_only_signed,
        no_wireless_conduit,
        no_person_to_person_channel,
        loss_triggers_cover_ic5,
        every_zone_is_reached,
        bms_mode_is_what_it_says,
        adapter_relays_only_what_reaches_the_bms,
    )
}


def check(
    zones: Sequence[Zone], base: Sequence[Conduit], variants: Sequence[BmsVariant]
) -> tuple[Violation, ...]:
    """Every violation of every invariant in every BMS mode, sorted. Empty means releasable."""
    out: list[Violation] = []
    modes = [v.mode for v in variants]
    if sorted(modes, key=list(BmsConduitMode).index) != list(BmsConduitMode):
        out.append(Violation("bms_modes_complete", "all", "one variant per BmsConduitMode"))
    by_id: dict[str, Conduit] = {}
    for c in [*base, *(c for v in variants for c in v.conduits)]:
        if c.conduit_id in by_id and by_id[c.conduit_id] != c:
            out.append(Violation("conduit_ids_unique", "all", f"{c.conduit_id} has two meanings"))
        by_id[c.conduit_id] = c
    for variant in variants:
        view = mode_view(zones, base, variant)
        for name, (_, fn) in INVARIANTS.items():
            out.extend(Violation(name, variant.mode.value, d) for d in fn(view))
    return tuple(sorted(set(out)))
