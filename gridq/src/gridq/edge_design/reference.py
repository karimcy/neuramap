"""The reference zone and conduit design, authored in code (D154 to D156).

Generic: it describes no real facility. A site's own network (addresses, VLANs, firewall products,
the BMS vendor, whether the host requires a diode) is unknown until the host supplies it, and
`gridq.edge_design.site` holds those facts as unknowns with data asks.

Twelve zones, one per responsible organisation and function, each with a Purdue level range. Eleven
base conduits plus the BMS-side conduits of the chosen `BmsConduitMode`. The reference default mode
is ``diode_read_only``.
"""

from __future__ import annotations

from typing import Final

from gridq.contracts._base import NotApplicable, not_applicable
from gridq.contracts.edge_cloud import ControlStage, TriggerCode
from gridq.edge_design.model import (
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

__all__ = ["BASE_CONDUITS", "BMS_VARIANTS", "DEFAULT_BMS_MODE", "ZONES"]

K = ControlKind
P = Protocol
Y = PayloadKind
L = PurdueLevel
T = TriggerCode

DEFAULT_BMS_MODE: Final = BmsConduitMode.DIODE_READ_ONLY


def _zone(
    zone_id: str,
    name: str,
    top: PurdueLevel,
    bottom: PurdueLevel,
    responsible: Org,
    on_site: bool,
    assets: tuple[str, ...],
    controls: tuple[ControlKind, ...],
    purpose: str,
) -> Zone:
    order = list(ControlKind)
    return Zone.model_validate(
        {
            "zone_id": zone_id,
            "name": name,
            "level_top": top,
            "level_bottom": bottom,
            "responsible": responsible,
            "on_site": on_site,
            "protocol_break": K.PROTOCOL_BREAK in controls,
            "assets": assets,
            "controls": tuple(sorted(controls, key=order.index)),
            "purpose": purpose,
        }
    )


ZONES: Final[tuple[Zone, ...]] = (
    _zone(
        "neura_cloud",
        "Neura cloud (live optimisation)",
        L.L5,
        L.L4,
        Org.NEURA,
        False,
        ("B2 forecasting and rolling MPC", "IC-5 cloud endpoint", "B4 telemetry platform"),
        (K.ASSET_INVENTORY,),
        "Plans setpoint schedules inside the IC-3 envelope and sends them as IC-5 frames.",
    ),
    _zone(
        "remote_ops",
        "Remote operations access broker",
        L.L4,
        L.L4,
        Org.NEURA,
        False,
        ("remote-access broker", "Neura operators' workstations"),
        (K.ASSET_INVENTORY,),
        "The only route a person takes to the site; sessions are brokered, never inbound to site.",
    ),
    _zone(
        "market_partner",
        "Route-to-market partner",
        L.L4,
        L.L4,
        Org.ROUTE_TO_MARKET_PARTNER,
        False,
        ("aggregator dispatch system",),
        (K.ASSET_INVENTORY,),
        "Owns market execution (ADR-D2); exchanges market signals with the site.",
    ),
    _zone(
        "site_dmz",
        "Site DMZ",
        L.DMZ,
        L.DMZ,
        Org.NEURA,
        True,
        ("edge gateway northbound interface", "outbound-only session relay"),
        (K.ASSET_INVENTORY, K.DESIGN_BASELINE_PINNED, K.PROTOCOL_BREAK),
        "Terminates every off-site session; nothing routes through it (NIST SP 800-82r3 5.2.3.1).",
    ),
    _zone(
        "edge_control",
        "Neura edge control",
        L.L3,
        L.L2,
        Org.NEURA,
        True,
        ("B1 edge gateway runtime", "protocol adapters", "telemetry buffer (B1-05)"),
        (
            K.ASSET_INVENTORY,
            K.CLOCK_SYNC_MONITORED,
            K.DESIGN_BASELINE_PINNED,
            K.LOCAL_FAIL_SAFE,
            K.PROTOCOL_BREAK,
            K.SIGNED_UPDATE_ONLY,
            K.TYPED_REFUSALS,
        ),
        "Holds the safety-critical constraints autonomously and writes only allow-listed points.",
    ),
    _zone(
        "thermaflex_adapter",
        "ThermaFlex BMS adapter",
        L.L3,
        L.L2,
        Org.THERMAFLEX,
        True,
        ("ThermaFlex adapter, reached through a controlled interface (AWAIT-19)",),
        (K.ASSET_INVENTORY, K.PROTOCOL_BREAK),
        "Translates IC-11 and chiller bands to BACnet/Modbus; Neura sees only its interface.",
    ),
    _zone(
        "bess_oem",
        "OEM BESS EMS and PPC",
        L.L2,
        L.L1,
        Org.BESS_OEM,
        True,
        ("OEM EMS", "power plant controller (PPC)", "PCS"),
        (K.ASSET_INVENTORY,),
        "Certified control and protection (E-08); runs Neura's setpoints inside its own limits.",
    ),
    _zone(
        "site_substation",
        "Site substation",
        L.L2,
        L.L1,
        Org.UNKNOWN,
        True,
        ("IEC 61850 station equipment",),
        (K.ASSET_INVENTORY,),
        "Read-only context for the edge; who owns it is a site fact (data ask).",
    ),
    _zone(
        "dno_anm",
        "DNO RTU and ANM interface",
        L.L1,
        L.L1,
        Org.DNO,
        True,
        ("DNO remote terminal unit",),
        (K.ASSET_INVENTORY,),
        "Sends ANM setpoints and trips to the edge, which enforces them locally (D119).",
    ),
    _zone(
        "facility_cooling",
        "Facility BMS and chiller plant",
        L.L2,
        L.L1,
        Org.HOST,
        True,
        ("BMS supervisory controller", "chiller plant manager", "CRAH/CRAC controllers"),
        (K.ASSET_INVENTORY,),
        "The host's cooling control; the host's own logic stays in charge of the chillers.",
    ),
    _zone(
        "metering",
        "Metering",
        L.L1,
        L.L1,
        Org.UNKNOWN,
        True,
        ("operational meters (1 s)", "revenue meters (COP-compliant)"),
        (K.ASSET_INVENTORY,),
        "Import, export and asset measurement; the meter operator is a site fact (data ask).",
    ),
    _zone(
        "facility_power_ro",
        "Facility power context (read-only)",
        L.L2,
        L.L1,
        Org.HOST,
        True,
        ("UPS", "EPMS", "DCIM"),
        (K.ASSET_INVENTORY,),
        "Read-only context on the IT power chain; never written (B1-01, TF-03).",
    ),
)


def _na(reason: str) -> NotApplicable:
    return not_applicable(reason)


def _conduit(**kw: object) -> Conduit:
    order: dict[str, list[str]] = {
        "protocols": list(Protocol),
        "payloads": list(PayloadKind),
        "controls": list(ControlKind),
    }
    for key, enum_order in order.items():
        items = kw[key]
        assert isinstance(items, tuple)
        kw[key] = tuple(sorted(items, key=enum_order.index))
    return Conduit.model_validate(kw)


_READ = NoWrite(kind="no_write", reason="read_only_by_construction")
_INTENTS = NoWrite(kind="no_write", reason="telemetry_and_intents")
_DIODE = NoWrite(kind="no_write", reason="unidirectional_hardware")

BASE_CONDUITS: Final[tuple[Conduit, ...]] = (
    _conduit(
        conduit_id="C1",
        name="Edge to cloud (IC-5)",
        zone_a="site_dmz",
        zone_b="neura_cloud",
        protocols=(P.MQTT, P.OPC_UA, P.HTTPS),
        payloads=(Y.IC5_FRAMES, Y.TELEMETRY, Y.CODE_IMAGE),
        initiator="a",
        data_flow="both",
        authority=_INTENTS,
        controls=(
            K.MUTUAL_TLS,
            K.FIREWALL_ALLOW_LIST,
            K.HASH_CHAINED_FRAMES,
            K.SIGNED_PAYLOAD,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
            K.STORE_AND_FORWARD,
        ),
        loss_trigger=T.CLOUD_LINK_LOST,
        human_initiated=False,
        purpose=(
            "IC-5 frames both ways over one outbound session the site opens; schedules are "
            "intents the edge checks, never device points. Signed update images (B1-06)."
        ),
    ),
    _conduit(
        conduit_id="C2",
        name="Edge control to DMZ",
        zone_a="edge_control",
        zone_b="site_dmz",
        protocols=(P.MQTT, P.OPC_UA, P.HTTPS),
        payloads=(Y.IC5_FRAMES, Y.TELEMETRY, Y.MARKET_SIGNALS, Y.CODE_IMAGE),
        initiator="a",
        data_flow="both",
        authority=_INTENTS,
        controls=(
            K.MUTUAL_TLS,
            K.FIREWALL_ALLOW_LIST,
            K.HASH_CHAINED_FRAMES,
            K.SIGNED_PAYLOAD,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
        ),
        loss_trigger=T.CLOUD_LINK_LOST,
        human_initiated=False,
        purpose=(
            "The edge's only path off site, re-originated at the DMZ. No human session crosses "
            "it: operators act on the edge only through audited IC-5 operator commands."
        ),
    ),
    _conduit(
        conduit_id="C3",
        name="Edge to OEM BESS EMS/PPC",
        zone_a="edge_control",
        zone_b="bess_oem",
        protocols=(P.MODBUS_TCP, P.SUNSPEC_MODBUS),
        payloads=(Y.DEVICE_POINTS, Y.TELEMETRY),
        initiator="a",
        data_flow="both",
        authority=WriteScope(
            kind="write",
            writer="a",
            point_classes=(PointClass.BESS_SETPOINT, PointClass.IMPORT_LIMIT),
            min_stage=ControlStage.L2_BESS_CLOSED_LOOP,
            tighten_only=False,
            requires_safety_case=True,
        ),
        controls=(
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.WRITE_ALLOW_LIST,
            K.AUDIT_IC13,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
            K.DEVICE_BINDING,
        ),
        loss_trigger=T.POWER_DEVICE_COMMS_LOST,
        human_initiated=False,
        purpose=(
            "BESS setpoints and the site import limit, written from L2 only; the OEM PPC "
            "executes and protects (spec section 8, principle 3)."
        ),
    ),
    _conduit(
        conduit_id="C4",
        name="Edge reads substation",
        zone_a="edge_control",
        zone_b="site_substation",
        protocols=(P.IEC61850_MMS,),
        payloads=(Y.TELEMETRY,),
        initiator="a",
        data_flow="b_to_a",
        authority=_READ,
        controls=(
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.READ_ONLY_BY_CONSTRUCTION,
            K.FLOW_LOGGING,
            K.DEVICE_BINDING,
        ),
        loss_trigger=_na("read-only context: IC-5 1.0.0 has no trigger for substation data"),
        human_initiated=False,
        purpose="Station measurements and status for context; the edge never writes here.",
    ),
    _conduit(
        conduit_id="C5",
        name="DNO ANM to edge",
        zone_a="dno_anm",
        zone_b="edge_control",
        protocols=(P.DNP3, P.IEC60870_5_104, P.HARDWIRED_IO),
        payloads=(Y.ANM_SIGNAL, Y.TELEMETRY),
        initiator="a",
        data_flow="both",
        authority=WriteScope(
            kind="write",
            writer="a",
            point_classes=(PointClass.ANM_SIGNAL,),
            min_stage=ControlStage.L0_READ_ONLY,
            tighten_only=True,
            requires_safety_case=False,
        ),
        controls=(
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.WRITE_ALLOW_LIST,
            K.AUDIT_IC13,
            K.FLOW_LOGGING,
            K.HARDWIRED_NO_NETWORK,
            K.DEVICE_BINDING,
        ),
        loss_trigger=T.DNO_SIGNAL_LOST,
        human_initiated=False,
        purpose=(
            "The DNO's setpoint or trip in, the acknowledgement out; received and enforced at "
            "the edge with the cloud link up or down (B1-03, D119). Hardwired where the DNO "
            "requires it."
        ),
    ),
    _conduit(
        conduit_id="C8",
        name="Edge reads metering",
        zone_a="edge_control",
        zone_b="metering",
        protocols=(P.MODBUS_TCP,),
        payloads=(Y.TELEMETRY,),
        initiator="a",
        data_flow="b_to_a",
        authority=_READ,
        controls=(
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.READ_ONLY_BY_CONSTRUCTION,
            K.FLOW_LOGGING,
            K.DEVICE_BINDING,
        ),
        loss_trigger=T.SITE_METER_LOST,
        human_initiated=False,
        purpose=(
            "Import, export and asset meters. Modbus TCP is what this design asks of the "
            "operational meters; how revenue meters are read is a site fact (data ask)."
        ),
    ),
    _conduit(
        conduit_id="C9",
        name="Edge reads facility power (UPS, EPMS, DCIM)",
        zone_a="edge_control",
        zone_b="facility_power_ro",
        protocols=(P.MODBUS_TCP, P.SNMPV3),
        payloads=(Y.TELEMETRY,),
        initiator="a",
        data_flow="b_to_a",
        authority=_READ,
        controls=(
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.READ_ONLY_BY_CONSTRUCTION,
            K.FLOW_LOGGING,
            K.DEVICE_BINDING,
        ),
        loss_trigger=_na("read-only context: losing it removes context, not a control path"),
        human_initiated=False,
        purpose="SNMPv3 and Modbus reads of the IT power chain; read-only by construction.",
    ),
    _conduit(
        conduit_id="C10",
        name="Site to route-to-market partner",
        zone_a="site_dmz",
        zone_b="market_partner",
        protocols=(P.DNP3, P.IEC60870_5_104),
        payloads=(Y.MARKET_SIGNALS, Y.TELEMETRY),
        initiator="a",
        data_flow="both",
        authority=_INTENTS,
        controls=(
            K.VPN_IPSEC,
            K.FIREWALL_ALLOW_LIST,
            K.PROTOCOL_DPI,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
            K.DEVICE_BINDING,
        ),
        loss_trigger=T.MARKET_LINK_LOST,
        human_initiated=False,
        purpose=(
            "Market instructions and availability, opened from the site. A partner instruction "
            "is an intent the edge checks against the envelope, never a device write."
        ),
    ),
    _conduit(
        conduit_id="C11",
        name="Brokered remote access",
        zone_a="site_dmz",
        zone_b="remote_ops",
        protocols=(P.HTTPS,),
        payloads=(Y.BROKERED_HUMAN_SESSION,),
        initiator="a",
        data_flow="both",
        authority=_INTENTS,
        controls=(
            K.MUTUAL_TLS,
            K.FIREWALL_ALLOW_LIST,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
            K.MFA_HUMAN,
            K.SESSION_RECORDING,
        ),
        loss_trigger=_na("human access only: losing it does not affect control"),
        human_initiated=True,
        purpose=(
            "The site opens an outbound session to the broker; a person joins it with MFA. "
            "No port on site listens for an inbound session."
        ),
    ),
)


# ---------------------------------------------------------------- BMS variants (D156)

_C6_READ = _conduit(
    conduit_id="C6",
    name="Edge reads the ThermaFlex adapter",
    zone_a="edge_control",
    zone_b="thermaflex_adapter",
    protocols=(P.HTTPS,),
    payloads=(Y.IC11_MESSAGES, Y.TELEMETRY),
    initiator="a",
    data_flow="b_to_a",
    authority=_READ,
    controls=(
        K.MUTUAL_TLS,
        K.FIREWALL_ALLOW_LIST,
        K.READ_ONLY_BY_CONSTRUCTION,
        K.FLOW_LOGGING,
        K.RATE_LIMIT,
    ),
    loss_trigger=T.THERMAL_COMMS_LOST,
    human_initiated=False,
    purpose="Cooling telemetry and IC-11 availability, read through the adapter's interface.",
)


def _c6_write(conduit_id: str, name: str, classes: tuple[PointClass, ...], purpose: str) -> Conduit:
    payloads: tuple[PayloadKind, ...] = (Y.IC11_MESSAGES, Y.TELEMETRY)
    if PointClass.CHILLER_BAND_OFFER in classes:
        payloads += (Y.CHILLER_BAND_OFFER,)
    return _conduit(
        conduit_id=conduit_id,
        name=name,
        zone_a="edge_control",
        zone_b="thermaflex_adapter",
        protocols=(P.HTTPS,),
        payloads=payloads,
        initiator="a",
        data_flow="both",
        authority=WriteScope(
            kind="write",
            writer="a",
            point_classes=classes,
            min_stage=ControlStage.L3_CHILLER_BAND,
            tighten_only=False,
            requires_safety_case=True,
        ),
        controls=(
            K.MUTUAL_TLS,
            K.FIREWALL_ALLOW_LIST,
            K.WRITE_ALLOW_LIST,
            K.AUDIT_IC13,
            K.FLOW_LOGGING,
            K.RATE_LIMIT,
        ),
        loss_trigger=T.THERMAL_COMMS_LOST,
        human_initiated=False,
        purpose=purpose,
    )


_C6_OFFER = _c6_write(
    "C6o",
    "Edge to the ThermaFlex adapter (band offer)",
    (PointClass.CHILLER_BAND_OFFER,),
    "The IC-5 chiller band, from L3 only, as an offer the adapter relays one way to the BMS.",
)

_C6_WRITE = _c6_write(
    "C6w",
    "Edge to the ThermaFlex adapter (chiller points)",
    (PointClass.CHILLER_SETPOINT, PointClass.CHILLER_SCHEDULE),
    "Chiller setpoints and schedules agreed in the B1-09 safety case (TF-03), from L3 only.",
)

_C7_DIODE_OUT = _conduit(
    conduit_id="C7",
    name="BMS telemetry out through a data diode",
    zone_a="facility_cooling",
    zone_b="thermaflex_adapter",
    protocols=(P.BACNET_IP, P.MODBUS_TCP),
    payloads=(Y.TELEMETRY,),
    initiator="a",
    data_flow="a_to_b",
    authority=_DIODE,
    controls=(
        K.UNIDIRECTIONAL_GATEWAY,
        K.PROTOCOL_DPI,
        K.READ_ONLY_BY_CONSTRUCTION,
        K.FLOW_LOGGING,
        K.DEVICE_BINDING,
    ),
    loss_trigger=T.THERMAL_COMMS_LOST,
    human_initiated=False,
    purpose=(
        "The host's BMS pushes telemetry out; the hardware cannot carry anything back "
        "(NIST SP 800-82r3 E.1.2)."
    ),
)

_C7_BAND_OFFER_IN = _conduit(
    conduit_id="C7o",
    name="Chiller band offer in, one way",
    zone_a="thermaflex_adapter",
    zone_b="facility_cooling",
    protocols=(P.BACNET_IP,),
    payloads=(Y.CHILLER_BAND_OFFER,),
    initiator="a",
    data_flow="a_to_b",
    authority=WriteScope(
        kind="write",
        writer="a",
        point_classes=(PointClass.CHILLER_BAND_OFFER,),
        min_stage=ControlStage.L3_CHILLER_BAND,
        tighten_only=False,
        requires_safety_case=True,
    ),
    controls=(
        K.UNIDIRECTIONAL_GATEWAY,
        K.PROTOCOL_DPI,
        K.WRITE_ALLOW_LIST,
        K.AUDIT_IC13,
        K.FLOW_LOGGING,
        K.DEVICE_BINDING,
    ),
    loss_trigger=_na(
        "no acknowledgement path: loss shows on C7 as the host no longer adopting the offer"
    ),
    human_initiated=False,
    purpose=(
        "One message type only, a chiller band with a valid-until time, into a declared "
        "host-owned register. The host's BMS logic decides whether to adopt it; an expired "
        "offer is dropped and the BMS keeps its own schedule."
    ),
)

_C7_BROKERED = _conduit(
    conduit_id="C7w",
    name="Brokered BMS read and write",
    zone_a="facility_cooling",
    zone_b="thermaflex_adapter",
    protocols=(P.BACNET_IP, P.MODBUS_TCP),
    payloads=(Y.DEVICE_POINTS, Y.TELEMETRY),
    initiator="b",
    data_flow="both",
    authority=WriteScope(
        kind="write",
        writer="b",
        point_classes=(PointClass.CHILLER_SETPOINT, PointClass.CHILLER_SCHEDULE),
        min_stage=ControlStage.L3_CHILLER_BAND,
        tighten_only=False,
        requires_safety_case=True,
    ),
    controls=(
        K.FIREWALL_ALLOW_LIST,
        K.PROTOCOL_DPI,
        K.WRITE_ALLOW_LIST,
        K.AUDIT_IC13,
        K.FLOW_LOGGING,
        K.RATE_LIMIT,
        K.DEVICE_BINDING,
    ),
    loss_trigger=T.THERMAL_COMMS_LOST,
    human_initiated=False,
    purpose=(
        "Allow-listed chiller setpoints and schedules written through a firewalled protocol "
        "break. A listed gap in the SL2 review: the host relies on software, not hardware."
    ),
)

BMS_VARIANTS: Final[tuple[BmsVariant, ...]] = (
    BmsVariant(
        mode=BmsConduitMode.NO_BMS_CONDUIT,
        max_stage=ControlStage.L2_BESS_CLOSED_LOOP,
        conduits=(),
        summary="No connection to the BMS: battery-only control.",
        caveat=_na("nothing reaches the BMS"),
    ),
    BmsVariant(
        mode=BmsConduitMode.DIODE_READ_ONLY,
        max_stage=ControlStage.L2_BESS_CLOSED_LOOP,
        conduits=(_C6_READ, _C7_DIODE_OUT),
        summary="The reference default: BMS telemetry out through a data diode; no chiller writes.",
        caveat=_na("a true one-way path: nothing can reach the BMS"),
    ),
    BmsVariant(
        mode=BmsConduitMode.DIODE_WITH_BAND_OFFER,
        max_stage=ControlStage.L3_CHILLER_BAND,
        conduits=(_C6_OFFER, _C7_DIODE_OUT, _C7_BAND_OFFER_IN),
        summary=(
            "Two opposed one-way paths: telemetry out, a chiller band offer in, adopted or "
            "ignored by the host's own BMS logic."
        ),
        caveat=(
            "Two opposed one-way paths form a loop at the application level. The assurance comes "
            "from the single fixed message type, the protocol break and the host-side "
            "termination. This mode is not hardware-incapable of carrying data back to the "
            "edge, and must not be presented as if it were."
        ),
    ),
    BmsVariant(
        mode=BmsConduitMode.BROKERED_BIDIRECTIONAL,
        max_stage=ControlStage.L3_CHILLER_BAND,
        conduits=(_C6_WRITE, _C7_BROKERED),
        summary="A firewalled, protocol-break conduit writing allow-listed chiller points.",
        caveat=(
            "Kept because the spec allows a site without a diode. It is a listed gap in the "
            "SL2 review (GAP-62443-20)."
        ),
    ),
)
