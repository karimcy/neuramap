"""``resilience_floor_policy`` (WP-SITE-04, spec A3-02): the resilience reserve as a hard floor.

**Sizing (D172).** The floor is the largest energy the protected load draws over the
autonomy window, over every load draw (or a customer-stated quantile across draws) and every start
minute, resolved per cell of a GB-clock partition (IC-3's seasons, day groups and 30-minute bands),
plus only a customer-stated margin, divided by the discharge efficiency and rounded up to whole kWh
with IC-3's snap-then-ceil rule (D86). The power reservation is the protected load's maximum kW
over the same windows, rounded up. A floor above usable energy, or a reservation above the
discharge rating, is ``infeasible``, never clamped. Any unknown input leaves the policy
``indeterminate`` with its data asks.

**Standby handover (WP-SITE-05, D-NEW-SITE05-5).** A requirement whose duration derives from the
standby plant is sized when `size_floor` is given that plant's `OnsiteGenerationModel` (the
requirement's ``standby_ref`` is the model's content hash; any other model raises). The window is
detection + the first start time at which the units started, less the k largest, carry the peak
protected load + transfer. The floor then covers the handover only, never endurance; the statement
says so (``gridq.resilience_floor_statement/1.1.0``). Run hours and fuel do not enter the handover.

**Sign-off (D173).** The signed document *is* the floor statement: the policy renders a
canonical byte string (`render_floor_statement`) and a ``critical_facilities_signoff`` (IC-4,
existing since 1.0.0, issuer host or customer) binds only if its ``document_hash`` equals the
statement's SHA-256. A sign-off for another floor, or for this floor before its inputs changed, is
different bytes and binds nothing. `admit_floor_signoff` returns `Approved` only past the full
bar; everything short of it is `Unsigned` with a reason code and typed asks naming the exact hash to
sign. Signing changes the label, never the physics: the floor is a hard floor either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from math import ceil
from typing import Annotated, Final, Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import Field, model_validator

from gridq.adequacy._common import (
    J_PER_KWH,
    ExactRational,
    NonNegInt,
    RecordClass,
    ask,
    ceil_snapped,
    combine_record_class,
)
from gridq.adequacy.inputs import (
    SIZING_FIELDS,
    AutonomyRequirement,
    BessParameters,
    DerivedFromStandby,
    EnsembleMaximum,
    ModelledSeries,
    OneWay,
    RoundTripOnly,
    StatedDuration,
    StatedKw,
    StatedMargin,
    StatedQuantile,
    efficiency_value,
    missing_bess_asks,
    missing_requirement_asks,
)
from gridq.adequacy.load import MINUTES_PER_DAY, LoadIdentity, LoadSource
from gridq.adequacy.onsite import AllStart, LargestUnitsOut, OnsiteGenerationModel
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    canonical_json,
    content_hash,
    not_applicable,
    unknown,
)
from gridq.contracts.dispatch_envelope import DayGroup, Season, TimeBand
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceStatus,
    EvidenceType,
    IssuerRole,
    SiteScope,
    VerificationMethod,
    Verified,
    VerifierRole,
    evidence_ref,
)
from gridq.ops.dossier_model import AskHolder, DossierAsk

__all__ = [
    "DECLARATION",
    "POLICY_SCHEMA",
    "STATEMENT_FORMAT",
    "Approved",
    "CellFloor",
    "Derivation",
    "FloorApproval",
    "FloorPartition",
    "FloorStatement",
    "FloorStatementV1_1",
    "HANDOVER_NOTE",
    "Handover",
    "Indeterminate",
    "Infeasible",
    "ResilienceFloorPolicy",
    "ResilienceTerms",
    "Sized",
    "StandbyDerivation",
    "UnsignedReason",
    "Unsigned",
    "admit_floor_signoff",
    "approval_label",
    "floor_statement_hash",
    "render_floor_statement",
    "resilience_terms",
    "size_floor",
]

POLICY_SCHEMA: Final = "gridq.adequacy.resilience_floor_policy"
STATEMENT_FORMAT: Final = "gridq.resilience_floor_statement/1.0.0"
STATEMENT_FORMAT_V1_1: Final = "gridq.resilience_floor_statement/1.1.0"
HANDOVER_NOTE: Final = (
    "This floor covers the handover to standby generation only, not endurance: it is sized for "
    "detection, the standby start and the load transfer."
)
DECLARATION: Final = (
    "This resilience reserve is a hard floor: it is never available to markets and no dispatch "
    "may take the battery below it."
)
#: Load is fetched in chunks of this many start minutes (bounded memory over a year's horizon).
_CHUNK_MIN: Final = 7 * MINUTES_PER_DAY


# --------------------------------------------------------------------------- partition


class FloorPartition(ContractModel):
    """A GB-clock partition of the year into cells, in IC-3's own frame (D85): seasons partition
    the months, day groups the ISO week, bands the local day on the 30-minute grid. A single
    all-year, all-week, all-day cell gives one flat floor."""

    seasons: tuple[Season, ...] = Field(min_length=1)
    day_groups: tuple[DayGroup, ...] = Field(min_length=1)
    bands: tuple[TimeBand, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _partitions(self) -> FloorPartition:
        if sorted(m for s in self.seasons for m in s.months) != list(range(1, 13)):
            raise ValueError("seasons must partition the twelve months")
        if sorted(d for g in self.day_groups for d in g.iso_weekdays) != list(range(1, 8)):
            raise ValueError("day groups must partition the ISO week")
        edge = 0
        for b in self.bands:
            if b.start_minute != edge:
                raise ValueError("bands must partition the day: sorted and contiguous from 0")
            edge = b.end_minute
        if edge != MINUTES_PER_DAY:
            raise ValueError("bands must end at minute 1440")
        for name, ids in (
            ("season", [s.season_id for s in self.seasons]),
            ("day group", [g.day_group_id for g in self.day_groups]),
            ("band", [b.band_id for b in self.bands]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} ids must be unique")
        return self

    @property
    def cell_ids(self) -> tuple[str, ...]:
        return tuple(
            f"{s.season_id}/{g.day_group_id}/{b.band_id}"
            for s in self.seasons
            for g in self.day_groups
            for b in self.bands
        )

    def cell_index(self, month: int, iso_weekday: int, minute_of_day: int) -> int:
        si = next(i for i, s in enumerate(self.seasons) if month in s.months)
        gi = next(i for i, g in enumerate(self.day_groups) if iso_weekday in g.iso_weekdays)
        bi = next(
            i for i, b in enumerate(self.bands) if b.start_minute <= minute_of_day < b.end_minute
        )
        return (si * len(self.day_groups) + gi) * len(self.bands) + bi

    def band_index_by_minute(self) -> np.ndarray:
        out = np.empty(MINUTES_PER_DAY, dtype=np.int64)
        for i, b in enumerate(self.bands):
            out[b.start_minute : b.end_minute] = i
        return out


# --------------------------------------------------------------------------- policy record


class CellFloor(ContractModel):
    """One cell's floor (kWh) and power reservation (kW). ``Unknown`` when no assessed window
    starts in the cell; a consumer then uses the flat values."""

    cell_id: NonEmptyStr
    floor_kwh: NonNegInt | Unknown
    resilience_kw: NonNegInt | Unknown
    #: The draw and naive-local start minute that set the floor (load-model sized cells only).
    binding_draw: NonNegInt | NotApplicable
    binding_start: NonEmptyStr | NotApplicable


class Sized(ContractModel):
    kind: Literal["sized"]
    cells: tuple[CellFloor, ...] = Field(min_length=1)
    #: The largest cell floor: the one figure for an envelope cell with no assessed minutes.
    flat_floor_kwh: NonNegInt
    #: The smallest cell floor: IC-3's ``FailSafe.resilience_floor_kwh``.
    fail_safe_floor_kwh: NonNegInt
    flat_resilience_kw: NonNegInt


class Indeterminate(ContractModel):
    """The floor cannot be sized: the named fields are missing (each has an ask on the policy)."""

    kind: Literal["indeterminate"]
    missing: tuple[NonEmptyStr, ...] = Field(min_length=1)


class Infeasible(ContractModel):
    """The requirement cannot be met by this battery. Never clamped to fit."""

    kind: Literal["infeasible"]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    computed_floor_kwh: NonNegInt
    usable_energy_kwh: NonNegInt
    computed_resilience_kw: NonNegInt
    discharge_rating_kw: NonNegInt


class Derivation(ContractModel):
    window_seconds: NonNegInt
    #: ``ceil(window_seconds / 60)``: the one-minute window the energy is summed over.
    window_minutes: NonNegInt
    statistic: EnsembleMaximum | StatedQuantile = Field(discriminator="kind")
    eta_used: ExactRational
    eta_is_bound: bool
    margin: StatedMargin | NotApplicable = Field(discriminator="kind")
    #: Start minutes whose window would run past the horizon end, and so were not assessed.
    excluded_tail_starts: NonNegInt
    flags: tuple[NonEmptyStr, ...]


class Handover(ContractModel):
    """How a standby-derived window was set (D-NEW-SITE05-5): detection + carry start + transfer."""

    standby_model_hash: Sha256
    #: The peak protected load the started units must carry, W.
    peak_protected_w: NonNegInt
    contingency_k: NonNegInt
    counted_units: tuple[NonEmptyStr, ...]
    #: Units left out because their rating or start time is unknown (each has an ask).
    excluded_units: tuple[NonEmptyStr, ...]
    #: The first start time at which the counted units, less the k largest, carry the peak.
    carry_start_s: NonNegInt
    detection_s: NonNegInt
    transfer_s: NonNegInt
    duration_s: NonNegInt

    @model_validator(mode="after")
    def _sum(self) -> Handover:
        if self.duration_s != self.detection_s + self.carry_start_s + self.transfer_s:
            raise ValueError("the handover is detection + carry start + transfer")
        return self


class StandbyDerivation(ContractModel):
    """A `Derivation` whose window is the standby handover. Standalone (not a subclass), so a
    1.0.0 policy's bytes never change."""

    window_seconds: NonNegInt
    window_minutes: NonNegInt
    statistic: EnsembleMaximum | StatedQuantile = Field(discriminator="kind")
    eta_used: ExactRational
    eta_is_bound: bool
    margin: StatedMargin | NotApplicable = Field(discriminator="kind")
    excluded_tail_starts: NonNegInt
    flags: tuple[NonEmptyStr, ...]
    handover: Handover

    @model_validator(mode="after")
    def _window(self) -> StandbyDerivation:
        if self.window_seconds != self.handover.duration_s:
            raise ValueError("the window is the handover duration")
        return self


