"""IC-14 telemetry series: frozen contract, version 1.0.0 (roadmap WP-TEL-01, NEW-IC-14, spec B4).

Every Operate consumer reads site telemetry through this contract: dispatch and forecasting (B2),
the control room (B5), the ledger and baselines (B6), the twin (B7), the NESO performance files
(B3-04) and the independent verifier (B6-05). The edge gateway (B1) and the site twin produce it.
Spec §10A has no telemetry contract; the roadmap calls this one NEW-IC-14.

Spec → this contract:

=====================================  =======================================================
roadmap WP-TEL-01                      here
=====================================  =======================================================
point naming                           `PointName` (asset kind, asset id, quantity, measurement
                                       role, aspect), `point_key`, ``series_id`` =
                                       ``<org>/<site>/<point key>``
units                                  `QUANTITY_TABLE`: one canonical integer unit, sign and
                                       range per quantity; converters to IC-3/IC-5 kW, kWh and
                                       tenths of a degree with a stated rounding side
time bases                             `RESOLUTIONS` (20 Hz, 1 s, 1 min raw; 1 s, 1 min, 30 min
                                       curated), fixed UTC blocks of integer slots,
                                       `ClockStatement`, `settlement_period_of`, `age_at`
quality flags                          `ValueQuality` (on a value) and `GapReason` (on a gap),
                                       each mapped to one OPC UA status code (`QUALITY_MAP`)
raw and curated tiers                  `TelemetryBatch` (raw, immutable, hash-chained),
                                       `CuratedSeries` (a pure function of raw, recomputed by
                                       hash), `DqAnnotation` (a finding that cites raw)
tenant segregation key                 `TenantKey` on every record, `read_guard` over IC-13
=====================================  =======================================================

**A missing sample is a gap, never a zero.** A block of a series is a run-length tiling of its
slots by `ValueRun` (integers, one quality) and `GapRun` (a reason, and no value field at all).
Every slot is covered exactly once, so a sample cannot go missing silently, and a gap has no
number that could be read as zero. A curated interval is an `Aggregate` only when its usable
time reaches the method's ``min_usable_ms``; otherwise it is a `CuratedGap`. Nothing is
interpolated, held or scaled up across a gap, and a ``held`` value (OPC UA
UncertainLastUsableValue) is never usable. Fill and substitution are not in this version.

**Names and units.** A point's quantity fixes its unit, sign and range, so a unit cannot be
declared wrongly: watts, var, VA and watt-hours, mHz, mV, mA, basis points of state of charge
and thousandths of a degree Celsius, all integers. Power follows the load convention: positive
flows from the grid side into the named asset (site import, battery charging); export and
discharge are negative. Energy is two non-negative cumulative registers. A meter point and its
SCADA counterpart differ only in ``role`` (`counterparts`). Comparing with IC-3 or IC-5 converts
upward exactly (`kw_to_w`); a figure emitted in kW states its rounding side (`w_to_kw`).

**Time.** Raw resolutions are a closed table (50 ms, 1 s, 1 min); a block spans 60 s (20 Hz and
1 s) or 30 min (1 min), its ``origin`` is a whole multiple of the span on the UTC epoch, and slot
``i`` covers ``[origin + i·period, origin + (i+1)·period)``. Curated windows are one block at
1 s, 1 min or 30 min (a UTC day of 48 settlement half-hours, IC-3's grid). Intervals are
half-open and labelled by their start. The GB clock never appears on the wire;
`settlement_period_of` maps a UTC half-hour to its local settlement date and period (46, 48 or
50 a day). Each block states who stamped it and whether the clock was synchronised; a block
stamped on an unsynchronised clock keeps its values in raw, curates to ``clock_untrusted`` gaps
and reads as ``clock_untrusted`` in `age_at`.

**Tiers.** One raw batch is one block of one series, chained per (series, sender, stream epoch)
by ``prev_batch_hash`` from the IC-13 genesis value; a new stream epoch names the head of the one
before. Late delivery after a link outage (store and forward, B1-05) is normal: `track` accepts a
late fill and refuses only a fork or a changed descriptor inside an epoch. A curated record embeds
its whole `CurationMethod`, lists the raw runs it read (`RawSpan`, committed by their head
hashes) and the annotations it considered, so `verify_curated` recomputes it byte for byte. A
late batch, a changed method or a changed annotation set makes a new revision that supersedes the
old by hash (`check_supersession`).

**Tenancy.** Every record carries a `TenantKey` (the data-owning IC-13 organisation and the site)
and a ``data_class``; ``series_id`` is prefixed by the key, and the key is inside the content
hash. A data-returning reader needs a `ReadDecision` from `read_guard`, which is IC-13
`authorise` on a ``telemetry`` resource; a denied decision or a record of another tenant raises
`TenantReadRefused`, and a transform given two tenants raises `MixedTenants`.

**IC-5.** A raw batch is shaped to travel edge to cloud: ``kind`` is ``telemetry_batch``, it
carries ``site_id`` (IC-5's header rule) and it can name the IC-5 link profile and link epoch it
was sealed under (`Ic5Binding`). IC-5 1.0.0 has no telemetry body; carrying the batch inside an
IC-5 frame is an additive IC-5 minor owned by IC-5. Until then batches travel on their own
northbound channel. This module does not import IC-5 (that minor would import this one).

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every record); JSON Schemas frozen at
``schemas/ic14_series_descriptor.v1.0.0.schema.json``, ``ic14_telemetry_batch``,
``ic14_curated_series`` and ``ic14_dq_annotation``; human-readable specification generated at
``specs/ic14_telemetry_series.v1.0.0.md``; git tag ``GIT_TAG``. Reads IC-13 (`IdentityClaims`,
`ResourceRef`, `ScopeGrant`, `authorise`, the audit genesis hash) and IC-4 (`EvidenceRef`, the 1.x
range).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Any, Final, Literal, final
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    UtcInstant,
    content_hash,
    not_applicable,
)
from gridq.contracts.audit import GENESIS_PREV_HASH
from gridq.contracts.evidence import EvidenceRef
from gridq.contracts.identity import (
    Action,
    IdentityClaims,
    ResourceKind,
    ResourceRef,
    ScopeGrant,
    authorise,
)

__all__ = [
    "BLOCK_SPAN_MS",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "CURATED_RESOLUTIONS",
    "GIT_TAG",
    "MAX_ABS_VALUE",
    "PERIOD_MS",
    "QUALITY_MAP",
    "QUANTITY_TABLE",
    "RAW_RESOLUTIONS",
    "RESOLUTIONS",
    "SUPPORTED_VERSIONS",
    "TRIGGER_GAP_REASON",
    "Aggregate",
    "AgeReading",
    "AssetKind",
    "Aspect",
    "BatchRefused",
    "ChainBroken",
    "ClockStatement",
    "CuratedGap",
    "CuratedSeries",
    "CurationMethod",
    "DqAnnotation",
    "FindingKind",
    "FreshnessPolicy",
    "GapReason",
    "GapRun",
    "Ic5Binding",
    "MeasurementRole",
    "MeterBinding",
    "MixedTenants",
    "NotCertified",
    "PointName",
    "Quantity",
    "QuantitySpec",
    "QualityMapping",
    "RawSpan",
    "ReadDecision",
    "SeriesDescriptor",
    "SlotGap",
    "SlotValue",
    "SourceAddress",
    "SourceProtocol",
    "SourceScaling",
    "TelemetryBatch",
    "TenantKey",
    "TenantReadRefused",
    "TrackState",
    "TrackStatus",
    "TrackVerdict",
    "Unit",
    "ValueQuality",
    "ValueRun",
    "age_at",
    "batch_end",
    "check_batch",
    "check_batch_size",
    "check_supersession",
    "clock_drift_ms",
    "counterparts",
    "curate",
    "current_revision",
    "effective_annotations",
    "from_opcua",
    "kw_to_w",
    "kwh_to_wh",
    "millideg_to_tenths_c",
    "parse_point_key",
    "point_key",
    "read_guard",
    "require_certified",
    "resample",
    "resource_ref",
    "settlement_period_of",
    "slot_instant",
    "split_import_export_w",
    "tenths_c_to_millideg",
    "to_opcua",
    "track",
    "values_in",
    "verify_batch_chain",
    "verify_curated",
    "w_to_kw",
    "wh_to_kwh",
]

#: The roadmap's name is NEW-IC-14; the payload literal is "IC-14".
CONTRACT_ID: Final = "IC-14"
CONTRACT_VERSION: Final = "1.0.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0",)
GIT_TAG: Final = "contracts/ic14-telemetry-series/v1.0.0"

#: The largest magnitude of any value: an integer every JSON reader holds exactly (2^53 - 1).
MAX_ABS_VALUE: Final = 2**53 - 1

_LONDON: Final = ZoneInfo("Europe/London")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MS: Final = timedelta(milliseconds=1)
_SYNTHETIC: Final = "SYNTHETIC-"

_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", max_length=120)]
#: An IC-13 organisation or site id without ``/`` (the tenant separator).
_TENANT_PART = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$", max_length=80)]
_ASSET_ID = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
Ms = Annotated[int, Field(ge=0)]
PosMs = Annotated[int, Field(ge=1)]
Seq = Annotated[int, Field(ge=0)]
Value = Annotated[int, Field(ge=-MAX_ABS_VALUE, le=MAX_ABS_VALUE)]


def _ms_since_epoch(t: datetime) -> int:
    return (t - _EPOCH) // _MS


def _whole_ms(t: datetime) -> bool:
    return t.microsecond % 1000 == 0


# --------------------------------------------------------------------------------------------
# Point naming and units
# --------------------------------------------------------------------------------------------


class AssetKind(StrEnum):
    SITE_BOUNDARY = "site_boundary"
    BESS = "bess"
    PCS = "pcs"
    PV = "pv"
    GENSET = "genset"
    CHILLER = "chiller"
    CRAH = "crah"
    UPS = "ups"
    IT_LOAD = "it_load"


class Quantity(StrEnum):
    ACTIVE_POWER = "active_power"
    REACTIVE_POWER = "reactive_power"
    APPARENT_POWER = "apparent_power"
    ENERGY_IMPORT = "energy_import"
    ENERGY_EXPORT = "energy_export"
    FREQUENCY = "frequency"
    VOLTAGE = "voltage"
    CURRENT = "current"
    STATE_OF_ENERGY = "state_of_energy"
    STATE_OF_CHARGE = "state_of_charge"
    TEMPERATURE = "temperature"


class MeasurementRole(StrEnum):
    REVENUE_METER = "revenue_meter"
    OPERATIONAL_METER = "operational_meter"
    SCADA = "scada"
    DEVICE_REPORTED = "device_reported"
    SETPOINT_ECHO = "setpoint_echo"
    DERIVED = "derived"


class Aspect(StrEnum):
    WHOLE = "whole"
    PHASE_L1 = "phase_l1"
    PHASE_L2 = "phase_l2"
    PHASE_L3 = "phase_l3"
    SUPPLY = "supply"
    RETURN = "return"
    AMBIENT = "ambient"
    INTERNAL = "internal"


class Unit(StrEnum):
    WATT = "W"
    VAR = "var"
    VOLT_AMPERE = "VA"
    WATT_HOUR = "Wh"
    MILLIHERTZ = "mHz"
    MILLIVOLT = "mV"
    MILLIAMPERE = "mA"
    BASIS_POINT = "basis_point"
    MILLIDEGREE_CELSIUS = "millidegree_C"


@dataclass(frozen=True)
class QuantitySpec:
    """A quantity's one unit, its hard range and the aspects it may be measured on."""

    unit: Unit
    minimum: int
    maximum: int
    aspects: frozenset[Aspect]


