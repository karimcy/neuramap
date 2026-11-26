"""IC-11 thermal flexibility: frozen contract, version 1.1.0 (roadmap WP-THERM-02, NEW-IC-11).

Spec A4-03 and TF-04 cross four workstreams (WS-THERM produces; WS-SITE, WS-SIZE, WS-DISP and
WS-ATL consume) and spec §10A has no contract for them. This module freezes three payloads:

1. `ThermalFlexAvailability`: what one site's cooling plant can shed, as **kW deliverable for a
   duration in minutes, by season and local time-of-day band** (A4-03), with the **rebound** after
   each release, the **IT-safety limits** that bound it (A4-02, TF-02) and the **evidence class**
   (modelled, trialled or metered). Producer: A4 ``thermal_plant_model`` (WP-THERM-03) at design
   time, ThermaFlex DC in operation. Consumers: A3 adequacy simulator (WP-SITE-04), constraint
   library and IC-3 (WP-SIZE-02/07), B2 forecasts and joint dispatch (WP-DISP-01/03), Atlas scene
   (WP-ATL-07).
2. `FlexInstruction`: B2 asks ThermaFlex for a reduction (TF-04): id, kW, start, duration, issuer.
3. `FlexAcknowledgement`: ThermaFlex's answer, bound to the instruction by hash: accepted,
   rejected with a reason code, or partially accepted with a kW.

Spec → this contract:

======================================  ======================================================
spec                                    field
======================================  ======================================================
A4-03 "MW x minutes"                    ``cells[*].availability.curve[*]`` (`FlexPoint`)
A4-03 "by time of day and season"       ``seasons``, ``bands``, ``cells`` (`AvailabilityCell`)
A4-02 / TF-04 "rebound"                 ``curve[*].rebound`` (`Rebound`)
A4-02 ASHRAE class or SLA, tighter      ``limits`` (`ThermalLimits`): declared, never computed
A4-03 "modelled → trialled → metered"   ``evidence_class`` + ``evidence`` (IC-4 refs)
TF-04 "accepts a flex instruction"      `FlexInstruction`, `FlexAcknowledgement`
======================================  ======================================================

**A declared kW is firm** (owner, 2026-11-23). For each curve point, ``reduction_kw`` is
deliverable for that point's ``duration_min`` at any event start inside the cell, within the
binding IT-safety band (``limits.binding``). It is not a typical or expected value: sizing and
dispatch may rely on it as stated.

**Evidence class is a floor.** ``evidence_class`` is the floor for every ``flex`` and
``zero_flex`` cell: each one is supported by evidence of at least that class. A cell without
evidence of that grade (for a trialled or metered surface, a month or band the observations did
not cover) is ``unknown``, never served at the surface's class. The model does not check this,
because the basis carries one observation window, not per-cell coverage.

**Zero flex is not "unavailable".** A cell is one of four explicit, non-null kinds:
`Flex` (a curve of positive kW), `ZeroFlex` (assessed, and the answer is 0 kW), `Unavailable`
(not offered, with a reason code) or `Unknown` (not assessed, with a reason). A zero cannot hide
inside a `Flex` curve (every point is at least 1 kW), so each state has exactly one form.

**Exact integers, no floats.** Power is integer **kW** (1 MW = 1000 kW), energy integer **kWh**,
durations integer **minutes**, temperatures integer **tenths of a degree Celsius**, humidity
integer per cent. Reasons, as for IC-2's rational weights: zero must be exactly zero (acceptance
2: a float 1e-12 is neither zero nor flex); "instructed kW <= deliverable kW" and "accepted kW <
instructed kW" must give one answer for every consumer; and an integer has one JSON form, so the
hash never depends on how a producer spelled a number.

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every instance); JSON Schemas frozen at
``schemas/ic11_thermal_flex_availability.v1.1.0.schema.json``,
``schemas/ic11_flex_instruction.v1.1.0.schema.json`` and
``schemas/ic11_flex_acknowledgement.v1.1.0.schema.json``; git tag ``GIT_TAG`` (create on
commit). Depends on IC-4 v1.1.0 (`EvidenceRef`, `IssuerRole`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Final, Literal, final
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    UtcInstant,
    Version1x,
    content_hash,
    not_applicable,
)
from gridq.contracts.evidence import V1_0_0_ISSUER_ROLES, EvidenceRef, IssuerRole

__all__ = [
    "CLOCK",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "EVIDENCE_RANK",
    "GIT_TAG",
    "MAX_DURATION_MIN",
    "SUPPORTED_VERSIONS",
    "AckCheck",
    "Accepted",
    "AshraeClass",
    "AshraeLimit",
    "AvailabilityCell",
    "AvailabilityRef",
    "AvailabilityStatus",
    "CoolingTopology",
    "CustomerSlaLimit",
    "EvidenceClass",
    "Flex",
    "FlexAcknowledgement",
    "FlexAnswer",
    "FlexInstruction",
    "FlexPoint",
    "HumidityLimit",
    "InstructionCheck",
    "InstructionIssuer",
    "LimitSource",
    "MeasurementPoint",
    "MeteredBasis",
    "ModelledBasis",
    "PartiallyAccepted",
    "Rebound",
    "Rejected",
    "ResponseReason",
    "ResponseTerms",
    "Season",
    "TemperatureBand",
    "ThermalFlexAvailability",
    "ThermalLimits",
    "TimeBand",
    "TrialledBasis",
    "Unavailable",
    "UnavailableReason",
    "ZeroFlex",
    "accepted_kw",
    "availability_ref",
    "check_instruction",
    "deliverable_kw",
    "kw_to_mw",
    "query",
    "query_at",
    "verify_acknowledgement",
]

CONTRACT_ID: Final = "IC-11"
CONTRACT_VERSION: Final = "1.1.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0", "1.1.0")
GIT_TAG: Final = "contracts/ic11-thermal-flex/v1.1.0"

#: The clock every time-of-day band is read on (GB local time; the tariff and DUoS convention).
CLOCK: Final = "Europe/London"
#: A day. The surface is indexed by a time of day, so a hold longer than a day is out of scope.
MAX_DURATION_MIN: Final = 1440
_MINUTES_PER_DAY: Final = 1440

Kw = Annotated[int, Field(ge=0)]
PosKw = Annotated[int, Field(ge=1)]
Minutes = Annotated[int, Field(ge=0)]
Duration = Annotated[int, Field(ge=1, le=MAX_DURATION_MIN)]
#: Tenths of a degree Celsius: 18.0 °C is ``180``. Bounded to a physically sane range.
TenthsC = Annotated[int, Field(ge=-500, le=1000)]
_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
#: An upper-snake revenue-stream identifier, as IC-4 ``StreamScope.stream``.
_STREAM = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]


# --------------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------------


class CoolingTopology(StrEnum):
    """Spec A4-01 cooling topologies."""

    AIR_COOLED_CHILLERS = "air_cooled_chillers"
    WATER_COOLED_CHILLERS_WITH_TOWERS = "water_cooled_chillers_with_towers"
    FREE_COOLING = "free_cooling"
    CRAH_CRAC = "crah_crac"
    REAR_DOOR = "rear_door"
    DIRECT_LIQUID_COOLING = "direct_liquid_cooling"


class EvidenceClass(StrEnum):
    """Spec A4-03 "modelled → trialled → metered". `EVIDENCE_RANK` orders them, weakest first.

    There is no synthetic member: synthetic fixtures say so in ``record_class`` and still carry
    a real evidence class, so consumers can exercise every class against fixtures.
    """

    MODELLED = "modelled"
    TRIALLED = "trialled"
    METERED = "metered"


EVIDENCE_RANK: Final[tuple[EvidenceClass, ...]] = (
    EvidenceClass.MODELLED,
    EvidenceClass.TRIALLED,
    EvidenceClass.METERED,
)


class AshraeClass(StrEnum):
    """ASHRAE TC9.9 data-processing environment classes (spec A4-02 names A1 and H1)."""

    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    H1 = "H1"


class MeasurementPoint(StrEnum):
    """Where a temperature limit is measured. ASHRAE classes are defined at the IT inlet."""

    IT_INLET = "it_inlet"
    SUPPLY_AIR = "supply_air"


class LimitSource(StrEnum):
    """Which declared limit the binding band was taken from (A4-02: "whichever is tighter").
    Checked by `ThermalLimits`: ``ashrae`` means the binding band equals the ASHRAE band,
    ``customer_sla`` means it equals the SLA band, and ``operator_margin`` means it lies inside
    both (inside the ASHRAE band alone when no SLA is known)."""

    ASHRAE = "ashrae"
    CUSTOMER_SLA = "customer_sla"
    #: Inside the ASHRAE band and a known SLA band, by an operator's stated margin.
    OPERATOR_MARGIN = "operator_margin"


class UnavailableReason(StrEnum):
    """Why a cell is not offered at all (as distinct from offered at 0 kW)."""

    CONTROL_AUTHORITY_NOT_GRANTED = "control_authority_not_granted"
    OUTSIDE_AGREED_WINDOW = "outside_agreed_window"
    HOST_WITHDRAWN = "host_withdrawn"
    MAINTENANCE = "maintenance"
    PLANT_FAULT = "plant_fault"


class AvailabilityStatus(StrEnum):
    """The four cell kinds, as a consumer reads them (`FlexAnswer.status`)."""

    FLEX = "flex"
    ZERO_FLEX = "zero_flex"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class InstructionIssuer(StrEnum):
    """Who issued a flex instruction to ThermaFlex.

    IC-4 `IssuerRole` deliberately has no Neura member (Neura cannot issue *evidence*), but the
    normal issuer of a flex instruction is Neura's B2 dispatch (TF-04). So the issuer has its own
    enum, and the upstream party the instruction serves is an IC-4 `IssuerRole`
    (``FlexInstruction.on_behalf_of``).
    """

    NEURA_DISPATCH = "neura_dispatch"
    NEURA_OPERATOR = "neura_operator"


class ResponseReason(StrEnum):
    """Why an instruction was rejected or only partly accepted. `check_instruction` produces the
    first six from the contract alone, reading the whole event ``[start, end)``; the rest are
    live conditions only the plant knows."""

    #: The instruction names another site, or another surface by hash.
    WRONG_SITE_OR_SURFACE = "wrong_site_or_surface"
    #: The event ``[start, end)`` is not inside ``[valid_from, valid_until)``.
    OUTSIDE_VALIDITY_WINDOW = "outside_validity_window"
    #: The event touches an ``unknown`` cell anywhere in ``[start, end)``.
    AVAILABILITY_UNKNOWN = "availability_unknown"
    #: The event touches an ``unavailable`` cell anywhere in ``[start, end)``.
    AVAILABILITY_UNAVAILABLE = "availability_unavailable"
    #: The kW is above the start cell's curve for the duration (``zero_flex`` declares 0).
    EXCEEDS_DECLARED_AVAILABILITY = "exceeds_declared_availability"
    #: Less notice than ``response.min_notice_min``.
    INSUFFICIENT_NOTICE = "insufficient_notice"
    IT_SAFETY_LIMIT = "it_safety_limit"
    REBOUND_RECOVERY_IN_PROGRESS = "rebound_recovery_in_progress"
    CONTROL_AUTHORITY_NOT_GRANTED = "control_authority_not_granted"
    HOST_OVERRIDE = "host_override"
    PLANT_FAULT = "plant_fault"
    TELEMETRY_LOSS = "telemetry_loss"


# --------------------------------------------------------------------------------------------
# The availability surface: seasons x time-of-day bands x duration
# --------------------------------------------------------------------------------------------


class Season(ContractModel):
    """A set of calendar months. A surface's seasons partition the twelve months."""

    season_id: _ID
    months: tuple[Annotated[int, Field(ge=1, le=12)], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ascending(self) -> Season:
        if list(self.months) != sorted(set(self.months)):
            raise ValueError("months must be strictly ascending with no repeats")
        return self


class TimeBand(ContractModel):
    """A half-open local-clock window ``[start_minute, end_minute)`` on `CLOCK`.

    Minutes after local midnight; ``end_minute == 1440`` is midnight at the end of the day. A
    band never wraps midnight. A cell's kW is indexed by the **start** of a flex event: "from
    00:15 the site can hold X kW for Y minutes", which is how A4-03 states it. Authority and
    assessment are not: `check_instruction` refuses an event that touches an ``unavailable`` or
    ``unknown`` cell anywhere in ``[start, end)``.
    """

    band_id: _ID
    start_minute: Annotated[int, Field(ge=0, le=_MINUTES_PER_DAY - 1)]
    end_minute: Annotated[int, Field(ge=1, le=_MINUTES_PER_DAY)]

    @model_validator(mode="after")
    def _ordered(self) -> TimeBand:
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be after start_minute (bands do not wrap midnight)")
        return self


class Rebound(ContractModel):
    """Extra cooling demand after a hold is released, above the counterfactual baseline.

    ``energy_kwh`` over the recovery, ``peak_kw`` at its highest, ``recovery_min`` until demand is
    back on baseline. All zero declares no rebound. Energy cannot exceed peak x recovery time
    (rounded up to the kWh), and a rebound with energy has a peak and a duration.
    """

    energy_kwh: Annotated[int, Field(ge=0)]
    peak_kw: Kw
    recovery_min: Minutes

    @model_validator(mode="after")
    def _consistent(self) -> Rebound:
        if (self.peak_kw == 0) != (self.recovery_min == 0):
            raise ValueError("a rebound with a peak has a recovery time, and only then")
        if self.energy_kwh * 60 > self.peak_kw * self.recovery_min + 59:
            raise ValueError("rebound energy_kwh exceeds peak_kw x recovery_min")
        return self


class FlexPoint(ContractModel):
    """``reduction_kw`` can be held for ``duration_min`` minutes, then releases ``rebound``.

    Firm: the kW is deliverable for ``duration_min`` minutes at any event start inside the cell,
    within the binding IT-safety band. It is not a typical or expected value.

    A point is always positive: a zero is a `ZeroFlex` cell, never a point (one form per state).
    """

    duration_min: Duration
    reduction_kw: PosKw
    rebound: Rebound


class Flex(ContractModel):
    """A cell with flex: a duration-capability curve, longest hold last.

    Durations strictly increase and kW strictly decrease (a longer hold cannot deliver more, and
    a point that does not lower the kW is redundant), so a curve has one encoding. Read it with
    `deliverable_kw`: for a requested duration the deliverable kW is that of the first point at
    least as long, and 0 beyond the longest point.
    """

    kind: Literal["flex"]
    curve: tuple[FlexPoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _frontier(self) -> Flex:
        for a, b in zip(self.curve, self.curve[1:], strict=False):
            if b.duration_min <= a.duration_min:
                raise ValueError("curve durations must be strictly increasing")
            if b.reduction_kw >= a.reduction_kw:
                raise ValueError(
                    "curve kW must strictly decrease with duration (a longer hold cannot deliver "
                    "more, and an equal point is redundant)"
                )
        return self


class ZeroFlex(ContractModel):
    """Assessed, offered, and the answer is 0 kW (e.g. chillers at full load on a summer peak).

    Distinct from `Unavailable` (not offered) and `Unknown` (not assessed): a zero-flex cell is a
    finding with the surface's evidence class behind it, like a flex cell. A cell without
    evidence of that class is `Unknown`.
    """

    kind: Literal["zero_flex"]
    reason: NonEmptyStr


class Unavailable(ContractModel):
    """Not offered in this cell at all, for a coded reason. No instruction may start in it or
    run into it (`check_instruction` reads the whole event)."""

    kind: Literal["unavailable"]
    reason_code: UnavailableReason
    detail: NonEmptyStr


class AvailabilityCell(ContractModel):
    season_id: _ID
    band_id: _ID
    availability: Flex | ZeroFlex | Unavailable | Unknown = Field(discriminator="kind")


# --------------------------------------------------------------------------------------------
# IT-safety limits: declared, never computed
# --------------------------------------------------------------------------------------------


class TemperatureBand(ContractModel):
    point: MeasurementPoint
    min_tenths_c: TenthsC
    max_tenths_c: TenthsC

    @model_validator(mode="after")
    def _ordered(self) -> TemperatureBand:
        if self.max_tenths_c <= self.min_tenths_c:
            raise ValueError("max_tenths_c must be above min_tenths_c")
        return self

    def within(self, outer: TemperatureBand) -> bool:
        """True when this band is inside ``outer`` (both at the same measurement point)."""
        return outer.min_tenths_c <= self.min_tenths_c and self.max_tenths_c <= outer.max_tenths_c


class HumidityLimit(ContractModel):
    kind: Literal["humidity_limit"]
    min_dew_point_tenths_c: TenthsC
    max_dew_point_tenths_c: TenthsC
    max_relative_humidity_percent: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def _ordered(self) -> HumidityLimit:
        if self.max_dew_point_tenths_c <= self.min_dew_point_tenths_c:
            raise ValueError("max dew point must be above min dew point")
        return self


class AshraeLimit(ContractModel):
    """The ASHRAE envelope as **declared** by the producer. The contract holds no ASHRAE table
    and derives nothing from ``ashrae_class``: the numbers are stated, with their source."""

    ashrae_class: AshraeClass
    envelope: Literal["recommended", "allowable"]
    temperature: TemperatureBand
    humidity: HumidityLimit | Unknown = Field(discriminator="kind")
    max_rate_of_change_tenths_c_per_hour: Annotated[int, Field(ge=1)] | Unknown
    #: The edition and table the numbers were transcribed from.
    source: NonEmptyStr


class CustomerSlaLimit(ContractModel):
    kind: Literal["customer_sla"]
    temperature: TemperatureBand
    humidity: HumidityLimit | Unknown = Field(discriminator="kind")
    #: The SLA document as an IC-4 evidence object, once registered.
    evidence: EvidenceRef | Unknown = Field(discriminator="kind")


class ThermalLimits(ContractModel):
    """Spec A4-02 / TF-02: the IT-safety limits the availability is bounded by.

    ``binding`` is the band ThermaFlex enforces as a hard constraint. It is declared, not
    computed. Checked:

    - the binding band, and a known SLA band, are measured at the ASHRAE band's point, so the
      nesting is never skipped (an SLA stated at another point is translated by the producer);
    - the binding band lies inside the ASHRAE band, and inside a known SLA band;
    - ``binding_source`` matches: ``ashrae`` is the ASHRAE band exactly, ``customer_sla`` is the
      SLA band exactly, ``operator_margin`` is any band inside both.

    Temperature only: which humidity limit is enforced when ASHRAE and the SLA differ is not
    stated in v1.
    """

    ashrae: AshraeLimit
    customer_sla: CustomerSlaLimit | NotApplicable | Unknown = Field(discriminator="kind")
    binding: TemperatureBand
    binding_source: LimitSource
    chiller_min_run_min: Minutes | Unknown
    chiller_min_off_min: Minutes | Unknown

    @model_validator(mode="after")
    def _binding_is_tightest(self) -> ThermalLimits:
        binding, outer = self.binding, self.ashrae.temperature
        if binding.point is not outer.point:
            raise ValueError(
                "the binding band must be measured at the ASHRAE band's point "
                f"({outer.point.value}), not {binding.point.value}"
            )
        if not binding.within(outer):
            raise ValueError("the binding band is wider than the declared ASHRAE band")
        sla = self.customer_sla
        if isinstance(sla, CustomerSlaLimit):
            if sla.temperature.point is not outer.point:
                raise ValueError(
                    "a customer SLA band must be measured at the ASHRAE band's point "
                    f"({outer.point.value}), not {sla.temperature.point.value}"
                )
            if not binding.within(sla.temperature):
                raise ValueError("the binding band is wider than the declared customer SLA band")
        if self.binding_source is LimitSource.ASHRAE and binding != outer:
            raise ValueError("binding_source is ashrae but the binding band is not the ASHRAE band")
        if self.binding_source is LimitSource.CUSTOMER_SLA:
            if not isinstance(sla, CustomerSlaLimit):
                raise ValueError("binding_source is customer_sla but no customer SLA is declared")
            if binding != sla.temperature:
                raise ValueError(
                    "binding_source is customer_sla but the binding band is not the SLA band"
                )
        return self


# --------------------------------------------------------------------------------------------
# Evidence basis (reuses IC-4 EvidenceRef)
# --------------------------------------------------------------------------------------------


def _sorted_unique_refs(refs: tuple[EvidenceRef, ...], what: str) -> None:
    hashes = [r.evidence_hash for r in refs]
    if hashes != sorted(set(hashes)):
        raise ValueError(f"{what} must be sorted by evidence_hash with no repeats")


class ModelledBasis(ContractModel):
    """Produced by a plant model (A4-01/WP-THERM-03), identified by its run inputs."""

    kind: Literal["modelled"]
    model: NonEmptyStr
    model_version: NonEmptyStr
    input_hash: Sha256
    #: IC-4 evidence the model was calibrated on (BMS history, design data). May be empty.
    calibration_evidence: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def _canonical(self) -> ModelledBasis:
        _sorted_unique_refs(self.calibration_evidence, "calibration_evidence")
        return self


class TrialledBasis(ContractModel):
    """Observed in a bounded trial of flex events against a baseline method fixed in advance
    (A4-04). Requires IC-4 evidence: the trial records and the registered baseline method."""

    kind: Literal["trialled"]
    trial_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    baseline_method: EvidenceRef
    observed_from: UtcInstant
    observed_until: UtcInstant
    n_events: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _canonical(self) -> TrialledBasis:
        _sorted_unique_refs(self.trial_evidence, "trial_evidence")
        if self.observed_until <= self.observed_from:
            raise ValueError("observed_until must be after observed_from")
        return self


class MeteredBasis(ContractModel):
    """Measured on commissioned metering across real events against a baseline method fixed in
    advance (A4-04). Requires IC-4 evidence: the meter records and the baseline method."""

    kind: Literal["metered"]
    meter_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    baseline_method: EvidenceRef
    observed_from: UtcInstant
    observed_until: UtcInstant
    n_events: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _canonical(self) -> MeteredBasis:
        _sorted_unique_refs(self.meter_evidence, "meter_evidence")
        if self.observed_until <= self.observed_from:
            raise ValueError("observed_until must be after observed_from")
        return self


_BASIS_FOR: Final = {
    EvidenceClass.MODELLED: "modelled",
    EvidenceClass.TRIALLED: "trialled",
    EvidenceClass.METERED: "metered",
}


def _surface_refs(
    limits: ThermalLimits, evidence: ModelledBasis | TrialledBasis | MeteredBasis
) -> list[tuple[str, EvidenceRef]]:
    """Every IC-4 reference a surface embeds, with where it sits."""
    out: list[tuple[str, EvidenceRef]] = []
    sla = limits.customer_sla
    if isinstance(sla, CustomerSlaLimit) and isinstance(sla.evidence, EvidenceRef):
        out.append(("limits.customer_sla.evidence", sla.evidence))
    if isinstance(evidence, ModelledBasis):
        out.extend(("evidence.calibration_evidence", r) for r in evidence.calibration_evidence)
    elif isinstance(evidence, TrialledBasis):
        out.extend(("evidence.trial_evidence", r) for r in evidence.trial_evidence)
        out.append(("evidence.baseline_method", evidence.baseline_method))
    else:
        out.extend(("evidence.meter_evidence", r) for r in evidence.meter_evidence)
        out.append(("evidence.baseline_method", evidence.baseline_method))
    return out


class ResponseTerms(ContractModel):
    """How fast the declared reduction arrives: notice needed before ``start``, and minutes from
    ``start`` to the full reduction. ``duration_min`` counts from ``start``."""

    kind: Literal["response_terms"]
    min_notice_min: Minutes
    ramp_min: Minutes


# --------------------------------------------------------------------------------------------
# The availability contract
# --------------------------------------------------------------------------------------------


@final
class ThermalFlexAvailability(ContractModel):
    """IC-11 v1.1.0 availability surface for one site's cooling plant.

    Grid: ``seasons`` partition the twelve months; ``bands`` partition the local day; ``cells``
    hold exactly one entry per (season, band), in season order then band order. Each cell is
    `Flex`, `ZeroFlex`, `Unavailable` or `Unknown`, never null. Power kW, energy kWh, time in
    minutes, temperatures in tenths of a degree: integers throughout.

    Every curve kW is firm: deliverable for its duration at any event start inside the cell,
    within the binding IT-safety band. ``evidence_class`` is the floor for every ``flex`` and
    ``zero_flex`` cell; a cell without evidence of that class is ``unknown``.
    """

    contract: Literal["IC-11"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION

    surface_id: _ID
    site_id: NonEmptyStr
    #: ``synthetic_fixture`` marks schema-test data (site id starts ``SYNTHETIC-``). Consumers
    #: must refuse it outside tests.
    record_class: Literal["registered", "synthetic_fixture"]
    topology: tuple[CoolingTopology, ...] = Field(min_length=1)
    #: Electrical demand of the cooling plant at full load; no reduction may exceed it.
    cooling_electrical_capacity_kw: PosKw | Unknown
    issued_at: UtcInstant
    valid_from: UtcInstant
    #: Exclusive.
    valid_until: UtcInstant
    clock: Literal["Europe/London"]

    evidence_class: EvidenceClass
    evidence: ModelledBasis | TrialledBasis | MeteredBasis = Field(discriminator="kind")

    limits: ThermalLimits
    response: ResponseTerms | Unknown = Field(discriminator="kind")

    seasons: tuple[Season, ...] = Field(min_length=1)
    bands: tuple[TimeBand, ...] = Field(min_length=1)
    cells: tuple[AvailabilityCell, ...] = Field(min_length=1)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ThermalFlexAvailability is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> ThermalFlexAvailability:
        if list(self.topology) != sorted(set(self.topology)):
            raise ValueError("topology must be sorted with no repeats")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.evidence.kind != _BASIS_FOR[self.evidence_class]:
            raise ValueError(
                f"evidence_class {self.evidence_class.value} needs evidence of kind "
                f"{_BASIS_FOR[self.evidence_class]}, not {self.evidence.kind}"
            )
        is_synthetic = self.record_class == "synthetic_fixture"
        if is_synthetic != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")

        months = [m for s in self.seasons for m in s.months]
        if sorted(months) != list(range(1, 13)):
            raise ValueError("seasons must partition the twelve months: each month exactly once")
        firsts = [s.months[0] for s in self.seasons]
        if firsts != sorted(firsts):
            raise ValueError("seasons must be sorted by their first month (one canonical order)")
        if len({s.season_id for s in self.seasons}) != len(self.seasons):
            raise ValueError("season_id values must be unique")

        edge = 0
        for b in self.bands:
            if b.start_minute != edge:
                raise ValueError(
                    "bands must partition the day: sorted, contiguous, from minute 0 to 1440"
                )
            edge = b.end_minute
        if edge != _MINUTES_PER_DAY:
            raise ValueError("bands must partition the day: they must end at minute 1440")
        if len({b.band_id for b in self.bands}) != len(self.bands):
            raise ValueError("band_id values must be unique")

        expected = [(s.season_id, b.band_id) for s in self.seasons for b in self.bands]
        got = [(c.season_id, c.band_id) for c in self.cells]
        if got != expected:
            raise ValueError(
                "cells must hold exactly one entry per (season, band), in season order then band "
                "order; a cell with no assessment is an explicit unknown, never missing"
            )

        cap = self.cooling_electrical_capacity_kw
        if isinstance(cap, int):
            for c in self.cells:
                a = c.availability
                if isinstance(a, Flex) and a.curve[0].reduction_kw > cap:
                    raise ValueError(
                        f"cell ({c.season_id}, {c.band_id}): reduction exceeds "
                        "cooling_electrical_capacity_kw"
                    )
        return self

    @model_validator(mode="after")
    def _down_level(self) -> ThermalFlexAvailability:
        # Down-level rule (WP-BO-06): a surface labelled 1.0.0 holds only 1.0.0 IC-4 references.
        if self.contract_version == "1.0.0":
            for where, ref in _surface_refs(self.limits, self.evidence):
                if ref.contract_version != "1.0.0":
                    raise ValueError(
                        f"{where} IC-4 reference version {ref.contract_version} is not in IC-11 "
                        "1.0.0: label the surface 1.1.0"
                    )
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity instructions and run records use."""
        return content_hash(self)


class AvailabilityRef(ContractModel):
    """A reference to an IC-11 availability surface by content hash."""

    kind: Literal["thermal_flex_availability_ref"]
    contract: Literal["IC-11"]
    contract_version: Version1x
    availability_hash: Sha256


def availability_ref(obj: ThermalFlexAvailability) -> AvailabilityRef:
    return AvailabilityRef(
        kind="thermal_flex_availability_ref",
        contract=CONTRACT_ID,
        contract_version=obj.contract_version,
        availability_hash=obj.content_hash(),
    )


# --------------------------------------------------------------------------------------------
# Flex instruction and acknowledgement (TF-04)
# --------------------------------------------------------------------------------------------


@final
class FlexInstruction(ContractModel):
    """IC-11 v1.1.0: a request to ThermaFlex to reduce cooling demand.

    ``start`` is on a whole minute; the reduction is held for ``duration_min`` minutes from
    ``start`` (the declared ramp is inside that). ``ack_deadline`` lies in ``(issued_at, start]``.
    All instants are UTC.
    """

    contract: Literal["IC-11"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION

    instruction_id: _ID
    site_id: NonEmptyStr
    #: The availability surface the issuer planned against.
    availability: AvailabilityRef
    issued_at: UtcInstant
    issuer_role: InstructionIssuer
    #: Opaque id of the issuing service or person (an IC-13 ``sub``); no personal data.
    issuer_id: NonEmptyStr
    #: The upstream party the reduction serves (DNO flex, NESO, aggregator), or not applicable
    #: when the dispatch serves the site's own cost.
    on_behalf_of: IssuerRole | NotApplicable
    stream: _STREAM | NotApplicable
    reduction_kw: PosKw
    start: UtcInstant
    duration_min: Duration
    ack_deadline: UtcInstant

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FlexInstruction is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _times(self) -> FlexInstruction:
        if self.start.second or self.start.microsecond:
            raise ValueError("start must fall on a whole minute")
        if self.start < self.issued_at:
            raise ValueError("start cannot precede issued_at")
        if not (self.issued_at < self.ack_deadline <= self.start):
            raise ValueError("ack_deadline must be after issued_at and no later than start")
        return self

    @model_validator(mode="after")
    def _down_level(self) -> FlexInstruction:
        # Down-level rule (WP-BO-06): an instruction labelled 1.0.0 holds only what 1.0.0 allowed.
        if self.contract_version == "1.0.0":
            version = self.availability.contract_version
            if version != "1.0.0":
                raise ValueError(
                    f"availability IC-11 reference version {version} is not in IC-11 1.0.0: "
                    "label the instruction 1.1.0"
                )
            role = self.on_behalf_of
            if isinstance(role, IssuerRole) and role.value not in V1_0_0_ISSUER_ROLES:
                raise ValueError(
                    f"on_behalf_of {role.value} is not in IC-11 1.0.0: label the instruction 1.1.0"
                )
        return self

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min)

    def content_hash(self) -> str:
        return content_hash(self)


class Accepted(ContractModel):
    """The full instructed reduction is accepted."""

    kind: Literal["accepted"]


class Rejected(ContractModel):
    kind: Literal["rejected"]
    reason_code: ResponseReason
    detail: NonEmptyStr


class PartiallyAccepted(ContractModel):
    """Part of the reduction is accepted: ``accepted_kw`` is at least 1 and below the instructed
    kW (checked against the instruction by `verify_acknowledgement`)."""

    kind: Literal["partially_accepted"]
    accepted_kw: PosKw
    reason_code: ResponseReason
    detail: NonEmptyStr


@final
class FlexAcknowledgement(ContractModel):
    """IC-11 v1.1.0: ThermaFlex's answer to one `FlexInstruction`, bound to it by hash."""

    contract: Literal["IC-11"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION

    ack_id: _ID
    instruction_id: _ID
    instruction_hash: Sha256
    acknowledged_at: UtcInstant
    #: Opaque id of the responding controller.
    responder_id: NonEmptyStr
    response: Accepted | Rejected | PartiallyAccepted = Field(discriminator="kind")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("FlexAcknowledgement is final: a new shape is a new contract version")

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# Consumer helpers: pure, deterministic, contract-only (no plant model).
# --------------------------------------------------------------------------------------------


class FlexAnswer(ContractModel):
    """What a surface says for one event start and duration. The four statuses stay distinct:
    zero flex answers ``0``; unavailable and unknown answer no number, with their reason."""

    status: AvailabilityStatus
    season_id: _ID
    band_id: _ID
    deliverable_kw: Kw | NotApplicable | Unknown
    rebound: Rebound | NotApplicable


def deliverable_kw(flex: Flex, duration_min: int) -> tuple[int, Rebound | None]:
    """kW holdable for ``duration_min`` minutes, with the rebound of the point that supplies it.

    The first curve point at least as long as the request supplies it; beyond the longest point
    the answer is ``(0, None)``.
    """
    if duration_min < 1:
        raise ValueError("duration_min must be at least 1")
    for p in flex.curve:
        if p.duration_min >= duration_min:
            return p.reduction_kw, p.rebound
    return 0, None


def _locate(
    surface: ThermalFlexAvailability, month: int, minute_of_day: int
) -> tuple[Season, TimeBand, AvailabilityCell]:
    if not (1 <= month <= 12 and 0 <= minute_of_day < _MINUTES_PER_DAY):
        raise ValueError("month must be 1-12 and minute_of_day 0-1439")
    season = next(s for s in surface.seasons if month in s.months)
    band = next(b for b in surface.bands if b.start_minute <= minute_of_day < b.end_minute)
    cell = next(
        c for c in surface.cells if (c.season_id, c.band_id) == (season.season_id, band.band_id)
    )
    return season, band, cell


def query(
    surface: ThermalFlexAvailability, month: int, minute_of_day: int, duration_min: int
) -> FlexAnswer:
    """Look up an event starting at a local ``month`` and ``minute_of_day`` on `CLOCK`."""
    season, band, cell = _locate(surface, month, minute_of_day)
    a = cell.availability

    def answer(
        status: AvailabilityStatus,
        kw: int | NotApplicable | Unknown,
        rebound: Rebound | NotApplicable,
    ) -> FlexAnswer:
        return FlexAnswer(
            status=status,
            season_id=season.season_id,
            band_id=band.band_id,
            deliverable_kw=kw,
            rebound=rebound,
        )

    if isinstance(a, Flex):
        kw, rebound = deliverable_kw(a, duration_min)
        if rebound is None:
            held = not_applicable(f"no curve point reaches {duration_min} min; nothing is held")
            return answer(AvailabilityStatus.FLEX, kw, held)
        return answer(AvailabilityStatus.FLEX, kw, rebound)
    if isinstance(a, ZeroFlex):
        nothing = not_applicable("zero flex: nothing is held, so nothing rebounds")
        return answer(AvailabilityStatus.ZERO_FLEX, 0, nothing)
    if isinstance(a, Unavailable):
        why = not_applicable(f"unavailable ({a.reason_code.value}): {a.detail}")
        return answer(
            AvailabilityStatus.UNAVAILABLE, why, not_applicable("unavailable: no flex is offered")
        )
    return answer(
        AvailabilityStatus.UNKNOWN, a, not_applicable("unknown: the cell has not been assessed")
    )


def query_at(surface: ThermalFlexAvailability, start: datetime, duration_min: int) -> FlexAnswer:
    """Look up an event starting at an aware instant, read on the GB clock (`CLOCK`)."""
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    local = start.astimezone(ZoneInfo(CLOCK))
    return query(surface, local.month, local.hour * 60 + local.minute, duration_min)


def _statuses_crossed(
    surface: ThermalFlexAvailability, start: datetime, end: datetime
) -> frozenset[AvailabilityStatus]:
    """The kinds of every cell an event ``[start, end)`` touches, read on the GB clock.

    Steps from band edge to band edge. Where the clock changes inside a step (BST starts or
    ends) it walks that step minute by minute, so a band the change jumps into is never missed.
    An instruction starts on a whole minute and lasts whole minutes, and the GB offset is a whole
    hour, so a cell never changes inside a minute and the walk is exact.
    """
    tz = ZoneInfo(CLOCK)
    seen: set[AvailabilityStatus] = set()
    t = start
    while t < end:
        local = t.astimezone(tz)
        minute = local.hour * 60 + local.minute
        _, band, cell = _locate(surface, local.month, minute)
        seen.add(AvailabilityStatus(cell.availability.kind))
        step = max(1, min(band.end_minute - minute, (end - t) // timedelta(minutes=1)))
        if (t + timedelta(minutes=step)).astimezone(tz).utcoffset() != local.utcoffset():
            step = 1
        t += timedelta(minutes=step)
    return frozenset(seen)


class InstructionCheck(ContractModel):
    """Whether an instruction fits the surface it names. ``reasons`` is sorted; empty means it
    fits. ``answer`` is the start cell's answer. The plant may still refuse for a live reason
    (`ResponseReason.IT_SAFETY_LIMIT` ...)."""

    ok: bool
    reasons: tuple[ResponseReason, ...]
    answer: FlexAnswer | NotApplicable


def check_instruction(
    surface: ThermalFlexAvailability, instruction: FlexInstruction
) -> InstructionCheck:
    """Check an instruction against its surface using the contract alone (B2 before sending,
    ThermaFlex on receipt; one function, so both reach the same reason codes).

    The kW is read from the cell the event **starts** in (the physics: a curve point is a hold
    from a start in that cell). Validity, authority and assessment are read over the **whole
    event** ``[start, end)``:

    - ``outside_validity_window`` unless ``valid_from <= start`` and ``end <= valid_until``;
    - ``availability_unavailable`` if the event touches an ``unavailable`` cell anywhere;
    - ``availability_unknown`` if the event touches an ``unknown`` cell anywhere. This is the
      conservative rule: an unknown cell carries no claim at the surface's evidence class, so an
      event may not run into one any more than it may start in one.

    ``zero_flex`` and ``flex`` cells the event runs into do not refuse it: the start cell's
    curve already declares the hold.
    """
    reasons: set[ResponseReason] = set()
    if (
        instruction.site_id != surface.site_id
        or instruction.availability.availability_hash != surface.content_hash()
    ):
        return InstructionCheck(
            ok=False,
            reasons=(ResponseReason.WRONG_SITE_OR_SURFACE,),
            answer=not_applicable("the instruction names another site or surface"),
        )
    if not (surface.valid_from <= instruction.start and instruction.end <= surface.valid_until):
        reasons.add(ResponseReason.OUTSIDE_VALIDITY_WINDOW)
    if isinstance(surface.response, ResponseTerms):
        notice = instruction.start - instruction.issued_at
        if notice < timedelta(minutes=surface.response.min_notice_min):
            reasons.add(ResponseReason.INSUFFICIENT_NOTICE)
    answer = query_at(surface, instruction.start, instruction.duration_min)
    if isinstance(answer.deliverable_kw, int) and instruction.reduction_kw > answer.deliverable_kw:
        reasons.add(ResponseReason.EXCEEDS_DECLARED_AVAILABILITY)
    crossed = _statuses_crossed(surface, instruction.start, instruction.end)
    if AvailabilityStatus.UNAVAILABLE in crossed:
        reasons.add(ResponseReason.AVAILABILITY_UNAVAILABLE)
    if AvailabilityStatus.UNKNOWN in crossed:
        reasons.add(ResponseReason.AVAILABILITY_UNKNOWN)
    return InstructionCheck(ok=not reasons, reasons=tuple(sorted(reasons)), answer=answer)


class AckCheck(ContractModel):
    accepted_kw: Kw
    late: bool


def verify_acknowledgement(instruction: FlexInstruction, ack: FlexAcknowledgement) -> AckCheck:
    """Check that ``ack`` answers ``instruction`` and return the kW it accepts.

    Refused (``ValueError``): another instruction's id or hash, an acknowledgement before the
    instruction was issued, or a partial acceptance that is not below the instructed kW. An
    acknowledgement after ``ack_deadline`` is valid and reported ``late``; what B2 does with a
    late answer is B2's policy, not the contract's.
    """
    if ack.instruction_id != instruction.instruction_id:
        raise ValueError("the acknowledgement names another instruction_id")
    if ack.instruction_hash != instruction.content_hash():
        raise ValueError("the acknowledgement's instruction_hash does not match the instruction")
    if ack.acknowledged_at < instruction.issued_at:
        raise ValueError("an acknowledgement cannot precede the instruction it answers")
    r = ack.response
    if isinstance(r, PartiallyAccepted) and r.accepted_kw >= instruction.reduction_kw:
        raise ValueError(
            "a partial acceptance must accept less than the instructed kW (all of it is 'accepted')"
        )
    return AckCheck(
        accepted_kw=accepted_kw(instruction, ack),
        late=ack.acknowledged_at > instruction.ack_deadline,
    )


def accepted_kw(instruction: FlexInstruction, ack: FlexAcknowledgement) -> int:
    r = ack.response
    if isinstance(r, Accepted):
        return instruction.reduction_kw
    if isinstance(r, PartiallyAccepted):
        return r.accepted_kw
    return 0


def kw_to_mw(kw: int) -> Fraction:
    """Exact MW for a kW integer (the contract never stores a float MW)."""
    return Fraction(kw, 1000)
