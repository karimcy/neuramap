"""Binding the reference design to a real site, with no invented site facts.

A facility's actual network is unknown until the host, the DNO, the OEM and the others tell us.
`SiteZoneConduitInstance` pins the design release by hash and holds every site fact the design
needs as either a supplied value or `Unknown` with its data ask (`DESIGN_ASKS`).
`unbound_instance` is the starting point for any site: every fact unknown, the BMS mode unknown,
and therefore no chiller authority (the highest stage it permits is L2).

A site that picks a BMS mode other than the reference default records why. A mode that permits L3
(a chiller write path) needs the B1-09 safety case and the host's sign-off, each by hash; an
`Unknown` is refused there.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    unknown,
)
from gridq.contracts.edge_cloud import ControlStage
from gridq.edge_design.model import BmsConduitMode
from gridq.edge_design.reference import BMS_VARIANTS, DEFAULT_BMS_MODE

__all__ = [
    "DESIGN_ASKS",
    "DesignAsk",
    "SiteFact",
    "SiteZoneConduitInstance",
    "max_stage_for",
    "unbound_instance",
]

Holder = Literal[
    "host", "dno", "bess_oem", "meter_operator", "route_to_market_partner", "thermaflex"
]


class DesignAsk(ContractModel):
    """A site fact the design needs and does not have, and who can supply it."""

    ask_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    fact: NonEmptyStr
    request: NonEmptyStr
    holder: Holder
    affects: tuple[NonEmptyStr, ...] = Field(min_length=1)


def _ask(ask_id: str, holder: str, affects: str, fact: str, request: str) -> DesignAsk:
    return DesignAsk.model_validate(
        {
            "ask_id": ask_id,
            "fact": fact,
            "request": request,
            "holder": holder,
            "affects": tuple(affects.split()),
        }
    )


#: Every site fact the design needs. None is assumed for any site.
DESIGN_ASKS: Final[tuple[DesignAsk, ...]] = (
    _ask(
        "facility_network_topology",
        "host",
        "zone:site_dmz zone:edge_control conduit:C2",
        "The facility's OT network: segments, addressing and where the edge equipment attaches.",
        "A current OT network diagram with segments, address ranges and the proposed edge "
        "location.",
    ),
    _ask(
        "existing_segmentation",
        "host",
        "zone:site_dmz conduit:C9",
        "Firewalls and segmentation already in place between the facility's IT, OT and BMS "
        "networks.",
        "The products and the rule ownership at each existing boundary.",
    ),
    _ask(
        "bms_vendor_and_protocol",
        "host",
        "zone:facility_cooling conduit:C7",
        "The BMS vendor and product, the protocol it exposes (BACnet/IP or Modbus) and the points.",
        "The BMS make and model, the protocol and a points list for the chiller plant.",
    ),
    _ask(
        "host_bms_mode",
        "host",
        "zone:facility_cooling conduit:C7",
        "Whether the host requires a data diode in front of the BMS, and whether it would let "
        "its BMS adopt a one-way chiller band offer.",
        "The host's policy on connections to the BMS and its answer on the band-offer mode.",
    ),
    _ask(
        "host_remote_access_policy",
        "host",
        "conduit:C11",
        "The host's rules for remote access by a third party to equipment on its site.",
        "The host's remote-access policy and any approval it needs per session.",
    ),
    _ask(
        "gateway_emergency_power",
        "host",
        "zone:site_dmz zone:edge_control",
        "Whether the gateway, DMZ equipment and diode can sit on a backed-up supply, and for how "
        "long.",
        "Which backed-up supply can feed the edge equipment, and its autonomy.",
    ),
    _ask(
        "dno_anm_path",
        "dno",
        "zone:dno_anm conduit:C5",
        "How the DNO's ANM signal reaches the site: to the edge, directly to the PPC, or both, "
        "and by which protocol or hardwired path.",
        "The ANM scheme's interface terms from the connection offer or the DNO.",
    ),
    _ask(
        "revenue_meter_access",
        "meter_operator",
        "zone:metering conduit:C8",
        "Who operates the revenue meters and whether they may be read locally, and how.",
        "The meter operator's name and its terms for local reads.",
    ),
    _ask(
        "substation_owner_and_61850",
        "host",
        "zone:site_substation conduit:C4",
        "Who owns the site substation and whether its IEC 61850 data can be read.",
        "The substation owner and whether a read-only IEC 61850 MMS client is permitted.",
    ),
    _ask(
        "partner_link",
        "route_to_market_partner",
        "zone:market_partner conduit:C10",
        "Whether the route-to-market partner needs a link to the site at all, or reaches it "
        "only through Neura's cloud, and if so with which protocol.",
        "The partner's site-integration requirements.",
    ),
    _ask(
        "oem_interfaces",
        "bess_oem",
        "zone:bess_oem conduit:C3",
        "The OEM EMS and PPC interfaces, register map and any IEC 62443 certification (E-08).",
        "The OEM's interface specification and certification evidence.",
    ),
    _ask(
        "thermaflex_interface",
        "thermaflex",
        "zone:thermaflex_adapter conduit:C6",
        "The controlled interface ThermaFlex exposes to the edge (AWAIT-19).",
        "ThermaFlex's interface specification.",
    ),
)

_ASK_IDS: Final = tuple(a.ask_id for a in DESIGN_ASKS)
_L3_MODES: Final = frozenset(
    v.mode for v in BMS_VARIANTS if v.max_stage is ControlStage.L3_CHILLER_BAND
)


class SiteFact(ContractModel):
    ask_id: NonEmptyStr
    value: NonEmptyStr | Unknown


def max_stage_for(mode: BmsConduitMode | Unknown) -> ControlStage:
    """The highest IC-5 stage a site's BMS mode permits. An unknown mode permits no chiller
    authority."""
    if isinstance(mode, Unknown):
        return ControlStage.L2_BESS_CLOSED_LOOP
    return next(v.max_stage for v in BMS_VARIANTS if v.mode is mode)


class SiteZoneConduitInstance(ContractModel):
    site_id: NonEmptyStr
    #: The design release this site binds to (`release.DESIGN_HASH`).
    design_hash: Sha256
    bms_mode: BmsConduitMode | Unknown
    #: Why the site departs from the reference default mode.
    deviation_reason: NonEmptyStr | NotApplicable | Unknown
    #: The B1-09 safety case and the host's sign-off, each by hash (required in an L3 mode).
    safety_case_ref: Sha256 | NotApplicable | Unknown
    host_signoff_ref: Sha256 | NotApplicable | Unknown
    facts: tuple[SiteFact, ...]

    @model_validator(mode="after")
    def _bound(self) -> SiteZoneConduitInstance:
        from gridq.edge_design.release import DESIGN_HASH

        if self.design_hash != DESIGN_HASH:
            raise ValueError("design_hash must be the released design's hash")
        if tuple(f.ask_id for f in self.facts) != _ASK_IDS:
            raise ValueError("facts answer every design ask once, in order")
        mode = self.bms_mode
        if isinstance(mode, Unknown):
            if not isinstance(self.deviation_reason, Unknown):
                raise ValueError("with the mode unknown, the deviation reason is unknown")
        elif mode is DEFAULT_BMS_MODE:
            if not isinstance(self.deviation_reason, NotApplicable):
                raise ValueError("the default mode is not a deviation")
        elif not isinstance(self.deviation_reason, str):
            raise ValueError(f"{mode.value} departs from the default and must say why")
        for name, ref in (
            ("safety_case_ref", self.safety_case_ref),
            ("host_signoff_ref", self.host_signoff_ref),
        ):
            if mode in _L3_MODES and not isinstance(ref, str):
                raise ValueError(f"an L3 mode needs {name} as a hash")
            if not isinstance(mode, Unknown) and mode not in _L3_MODES and isinstance(ref, str):
                raise ValueError(f"{name} is not applicable without a chiller write path")
        return self

    @property
    def max_stage(self) -> ControlStage:
        return max_stage_for(self.bms_mode)

    @property
    def open_asks(self) -> tuple[DesignAsk, ...]:
        unknown_ids = {f.ask_id for f in self.facts if isinstance(f.value, Unknown)}
        return tuple(a for a in DESIGN_ASKS if a.ask_id in unknown_ids)


def unbound_instance(site_id: str) -> SiteZoneConduitInstance:
    """A site with nothing known: every fact unknown with its ask, the mode unknown."""
    from gridq.edge_design.release import DESIGN_HASH

    pending = "not yet supplied: data ask raised"
    return SiteZoneConduitInstance(
        site_id=site_id,
        design_hash=DESIGN_HASH,
        bms_mode=unknown("the host has not said whether it requires a data diode (host_bms_mode)"),
        deviation_reason=unknown("the BMS mode is unknown"),
        safety_case_ref=unknown("the BMS mode is unknown"),
        host_signoff_ref=unknown("the BMS mode is unknown"),
        facts=tuple(
            SiteFact(ask_id=a.ask_id, value=unknown(f"{pending} ({a.holder})")) for a in DESIGN_ASKS
        ),
    )