_WHOLE = frozenset({Aspect.WHOLE})
_PHASES = frozenset({Aspect.PHASE_L1, Aspect.PHASE_L2, Aspect.PHASE_L3})
_TEMPS = frozenset({Aspect.SUPPLY, Aspect.RETURN, Aspect.AMBIENT, Aspect.INTERNAL})
_Q = Quantity

#: The frozen table. A new row, or a new member of any enum above, is a minor version; changing a
#: unit, range or aspect set is a major one.
QUANTITY_TABLE: Final[Mapping[Quantity, QuantitySpec]] = {
    _Q.ACTIVE_POWER: QuantitySpec(Unit.WATT, -MAX_ABS_VALUE, MAX_ABS_VALUE, _WHOLE | _PHASES),
    _Q.REACTIVE_POWER: QuantitySpec(Unit.VAR, -MAX_ABS_VALUE, MAX_ABS_VALUE, _WHOLE | _PHASES),
    _Q.APPARENT_POWER: QuantitySpec(Unit.VOLT_AMPERE, 0, MAX_ABS_VALUE, _WHOLE | _PHASES),
    _Q.ENERGY_IMPORT: QuantitySpec(Unit.WATT_HOUR, 0, MAX_ABS_VALUE, _WHOLE),
    _Q.ENERGY_EXPORT: QuantitySpec(Unit.WATT_HOUR, 0, MAX_ABS_VALUE, _WHOLE),
    _Q.FREQUENCY: QuantitySpec(Unit.MILLIHERTZ, 0, 100_000, _WHOLE),
    _Q.VOLTAGE: QuantitySpec(Unit.MILLIVOLT, 0, MAX_ABS_VALUE, _PHASES),
    _Q.CURRENT: QuantitySpec(Unit.MILLIAMPERE, 0, MAX_ABS_VALUE, _PHASES),
    _Q.STATE_OF_ENERGY: QuantitySpec(Unit.WATT_HOUR, 0, MAX_ABS_VALUE, _WHOLE),
    _Q.STATE_OF_CHARGE: QuantitySpec(Unit.BASIS_POINT, 0, 10_000, _WHOLE),
    _Q.TEMPERATURE: QuantitySpec(Unit.MILLIDEGREE_CELSIUS, -273_150, MAX_ABS_VALUE, _TEMPS),
}

#: Quantities a setpoint can never echo (registers and states, not commands).
_NOT_ECHOED: Final = frozenset(
    {_Q.ENERGY_IMPORT, _Q.ENERGY_EXPORT, _Q.STATE_OF_ENERGY, _Q.STATE_OF_CHARGE}
)
_METER_ROLES: Final = frozenset({MeasurementRole.REVENUE_METER, MeasurementRole.OPERATIONAL_METER})


def _point_name_schema(schema: dict[str, Any]) -> None:
    """The (quantity, aspect) table, stated in the JSON Schema so a gateway enforces it too."""
    schema["oneOf"] = [
        {
            "properties": {
                "quantity": {"const": q.value},
                "aspect": {"enum": sorted(a.value for a in spec.aspects)},
            }
        }
        for q, spec in QUANTITY_TABLE.items()
    ]


class PointName(ContractModel):
    """What a series measures: one quantity, on one asset, in one measurement role and aspect.
    Its canonical string is `point_key`; site and tenant are not part of it (they are on the
    record)."""

    model_config = ConfigDict(json_schema_extra=_point_name_schema)

    asset_kind: AssetKind
    asset_id: _ASSET_ID
    quantity: Quantity
    role: MeasurementRole
    aspect: Aspect

    @model_validator(mode="after")
    def _in_table(self) -> PointName:
        if self.aspect not in QUANTITY_TABLE[self.quantity].aspects:
            raise ValueError(f"{self.quantity.value} is not measured on aspect {self.aspect.value}")
        if self.role is MeasurementRole.SETPOINT_ECHO and self.quantity in _NOT_ECHOED:
            raise ValueError(f"a setpoint never echoes {self.quantity.value}")
        return self


def point_key(name: PointName) -> str:
    """The canonical string of a point: ``asset_kind.asset_id.quantity.role.aspect``."""
    return ".".join(
        (name.asset_kind.value, name.asset_id, name.quantity.value, name.role.value, name.aspect)
    )


def parse_point_key(key: str) -> PointName:
    """The `PointName` whose `point_key` is ``key``; raises `ValueError` on anything else."""
    parts = key.split(".")
    if len(parts) != 5:
        raise ValueError(f"a point key has five dot-separated parts, not {len(parts)}: {key!r}")
    kind, asset_id, quantity, role, aspect = parts
    return PointName.model_validate(
        {
            "asset_kind": kind,
            "asset_id": asset_id,
            "quantity": quantity,
            "role": role,
            "aspect": aspect,
        },
        strict=False,
    )


def counterparts(name: PointName, names: Iterable[PointName]) -> tuple[PointName, ...]:
    """The points of ``names`` that measure the same thing as ``name`` in another role (a revenue
    meter and its SCADA reading, say), in role order."""
    same = [
        n
        for n in names
        if n.role is not name.role
        and (n.asset_kind, n.asset_id, n.quantity, n.aspect)
        == (name.asset_kind, name.asset_id, name.quantity, name.aspect)
    ]
    order = list(MeasurementRole)
    return tuple(sorted(set(same), key=lambda n: order.index(n.role)))


def split_import_export_w(value_w: int) -> tuple[int, int]:
    """A signed active power (load convention) as IC-5's two non-negative terms, (import,
    export) in W: at most one is non-zero and import minus export is the input."""
    return (max(value_w, 0), max(-value_w, 0))


Side = Literal["floor", "ceil"]


def _divide(value: int, by: int, side: Side) -> int:
    return value // by if side == "floor" else -(-value // by)


def kw_to_w(kw: int) -> int:
    """IC-3/IC-5 kW in W, exactly."""
    return kw * 1000


def kwh_to_wh(kwh: int) -> int:
    """IC-3/IC-5 kWh in Wh, exactly."""
    return kwh * 1000


def tenths_c_to_millideg(tenths: int) -> int:
    """IC-3/IC-11 tenths of a degree in thousandths, exactly."""
    return tenths * 100


def w_to_kw(value_w: int, side: Side) -> int:
    """W as whole kW, rounded to the stated side (ceil against a cap, floor against a floor)."""
    return _divide(value_w, 1000, side)


def wh_to_kwh(value_wh: int, side: Side) -> int:
    """Wh as whole kWh, rounded to the stated side."""
    return _divide(value_wh, 1000, side)


def millideg_to_tenths_c(value: int, side: Side) -> int:
    """Thousandths of a degree as whole tenths, rounded to the stated side."""
    return _divide(value, 100, side)