class ResilienceFloorPolicy(ContractModel):
    """The floor, how it was derived, and what is missing. Carries no evidence: the sign-off binds
    to the bytes of `render_floor_statement`, which include this policy's hash."""

    schema_name: Literal["gridq.adequacy.resilience_floor_policy"] = POLICY_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    requirement: AutonomyRequirement
    bess_parameters_hash: Sha256
    load: LoadIdentity | NotApplicable = Field(discriminator="kind")
    partition: FloorPartition
    state: Sized | Indeterminate | Infeasible = Field(discriminator="kind")
    derivation: Derivation | StandbyDerivation | NotApplicable
    data_requests: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _consistent(self) -> ResilienceFloorPolicy:
        ids = [a.ask_id for a in self.data_requests]
        if ids != sorted(set(ids)):
            raise ValueError("data_requests are sorted by ask_id, without repeats")
        if isinstance(self.state, Sized):
            if [c.cell_id for c in self.state.cells] != list(self.partition.cell_ids):
                raise ValueError("a sized policy has one cell floor per partition cell, in order")
            if isinstance(self.derivation, NotApplicable):
                raise ValueError("a sized policy states its derivation")
        if isinstance(self.state, Indeterminate) and not self.data_requests:
            raise ValueError("an indeterminate policy says what is missing")
        return self

    def content_hash(self) -> str:
        return content_hash(self)

    def floor_kwh_for_cell(self, cell: int) -> int:
        """The floor in force in partition cell ``cell`` (the flat floor where it is unknown)."""
        if not isinstance(self.state, Sized):
            raise ValueError("the policy has no sized floor")
        v = self.state.cells[cell].floor_kwh
        return v if isinstance(v, int) else self.state.flat_floor_kwh

    def resilience_kw_for_cell(self, cell: int) -> int:
        if not isinstance(self.state, Sized):
            raise ValueError("the policy has no sized floor")
        v = self.state.cells[cell].resilience_kw
        return v if isinstance(v, int) else self.state.flat_resilience_kw


