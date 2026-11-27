"""The twin's device side: a Neura-declared register map, and the commissioning set that tells a
gateway what each register is (WP-TWIN-01, D196).

**The map is Neura's own and SYNTHETIC.** No vendor's register map is reproduced: a real site's
maps come from its OEMs (a battery's SunSpec models, a meter's register list, a BMS's points) and
are a person-held ask. Every point here is one signed 64-bit integer in four big-endian registers,
in the IC-14 canonical unit, so a gateway needs no per-point scaling. Four Modbus unit ids stand in
for the devices of a site, in the edge zone-and-conduit design's terms
(`docs/design/edge_zone_conduit_design.v1.0.0.md`):

=====  ==============  =======  ====  ====================================================
unit   device          table    addr  point (IC-14 point key)
=====  ==============  =======  ====  ====================================================
1      battery (C3)    input    0     bess.bess-1.active_power.device_reported.whole
1                      input    4     bess.bess-1.state_of_energy.device_reported.whole
1                      input    8     bess.bess-1.state_of_charge.device_reported.whole
1                      holding  0     bess.bess-1.active_power.setpoint_echo.whole
                                      (write class ``bess_setpoint``)
1                      holding  4     site_boundary.main.active_power.setpoint_echo.whole
                                      (write class ``import_limit``)
2      site meter      input    0     site_boundary.main.active_power.operational_meter.whole
3      facility (RO)   input    0     it_load.hall-1.active_power.scada.whole
4      cooling (RO)    input    0     chiller.plant-1.active_power.device_reported.whole
4                      input    4     chiller.plant-1.temperature.device_reported.supply
4                      input    8     crah.hall-1.temperature.device_reported.supply
4                      input    12    chiller.plant-1.temperature.device_reported.ambient
=====  ==============  =======  ====  ====================================================

Unit 4 exists only when the twin has a thermal plant. Power follows IC-14's load convention
(positive into the asset: site import, battery charging).

The commissioning set labels every descriptor what it is: ``record_class: synthetic_fixture``,
source protocol ``simulated``, the register coordinates in the source address. The gateway copies
the descriptors verbatim; it never sees or decides the label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Literal

from gridq.contracts.telemetry_series import QUANTITY_TABLE, SeriesDescriptor, parse_point_key

__all__ = [
    "BESS_UNIT",
    "COOLING_UNIT",
    "FACILITY_UNIT",
    "METER_UNIT",
    "REGISTER_MAP",
    "RegisterPoint",
    "commissioning_document",
    "points_for",
]

BESS_UNIT: Final = 1
METER_UNIT: Final = 2
FACILITY_UNIT: Final = 3
COOLING_UNIT: Final = 4

#: Synthetic plausible bounds: wide enough for any twin this package builds, inside the quantity
#: ranges, chosen only to satisfy IC-14's invariants (no published source sets them).
_POWER_W: Final = (-1_000_000_000, 1_000_000_000)
_TEMP_MDEG: Final = (-60_000, 120_000)


@dataclass(frozen=True)
class RegisterPoint:
    unit_id: int
    table: Literal["input", "holding"]
    address: int
    point_key: str
    write_class: str | None
    ucum: str
    brick_class: str | None
    #: ``None``: the bound is the twin's own (state of energy: its usable energy).
    plausible: tuple[int, int] | None


REGISTER_MAP: Final[tuple[RegisterPoint, ...]] = (
    RegisterPoint(
        BESS_UNIT, "input", 0, "bess.bess-1.active_power.device_reported.whole",
        None, "W", "Active_Power_Sensor", _POWER_W,
    ),
    RegisterPoint(
        BESS_UNIT, "input", 4, "bess.bess-1.state_of_energy.device_reported.whole",
        None, "W.h", None, None,
    ),
    RegisterPoint(
        BESS_UNIT, "input", 8, "bess.bess-1.state_of_charge.device_reported.whole",
        None, "10*-4", None, (0, 10_000),
    ),
    RegisterPoint(
        BESS_UNIT, "holding", 0, "bess.bess-1.active_power.setpoint_echo.whole",
        "bess_setpoint", "W", None, _POWER_W,
    ),
    RegisterPoint(
        BESS_UNIT, "holding", 4, "site_boundary.main.active_power.setpoint_echo.whole",
        "import_limit", "W", None, _POWER_W,
    ),
    RegisterPoint(
        METER_UNIT, "input", 0, "site_boundary.main.active_power.operational_meter.whole",
        None, "W", "Active_Power_Sensor", _POWER_W,
    ),
    RegisterPoint(
        FACILITY_UNIT, "input", 0, "it_load.hall-1.active_power.scada.whole",
        None, "W", "Active_Power_Sensor", _POWER_W,
    ),
    RegisterPoint(
        COOLING_UNIT, "input", 0, "chiller.plant-1.active_power.device_reported.whole",
        None, "W", "Active_Power_Sensor", _POWER_W,
    ),
    RegisterPoint(
        COOLING_UNIT, "input", 4, "chiller.plant-1.temperature.device_reported.supply",
        None, "mCel", None, _TEMP_MDEG,
    ),
    RegisterPoint(
        COOLING_UNIT, "input", 8, "crah.hall-1.temperature.device_reported.supply",
        None, "mCel", None, _TEMP_MDEG,
    ),
    RegisterPoint(
        COOLING_UNIT, "input", 12, "chiller.plant-1.temperature.device_reported.ambient",
        None, "mCel", None, _TEMP_MDEG,
    ),
)  # fmt: skip


def points_for(thermal: bool) -> tuple[RegisterPoint, ...]:
    return tuple(p for p in REGISTER_MAP if thermal or p.unit_id != COOLING_UNIT)


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic twin: {reason}"}


def _descriptor(p: RegisterPoint, org_id: str, site_id: str, usable_wh: int) -> dict[str, Any]:
    name = parse_point_key(p.point_key)
    spec = QUANTITY_TABLE[name.quantity]
    lo, hi = p.plausible if p.plausible is not None else (0, usable_wh)
    metered = name.role.value in ("revenue_meter", "operational_meter")
    return {
        "contract": "IC-14",
        "contract_version": "1.0.0",
        "tenant": {
            "kind": "tenant_key",
            "org_id": org_id,
            "org_kind": "customer",
            "site_id": site_id,
        },
        "site_id": site_id,
        "series_id": f"{org_id}/{site_id}/{p.point_key}",
        "data_class": "customer_commercial",
        "record_class": "synthetic_fixture",
        "kind": "series_descriptor",
        "name": name.model_dump(mode="json"),
        "unit": spec.unit.value,
        "resolution": "operational_1s",
        "source": {
            "kind": "source_address",
            "protocol": "simulated",
            "address": f"modbus_tcp/unit={p.unit_id}/{p.table}/{p.address}/int64_be",
        },
        "source_scaling": {
            "kind": "source_scaling",
            "source_unit_ucum": p.ucum,
            "scale_num": 1,
            "scale_den": 1,
            "offset": 0,
        },
        "brick_class": p.brick_class or _na("no Brick class chosen for this point"),
        "plausible_min": lo,
        "plausible_max": hi,
        "meter": (
            {"kind": "unknown", "reason": "synthetic twin: no meter registry entry exists"}
            if metered
            else _na("not a metered role")
        ),
        "freshness": {
            "kind": "freshness_policy",
            "stale_after_ms": 120_000,
            "lost_after_ms": 600_000,
        },
        "max_batch_bytes": 65_536,
    }


def commissioning_document(
    *, org_id: str, site_id: str, sender_id: str, usable_wh: int, thermal: bool
) -> str:
    """The twin's commissioning set as JSON, every descriptor validated against IC-14."""
    if not (org_id.startswith("SYNTHETIC-") and site_id.startswith("SYNTHETIC-")):
        raise ValueError("the twin commissions SYNTHETIC- organisations and sites only")
    points = []
    for p in points_for(thermal):
        d = _descriptor(p, org_id, site_id, usable_wh)
        SeriesDescriptor.model_validate_json(json.dumps(d))
        points.append(
            {
                "descriptor": d,
                "wire": {
                    "unit_id": p.unit_id,
                    "table": p.table,
                    "address": p.address,
                    "words": 4,
                    "encoding": "int64_be",
                },
                "write_class": p.write_class,
            }
        )
    doc = {
        "kind": "southbound_commissioning",
        "site_id": site_id,
        "sender_id": sender_id,
        "points": points,
    }
    return json.dumps(doc, sort_keys=True, indent=1)
