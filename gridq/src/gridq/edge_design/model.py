"""The typed zone and conduit model (D154 and D155).

A design is a graph. Zones are nodes, each with a Purdue level range, a responsible organisation and
whether it terminates and re-originates every session (``protocol_break``). Conduits are typed
edges. A conduit cannot be built without its protocols, its direction and its security controls
(acceptance 1): each is a required field with a non-empty constraint, and a validator derives the
foundational requirements (FRs) the conduit's attributes demand and refuses one whose controls do
not cover them.

Direction is three fields, never one:

- ``initiator``: the zone that opens the session (``a`` or ``b``);
- ``data_flow``: ``a_to_b``, ``b_to_a`` or ``both``;
- ``authority``: `NoWrite` with its reason, or `WriteScope` naming the writer, the written zone,
  the point classes, the lowest IC-5 `ControlStage` at which the write is used, whether it may only
  tighten, and whether a B1-09 safety case is required.

The control vocabulary (`ControlKind`) is closed. Each control's FRs, the SRs it is mapped to at
title level and the NIST CSF 1.1 subcategories it serves are Neura's own design judgement, recorded
in `CONTROLS`. They are not requirement text and claim no conformity.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable
from gridq.contracts.edge_cloud import ControlStage, TriggerCode

__all__ = [
    "COMMS_LOSS_TRIGGERS",
    "CONTROLS",
    "PROTOCOL_IS_IP",
    "PURDUE_ORDER",
    "STAGE_ORDER",
    "BmsConduitMode",
    "BmsVariant",
    "Conduit",
    "ControlKind",
    "ControlSpec",
    "NoWrite",
    "Org",
    "PayloadKind",
    "PointClass",
    "Protocol",
    "PurdueLevel",
    "WriteScope",
    "Zone",
    "ZoneId",
    "required_frs",
    "stage_at_least",
]


class PurdueLevel(StrEnum):
    """Purdue levels in order, the site DMZ between levels 3 and 4 (NIST SP 800-82r3 5.2.3.1)."""

    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    DMZ = "l3_5_dmz"
    L4 = "l4"
    L5 = "l5"


#: Position of each level; two levels are adjacent when their positions differ by one.
PURDUE_ORDER: Final[Mapping[PurdueLevel, int]] = {lv: i for i, lv in enumerate(PurdueLevel)}

STAGE_ORDER: Final[Mapping[ControlStage, int]] = {s: i for i, s in enumerate(ControlStage)}


def stage_at_least(stage: ControlStage, floor: ControlStage) -> bool:
    return STAGE_ORDER[stage] >= STAGE_ORDER[floor]


class Org(StrEnum):
    """The organisation responsible for a zone (a zone criterion in the public ISAGCA guide)."""

    NEURA = "neura"
    ROUTE_TO_MARKET_PARTNER = "route_to_market_partner"
    BESS_OEM = "bess_oem"
    DNO = "dno"
    HOST = "host"
    THERMAFLEX = "thermaflex"
    #: Not known for the reference design: who owns it is a site fact, raised as a data ask.
    UNKNOWN = "unknown"


ZoneId = Literal[
    "neura_cloud",
    "remote_ops",
    "market_partner",
    "site_dmz",
    "edge_control",
    "thermaflex_adapter",
    "bess_oem",
    "site_substation",
    "dno_anm",
    "facility_cooling",
    "metering",
    "facility_power_ro",
]


class Protocol(StrEnum):
    """Spec B1-01's protocols, plus HTTPS for Neura's own northbound and local interfaces and
    hardwired I/O for a DNO signal that is not networked. There is no wireless protocol: the design
    has no wireless conduit (`invariants.no_wireless_conduit`)."""

    MODBUS_TCP = "modbus_tcp"
    SUNSPEC_MODBUS = "sunspec_modbus"
    IEC61850_MMS = "iec61850_mms"
    DNP3 = "dnp3"
    IEC60870_5_104 = "iec60870_5_104"
    BACNET_IP = "bacnet_ip"
    SNMPV3 = "snmpv3"
    OPC_UA = "opc_ua"
    MQTT = "mqtt"
    HTTPS = "https"
    HARDWIRED_IO = "hardwired_io"


#: Whether a protocol is carried over IP (DNP3 is listed by B1-01 beside IEC 104 and is taken as
#: DNP3 over IP; a serial or hardwired DNO path is `HARDWIRED_IO`).
PROTOCOL_IS_IP: Final[Mapping[Protocol, bool]] = {
    p: p is not Protocol.HARDWIRED_IO for p in Protocol
}


class PayloadKind(StrEnum):
    """What a conduit carries. ``code_image`` is executable content (only a signed update image)."""

    IC5_FRAMES = "ic5_frames"
    IC11_MESSAGES = "ic11_messages"
    DEVICE_POINTS = "device_points"
    TELEMETRY = "telemetry"
    ANM_SIGNAL = "anm_signal"
    MARKET_SIGNALS = "market_signals"
    CHILLER_BAND_OFFER = "chiller_band_offer"
    BROKERED_HUMAN_SESSION = "brokered_human_session"
    CODE_IMAGE = "code_image"


class PointClass(StrEnum):
    """Classes of writable point. The registers behind each are WP-EDGE-03's allow-list."""

    BESS_SETPOINT = "bess_setpoint"
    IMPORT_LIMIT = "import_limit"
    CHILLER_SETPOINT = "chiller_setpoint"
    CHILLER_SCHEDULE = "chiller_schedule"
    CHILLER_BAND_OFFER = "chiller_band_offer"
    ANM_SIGNAL = "anm_signal"