# --------------------------------------------------------------------------- sizing


def _cell_of_naive_minutes(
    partition: FloorPartition, horizon_start: date, m0: int, m1: int
) -> np.ndarray:
    band_of = partition.band_index_by_minute()
    n_g, n_b = len(partition.day_groups), len(partition.bands)
    out = np.empty(m1 - m0, dtype=np.int64)
    d0, d1 = m0 // MINUTES_PER_DAY, -(-m1 // MINUTES_PER_DAY)
    for d in range(d0, d1):
        day = horizon_start + timedelta(days=d)
        si = next(i for i, s in enumerate(partition.seasons) if day.month in s.months)
        gi = next(
            i for i, g in enumerate(partition.day_groups) if day.isoweekday() in g.iso_weekdays
        )
        lo, hi = max(m0, d * MINUTES_PER_DAY), min(m1, (d + 1) * MINUTES_PER_DAY)
        mins = np.arange(lo, hi) - d * MINUTES_PER_DAY
        out[lo - m0 : hi - m0] = (si * n_g + gi) * n_b + band_of[mins]
    return out


def _higher_quantile_index(q: Fraction, n: int) -> int:
    """numpy's ``method="higher"`` rank, in exact arithmetic."""
    return ceil(q * (n - 1))


def _protected_w(series_w: np.ndarray, fraction: Fraction) -> np.ndarray:
    """``ceil(series x fraction)`` in integer watts: the protected load rounded to the safe side."""
    num, den = fraction.numerator, fraction.denominator
    return (series_w * num + den - 1) // den


def _energy_to_floor(e_wmin: int, margin: StatedMargin | NotApplicable, eta: Fraction) -> int:
    kwh = Fraction(e_wmin, 60 * 1000)
    if isinstance(margin, StatedMargin):
        m = margin.value.as_fraction()
        kwh = kwh * (1 + m) if margin.basis == "fraction" else kwh + m
    return max(0, ceil_snapped(kwh / eta))