# --------------------------------------------------------------------------------------------
# Quality flags
# --------------------------------------------------------------------------------------------


class ValueQuality(StrEnum):
    """The quality of a value. Declaration order is the worst-of order, best first."""

    GOOD = "good"
    LOCAL_OVERRIDE = "local_override"
    INACCURATE = "inaccurate"
    OUT_OF_RANGE = "out_of_range"
    SUBNORMAL = "subnormal"
    #: The source stopped updating and repeats its last value: never usable, never fresh.
    HELD = "held"


class GapReason(StrEnum):
    """Why there is no value. Every member is an OPC UA Bad status."""

    COMMS_LOST = "comms_lost"
    SENSOR_FAILURE = "sensor_failure"
    DEVICE_FAILURE = "device_failure"
    OUT_OF_SERVICE = "out_of_service"
    CONFIGURATION_ERROR = "configuration_error"
    CLOCK_UNTRUSTED = "clock_untrusted"
    BUFFER_LOST = "buffer_lost"
    NOT_RECORDED = "not_recorded"
    #: Curated only: the interval's usable time is below the method's threshold.
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


@dataclass(frozen=True)
class QualityMapping:
    """A member's OPC UA status code (name and value as the OPC Foundation's ``StatusCode.csv``
    states them) and its IEC 61850-7-3 reading (documentation only)."""

    opcua_name: str
    opcua_code: int
    iec61850: str


_VQ, _GR = ValueQuality, GapReason
#: Total over both enums, injective onto OPC UA. Codes checked against
#: https://raw.githubusercontent.com/OPCFoundation/UA-Nodeset/latest/Schema/StatusCode.csv.
QUALITY_MAP: Final[Mapping[ValueQuality | GapReason, QualityMapping]] = {
    _VQ.GOOD: QualityMapping("Good", 0x00000000, "validity good, source process"),
    _VQ.LOCAL_OVERRIDE: QualityMapping(
        "GoodLocalOverride", 0x00960000, "validity good, source substituted, operatorBlocked"
    ),
    _VQ.INACCURATE: QualityMapping(
        "UncertainSensorNotAccurate", 0x40930000, "validity questionable, inaccurate"
    ),
    _VQ.OUT_OF_RANGE: QualityMapping(
        "UncertainEngineeringUnitsExceeded", 0x40940000, "validity questionable, outOfRange"
    ),
    _VQ.SUBNORMAL: QualityMapping(
        "UncertainSubNormal", 0x40950000, "validity questionable, inconsistent"
    ),
    _VQ.HELD: QualityMapping(
        "UncertainLastUsableValue", 0x40900000, "validity questionable, oldData"
    ),
    _GR.COMMS_LOST: QualityMapping("BadNoCommunication", 0x80310000, "validity invalid, oldData"),
    _GR.SENSOR_FAILURE: QualityMapping("BadSensorFailure", 0x808C0000, "validity invalid, failure"),
    _GR.DEVICE_FAILURE: QualityMapping("BadDeviceFailure", 0x808B0000, "validity invalid, failure"),
    _GR.OUT_OF_SERVICE: QualityMapping(
        "BadOutOfService", 0x808D0000, "validity invalid, operatorBlocked"
    ),
    _GR.CONFIGURATION_ERROR: QualityMapping(
        "BadConfigurationError", 0x80890000, "validity invalid, badReference"
    ),
    _GR.CLOCK_UNTRUSTED: QualityMapping(
        "BadInvalidTimestamp", 0x80230000, "validity invalid, inconsistent"
    ),
    _GR.BUFFER_LOST: QualityMapping("BadDataLost", 0x809D0000, "validity invalid, overflow"),
    _GR.NOT_RECORDED: QualityMapping("BadNoData", 0x809B0000, "validity invalid"),
    _GR.INSUFFICIENT_COVERAGE: QualityMapping("BadDataUnavailable", 0x809E0000, "validity invalid"),
}
_BY_CODE: Final = {m.opcua_code: member for member, m in QUALITY_MAP.items()}


def to_opcua(member: ValueQuality | GapReason) -> int:
    """The OPC UA status code of a quality or gap reason."""
    return QUALITY_MAP[member].opcua_code


def from_opcua(code: int) -> ValueQuality | GapReason:
    """The member mapped to an OPC UA status code. An unlisted code raises: an adapter maps it
    explicitly or emits a ``configuration_error`` gap; there is no fallback by severity."""
    try:
        return _BY_CODE[code]
    except KeyError:
        raise ValueError(f"OPC UA status 0x{code:08X} has no IC-14 1.0.0 member") from None


#: IC-5 fail-safe trigger codes (by value; this module does not import IC-5) that mean a series
#: has no value while they are active, and the gap reason that records it. A lost cloud link is
#: absent: the edge buffers (B1-05) until the buffer overflows, which is ``buffer_lost``.
TRIGGER_GAP_REASON: Final[Mapping[str, GapReason]] = {
    "site_meter_lost": GapReason.COMMS_LOST,
    "power_device_comms_lost": GapReason.COMMS_LOST,
    "thermal_comms_lost": GapReason.COMMS_LOST,
    "clock_unsynchronised": GapReason.CLOCK_UNTRUSTED,
}


class FindingKind(StrEnum):
    """What a data-quality monitor found (WP-TEL-03)."""

    GAP = "gap"
    FLATLINE = "flatline"
    CLOCK_DRIFT = "clock_drift"
    UNIT_MISMATCH = "unit_mismatch"
    METER_SCADA_DIVERGENCE = "meter_scada_divergence"


# --------------------------------------------------------------------------------------------
# Time base
# --------------------------------------------------------------------------------------------

Resolution = Literal["event_20hz", "operational_1s", "standard_1min", "settlement_30min"]
RawResolution = Literal["event_20hz", "operational_1s", "standard_1min"]
CuratedResolution = Literal["operational_1s", "standard_1min", "settlement_30min"]

#: (period, block span) in ms. The periods nest (50 | 1,000 | 60,000 | 1,800,000), and every
#: raw block lies inside one block of every coarser curated resolution.
RESOLUTIONS: Final[Mapping[str, tuple[int, int]]] = {
    "event_20hz": (50, 60_000),
    "operational_1s": (1_000, 60_000),
    "standard_1min": (60_000, 1_800_000),
    "settlement_30min": (1_800_000, 86_400_000),
}
PERIOD_MS: Final[Mapping[str, int]] = {k: v[0] for k, v in RESOLUTIONS.items()}
BLOCK_SPAN_MS: Final[Mapping[str, int]] = {k: v[1] for k, v in RESOLUTIONS.items()}
RAW_RESOLUTIONS: Final = ("event_20hz", "operational_1s", "standard_1min")
CURATED_RESOLUTIONS: Final = ("operational_1s", "standard_1min", "settlement_30min")


def _slot_count(resolution: str) -> int:
    return BLOCK_SPAN_MS[resolution] // PERIOD_MS[resolution]


def _check_origin(origin: datetime, resolution: str) -> None:
    if not _whole_ms(origin):
        raise ValueError("origin must be a whole millisecond")
    if _ms_since_epoch(origin) % BLOCK_SPAN_MS[resolution]:
        raise ValueError(
            f"origin must be a whole multiple of the {resolution} block span "
            f"({BLOCK_SPAN_MS[resolution]} ms) from the UTC epoch"
        )


def settlement_period_of(start: datetime) -> tuple[date, int]:
    """The GB settlement date and period (1 to 46, 48 or 50) of the UTC half-hour starting at
    ``start``. UTC to local only: the period is counted from local midnight."""
    if not _whole_ms(start) or _ms_since_epoch(start) % PERIOD_MS["settlement_30min"]:
        raise ValueError("a settlement period starts on the UTC half-hour grid")
    local = start.astimezone(_LONDON)
    midnight = datetime(local.year, local.month, local.day, tzinfo=_LONDON).astimezone(UTC)
    return local.date(), (start - midnight) // timedelta(minutes=30) + 1


class ClockStatement(ContractModel):
    """Who stamped a block and how far the stamp can be trusted. ``mono_*`` are the edge's
    monotonic clock (IC-5 ``now_mono_ms``) at the block's first and last acquisition."""

    kind: Literal["clock_statement"]
    stamped_by: Literal["source_device", "edge_acquisition"]
    #: IC-5's literals (`FailSafeEvent.clock_state`).
    clock_state: Literal["synchronised", "unsynchronised"]
    clock_offset_bound_ms: Ms | Unknown
    boot_id: _ID
    mono_origin_ms: Ms
    mono_end_ms: Ms
    #: Edge wall clock minus the source's stamp at the first sample, signed (source stamps only).
    receipt_lag_ms: int | NotApplicable

    @model_validator(mode="after")
    def _consistent(self) -> ClockStatement:
        if self.clock_state == "synchronised" and not isinstance(self.clock_offset_bound_ms, int):
            raise ValueError("a synchronised clock states its offset bound")
        if self.mono_end_ms < self.mono_origin_ms:
            raise ValueError("mono_end_ms must not be before mono_origin_ms")
        if (self.stamped_by == "source_device") != isinstance(self.receipt_lag_ms, int):
            raise ValueError("receipt_lag_ms is stated exactly when the source stamped the block")
        return self