class ControlKind(StrEnum):
    """The closed control vocabulary. ``scope`` in `CONTROLS` says whether a control sits on a
    conduit or on a zone."""

    # conduit controls
    MUTUAL_TLS = "mutual_tls"
    VPN_IPSEC = "vpn_ipsec"
    FIREWALL_ALLOW_LIST = "firewall_allow_list"
    PROTOCOL_DPI = "protocol_dpi"
    UNIDIRECTIONAL_GATEWAY = "unidirectional_gateway"
    READ_ONLY_BY_CONSTRUCTION = "read_only_by_construction"
    WRITE_ALLOW_LIST = "write_allow_list"
    HASH_CHAINED_FRAMES = "hash_chained_frames"
    SIGNED_PAYLOAD = "signed_payload"
    AUDIT_IC13 = "audit_ic13"
    FLOW_LOGGING = "flow_logging"
    HARDWIRED_NO_NETWORK = "hardwired_no_network"
    RATE_LIMIT = "rate_limit"
    STORE_AND_FORWARD = "store_and_forward"
    MFA_HUMAN = "mfa_human"
    SESSION_RECORDING = "session_recording"
    DEVICE_BINDING = "device_binding"
    # zone controls
    LOCAL_FAIL_SAFE = "local_fail_safe"
    TYPED_REFUSALS = "typed_refusals"
    CLOCK_SYNC_MONITORED = "clock_sync_monitored"
    SIGNED_UPDATE_ONLY = "signed_update_only"
    DESIGN_BASELINE_PINNED = "design_baseline_pinned"
    ASSET_INVENTORY = "asset_inventory"
    PROTOCOL_BREAK = "protocol_break"


class ControlSpec(ContractModel):
    scope: Literal["conduit", "zone"]
    frs: tuple[Annotated[int, Field(ge=1, le=7)], ...] = Field(min_length=1)
    sr_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    csf: tuple[NonEmptyStr, ...] = Field(min_length=1)
    meaning: NonEmptyStr


def _c(scope: str, frs: tuple[int, ...], srs: str, csf: str, meaning: str) -> ControlSpec:
    return ControlSpec(
        scope=scope,  # type: ignore[arg-type]
        frs=frs,
        sr_ids=tuple(f"SR {s}" for s in srs.split()),
        csf=tuple(csf.split()),
        meaning=meaning,
    )


