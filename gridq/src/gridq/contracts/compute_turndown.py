"""IC-9 compute turn-down declaration: frozen contract, version 1.0.0 (roadmap WP-SITE-07, B8-01).

A GPU operator *declares* the turn-down its workloads can offer at one site: how many kW of IT load,
for how long, on how much notice, at what price, under which service-level class, and when.
Declaring is optional and never assumed (A2-04): with no declaration there is no instance, and the
data-centre load model leaves IT load untouched (spec §14, "IT load untouched" is the default).
Producer: the GPU operator, or an orchestrator adapter (WP-SITE-08, B8-02). Consumers: the
co-optimising sizer A5 (WP-SIZE-11), live dispatch B2 in shadow mode, IC-3, and the
data-centre load model's on-request turn-down overlay.

Spec → this contract (decisions D-NEW-SITE07-1 to -6):

=====================================  =======================================================
spec (§10A IC-9, B8-01)                field
=====================================  =======================================================
"MW"                                   ``envelopes[*].curve`` (`AbsoluteCurve` kW or
                                       `RelativeCurve` basis points of running IT load)
"duration"                             ``curve.points[*].max_hold_min``
"notice"                               ``envelopes[*].response`` (`ResponseTerms`, seconds)
"price"                                ``envelopes[*].price`` (`PriceOffer` or ``unknown``)
"SLA class"                            ``sla_classes`` (`SlaClass`), named by
                                       ``envelopes[*].sla_class_id``
"validity"                             ``valid_from``, ``valid_until`` (half-open),
                                       ``availability`` (the GB-clock template and dated
                                       overrides), ``resolved_hash``, ``supersedes``
=====================================  =======================================================

**Conditional, never firm floor.** A declared kW is the operator's commitment, but it is a
resource offered by a third party's workloads: every consumer classifies IC-9 kW as
CONDITIONAL (B8-03), outside the priced floor. The contract carries no firmness field and no
validator or reader refuses, zeroes or ranks a revenue stream (D57): the only rule it enforces
between offers is arithmetic, so the same kW is never counted twice (stacking, the shared
call budget of an SLA class, and the retained IT floor). Stage L4 is advisory and shadow only
(ADR-D2): there is no instruction or acknowledgement message in this version.

**Measured at IT load.** Every turn-down quantity is IT electrical load. What it saves at the
meter (UPS, distribution, cooling) is the load model's job (its turn-down overlay).

**Exact integers.** Power integer kW, shares integer basis points (1/10,000), hold durations
integer minutes (1 to `MAX_HOLD_MIN`), notice and ramp integer seconds, money integer pence.
A producer rounds to the safe side: kW, basis points and hold durations down, notice and ramp up,
and refuses rather than emits a zero offer.

**Time base.** A recurring template on the GB clock (`CLOCK`): ``seasons`` partition the months,
``day_groups`` partition the ISO week, 30-minute ``bands`` partition the local day, and each
cell's state is `Offered` (named envelopes), `Unavailable` (with a reason) or ``unknown``.
Dated UTC ``overrides`` on the half-hour grid replace the state wholesale. `resolve` compiles the
declaration onto UTC half-hours (`ResolvedTimeline`); ``resolved_hash`` binds that compilation,
so two implementations that read a clock change differently fail conformance instead of
disagreeing silently. `declared_at_local` reads the declaration at a naive local half-hour (the
load model's time basis) under PEP 495 ``fold=0``: on the spring-forward day the missing local
hour 01:00 to 02:00 reads UTC 01:00 to 02:00, which local 02:00 to 03:00 also reads; on the
fall-back day the repeated local hour is read at its first occurrence (BST).

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every instance); JSON Schemas frozen at
``schemas/ic9_compute_turndown_declaration.v1.0.0.schema.json`` and
``schemas/ic9_resolved_timeline.v1.0.0.schema.json``; human-readable specification generated at
``specs/ic9_compute_turndown_declaration.v1.0.0.md``; git tag ``GIT_TAG``. References IC-4
evidence through `EvidenceRef` (the 1.x range).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from fractions import Fraction
from itertools import combinations
from typing import Annotated, Any, Final, Literal, final
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    UtcInstant,
    Version1x,
    content_hash,
    not_applicable,
    unknown,
)
from gridq.contracts.evidence import EvidenceObject, EvidenceRef, EvidenceType, resolve_ref

__all__ = [
    "BASIS_POINTS",
    "CLOCK",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "GIT_TAG",
    "MAX_HOLD_MIN",
    "RESOLUTION_MIN",
    "SUPPORTED_VERSIONS",
    "AbsoluteCurve",
    "AbsolutePoint",
    "AvailabilityPrice",
    "AvailabilityTemplate",
    "BudgetUse",
    "CallCheck",
    "CallLimits",
    "CallReason",
    "ComputeTurndownDeclaration",
    "DayGroup",
    "DeclarationRef",
    "DeclaredBasis",
    "DurationBudget",
    "EventBudget",
    "EventFee",
    "FirmOffer",
    "Indicative",
    "ItReference",
    "Layer",
    "MeteredBasis",
    "MinimumRest",
    "NoReturn",
    "NotInForce",
    "Offered",
    "OfferedAnswer",
    "Override",
    "PastCall",
    "PriceOffer",
    "ProposedCall",
    "ReboundSize",
    "RelativeCurve",
    "RelativePoint",
    "ResolvedTimeline",
    "ResponseTerms",
    "ReturnsLater",
    "Season",
    "SlaClass",
    "StackAnswer",
    "Stacking",
    "SyntheticPlaceholder",
    "TemplateCell",
    "TimeBand",
    "TimeBase",
    "TimelineStep",
    "TrialledBasis",
    "TurndownEnvelope",
    "Unavailable",
    "UnavailableReason",
    "UtilisationPrice",
    "WorkloadAction",
    "active_at",
    "availability_cost_pence",
    "call_budget_used",
    "call_cost_pence",
    "check_call",
    "declaration_ref",
    "declared_at_local",
    "deliverable_against",
    "deliverable_kw",
    "offered_at",
    "pence_to_pounds",
    "price_basis_label",
    "resolve",
    "resolve_sla_evidence",
    "resolved_hash_of",
    "stack_at",
    "timeline_matches",
    "timeline_state_at",
    "turn_down_kw",
]

CONTRACT_ID: Final = "IC-9"
CONTRACT_VERSION: Final = "1.0.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0",)
GIT_TAG: Final = "contracts/ic9-compute-turndown/v1.0.0"

#: The clock every template band is read on (GB local time, as IC-3 and IC-11).
CLOCK: Final = "Europe/London"
#: The grid: validity, overrides, bands and the compiled timeline all fall on 30-minute edges.
RESOLUTION_MIN: Final = 30
#: The longest hold a curve may state (a day, IC-11's cap).
MAX_HOLD_MIN: Final = 1440
#: A share of running IT load is stated in basis points: 10,000 is all of it.
BASIS_POINTS: Final = 10_000

_MINUTES_PER_DAY: Final = 1440
_HALF_HOUR: Final = timedelta(minutes=RESOLUTION_MIN)
_TZ: Final = ZoneInfo(CLOCK)
_SYNTHETIC: Final = "SYNTHETIC-"

Kw = Annotated[int, Field(ge=0)]
PosKw = Annotated[int, Field(ge=1)]
PosInt = Annotated[int, Field(ge=1)]
Seconds = Annotated[int, Field(ge=0)]
HoldMin = Annotated[int, Field(ge=1, le=MAX_HOLD_MIN)]
BasisPoints = Annotated[int, Field(ge=1, le=BASIS_POINTS)]
Pence = Annotated[int, Field(ge=1)]
_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


# --------------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------------


class WorkloadAction(StrEnum):
    """What the operator does to its workloads to turn down. Closed; a minor version extends it."""

    #: Paused or checkpointed: the work resumes later, so its energy comes back.
    DEFERRED = "deferred"
    #: Power-capped or throttled: the work runs slower.
    SLOWED = "slowed"
    #: Migrated off this site: the energy does not come back here.
    MOVED = "moved"
    #: Shed: the work is not done.
    DROPPED = "dropped"


class UnavailableReason(StrEnum):
    """Why a cell or an override offers nothing (as distinct from ``unknown``)."""

    #: The workloads in this window may not be turned down.
    WORKLOAD_PROTECTED = "workload_protected"
    #: Outside the window the operator agreed to offer.
    OUTSIDE_AGREED_WINDOW = "outside_agreed_window"
    #: Withdrawn by the operator.
    OPERATOR_WITHDRAWN = "operator_withdrawn"
    MAINTENANCE = "maintenance"


class CallReason(StrEnum):
    """Why a proposed call does not fit the declaration (`check_call`). Read from the contract
    alone, over the whole event ``[start, start + duration)``; the order here is the order of
    ``CallCheck.reasons``."""

    #: The event is not inside ``[valid_from, valid_until)``.
    OUTSIDE_VALIDITY = "outside_validity"
    #: A half-hour of the event is a template ``unavailable`` cell, or does not offer a named
    #: envelope.
    NOT_OFFERED = "not_offered"
    #: A half-hour of the event is withdrawn by a dated ``unavailable`` override.
    BLACKOUT = "blackout"
    #: A half-hour of the event is ``unknown``.
    AVAILABILITY_UNKNOWN = "availability_unknown"
    #: Less notice than a named envelope's ``min_notice_s``.
    NOTICE_TOO_SHORT = "notice_too_short"
    #: Longer than a named envelope's longest hold.
    DURATION_EXCEEDS_CURVE = "duration_exceeds_curve"
    #: More kW than the named envelopes deliver for the duration at the running IT load.
    KW_EXCEEDS_CURVE = "kw_exceeds_curve"
    #: Two named envelopes, or a named envelope and one in an overlapping call, are alternatives
    #: (one exclusion group), or the same envelope is called twice at once.
    EXCLUSION_CONFLICT = "exclusion_conflict"
    #: With the overlapping calls, more than ``stacking.max_concurrent_turn_down_kw``.
    STACK_CAP_EXCEEDED = "stack_cap_exceeded"
    #: With the overlapping calls, the IT load left would be below ``retained_it_floor_kw``.
    RETAINED_IT_FLOOR = "retained_it_floor"
    #: The SLA class's event count in its window would be exceeded.
    EVENT_LIMIT = "event_limit"
    #: Less rest since the SLA class's last event than its ``min_rest``.
    REST_TOO_SHORT = "rest_too_short"
    #: The SLA class's cumulative minutes in its window would be exceeded.
    BUDGET_EXHAUSTED = "budget_exhausted"


# --------------------------------------------------------------------------------------------
# The time base: seasons x day groups x bands, dated overrides (IC-3 names)
# --------------------------------------------------------------------------------------------


class Season(ContractModel):
    """A set of calendar months. The declaration's seasons partition the twelve months."""

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


