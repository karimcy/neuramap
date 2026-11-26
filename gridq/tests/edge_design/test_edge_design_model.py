"""WP-EDGE-02 acceptance 1: every conduit lists its protocol, direction and security controls, and
a conduit missing any of them cannot be built."""

from __future__ import annotations

import json
import re

import pytest
from _edge_support import conduit, conduit_json
from pydantic import ValidationError

from gridq.edge_design.model import (
    CONTROLS,
    PROTOCOL_IS_IP,
    Conduit,
    ControlKind,
    WriteScope,
    Zone,
    covered_frs,
    required_frs,
)
from gridq.edge_design.public_sources import SR_CATALOGUE
from gridq.edge_design.release import DESIGN

CONDUITS = DESIGN.all_conduits()
IDS = [c.conduit_id for c in CONDUITS]
#: Protocol, the three parts of direction, and the controls.
REQUIRED = ("protocols", "initiator", "data_flow", "authority", "controls")


def test_the_design_has_every_conduit_of_every_mode() -> None:
    assert IDS == [
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C8",
        "C9",
        "C10",
        "C11",
        "C6",
        "C7",
        "C6o",
        "C7o",
        "C6w",
        "C7w",
    ]


@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize("cid", IDS)
def test_conduit_without_protocols_direction_or_controls_is_refused(cid: str, field: str) -> None:
    data = conduit_json(conduit(cid))
    assert Conduit.model_validate_json(json.dumps(data)) == conduit(cid)  # control: it builds
    del data[field]
    with pytest.raises(ValidationError, match=rf"{field}\n\s+Field required"):
        Conduit.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field", ["protocols", "controls", "payloads"])
@pytest.mark.parametrize("cid", IDS)
def test_an_empty_protocol_or_control_list_is_refused(cid: str, field: str) -> None:
    data = conduit_json(conduit(cid))
    data[field] = []
    with pytest.raises(ValidationError, match=rf"{field}\n\s+Tuple should have at least 1 item"):
        Conduit.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("cid", IDS)
def test_every_conduit_lists_protocol_direction_and_controls(cid: str) -> None:
    c = conduit(cid)
    assert c.protocols and c.controls
    assert c.initiator in ("a", "b") and c.data_flow in ("a_to_b", "b_to_a", "both")
    assert c.authority.kind in ("no_write", "write")


@pytest.mark.parametrize("cid", IDS)
def test_controls_cover_required_frs(cid: str) -> None:
    """The FRs a conduit's attributes demand are covered by its controls: FR 5 and 6 always,
    FR 1 and 3 over IP, FR 2 and 3 for a write, FR 1, 3, 4 and 7 across the site boundary."""
    c = conduit(cid)
    need = required_frs(c)
    assert {5, 6} <= need
    if c.is_ip:
        assert {1, 3} <= need
    if isinstance(c.authority, WriteScope):
        assert {2, 3} <= need
    assert need <= covered_frs(c)
    assert ControlKind.FLOW_LOGGING in c.controls  # FR 6 on every conduit in this design
    off_site = {"neura_cloud", "remote_ops", "market_partner"}
    if set(c.zones) & off_site:
        assert {ControlKind.MUTUAL_TLS, ControlKind.VPN_IPSEC} & set(c.controls)
        assert ControlKind.RATE_LIMIT in c.controls


def test_conduit_missing_an_fr_is_refused() -> None:
    data = conduit_json(conduit("C1"))
    data["controls"] = [k for k in data["controls"] if k not in ("rate_limit", "store_and_forward")]
    with pytest.raises(ValidationError, match=r"required FRs \[7\]"):
        Conduit.model_validate_json(json.dumps(data))


def test_controls_must_be_conduit_scoped_sorted_and_unique() -> None:
    data = conduit_json(conduit("C3"))
    for bad in (
        [*data["controls"], "local_fail_safe"],
        list(reversed(data["controls"])),
        [*data["controls"], data["controls"][0]],
    ):
        with pytest.raises(ValidationError):
            Conduit.model_validate_json(json.dumps({**data, "controls": bad}))


def test_a_loss_trigger_must_be_a_comms_loss_trigger() -> None:
    data = conduit_json(conduit("C1"))
    data["loss_trigger"] = "schedule_stale"
    with pytest.raises(ValidationError, match="comms-loss"):
        Conduit.model_validate_json(json.dumps(data))


def test_every_zone_keeps_an_asset_inventory() -> None:
    for z in DESIGN.zones:
        assert z.assets and ControlKind.ASSET_INVENTORY in z.controls, z.zone_id


def test_zone_rules_are_enforced() -> None:
    z = next(z for z in DESIGN.zones if z.zone_id == "edge_control")
    data = json.loads(z.model_dump_json())
    for update in (
        {"protocol_break": False},
        {"controls": [*data["controls"], "mutual_tls"]},
        {"level_top": "l1"},
        {"assets": []},
    ):
        with pytest.raises(ValidationError):
            Zone.model_validate_json(json.dumps({**data, **update}))


def test_control_table_is_complete_and_names_real_srs() -> None:
    srs = {e.sr_id for e in SR_CATALOGUE}
    used = {k for z in DESIGN.zones for k in z.controls} | {k for c in CONDUITS for k in c.controls}
    assert used == set(ControlKind)
    for k, spec in CONTROLS.items():
        assert set(spec.sr_ids) <= srs, k
        assert all(re.fullmatch(r"(ID|PR|DE|RS|RC)\.[A-Z]{2}-[0-9]+", t) for t in spec.csf), k


def test_only_hardwired_io_is_not_ip() -> None:
    assert [p for p, ip in PROTOCOL_IS_IP.items() if not ip] == ["hardwired_io"]