_K = ControlKind
#: Each control's FRs, title-level SR mapping and NIST CSF 1.1 subcategories (Neura's judgement).
CONTROLS: Final[Mapping[ControlKind, ControlSpec]] = {
    _K.MUTUAL_TLS: _c(
        "conduit",
        (1, 3, 4),
        "1.2 1.8 1.9 3.1 3.8 4.1 4.3",
        "PR.AC-1 PR.AC-7 PR.DS-2 PR.PT-4",
        "both ends present certificates; the session is encrypted and integrity-protected",
    ),
    _K.VPN_IPSEC: _c(
        "conduit",
        (1, 4, 5),
        "1.13 4.1 4.3 5.2",
        "PR.AC-3 PR.DS-2 PR.PT-4",
        "the conduit runs inside an authenticated, encrypted tunnel across an untrusted network",
    ),
    _K.FIREWALL_ALLOW_LIST: _c(
        "conduit",
        (5,),
        "5.1 5.2 7.7",
        "PR.AC-5 PR.PT-4 PR.PT-3",
        "a stateful boundary that permits only the listed endpoints, ports and protocols",
    ),
    _K.PROTOCOL_DPI: _c(
        "conduit",
        (2, 3, 5),
        "3.5 5.2 6.2",
        "PR.PT-4 DE.CM-1",
        "protocol-aware inspection that passes only well-formed messages of the allowed functions",
    ),
    _K.UNIDIRECTIONAL_GATEWAY: _c(
        "conduit",
        (5,),
        "5.1 5.2",
        "PR.PT-4 PR.DS-5",
        "hardware that can carry data one way only (NIST SP 800-82r3 E.1.2)",
    ),
    _K.READ_ONLY_BY_CONSTRUCTION: _c(
        "conduit",
        (2,),
        "2.1 7.7",
        "PR.AC-4 PR.PT-3",
        "the adapter has no write function for this conduit; a write cannot be expressed",
    ),
    _K.WRITE_ALLOW_LIST: _c(
        "conduit",
        (2, 3),
        "2.1 3.5",
        "PR.AC-4 PR.PT-3 PR.PT-4",
        "only declared points of the declared classes are writable (B1-04; registers: WP-EDGE-03)",
    ),
    _K.HASH_CHAINED_FRAMES: _c(
        "conduit",
        (2, 3),
        "2.12 3.1 3.8",
        "PR.DS-6 PR.DS-2",
        "every frame names its predecessor by hash (IC-5 D114), so loss, replay and forks are seen",
    ),
    _K.SIGNED_PAYLOAD: _c(
        "conduit",
        (3,),
        "3.4",
        "PR.DS-6",
        "executable content is accepted only with a valid signature (B1-06, WP-EDGE-06)",
    ),
    _K.AUDIT_IC13: _c(
        "conduit",
        (2, 6),
        "2.8 2.12 3.9 6.1",
        "PR.PT-1 DE.AE-3 RS.AN-3",
        "every command is an IC-13 hash-chained audit event with the identity that issued it",
    ),
    _K.FLOW_LOGGING: _c(
        "conduit",
        (6,),
        "6.1 6.2",
        "DE.CM-1 DE.AE-3",
        "the boundary logs every flow it permits or refuses, for monitoring and investigation",
    ),
    _K.HARDWIRED_NO_NETWORK: _c(
        "conduit",
        (5,),
        "5.1",
        "PR.AC-5 PR.PT-4",
        "a hardwired signal with no network path, so it cannot be reached from any network",
    ),
    _K.RATE_LIMIT: _c(
        "conduit",
        (7,),
        "7.1 7.2",
        "PR.DS-4 PR.PT-5",
        "bounded message rate and size (IC-5 max_frame_bytes), so a flood cannot starve control",
    ),
    _K.STORE_AND_FORWARD: _c(
        "conduit",
        (7,),
        "7.4",
        "PR.IP-4",
        "telemetry is buffered through a link outage and forwarded after it (B1-05, WP-EDGE-04)",
    ),
    _K.MFA_HUMAN: _c(
        "conduit",
        (1,),
        "1.1 1.13",
        "PR.AC-7 PR.AC-3",
        "a person is identified individually and authenticated with more than one factor",
    ),
    _K.SESSION_RECORDING: _c(
        "conduit",
        (6,),
        "2.8 6.2",
        "DE.CM-3 PR.PT-1",
        "every brokered human session is recorded and can be terminated by the broker",
    ),
    _K.DEVICE_BINDING: _c(
        "conduit",
        (1,),
        "1.2",
        "PR.AC-1",
        "the edge talks only to the device identities (address, port) commissioned for the conduit",
    ),
    _K.LOCAL_FAIL_SAFE: _c(
        "zone",
        (3, 7),
        "3.6 7.1",
        "PR.PT-5",
        "the edge holds the IC-5 fail-safe target on any trigger, with no cloud message (B1-02)",
    ),
    _K.TYPED_REFUSALS: _c(
        "zone",
        (3,),
        "3.5 3.7",
        "PR.PT-4",
        "every refused input gets a typed reason code (IC-5 RejectReason, frame verdicts)",
    ),
    _K.CLOCK_SYNC_MONITORED: _c(
        "zone",
        (2, 6),
        "2.11",
        "PR.PT-1",
        "an untrusted wall clock is the IC-5 trigger clock_unsynchronised and enters fail-safe",
    ),
    _K.SIGNED_UPDATE_ONLY: _c(
        "zone",
        (3,),
        "3.2 3.4",
        "PR.DS-6 DE.CM-4",
        "software enters the zone only as a signed image through the update channel (B1-06)",
    ),
    _K.DESIGN_BASELINE_PINNED: _c(
        "zone",
        (7,),
        "7.6",
        "PR.IP-1",
        "the zone's network configuration derives from this hashed design release",
    ),
    _K.ASSET_INVENTORY: _c(
        "zone",
        (7,),
        "7.8",
        "ID.AM-1 ID.AM-2",
        "every asset in the zone is listed, with a site-level inventory from commissioning (B1-08)",
    ),
    _K.PROTOCOL_BREAK: _c(
        "zone",
        (5,),
        "5.2 5.4",
        "PR.PT-4 RS.MI-1",
        "the zone terminates each session and originates a new one; it never routes packets",
    ),
}

