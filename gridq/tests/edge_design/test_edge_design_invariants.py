"""The design's invariants: the reference design passes every one in every BMS mode, and each one
fires on a design that breaks it."""

from __future__ import annotations

import pytest
from _edge_support import conduit, fired, variant_of, view, with_conduit, with_zone
from pydantic import ValidationError

from gridq.contracts._base import not_applicable
from gridq.contracts.edge_cloud import ControlStage
from gridq.edge_design import invariants, reference
from gridq.edge_design.model import (
    BmsConduitMode,
    ControlKind,
    NoWrite,
    PayloadKind,
    PointClass,
    PurdueLevel,
    WriteScope,
)
from gridq.edge_design.release import DESIGN, build_design

K = ControlKind
MODES = list(BmsConduitMode)


def _write(classes: tuple[PointClass, ...], stage: ControlStage, writer: str = "a") -> WriteScope:
    return WriteScope(
        kind="write",
        writer=writer,  # type: ignore[arg-type]
        point_classes=classes,
        min_stage=stage,
        tighten_only=False,
        requires_safety_case=True,
    )


def test_reference_design_has_no_violations() -> None:
    assert invariants.check(reference.ZONES, reference.BASE_CONDUITS, reference.BMS_VARIANTS) == ()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", list(invariants.INVARIANTS))
def test_every_invariant_holds_in_every_mode(name: str, mode: BmsConduitMode) -> None:
    assert fired(name, view(mode)) == []


def test_design_refuses_a_violating_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = variant_of(BmsConduitMode.DIODE_READ_ONLY).model_copy(
        update={"conduits": (conduit("C6"), conduit("C7w"))}
    )
    variants = tuple(bad if v.mode is bad.mode else v for v in reference.BMS_VARIANTS)
    monkeypatch.setattr(reference, "BMS_VARIANTS", variants)
    with pytest.raises(ValidationError, match="bms_mode_is_what_it_says"):
        build_design()


def test_conduit_ids_unique() -> None:
    assert fired("conduit_ids_unique", view(extra=[conduit("C1")]))
    renamed = conduit("C6").model_copy(update={"conduit_id": "C3"})
    assert invariants.check(
        reference.ZONES,
        reference.BASE_CONDUITS,
        [
            *reference.BMS_VARIANTS[:1],
            variant_of(BmsConduitMode.DIODE_READ_ONLY).model_copy(update={"conduits": (renamed,)}),
            *reference.BMS_VARIANTS[2:],
        ],
    )


def test_endpoints_are_zones() -> None:
    zones = tuple(z for z in reference.ZONES if z.zone_id != "metering")
    assert fired(
        "endpoints_are_zones",
        invariants.mode_view(
            zones, reference.BASE_CONDUITS, variant_of(reference.DEFAULT_BMS_MODE)
        ),
    )


def test_purdue_adjacent() -> None:
    assert fired("purdue_adjacent", view(base=with_conduit("C2", zone_b="neura_cloud")))
    lowered = with_zone("metering", level_top=PurdueLevel.L0, level_bottom=PurdueLevel.L0)
    assert fired("purdue_adjacent", view(zones=lowered))


def test_enterprise_reaches_only_dmz() -> None:
    v = view(base=with_conduit("C10", zone_a="edge_control"))
    assert fired("enterprise_reaches_only_dmz", v)


def test_boundary_outbound_only() -> None:
    assert fired("boundary_outbound_only", view(base=with_conduit("C11", initiator="b")))
    assert fired("boundary_outbound_only", view(base=with_conduit("C1", initiator="b")))


def test_off_site_reaches_control_only_through_a_break() -> None:
    no_break = with_zone("site_dmz", protocol_break=False)
    assert fired("off_site_reaches_control_only_through_a_break", view(zones=no_break))


def test_anm_stays_on_site() -> None:
    to_cloud = with_conduit(
        "C1", payloads=(PayloadKind.IC5_FRAMES, PayloadKind.TELEMETRY, PayloadKind.ANM_SIGNAL)
    )
    assert fired("anm_stays_on_site", view(base=to_cloud))
    into_bess = with_conduit(
        "C3", authority=_write((PointClass.ANM_SIGNAL,), ControlStage.L0_READ_ONLY)
    )
    assert fired("anm_stays_on_site", view(base=into_bess))