def size_floor(
    requirement: AutonomyRequirement,
    bess: BessParameters,
    partition: FloorPartition,
    *,
    load: LoadSource | NotApplicable,
    standby: OnsiteGenerationModel | None = None,
) -> ResilienceFloorPolicy:
    """Size the resilience floor from the autonomy requirement (D172). Pure.

    ``standby`` (WP-SITE-05) is the standby plant a ``derived_from_standby`` duration names; without
    it such a duration stays indeterminate, exactly as before."""
    if requirement.site_id != bess.site_id:
        raise ValueError("the requirement and the BESS parameters name different sites")
    identity = load.identity if isinstance(load, LoadSource) else load
    if isinstance(identity, LoadIdentity) and identity.site_id != requirement.site_id:
        raise ValueError("the load and the requirement name different sites")
    record_class = combine_record_class(
        requirement.record_class,
        bess.record_class,
        identity.record_class if isinstance(identity, LoadIdentity) else "registered",
    )
    asks = list(missing_requirement_asks(requirement)) + list(
        missing_bess_asks(bess, SIZING_FIELDS)
    )
    derived = isinstance(requirement.duration, DerivedFromStandby)
    if standby is not None:
        if not isinstance(requirement.duration, DerivedFromStandby):
            raise ValueError("a standby model is given but the duration does not derive from it")
        if standby.content_hash() != requirement.duration.standby_ref:
            raise ValueError("the standby model is not the one the requirement names")
        if standby.site_id != requirement.site_id:
            raise ValueError("the standby model and the requirement name different sites")
        asks = [(name, a) for name, a in asks if name != "duration"]
        if requirement.coverage == "full_outage_autonomy":
            asks.append(
                (
                    "coverage",
                    ask(
                        "The duration derives from the standby handover, but the coverage stated "
                        "is full outage autonomy: state ride-through to standby, or a duration",
                        site_id=requirement.site_id,
                    ),
                )
            )
    pl = requirement.protected_load
    if isinstance(pl, ModelledSeries) and not isinstance(load, LoadSource):
        asks.append(
            (
                "load",
                ask(
                    "Supply the site's one-minute load series (the protected load is stated as a "
                    "fraction of it)",
                    site_id=requirement.site_id,
                ),
            )
        )

    def policy(
        state: Sized | Indeterminate | Infeasible,
        derivation: Derivation | StandbyDerivation | NotApplicable,
        extra: list[DossierAsk],
    ) -> ResilienceFloorPolicy:
        reqs = {a.ask_id: a for _, a in asks} | {a.ask_id: a for a in extra}
        return ResilienceFloorPolicy(
            site_id=requirement.site_id,
            record_class=record_class,
            requirement=requirement,
            bess_parameters_hash=bess.content_hash(),
            load=identity,
            partition=partition,
            state=state,
            derivation=derivation,
            data_requests=tuple(reqs[k] for k in sorted(reqs)),
        )

    handover: Handover | None = None
    standby_open: list[DossierAsk] = []
    if derived and standby is not None and not asks:
        assert isinstance(pl, StatedKw | ModelledSeries)
        handover, more, standby_open = _handover(standby, pl, load, requirement.site_id)
        asks.extend(more)

    if asks:
        missing = tuple(sorted({name for name, _ in asks}))
        return policy(Indeterminate(kind="indeterminate", missing=missing), _na_derivation(), [])

    if handover is not None:
        duration_s = handover.duration_s
    else:
        assert isinstance(requirement.duration, StatedDuration)
        duration_s = requirement.duration.seconds
    assert isinstance(requirement.statistic, EnsembleMaximum | StatedQuantile)
    assert not isinstance(requirement.margin, Unknown)
    assert isinstance(bess.discharge_efficiency, OneWay | RoundTripOnly)
    assert isinstance(bess.usable_energy_kwh, int) and isinstance(bess.discharge_rating_kw, int)
    eta = efficiency_value(bess.discharge_efficiency)
    flags: set[str] = set()
    extra: list[DossierAsk] = list(standby_open)
    if isinstance(bess.discharge_efficiency, RoundTripOnly):
        flags.add("discharge_efficiency_bound")
        extra.append(
            ask(
                "State the BESS one-way discharge efficiency (only a round-trip figure is held; "
                "it is used as a safe-side bound)",
                site_id=bess.site_id,
            )
        )
    if bess.usable_basis != "end_of_life":
        flags.add("floor_checked_against_non_eol_energy")
        extra.append(
            ask(
                "State the BESS usable energy at end of life (the floor is checked against a "
                "non end-of-life figure)",
                site_id=bess.site_id,
            )
        )
    n = max(1, -(-duration_s // 60))
    n_cells = len(partition.cell_ids)
    cell_e = [-1] * n_cells  # W x min; -1 = no assessed start
    cell_p = [-1] * n_cells  # W
    binding: list[tuple[int, int] | None] = [None] * n_cells
    excluded = 0
    if isinstance(pl, StatedKw):
        e_all, p_all = pl.kw * 1000 * n, pl.kw * 1000
        cell_e = [e_all] * n_cells
        cell_p = [p_all] * n_cells
    else:
        assert isinstance(pl, ModelledSeries) and isinstance(load, LoadSource)
        excluded = _size_from_load(
            load, pl, requirement.statistic, partition, n, cell_e, cell_p, binding
        )
    cells: list[CellFloor] = []
    h0 = identity.horizon_start if isinstance(identity, LoadIdentity) else None
    for i, cid in enumerate(partition.cell_ids):
        if cell_e[i] < 0:
            why = unknown("no assessed window starts in this cell (the flat floor applies)")
            cells.append(
                CellFloor(
                    cell_id=cid,
                    floor_kwh=why,
                    resilience_kw=why,
                    binding_draw=not_applicable("no assessed window"),
                    binding_start=not_applicable("no assessed window"),
                )
            )
            continue
        b = binding[i]
        cells.append(
            CellFloor(
                cell_id=cid,
                floor_kwh=_energy_to_floor(cell_e[i], requirement.margin, eta),
                resilience_kw=-(-cell_p[i] // 1000),
                binding_draw=b[0] if b else not_applicable("a stated constant kW"),
                binding_start=(
                    _naive(h0, b[1]) if b and h0 else not_applicable("a stated constant kW")
                ),
            )
        )
    known = [c for c in cells if isinstance(c.floor_kwh, int)]
    common = {
        "window_seconds": duration_s,
        "window_minutes": n,
        "statistic": requirement.statistic,
        "eta_used": ExactRational.of(eta),
        "eta_is_bound": isinstance(bess.discharge_efficiency, RoundTripOnly),
        "margin": requirement.margin,
        "excluded_tail_starts": excluded,
        "flags": tuple(sorted(flags)),
    }
    derivation: Derivation | StandbyDerivation = (
        StandbyDerivation(**common, handover=handover)  # type: ignore[arg-type]
        if handover is not None
        else Derivation(**common)  # type: ignore[arg-type]
    )
    if not known:
        return policy(
            Indeterminate(kind="indeterminate", missing=("load_horizon",)),
            NotApplicable(kind="not_applicable", reason="no assessed window"),
            [
                ask(
                    f"Supply a load horizon longer than the {n}-minute autonomy window",
                    site_id=requirement.site_id,
                )
            ],
        )
    flat = max(c.floor_kwh for c in known)  # type: ignore[type-var]
    fail_safe = min(c.floor_kwh for c in known)  # type: ignore[type-var]
    flat_kw = max(c.resilience_kw for c in known)  # type: ignore[type-var]
    assert isinstance(flat, int) and isinstance(fail_safe, int) and isinstance(flat_kw, int)
    reasons: list[str] = []
    if flat > bess.usable_energy_kwh:
        reasons.append(
            f"the floor ({flat} kWh) exceeds the usable energy ({bess.usable_energy_kwh} kWh)"
        )
    if flat_kw > bess.discharge_rating_kw:
        reasons.append(
            f"the protected load ({flat_kw} kW) exceeds the discharge rating "
            f"({bess.discharge_rating_kw} kW)"
        )
    if reasons:
        return policy(
            Infeasible(
                kind="infeasible",
                reasons=tuple(reasons),
                computed_floor_kwh=flat,
                usable_energy_kwh=bess.usable_energy_kwh,
                computed_resilience_kw=flat_kw,
                discharge_rating_kw=bess.discharge_rating_kw,
            ),
            derivation,
            extra,
        )
    if flat == bess.usable_energy_kwh:
        derivation = derivation.model_copy(
            update={"flags": tuple(sorted(flags | {"no_tradable_energy"}))}
        )
    return policy(
        Sized(
            kind="sized",
            cells=tuple(cells),
            flat_floor_kwh=flat,
            fail_safe_floor_kwh=fail_safe,
            flat_resilience_kw=flat_kw,
        ),
        derivation,
        extra,
    )


def _peak_protected_w(pl: ModelledSeries, load: LoadSource) -> int:
    """The largest protected load over every draw and minute of the horizon, W (rounded up)."""
    ident = load.identity
    total = (ident.horizon_end - ident.horizon_start).days * MINUTES_PER_DAY
    frac = pl.fraction.as_fraction()
    best = 0
    for c0 in range(0, total, _CHUNK_MIN):
        fac, it = load.minutes(c0, min(total, c0 + _CHUNK_MIN))
        p = _protected_w(it if pl.basis == "it" else fac, frac)
        best = max(best, int(p.max()))
    return best


def _handover(
    model: OnsiteGenerationModel,
    pl: StatedKw | ModelledSeries,
    load: LoadSource | NotApplicable,
    site: str,
) -> tuple[Handover | None, list[tuple[str, DossierAsk]], list[DossierAsk]]:
    """The standby handover (D-NEW-SITE05-5, -6 (e)), or the asks that block it; and the asks
    that stay open without blocking (units left out for an unknown rating or start time)."""
    from gridq.contracts._base import Unknown as _Unknown

    asks: list[tuple[str, DossierAsk]] = []
    holder = AskHolder.HOST if model.declared_by.role == "host" else AskHolder.CUSTOMER
    counted: list[tuple[int, int, str]] = []  # (start s, rated kW, unit)
    excluded: list[str] = []
    for g in model.gensets:
        if isinstance(g.rated_kw, int) and isinstance(g.start_to_load_s, int):
            counted.append((g.start_to_load_s, g.rated_kw, g.unit_id))
            continue
        excluded.append(g.unit_id)
        asks.append(
            (
                f"standby.{g.unit_id}",
                ask(
                    f"State the rated output (kW) and start-to-load time (s) of standby genset "
                    f"{g.unit_id}; it is left out of the handover until both are stated",
                    site_id=site,
                    holder=holder,
                    closes_with=(EvidenceType.TECHNICAL_REPORT, EvidenceType.COMMISSIONING_RECORD),
                ),
            )
        )
    sc = model.start_contingency
    k = sc.k if isinstance(sc, LargestUnitsOut) else 0 if isinstance(sc, AllStart) else None
    blocking: list[tuple[str, DossierAsk]] = []
    for name, value, text in (
        (
            "start_contingency",
            k,
            "State the standby start contingency: every unit starts, or the "
            "k largest are assumed not to",
        ),
        (
            "detection_s",
            model.detection_s,
            "State the time (seconds) from loss of supply to the standby call",
        ),
        (
            "transfer_s",
            model.transfer_s,
            "State the time (seconds) from a set on load to the protected load transferred",
        ),
    ):
        if value is None or isinstance(value, _Unknown | NotApplicable):
            blocking.append((name, ask(text, site_id=site, holder=holder)))
    if blocking:
        return None, blocking + asks, []
    assert k is not None
    assert isinstance(model.detection_s, int) and isinstance(model.transfer_s, int)
    if isinstance(pl, StatedKw):
        peak_w = pl.kw * 1000
    elif isinstance(load, LoadSource):
        peak_w = _peak_protected_w(pl, load)
    else:  # pragma: no cover - the load ask is already on the policy
        return None, asks, []
    t_star: int | None = None
    for t in sorted({s for s, _, _ in counted}):
        ratings = sorted((r for s, r, _ in counted if s <= t), reverse=True)
        if (sum(ratings) - sum(ratings[:k])) * 1000 >= peak_w:
            t_star = t
            break
    if t_star is None:
        asks.append(
            (
                "standby_capacity",
                ask(
                    f"The standby units counted ({', '.join(u for _, _, u in counted) or 'none'}) "
                    f"less the {k} largest cannot carry the peak protected load "
                    f"({-(-peak_w // 1000)} kW): state further standby plant, or a duration",
                    site_id=site,
                    holder=holder,
                ),
            )
        )
        return None, asks, []
    h = Handover(
        standby_model_hash=model.content_hash(),
        peak_protected_w=peak_w,
        contingency_k=k,
        counted_units=tuple(sorted(u for _, _, u in counted)),
        excluded_units=tuple(sorted(excluded)),
        carry_start_s=t_star,
        detection_s=model.detection_s,
        transfer_s=model.transfer_s,
        duration_s=model.detection_s + t_star + model.transfer_s,
    )
    return h, [], [a for _, a in asks]


def _na_derivation() -> NotApplicable:
    return not_applicable("the floor is not sized: inputs are missing")


def _naive(h0: date, minute: int) -> str:
    return (datetime.combine(h0, datetime.min.time()) + timedelta(minutes=minute)).isoformat()


def _size_from_load(
    load: LoadSource,
    pl: ModelledSeries,
    statistic: EnsembleMaximum | StatedQuantile,
    partition: FloorPartition,
    n: int,
    cell_e: list[int],
    cell_p: list[int],
    binding: list[tuple[int, int] | None],
) -> int:
    """Fill the per-cell maxima in place from the load ensemble; return the excluded tail starts.

    Integer watt-minutes throughout, so the maxima are exact on every platform."""
    ident = load.identity
    total = (ident.horizon_end - ident.horizon_start).days * MINUTES_PER_DAY
    starts = total - n + 1
    if starts <= 0:
        return total
    frac = pl.fraction.as_fraction()
    q_index = (
        _higher_quantile_index(statistic.q.as_fraction(), ident.draws)
        if isinstance(statistic, StatedQuantile)
        else None
    )
    for c0 in range(0, starts, _CHUNK_MIN):
        c1 = min(starts, c0 + _CHUNK_MIN)
        fac, it = load.minutes(c0, c1 + n - 1)
        p = _protected_w(it if pl.basis == "it" else fac, frac)
        cs = np.zeros((p.shape[0], p.shape[1] + 1), dtype=np.int64)
        np.cumsum(p, axis=1, out=cs[:, 1:])
        e = cs[:, n : n + (c1 - c0)] - cs[:, : c1 - c0]  # (N, starts) W x min
        if q_index is None:
            e_hat = e.max(axis=0)
            arg = e.argmax(axis=0)
        else:
            order = np.argsort(e, axis=0, kind="stable")
            arg = order[q_index]
            e_hat = np.take_along_axis(e, arg[None, :], axis=0)[0]
        p_hat = sliding_window_view(p.max(axis=0), n)[: c1 - c0].max(axis=1)
        cells = _cell_of_naive_minutes(partition, ident.horizon_start, c0, c1)
        for ci in np.unique(cells):
            sel = np.flatnonzero(cells == ci)
            j = sel[int(np.argmax(e_hat[sel]))]
            if int(e_hat[j]) > cell_e[ci]:
                cell_e[ci] = int(e_hat[j])
                binding[ci] = (int(arg[j]), c0 + int(j))
            cell_p[ci] = max(cell_p[ci], int(p_hat[sel].max()))
    return total - starts


# --------------------------------------------------------------------------- IC-3 producer helper


class ResilienceTerms(ContractModel):
    """What an IC-3 producer (WP-SIZE-08) puts in each cell for the resilience reserve
    (D174): the floor kWh (rounded up), the discharge-direction ``resilience_kw``
    (rounded up: a reserve, the merchant slice absorbs the residual) and 0 kW to charge."""

    flat_floor_kwh: NonNegInt
    fail_safe_floor_kwh: NonNegInt
    discharge_resilience_kw: NonNegInt
    charge_resilience_kw: Literal[0]
    cells: tuple[CellFloor, ...]


def resilience_terms(policy: ResilienceFloorPolicy) -> ResilienceTerms:
    if not isinstance(policy.state, Sized):
        raise ValueError(f"the policy is {policy.state.kind}: there are no terms to export")
    s = policy.state
    return ResilienceTerms(
        flat_floor_kwh=s.flat_floor_kwh,
        fail_safe_floor_kwh=s.fail_safe_floor_kwh,
        discharge_resilience_kw=s.flat_resilience_kw,
        charge_resilience_kw=0,
        cells=s.cells,
    )


# --------------------------------------------------------------------------- statement


class FloorStatement(ContractModel):
    """The document a critical-facilities sign-off signs: exactly these canonical bytes."""

    format: Literal["gridq.resilience_floor_statement/1.0.0"]
    site_id: NonEmptyStr
    record_class: RecordClass
    flat_floor_kwh: NonNegInt
    fail_safe_floor_kwh: NonNegInt
    flat_resilience_kw: NonNegInt
    cells: tuple[CellFloor, ...]
    requirement: AutonomyRequirement
    policy_hash: Sha256
    declaration: Literal[
        "This resilience reserve is a hard floor: it is never available to markets and no "
        "dispatch may take the battery below it."
    ]


class FloorStatementV1_1(ContractModel):
    """``1.1.0``: a standby-derived floor. The 1.0.0 fields, the handover and the fixed note that
    the floor covers the handover only, not endurance."""

    format: Literal["gridq.resilience_floor_statement/1.1.0"]
    site_id: NonEmptyStr
    record_class: RecordClass
    flat_floor_kwh: NonNegInt
    fail_safe_floor_kwh: NonNegInt
    flat_resilience_kw: NonNegInt
    cells: tuple[CellFloor, ...]
    requirement: AutonomyRequirement
    policy_hash: Sha256
    declaration: Literal[
        "This resilience reserve is a hard floor: it is never available to markets and no "
        "dispatch may take the battery below it."
    ]
    handover: Handover
    handover_note: Literal[
        "This floor covers the handover to standby generation only, not endurance: it is sized "
        "for detection, the standby start and the load transfer."
    ]


def render_floor_statement(policy: ResilienceFloorPolicy) -> bytes:
    """The canonical bytes to sign (UTF-8 canonical JSON, no trailing newline). Sized only.
    A standby-derived policy renders ``1.1.0``; every other policy ``1.0.0``, unchanged."""
    if not isinstance(policy.state, Sized):
        raise ValueError(f"the policy is {policy.state.kind}: there is no floor to sign")
    s = policy.state
    if isinstance(policy.derivation, StandbyDerivation):
        v11 = FloorStatementV1_1(
            format=STATEMENT_FORMAT_V1_1,
            site_id=policy.site_id,
            record_class=policy.record_class,
            flat_floor_kwh=s.flat_floor_kwh,
            fail_safe_floor_kwh=s.fail_safe_floor_kwh,
            flat_resilience_kw=s.flat_resilience_kw,
            cells=s.cells,
            requirement=policy.requirement,
            policy_hash=policy.content_hash(),
            declaration=DECLARATION,
            handover=policy.derivation.handover,
            handover_note=HANDOVER_NOTE,
        )
        return canonical_json(v11).encode("utf-8")
    stmt = FloorStatement(
        format=STATEMENT_FORMAT,
        site_id=policy.site_id,
        record_class=policy.record_class,
        flat_floor_kwh=s.flat_floor_kwh,
        fail_safe_floor_kwh=s.fail_safe_floor_kwh,
        flat_resilience_kw=s.flat_resilience_kw,
        cells=s.cells,
        requirement=policy.requirement,
        policy_hash=policy.content_hash(),
        declaration=DECLARATION,
    )
    return canonical_json(stmt).encode("utf-8")


def floor_statement_hash(policy: ResilienceFloorPolicy) -> str | Unknown:
    import hashlib

    if not isinstance(policy.state, Sized):
        return unknown(f"the policy is {policy.state.kind}: no floor statement exists")
    return "sha256:" + hashlib.sha256(render_floor_statement(policy)).hexdigest()


# --------------------------------------------------------------------------- sign-off


class UnsignedReason(StrEnum):
    """Why a floor is unsigned. Declared in precedence order: when several candidates fail, the
    reason reported is that of the candidate that got furthest (the latest member)."""

    FLOOR_UNKNOWN = "floor_unknown"
    FLOOR_INFEASIBLE = "floor_infeasible"
    ABSENT = "absent"
    HASH_MISMATCH = "hash_mismatch"
    WRONG_SCOPE = "wrong_scope"
    WRONG_ISSUER_ROLE = "wrong_issuer_role"
    SYNTHETIC = "synthetic"
    EXPIRED = "expired"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    VALIDITY_UNKNOWN = "validity_unknown"
    UNVERIFIED = "unverified"
    VERIFICATION_METHOD_INSUFFICIENT = "verification_method_insufficient"
    VERIFIED_AFTER_AS_OF = "verified_after_as_of"


_PRECEDENCE: Final = {r: i for i, r in enumerate(UnsignedReason)}

_REASON_TEXT: Final = {
    UnsignedReason.FLOOR_UNKNOWN: "the floor is not sized, so there is nothing to sign",
    UnsignedReason.FLOOR_INFEASIBLE: "the floor is infeasible for this battery, so it cannot be "
    "signed",
    UnsignedReason.ABSENT: "no critical-facilities sign-off is held",
    UnsignedReason.HASH_MISMATCH: "the sign-off held covers a different floor statement",
    UnsignedReason.WRONG_SCOPE: "the sign-off held is not scoped to this site",
    UnsignedReason.WRONG_ISSUER_ROLE: "the sign-off held is not issued by the host or customer",
    UnsignedReason.SYNTHETIC: "the sign-off held is a synthetic fixture",
    UnsignedReason.EXPIRED: "the sign-off held has expired",
    UnsignedReason.NOT_YET_EFFECTIVE: "the sign-off held is not yet in effect",
    UnsignedReason.VALIDITY_UNKNOWN: "the sign-off held has no stated effective date or expiry",
    UnsignedReason.UNVERIFIED: "the sign-off held has not been verified with its issuer",
    UnsignedReason.VERIFICATION_METHOD_INSUFFICIENT: "the sign-off held was not confirmed with "
    "its issuer",
    UnsignedReason.VERIFIED_AFTER_AS_OF: "the sign-off held was verified after the run date",
}

_ISSUERS: Final = (IssuerRole.HOST, IssuerRole.CUSTOMER)


class Approved(ContractModel):
    """An admitted sign-off. Constructible by hand, but a result holding one re-runs the
    admission and refuses it unless the outcome is identical (`gridq.adequacy.result`)."""

    kind: Literal["approved"]
    statement_hash: Sha256
    evidence: EvidenceObject
    evidence_ref: EvidenceRef
    issuer_role: Literal["host", "customer"]
    verifier_role: VerifierRole
    as_of: IsoDate
    synthetic: bool


class Unsigned(ContractModel):
    kind: Literal["unsigned"]
    statement_hash: Sha256 | Unknown
    as_of: IsoDate
    reason: UnsignedReason
    #: Every candidate sign-off and why it did not admit, sorted by hash.
    candidates: tuple[tuple[Sha256, UnsignedReason], ...]
    requests: tuple[DossierAsk, ...] = Field(min_length=1)


FloorApproval = Annotated[Approved | Unsigned, Field(discriminator="kind")]


def _check(
    obj: EvidenceObject, policy: ResilienceFloorPolicy, statement_hash: str, as_of: date
) -> UnsignedReason | None:
    if obj.document_hash != statement_hash:
        return UnsignedReason.HASH_MISMATCH
    if not isinstance(obj.scope, SiteScope) or obj.scope.site_id != policy.site_id:
        return UnsignedReason.WRONG_SCOPE
    if obj.is_self_issued or obj.issuer.role not in _ISSUERS:
        return UnsignedReason.WRONG_ISSUER_ROLE
    if obj.record_class == "synthetic_fixture" and policy.record_class != "synthetic_fixture":
        return UnsignedReason.SYNTHETIC
    status = obj.status_at(as_of)
    if status is not EvidenceStatus.VALID:
        return {
            EvidenceStatus.EXPIRED: UnsignedReason.EXPIRED,
            EvidenceStatus.NOT_YET_EFFECTIVE: UnsignedReason.NOT_YET_EFFECTIVE,
            EvidenceStatus.VALIDITY_UNKNOWN: UnsignedReason.VALIDITY_UNKNOWN,
        }[status]
    if not isinstance(obj.verification, Verified):
        return UnsignedReason.UNVERIFIED
    if obj.verification.method is not VerificationMethod.CONFIRMED_WITH_ISSUER:
        return UnsignedReason.VERIFICATION_METHOD_INSUFFICIENT
    if obj.verification.verified_on > as_of:
        return UnsignedReason.VERIFIED_AFTER_AS_OF
    return None


def _signoff_ask(policy: ResilienceFloorPolicy, text: str) -> DossierAsk:
    holder = AskHolder.HOST if policy.requirement.declared_by.role == "host" else AskHolder.CUSTOMER
    return ask(
        text,
        site_id=policy.site_id,
        holder=holder,
        closes_with=(EvidenceType.CRITICAL_FACILITIES_SIGNOFF,),
        prerequisites=tuple(a.ask_id for a in policy.data_requests)
        if not isinstance(policy.state, Sized)
        else (),
    )


def admit_floor_signoff(
    policy: ResilienceFloorPolicy, objects: Iterable[EvidenceObject], *, as_of: date
) -> Approved | Unsigned:
    """The admission bar for a floor sign-off (D173). Pure; reads no clock.

    A candidate is any ``critical_facilities_signoff``. It admits only when its document hash is
    the floor statement's hash, it is scoped to the site, a host or customer issued it, it is not a
    synthetic fixture (unless the policy is one), it is VALID on ``as_of``, and it was verified by
    confirmation with the issuer on or before ``as_of``. The lowest admitting hash wins.
    """
    candidates = sorted(
        (o for o in objects if o.evidence_type is EvidenceType.CRITICAL_FACILITIES_SIGNOFF),
        key=lambda o: o.content_hash(),
    )
    sh = floor_statement_hash(policy)
    if isinstance(sh, Unknown):
        reason = (
            UnsignedReason.FLOOR_INFEASIBLE
            if isinstance(policy.state, Infeasible)
            else UnsignedReason.FLOOR_UNKNOWN
        )
        return Unsigned(
            kind="unsigned",
            statement_hash=sh,
            as_of=as_of,
            reason=reason,
            candidates=tuple((o.content_hash(), reason) for o in candidates),
            requests=(
                _signoff_ask(
                    policy,
                    "Critical-facilities sign-off of the resilience floor, once the floor can "
                    f"be sized ({_REASON_TEXT[reason]})",
                ),
            ),
        )
    failures: list[tuple[str, UnsignedReason]] = []
    for o in candidates:
        why = _check(o, policy, sh, as_of)
        if why is None:
            assert isinstance(o.verification, Verified)
            role = o.issuer.role
            assert isinstance(role, IssuerRole)
            return Approved(
                kind="approved",
                statement_hash=sh,
                evidence=o,
                evidence_ref=evidence_ref(o),
                issuer_role="host" if role is IssuerRole.HOST else "customer",
                verifier_role=o.verification.verifier_role,
                as_of=as_of,
                synthetic=(
                    policy.record_class == "synthetic_fixture"
                    or o.record_class == "synthetic_fixture"
                ),
            )
        failures.append((o.content_hash(), why))
    if failures:
        reason = max((w for _, w in failures), key=lambda r: _PRECEDENCE[r])
    else:
        reason = UnsignedReason.ABSENT
    floor_kwh = policy.state.flat_floor_kwh if isinstance(policy.state, Sized) else 0
    text = (
        f"Critical-facilities sign-off (IC-4 critical_facilities_signoff, issued by the host or "
        f"customer, scoped to site {policy.site_id}) of the resilience floor statement {sh} "
        f"(flat floor {floor_kwh} kWh), verified by confirmation with the issuer: "
        f"{_REASON_TEXT[reason]}"
    )
    return Unsigned(
        kind="unsigned",
        statement_hash=sh,
        as_of=as_of,
        reason=reason,
        candidates=tuple(failures),
        requests=(_signoff_ask(policy, text),),
    )


def approval_label(approval: Approved | Unsigned) -> str:
    """The one closed table of approval wording. An unsigned floor never reads as approved."""
    if isinstance(approval, Approved):
        label = (
            f"Floor signed by {approval.issuer_role}; verified by "
            f"{approval.verifier_role.value} (confirmed with issuer)"
        )
        return label + (" (SYNTHETIC FIXTURE)" if approval.synthetic else "")
    return f"FLOOR UNSIGNED: {_REASON_TEXT[approval.reason]}"


def floor_joules(kwh: int) -> int:
    """Whole kWh to joules: the floor in the kernel's unit, exact."""
    return kwh * J_PER_KWH