#: The IC-5 fail-safe triggers that mean "a conduit is down" (D118). Each maps to a conduit.
COMMS_LOSS_TRIGGERS: Final = frozenset(
    {
        TriggerCode.CLOUD_LINK_LOST,
        TriggerCode.MARKET_LINK_LOST,
        TriggerCode.DNO_SIGNAL_LOST,
        TriggerCode.SITE_METER_LOST,
        TriggerCode.POWER_DEVICE_COMMS_LOST,
        TriggerCode.THERMAL_COMMS_LOST,
    }
)


class Zone(ContractModel):
    zone_id: ZoneId
    name: NonEmptyStr
    #: The highest and lowest Purdue level the zone spans (equal for a one-level zone).
    level_top: PurdueLevel
    level_bottom: PurdueLevel
    responsible: Org
    on_site: bool
    protocol_break: bool
    assets: tuple[NonEmptyStr, ...] = Field(min_length=1)
    controls: tuple[ControlKind, ...]
    purpose: NonEmptyStr

    @model_validator(mode="after")
    def _shape(self) -> Zone:
        if PURDUE_ORDER[self.level_top] < PURDUE_ORDER[self.level_bottom]:
            raise ValueError("level_top must not be below level_bottom")
        if any(CONTROLS[c].scope != "zone" for c in self.controls):
            raise ValueError("a zone carries zone controls only")
        if list(self.controls) != sorted(set(self.controls), key=list(ControlKind).index):
            raise ValueError("zone controls are unique and in enum order")
        if self.protocol_break != (ControlKind.PROTOCOL_BREAK in self.controls):
            raise ValueError("protocol_break is true exactly when the zone carries PROTOCOL_BREAK")
        return self


class NoWrite(ContractModel):
    kind: Literal["no_write"]
    reason: Literal["read_only_by_construction", "unidirectional_hardware", "telemetry_and_intents"]


class WriteScope(ContractModel):
    """A conduit over which one side writes points of the listed classes into the other."""

    kind: Literal["write"]
    writer: Literal["a", "b"]
    point_classes: tuple[PointClass, ...] = Field(min_length=1)
    #: The lowest IC-5 control stage at which this write is used (ADR-D2's staged authority).
    min_stage: ControlStage
    #: True when the write can only lower a limit (the cloud and the DNO only tighten, D119).
    tighten_only: bool
    #: True when a site may use this write only with a B1-09 safety case and host sign-off.
    requires_safety_case: bool

    @model_validator(mode="after")
    def _sorted(self) -> WriteScope:
        if list(self.point_classes) != sorted(set(self.point_classes), key=list(PointClass).index):
            raise ValueError("point_classes are unique and in enum order")
        return self