def test_writes_only_into_writable_zones() -> None:
    for cid in ("C4", "C8", "C9"):
        v = view(
            base=with_conduit(
                cid, authority=_write((PointClass.BESS_SETPOINT,), ControlStage.L2_BESS_CLOSED_LOOP)
            )
        )
        assert fired("writes_only_into_writable_zones", v), cid
    wrong_class = with_conduit(
        "C3", authority=_write((PointClass.CHILLER_SETPOINT,), ControlStage.L3_CHILLER_BAND)
    )
    assert fired("writes_only_into_writable_zones", view(base=wrong_class))


def test_writer_opens_and_flows() -> None:
    assert fired("writer_opens_and_flows", view(base=with_conduit("C3", initiator="b")))
    assert fired("writer_opens_and_flows", view(base=with_conduit("C3", data_flow="b_to_a")))


def test_write_stage_matches_point_class() -> None:
    early = with_conduit(
        "C3",
        authority=_write(
            (PointClass.BESS_SETPOINT, PointClass.IMPORT_LIMIT), ControlStage.L1_SHADOW
        ),
    )
    assert fired("write_stage_matches_point_class", view(base=early))
    a = conduit("C3").authority
    no_case = with_conduit("C3", authority=a.model_copy(update={"requires_safety_case": False}))
    assert fired("write_stage_matches_point_class", view(base=no_case))


def test_authority_matches_controls() -> None:
    c3 = conduit("C3")
    no_audit = tuple(k for k in c3.controls if k is not K.AUDIT_IC13)
    assert fired("authority_matches_controls", view(base=with_conduit("C3", controls=no_audit)))
    c4 = conduit("C4")
    no_ro = tuple(k for k in c4.controls if k is not K.READ_ONLY_BY_CONSTRUCTION)
    assert fired("authority_matches_controls", view(base=with_conduit("C4", controls=no_ro)))
    two_way_diode = conduit("C7").model_copy(update={"data_flow": "both"})
    v = view(BmsConduitMode.NO_BMS_CONDUIT, extra=[two_way_diode])
    assert fired("authority_matches_controls", v)
    unbacked = conduit("C7").model_copy(
        update={
            "controls": tuple(
                k for k in conduit("C7").controls if k is not K.UNIDIRECTIONAL_GATEWAY
            )
        }
    )
    assert fired(
        "authority_matches_controls", view(BmsConduitMode.NO_BMS_CONDUIT, extra=[unbacked])
    )


def test_read_only_context_is_one_way() -> None:
    for cid in ("C4", "C8", "C9"):
        assert fired("read_only_context_is_one_way", view(base=with_conduit(cid, data_flow="both")))


def test_humans_use_mfa() -> None:
    c11 = conduit("C11")
    no_mfa = tuple(k for k in c11.controls if k is not K.MFA_HUMAN)
    assert fired("humans_use_mfa", view(base=with_conduit("C11", controls=no_mfa)))
    deeper = with_conduit(
        "C2",
        payloads=(PayloadKind.IC5_FRAMES, PayloadKind.BROKERED_HUMAN_SESSION),
        human_initiated=True,
    )
    assert fired("humans_use_mfa", view(base=deeper))
    assert fired("humans_use_mfa", view(base=with_conduit("C11", human_initiated=False)))


def test_code_enters_only_signed() -> None:
    c1 = conduit("C1")
    unsigned = tuple(k for k in c1.controls if k is not K.SIGNED_PAYLOAD)
    assert fired("code_enters_only_signed", view(base=with_conduit("C1", controls=unsigned)))
    to_bess = with_conduit(
        "C3",
        payloads=(PayloadKind.DEVICE_POINTS, PayloadKind.CODE_IMAGE),
        controls=(*conduit("C3").controls, K.SIGNED_PAYLOAD),
    )
    assert fired("code_enters_only_signed", view(base=to_bess))
    edge = next(z for z in reference.ZONES if z.zone_id == "edge_control")
    loose = with_zone(
        "edge_control", controls=tuple(k for k in edge.controls if k is not K.SIGNED_UPDATE_ONLY)
    )
    assert fired("code_enters_only_signed", view(zones=loose))


