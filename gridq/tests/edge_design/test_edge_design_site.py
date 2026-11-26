"""A site binds to the design by hash with every site fact unknown until supplied; no facility's
network is invented."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from gridq.contracts.edge_cloud import ControlStage
from gridq.edge_design.model import BmsConduitMode
from gridq.edge_design.release import DESIGN_HASH
from gridq.edge_design.site import (
    DESIGN_ASKS,
    SiteZoneConduitInstance,
    max_stage_for,
    unbound_instance,
)

H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64
NA = {"kind": "not_applicable", "reason": "x"}


def _site(**update: Any) -> dict[str, Any]:
    data = json.loads(unbound_instance("SYNTHETIC-SITE").model_dump_json())
    data.update(update)
    return data


def _build(**update: Any) -> SiteZoneConduitInstance:
    return SiteZoneConduitInstance.model_validate_json(json.dumps(_site(**update)))


def test_unbound_instance_is_all_unknown_and_l2() -> None:
    u = unbound_instance("SYNTHETIC-SITE")
    assert u.design_hash == DESIGN_HASH
    assert all(f.value.kind == "unknown" for f in u.facts)  # type: ignore[union-attr]
    assert u.open_asks == DESIGN_ASKS
    assert u.max_stage is ControlStage.L2_BESS_CLOSED_LOOP
    assert u.bms_mode.kind == "unknown"  # type: ignore[union-attr]


def test_instance_pins_design_hash() -> None:
    with pytest.raises(ValidationError, match="released design"):
        _build(design_hash=H1)


def test_facts_answer_every_ask() -> None:
    data = _site()
    data["facts"] = data["facts"][1:]
    with pytest.raises(ValidationError, match="every design ask"):
        SiteZoneConduitInstance.model_validate_json(json.dumps(data))
    data = _site()
    data["facts"][0]["value"] = "Segment 10.0.0.0/24 for the edge (host email 2027-01-05)"
    site = SiteZoneConduitInstance.model_validate_json(json.dumps(data))
    assert len(site.open_asks) == len(DESIGN_ASKS) - 1


def test_default_mode_is_not_a_deviation() -> None:
    ok = _build(
        bms_mode="diode_read_only", deviation_reason=NA, safety_case_ref=NA, host_signoff_ref=NA
    )
    assert ok.max_stage is ControlStage.L2_BESS_CLOSED_LOOP
    with pytest.raises(ValidationError, match="not a deviation"):
        _build(
            bms_mode="diode_read_only",
            deviation_reason="because",
            safety_case_ref=NA,
            host_signoff_ref=NA,
        )


def test_deviation_needs_reason() -> None:
    with pytest.raises(ValidationError, match="must say why"):
        _build(
            bms_mode="no_bms_conduit", deviation_reason=NA, safety_case_ref=NA, host_signoff_ref=NA
        )
    ok = _build(
        bms_mode="no_bms_conduit",
        deviation_reason="battery only at this site",
        safety_case_ref=NA,
        host_signoff_ref=NA,
    )
    assert ok.max_stage is ControlStage.L2_BESS_CLOSED_LOOP


@pytest.mark.parametrize("mode", ["diode_with_band_offer", "brokered_bidirectional"])
def test_l3_mode_needs_safety_case_and_signoff(mode: str) -> None:
    reason = "host accepts a band offer"
    unknown_ref = {"kind": "unknown", "reason": "not yet signed"}
    for case, signoff in ((unknown_ref, H2), (H1, unknown_ref), (NA, H2), (H1, NA)):
        with pytest.raises(ValidationError, match="L3 mode needs"):
            _build(
                bms_mode=mode,
                deviation_reason=reason,
                safety_case_ref=case,
                host_signoff_ref=signoff,
            )
    ok = _build(bms_mode=mode, deviation_reason=reason, safety_case_ref=H1, host_signoff_ref=H2)
    assert ok.max_stage is ControlStage.L3_CHILLER_BAND


def test_a_safety_case_without_a_chiller_path_is_refused() -> None:
    with pytest.raises(ValidationError, match="not applicable"):
        _build(
            bms_mode="diode_read_only", deviation_reason=NA, safety_case_ref=H1, host_signoff_ref=NA
        )


def test_max_stage_by_mode() -> None:
    assert {m: max_stage_for(m) for m in BmsConduitMode} == {
        BmsConduitMode.NO_BMS_CONDUIT: ControlStage.L2_BESS_CLOSED_LOOP,
        BmsConduitMode.DIODE_READ_ONLY: ControlStage.L2_BESS_CLOSED_LOOP,
        BmsConduitMode.DIODE_WITH_BAND_OFFER: ControlStage.L3_CHILLER_BAND,
        BmsConduitMode.BROKERED_BIDIRECTIONAL: ControlStage.L3_CHILLER_BAND,
    }


def test_asks_are_unique_and_name_their_holder() -> None:
    ids = [a.ask_id for a in DESIGN_ASKS]
    assert len(ids) == len(set(ids)) == 12
    assert {a.holder for a in DESIGN_ASKS} == {
        "host",
        "dno",
        "bess_oem",
        "meter_operator",
        "route_to_market_partner",
        "thermaflex",
    }