class Conduit(ContractModel):
    """A logical grouping of channels between two zones (ISAGCA guide), with everything a later
    package needs to test against it. Protocols, direction and controls are all required."""

    conduit_id: Annotated[str, Field(pattern=r"^C[0-9]{1,2}[a-z]?$")]
    name: NonEmptyStr
    zone_a: ZoneId
    zone_b: ZoneId
    protocols: tuple[Protocol, ...] = Field(min_length=1)
    payloads: tuple[PayloadKind, ...] = Field(min_length=1)
    initiator: Literal["a", "b"]
    data_flow: Literal["a_to_b", "b_to_a", "both"]
    authority: NoWrite | WriteScope = Field(discriminator="kind")
    controls: tuple[ControlKind, ...] = Field(min_length=1)
    #: The IC-5 trigger the edge raises when this conduit is lost, or why there is none.
    loss_trigger: TriggerCode | NotApplicable
    human_initiated: bool
    purpose: NonEmptyStr

    @model_validator(mode="after")
    def _shape(self) -> Conduit:
        if self.zone_a == self.zone_b:
            raise ValueError("a conduit joins two different zones")
        for name, seq, enum in (
            ("protocols", self.protocols, Protocol),
            ("payloads", self.payloads, PayloadKind),
            ("controls", self.controls, ControlKind),
        ):
            order = list(enum)  # type: ignore[call-overload]
            if list(seq) != sorted(set(seq), key=order.index):
                raise ValueError(f"{name} are unique and in enum order")
        if any(CONTROLS[c].scope != "conduit" for c in self.controls):
            raise ValueError("a conduit carries conduit controls only")
        if isinstance(self.loss_trigger, TriggerCode) and (
            self.loss_trigger not in COMMS_LOSS_TRIGGERS
        ):
            raise ValueError("loss_trigger must be one of the IC-5 comms-loss triggers")
        missing = required_frs(self) - covered_frs(self)
        if missing:
            raise ValueError(f"controls do not cover the required FRs {sorted(missing)}")
        return self

    @property
    def zones(self) -> tuple[str, str]:
        return (self.zone_a, self.zone_b)

    def side(self, which: Literal["a", "b"]) -> str:
        return self.zone_a if which == "a" else self.zone_b

    @property
    def is_ip(self) -> bool:
        return any(PROTOCOL_IS_IP[p] for p in self.protocols)


def covered_frs(conduit: Conduit) -> frozenset[int]:
    return frozenset(fr for c in conduit.controls for fr in CONTROLS[c].frs)


def required_frs(conduit: Conduit) -> frozenset[int]:
    """The FRs a conduit's own attributes demand (D155), never hand-typed:

    every conduit FR 5 (restricted flow) and FR 6 (events); over IP, FR 1 and FR 3; writing, FR 2
    and FR 3; crossing the site boundary, FR 1, FR 3, FR 4 and FR 7. A human-initiated conduit is
    further required to carry ``mfa_human`` (`invariants.humans_use_mfa`)."""
    need = {5, 6}
    if conduit.is_ip:
        need |= {1, 3}
    if isinstance(conduit.authority, WriteScope):
        need |= {2, 3}
    if _crosses_boundary(conduit):
        need |= {1, 3, 4, 7}
    return frozenset(need)


_OFF_SITE: Final = frozenset({"neura_cloud", "remote_ops", "market_partner"})


def _crosses_boundary(conduit: Conduit) -> bool:
    return (conduit.zone_a in _OFF_SITE) != (conduit.zone_b in _OFF_SITE)


class BmsConduitMode(StrEnum):
    """How a site reaches the facility BMS (D156). The reference default is
    ``diode_read_only``; any other mode at a site carries a deviation reason."""

    NO_BMS_CONDUIT = "no_bms_conduit"
    DIODE_READ_ONLY = "diode_read_only"
    DIODE_WITH_BAND_OFFER = "diode_with_band_offer"
    BROKERED_BIDIRECTIONAL = "brokered_bidirectional"


class BmsVariant(ContractModel):
    """The BMS-side conduits of one mode and the highest control stage the mode permits."""

    mode: BmsConduitMode
    max_stage: ControlStage
    conduits: tuple[Conduit, ...]
    summary: NonEmptyStr
    caveat: NonEmptyStr | NotApplicable