class FreshnessPolicy(ContractModel):
    """When a series reads stale and when lost (`age_at`). Required, with no default."""

    kind: Literal["freshness_policy"]
    stale_after_ms: PosMs
    lost_after_ms: PosMs


# --------------------------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------------------------


class TenantKey(ContractModel):
    """The data owner (an IC-13 organisation) and the site. Lender and verifier organisations
    never own telemetry; they read it through IC-13 scopes."""

    kind: Literal["tenant_key"]
    org_id: _TENANT_PART
    org_kind: Literal["customer", "neura"]
    site_id: _TENANT_PART

    def canonical(self) -> str:
        return f"{self.org_id}/{self.site_id}"


DataClass = Literal["neura_operational", "customer_commercial", "customer_personal"]
RecordClass = Literal["live", "synthetic_fixture"]


class _Head(ContractModel):
    """The head every IC-14 record carries, and the rules on it."""

    tenant: TenantKey
    site_id: _TENANT_PART
    series_id: NonEmptyStr
    data_class: DataClass
    record_class: RecordClass

    def point(self) -> PointName:
        return parse_point_key(self.series_id.removeprefix(self.tenant.canonical() + "/"))

    @model_validator(mode="after")
    def _head(self) -> _Head:
        if self.site_id != self.tenant.site_id:
            raise ValueError("site_id must equal tenant.site_id")
        prefix = self.tenant.canonical() + "/"
        if not self.series_id.startswith(prefix):
            raise ValueError(f"series_id must start with the tenant key {prefix!r}")
        self.point()
        synthetic = self.record_class == "synthetic_fixture"
        for label in (self.tenant.org_id, self.tenant.site_id):
            if label.startswith(_SYNTHETIC) != synthetic:
                raise ValueError(
                    "org_id and site_id start with SYNTHETIC- exactly when the record is a "
                    "synthetic fixture"
                )
        if (self.tenant.org_kind == "neura") != (self.data_class == "neura_operational"):
            raise ValueError("a Neura-owned series is neura_operational, and only it")
        return self


# --------------------------------------------------------------------------------------------
# The series descriptor
# --------------------------------------------------------------------------------------------


class SourceProtocol(StrEnum):
    MODBUS_TCP = "modbus_tcp"
    SUNSPEC = "sunspec"
    IEC61850_MMS = "iec61850_mms"
    DNP3 = "dnp3"
    IEC60870_5_104 = "iec60870_5_104"
    BACNET_IP = "bacnet_ip"
    SNMP_V3 = "snmp_v3"
    OPC_UA = "opc_ua"
    MQTT = "mqtt"
    SIMULATED = "simulated"


class SourceAddress(ContractModel):
    """Where the edge reads the point (provenance, never identity)."""

    kind: Literal["source_address"]
    protocol: SourceProtocol
    address: NonEmptyStr


class SourceScaling(ContractModel):
    """How the adapter turns the source's number into the canonical unit:
    ``raw * scale_num / scale_den + offset``, rounded half to even at the adapter."""

    kind: Literal["source_scaling"]
    source_unit_ucum: NonEmptyStr
    scale_num: Annotated[int, Field(ge=1)]
    scale_den: Annotated[int, Field(ge=1)]
    offset: int


class MeterBinding(ContractModel):
    """The meter behind a metered point and its certificate (IC-4), or ``unknown`` until the
    meter registry (WP-TEL-04) holds it."""

    kind: Literal["meter_binding"]
    meter_id: _ID
    certificate: EvidenceRef | Unknown = Field(discriminator="kind")