class Offered(ContractModel):
    """The named envelopes are offered (each envelope is a menu item; `stack_at` combines them)."""

    kind: Literal["offered"]
    envelope_ids: tuple[_ID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _sorted(self) -> Offered:
        if list(self.envelope_ids) != sorted(set(self.envelope_ids)):
            raise ValueError("envelope_ids must be sorted with no repeats")
        return self


class Unavailable(ContractModel):
    """Nothing is offered, for a stated reason. A template cell reads as ``not_offered``; a dated
    override reads as ``blackout`` (`CallReason`)."""

    kind: Literal["unavailable"]
    reason_code: UnavailableReason


CellState = Annotated[Offered | Unavailable | Unknown, Field(discriminator="kind")]


class TemplateCell(ContractModel):
    """The state of one (season, day group, band) of the recurring template."""

    season_id: _ID
    day_group_id: _ID
    band_id: _ID
    state: CellState


class Override(ContractModel):
    """Replaces the template's state wholesale over ``[start, end)`` (UTC, on the grid): a dated
    withdrawal (``unavailable``: a blackout), a dated offer, or a dated ``unknown``. Never a
    field-by-field merge."""

    override_id: _ID
    start: UtcInstant
    end: UtcInstant
    state: CellState
    reason: NonEmptyStr


class AvailabilityTemplate(ContractModel):
    """When each envelope is offered: the recurring GB-clock template and the dated overrides.

    ``seasons`` partition the months (sorted by first month), ``day_groups`` the ISO week (sorted
    by first weekday), ``bands`` the local day (sorted, contiguous, 0 to 1440). ``cells`` hold
    exactly one state per (season, day group, band), in that order, and the template is
    canonical: no two bands, day groups or seasons carry the same states everywhere.
    """

    seasons: tuple[Season, ...] = Field(min_length=1)
    day_groups: tuple[DayGroup, ...] = Field(min_length=1)
    bands: tuple[TimeBand, ...] = Field(min_length=1)
    cells: tuple[TemplateCell, ...] = Field(min_length=1)
    overrides: tuple[Override, ...]

    @model_validator(mode="after")
    def _partitions(self) -> AvailabilityTemplate:
        months = [m for s in self.seasons for m in s.months]
        if sorted(months) != list(range(1, 13)):
            raise ValueError("seasons must partition the twelve months: each month exactly once")
        if [s.months[0] for s in self.seasons] != sorted(s.months[0] for s in self.seasons):
            raise ValueError("seasons must be sorted by their first month (one canonical order)")
        days = [d for g in self.day_groups for d in g.iso_weekdays]
        if sorted(days) != list(range(1, 8)):
            raise ValueError("day_groups must partition the ISO week: each weekday exactly once")
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
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{what} values must be unique")
        expected = [
            (s.season_id, g.day_group_id, b.band_id)
            for s in self.seasons
            for g in self.day_groups
            for b in self.bands
        ]
        got = [(c.season_id, c.day_group_id, c.band_id) for c in self.cells]
        if got != expected:
            raise ValueError(
                "cells must hold exactly one state per (season, day group, band), in that order; "
                "a cell nobody assessed is an explicit unknown, never missing"
            )
        _check_canonical_template(self)
        return self


def _check_canonical_template(tpl: AvailabilityTemplate) -> None:
    """One encoding per policy: no band edge, day group or season the states do not need."""
    row = {(c.season_id, c.day_group_id, c.band_id): c.state for c in tpl.cells}
    seasons = [s.season_id for s in tpl.seasons]
    groups = [g.day_group_id for g in tpl.day_groups]
    bands = [b.band_id for b in tpl.bands]
    for b1, b2 in zip(bands, bands[1:], strict=False):
        if all(row[(s, g, b1)] == row[(s, g, b2)] for s in seasons for g in groups):
            raise ValueError(f"bands {b1} and {b2} carry the same states everywhere: merge them")
    for g1, g2 in combinations(groups, 2):
        if all(row[(s, g1, b)] == row[(s, g2, b)] for s in seasons for b in bands):
            raise ValueError(
                f"day groups {g1} and {g2} carry the same states everywhere: merge them"
            )
    for s1, s2 in combinations(seasons, 2):
        if all(row[(s1, g, b)] == row[(s2, g, b)] for g in groups for b in bands):
            raise ValueError(f"seasons {s1} and {s2} carry the same states everywhere: merge them")


def _on_grid(t: datetime) -> bool:
    return t.second == 0 and t.microsecond == 0 and t.minute % RESOLUTION_MIN == 0


# --------------------------------------------------------------------------------------------
# The envelope: response, curve, rebound, price
# --------------------------------------------------------------------------------------------


class ResponseTerms(ContractModel):
    """How fast the turn-down arrives: notice needed before the event starts, and seconds from the
    start to the full reduction. Seconds, because compute responds inside a minute; a consumer on
    minutes reads ``ceil(s / 60)``. A hold counts from the start."""

    kind: Literal["response_terms"]
    min_notice_s: Seconds
    ramp_s: Seconds


def _pareto(points: Sequence[tuple[int, int]], what: str) -> None:
    holds = [h for h, _ in points]
    amounts = [a for _, a in points]
    if any(b <= a for a, b in zip(holds, holds[1:], strict=False)):
        raise ValueError(f"{what}: max_hold_min must strictly increase")
    if any(b >= a for a, b in zip(amounts, amounts[1:], strict=False)):
        raise ValueError(
            f"{what}: the amount must strictly decrease as the hold lengthens (a point that does "
            "not lower it is redundant): one encoding per curve"
        )


class AbsolutePoint(ContractModel):
    """``turn_down_kw`` of IT load can be held for up to ``max_hold_min`` minutes."""

    max_hold_min: HoldMin
    turn_down_kw: PosKw


class AbsoluteCurve(ContractModel):
    """A duration curve in kW, shortest hold first: durations strictly up, kW strictly down."""

    kind: Literal["absolute_kw"]
    points: tuple[AbsolutePoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_encoding(self) -> AbsoluteCurve:
        _pareto([(p.max_hold_min, p.turn_down_kw) for p in self.points], "absolute curve")
        return self


class RelativePoint(ContractModel):
    """``reduction_bp`` basis points of the IT load running before the event can be held for up
    to ``max_hold_min`` minutes."""

    max_hold_min: HoldMin
    reduction_bp: BasisPoints


class RelativeCurve(ContractModel):
    """A duration curve in basis points of running IT load, shortest hold first, with an optional
    kW cap. kW is monotone in basis points at any load, so the curve's Pareto rule holds in kW."""

    kind: Literal["share_of_it_load"]
    points: tuple[RelativePoint, ...] = Field(min_length=1)
    cap_kw: PosKw | NotApplicable

    @model_validator(mode="after")
    def _one_encoding(self) -> RelativeCurve:
        _pareto([(p.max_hold_min, p.reduction_bp) for p in self.points], "relative curve")
        return self


Curve = Annotated[AbsoluteCurve | RelativeCurve, Field(discriminator="kind")]


class ReboundSize(ContractModel):
    """How much comes back after a hold, when the SLA class says energy returns. When it returns
    is the class's ``energy_return``. ``recovery_permille`` is the energy recovered per 1,000 of
    the energy turned down (above 1,000 if recovery costs more than it saved)."""

    kind: Literal["rebound_size"]
    recovery_permille: PosInt | Unknown
    max_rebound_kw: PosKw | Unknown


class AvailabilityPrice(ContractModel):
    """A holding fee: pence per MW of turn-down held available, per hour."""

    kind: Literal["availability_price"]
    pence_per_mw_per_hour: Pence


class UtilisationPrice(ContractModel):
    """An energy fee: pence per MWh of IT load turned down."""

    kind: Literal["utilisation_price"]
    pence_per_mwh: Pence


class EventFee(ContractModel):
    """A fee per called event, in pence."""

    kind: Literal["event_fee"]
    pence: Pence


class Indicative(ContractModel):
    """The operator stated the price on ``stated_on`` without a binding offer document."""

    kind: Literal["indicative"]
    stated_on: IsoDate


class FirmOffer(ContractModel):
    """A binding price, with the offer document as IC-4 evidence."""

    kind: Literal["firm_offer"]
    evidence: EvidenceRef


class SyntheticPlaceholder(ContractModel):
    """A schema-test fixture's price: a placeholder, not an offer. Required exactly when the
    declaration's ``record_class`` is ``synthetic_fixture``."""

    kind: Literal["synthetic_placeholder"]


class PriceOffer(ContractModel):
    """What the operator asks to be paid, by component. A component that is not charged is
    ``not_applicable`` (an amount is never zero); one not yet stated is ``unknown``. At least one
    component is an amount or ``not_applicable`` (all ``unknown`` is the whole price
    ``unknown``). A firm offer has no ``unknown`` component."""

    kind: Literal["price_offer"]
    currency: Literal["GBP"]
    availability: AvailabilityPrice | NotApplicable | Unknown = Field(discriminator="kind")
    utilisation: UtilisationPrice | NotApplicable | Unknown = Field(discriminator="kind")
    per_event: EventFee | NotApplicable | Unknown = Field(discriminator="kind")
    price_basis: Indicative | FirmOffer | SyntheticPlaceholder = Field(discriminator="kind")

    @model_validator(mode="after")
    def _stated(self) -> PriceOffer:
        parts = (self.availability, self.utilisation, self.per_event)
        if all(isinstance(p, Unknown) for p in parts):
            raise ValueError("every component is unknown: declare the whole price unknown")
        if isinstance(self.price_basis, FirmOffer) and any(isinstance(p, Unknown) for p in parts):
            raise ValueError("a firm offer states every component (no unknown)")
        return self


class TurndownEnvelope(ContractModel):
    """One menu item: a duration curve of IT turn-down with its response, price, rebound and SLA
    class. Envelopes in one ``exclusion_group`` are alternatives; any others stack (`stack_at`)."""

    envelope_id: _ID
    sla_class_id: _ID
    response: ResponseTerms
    price: PriceOffer | Unknown = Field(discriminator="kind")
    curve: Curve
    rebound: ReboundSize | NotApplicable | Unknown = Field(discriminator="kind")
    exclusion_group: _ID | NotApplicable

    @model_validator(mode="after")
    def _ramp_inside_hold(self) -> TurndownEnvelope:
        shortest = self.curve.points[0].max_hold_min
        if self.response.ramp_s >= shortest * 60:
            raise ValueError(
                f"envelope {self.envelope_id}: ramp_s must finish inside the shortest hold "
                f"({shortest} min)"
            )
        return self


# --------------------------------------------------------------------------------------------
# The SLA class: what is done to the workload, whether energy returns, and the shared budget
# --------------------------------------------------------------------------------------------


class ReturnsLater(ContractModel):
    """The turned-down energy comes back at this site, within ``return_window_min`` of release."""

    kind: Literal["returns_later"]
    return_window_min: PosInt | Unknown


class NoReturn(ContractModel):
    """The turned-down energy does not come back at this site."""

    kind: Literal["no_return"]


class EventBudget(ContractModel):
    """At most ``max_events`` events start in any ``window_days`` x 24 h."""

    kind: Literal["event_budget"]
    max_events: PosInt
    window_days: PosInt


class DurationBudget(ContractModel):
    """At most ``max_minutes`` of hold in events starting in any ``window_days`` x 24 h."""

    kind: Literal["duration_budget"]
    max_minutes: PosInt
    window_days: PosInt


class MinimumRest(ContractModel):
    """At least ``minutes`` from the end of one event of the class to the start of the next."""

    kind: Literal["minimum_rest"]
    minutes: PosInt


class CallLimits(ContractModel):
    """The SLA class's budget, shared by every envelope that names the class. A limit the SLA does
    not have is ``not_applicable``; one not known is ``unknown`` (never checked, never assumed)."""

    kind: Literal["call_limits"]
    max_events: EventBudget | NotApplicable | Unknown = Field(discriminator="kind")
    max_cumulative: DurationBudget | NotApplicable | Unknown = Field(discriminator="kind")
    min_rest: MinimumRest | NotApplicable | Unknown = Field(discriminator="kind")


class SlaClass(ContractModel):
    """A service-level class, declared once and named by envelopes.

    ``operator_label`` is the operator's own name for it: opaque, never parsed, compared or
    ranked. ``workload_action`` constrains ``energy_return``: deferred work returns (not
    ``no_return``); moved or dropped work does not return here (not ``returns_later``); slowed
    work may do either. There is no rank and no firmness: protection is the absence of an
    envelope, and every IC-9 kW is CONDITIONAL to a consumer.
    """

    class_id: _ID
    operator_label: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    workload_action: WorkloadAction | Unknown
    energy_return: ReturnsLater | NoReturn | Unknown = Field(discriminator="kind")
    call_limits: CallLimits
    #: The SLA document (IC-4 ``service_level_agreement``), or unknown: IC-9 never requires one.
    sla_evidence: EvidenceRef | Unknown

    @model_validator(mode="after")
    def _action_bounds_return(self) -> SlaClass:
        action, ret = self.workload_action, self.energy_return
        if action == WorkloadAction.DEFERRED and isinstance(ret, NoReturn):
            raise ValueError(f"class {self.class_id}: deferred work returns (not no_return)")
        if action in (WorkloadAction.MOVED, WorkloadAction.DROPPED) and isinstance(
            ret, ReturnsLater
        ):
            raise ValueError(
                f"class {self.class_id}: {action} work does not return at this site "
                "(not returns_later)"
            )
        return self


# --------------------------------------------------------------------------------------------
# Declaration-level blocks: stacking, IT reference, basis
# --------------------------------------------------------------------------------------------


class Stacking(ContractModel):
    """How the offered envelopes combine. ``merit_order`` lists every envelope once: `stack_at`
    takes them in that order, one per exclusion group, until ``max_concurrent_turn_down_kw``."""

    kind: Literal["stacking"]
    merit_order: tuple[_ID, ...] = Field(min_length=1)
    max_concurrent_turn_down_kw: PosKw


class ItReference(ContractModel):
    """The IT load the operator never turns down below, whatever is called."""

    kind: Literal["it_reference"]
    retained_it_floor_kw: Kw


def _sorted_unique_refs(refs: tuple[EvidenceRef, ...], what: str) -> None:
    hashes = [r.evidence_hash for r in refs]
    if hashes != sorted(set(hashes)):
        raise ValueError(f"{what} must be sorted by evidence_hash with no repeats")


class DeclaredBasis(ContractModel):
    """The operator states the envelopes from its own knowledge of its workloads; supporting IC-4
    evidence may be listed (sorted by hash; may be empty)."""

    kind: Literal["operator_declared"]
    supporting_evidence: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def _canonical(self) -> DeclaredBasis:
        _sorted_unique_refs(self.supporting_evidence, "supporting_evidence")
        return self


class TrialledBasis(ContractModel):
    """Observed in a bounded trial of turn-down events against a baseline method fixed in advance
    (IC-11's pattern). Requires IC-4 evidence: the trial records and the baseline method."""

    kind: Literal["trialled"]
    trial_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    baseline_method: EvidenceRef
    observed_from: UtcInstant
    observed_until: UtcInstant
    n_events: PosInt

    @model_validator(mode="after")
    def _canonical(self) -> TrialledBasis:
        _sorted_unique_refs(self.trial_evidence, "trial_evidence")
        if self.observed_until <= self.observed_from:
            raise ValueError("observed_until must be after observed_from")
        return self


class MeteredBasis(ContractModel):
    """Measured on IT metering across real events against a baseline method fixed in advance.
    Requires IC-4 evidence: the meter records and the baseline method."""

    kind: Literal["metered"]
    meter_evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    baseline_method: EvidenceRef
    observed_from: UtcInstant
    observed_until: UtcInstant
    n_events: PosInt

    @model_validator(mode="after")
    def _canonical(self) -> MeteredBasis:
        _sorted_unique_refs(self.meter_evidence, "meter_evidence")
        if self.observed_until <= self.observed_from:
            raise ValueError("observed_until must be after observed_from")
        return self


# --------------------------------------------------------------------------------------------
# The compiled timeline
# --------------------------------------------------------------------------------------------


class TimelineStep(ContractModel):
    """From ``start`` to the next step (or the validity's end), ``state`` holds, from the template
    or from a dated override (``origin``: a blackout and a template gap read differently)."""

    start: UtcInstant
    origin: Literal["template", "override"]
    state: CellState


@final
class ResolvedTimeline(ContractModel):
    """IC-9 v1.0.0: a declaration's availability compiled onto UTC half-hours (`resolve`).
    Step-encoded: a step starts wherever the state or its origin changes. Read with no clock
    logic (`timeline_state_at`); trusted by ``content_hash() == declaration.resolved_hash``."""

    contract: Literal["IC-9"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION
    kind: Literal["resolved_timeline"]

    site_id: NonEmptyStr
    declaration_id: _ID
    valid_from: UtcInstant
    valid_until: UtcInstant
    resolution_min: Literal[30]
    n_periods: PosInt
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
        if self.steps[0].start != self.valid_from:
            raise ValueError("the first step starts at valid_from")
        for s in self.steps:
            if not _on_grid(s.start):
                raise ValueError("every step starts on the 30-minute UTC grid")
        for a, b in zip(self.steps, self.steps[1:], strict=False):
            if b.start <= a.start:
                raise ValueError("step starts must strictly increase")
            if (b.origin, b.state) == (a.origin, a.state):
                raise ValueError("adjacent steps with the same state and origin are one step")
        if self.steps[-1].start >= self.valid_until:
            raise ValueError("a step starts after the validity ends")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------------------------


@final
class ComputeTurndownDeclaration(ContractModel):
    """IC-9 v1.0.0: the turn-down one GPU operator declares at one site over one validity.

    ``envelopes`` are the menu (sorted by ``envelope_id``), ``sla_classes`` the service classes
    they name (sorted by ``class_id``; each named by at least one envelope), ``availability``
    says which envelopes are offered at every half-hour of ``[valid_from, valid_until)``, and
    ``resolved_hash`` binds its compilation (`resolve`). ``stacking`` and ``it_reference`` are
    the arithmetic that stops one kW being counted twice. Every envelope is offered somewhere.
    """

    contract: Literal["IC-9"] = CONTRACT_ID
    contract_version: Literal["1.0.0"] = CONTRACT_VERSION

    #: ``synthetic_fixture`` marks schema-test data: the site, operator, SLA class ids and labels
    #: start ``SYNTHETIC-`` and every price is a `SyntheticPlaceholder`. No consumer presents
    #: it as an operator's offer (the load model binds it with a SYNTHETIC note).
    record_class: Literal["registered", "synthetic_fixture"]
    declaration_id: _ID
    site_id: NonEmptyStr
    operator_id: NonEmptyStr
    issued_at: UtcInstant
    valid_from: UtcInstant
    #: Exclusive.
    valid_until: UtcInstant
    #: The content hash of the declaration this one replaces from its ``valid_from``.
    supersedes: Sha256 | NotApplicable
    measurement_point: Literal["it_load"]
    clock: Literal["Europe/London"]

    sla_classes: tuple[SlaClass, ...] = Field(min_length=1)
    envelopes: tuple[TurndownEnvelope, ...] = Field(min_length=1)
    availability: AvailabilityTemplate
    resolved_hash: Sha256
    stacking: Stacking
    it_reference: ItReference
    basis: DeclaredBasis | TrialledBasis | MeteredBasis = Field(discriminator="kind")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError(
            "ComputeTurndownDeclaration is final: a new shape is a new contract version"
        )

    @model_validator(mode="after")
    def _consistent(self) -> ComputeTurndownDeclaration:
        if not (_on_grid(self.valid_from) and _on_grid(self.valid_until)):
            raise ValueError("valid_from and valid_until must fall on the 30-minute UTC grid")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        _check_synthetic(self)
        _check_classes(self)
        _check_stacking(self)
        _check_offers(self)
        _check_overrides(self)
        if resolve(self).content_hash() != self.resolved_hash:
            raise ValueError(
                "resolved_hash is not the hash of this declaration's compiled timeline "
                "(resolved_hash_of gives the right one)"
            )
        return self

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity consumers cite."""
        return content_hash(self)


def _check_synthetic(d: ComputeTurndownDeclaration) -> None:
    synthetic = d.record_class == "synthetic_fixture"
    labels = [("site_id", d.site_id), ("operator_id", d.operator_id)]
    for c in d.sla_classes:
        labels += [(f"class {c.class_id} class_id", c.class_id)]
        labels += [(f"class {c.class_id} operator_label", c.operator_label)]
    for what, text in labels:
        if text.startswith(_SYNTHETIC) != synthetic:
            raise ValueError(
                f"{what} starts with {_SYNTHETIC} exactly when the record is synthetic"
            )
    for e in d.envelopes:
        if isinstance(e.price, PriceOffer):
            placeholder = isinstance(e.price.price_basis, SyntheticPlaceholder)
            if placeholder != synthetic:
                raise ValueError(
                    f"envelope {e.envelope_id}: a price is a synthetic placeholder exactly when "
                    "the record is synthetic"
                )


def _check_classes(d: ComputeTurndownDeclaration) -> None:
    class_ids = [c.class_id for c in d.sla_classes]
    if class_ids != sorted(set(class_ids)):
        raise ValueError("sla_classes must be sorted by class_id with no repeats")
    envelope_ids = [e.envelope_id for e in d.envelopes]
    if envelope_ids != sorted(set(envelope_ids)):
        raise ValueError("envelopes must be sorted by envelope_id with no repeats")
    by_id = {c.class_id: c for c in d.sla_classes}
    for e in d.envelopes:
        if e.sla_class_id not in by_id:
            raise ValueError(f"envelope {e.envelope_id} names no declared class {e.sla_class_id}")
    unused = sorted(set(class_ids) - {e.sla_class_id for e in d.envelopes})
    if unused:
        raise ValueError(f"sla class(es) {unused} are named by no envelope")
    for e in d.envelopes:
        ret = by_id[e.sla_class_id].energy_return
        if isinstance(ret, NoReturn) and not isinstance(e.rebound, NotApplicable):
            raise ValueError(
                f"envelope {e.envelope_id}: its class returns no energy, so rebound is "
                "not_applicable"
            )
        if isinstance(ret, ReturnsLater) and isinstance(e.rebound, NotApplicable):
            raise ValueError(
                f"envelope {e.envelope_id}: its class returns energy, so rebound is a size or "
                "unknown"
            )
        if isinstance(ret, Unknown) and not isinstance(e.rebound, Unknown):
            raise ValueError(
                f"envelope {e.envelope_id}: whether its class returns energy is unknown, so "
                "rebound is unknown"
            )


def _group_of(e: TurndownEnvelope) -> str:
    """The exclusion group, or the envelope's own id when it is in none (a group of one)."""
    g = e.exclusion_group
    return g if isinstance(g, str) else f"\0{e.envelope_id}"


def _check_stacking(d: ComputeTurndownDeclaration) -> None:
    ids = [e.envelope_id for e in d.envelopes]
    if sorted(d.stacking.merit_order) != ids:
        raise ValueError("stacking.merit_order must list every envelope exactly once")
    members: dict[str, list[TurndownEnvelope]] = {}
    for e in d.envelopes:
        if isinstance(e.exclusion_group, str):
            members.setdefault(e.exclusion_group, []).append(e)
    for g, es in sorted(members.items()):
        if len(es) < 2:
            raise ValueError(
                f"exclusion group {g} has one member: an envelope alone is not_applicable"
            )
    if all(isinstance(e.curve, AbsoluteCurve) for e in d.envelopes):
        best: dict[str, int] = {}
        for e in d.envelopes:
            g = _group_of(e)
            best[g] = max(best.get(g, 0), e.curve.points[0].turn_down_kw)  # type: ignore[union-attr]
        if d.stacking.max_concurrent_turn_down_kw > sum(best.values()):
            raise ValueError(
                "stacking.max_concurrent_turn_down_kw is above what the envelopes can ever "
                f"stack to ({sum(best.values())} kW): a cap that cannot bind is not stated"
            )


def _states(tpl: AvailabilityTemplate) -> Iterator[tuple[str, CellState]]:
    for c in tpl.cells:
        yield f"cell ({c.season_id}, {c.day_group_id}, {c.band_id})", c.state
    for o in tpl.overrides:
        yield f"override {o.override_id}", o.state


def _check_offers(d: ComputeTurndownDeclaration) -> None:
    known = {e.envelope_id for e in d.envelopes}
    offered: set[str] = set()
    for where, state in _states(d.availability):
        if isinstance(state, Offered):
            missing = sorted(set(state.envelope_ids) - known)
            if missing:
                raise ValueError(f"{where} offers undeclared envelope(s) {missing}")
            offered.update(state.envelope_ids)
    never = sorted(known - offered)
    if never:
        raise ValueError(f"envelope(s) {never} are offered by no cell or override")


def _check_overrides(d: ComputeTurndownDeclaration) -> None:
    overrides = d.availability.overrides
    ids = [o.override_id for o in overrides]
    if len(set(ids)) != len(ids):
        raise ValueError("override_id values must be unique")
    for o in overrides:
        if not (_on_grid(o.start) and _on_grid(o.end)):
            raise ValueError(f"override {o.override_id} must fall on the 30-minute UTC grid")
        if o.end <= o.start:
            raise ValueError(f"override {o.override_id}: end must be after start")
        if not (d.valid_from <= o.start and o.end <= d.valid_until):
            raise ValueError(f"override {o.override_id} lies outside the validity")
    for a, b in zip(overrides, overrides[1:], strict=False):
        if b.start < a.start:
            raise ValueError("overrides must be sorted by start")
        if b.start < a.end:
            raise ValueError(f"overrides {a.override_id} and {b.override_id} overlap")
        if b.start == a.end and b.state == a.state:
            raise ValueError(
                f"overrides {a.override_id} and {b.override_id} are adjacent with the same state: "
                "one override"
            )


# --------------------------------------------------------------------------------------------
# Resolution: the one place local time is read
# --------------------------------------------------------------------------------------------


class TimeBase(ContractModel):
    """The fields of a declaration that `resolve` reads, alone. A producer cannot validate a
    declaration before it knows ``resolved_hash``, so it takes the hash from its draft first
    (`resolved_hash_of`) and then validates the whole declaration, which re-checks everything."""

    model_config = ConfigDict(extra="ignore")  # merged onto ContractModel's config

    site_id: NonEmptyStr
    declaration_id: _ID
    valid_from: UtcInstant
    valid_until: UtcInstant
    availability: AvailabilityTemplate


class _Template:
    """The template as lookups, built once per resolution."""

    def __init__(self, tpl: AvailabilityTemplate) -> None:
        self.season = {m: s.season_id for s in tpl.seasons for m in s.months}
        self.group = {d: g.day_group_id for g in tpl.day_groups for d in g.iso_weekdays}
        self.bands = tpl.bands
        self.cell = {(c.season_id, c.day_group_id, c.band_id): c.state for c in tpl.cells}

    def at(self, t: datetime) -> tuple[Offered | Unavailable | Unknown, int]:
        """The state of the local cell holding instant ``t``, and the minutes from ``t`` to that
        band's local end."""
        local = t.astimezone(_TZ)
        minute = local.hour * 60 + local.minute
        band = next(b for b in self.bands if b.start_minute <= minute < b.end_minute)
        key = (self.season[local.month], self.group[local.isoweekday()], band.band_id)
        return self.cell[key], band.end_minute - minute


_Segment = tuple[
    datetime, datetime, Literal["template", "override"], Offered | Unavailable | Unknown
]


def _template_segments(tpl: _Template, start: datetime, end: datetime) -> Iterator[_Segment]:
    """UTC segments of ``[start, end)``, each inside one local cell. Steps from band end to band
    end; where the GB offset changes inside a step it walks that step a half-hour at a time (the
    change falls at 01:00 UTC, on the grid, so each half-hour has one offset and one cell)."""
    t = start
    while t < end:
        state, to_band_end = tpl.at(t)
        nxt = min(t + timedelta(minutes=to_band_end), end)
        if nxt.astimezone(_TZ).utcoffset() != t.astimezone(_TZ).utcoffset():
            nxt = t + _HALF_HOUR
        yield t, nxt, "template", state
        t = nxt


def _segments(base: ComputeTurndownDeclaration | TimeBase) -> Iterator[_Segment]:
    tpl = _Template(base.availability)
    t = base.valid_from
    for o in base.availability.overrides:
        if t < o.start:
            yield from _template_segments(tpl, t, o.start)
        yield o.start, o.end, "override", o.state
        t = o.end
    if t < base.valid_until:
        yield from _template_segments(tpl, t, base.valid_until)


def resolve(declaration: ComputeTurndownDeclaration | TimeBase) -> ResolvedTimeline:
    """Compile the availability onto UTC half-hours. Pure and deterministic; the declaration's
    ``resolved_hash`` is ``resolve(declaration).content_hash()``."""
    steps: list[TimelineStep] = []
    for start, _, origin, state in _segments(declaration):
        if steps and (steps[-1].origin, steps[-1].state) == (origin, state):
            continue
        steps.append(TimelineStep(start=start, origin=origin, state=state))
    return ResolvedTimeline(
        kind="resolved_timeline",
        site_id=declaration.site_id,
        declaration_id=declaration.declaration_id,
        valid_from=declaration.valid_from,
        valid_until=declaration.valid_until,
        resolution_min=RESOLUTION_MIN,
        n_periods=(declaration.valid_until - declaration.valid_from) // _HALF_HOUR,
        steps=tuple(steps),
    )


def resolved_hash_of(draft: Mapping[str, Any]) -> str:
    """The ``resolved_hash`` a draft declaration (JSON-mode mapping) must carry. The draft's other
    fields are ignored here; validating the declaration afterwards checks them all."""
    return resolve(TimeBase.model_validate_json(json.dumps(dict(draft)))).content_hash()


def timeline_matches(declaration: ComputeTurndownDeclaration, timeline: ResolvedTimeline) -> bool:
    """True when ``timeline`` is the declaration's compilation (checked by hash alone)."""
    return timeline.content_hash() == declaration.resolved_hash


class OfferedAnswer(ContractModel):
    """The state in force at ``instant`` (UTC) and where it came from."""

    kind: Literal["offered_answer"]
    instant: UtcInstant
    origin: Literal["template", "override"]
    state: CellState


class NotInForce(ContractModel):
    """Nothing is declared at ``instant``: it lies outside the validity."""

    kind: Literal["not_in_force"]
    instant: UtcInstant
    reason: NonEmptyStr


def _require_inside(d: ComputeTurndownDeclaration | ResolvedTimeline, instant: datetime) -> None:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    if not (d.valid_from <= instant < d.valid_until):
        raise ValueError(
            f"{instant.isoformat()} is outside the validity [{d.valid_from}, {d.valid_until})"
        )


def offered_at(declaration: ComputeTurndownDeclaration, instant: datetime) -> OfferedAnswer:
    """The state at an aware ``instant`` inside the validity: an override's if one holds it, else
    the template cell's on the GB clock (the same lookup `resolve` uses)."""
    _require_inside(declaration, instant)
    for o in declaration.availability.overrides:
        if o.start <= instant < o.end:
            return OfferedAnswer(
                kind="offered_answer", instant=instant, origin="override", state=o.state
            )
    state, _ = _Template(declaration.availability).at(instant)
    return OfferedAnswer(kind="offered_answer", instant=instant, origin="template", state=state)


def timeline_state_at(timeline: ResolvedTimeline, instant: datetime) -> OfferedAnswer:
    """The state at ``instant``, read from the compiled timeline (no clock logic)."""
    _require_inside(timeline, instant)
    step = next(s for s in reversed(timeline.steps) if s.start <= instant)
    return OfferedAnswer(
        kind="offered_answer", instant=instant, origin=step.origin, state=step.state
    )


def declared_at_local(
    declaration: ComputeTurndownDeclaration, local_date: date, half_hour: int
) -> OfferedAnswer | NotInForce:
    """The state at a naive local half-hour (the load model's time basis: 48 half-hours a day).

    The naive time ``local_date + 30 x half_hour minutes`` is read on `CLOCK` under PEP 495
    ``fold=0``. On the spring-forward day the missing local 01:00 to 02:00 reads UTC 01:00 to
    02:00, which local 02:00 to 03:00 also reads; on the fall-back day the repeated local 01:00
    to 02:00 reads its first (BST) occurrence, UTC 00:00 to 01:00. A minute ``m`` of the local
    day reads half-hour ``m // 30``. Outside the validity the answer is `NotInForce`.
    """
    if not (0 <= half_hour < _MINUTES_PER_DAY // RESOLUTION_MIN):
        raise ValueError("half_hour must be 0 to 47")
    naive = datetime.combine(local_date, time()) + timedelta(minutes=RESOLUTION_MIN * half_hour)
    instant = naive.replace(tzinfo=_TZ, fold=0).astimezone(UTC)
    if not (declaration.valid_from <= instant < declaration.valid_until):
        return NotInForce(
            kind="not_in_force",
            instant=instant,
            reason=f"{instant.isoformat()} is outside the declaration's validity",
        )
    return offered_at(declaration, instant)


# --------------------------------------------------------------------------------------------
# kW: one reader per question
# --------------------------------------------------------------------------------------------


def _check_load(duration_min: int, it_load_kw: int) -> None:
    if isinstance(duration_min, bool) or not isinstance(duration_min, int):
        raise ValueError("duration_min must be an integer")
    if not (1 <= duration_min <= MAX_HOLD_MIN):
        raise ValueError(f"duration_min must be 1 to {MAX_HOLD_MIN}")
    if isinstance(it_load_kw, bool) or not isinstance(it_load_kw, int) or it_load_kw < 0:
        raise ValueError("it_load_kw must be a non-negative integer (floor a float load first)")


def turn_down_kw(curve: AbsoluteCurve | RelativeCurve, duration_min: int, it_load_kw: int) -> int:
    """kW of IT load the curve turns down for a hold of ``duration_min`` minutes when
    ``it_load_kw`` is running before the event. The first point whose hold covers the duration
    supplies it; beyond the longest hold the answer is 0. Never more than the running load:
    absolute ``min(kW, load)``; relative ``min(cap, floor(bp x load / 10,000))``."""
    _check_load(duration_min, it_load_kw)
    if isinstance(curve, AbsoluteCurve):
        for p in curve.points:
            if p.max_hold_min >= duration_min:
                return min(p.turn_down_kw, it_load_kw)
        return 0
    for q in curve.points:
        if q.max_hold_min >= duration_min:
            kw = q.reduction_bp * it_load_kw // BASIS_POINTS
            return kw if isinstance(curve.cap_kw, NotApplicable) else min(kw, curve.cap_kw)
    return 0


def deliverable_kw(envelope: TurndownEnvelope, duration_min: int, it_load_kw: int) -> int:
    """One envelope alone: `turn_down_kw` of its curve. Combining envelopes is `stack_at`'s."""
    return turn_down_kw(envelope.curve, duration_min, it_load_kw)


def deliverable_against(
    declaration: ComputeTurndownDeclaration, total_kw: int, it_load_kw: int
) -> int:
    """``total_kw`` limited so the IT load left is never below ``retained_it_floor_kw``."""
    floor = declaration.it_reference.retained_it_floor_kw
    return min(total_kw, max(0, it_load_kw - floor))


class Layer(ContractModel):
    """One envelope's share of a stack, and the running total after it."""

    envelope_id: _ID
    kw: PosKw
    cumulative_kw: PosKw


class StackAnswer(ContractModel):
    """The kW deliverable if a call of ``duration_min`` started at ``instant`` with
    ``it_load_kw`` running: the layers (merit order) and their total. Read at the start: whether
    the whole event fits is `check_call`'s question."""

    kind: Literal["stack_answer"]
    instant: UtcInstant
    duration_min: HoldMin
    it_load_kw: Kw
    origin: Literal["template", "override"]
    state: CellState
    layers: tuple[Layer, ...]
    total_kw: Kw


def stack_at(
    declaration: ComputeTurndownDeclaration, instant: datetime, duration_min: int, it_load_kw: int
) -> StackAnswer:
    """The only way to combine envelopes. Takes the envelopes offered at ``instant`` in
    ``stacking.merit_order``; skips one whose exclusion group is already in the stack, or that
    cannot hold for ``duration_min``; converts each with `turn_down_kw` at ``it_load_kw``; and
    stops at ``max_concurrent_turn_down_kw`` and at the retained IT floor
    (`deliverable_against`). Summing curves instead would count alternatives twice and ignore
    the cap."""
    _check_load(duration_min, it_load_kw)
    answer = offered_at(declaration, instant)
    layers: list[Layer] = []
    total = 0
    if isinstance(answer.state, Offered):
        offered = set(answer.state.envelope_ids)
        by_id = {e.envelope_id: e for e in declaration.envelopes}
        limit = deliverable_against(
            declaration, declaration.stacking.max_concurrent_turn_down_kw, it_load_kw
        )
        groups: set[str] = set()
        for envelope_id in declaration.stacking.merit_order:
            if envelope_id not in offered:
                continue
            e = by_id[envelope_id]
            if _group_of(e) in groups:
                continue
            kw = min(deliverable_kw(e, duration_min, it_load_kw), limit - total)
            if kw <= 0:
                continue
            groups.add(_group_of(e))
            total += kw
            layers.append(Layer(envelope_id=envelope_id, kw=kw, cumulative_kw=total))
    return StackAnswer(
        kind="stack_answer",
        instant=instant,
        duration_min=duration_min,
        it_load_kw=it_load_kw,
        origin=answer.origin,
        state=answer.state,
        layers=tuple(layers),
        total_kw=total,
    )


# --------------------------------------------------------------------------------------------
# Calls: the whole event against the declaration and the SLA classes' shared budgets
# --------------------------------------------------------------------------------------------


def _sorted_ids(ids: tuple[str, ...]) -> None:
    if list(ids) != sorted(set(ids)):
        raise ValueError("envelope_ids must be sorted with no repeats")


class ProposedCall(ContractModel):
    """A call a consumer is considering (a function argument, not a wire message: ADR-D2 leaves
    no write path). ``turn_down_kw`` in total across ``envelope_ids``, issued at ``issued_at``,
    from ``start`` for ``duration_min``, with ``it_load_kw`` running before it."""

    kind: Literal["proposed_call"]
    envelope_ids: tuple[_ID, ...] = Field(min_length=1)
    issued_at: UtcInstant
    start: UtcInstant
    duration_min: HoldMin
    turn_down_kw: PosKw
    it_load_kw: Kw

    @model_validator(mode="after")
    def _ids(self) -> ProposedCall:
        _sorted_ids(self.envelope_ids)
        return self


class PastCall(ContractModel):
    """A call already made (the caller's history; the contract holds no state)."""

    kind: Literal["past_call"]
    envelope_ids: tuple[_ID, ...] = Field(min_length=1)
    start: UtcInstant
    duration_min: HoldMin
    turn_down_kw: PosKw

    @model_validator(mode="after")
    def _ids(self) -> PastCall:
        _sorted_ids(self.envelope_ids)
        return self


class BudgetUse(ContractModel):
    """What an SLA class has used as of ``as_of``: events and hold minutes of calls starting in
    each limit's window ``(as_of - window_days x 24 h, as_of]``, and the latest end of any call
    starting at or before ``as_of``. A count whose limit is not an amount is not_applicable."""

    kind: Literal["budget_use"]
    class_id: _ID
    as_of: UtcInstant
    events_in_window: Kw | NotApplicable
    minutes_in_window: Kw | NotApplicable
    last_end: UtcInstant | NotApplicable


def _uses_class(d: ComputeTurndownDeclaration, class_id: str, call: PastCall) -> bool:
    by_id = {e.envelope_id: e for e in d.envelopes}
    return any(by_id[i].sla_class_id == class_id for i in call.envelope_ids if i in by_id)


def call_budget_used(
    declaration: ComputeTurndownDeclaration,
    class_id: str,
    calls: Iterable[PastCall],
    *,
    as_of: datetime,
) -> BudgetUse:
    """The arithmetic of one SLA class's shared budget: every call naming any envelope of the
    class counts once. Reports; never refuses (`check_call` compares it with the limits)."""
    sla = next((c for c in declaration.sla_classes if c.class_id == class_id), None)
    if sla is None:
        raise ValueError(f"no sla class {class_id} in declaration {declaration.declaration_id}")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    mine = [c for c in calls if _uses_class(declaration, class_id, c) and c.start <= as_of]
    limits = sla.call_limits

    def in_window(days: int) -> list[PastCall]:
        return [c for c in mine if as_of - timedelta(days=days) < c.start]

    events: int | NotApplicable = not_applicable("the class has no event budget")
    if isinstance(limits.max_events, EventBudget):
        events = len(in_window(limits.max_events.window_days))
    minutes: int | NotApplicable = not_applicable("the class has no duration budget")
    if isinstance(limits.max_cumulative, DurationBudget):
        minutes = sum(c.duration_min for c in in_window(limits.max_cumulative.window_days))
    ends = [c.start + timedelta(minutes=c.duration_min) for c in mine]
    return BudgetUse(
        kind="budget_use",
        class_id=class_id,
        as_of=as_of,
        events_in_window=events,
        minutes_in_window=minutes,
        last_end=max(ends) if ends else not_applicable("no earlier call of this class"),
    )


class CallCheck(ContractModel):
    """Whether a proposed call fits the declaration. ``reasons`` follow `CallReason`'s order;
    empty means it fits. ``unchecked`` names each limit that is ``unknown`` and so was not
    checked (never assumed either way)."""

    ok: bool
    reasons: tuple[CallReason, ...]
    unchecked: tuple[NonEmptyStr, ...]


def check_call(
    declaration: ComputeTurndownDeclaration,
    call: ProposedCall,
    history: Sequence[PastCall],
) -> CallCheck:
    """Check the whole event ``[start, start + duration)`` against the declaration: validity, the
    state of every half-hour it touches, notice, hold, kW, exclusion, the concurrent cap and the
    retained floor (with the overlapping calls in ``history``), and each named SLA class's shared
    budget over every envelope of the class."""
    by_id = {e.envelope_id: e for e in declaration.envelopes}
    unknown_ids = sorted(set(call.envelope_ids) - set(by_id))
    if unknown_ids:
        raise ValueError(f"the call names undeclared envelope(s) {unknown_ids}")
    named = [by_id[i] for i in call.envelope_ids]
    reasons: set[CallReason] = set()
    unchecked: list[str] = []
    end = call.start + timedelta(minutes=call.duration_min)

    if not (declaration.valid_from <= call.start and end <= declaration.valid_until):
        reasons.add(CallReason.OUTSIDE_VALIDITY)
    t = declaration.valid_from + ((call.start - declaration.valid_from) // _HALF_HOUR) * _HALF_HOUR
    while t < end:
        if declaration.valid_from <= t < declaration.valid_until:
            a = offered_at(declaration, t)
            if isinstance(a.state, Unknown):
                reasons.add(CallReason.AVAILABILITY_UNKNOWN)
            elif isinstance(a.state, Unavailable):
                reasons.add(
                    CallReason.BLACKOUT if a.origin == "override" else CallReason.NOT_OFFERED
                )
            elif not set(call.envelope_ids) <= set(a.state.envelope_ids):
                reasons.add(CallReason.NOT_OFFERED)
        t += _HALF_HOUR

    notice_s = (call.start - call.issued_at).total_seconds()
    if any(notice_s < e.response.min_notice_s for e in named):
        reasons.add(CallReason.NOTICE_TOO_SHORT)
    if any(call.duration_min > e.curve.points[-1].max_hold_min for e in named):
        reasons.add(CallReason.DURATION_EXCEEDS_CURVE)
    capability = sum(deliverable_kw(e, call.duration_min, call.it_load_kw) for e in named)
    if call.turn_down_kw > capability:
        reasons.add(CallReason.KW_EXCEEDS_CURVE)

    overlapping = [
        c
        for c in history
        if c.start < end and call.start < c.start + timedelta(minutes=c.duration_min)
    ]
    groups = [_group_of(e) for e in named]
    busy = {_group_of(by_id[i]) for c in overlapping for i in c.envelope_ids if i in by_id}
    if len(set(groups)) != len(groups) or busy & set(groups):
        reasons.add(CallReason.EXCLUSION_CONFLICT)
    concurrent = sum(c.turn_down_kw for c in overlapping) + call.turn_down_kw
    if concurrent > declaration.stacking.max_concurrent_turn_down_kw:
        reasons.add(CallReason.STACK_CAP_EXCEEDED)
    if concurrent > deliverable_against(declaration, concurrent, call.it_load_kw):
        reasons.add(CallReason.RETAINED_IT_FLOOR)

    classes = sorted({e.sla_class_id for e in named})
    for cid in classes:
        limits = next(c for c in declaration.sla_classes if c.class_id == cid).call_limits
        used = call_budget_used(declaration, cid, history, as_of=call.start)
        where = f"sla_classes[{cid}].call_limits"
        if isinstance(limits.max_events, EventBudget):
            assert isinstance(used.events_in_window, int)
            if used.events_in_window + 1 > limits.max_events.max_events:
                reasons.add(CallReason.EVENT_LIMIT)
        elif isinstance(limits.max_events, Unknown):
            unchecked.append(f"{where}.max_events")
        if isinstance(limits.max_cumulative, DurationBudget):
            assert isinstance(used.minutes_in_window, int)
            if used.minutes_in_window + call.duration_min > limits.max_cumulative.max_minutes:
                reasons.add(CallReason.BUDGET_EXHAUSTED)
        elif isinstance(limits.max_cumulative, Unknown):
            unchecked.append(f"{where}.max_cumulative")
        if isinstance(limits.min_rest, MinimumRest):
            last = used.last_end
            if isinstance(last, datetime) and call.start - last < timedelta(
                minutes=limits.min_rest.minutes
            ):
                reasons.add(CallReason.REST_TOO_SHORT)
        elif isinstance(limits.min_rest, Unknown):
            unchecked.append(f"{where}.min_rest")

    ordered = tuple(r for r in CallReason if r in reasons)
    return CallCheck(ok=not ordered, reasons=ordered, unchecked=tuple(unchecked))


# --------------------------------------------------------------------------------------------
# Price: the operator's ask, exact
# --------------------------------------------------------------------------------------------


def _component(
    part: AvailabilityPrice | UtilisationPrice | EventFee | NotApplicable | Unknown,
) -> int | Unknown:
    if isinstance(part, Unknown):
        return part
    if isinstance(part, NotApplicable):
        return 0
    if isinstance(part, AvailabilityPrice):
        return part.pence_per_mw_per_hour
    if isinstance(part, UtilisationPrice):
        return part.pence_per_mwh
    return part.pence


def call_cost_pence(
    price: PriceOffer | Unknown, *, reduction_kw: int, duration_min: int
) -> Fraction | Unknown:
    """The operator's ask for one called event, in pence: utilisation x kW x minutes / 60,000
    (kW-minutes to MWh) plus the per-event fee. A component not charged counts 0; an unknown one
    (or an unknown price) makes the answer unknown, never zero."""
    if isinstance(price, Unknown):
        return price
    rate, fee = _component(price.utilisation), _component(price.per_event)
    for part in (rate, fee):
        if isinstance(part, Unknown):
            return unknown(f"a component of the price is unknown: {part.reason}")
    assert isinstance(rate, int) and isinstance(fee, int)
    return Fraction(rate * reduction_kw * duration_min, 60_000) + fee


def availability_cost_pence(
    price: PriceOffer | Unknown, *, held_kw: int, held_min: int
) -> Fraction | Unknown:
    """The operator's ask for holding ``held_kw`` available for ``held_min`` minutes, in pence:
    pence per MW per hour x kW x minutes / 60,000."""
    if isinstance(price, Unknown):
        return price
    rate = _component(price.availability)
    if isinstance(rate, Unknown):
        return unknown(f"the availability price is unknown: {rate.reason}")
    return Fraction(rate * held_kw * held_min, 60_000)


def pence_to_pounds(pence: Fraction) -> Fraction:
    return pence / 100


def price_basis_label(price: PriceOffer | Unknown) -> str:
    """How a reader must label the price: indicative, firm (evidenced), or a placeholder."""
    if isinstance(price, Unknown):
        return f"price unknown: {price.reason}"
    basis = price.price_basis
    if isinstance(basis, SyntheticPlaceholder):
        return "SYNTHETIC PLACEHOLDER, not an offer"
    if isinstance(basis, FirmOffer):
        return f"firm offer, evidenced by {basis.evidence.evidence_hash}"
    return f"indicative, stated on {basis.stated_on.isoformat()}; not a binding offer"


# --------------------------------------------------------------------------------------------
# References, supersession, evidence
# --------------------------------------------------------------------------------------------


class DeclarationRef(ContractModel):
    """A reference to an IC-9 declaration by content hash (what IC-3 and WP-SIZE-11 cite)."""

    kind: Literal["compute_turndown_declaration_ref"]
    contract: Literal["IC-9"]
    contract_version: Version1x
    declaration_id: _ID
    declaration_hash: Sha256


def declaration_ref(declaration: ComputeTurndownDeclaration) -> DeclarationRef:
    return DeclarationRef(
        kind="compute_turndown_declaration_ref",
        contract=CONTRACT_ID,
        contract_version=declaration.contract_version,
        declaration_id=declaration.declaration_id,
        declaration_hash=declaration.content_hash(),
    )


def active_at(
    declarations: Sequence[ComputeTurndownDeclaration], instant: datetime
) -> ComputeTurndownDeclaration | NotApplicable:
    """The declaration in force at ``instant`` among one site's declarations (IC-3's rule).

    A declaration is in force inside its validity unless a successor that names it in
    ``supersedes`` has started. Refused (``ValueError``), never arbitrated: declarations of more
    than one site, a successor that starts before what it supersedes, and two in force at once.
    None in force is ``not_applicable``: nothing is declared, and IT load is untouched.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    by_hash = {d.content_hash(): d for d in declarations}
    sites = sorted({d.site_id for d in by_hash.values()})
    if len(sites) > 1:
        raise ValueError(f"active_at reads one site's declarations, not {sites}")
    for d in by_hash.values():
        prior = by_hash.get(d.supersedes) if isinstance(d.supersedes, str) else None
        if prior is not None and d.valid_from < prior.valid_from:
            raise ValueError(f"{d.declaration_id} starts before the declaration it supersedes")
    started = {
        d.supersedes
        for d in by_hash.values()
        if isinstance(d.supersedes, str) and d.valid_from <= instant
    }
    live = [
        d
        for h, d in sorted(by_hash.items())
        if d.valid_from <= instant < d.valid_until and h not in started
    ]
    if len(live) > 1:
        raise ValueError(
            f"{len(live)} declarations are in force at {instant.isoformat()} and none supersedes "
            f"the others: {[d.declaration_id for d in live]}"
        )
    if not live:
        return not_applicable(f"no declaration is in force at {instant.isoformat()}")
    return live[0]


def resolve_sla_evidence(
    sla_class: SlaClass, objects: Iterable[EvidenceObject], *, include_synthetic: bool = False
) -> EvidenceObject | Unknown:
    """The SLA document behind a class, resolved as an IC-4 ``service_level_agreement``. A
    self-issued record is never admitted; a synthetic one only when the caller asks. A class with
    no evidence answers ``unknown`` (IC-9 never requires an SLA document)."""
    ref = sla_class.sla_evidence
    if isinstance(ref, Unknown):
        return ref
    return resolve_ref(
        ref,
        objects,
        expected_type=EvidenceType.SERVICE_LEVEL_AGREEMENT,
        include_synthetic=include_synthetic,
    )
