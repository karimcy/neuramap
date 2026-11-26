"""IC-3 dispatch envelope: frozen contract, version 1.0.0 (roadmap WP-SIZE-07, spec §10A, A8-03).

The envelope is what Assess hands to Operate: the reserve policy, availability windows and state
of energy floors that live dispatch (B2), the edge gateway (B1), auctions, the dashboard, the
ledger and the IC-15 legal templates must honour. One producer (the sizer, through the WP-SIZE-08
exporter); many consumers. They are bound to one instance by its content hash.

Spec → this contract:

==================================  ==========================================================
spec §10A IC-3                      field
==================================  ==========================================================
reserve partition by time           ``terms[*].terms.discharge`` / ``.charge`` (`PowerPartition`)
SoC floors                          ``terms[*].terms.energy`` (`EnergyStack`), ``fail_safe``
import limits                       ``terms[*].terms.import_limit_kw``, ``.import_basis``
availability windows                service slices by cell (`service_windows`) and the
                                    ``withheld`` tranche (whole-asset availability)
committed services                  ``services`` (`CommittedService`)
thermal bands                       ``thermal`` (`ThermalEnvelope`) and ``terms[*].terms.thermal``
validity period                     ``valid_from``, ``valid_until`` (half-open), ``supersedes``
source ``input_hash``               ``source.input_hash`` (required; `EnvelopeSource`)
==================================  ==========================================================

**Time base (D85).** A recurring template on the GB clock (`CLOCK`): ``seasons``
partition the twelve months, ``day_groups`` partition the ISO week, ``bands`` partition the local
day on the 30-minute grid, and ``cells`` name one ``terms_id`` per (season, day group, band).
Dated ``overrides`` replace a cell's terms wholesale for an absolute UTC interval. `resolve`
compiles the envelope onto UTC half-hours (it maps each UTC half-hour to its local cell, never a
local time to UTC, so the 23- and 25-hour days need no rule of their own) and step-encodes the
result as a `ResolvedTimeline`. The envelope carries that timeline's hash (``resolved_hash``) and
validation recomputes it, so a consumer with no clock logic (the edge) can read the timeline and
trust it by hash.

**Exact integers (D86).** Power is integer kW, energy integer kWh (absolute state of
energy, never a fraction), durations integer minutes, temperatures integer tenths of a degree
(IC-11 `TenthsC`). No floats, rationals or percentages. Every closure is an exact equality.
**Rounding obligation on the producer**: round every quantity to the safe side. Floors and held
energy round up; limits, ceilings and service slices round down; the merchant slice absorbs the
residual, so closure holds. Snap by 1e-6 before rounding so solver noise does not cost a whole kW.
If rounding makes the envelope infeasible the producer refuses; it never relaxes a term.

**Partition (D87).** Each cell's battery rating is partitioned, per direction, into
resilience, one slice per committed service, merchant and a withheld tranche, and the usable
energy is stacked as resilience floor, energy held for discharge services, merchant, energy held
for charge services and withheld energy. Both sums are exact. "No double-booking" is this
arithmetic (D57): nothing here gates a revenue stream, its basis, size or value.

**Thermal (D88).** An envelope for a site with controllable cooling names its IC-11
surface by hash and restates the surface's binding IT-safety band; every cell then carries an
operating band inside it and any thermal commitments. `check_against_surface` proves the
commitments against the surface's firm kW.

**Source (D89).** ``source.input_hash`` is required and equals the assessment quote's
input hash; ``source.run`` restates the four run pins and ``run_id`` is re-derived from them; the
basis is a typed label (a study basis is never presented as underwriting, D46).

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every instance); JSON Schemas frozen at
``schemas/ic3_dispatch_envelope.v1.0.0.schema.json`` and
``schemas/ic3_resolved_timeline.v1.0.0.schema.json``; git tag ``GIT_TAG``. Reads IC-1 and IC-2
references (`ConnectionAgreementRef`, `ScenarioSetRef`), IC-4 (`EvidenceRef`, `IssuerRole`) and
IC-11 (`AvailabilityRef`, `TemperatureBand`), all by the 1.x range.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from itertools import combinations
from math import floor
from typing import Annotated, Any, Final, Literal, final
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, model_validator

from gridq.contracts._base import (
    EMPTY_SHA256,
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
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.curtailment import ConnectionAgreementRef, ScenarioSetRef
from gridq.contracts.evidence import EvidenceRef, IssuerRole
from gridq.contracts.thermal_flex import (
    AvailabilityRef,
    Flex,
    TemperatureBand,
    TenthsC,
    ThermalFlexAvailability,
    Unavailable,
    deliverable_kw,
)

__all__ = [
    "CLOCK",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "GIT_TAG",
    "RANK",
    "RESOLUTION_MIN",
    "SUPPORTED_VERSIONS",
    "VERSION_2X_PATTERN",
    "AgreementCheck",
    "AgreementReason",
    "AssumedApproved",
    "CellTerms",
    "CommittedService",
    "ConstraintLibraryPin",
    "DayGroup",
    "Direction",
    "DispatchEnvelope",
    "EnergyStack",
    "EnvelopeRef",
    "EnvelopeSource",
    "Evidenced",
    "FailSafe",
    "FlatLimits",
    "HashedTerms",
    "HeldEnergy",
    "ImportBasis",
    "LibraryCheck",
    "NamedTerms",
    "Override",
    "PowerPartition",
    "Producer",
    "QuoteBinding",
    "RecordClass",
    "ResolvedTimeline",
    "RunPins",
    "ScheduleCheck",
    "ScheduledPeriod",
    "Season",
    "Segment",
    "ServiceDelivery",
    "ServiceOffer",
    "ServiceSlice",
    "ServiceWindow",
    "SourceCheck",
    "SourceFinding",
    "StudyBasisLabel",
    "SurfaceCheck",
    "SurfaceReason",
    "TemperatureRange",
    "TemplateCell",
    "ThermalCommitment",
    "ThermalEnvelope",
    "ThermalTerms",
    "TimeBand",
    "TimeBase",
    "TimelineStep",
    "UnderwritingBasisLabel",
    "UnderwrittenTotals",
    "UnderwrittenValue",
    "Violation",
    "ViolationCode",
    "Withheld",
    "WithheldReason",
    "active_at",
    "basis_banner",
    "basis_label",
    "check_against_agreement",
    "check_against_surface",
    "check_constraint_library",
    "check_schedule",
    "check_source",
    "derive_run_id",
    "envelope_ref",
    "fail_safe_terms",
    "flat_limits",
    "merchant_headroom",
    "offered_kw",
    "reserved_kw",
    "resolve",
    "resolved_hash_of",
    "service_windows",
    "terms_at",
    "timeline_matches",
    "timeline_segments",
    "timeline_terms_at",
    "underwritten_total",
]

CONTRACT_ID: Final = "IC-3"
CONTRACT_VERSION: Final = "1.0.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0",)
GIT_TAG: Final = "contracts/ic3-dispatch-envelope/v1.0.0"

#: The clock every season, day group and band is read on (GB local time, as IC-11).
CLOCK: Final = "Europe/London"
#: The grid: band edges, override bounds and validity bounds fall on UTC half-hours. GB clock
#: changes fall at 01:00 UTC, so a local half-hour band compiles onto UTC half-hours exactly.
RESOLUTION_MIN: Final = 30
#: IC-12 (the chassis, `QuoteRecord`) was at major version 2 when IC-3 1.0.0 froze: an assessment
#: reference accepts 2.x. IC-12 is now 3.0.0 (WP-UW-07); binding a 3.0.0 quote needs an IC-3 minor.
VERSION_2X_PATTERN: Final = r"^2\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

_TZ: Final = ZoneInfo(CLOCK)
_HALF_HOUR: Final = timedelta(minutes=RESOLUTION_MIN)
_MINUTE: Final = timedelta(minutes=1)
_MINUTES_PER_DAY: Final = 1440

Kw = Annotated[int, Field(ge=0)]
PosKw = Annotated[int, Field(ge=1)]
Kwh = Annotated[int, Field(ge=0)]
PosKwh = Annotated[int, Field(ge=1)]
Duration = Annotated[int, Field(ge=1, le=_MINUTES_PER_DAY)]
_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
#: An upper-snake revenue-stream identifier, as IC-4 ``StreamScope.stream`` and IC-11. Open: a
#: new stream is a new string, never a contract change.
_STREAM = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
#: A chassis extension-registry id (`gridq.extensions.ID_PATTERN`, D73). The leaf cannot read the
#: registry; a test checks the fixtures' ids against it.
_REGISTRY_ID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
_RUN_ID = Annotated[str, Field(pattern=r"^run_[0-9a-f]{16}$")]
_VERSION_2X = Annotated[str, Field(pattern=VERSION_2X_PATTERN)]


# --------------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------------


class RecordClass(StrEnum):
    """What the envelope is. ``synthetic_fixture`` exactly when the site id starts ``SYNTHETIC-``;
    consumers must refuse it outside tests. ``live`` may hold no unknown reference."""

    LIVE = "live"
    REPLAYED_REAL_DATA = "replayed_real_data"
    MODELLED = "modelled"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class ImportBasis(StrEnum):
    """What a cell's import limit rests on. Only a ``firm`` limit is bounded by the fail-safe
    firm limit; a non-firm or DNO-scheduled limit may exceed it while it is granted."""

    FIRM = "firm"
    NON_FIRM = "non_firm"
    SCHEDULED_DNO_SETPOINT = "scheduled_dno_setpoint"


class WithheldReason(StrEnum):
    """Why part of the asset is not offered at all in a cell (whole-asset availability)."""

    MAINTENANCE = "maintenance"
    PLANT_FAULT = "plant_fault"
    HOST_RESTRICTION = "host_restriction"
    NETWORK_RESTRICTION = "network_restriction"
    WARRANTY_LIMIT = "warranty_limit"
    COMMISSIONING = "commissioning"


class Direction(StrEnum):
    DISCHARGE = "discharge"
    CHARGE = "charge"


# --------------------------------------------------------------------------------------------
# Committed services
# --------------------------------------------------------------------------------------------


class Evidenced(ContractModel):
    """The service rests on an IC-4 evidence object (a contract, an award, an agreement)."""

    kind: Literal["evidenced"]
    evidence: EvidenceRef


class AssumedApproved(ContractModel):
    """The service is assumed approved, with the question that would confirm it. Labelled, never
    refused (D57): it partitions exactly like an evidenced service."""

    kind: Literal["assumed_approved"]
    confirmation_ask: NonEmptyStr


class UnderwrittenValue(ContractModel):
    """The value the assessment underwrote for a service, in whole pence a year, with its basis.
    Read by IC-15; never an input to any invariant here."""

    kind: Literal["underwritten_value"]
    pence_per_year: Annotated[int, Field(ge=0)]
    value_basis: Evidenced | AssumedApproved = Field(discriminator="kind")


class CommittedService(ContractModel):
    """A service the asset is committed to. Its battery slices and thermal commitments are in the
    cells; this states what it is and what it rests on.

    ``sustain_min`` is how long a battery slice must be held: every positive slice has held energy
    of at least ``ceil(kw x sustain_min / 60)`` kWh. A service delivered only by cooling turn-down
    has no battery slice, and its ``sustain_min`` is not applicable (each thermal commitment
    states its own duration).
    """

    service_id: _ID
    stream: _STREAM
    sustain_min: Duration | NotApplicable
    counterparty_role: IssuerRole | Unknown
    #: The counterparty's reference for the commitment (agreement or unit id), when known.
    reference: NonEmptyStr | Unknown
    basis: Evidenced | AssumedApproved | Unknown = Field(discriminator="kind")
    underwritten: UnderwrittenValue | Unknown | NotApplicable = Field(discriminator="kind")


# --------------------------------------------------------------------------------------------
# Cell terms: the partition, the energy stack, the import limit, the thermal band
# --------------------------------------------------------------------------------------------


class ServiceSlice(ContractModel):
    service_id: _ID
    kw: PosKw


class Withheld(ContractModel):
    """Power not offered to anyone in this cell (a derating or an outage), with its reason."""

    kind: Literal["withheld"]
    kw: PosKw
    reason_code: WithheldReason
    reason: NonEmptyStr


class PowerPartition(ContractModel):
    """One direction's rating, partitioned: ``resilience_kw + sum(services) + merchant_kw +
    withheld.kw`` equals the rating exactly (checked by the envelope, which holds the rating).
    A zero service slice is absence: a slice is at least 1 kW."""

    resilience_kw: Kw
    services: tuple[ServiceSlice, ...]
    merchant_kw: Kw
    withheld: Withheld | NotApplicable = Field(discriminator="kind")

    @model_validator(mode="after")
    def _canonical(self) -> PowerPartition:
        ids = [s.service_id for s in self.services]
        if ids != sorted(set(ids)):
            raise ValueError("service slices must be sorted by service_id with no repeats")
        return self

    def total_kw(self) -> int:
        withheld = self.withheld.kw if isinstance(self.withheld, Withheld) else 0
        return self.resilience_kw + sum(s.kw for s in self.services) + self.merchant_kw + withheld

    def slice_kw(self, service_id: str) -> int:
        return next((s.kw for s in self.services if s.service_id == service_id), 0)


class HeldEnergy(ContractModel):
    service_id: _ID
    kwh: PosKwh


class EnergyStack(ContractModel):
    """The usable energy, stacked bottom to top: ``resilience_floor_kwh``, energy held so
    discharge services can be delivered (``held_up``), the merchant band, headroom held so charge
    services can be absorbed (``held_down``), and withheld energy. The sum equals the usable
    energy exactly. State of energy floor = resilience + held up; ceiling = usable - held down -
    withheld (`flat_limits`)."""

    resilience_floor_kwh: Kwh
    held_up: tuple[HeldEnergy, ...]
    merchant_kwh: Kwh
    held_down: tuple[HeldEnergy, ...]
    withheld_kwh: Kwh

    @model_validator(mode="after")
    def _canonical(self) -> EnergyStack:
        for name, held in (("held_up", self.held_up), ("held_down", self.held_down)):
            ids = [h.service_id for h in held]
            if ids != sorted(set(ids)):
                raise ValueError(f"{name} must be sorted by service_id with no repeats")
        return self

    def total_kwh(self) -> int:
        return (
            self.resilience_floor_kwh
            + sum(h.kwh for h in self.held_up)
            + self.merchant_kwh
            + sum(h.kwh for h in self.held_down)
            + self.withheld_kwh
        )


class ThermalCommitment(ContractModel):
    """A committed cooling turn-down: ``reduction_kw`` held for ``duration_min`` from any start in
    the cell, for a declared service."""

    service_id: _ID
    reduction_kw: PosKw
    duration_min: Duration


class ThermalTerms(ContractModel):
    """A cell's operating band (inside the envelope's binding band, at its point) and its thermal
    commitments. No commitment is an empty tuple, never a zero."""

    kind: Literal["thermal_terms"]
    band: TemperatureBand
    commitments: tuple[ThermalCommitment, ...]

    @model_validator(mode="after")
    def _canonical(self) -> ThermalTerms:
        ids = [c.service_id for c in self.commitments]
        if ids != sorted(set(ids)):
            raise ValueError("thermal commitments must be sorted by service_id with no repeats")
        return self


class CellTerms(ContractModel):
    """Everything in force in one cell: the site import and export limits at the connection point,
    the partition of each battery direction, the energy stack and the thermal terms."""

    import_limit_kw: Kw
    export_limit_kw: Kw
    import_basis: ImportBasis
    discharge: PowerPartition
    charge: PowerPartition
    energy: EnergyStack
    thermal: ThermalTerms | NotApplicable = Field(discriminator="kind")

    @model_validator(mode="after")
    def _holds_match_slices(self) -> CellTerms:
        for direction, part, held in (
            ("discharge", self.discharge, self.energy.held_up),
            ("charge", self.charge, self.energy.held_down),
        ):
            if [s.service_id for s in part.services] != [h.service_id for h in held]:
                raise ValueError(
                    f"every {direction} service slice has held energy and only those do "
                    f"({'held_up' if direction == 'discharge' else 'held_down'})"
                )
        withheld = isinstance(self.discharge.withheld, Withheld) or isinstance(
            self.charge.withheld, Withheld
        )
        if self.energy.withheld_kwh > 0 and not withheld:
            raise ValueError("withheld energy needs a withheld power tranche in some direction")
        return self


class NamedTerms(ContractModel):
    terms_id: _ID
    terms: CellTerms


# --------------------------------------------------------------------------------------------
# The time base: template, overrides, validity
# --------------------------------------------------------------------------------------------


class Season(ContractModel):
    """A set of calendar months. The envelope's seasons partition the twelve months."""

    season_id: _ID
    months: tuple[Annotated[int, Field(ge=1, le=12)], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ascending(self) -> Season:
        if list(self.months) != sorted(set(self.months)):
            raise ValueError("months must be strictly ascending with no repeats")
        return self


class DayGroup(ContractModel):
    """A set of ISO weekdays (1 = Monday ... 7 = Sunday). The day groups partition the week."""

    day_group_id: _ID
    iso_weekdays: tuple[Annotated[int, Field(ge=1, le=7)], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ascending(self) -> DayGroup:
        if list(self.iso_weekdays) != sorted(set(self.iso_weekdays)):
            raise ValueError("iso_weekdays must be strictly ascending with no repeats")
        return self


class TimeBand(ContractModel):
    """A half-open local-clock window ``[start_minute, end_minute)`` on `CLOCK`, on the 30-minute
    grid. ``end_minute == 1440`` is midnight at the end of the day; a band never wraps midnight."""

    band_id: _ID
    start_minute: Annotated[int, Field(ge=0, le=_MINUTES_PER_DAY - RESOLUTION_MIN)]
    end_minute: Annotated[int, Field(ge=RESOLUTION_MIN, le=_MINUTES_PER_DAY)]

    @model_validator(mode="after")
    def _on_grid(self) -> TimeBand:
        if self.start_minute % RESOLUTION_MIN or self.end_minute % RESOLUTION_MIN:
            raise ValueError(f"band edges must fall on the {RESOLUTION_MIN}-minute grid")
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be after start_minute (bands do not wrap midnight)")
        return self


class TemplateCell(ContractModel):
    season_id: _ID
    day_group_id: _ID
    band_id: _ID
    terms_id: _ID


class Override(ContractModel):
    """Replaces the template's terms wholesale over ``[start, end)`` (UTC, on the grid): a
    maintenance outage, a stress event, a dated call. Never a field-by-field merge."""

    override_id: _ID
    start: UtcInstant
    end: UtcInstant
    terms_id: _ID
    reason: NonEmptyStr


def _on_grid(t: datetime) -> bool:
    return t.second == 0 and t.microsecond == 0 and t.minute % RESOLUTION_MIN == 0


# --------------------------------------------------------------------------------------------
# Envelope-level blocks: fail-safe, thermal, source
# --------------------------------------------------------------------------------------------


class FailSafe(ContractModel):
    """What the edge reverts to on loss of the cloud or market link (spec B1-02): import at or
    below the firm limit, state of energy at or above the resilience floor, markets declared
    unavailable. ``resilience_floor_kwh`` equals the lowest resilience floor in force anywhere in
    the validity, so entering fail-safe from any compliant state is already compliant."""

    firm_import_limit_kw: Kw
    resilience_floor_kwh: Kwh
    markets: Literal["declared_unavailable"]


class ThermalEnvelope(ContractModel):
    """The site's IC-11 surface by hash, and its binding IT-safety band restated (equal to the
    surface's ``limits.binding``: the hard limit). Each cell's band lies inside it."""

    kind: Literal["thermal_envelope"]
    surface: AvailabilityRef
    binding: TemperatureBand


class RunPins(ContractModel):
    """The assessment run's four pins, restated so ``run_id`` can be re-derived (`derive_run_id`,
    the `gridq.provenance` derivation)."""

    run_id: _RUN_ID
    assumption_version: NonEmptyStr
    code_version: NonEmptyStr
    schema_version: NonEmptyStr


class Producer(ContractModel):
    engine: NonEmptyStr
    engine_version: NonEmptyStr


class StudyBasisLabel(ContractModel):
    """The run was priced on a named study basis (D46/D55). ``label`` is that basis's banner and
    must be shown wherever the envelope is (`basis_banner`)."""

    kind: Literal["study"]
    basis_id: _REGISTRY_ID
    label: NonEmptyStr
    disclosure: NonEmptyStr


class UnderwritingBasisLabel(ContractModel):
    """The run was priced on an underwriting basis. Accepted in shape; no underwriting basis is
    implemented yet, so no fixture uses it. A basis id that names a study is refused here (D46)."""

    kind: Literal["underwriting"]
    basis_id: _REGISTRY_ID

    @model_validator(mode="after")
    def _not_a_study(self) -> UnderwritingBasisLabel:
        if "study" in self.basis_id:
            raise ValueError("a study basis is never presented as underwriting (D46)")
        return self


class ConstraintLibraryPin(ContractModel):
    """The constraint library the envelope was sized with (WP-SIZE-02). Dispatch compares its own
    library with this (`check_constraint_library`)."""

    kind: Literal["constraint_library_pin"]
    library_version: NonEmptyStr
    library_hash: Sha256 | Unknown
    constraint_ids: tuple[_REGISTRY_ID, ...]

    @model_validator(mode="after")
    def _canonical(self) -> ConstraintLibraryPin:
        if list(self.constraint_ids) != sorted(set(self.constraint_ids)):
            raise ValueError("constraint_ids must be sorted with no repeats")
        return self


class QuoteBinding(ContractModel):
    """The IC-12 quote the envelope was exported from, by id and input hash. A `QuoteRecord` is
    mutable through its lifecycle, so it is not bound by content hash."""

    kind: Literal["quote_binding"]
    contract: Literal["IC-12"]
    contract_version: _VERSION_2X
    quote_id: NonEmptyStr
    quote_input_hash: Sha256


def derive_run_id(
    input_hash: str, assumption_version: str, code_version: str, schema_version: str
) -> str:
    """``run_id`` from the four pins: GridQ's `build_run_context` derivation, restated here so the
    contract stays a leaf (a test proves the two agree). ``"run_"`` plus the first 16 hex digits
    of the SHA-256 of the sorted, compact JSON of the four pins."""
    pins = {
        "input_hash": input_hash,
        "assumption_version": assumption_version,
        "code_version": code_version,
        "schema_version": schema_version,
    }
    text = json.dumps(pins, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "run_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class EnvelopeSource(ContractModel):
    """Where the envelope came from. ``input_hash`` is required and is never the hash of nothing;
    the assessment quote's input hash equals it; ``run.run_id`` re-derives from the pins."""

    input_hash: Sha256
    run: RunPins
    producer: Producer
    basis: StudyBasisLabel | UnderwritingBasisLabel = Field(discriminator="kind")
    constraint_library: ConstraintLibraryPin | Unknown = Field(discriminator="kind")
    assessment: QuoteBinding
    connection_agreement: ConnectionAgreementRef | NotApplicable | Unknown = Field(
        discriminator="kind"
    )
    #: Stated apart from ``input_hash``: a hash is one-way, so a verifier cannot recover the IC-2
    #: set from it.
    scenario_set: ScenarioSetRef | NotApplicable | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _bound(self) -> EnvelopeSource:
        if self.input_hash == EMPTY_SHA256:
            raise ValueError("source.input_hash is the hash of no input: an envelope needs one")
        if self.assessment.quote_input_hash != self.input_hash:
            raise ValueError("assessment.quote_input_hash must equal source.input_hash")
        r = self.run
        expected = derive_run_id(
            self.input_hash, r.assumption_version, r.code_version, r.schema_version
        )
        if r.run_id != expected:
            raise ValueError(
                f"run.run_id {r.run_id} does not derive from the run pins ({expected})"
            )
        return self


# --------------------------------------------------------------------------------------------
# The compiled timeline
# --------------------------------------------------------------------------------------------


class HashedTerms(ContractModel):
    terms_hash: Sha256
    terms: CellTerms


class TimelineStep(ContractModel):
    """From half-hour ``index`` (0 is ``valid_from``) to the next step, ``terms_hash`` holds."""

    index: Annotated[int, Field(ge=0)]
    terms_hash: Sha256


@final
class ResolvedTimeline(ContractModel):
    """IC-3 v1.0.0: an envelope compiled onto UTC half-hours (`resolve`). Step-encoded: a step
    starts wherever the terms change. Terms are keyed by their own content hash, so the timeline
    does not depend on how the template named them. Read it with no clock logic
    (`timeline_terms_at`); trust it by ``content_hash() == envelope.resolved_hash``."""

    contract: Literal["IC-3"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["resolved_timeline"]

    site_id: NonEmptyStr
    valid_from: UtcInstant
    valid_until: UtcInstant
    resolution_min: Literal[30]
    n_periods: Annotated[int, Field(ge=1)]
    terms: tuple[HashedTerms, ...] = Field(min_length=1)
    steps: tuple[TimelineStep, ...] = Field(min_length=1)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ResolvedTimeline is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> ResolvedTimeline:
        if not (_on_grid(self.valid_from) and _on_grid(self.valid_until)):
            raise ValueError("valid_from and valid_until must fall on the 30-minute UTC grid")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.n_periods != (self.valid_until - self.valid_from) // _HALF_HOUR:
            raise ValueError("n_periods must be the number of half-hours in the validity")
        hashes = [t.terms_hash for t in self.terms]
        if hashes != sorted(set(hashes)):
            raise ValueError("terms must be sorted by terms_hash with no repeats")
        for t in self.terms:
            if content_hash(t.terms) != t.terms_hash:
                raise ValueError(f"terms_hash {t.terms_hash} is not the hash of its terms")
        if self.steps[0].index != 0:
            raise ValueError("the first step starts at index 0")
        for a, b in zip(self.steps, self.steps[1:], strict=False):
            if b.index <= a.index:
                raise ValueError("step indices must strictly increase")
            if b.terms_hash == a.terms_hash:
                raise ValueError("adjacent steps with the same terms are one step")
        if self.steps[-1].index >= self.n_periods:
            raise ValueError("a step starts after the validity ends")
        used = {s.terms_hash for s in self.steps}
        if used != set(hashes):
            raise ValueError("terms must be exactly those the steps use")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


class Segment(ContractModel):
    start: UtcInstant
    end: UtcInstant
    terms_hash: Sha256


def timeline_segments(timeline: ResolvedTimeline) -> tuple[Segment, ...]:
    """The steps as UTC segments ``[start, end)``."""
    edges = [s.index for s in timeline.steps] + [timeline.n_periods]
    return tuple(
        Segment(
            start=timeline.valid_from + a * _HALF_HOUR,
            end=timeline.valid_from + b * _HALF_HOUR,
            terms_hash=s.terms_hash,
        )
        for s, a, b in zip(timeline.steps, edges, edges[1:], strict=False)
    )


def _index_of(valid_from: datetime, valid_until: datetime, instant: datetime) -> int:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    if not (valid_from <= instant < valid_until):
        raise ValueError(
            f"{instant.isoformat()} is outside the validity [{valid_from}, {valid_until})"
        )
    return (instant - valid_from) // _HALF_HOUR


def timeline_terms_at(timeline: ResolvedTimeline, instant: datetime) -> CellTerms:
    """The terms in force at ``instant``, read from the compiled timeline (no clock logic)."""
    i = _index_of(timeline.valid_from, timeline.valid_until, instant)
    step = next(s for s in reversed(timeline.steps) if s.index <= i)
    return next(t.terms for t in timeline.terms if t.terms_hash == step.terms_hash)


# --------------------------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------------------------


@final
class DispatchEnvelope(ContractModel):
    """IC-3 v1.0.0: the terms live dispatch must honour at one site over one validity.

    The template (``seasons`` x ``day_groups`` x ``bands`` → ``cells`` → ``terms``) and the dated
    ``overrides`` say which `CellTerms` are in force at every half-hour of ``[valid_from,
    valid_until)``; ``resolved_hash`` is the hash of that compilation. Power kW, energy kWh, time
    in minutes, temperatures in tenths of a degree: integers throughout, and every partition sums
    exactly.
    """

    contract: Literal["IC-3"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION

    envelope_id: _ID
    site_id: NonEmptyStr
    record_class: RecordClass
    source: EnvelopeSource
    #: The envelope this one replaces from its ``valid_from``, by content hash.
    supersedes: Sha256 | NotApplicable
    #: Inclusive, on the 30-minute UTC grid.
    valid_from: UtcInstant
    #: Exclusive, on the 30-minute UTC grid.
    valid_until: UtcInstant
    clock: Literal["Europe/London"]
    resolution_min: Literal[30]

    rated_discharge_kw: PosKw
    rated_charge_kw: PosKw
    usable_energy_kwh: PosKwh
    fail_safe: FailSafe
    services: tuple[CommittedService, ...]
    thermal: ThermalEnvelope | NotApplicable | Unknown = Field(discriminator="kind")

    seasons: tuple[Season, ...] = Field(min_length=1)
    day_groups: tuple[DayGroup, ...] = Field(min_length=1)
    bands: tuple[TimeBand, ...] = Field(min_length=1)
    cells: tuple[TemplateCell, ...] = Field(min_length=1)
    terms: tuple[NamedTerms, ...] = Field(min_length=1)
    overrides: tuple[Override, ...]

    resolved_hash: Sha256

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DispatchEnvelope is final: a new shape is a new contract version")

    # ---------------------------------------------------------------- identity and labelling

    @model_validator(mode="after")
    def _labelled(self) -> DispatchEnvelope:
        is_synthetic = self.record_class is RecordClass.SYNTHETIC_FIXTURE
        if is_synthetic != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")
        if self.record_class is RecordClass.LIVE:
            unknown = _unknown_references(self)
            if unknown:
                raise ValueError(f"a live envelope may hold no unknown reference: {unknown}")
        return self

    # ---------------------------------------------------------------- the time base

    @model_validator(mode="after")
    def _time_base(self) -> DispatchEnvelope:
        if not (_on_grid(self.valid_from) and _on_grid(self.valid_until)):
            raise ValueError("valid_from and valid_until must fall on the 30-minute UTC grid")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")

        months = [m for s in self.seasons for m in s.months]
        if sorted(months) != list(range(1, 13)):
            raise ValueError("seasons must partition the twelve months: each month exactly once")
        if [s.months[0] for s in self.seasons] != sorted(s.months[0] for s in self.seasons):
            raise ValueError("seasons must be sorted by their first month (one canonical order)")
        days = [d for g in self.day_groups for d in g.iso_weekdays]
        if sorted(days) != list(range(1, 8)):
            raise ValueError("day_groups must partition the week: each ISO weekday exactly once")
        firsts = [g.iso_weekdays[0] for g in self.day_groups]
        if firsts != sorted(firsts):
            raise ValueError("day_groups must be sorted by their first weekday")
        edge = 0
        for b in self.bands:
            if b.start_minute != edge:
                raise ValueError("bands must partition the day: sorted, contiguous, from 0 to 1440")
            edge = b.end_minute
        if edge != _MINUTES_PER_DAY:
            raise ValueError("bands must partition the day: they must end at minute 1440")
        for what, ids in (
            ("season_id", [s.season_id for s in self.seasons]),
            ("day_group_id", [g.day_group_id for g in self.day_groups]),
            ("band_id", [b.band_id for b in self.bands]),
            ("override_id", [o.override_id for o in self.overrides]),
            ("service_id", [s.service_id for s in self.services]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{what} values must be unique")
        service_ids = [s.service_id for s in self.services]
        if service_ids != sorted(service_ids):
            raise ValueError("services must be sorted by service_id")

        terms_ids = [n.terms_id for n in self.terms]
        if terms_ids != sorted(set(terms_ids)):
            raise ValueError("terms must be sorted by terms_id with no repeats")
        if len({content_hash(n.terms) for n in self.terms}) != len(self.terms):
            raise ValueError("two terms_id name identical terms: one set of terms, one id")

        expected = [
            (s.season_id, g.day_group_id, b.band_id)
            for s in self.seasons
            for g in self.day_groups
            for b in self.bands
        ]
        if [(c.season_id, c.day_group_id, c.band_id) for c in self.cells] != expected:
            raise ValueError(
                "cells must hold exactly one entry per (season, day group, band), in season, "
                "day group, band order"
            )
        known = set(terms_ids)
        referenced = {c.terms_id for c in self.cells} | {o.terms_id for o in self.overrides}
        if not referenced <= known:
            raise ValueError(
                f"cells or overrides name undeclared terms: {sorted(referenced - known)}"
            )
        if referenced != known:
            raise ValueError(f"terms no cell or override uses: {sorted(known - referenced)}")
        _check_canonical_template(self)
        _check_overrides(self)
        return self

    # ---------------------------------------------------------------- the partition

    @model_validator(mode="after")
    def _partition(self) -> DispatchEnvelope:
        services = {s.service_id: s for s in self.services}
        used: set[str] = set()
        for named in self.terms:
            where, t = f"terms {named.terms_id}", named.terms
            if t.discharge.total_kw() != self.rated_discharge_kw:
                raise ValueError(
                    f"{where}: the discharge partition sums to {t.discharge.total_kw()} kW, not "
                    f"the {self.rated_discharge_kw} kW rating"
                )
            if t.charge.total_kw() != self.rated_charge_kw:
                raise ValueError(
                    f"{where}: the charge partition sums to {t.charge.total_kw()} kW, not the "
                    f"{self.rated_charge_kw} kW rating"
                )
            if t.energy.total_kwh() != self.usable_energy_kwh:
                raise ValueError(
                    f"{where}: the energy stack sums to {t.energy.total_kwh()} kWh, not the "
                    f"{self.usable_energy_kwh} kWh usable energy"
                )
            for part, held in ((t.discharge, t.energy.held_up), (t.charge, t.energy.held_down)):
                for s, h in zip(part.services, held, strict=True):
                    service = services.get(s.service_id)
                    if service is None:
                        raise ValueError(f"{where}: slice for undeclared service {s.service_id}")
                    sustain = service.sustain_min
                    if not isinstance(sustain, int):
                        raise ValueError(
                            f"{where}: {s.service_id} has no sustain_min, so no battery slice"
                        )
                    if h.kwh * 60 < s.kw * sustain:
                        raise ValueError(
                            f"{where}: {s.service_id} holds {h.kwh} kWh, below {s.kw} kW for "
                            f"{sustain} min"
                        )
                    used.add(s.service_id)
            if (
                t.import_basis is ImportBasis.FIRM
                and t.import_limit_kw > self.fail_safe.firm_import_limit_kw
            ):
                raise ValueError(
                    f"{where}: a firm import limit of {t.import_limit_kw} kW exceeds the fail-safe "
                    f"firm limit of {self.fail_safe.firm_import_limit_kw} kW"
                )
            if isinstance(t.thermal, ThermalTerms):
                for c in t.thermal.commitments:
                    if c.service_id not in services:
                        raise ValueError(
                            f"{where}: thermal commitment for undeclared {c.service_id}"
                        )
                    used.add(c.service_id)
        idle = sorted(set(services) - used)
        if idle:
            raise ValueError(f"declared services with no slice or commitment anywhere: {idle}")
        return self

    # ---------------------------------------------------------------- thermal bands

    @model_validator(mode="after")
    def _thermal(self) -> DispatchEnvelope:
        th = self.thermal
        for named in self.terms:
            cell = named.terms.thermal
            if isinstance(th, ThermalEnvelope) != isinstance(cell, ThermalTerms):
                raise ValueError(
                    f"terms {named.terms_id}: a cell carries thermal terms exactly when the "
                    "envelope names a thermal surface"
                )
            if isinstance(th, ThermalEnvelope) and isinstance(cell, ThermalTerms):
                if cell.band.point is not th.binding.point:
                    raise ValueError(
                        f"terms {named.terms_id}: the band must be measured at the binding point "
                        f"({th.binding.point.value})"
                    )
                if not cell.band.within(th.binding):
                    raise ValueError(
                        f"terms {named.terms_id}: the band is wider than the binding band"
                    )
        return self

    # ---------------------------------------------------------------- the compilation

    @model_validator(mode="after")
    def _compiled(self) -> DispatchEnvelope:
        timeline = resolve(self)
        floors = [t.terms.energy.resilience_floor_kwh for t in timeline.terms]
        if self.fail_safe.resilience_floor_kwh != min(floors):
            raise ValueError(
                f"fail_safe.resilience_floor_kwh must equal the lowest resilience floor in force "
                f"({min(floors)} kWh)"
            )
        if timeline.content_hash() != self.resolved_hash:
            raise ValueError(
                f"resolved_hash does not match the compiled timeline ({timeline.content_hash()})"
            )
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: what IC-15, the ledger and dispatch bind."""
        return content_hash(self)


def _unknown_references(env: DispatchEnvelope) -> list[str]:
    """Every embedded reference that is unknown (a live envelope may hold none)."""
    src = env.source
    out = []
    if isinstance(src.connection_agreement, Unknown):
        out.append("source.connection_agreement")
    if isinstance(src.scenario_set, Unknown):
        out.append("source.scenario_set")
    lib = src.constraint_library
    if isinstance(lib, Unknown) or isinstance(lib.library_hash, Unknown):
        out.append("source.constraint_library")
    if isinstance(env.thermal, Unknown):
        out.append("thermal")
    return out


def _check_canonical_template(env: DispatchEnvelope) -> None:
    """One encoding per policy: no band edge, day group or season the terms do not need."""
    row = {(c.season_id, c.day_group_id, c.band_id): c.terms_id for c in env.cells}
    seasons = [s.season_id for s in env.seasons]
    groups = [g.day_group_id for g in env.day_groups]
    bands = [b.band_id for b in env.bands]
    for b1, b2 in zip(bands, bands[1:], strict=False):
        if all(row[(s, g, b1)] == row[(s, g, b2)] for s in seasons for g in groups):
            raise ValueError(f"bands {b1} and {b2} carry the same terms everywhere: merge them")
    for g1, g2 in combinations(groups, 2):
        if all(row[(s, g1, b)] == row[(s, g2, b)] for s in seasons for b in bands):
            raise ValueError(
                f"day groups {g1} and {g2} carry the same terms everywhere: merge them"
            )
    for s1, s2 in combinations(seasons, 2):
        if all(row[(s1, g, b)] == row[(s2, g, b)] for g in groups for b in bands):
            raise ValueError(f"seasons {s1} and {s2} carry the same terms everywhere: merge them")


def _check_overrides(env: DispatchEnvelope) -> None:
    for o in env.overrides:
        if not (_on_grid(o.start) and _on_grid(o.end)):
            raise ValueError(f"override {o.override_id} must fall on the 30-minute UTC grid")
        if o.end <= o.start:
            raise ValueError(f"override {o.override_id}: end must be after start")
        if not (env.valid_from <= o.start and o.end <= env.valid_until):
            raise ValueError(f"override {o.override_id} lies outside the validity")
    for a, b in zip(env.overrides, env.overrides[1:], strict=False):
        if b.start < a.start:
            raise ValueError("overrides must be sorted by start")
        if b.start < a.end:
            raise ValueError(f"overrides {a.override_id} and {b.override_id} overlap")
        if b.start == a.end and b.terms_id == a.terms_id:
            raise ValueError(
                f"overrides {a.override_id} and {b.override_id} are adjacent with the same terms: "
                "one override"
            )


# --------------------------------------------------------------------------------------------
# Resolution: the one place local time is read
# --------------------------------------------------------------------------------------------


class _Template:
    """The template as lookups, built once per resolution."""

    def __init__(self, env: DispatchEnvelope | TimeBase) -> None:
        self.season = {m: s.season_id for s in env.seasons for m in s.months}
        self.group = {d: g.day_group_id for g in env.day_groups for d in g.iso_weekdays}
        self.bands = env.bands
        self.cell = {(c.season_id, c.day_group_id, c.band_id): c.terms_id for c in env.cells}

    def at(self, t: datetime) -> tuple[str, int]:
        """The terms id of the local cell holding instant ``t``, and the minutes from ``t`` to
        that band's local end."""
        local = t.astimezone(_TZ)
        minute = local.hour * 60 + local.minute
        band = next(b for b in self.bands if b.start_minute <= minute < b.end_minute)
        key = (self.season[local.month], self.group[local.isoweekday()], band.band_id)
        return self.cell[key], band.end_minute - minute


def _template_segments(
    tpl: _Template, start: datetime, end: datetime
) -> Iterator[tuple[datetime, datetime, str]]:
    """UTC segments of ``[start, end)``, each inside one local cell. Steps from band end to band
    end; where the GB offset changes inside a step it walks that step a half-hour at a time (the
    change falls at 01:00 UTC, on the grid, so each half-hour has one offset and one cell)."""
    t = start
    while t < end:
        terms_id, to_band_end = tpl.at(t)
        nxt = min(t + timedelta(minutes=to_band_end), end)
        if nxt.astimezone(_TZ).utcoffset() != t.astimezone(_TZ).utcoffset():
            nxt = t + _HALF_HOUR
        yield t, nxt, terms_id
        t = nxt


def _segments(env: DispatchEnvelope | TimeBase) -> Iterator[tuple[datetime, datetime, str]]:
    tpl = _Template(env)
    t = env.valid_from
    for o in env.overrides:
        if t < o.start:
            yield from _template_segments(tpl, t, o.start)
        yield o.start, o.end, o.terms_id
        t = o.end
    if t < env.valid_until:
        yield from _template_segments(tpl, t, env.valid_until)


def resolve(envelope: DispatchEnvelope | TimeBase) -> ResolvedTimeline:
    """Compile the envelope onto UTC half-hours. Pure and deterministic; the envelope's
    ``resolved_hash`` is ``resolve(envelope).content_hash()``."""
    by_id = {n.terms_id: n.terms for n in envelope.terms}
    hash_of = {tid: content_hash(t) for tid, t in by_id.items()}
    steps: list[TimelineStep] = []
    for start, _, terms_id in _segments(envelope):
        h = hash_of[terms_id]
        if steps and steps[-1].terms_hash == h:
            continue
        steps.append(TimelineStep(index=(start - envelope.valid_from) // _HALF_HOUR, terms_hash=h))
    used = sorted({s.terms_hash for s in steps})
    terms_for = {hash_of[tid]: t for tid, t in by_id.items()}
    return ResolvedTimeline(
        kind="resolved_timeline",
        site_id=envelope.site_id,
        valid_from=envelope.valid_from,
        valid_until=envelope.valid_until,
        resolution_min=RESOLUTION_MIN,
        n_periods=(envelope.valid_until - envelope.valid_from) // _HALF_HOUR,
        terms=tuple(HashedTerms(terms_hash=h, terms=terms_for[h]) for h in used),
        steps=tuple(steps),
    )


class TimeBase(ContractModel):
    """The fields of an envelope that `resolve` reads, alone. A producer cannot validate an
    envelope before it knows ``resolved_hash``, so it takes the hash from its draft first
    (`resolved_hash_of`) and then validates the whole envelope, which re-checks everything."""

    model_config = ConfigDict(extra="ignore")  # merged onto ContractModel's config

    site_id: NonEmptyStr
    valid_from: UtcInstant
    valid_until: UtcInstant
    seasons: tuple[Season, ...] = Field(min_length=1)
    day_groups: tuple[DayGroup, ...] = Field(min_length=1)
    bands: tuple[TimeBand, ...] = Field(min_length=1)
    cells: tuple[TemplateCell, ...] = Field(min_length=1)
    terms: tuple[NamedTerms, ...] = Field(min_length=1)
    overrides: tuple[Override, ...]


def resolved_hash_of(draft: Mapping[str, Any]) -> str:
    """The ``resolved_hash`` a draft envelope (JSON-mode mapping) must carry. The draft's other
    fields are ignored here; validating the envelope afterwards checks them all."""
    return resolve(TimeBase.model_validate_json(json.dumps(dict(draft)))).content_hash()


def timeline_matches(envelope: DispatchEnvelope, timeline: ResolvedTimeline) -> bool:
    """True when ``timeline`` is the envelope's compilation (the edge's check, by hash alone)."""
    return timeline.content_hash() == envelope.resolved_hash


def terms_at(envelope: DispatchEnvelope, instant: datetime) -> CellTerms:
    """The terms in force at an aware ``instant`` inside the validity: an override's if one holds
    it, else the template cell's on the GB clock (the same lookup `resolve` uses)."""
    _index_of(envelope.valid_from, envelope.valid_until, instant)
    by_id = {n.terms_id: n.terms for n in envelope.terms}
    for o in envelope.overrides:
        if o.start <= instant < o.end:
            return by_id[o.terms_id]
    terms_id, _ = _Template(envelope).at(instant)
    return by_id[terms_id]


# --------------------------------------------------------------------------------------------
# Views of the partition (pure, contract-only)
# --------------------------------------------------------------------------------------------


class FlatLimits(ContractModel):
    """The flat view of one cell: what a dispatcher that does not deliver the services may do."""

    import_limit_kw: Kw
    export_limit_kw: Kw
    merchant_discharge_kw: Kw
    merchant_charge_kw: Kw
    soe_floor_kwh: Kwh
    soe_ceiling_kwh: Kwh


def flat_limits(terms: CellTerms) -> FlatLimits:
    e = terms.energy
    return FlatLimits(
        import_limit_kw=terms.import_limit_kw,
        export_limit_kw=terms.export_limit_kw,
        merchant_discharge_kw=terms.discharge.merchant_kw,
        merchant_charge_kw=terms.charge.merchant_kw,
        soe_floor_kwh=e.resilience_floor_kwh + sum(h.kwh for h in e.held_up),
        soe_ceiling_kwh=e.total_kwh() - sum(h.kwh for h in e.held_down) - e.withheld_kwh,
    )


def _part(terms: CellTerms, direction: Direction) -> PowerPartition:
    return terms.discharge if direction is Direction.DISCHARGE else terms.charge


def merchant_headroom(terms: CellTerms, direction: Direction) -> int:
    """kW free for merchant trading and auction offers in ``direction`` (WP-DISP-08)."""
    return _part(terms, direction).merchant_kw


def reserved_kw(terms: CellTerms, direction: Direction) -> int:
    """kW reserved in ``direction``: resilience plus every committed service slice."""
    p = _part(terms, direction)
    return p.resilience_kw + sum(s.kw for s in p.services)


def fail_safe_terms(envelope: DispatchEnvelope) -> FailSafe:
    """What the edge enforces with no schedule: one set of scalars for the whole validity."""
    return envelope.fail_safe


class ServiceOffer(ContractModel):
    service_id: _ID
    discharge_kw: Kw
    charge_kw: Kw
    thermal_reduction_kw: Kw


def _offer(terms: CellTerms, service_id: str) -> ServiceOffer:
    thermal = 0
    if isinstance(terms.thermal, ThermalTerms):
        thermal = sum(
            c.reduction_kw for c in terms.thermal.commitments if c.service_id == service_id
        )
    return ServiceOffer(
        service_id=service_id,
        discharge_kw=terms.discharge.slice_kw(service_id),
        charge_kw=terms.charge.slice_kw(service_id),
        thermal_reduction_kw=thermal,
    )


def _declared(envelope: DispatchEnvelope, service_id: str) -> None:
    if service_id not in {s.service_id for s in envelope.services}:
        raise ValueError(f"{service_id} is not a declared service of this envelope")


def offered_kw(envelope: DispatchEnvelope, service_id: str, instant: datetime) -> ServiceOffer:
    """What the envelope commits to ``service_id`` at ``instant``."""
    _declared(envelope, service_id)
    return _offer(terms_at(envelope, instant), service_id)


class ServiceWindow(ContractModel):
    """A UTC window in which a service is offered, with the same offer throughout."""

    start: UtcInstant
    end: UtcInstant
    offer: ServiceOffer


def service_windows(envelope: DispatchEnvelope, service_id: str) -> tuple[ServiceWindow, ...]:
    """The service's availability windows: every UTC window with a positive slice or thermal
    commitment, adjacent windows with the same offer merged (the time-indexed view)."""
    _declared(envelope, service_id)
    timeline = resolve(envelope)
    by_hash = {t.terms_hash: t.terms for t in timeline.terms}
    out: list[ServiceWindow] = []
    for seg in timeline_segments(timeline):
        offer = _offer(by_hash[seg.terms_hash], service_id)
        if not (offer.discharge_kw or offer.charge_kw or offer.thermal_reduction_kw):
            continue
        if out and out[-1].end == seg.start and out[-1].offer == offer:
            out[-1] = ServiceWindow(start=out[-1].start, end=seg.end, offer=offer)
        else:
            out.append(ServiceWindow(start=seg.start, end=seg.end, offer=offer))
    return tuple(out)


class UnderwrittenTotals(ContractModel):
    """Underwritten value by basis, never blended into one figure."""

    evidenced_pence_per_year: Annotated[int, Field(ge=0)]
    assumed_approved_pence_per_year: Annotated[int, Field(ge=0)]
    unknown_services: tuple[_ID, ...]
    not_applicable_services: tuple[_ID, ...]


def underwritten_total(envelope: DispatchEnvelope) -> UnderwrittenTotals:
    evidenced = assumed = 0
    unknown: list[str] = []
    na: list[str] = []
    for s in envelope.services:
        u = s.underwritten
        if isinstance(u, UnderwrittenValue):
            if isinstance(u.value_basis, Evidenced):
                evidenced += u.pence_per_year
            else:
                assumed += u.pence_per_year
        elif isinstance(u, Unknown):
            unknown.append(s.service_id)
        else:
            na.append(s.service_id)
    return UnderwrittenTotals(
        evidenced_pence_per_year=evidenced,
        assumed_approved_pence_per_year=assumed,
        unknown_services=tuple(unknown),
        not_applicable_services=tuple(na),
    )


def basis_label(service: CommittedService) -> str:
    """How a service's basis reads on any surface: evidenced, assumed approved or unknown."""
    b = service.basis
    if isinstance(b, Evidenced):
        return f"evidenced: {b.evidence.contract} {b.evidence.evidence_hash}"
    if isinstance(b, AssumedApproved):
        return f"assumed approved, to confirm: {b.confirmation_ask}"
    return f"unknown: {b.reason}"


def basis_banner(envelope: DispatchEnvelope) -> str:
    """The banner every artifact built from the envelope must show (D46: a study basis is never
    presented as underwriting)."""
    b = envelope.source.basis
    if isinstance(b, StudyBasisLabel):
        return f"{b.label} ({b.basis_id}). {b.disclosure}"
    return f"UNDERWRITING BASIS ({b.basis_id})"


# --------------------------------------------------------------------------------------------
# References and supersession
# --------------------------------------------------------------------------------------------


class EnvelopeRef(ContractModel):
    """A reference to an IC-3 envelope by content hash, with its source input hash (what IC-8,
    IC-15 and the ledger embed)."""

    kind: Literal["dispatch_envelope_ref"]
    contract: Literal["IC-3"]
    contract_version: Version1x
    envelope_hash: Sha256
    input_hash: Sha256


def envelope_ref(envelope: DispatchEnvelope) -> EnvelopeRef:
    return EnvelopeRef(
        kind="dispatch_envelope_ref",
        contract=CONTRACT_ID,
        contract_version=envelope.contract_version,
        envelope_hash=envelope.content_hash(),
        input_hash=envelope.source.input_hash,
    )


def active_at(
    envelopes: Sequence[DispatchEnvelope], instant: datetime
) -> DispatchEnvelope | NotApplicable:
    """The envelope in force at ``instant`` among one site's envelopes.

    An envelope is in force inside its validity unless a successor that names it in
    ``supersedes`` has started. Refused (``ValueError``), never arbitrated: envelopes of more than
    one site, a successor that starts before the envelope it supersedes, and two envelopes in force
    at once. No envelope in force is ``NotApplicable``: the edge falls back to `fail_safe_terms`
    of the last envelope it held.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    by_hash = {e.content_hash(): e for e in envelopes}
    sites = sorted({e.site_id for e in by_hash.values()})
    if len(sites) > 1:
        raise ValueError(f"active_at reads one site's envelopes, not {sites}")
    for e in by_hash.values():
        prior = by_hash.get(e.supersedes) if isinstance(e.supersedes, str) else None
        if prior is not None and e.valid_from < prior.valid_from:
            raise ValueError(f"{e.envelope_id} starts before the envelope it supersedes")
    started = {
        e.supersedes
        for e in by_hash.values()
        if isinstance(e.supersedes, str) and e.valid_from <= instant
    }
    live = [
        e
        for h, e in sorted(by_hash.items())
        if e.valid_from <= instant < e.valid_until and h not in started
    ]
    if len(live) > 1:
        raise ValueError(
            f"{len(live)} envelopes are in force at {instant.isoformat()} and none supersedes the "
            f"others: {[e.envelope_id for e in live]}"
        )
    if not live:
        return not_applicable(f"no envelope is in force at {instant.isoformat()}")
    return live[0]


# --------------------------------------------------------------------------------------------
# Source checks (WP-SIZE-08, WP-DISP-02, WP-LEDGER-05)
# --------------------------------------------------------------------------------------------


class SourceFinding(ContractModel):
    field: NonEmptyStr
    status: Literal["mismatch", "unverifiable"]
    detail: NonEmptyStr


class SourceCheck(ContractModel):
    """``ok`` only when every compared field matched; ``compared`` names them, so a check that
    compared nothing cannot read as a pass."""

    ok: bool
    compared: tuple[NonEmptyStr, ...]
    findings: tuple[SourceFinding, ...]


def check_source(
    envelope: DispatchEnvelope,
    *,
    quote_input_hash: str | None = None,
    quote_id: str | None = None,
    scenario_set_hash: str | None = None,
    agreement_hash: str | None = None,
) -> SourceCheck:
    """Compare the envelope's source with what the caller holds. An argument left out is not
    compared; at least one must be given. A comparison against an unknown is ``unverifiable``,
    never a pass; against a not-applicable reference it is a ``mismatch``."""
    src = envelope.source
    compared: list[str] = []
    findings: list[SourceFinding] = []

    def compare(field: str, held: str, mine: str | Unknown | NotApplicable) -> None:
        compared.append(field)
        if isinstance(mine, Unknown):
            findings.append(SourceFinding(field=field, status="unverifiable", detail=mine.reason))
        elif isinstance(mine, NotApplicable):
            findings.append(
                SourceFinding(
                    field=field, status="mismatch", detail=f"not applicable: {mine.reason}"
                )
            )
        elif mine != held:
            findings.append(
                SourceFinding(
                    field=field, status="mismatch", detail=f"envelope {mine}, held {held}"
                )
            )

    if quote_input_hash is not None:
        compare("source.input_hash", quote_input_hash, src.input_hash)
    if quote_id is not None:
        compare("source.assessment.quote_id", quote_id, src.assessment.quote_id)
    if scenario_set_hash is not None:
        s = src.scenario_set
        compare(
            "source.scenario_set",
            scenario_set_hash,
            s.scenario_set_hash if isinstance(s, ScenarioSetRef) else s,
        )
    if agreement_hash is not None:
        a = src.connection_agreement
        mine = a.agreement_hash if isinstance(a, ConnectionAgreementRef) else a
        compare("source.connection_agreement", agreement_hash, mine)
    if not compared:
        raise ValueError("check_source needs at least one value to compare")
    return SourceCheck(ok=not findings, compared=tuple(compared), findings=tuple(findings))


class LibraryCheck(ContractModel):
    status: Literal["match", "mismatch", "unverifiable"]
    detail: NonEmptyStr


def check_constraint_library(
    envelope: DispatchEnvelope, *, library_version: str, library_hash: str
) -> LibraryCheck:
    """Dispatch's constraint library against the envelope's pin (WP-DISP-02: the version matches
    or the mismatch is flagged). An unknown pin or pinned hash is ``unverifiable``."""
    pin = envelope.source.constraint_library
    if isinstance(pin, Unknown):
        return LibraryCheck(status="unverifiable", detail=f"no library pinned: {pin.reason}")
    if pin.library_version != library_version:
        return LibraryCheck(
            status="mismatch",
            detail=f"envelope sized with {pin.library_version}, dispatch runs {library_version}",
        )
    if isinstance(pin.library_hash, Unknown):
        return LibraryCheck(
            status="unverifiable",
            detail=f"versions match; hash not pinned: {pin.library_hash.reason}",
        )
    if pin.library_hash != library_hash:
        return LibraryCheck(
            status="mismatch", detail="versions match but the library hashes differ"
        )
    return LibraryCheck(status="match", detail=f"{library_version} {library_hash}")


# --------------------------------------------------------------------------------------------
# Against IC-1: the agreement the import limits rest on
# --------------------------------------------------------------------------------------------


class AgreementReason(StrEnum):
    NO_AGREEMENT_REFERENCE = "no_agreement_reference"
    WRONG_SITE_OR_AGREEMENT = "wrong_site_or_agreement"
    FIRM_IMPORT_UNKNOWN = "firm_import_unknown"
    NON_FIRM_IMPORT_UNKNOWN = "non_firm_import_unknown"
    FAIL_SAFE_ABOVE_FIRM = "fail_safe_above_firm"
    LIMIT_ABOVE_AGREEMENT = "limit_above_agreement"


class AgreementCheck(ContractModel):
    ok: bool
    reasons: tuple[AgreementReason, ...]


def _mw_to_kw_floor(mw: float) -> int:
    """Whole kW at or below an IC-1 MW figure, read as its decimal text (IC-1 writes Python's
    shortest repr), so 0.3 MW is 300 kW and not 299."""
    return floor(Fraction(repr(mw)) * 1000)


def check_against_agreement(
    envelope: DispatchEnvelope, agreement: ConnectionAgreement
) -> AgreementCheck:
    """The envelope's import limits against the IC-1 agreement it names: the fail-safe firm limit
    at or below the agreed firm import, every cell's limit at or below firm plus non-firm. The
    agreement's time-of-day profile is not checked in v1 (its periods are settlement periods 1-48,
    which do not define the 46- and 50-period clock-change days)."""
    ref = envelope.source.connection_agreement
    if not isinstance(ref, ConnectionAgreementRef):
        return AgreementCheck(ok=False, reasons=(AgreementReason.NO_AGREEMENT_REFERENCE,))
    if agreement.site_id != envelope.site_id or ref.agreement_hash != agreement.content_hash():
        return AgreementCheck(ok=False, reasons=(AgreementReason.WRONG_SITE_OR_AGREEMENT,))
    reasons: set[AgreementReason] = set()
    firm, non_firm = agreement.firm_import_mw, agreement.non_firm_import_mw
    if isinstance(firm, Unknown):
        reasons.add(AgreementReason.FIRM_IMPORT_UNKNOWN)
    elif envelope.fail_safe.firm_import_limit_kw > _mw_to_kw_floor(firm):
        reasons.add(AgreementReason.FAIL_SAFE_ABOVE_FIRM)
    if isinstance(non_firm, Unknown):
        reasons.add(AgreementReason.NON_FIRM_IMPORT_UNKNOWN)
    elif not isinstance(firm, Unknown):
        cap = floor((Fraction(repr(firm)) + Fraction(repr(non_firm))) * 1000)
        if any(n.terms.import_limit_kw > cap for n in envelope.terms):
            reasons.add(AgreementReason.LIMIT_ABOVE_AGREEMENT)
    return AgreementCheck(ok=not reasons, reasons=tuple(sorted(reasons)))


# --------------------------------------------------------------------------------------------
# Against IC-11: committed thermal flex is provable against the surface
# --------------------------------------------------------------------------------------------


class SurfaceReason(StrEnum):
    NO_THERMAL_SURFACE = "no_thermal_surface"
    WRONG_SITE_OR_SURFACE = "wrong_site_or_surface"
    #: A non-fixture envelope rests on a synthetic surface.
    SYNTHETIC_SURFACE = "synthetic_surface"
    OUTSIDE_SURFACE_VALIDITY = "outside_surface_validity"
    BINDING_MISMATCH = "binding_mismatch"
    COMMITMENT_IN_UNAVAILABLE = "commitment_in_unavailable"
    COMMITMENT_IN_UNKNOWN = "commitment_in_unknown"
    COMMITMENT_EXCEEDS_DELIVERABLE = "commitment_exceeds_deliverable"


class SurfaceCheck(ContractModel):
    ok: bool
    reasons: tuple[SurfaceReason, ...]


def _surface_cells(
    surface: ThermalFlexAvailability, start: datetime, end: datetime
) -> Iterator[tuple[datetime, object]]:
    """Each IC-11 cell ``[start, end)`` touches, with the instant it is entered, read on the GB
    clock. IC-11 bands are minute-resolution; across a clock change this walks minute by minute."""
    season = {m: s.season_id for s in surface.seasons for m in s.months}
    cells = {(c.season_id, c.band_id): c.availability for c in surface.cells}
    t = start
    while t < end:
        local = t.astimezone(_TZ)
        minute = local.hour * 60 + local.minute
        band = next(b for b in surface.bands if b.start_minute <= minute < b.end_minute)
        yield t, cells[(season[local.month], band.band_id)]
        nxt = t + timedelta(minutes=band.end_minute - minute)
        if nxt.astimezone(_TZ).utcoffset() != local.utcoffset():
            nxt = t + _MINUTE
        t = nxt


def check_against_surface(
    envelope: DispatchEnvelope, surface: ThermalFlexAvailability
) -> SurfaceCheck:
    """Prove every thermal commitment against the IC-11 surface the envelope names.

    For each compiled segment with commitments: the summed kW, at the longest committed duration,
    must be deliverable (the IC-11 curve of the cell an event starts in) from every start in the
    segment; and no minute of ``[segment start, segment end + longest duration)`` may touch an
    ``unavailable`` or ``unknown`` cell (IC-11's whole-event rule). Reasons are sorted; empty is
    a pass.
    """
    th = envelope.thermal
    if not isinstance(th, ThermalEnvelope):
        return SurfaceCheck(ok=False, reasons=(SurfaceReason.NO_THERMAL_SURFACE,))
    if (
        envelope.site_id != surface.site_id
        or th.surface.availability_hash != surface.content_hash()
    ):
        return SurfaceCheck(ok=False, reasons=(SurfaceReason.WRONG_SITE_OR_SURFACE,))
    reasons: set[SurfaceReason] = set()
    if (
        surface.record_class == "synthetic_fixture"
        and envelope.record_class is not RecordClass.SYNTHETIC_FIXTURE
    ):
        reasons.add(SurfaceReason.SYNTHETIC_SURFACE)
    if not (
        surface.valid_from <= envelope.valid_from and envelope.valid_until <= surface.valid_until
    ):
        reasons.add(SurfaceReason.OUTSIDE_SURFACE_VALIDITY)
    if th.binding != surface.limits.binding:
        reasons.add(SurfaceReason.BINDING_MISMATCH)
    timeline = resolve(envelope)
    by_hash = {t.terms_hash: t.terms for t in timeline.terms}
    for seg in timeline_segments(timeline):
        thermal = by_hash[seg.terms_hash].thermal
        if not isinstance(thermal, ThermalTerms) or not thermal.commitments:
            continue
        total = sum(c.reduction_kw for c in thermal.commitments)
        longest = max(c.duration_min for c in thermal.commitments)
        event_end = seg.end + timedelta(minutes=longest)
        if event_end > surface.valid_until:
            reasons.add(SurfaceReason.OUTSIDE_SURFACE_VALIDITY)
        for entered, cell in _surface_cells(surface, seg.start, event_end):
            if isinstance(cell, Unavailable):
                reasons.add(SurfaceReason.COMMITMENT_IN_UNAVAILABLE)
            elif isinstance(cell, Unknown):
                reasons.add(SurfaceReason.COMMITMENT_IN_UNKNOWN)
            elif entered < seg.end:
                kw = deliverable_kw(cell, longest)[0] if isinstance(cell, Flex) else 0
                if total > kw:
                    reasons.add(SurfaceReason.COMMITMENT_EXCEEDS_DELIVERABLE)
    return SurfaceCheck(ok=not reasons, reasons=tuple(sorted(reasons)))


# --------------------------------------------------------------------------------------------
# Schedules against the envelope (WP-DISP-02's property test, the edge's check)
# --------------------------------------------------------------------------------------------


class TemperatureRange(ContractModel):
    """The lowest and highest temperature a schedule expects over a period, at a point."""

    point: Literal["it_inlet", "supply_air"]
    low_tenths_c: TenthsC
    high_tenths_c: TenthsC

    @model_validator(mode="after")
    def _ordered(self) -> TemperatureRange:
        if self.high_tenths_c < self.low_tenths_c:
            raise ValueError("high_tenths_c must not be below low_tenths_c")
        return self


class ServiceDelivery(ContractModel):
    service_id: _ID
    direction: Direction
    kw: PosKw


class ScheduledPeriod(ContractModel):
    """One period of a dispatch schedule, inside one half-hour of the grid (a 5-minute MPC step or
    a whole settlement period). Battery power is split into merchant and per-service delivery."""

    start: UtcInstant
    end: UtcInstant
    merchant_discharge_kw: Kw
    merchant_charge_kw: Kw
    service_delivery: tuple[ServiceDelivery, ...]
    site_import_kw: Kw
    site_export_kw: Kw
    soe_start_kwh: Kwh
    soe_end_kwh: Kwh
    temperature: TemperatureRange | NotApplicable

    @model_validator(mode="after")
    def _shape(self) -> ScheduledPeriod:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        for t in (self.start, self.end):
            if t.second or t.microsecond:
                raise ValueError("a period starts and ends on whole minutes")
        first = self.start.replace(minute=self.start.minute - self.start.minute % RESOLUTION_MIN)
        if self.end - first > _HALF_HOUR:
            raise ValueError("a period lies inside one half-hour of the grid")
        keys = [(d.service_id, d.direction.value) for d in self.service_delivery]
        if keys != sorted(set(keys)):
            raise ValueError("service_delivery must be sorted by (service_id, direction), unique")
        return self


class ViolationCode(StrEnum):
    """In the spec §8 safety hierarchy order (`RANK`): thermal limits, then connection compliance,
    then the resilience reserve, then contracted delivery, then merchant."""

    OUTSIDE_VALIDITY = "outside_validity"
    THERMAL_BAND = "thermal_band"
    THERMAL_NOT_STATED = "thermal_not_stated"
    IMPORT_LIMIT = "import_limit"
    EXPORT_LIMIT = "export_limit"
    SOE_BELOW_RESILIENCE_FLOOR = "soe_below_resilience_floor"
    SOE_BELOW_SERVICE_FLOOR = "soe_below_service_floor"
    SOE_ABOVE_CEILING = "soe_above_ceiling"
    SERVICE_OVER_SLICE = "service_over_slice"
    MERCHANT_OVER_SLICE = "merchant_over_slice"


#: Rank in the safety hierarchy (lower is more severe). 0: no envelope is in force (fail safe).
RANK: Final[dict[ViolationCode, int]] = {
    ViolationCode.OUTSIDE_VALIDITY: 0,
    ViolationCode.THERMAL_BAND: 1,
    ViolationCode.THERMAL_NOT_STATED: 1,
    ViolationCode.IMPORT_LIMIT: 2,
    ViolationCode.EXPORT_LIMIT: 2,
    ViolationCode.SOE_BELOW_RESILIENCE_FLOOR: 3,
    ViolationCode.SOE_BELOW_SERVICE_FLOOR: 4,
    ViolationCode.SOE_ABOVE_CEILING: 4,
    ViolationCode.SERVICE_OVER_SLICE: 4,
    ViolationCode.MERCHANT_OVER_SLICE: 5,
}
_CODE_ORDER: Final = {c: i for i, c in enumerate(ViolationCode)}


class Violation(ContractModel):
    period: Annotated[int, Field(ge=0)]
    code: ViolationCode
    rank: Annotated[int, Field(ge=0)]
    detail: NonEmptyStr


class ScheduleCheck(ContractModel):
    """``violations`` are ordered by safety rank, then period, then code; empty means the schedule
    satisfies the envelope."""

    ok: bool
    violations: tuple[Violation, ...]


def _period_violations(
    envelope: DispatchEnvelope, p: ScheduledPeriod
) -> list[tuple[ViolationCode, str]]:
    if not (envelope.valid_from <= p.start and p.end <= envelope.valid_until):
        return [(ViolationCode.OUTSIDE_VALIDITY, "the period is outside the envelope's validity")]
    t = terms_at(envelope, p.start)
    out: list[tuple[ViolationCode, str]] = []
    if isinstance(t.thermal, ThermalTerms):
        band, temp = t.thermal.band, p.temperature
        if isinstance(temp, NotApplicable):
            out.append((ViolationCode.THERMAL_NOT_STATED, "the cell has a thermal band"))
        elif (
            temp.point != band.point.value
            or temp.low_tenths_c < band.min_tenths_c
            or temp.high_tenths_c > band.max_tenths_c
        ):
            out.append(
                (ViolationCode.THERMAL_BAND, f"outside {band.min_tenths_c}-{band.max_tenths_c}")
            )
    if p.site_import_kw > t.import_limit_kw:
        out.append((ViolationCode.IMPORT_LIMIT, f"{p.site_import_kw} > {t.import_limit_kw} kW"))
    if p.site_export_kw > t.export_limit_kw:
        out.append((ViolationCode.EXPORT_LIMIT, f"{p.site_export_kw} > {t.export_limit_kw} kW"))
    limits = flat_limits(t)
    low, high = min(p.soe_start_kwh, p.soe_end_kwh), max(p.soe_start_kwh, p.soe_end_kwh)
    if low < t.energy.resilience_floor_kwh:
        out.append(
            (
                ViolationCode.SOE_BELOW_RESILIENCE_FLOOR,
                f"{low} < {t.energy.resilience_floor_kwh} kWh",
            )
        )
    elif low < limits.soe_floor_kwh:
        out.append((ViolationCode.SOE_BELOW_SERVICE_FLOOR, f"{low} < {limits.soe_floor_kwh} kWh"))
    if high > limits.soe_ceiling_kwh:
        out.append((ViolationCode.SOE_ABOVE_CEILING, f"{high} > {limits.soe_ceiling_kwh} kWh"))
    for d in p.service_delivery:
        allowed = _part(t, d.direction).slice_kw(d.service_id)
        if d.kw > allowed:
            out.append(
                (
                    ViolationCode.SERVICE_OVER_SLICE,
                    f"{d.service_id} {d.direction.value} {d.kw} > {allowed} kW",
                )
            )
    if p.merchant_discharge_kw > t.discharge.merchant_kw:
        out.append(
            (
                ViolationCode.MERCHANT_OVER_SLICE,
                f"discharge {p.merchant_discharge_kw} > {t.discharge.merchant_kw} kW",
            )
        )
    if p.merchant_charge_kw > t.charge.merchant_kw:
        out.append(
            (
                ViolationCode.MERCHANT_OVER_SLICE,
                f"charge {p.merchant_charge_kw} > {t.charge.merchant_kw} kW",
            )
        )
    return out


def check_schedule(
    envelope: DispatchEnvelope, schedule: Sequence[ScheduledPeriod]
) -> ScheduleCheck:
    """Every violation of the envelope by ``schedule``, most severe first (spec §8 hierarchy)."""
    found = [
        Violation(period=i, code=code, rank=RANK[code], detail=detail)
        for i, p in enumerate(schedule)
        for code, detail in _period_violations(envelope, p)
    ]
    found.sort(key=lambda v: (v.rank, v.period, _CODE_ORDER[v.code], v.detail))
    return ScheduleCheck(ok=not found, violations=tuple(found))