@final
class SeriesDescriptor(_Head):
    """IC-14 v1.0.0: one series, declared once and bound by hash into each of its batches and
    curated records. A changed descriptor starts a new stream epoch."""

    contract: Literal["IC-14"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["series_descriptor"]
    name: PointName
    unit: Unit
    resolution: RawResolution
    source: SourceAddress | NotApplicable = Field(discriminator="kind")
    source_scaling: SourceScaling | NotApplicable = Field(discriminator="kind")
    brick_class: NonEmptyStr | NotApplicable
    #: In the canonical unit. A good value outside them is refused (`check_batch`).
    plausible_min: Value
    plausible_max: Value
    meter: MeterBinding | Unknown | NotApplicable = Field(discriminator="kind")
    freshness: FreshnessPolicy
    max_batch_bytes: Annotated[int, Field(ge=1)]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SeriesDescriptor is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> SeriesDescriptor:
        if self.point() != self.name:
            raise ValueError("series_id must name this descriptor's point")
        spec = QUANTITY_TABLE[self.name.quantity]
        if self.unit is not spec.unit:
            raise ValueError(f"{self.name.quantity.value} is measured in {spec.unit.value}")
        if not spec.minimum <= self.plausible_min < self.plausible_max <= spec.maximum:
            raise ValueError("plausible_min < plausible_max, both inside the quantity's range")
        metered = self.name.role in _METER_ROLES
        if metered == isinstance(self.meter, NotApplicable):
            raise ValueError("a metered role names its meter (or unknown); no other role does")
        derived = self.name.role is MeasurementRole.DERIVED
        for part in (self.source, self.source_scaling):
            if derived != isinstance(part, NotApplicable):
                raise ValueError("a derived point has no source; every other point has one")
        if isinstance(self.source, SourceAddress) and (
            (self.source.protocol is SourceProtocol.SIMULATED)
            != (self.record_class == "synthetic_fixture")
        ):
            raise ValueError("a simulated source is exactly a synthetic fixture")
        span = BLOCK_SPAN_MS[self.resolution]
        if not span < self.freshness.stale_after_ms < self.freshness.lost_after_ms:
            raise ValueError(
                f"block span ({span} ms) < stale_after_ms < lost_after_ms: a healthy series "
                "delivers one block per span"
            )
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def require_certified(descriptor: SeriesDescriptor) -> EvidenceRef:
    """The meter certificate behind a series; raises `NotCertified` unless it is an IC-4
    reference (the registry resolves it and holds class and accuracy)."""
    meter = descriptor.meter
    if isinstance(meter, MeterBinding) and isinstance(meter.certificate, EvidenceRef):
        return meter.certificate
    raise NotCertified(f"{descriptor.series_id} has no meter certificate on record")


class NotCertified(ValueError):
    """The series has no IC-4 meter certificate."""


# --------------------------------------------------------------------------------------------
# The raw tier
# --------------------------------------------------------------------------------------------


class ValueRun(ContractModel):
    """Consecutive slots that hold values of one quality."""

    kind: Literal["values"]
    first_slot: Seq
    values: tuple[Value, ...] = Field(min_length=1)
    quality: ValueQuality

    @property
    def last_slot(self) -> int:
        return self.first_slot + len(self.values) - 1


class GapRun(ContractModel):
    """Consecutive slots with no value, and why. There is no value field to read as zero."""

    kind: Literal["gap"]
    first_slot: Seq
    last_slot: Seq
    reason: GapReason

    @model_validator(mode="after")
    def _ordered(self) -> GapRun:
        if self.last_slot < self.first_slot:
            raise ValueError("last_slot must not be before first_slot")
        return self


Segment = Annotated[ValueRun | GapRun, Field(discriminator="kind")]


def _check_tiling(segments: Sequence[ValueRun | GapRun], slot_count: int) -> None:
    nxt = 0
    for i, s in enumerate(segments):
        if s.first_slot != nxt:
            raise ValueError(f"segment {i} starts at slot {s.first_slot}, expected {nxt}")
        if i and _same_run(segments[i - 1], s):
            raise ValueError(f"segments {i - 1} and {i} are one run and must be merged")
        nxt = s.last_slot + 1
    if nxt != slot_count:
        raise ValueError(f"the segments end at slot {nxt - 1}, expected {slot_count - 1}")


def _same_run(a: ValueRun | GapRun, b: ValueRun | GapRun) -> bool:
    if isinstance(a, GapRun) and isinstance(b, GapRun):
        return a.reason is b.reason
    if isinstance(a, ValueRun) and isinstance(b, ValueRun):
        return a.quality is b.quality
    return False


class Ic5Binding(ContractModel):
    """The IC-5 link profile and link epoch a batch was sealed under."""

    kind: Literal["ic5_binding"]
    link_profile_hash: Sha256
    link_epoch: Seq


@final
class TelemetryBatch(_Head):
    """IC-14 v1.0.0: one block of one series, as the edge sealed it. Immutable; chained per
    (series, sender, stream epoch)."""

    contract: Literal["IC-14"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["telemetry_batch"]
    descriptor_hash: Sha256
    sender_id: _ID
    stream_epoch: Seq
    seq: Seq
    prev_batch_hash: Sha256
    #: The head of the previous stream epoch; ``unknown`` when the edge lost its chain state.
    prior_epoch_head: Sha256 | NotApplicable | Unknown
    origin: UtcInstant
    resolution: RawResolution
    slot_count: Annotated[int, Field(ge=1)]
    segments: tuple[Segment, ...] = Field(min_length=1)
    clock: ClockStatement
    sealed_at: UtcInstant
    ic5: Ic5Binding | NotApplicable = Field(discriminator="kind")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("TelemetryBatch is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> TelemetryBatch:
        _check_origin(self.origin, self.resolution)
        if self.slot_count != _slot_count(self.resolution):
            raise ValueError(f"a {self.resolution} block has {_slot_count(self.resolution)} slots")
        _check_tiling(self.segments, self.slot_count)
        spec = QUANTITY_TABLE[self.point().quantity]
        for s in self.segments:
            if isinstance(s, GapRun) and s.reason is GapReason.INSUFFICIENT_COVERAGE:
                raise ValueError("insufficient_coverage is a curated reason, not a raw one")
            if isinstance(s, ValueRun) and any(
                not spec.minimum <= v <= spec.maximum for v in s.values
            ):
                raise ValueError("a value is outside the quantity's range")
        if (self.seq == 0) != (self.prev_batch_hash == GENESIS_PREV_HASH):
            raise ValueError("prev_batch_hash is the genesis value exactly when seq == 0")
        if (self.stream_epoch == 0) != isinstance(self.prior_epoch_head, NotApplicable):
            raise ValueError("prior_epoch_head is not_applicable exactly in stream epoch 0")
        if self.sealed_at < batch_end(self):
            raise ValueError("a batch is sealed at or after the end of its block")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def batch_end(batch: TelemetryBatch | CuratedSeries) -> datetime:
    """The end of a block: ``origin`` plus its span (half-open)."""
    return batch.origin + BLOCK_SPAN_MS[batch.resolution] * _MS


def slot_instant(batch: TelemetryBatch | CuratedSeries, slot: int) -> datetime:
    """The start of slot ``slot`` of a block."""
    if not 0 <= slot < _slot_count(batch.resolution):
        raise ValueError(f"slot {slot} is outside the block")
    return batch.origin + slot * PERIOD_MS[batch.resolution] * _MS


class BatchRefused(ValueError):
    """A batch is not admissible for its descriptor. ``code`` is a stable reason."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def check_batch_size(raw: bytes, descriptor: SeriesDescriptor) -> bool:
    """True when a received batch's bytes are within the descriptor's ``max_batch_bytes``."""
    return len(raw) <= descriptor.max_batch_bytes


def check_batch(descriptor: SeriesDescriptor, batch: TelemetryBatch) -> None:
    """Refuse (`BatchRefused`) a batch that is not bound to ``descriptor`` by hash, differs from
    it in tenant, series, class or resolution, or holds a ``good`` value outside the plausible
    bounds (the producer flags such a value ``out_of_range``)."""
    if batch.descriptor_hash != descriptor.content_hash():
        raise BatchRefused("descriptor_mismatch", "descriptor_hash is not this descriptor's")
    for name in ("tenant", "site_id", "series_id", "data_class", "record_class", "resolution"):
        if getattr(batch, name) != getattr(descriptor, name):
            raise BatchRefused("head_mismatch", f"{name} differs from the descriptor")
    lo, hi = descriptor.plausible_min, descriptor.plausible_max
    for s in batch.segments:
        if isinstance(s, ValueRun) and s.quality is ValueQuality.GOOD:
            if any(not lo <= v <= hi for v in s.values):
                raise BatchRefused(
                    "implausible_good_value", "a good value lies outside the plausible bounds"
                )


class ChainBroken(ValueError):
    def __init__(self, seq: int, detail: str) -> None:
        super().__init__(f"batch chain broken at seq {seq}: {detail}")
        self.seq = seq


def verify_batch_chain(
    batches: Sequence[TelemetryBatch], *, prior_epoch_head: str | None = None
) -> str:
    """Check a complete, in-order run of one chain (series, sender, stream epoch) and return its
    head hash. A run starting after seq 0 needs no genesis; ``prior_epoch_head``, when given, must
    be the head the epoch names."""
    if not batches:
        raise ValueError("no batches to verify")
    first = batches[0]
    prev: TelemetryBatch | None = None
    for b in batches:
        key = (b.series_id, b.sender_id, b.stream_epoch, b.descriptor_hash, b.prior_epoch_head)
        if key != (
            first.series_id,
            first.sender_id,
            first.stream_epoch,
            first.descriptor_hash,
            first.prior_epoch_head,
        ):
            raise ChainBroken(b.seq, "another series, sender, epoch or descriptor in one chain")
        if prev is not None:
            if b.seq != prev.seq + 1:
                raise ChainBroken(b.seq, f"expected seq {prev.seq + 1}")
            if b.prev_batch_hash != prev.content_hash():
                raise ChainBroken(b.seq, "prev_batch_hash does not match the previous batch")
            if b.origin != batch_end(prev):
                raise ChainBroken(b.seq, "the block does not follow the previous one")
        prev = b
    if prior_epoch_head is not None and first.prior_epoch_head != prior_epoch_head:
        raise ChainBroken(first.seq, "prior_epoch_head is not the previous epoch's head")
    assert prev is not None
    return prev.content_hash()


def clock_drift_ms(prev: TelemetryBatch, batch: TelemetryBatch) -> int:
    """Wall-clock advance minus monotonic advance between two blocks of one edge boot, in ms
    (signed). WP-TEL-03 compares it with the IC-5 profile's ``max_clock_skew_ms``."""
    if prev.clock.boot_id != batch.clock.boot_id:
        raise ValueError("drift is measured within one edge boot")
    wall = _ms_since_epoch(batch.origin) - _ms_since_epoch(prev.origin)
    return wall - (batch.clock.mono_origin_ms - prev.clock.mono_origin_ms)


# --------------------------------------------------------------------------------------------
# Receipt: the tracker
# --------------------------------------------------------------------------------------------


class TrackStatus(StrEnum):
    IN_ORDER = "in_order"
    DUPLICATE = "duplicate"
    GAP = "gap"
    LATE_FILL = "late_fill"
    FORK = "fork"
    STALE_EPOCH = "stale_epoch"
    PROFILE_MISMATCH = "profile_mismatch"
    NEW_EPOCH = "new_epoch"


_REFUSED: Final = frozenset({TrackStatus.FORK, TrackStatus.PROFILE_MISMATCH})


@dataclass(frozen=True)
class TrackVerdict:
    status: TrackStatus
    accepted: bool


@dataclass(frozen=True)
class TrackState:
    """What a receiver holds for one series: per (sender, epoch), seq -> (hash, prev hash), the
    epoch's descriptor, and each sender's newest epoch. Immutable: `track` returns a new one."""

    series_id: str
    chains: Mapping[tuple[str, int], Mapping[int, tuple[str, str]]] = field(default_factory=dict)
    descriptors: Mapping[tuple[str, int], str] = field(default_factory=dict)
    newest_epoch: Mapping[str, int] = field(default_factory=dict)


def track(state: TrackState, batch: TelemetryBatch) -> tuple[TrackState, TrackVerdict]:
    """The receiver's verdict on an arriving batch, and the state after it. Late delivery is
    normal (store and forward); only a fork, or a changed descriptor inside an epoch, is
    refused, and a refused batch leaves the state unchanged."""
    if batch.series_id != state.series_id:
        raise ValueError("a batch of another series")
    chain = (batch.sender_id, batch.stream_epoch)
    held = state.chains.get(chain, {})
    h = batch.content_hash()
    newest = state.newest_epoch.get(batch.sender_id)
    known = state.descriptors.get(chain)
    if known is not None and known != batch.descriptor_hash:
        status = TrackStatus.PROFILE_MISMATCH
    elif batch.seq in held:
        status = TrackStatus.DUPLICATE if held[batch.seq][0] == h else TrackStatus.FORK
    elif (batch.seq - 1 in held and held[batch.seq - 1][0] != batch.prev_batch_hash) or (
        batch.seq + 1 in held and held[batch.seq + 1][1] != h
    ):
        status = TrackStatus.FORK
    elif newest is None or batch.stream_epoch > newest:
        status = TrackStatus.NEW_EPOCH
    elif batch.stream_epoch < newest:
        status = TrackStatus.STALE_EPOCH
    else:
        top = max(held) if held else -1
        if batch.seq == top + 1:
            status = TrackStatus.IN_ORDER
        elif batch.seq > top + 1:
            status = TrackStatus.GAP
        else:
            status = TrackStatus.LATE_FILL
    if status in _REFUSED or status is TrackStatus.DUPLICATE:
        return state, TrackVerdict(status, status not in _REFUSED)
    chains = {**state.chains, chain: {**held, batch.seq: (h, batch.prev_batch_hash)}}
    descriptors = {**state.descriptors, chain: batch.descriptor_hash}
    newest_epoch = {
        **state.newest_epoch,
        batch.sender_id: max(batch.stream_epoch, -1 if newest is None else newest),
    }
    return TrackState(state.series_id, chains, descriptors, newest_epoch), TrackVerdict(
        status, True
    )


# --------------------------------------------------------------------------------------------
# Annotations and raw spans
# --------------------------------------------------------------------------------------------


class RawSpan(ContractModel):
    """A contiguous run of one raw chain, committed by its first prev hash and its head hash."""

    kind: Literal["raw_span"]
    series_id: NonEmptyStr
    sender_id: _ID
    stream_epoch: Seq
    first_seq: Seq
    last_seq: Seq
    first_prev_hash: Sha256
    last_batch_hash: Sha256
    start: UtcInstant
    end: UtcInstant

    @model_validator(mode="after")
    def _ordered(self) -> RawSpan:
        if self.last_seq < self.first_seq or self.end <= self.start:
            raise ValueError("a raw span runs forward: first_seq <= last_seq and start < end")
        return self

    def sort_key(self) -> tuple[datetime, str, int]:
        return (self.start, self.sender_id, self.stream_epoch)


@final
class DqAnnotation(_Head):
    """IC-14 v1.0.0: a data-quality monitor's finding on a series over ``[start, end)``, citing
    the raw it read. Raw is never rewritten; a curated record counts a finding only through its
    method's ``applied_findings``. A withdrawal supersedes the annotation it withdraws."""

    contract: Literal["IC-14"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["dq_annotation"]
    annotation_id: _ID
    monitor_id: _ID
    monitor_version: NonEmptyStr
    detector_hash: Sha256
    finding: FindingKind
    disposition: Literal["asserted", "withdrawn"]
    start: UtcInstant
    end: UtcInstant
    evidence: tuple[RawSpan, ...] = Field(min_length=1)
    supersedes: Sha256 | NotApplicable
    issued_at: UtcInstant

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DqAnnotation is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> DqAnnotation:
        if self.end <= self.start:
            raise ValueError("an annotation covers a forward interval: start < end")
        if self.disposition == "withdrawn" and isinstance(self.supersedes, NotApplicable):
            raise ValueError("a withdrawal names the annotation it withdraws (supersedes)")
        keys = [s.sort_key() for s in self.evidence]
        if keys != sorted(keys):
            raise ValueError("evidence spans are sorted by (start, sender_id, stream_epoch)")
        if any(s.series_id != self.series_id for s in self.evidence):
            raise ValueError("evidence is raw of this series")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


def effective_annotations(annotations: Iterable[DqAnnotation]) -> tuple[DqAnnotation, ...]:
    """The asserted annotations that no other annotation of the set supersedes, sorted by hash."""
    items = {a.content_hash(): a for a in annotations}
    superseded = {a.supersedes for a in items.values() if isinstance(a.supersedes, str)}
    return tuple(
        items[h]
        for h in sorted(items)
        if h not in superseded and items[h].disposition == "asserted"
    )


# --------------------------------------------------------------------------------------------
# The curated tier
# --------------------------------------------------------------------------------------------


class CurationMethod(ContractModel):
    """How raw becomes one curated interval, stated in full (so the record recomputes from IC-14
    alone). ``method_id`` names the method across revisions; a changed method under the same id
    is a ``method_change``. Every number is required: no published source sets a threshold."""

    kind: Literal["curation_method"]
    method_id: _ID
    aggregation: Literal["mean", "min", "max", "last"]
    #: The least usable time, in ms, for an interval to hold a value; below it, a gap.
    min_usable_ms: PosMs
    #: Qualities that count as usable. Contains ``good``; never ``held``.
    usable: tuple[ValueQuality, ...] = Field(min_length=1)
    #: Finding kinds whose effective annotations exclude the time they cover. Never ``gap``.
    applied_findings: tuple[FindingKind, ...]
    #: For ``mean`` only: how the exact mean is rounded to an integer.
    rounding: Literal["floor", "ceil", "half_even"] | NotApplicable

    @model_validator(mode="after")
    def _consistent(self) -> CurationMethod:
        order = list(ValueQuality)
        if list(self.usable) != sorted(set(self.usable), key=order.index):
            raise ValueError("usable is in ValueQuality order, without repeats")
        if ValueQuality.GOOD not in self.usable:
            raise ValueError("usable must contain good")
        if ValueQuality.HELD in self.usable:
            raise ValueError("a held value is never usable")
        findings = list(FindingKind)
        if list(self.applied_findings) != sorted(set(self.applied_findings), key=findings.index):
            raise ValueError("applied_findings is in FindingKind order, without repeats")
        if FindingKind.GAP in self.applied_findings:
            raise ValueError("a gap finding is never applied: raw gaps are already gaps")
        if (self.aggregation == "mean") == isinstance(self.rounding, NotApplicable):
            raise ValueError("a mean states its rounding; no other aggregation does")
        return self


class Aggregate(ContractModel):
    """A curated interval with a value. ``usable_ms + rejected_ms + gap_ms`` is the period."""

    kind: Literal["aggregate"]
    slot: Seq
    value: Value
    #: The worst quality among the values used.
    quality: ValueQuality
    usable_ms: Ms
    rejected_ms: Ms
    gap_ms: Ms


class CuratedGap(ContractModel):
    """A curated interval without a value: too little usable time. ``reason`` is the single raw
    reason when the whole interval was one gap, else ``insufficient_coverage``."""

    kind: Literal["gap"]
    slot: Seq
    reason: GapReason
    usable_ms: Ms
    rejected_ms: Ms
    gap_ms: Ms


Interval = Annotated[Aggregate | CuratedGap, Field(discriminator="kind")]


@final
class CuratedSeries(_Head):
    """IC-14 v1.0.0: one block of a series at a curated resolution, computed from raw only by
    `curate`, and recomputable by `verify_curated`. Identified by (series, origin, resolution,
    method id); a later revision supersedes the earlier one by hash."""

    contract: Literal["IC-14"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["curated_series"]
    descriptor_hash: Sha256
    origin: UtcInstant
    resolution: CuratedResolution
    source_resolution: RawResolution
    method: CurationMethod
    inputs: tuple[RawSpan, ...]
    annotations: tuple[Sha256, ...]
    intervals: tuple[Interval, ...] = Field(min_length=1)
    revision: Seq
    supersedes: Sha256 | NotApplicable
    supersede_reason: Literal["late_raw", "annotation_change", "method_change"] | NotApplicable

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("CuratedSeries is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> CuratedSeries:
        period, source = PERIOD_MS[self.resolution], PERIOD_MS[self.source_resolution]
        if not (period > source and period % source == 0):
            raise ValueError("a curated period is a strictly coarser multiple of the raw period")
        _check_origin(self.origin, self.resolution)
        if [i.slot for i in self.intervals] != list(range(_slot_count(self.resolution))):
            raise ValueError("intervals are the block's slots, in order, each once")
        if not self.method.min_usable_ms <= period:
            raise ValueError("min_usable_ms must not exceed the curated period")
        for i in self.intervals:
            if i.usable_ms + i.rejected_ms + i.gap_ms != period:
                raise ValueError(
                    f"interval {i.slot}: usable + rejected + gap must equal the period"
                )
            if isinstance(i, Aggregate) and i.usable_ms < self.method.min_usable_ms:
                raise ValueError(f"interval {i.slot}: a value needs min_usable_ms of usable time")
            if isinstance(i, CuratedGap) and i.usable_ms >= self.method.min_usable_ms:
                raise ValueError(f"interval {i.slot}: enough usable time is a value, not a gap")
        keys = [s.sort_key() for s in self.inputs]
        if keys != sorted(keys):
            raise ValueError("inputs are sorted by (start, sender_id, stream_epoch)")
        end = self.origin + BLOCK_SPAN_MS[self.resolution] * _MS
        for a, b in zip(self.inputs, self.inputs[1:], strict=False):
            if b.start < a.end:
                raise ValueError("input spans overlap")
        for s in self.inputs:
            if s.series_id != self.series_id or s.start < self.origin or s.end > end:
                raise ValueError("an input is raw of this series inside the window")
        if list(self.annotations) != sorted(set(self.annotations)):
            raise ValueError("annotations are sorted and unique")
        if (self.revision == 0) != isinstance(self.supersedes, NotApplicable) or (
            self.revision == 0
        ) != isinstance(self.supersede_reason, NotApplicable):
            raise ValueError("revision 0 exactly when nothing is superseded, for no reason")
        return self

    def content_hash(self) -> str:
        return content_hash(self)

    def key(self) -> tuple[str, datetime, str, str]:
        return (self.series_id, self.origin, self.resolution, self.method.method_id)


class MixedTenants(ValueError):
    """A transform was given records of more than one tenant or series."""


class TenantReadRefused(PermissionError):
    """A read without an allowed decision for the records' tenant."""


def _one_series(records: Sequence[_Head], series_id: str | None = None) -> None:
    tenants = {r.tenant for r in records}
    if len(tenants) > 1:
        raise MixedTenants("records of more than one tenant in one transform")
    series = {r.series_id for r in records} | ({series_id} if series_id else set())
    if len(series) > 1:
        raise MixedTenants("records of more than one series in one transform")


def _round(num: int, den: int, rounding: str) -> int:
    if rounding == "floor":
        return num // den
    if rounding == "ceil":
        return -(-num // den)
    return round(Fraction(num, den))  # Fraction rounds half to even


@dataclass
class _Tally:
    usable: list[tuple[int, ValueQuality]] = field(default_factory=list)
    usable_ms: int = 0
    rejected_ms: int = 0
    gap_reasons: dict[GapReason, int] = field(default_factory=dict)

    def gap(self, reason: GapReason, ms: int) -> None:
        self.gap_reasons[reason] = self.gap_reasons.get(reason, 0) + ms


def resample(
    batches: Sequence[TelemetryBatch],
    annotations: Sequence[DqAnnotation],
    method: CurationMethod,
    origin: datetime,
    resolution: CuratedResolution,
) -> tuple[Aggregate | CuratedGap, ...]:
    """The curated intervals of the block at ``origin``: pure, raw input only. A raw slot no
    batch covers is ``not_recorded``; a slot of a block stamped on an unsynchronised clock is
    ``clock_untrusted``; a value of an unusable quality, or under an applied annotation, is
    rejected. An interval holds a value only with ``min_usable_ms`` of usable time."""
    if any(not isinstance(b, TelemetryBatch) for b in batches):
        raise TypeError("curated data derives from raw batches only")
    _one_series([*batches, *annotations])
    if len({b.resolution for b in batches}) > 1:
        raise ValueError("raw batches of one resolution")
    period = PERIOD_MS[resolution]
    _check_origin(origin, resolution)
    window_end = origin + BLOCK_SPAN_MS[resolution] * _MS
    applied = [
        a for a in effective_annotations(annotations) if a.finding in method.applied_findings
    ]
    n = _slot_count(resolution)
    tallies = [_Tally() for _ in range(n)]
    covered: dict[int, int] = {}  # raw slot start (epoch ms) -> covered once
    # With no batch at all, every interval is wholly not_recorded and no raw period is read.
    raw_period = PERIOD_MS[batches[0].resolution] if batches else 1
    if batches and (period <= raw_period or period % raw_period):
        raise ValueError("a curated period is a strictly coarser multiple of the raw period")
    base = _ms_since_epoch(origin)
    for b in batches:
        if b.origin < origin or batch_end(b) > window_end:
            continue
        b0 = _ms_since_epoch(b.origin)
        untrusted = b.clock.clock_state == "unsynchronised"
        for s in b.segments:
            for k in range(s.first_slot, s.last_slot + 1):
                t = b0 + k * raw_period
                if t in covered:
                    raise ValueError("two batches cover one raw slot")
                covered[t] = 1
                tally = tallies[(t - base) // period]
                if untrusted:
                    tally.gap(GapReason.CLOCK_UNTRUSTED, raw_period)
                elif isinstance(s, GapRun):
                    tally.gap(s.reason, raw_period)
                else:
                    v = s.values[k - s.first_slot]
                    at = _EPOCH + t * _MS
                    hit = any(a.start < at + raw_period * _MS and at < a.end for a in applied)
                    if s.quality in method.usable and not hit:
                        tally.usable.append((v, s.quality))
                        tally.usable_ms += raw_period
                    else:
                        tally.rejected_ms += raw_period
    out: list[Aggregate | CuratedGap] = []
    order = list(ValueQuality)
    for i, tally in enumerate(tallies):
        gap_ms = period - tally.usable_ms - tally.rejected_ms
        missing = gap_ms - sum(tally.gap_reasons.values())
        if missing:
            tally.gap(GapReason.NOT_RECORDED, missing)
        if tally.usable_ms >= method.min_usable_ms:
            values = [v for v, _ in tally.usable]
            if method.aggregation == "mean":
                assert isinstance(method.rounding, str)
                value = _round(sum(values), len(values), method.rounding)
            elif method.aggregation == "min":
                value = min(values)
            elif method.aggregation == "max":
                value = max(values)
            else:
                value = values[-1]
            worst = max((q for _, q in tally.usable), key=order.index)
            out.append(
                Aggregate(
                    kind="aggregate",
                    slot=i,
                    value=value,
                    quality=worst,
                    usable_ms=tally.usable_ms,
                    rejected_ms=tally.rejected_ms,
                    gap_ms=gap_ms,
                )
            )
        else:
            whole = gap_ms == period and len(tally.gap_reasons) == 1
            reason = next(iter(tally.gap_reasons)) if whole else GapReason.INSUFFICIENT_COVERAGE
            out.append(
                CuratedGap(
                    kind="gap",
                    slot=i,
                    reason=reason,
                    usable_ms=tally.usable_ms,
                    rejected_ms=tally.rejected_ms,
                    gap_ms=gap_ms,
                )
            )
    return tuple(out)


def _spans(batches: Sequence[TelemetryBatch]) -> tuple[RawSpan, ...]:
    """The batches as contiguous chain runs (one per sender and epoch, split where seq or time
    jumps), sorted."""
    ordered = sorted(batches, key=lambda b: (b.sender_id, b.stream_epoch, b.seq))
    runs: list[list[TelemetryBatch]] = []
    for b in ordered:
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and (last.sender_id, last.stream_epoch) == (b.sender_id, b.stream_epoch)
            and b.seq == last.seq + 1
            and b.prev_batch_hash == last.content_hash()
            and b.origin == batch_end(last)
        ):
            runs[-1].append(b)
        else:
            runs.append([b])
    spans = [
        RawSpan(
            kind="raw_span",
            series_id=r[0].series_id,
            sender_id=r[0].sender_id,
            stream_epoch=r[0].stream_epoch,
            first_seq=r[0].seq,
            last_seq=r[-1].seq,
            first_prev_hash=r[0].prev_batch_hash,
            last_batch_hash=r[-1].content_hash(),
            start=r[0].origin,
            end=batch_end(r[-1]),
        )
        for r in runs
    ]
    return tuple(sorted(spans, key=RawSpan.sort_key))


def curate(
    descriptor: SeriesDescriptor,
    batches: Sequence[TelemetryBatch],
    annotations: Sequence[DqAnnotation],
    method: CurationMethod,
    origin: datetime,
    resolution: CuratedResolution,
    prior: CuratedSeries | NotApplicable,
) -> CuratedSeries:
    """The curated record of the block at ``origin``, from the raw batches inside it and the
    annotations considered. With a ``prior`` revision, the result supersedes it, for the reason
    that differs: the method, else the annotation set, else the raw read (``late_raw``)."""
    _one_series([descriptor, *batches, *annotations])
    for b in batches:
        check_batch(descriptor, b)
    window_end = origin + BLOCK_SPAN_MS[resolution] * _MS
    inside = [b for b in batches if origin <= b.origin and batch_end(b) <= window_end]
    intervals = resample(inside, annotations, method, origin, resolution)
    inputs = _spans(inside)
    ann = tuple(sorted({a.content_hash() for a in annotations}))
    revision: dict[str, Any] = {
        "revision": 0,
        "supersedes": not_applicable("first revision"),
        "supersede_reason": not_applicable("first revision"),
    }
    if isinstance(prior, CuratedSeries):
        if prior.key() != (descriptor.series_id, origin, resolution, method.method_id):
            raise ValueError("the prior revision is of another series, block or method")
        if content_hash(prior.method) != content_hash(method):
            reason = "method_change"
        elif prior.annotations != ann:
            reason = "annotation_change"
        elif prior.inputs != inputs:
            reason = "late_raw"
        else:
            raise ValueError("nothing changed: the prior revision stands")
        revision = {
            "revision": prior.revision + 1,
            "supersedes": prior.content_hash(),
            "supersede_reason": reason,
        }
    return CuratedSeries(
        kind="curated_series",
        tenant=descriptor.tenant,
        site_id=descriptor.site_id,
        series_id=descriptor.series_id,
        data_class=descriptor.data_class,
        record_class=descriptor.record_class,
        descriptor_hash=descriptor.content_hash(),
        origin=origin,
        resolution=resolution,
        source_resolution=descriptor.resolution,
        method=method,
        inputs=inputs,
        annotations=ann,
        intervals=intervals,
        **revision,
    )


def verify_curated(
    record: CuratedSeries,
    descriptor: SeriesDescriptor,
    batches: Sequence[TelemetryBatch],
    annotations: Sequence[DqAnnotation],
) -> None:
    """Recompute ``record`` from raw and refuse (`ValueError`) any difference: the descriptor,
    the raw runs it names (each span's chain links and end hashes), the annotation set, and
    the record's own content hash."""
    if record.descriptor_hash != descriptor.content_hash():
        raise ValueError("the record is not bound to this descriptor")
    window_end = batch_end(record)
    inside = [b for b in batches if record.origin <= b.origin and batch_end(b) <= window_end]
    if _spans(inside) != record.inputs:
        raise ValueError("the raw batches are not the runs the record names")
    if tuple(sorted({a.content_hash() for a in annotations})) != record.annotations:
        raise ValueError("the annotations are not the set the record considered")
    rebuilt = record.model_copy(
        update={
            "intervals": resample(
                inside, annotations, record.method, record.origin, record.resolution
            )
        }
    )
    if CuratedSeries.model_validate_json(rebuilt.model_dump_json()).content_hash() != (
        record.content_hash()
    ):
        raise ValueError("the record does not recompute from its raw")


def _covered_blocks(record: CuratedSeries) -> frozenset[datetime]:
    span = BLOCK_SPAN_MS[record.source_resolution] * _MS
    out: set[datetime] = set()
    for s in record.inputs:
        t = s.start
        while t < s.end:
            out.add(t)
            t += span
    return frozenset(out)


def check_supersession(old: CuratedSeries, new: CuratedSeries) -> None:
    """Refuse (`ValueError`) a revision that does not follow ``old``: another key, a revision
    other than the next, another superseded hash, or a reason the change does not bear out
    (``late_raw`` reads strictly more raw under the same method and annotations;
    ``method_change`` changes the method; ``annotation_change`` changes the annotation set)."""
    if new.key() != old.key():
        raise ValueError("a revision keeps the series, block, resolution and method id")
    if new.revision != old.revision + 1 or new.supersedes != old.content_hash():
        raise ValueError("a revision is the next one and supersedes its predecessor's hash")
    same_method = content_hash(new.method) == content_hash(old.method)
    if new.supersede_reason == "late_raw":
        if not (same_method and new.annotations == old.annotations):
            raise ValueError("late_raw keeps the method and the annotation set")
        if not _covered_blocks(new) > _covered_blocks(old):
            raise ValueError("late_raw reads strictly more raw than its predecessor")
    elif new.supersede_reason == "method_change":
        if same_method:
            raise ValueError("method_change changes the method")
    elif new.supersede_reason == "annotation_change":
        if new.annotations == old.annotations:
            raise ValueError("annotation_change changes the annotation set")


def current_revision(records: Sequence[CuratedSeries]) -> CuratedSeries:
    """The newest revision of one curated block; refuses a fork (two records superseding one
    hash), a second first revision, or records of another key."""
    if not records:
        raise ValueError("no records")
    if len({r.key() for r in records}) > 1:
        raise ValueError("records of more than one curated block")
    roots = [r for r in records if isinstance(r.supersedes, NotApplicable)]
    by_parent: dict[str, list[CuratedSeries]] = {}
    for r in records:
        if isinstance(r.supersedes, str):
            by_parent.setdefault(r.supersedes, []).append(r)
    if len(roots) != 1:
        raise ValueError("one first revision per curated block")
    if any(len(v) > 1 for v in by_parent.values()):
        raise ValueError("a fork: two revisions supersede one record")
    head = roots[0]
    while head.content_hash() in by_parent:
        head = by_parent[head.content_hash()][0]
    return head


# --------------------------------------------------------------------------------------------
# Reading: tenancy, values and data age
# --------------------------------------------------------------------------------------------


def resource_ref(tenant: TenantKey) -> ResourceRef:
    """The IC-13 resource a read of this tenant's telemetry targets (and an audit event names)."""
    return ResourceRef(
        kind=ResourceKind.TELEMETRY, org_id=tenant.org_id, resource_id=tenant.site_id
    )


class ReadDecision(ContractModel):
    """The outcome of IC-13 authorisation for a read of one tenant's telemetry. Allowed exactly
    when ``scope`` is a grant that covers a read of ``target``; ``target`` goes straight into an
    IC-13 audit event, whether allowed or denied."""

    kind: Literal["read_decision"]
    tenant: TenantKey
    target: ResourceRef
    outcome: Literal["allowed", "denied"]
    scope: ScopeGrant | NotApplicable

    @model_validator(mode="after")
    def _consistent(self) -> ReadDecision:
        if self.target != resource_ref(self.tenant):
            raise ValueError("target must be the tenant's telemetry resource")
        granted = isinstance(self.scope, ScopeGrant) and self.scope.covers(Action.READ, self.target)
        if (self.outcome == "allowed") != granted:
            raise ValueError("allowed exactly when the scope covers a read of the target")
        return self


#: IC-13 `audit_access`'s wording for a denied scope, so a decision and its audit event agree.
_NO_SCOPE: Final = not_applicable("no scope in the token permits this action on this target")


def read_guard(claims: IdentityClaims, tenant: TenantKey) -> ReadDecision:
    """IC-13 `authorise` for a read of ``tenant``'s telemetry by validated ``claims``. Never
    raises on denial: a denied read is audited too."""
    target = resource_ref(tenant)
    grant = authorise(claims, Action.READ, target)
    return ReadDecision(
        kind="read_decision",
        tenant=tenant,
        target=target,
        outcome="denied" if grant is None else "allowed",
        scope=_NO_SCOPE if grant is None else grant,
    )


def _admit(decision: ReadDecision, records: Iterable[_Head]) -> None:
    if decision.outcome != "allowed":
        raise TenantReadRefused(f"read of {decision.tenant.canonical()} denied")
    for r in records:
        if r.tenant != decision.tenant:
            raise TenantReadRefused(
                f"a record of {r.tenant.canonical()} under a decision for "
                f"{decision.tenant.canonical()}"
            )


class SlotValue(ContractModel):
    kind: Literal["value"]
    start: UtcInstant
    value: Value
    quality: ValueQuality
    clock_trusted: bool


class SlotGap(ContractModel):
    kind: Literal["gap"]
    start: UtcInstant
    end: UtcInstant
    reason: GapReason


def values_in(
    decision: ReadDecision, batches: Sequence[TelemetryBatch]
) -> tuple[SlotValue | SlotGap, ...]:
    """The slots of ``batches`` in time order: each value with its quality and whether its clock
    was trusted, and each gap as one interval with its reason. Raises `TenantReadRefused` unless
    the decision allows a read of every batch's tenant."""
    _admit(decision, batches)
    out: list[SlotValue | SlotGap] = []
    for b in sorted(batches, key=lambda b: (b.origin, b.sender_id, b.stream_epoch)):
        p = PERIOD_MS[b.resolution] * _MS
        trusted = b.clock.clock_state == "synchronised"
        for s in b.segments:
            start = b.origin + s.first_slot * p
            if isinstance(s, GapRun):
                end = b.origin + (s.last_slot + 1) * p
                out.append(SlotGap(kind="gap", start=start, end=end, reason=s.reason))
            else:
                out.extend(
                    SlotValue(
                        kind="value",
                        start=start + k * p,
                        value=v,
                        quality=s.quality,
                        clock_trusted=trusted,
                    )
                    for k, v in enumerate(s.values)
                )
    return tuple(out)


AgeState = Literal["fresh", "stale", "lost", "future_dated", "clock_untrusted", "no_data"]


class AgeReading(ContractModel):
    """How old a series' newest usable value is at ``as_of``. ``value`` and ``quality`` are given
    exactly when the reading is ``fresh``: a stale series shows its age, never its last value."""

    kind: Literal["age_reading"]
    as_of: UtcInstant
    state: AgeState
    age_ms: int | NotApplicable
    last_usable_at: UtcInstant | NotApplicable
    value: Value | NotApplicable
    quality: ValueQuality | NotApplicable

    @model_validator(mode="after")
    def _consistent(self) -> AgeReading:
        fresh = self.state == "fresh"
        if fresh != isinstance(self.value, int) or fresh != isinstance(self.quality, str):
            raise ValueError("a value and quality are given exactly when fresh")
        return self


def age_at(
    decision: ReadDecision,
    descriptor: SeriesDescriptor,
    batches: Sequence[TelemetryBatch],
    at: datetime,
    max_clock_skew_ms: int,
) -> AgeReading:
    """The data age of a series at ``at``, against the descriptor's freshness policy. Age runs
    from the start of the newest slot holding a usable value (any quality but ``held``); at a
    threshold the worse state holds. A newest block stamped on an unsynchronised clock reads
    ``clock_untrusted``; a value dated after ``at`` by more than the clock skew reads
    ``future_dated``."""
    _admit(decision, [descriptor, *batches])
    _one_series([descriptor, *batches])
    na = not_applicable
    blank: dict[str, Any] = {
        "kind": "age_reading",
        "as_of": at,
        "value": na("not fresh"),
        "quality": na("not fresh"),
    }
    if not batches:
        return AgeReading(
            state="no_data", age_ms=na("no data"), last_usable_at=na("no data"), **blank
        )
    newest = max(batches, key=lambda b: (b.origin, b.sealed_at))
    if newest.clock.clock_state == "unsynchronised":
        return AgeReading(
            state="clock_untrusted",
            age_ms=na("the newest block's clock was unsynchronised"),
            last_usable_at=na("the newest block's clock was unsynchronised"),
            **blank,
        )
    best: tuple[datetime, int, ValueQuality] | None = None
    for b in batches:
        if b.clock.clock_state != "synchronised":
            continue
        p = PERIOD_MS[b.resolution] * _MS
        for s in b.segments:
            if isinstance(s, ValueRun) and s.quality is not ValueQuality.HELD:
                t = b.origin + s.last_slot * p
                if best is None or t > best[0]:
                    best = (t, s.values[-1], s.quality)
    if best is None:
        return AgeReading(
            state="no_data",
            age_ms=na("no usable value"),
            last_usable_at=na("no usable value"),
            **blank,
        )
    t, value, quality = best
    age = (at - t) // _MS
    if age < 0:
        if -age > max_clock_skew_ms:
            return AgeReading(state="future_dated", age_ms=age, last_usable_at=t, **blank)
        age = 0
    policy = descriptor.freshness
    if age >= policy.lost_after_ms:
        state: AgeState = "lost"
    elif age >= policy.stale_after_ms:
        state = "stale"
    else:
        return AgeReading(
            kind="age_reading",
            as_of=at,
            state="fresh",
            age_ms=age,
            last_usable_at=t,
            value=value,
            quality=quality,
        )
    return AgeReading(state=state, age_ms=age, last_usable_at=t, **blank)