def test_no_wireless_conduit() -> None:
    v = view(base=with_conduit("C8", protocols=("wifi_802_11",)))
    assert fired("no_wireless_conduit", v) == ["C8: wifi_802_11 is not a listed wired protocol"]


def test_no_person_to_person_channel() -> None:
    v = view(base=with_conduit("C11", payloads=("email",)))
    assert fired("no_person_to_person_channel", v) == ["C11: email is not a permitted payload"]


def test_loss_triggers_cover_ic5() -> None:
    v = view(base=with_conduit("C8", loss_trigger=not_applicable("x")))
    assert fired("loss_triggers_cover_ic5", v) == [
        "IC-5 trigger site_meter_lost maps to no conduit"
    ]
    assert fired("loss_triggers_cover_ic5", view(BmsConduitMode.NO_BMS_CONDUIT)) == []


def test_every_zone_is_reached() -> None:
    base = tuple(c for c in reference.BASE_CONDUITS if c.conduit_id != "C9")
    assert fired("every_zone_is_reached", view(base=base)) == [
        "zone facility_power_ro has no conduit"
    ]


def test_bms_mode_is_what_it_says() -> None:
    # the diode mode with anything flowing into the BMS, a write or not
    v = view(BmsConduitMode.DIODE_READ_ONLY, extra=[conduit("C7o")])
    assert "diode_read_only: nothing may be written or flow into the BMS" in fired(
        "bms_mode_is_what_it_says", v
    )
    back = conduit("C7").model_copy(
        update={"conduit_id": "C7x", "zone_a": "thermaflex_adapter", "zone_b": "facility_cooling"}
    )
    v = view(BmsConduitMode.DIODE_READ_ONLY, extra=[back])
    assert "diode_read_only: nothing may be written or flow into the BMS" in fired(
        "bms_mode_is_what_it_says", v
    )
    # the band-offer mode with a second message type on the inbound path
    offer = conduit("C7o")
    wider = offer.model_copy(
        update={"payloads": (PayloadKind.DEVICE_POINTS, PayloadKind.CHILLER_BAND_OFFER)}
    )
    var = variant_of(BmsConduitMode.DIODE_WITH_BAND_OFFER)
    var = var.model_copy(
        update={"conduits": tuple(wider if c.conduit_id == "C7o" else c for c in var.conduits)}
    )
    v = invariants.mode_view(reference.ZONES, reference.BASE_CONDUITS, var)
    assert fired("bms_mode_is_what_it_says", v)
    # the band offer terminating somewhere the host does not own
    hostless = with_zone("facility_cooling", responsible="thermaflex")
    v = view(BmsConduitMode.DIODE_WITH_BAND_OFFER, zones=hostless)
    assert fired("bms_mode_is_what_it_says", v)
    # an L3 claim with no chiller write path
    lying = variant_of(BmsConduitMode.DIODE_READ_ONLY).model_copy(
        update={"max_stage": ControlStage.L3_CHILLER_BAND}
    )
    v = invariants.mode_view(reference.ZONES, reference.BASE_CONDUITS, lying)
    assert fired("bms_mode_is_what_it_says", v)


def test_adapter_relays_only_what_reaches_the_bms() -> None:
    var = variant_of(BmsConduitMode.DIODE_WITH_BAND_OFFER)
    var = var.model_copy(
        update={
            "conduits": tuple(conduit("C6w") if c.conduit_id == "C6o" else c for c in var.conduits)
        }
    )
    v = invariants.mode_view(reference.ZONES, reference.BASE_CONDUITS, var)
    assert fired("adapter_relays_only_what_reaches_the_bms", v)


def test_diode_conduit_cannot_carry_a_write() -> None:
    """A conduit declared hardware one-way out of the BMS writes nothing."""
    c7 = conduit("C7")
    assert isinstance(c7.authority, NoWrite) and c7.authority.reason == "unidirectional_hardware"
    assert c7.data_flow == "a_to_b" and c7.zone_a == "facility_cooling"
    assert K.UNIDIRECTIONAL_GATEWAY in c7.controls


def test_the_released_design_is_the_reference() -> None:
    assert DESIGN.zones == reference.ZONES
    assert DESIGN.base_conduits == reference.BASE_CONDUITS
    assert DESIGN.bms_variants == reference.BMS_VARIANTS
