"""``onsite_generation_model`` (WP-SITE-05, spec A3-05): gensets and co-located generation as
declared adequacy resources. Decisions D210 and D215.

`OnsiteGenerationModel` (``gridq.adequacy.onsite_generation_model`` 1.0.0) is a declaration, never
evidence (IC-4). Shared resources are top-level lists that name units by id, so one shape holds a
per-unit permit and a site-wide one:

- `GensetUnit`: rating, start-to-load time, how it runs, and whether its permit lets it run to cover
  a restriction (``curtailment_cover``);
- `FuelStore`: endurance (seconds at rated, a tank and burn curve, or a continuous supply), the
  level at the period start and the resupply lead;
- `RunHourLimit` (permit and emissions limits: MCPD, Specified Generator, other permit, planning
  condition): cap, window, what it counts and the hours used as at a date; or `NoRunHourLimit`, a
  stated "no limit applies" (never inferred from omission);
- `TestRunSchedule` on the GB wall clock, or `NoTestRuns`;
- `ColocatedSource` (AD, EfW, landfill gas, PV, wind): a one-minute output series on the load
  model's clock, or a stated firm kW (never for PV or wind);
- the start contingency (all start, or the k largest units out), detection and transfer time.

**No number has a default.** Every element may be `Unknown`; nothing blocks the run. Each unknown
removes cover on the safe side (zero credit for the units it touches) and becomes a typed ask
(`missing_onsite_asks`). A unit is never unlimited, untested or fuelled by omission.

**IC-4 gap G10.** IC-4 1.2.0 has no evidence type for an environmental permit or an MCPD / Specified
Generator registration. Permit asks name the gap and close with nothing yet; run-hour logs close
with ``operational_data_record``, ratings and start times with ``technical_report`` or
``commissioning_record``, parallel running with ``g99_approval``. No IC-4 change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Final, Literal

import numpy as np
from pydantic import Field, model_validator

from gridq.adequacy._common import NonNegInt, PosInt, RecordClass, ask
from gridq.adequacy.inputs import Declarant
from gridq.adequacy.load import MINUTES_PER_DAY
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
)
from gridq.contracts.evidence import EvidenceType
from gridq.ops.dossier_model import (
    AskHolder,
    ContractGap,
    DossierAsk,
    DossierField,
    GenerationTechnology,
)

__all__ = [
    "AllStart",
    "BurnCurve",
    "BurnPoint",
    "CalendarYear",
    "ColocatedSource",
    "ContinuousSupply",
    "FuelStore",
    "G10",
    "GENSET_TECHNOLOGIES",
    "GensetUnit",
    "LargestUnitsOut",
    "MinuteOutputArray",
    "MinuteSeriesRef",
    "MonthlyNthWeekday",
    "NoResupply",
    "NoRunHourLimit",
    "NoTestRuns",
    "ONSITE_SCHEMA",
    "OnsiteGeneration",
    "OnsiteGenerationModel",
    "ResupplyLead",
    "RollingDays",
    "RunHourLimit",
    "StatedEndurance",
    "StatedFirmKw",
    "StatedFull",
    "StatedLevel",
    "TankAndBurn",
    "TestRunSchedule",
    "UsedAt",
    "Weekly",
    "missing_onsite_asks",
]

ONSITE_SCHEMA: Final = "gridq.adequacy.onsite_generation_model"

#: IC-4 cannot carry an environmental permit or an MCPD / Specified Generator registration.
G10: Final = ContractGap(
    gap_id="IC4-G10",
    contract="IC-4 1.2.0",
    missing="an evidence type for an environmental permit or an MCPD / Specified Generator "
    "registration (the run-hour and emissions limits of a genset)",
    fields=(DossierField.ONSITE_GENERATION,),
    owner="WP-BO-02, with the next IC-4 minor (types only)",
)

GENSET_TECHNOLOGIES: Final = frozenset(
    {
        GenerationTechnology.DIESEL_GENSET,
        GenerationTechnology.GAS_GENSET,
        GenerationTechnology.COMBINED_HEAT_AND_POWER,
    }
)
COLOCATED_TECHNOLOGIES: Final = frozenset(
    {
        GenerationTechnology.ANAEROBIC_DIGESTION,
        GenerationTechnology.ENERGY_FROM_WASTE,
        GenerationTechnology.LANDFILL_GAS,
        GenerationTechnology.SOLAR_PV,
        GenerationTechnology.WIND,
    }
)
_VARIABLE: Final = frozenset({GenerationTechnology.SOLAR_PV, GenerationTechnology.WIND})

UnitIds = Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


def _sorted_unique(ids: tuple[str, ...], what: str) -> None:
    if list(ids) != sorted(set(ids)):
        raise ValueError(f"{what} are sorted, without repeats")


# --------------------------------------------------------------------------- gensets


class GensetUnit(ContractModel):
    """One genset. The order of `OnsiteGenerationModel.gensets` is the stated merit order."""

    unit_id: NonEmptyStr
    technology: GenerationTechnology
    rated_kw: PosInt | Unknown
    #: From the call to carrying rated load, whole seconds (the kernel uses ``ceil(s / 60)`` min).
    start_to_load_s: NonNegInt | Unknown
    operation: Literal["standby_only", "parallel_with_grid", "islanded_failover"] | Unknown
    #: Whether the permit lets it run to cover a restriction, not only emergencies and tests.
    curtailment_cover: Literal["permitted", "not_permitted"] | Unknown

    @model_validator(mode="after")
    def _genset(self) -> GensetUnit:
        if self.technology not in GENSET_TECHNOLOGIES:
            raise ValueError("a genset is diesel, gas or CHP")
        return self


# --------------------------------------------------------------------------- fuel


class StatedEndurance(ContractModel):
    """Unit-seconds at rated output from a full store: two units running burn it twice as fast.
    Fuel is counted at rated for every running second, a safe upper bound (flag ``fuel_bound``)."""

    kind: Literal["stated_endurance"]
    seconds_at_rated: PosInt


class BurnPoint(ContractModel):
    output_kw: NonNegInt
    ml_per_hour: PosInt


class BurnCurve(ContractModel):
    """A unit's stated burn curve: points from idle (0 kW) up to at least rated, non-decreasing,
    read piecewise-linearly in exact arithmetic."""

    unit_id: NonEmptyStr
    points: tuple[BurnPoint, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _curve(self) -> BurnCurve:
        kws = [p.output_kw for p in self.points]
        if kws[0] != 0 or kws != sorted(set(kws)):
            raise ValueError("a burn curve starts at idle (0 kW), outputs strictly increasing")
        mls = [p.ml_per_hour for p in self.points]
        if mls != sorted(mls):
            raise ValueError("a burn curve is non-decreasing")
        return self


class TankAndBurn(ContractModel):
    kind: Literal["tank_and_burn"]
    usable_ml: PosInt
    curves: tuple[BurnCurve, ...] = Field(min_length=1)


class ContinuousSupply(ContractModel):
    """A supply that does not run down on the simulated timescale (e.g. piped gas), stated."""

    kind: Literal["continuous_supply"]
    statement: NonEmptyStr


class StatedFull(ContractModel):
    kind: Literal["full"]


class StatedLevel(ContractModel):
    """The level at the period start: unit-seconds at rated, or ml for a tank."""

    kind: Literal["stated_level"]
    amount: NonNegInt


class ResupplyLead(ContractModel):
    """The store is refilled to full when this lead fits between one run's end and the next."""

    kind: Literal["resupply_lead"]
    lead_s: PosInt


class NoResupply(ContractModel):
    kind: Literal["no_resupply"]


class FuelStore(ContractModel):
    store_id: NonEmptyStr
    unit_ids: UnitIds
    endurance: StatedEndurance | TankAndBurn | ContinuousSupply | Unknown = Field(
        discriminator="kind"
    )
    opening: StatedFull | StatedLevel | NotApplicable | Unknown = Field(discriminator="kind")
    resupply: ResupplyLead | NoResupply | NotApplicable | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _store(self) -> FuelStore:
        _sorted_unique(self.unit_ids, "a store's unit ids")
        continuous = isinstance(self.endurance, ContinuousSupply)
        for name in ("opening", "resupply"):
            if isinstance(getattr(self, name), NotApplicable) != continuous:
                raise ValueError(f"{name} is not_applicable exactly for a continuous supply")
        if isinstance(self.endurance, TankAndBurn):
            ids = [c.unit_id for c in self.endurance.curves]
            _sorted_unique(tuple(ids), "burn curves")
            if not set(ids) <= set(self.unit_ids):
                raise ValueError("a burn curve names a unit the store does not feed")
        return self


# --------------------------------------------------------------------------- run-hour limits


class CalendarYear(ContractModel):
    """1 January to 31 December on the GB clock."""

    kind: Literal["calendar_year"]


class RollingDays(ContractModel):
    kind: Literal["rolling_days"]
    days: PosInt


class UsedAt(ContractModel):
    """Run seconds already counted against the limit as at 00:00 GB time on ``as_of``."""

    kind: Literal["used_at"]
    as_of: IsoDate
    seconds: NonNegInt


LimitBasis = Literal[
    "mcpd", "specified_generator", "environmental_permit_other", "planning_condition"
]


class RunHourLimit(ContractModel):
    """A permit or emissions run-hour limit over one unit or several (a site-wide permit)."""

    limit_id: NonEmptyStr
    unit_ids: UnitIds
    basis: LimitBasis
    cap_s: NonNegInt | Unknown
    window: CalendarYear | RollingDays | Unknown = Field(discriminator="kind")
    #: ``non_emergency`` counts tests and restriction cover alike (both are non-emergency here).
    counts: Literal["all_running", "test_only", "non_emergency"] | Unknown
    used: UsedAt | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _ids(self) -> RunHourLimit:
        _sorted_unique(self.unit_ids, "a limit's unit ids")
        return self


class NoRunHourLimit(ContractModel):
    """A stated "no run-hour limit applies" for these units, with the reason."""

    unit_ids: UnitIds
    reason: NonEmptyStr


# --------------------------------------------------------------------------- tests


class Weekly(ContractModel):
    kind: Literal["weekly"]
    iso_weekday: Annotated[int, Field(ge=1, le=7)]


class MonthlyNthWeekday(ContractModel):
    kind: Literal["monthly_nth_weekday"]
    n: Annotated[int, Field(ge=1, le=5)]
    iso_weekday: Annotated[int, Field(ge=1, le=7)]


class TestRunSchedule(ContractModel):
    """A recurring test on the Europe/London wall clock. A non-existent local start moves to the
    next valid minute; a repeated one takes its first occurrence. A test is never cover."""

    __test__ = False  # not a pytest class

    schedule_id: NonEmptyStr
    unit_ids: UnitIds
    recurrence: Weekly | MonthlyNthWeekday | Unknown = Field(discriminator="kind")
    local_start_minute: Annotated[int, Field(ge=0, le=1439)]
    duration_s: PosInt | Unknown
    mode: Literal["off_load", "load_bank", "site_load"] | Unknown

    @model_validator(mode="after")
    def _ids(self) -> TestRunSchedule:
        _sorted_unique(self.unit_ids, "a schedule's unit ids")
        return self


class NoTestRuns(ContractModel):
    """A stated "these units are not test-run" (never inferred from omission)."""

    __test__ = False

    unit_ids: UnitIds
    reason: NonEmptyStr


# --------------------------------------------------------------------------- co-located


class MinuteSeriesRef(ContractModel):
    """A one-minute output series supplied beside the model (`MinuteOutputArray`), by digest."""

    kind: Literal["minute_series"]
    series_digest: Sha256


class StatedFirmKw(ContractModel):
    kind: Literal["stated_firm_kw"]
    kw: PosInt


class ColocatedSource(ContractModel):
    source_id: NonEmptyStr
    technology: GenerationTechnology
    output: MinuteSeriesRef | StatedFirmKw | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _source(self) -> ColocatedSource:
        if self.technology not in COLOCATED_TECHNOLOGIES:
            raise ValueError("a co-located source is AD, EfW, landfill gas, PV or wind")
        if self.technology in _VARIABLE and isinstance(self.output, StatedFirmKw):
            raise ValueError("PV and wind state an output series, never a firm kW")
        return self


# --------------------------------------------------------------------------- the model


class AllStart(ContractModel):
    kind: Literal["all_start"]


class LargestUnitsOut(ContractModel):
    """The k largest committed units are assumed not to start (N+k), deterministically."""

    kind: Literal["largest_units_out"]
    k: PosInt


class OnsiteGenerationModel(ContractModel):
    """``gridq.adequacy.onsite_generation_model`` 1.0.0 (D210). Its identity is its
    content hash: a requirement's ``standby_ref`` names that hash (D215 (g))."""

    schema_name: Literal["gridq.adequacy.onsite_generation_model"] = ONSITE_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    statement: Literal["assets_listed", "no_onsite_generation"]
    gensets: tuple[GensetUnit, ...]
    fuel_stores: tuple[FuelStore, ...]
    run_limits: tuple[RunHourLimit, ...]
    no_run_hour_limit: tuple[NoRunHourLimit, ...]
    test_runs: tuple[TestRunSchedule, ...]
    no_test_runs: tuple[NoTestRuns, ...]
    colocated: tuple[ColocatedSource, ...]
    start_contingency: AllStart | LargestUnitsOut | NotApplicable | Unknown = Field(
        discriminator="kind"
    )
    #: Seconds from loss of supply to the call (handover only).
    detection_s: NonNegInt | NotApplicable | Unknown
    #: Seconds from a set on load to the protected load transferred (handover only).
    transfer_s: NonNegInt | NotApplicable | Unknown
    declared_by: Declarant

    @model_validator(mode="after")
    def _consistent(self) -> OnsiteGenerationModel:
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")
        lists = (
            self.gensets,
            self.fuel_stores,
            self.run_limits,
            self.no_run_hour_limit,
            self.test_runs,
            self.no_test_runs,
            self.colocated,
        )
        if (self.statement == "no_onsite_generation") != (not any(lists)):
            raise ValueError("assets_listed needs an asset; no_onsite_generation, none at all")
        units = [g.unit_id for g in self.gensets]
        if len(set(units)) != len(units):
            raise ValueError("unit ids are unique")
        for name, ids in (
            ("store", [s.store_id for s in self.fuel_stores]),
            ("limit", [r.limit_id for r in self.run_limits]),
            ("schedule", [t.schedule_id for t in self.test_runs]),
            ("source", [c.source_id for c in self.colocated]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} ids are unique")
        known = set(units)
        named: list[tuple[str, ...]] = [
            *(g.unit_ids for g in self.fuel_stores),
            *(g.unit_ids for g in self.run_limits),
            *(g.unit_ids for g in self.no_run_hour_limit),
            *(g.unit_ids for g in self.test_runs),
            *(g.unit_ids for g in self.no_test_runs),
        ]
        if any(not set(ids) <= known for ids in named):
            raise ValueError("every unit id named resolves to a genset")
        limited = {u for r in self.run_limits for u in r.unit_ids}
        if limited & {u for n in self.no_run_hour_limit for u in n.unit_ids}:
            raise ValueError("a unit cannot have a run-hour limit and a no-limit statement")
        tested = {u for t in self.test_runs for u in t.unit_ids}
        if tested & {u for n in self.no_test_runs for u in n.unit_ids}:
            raise ValueError("a unit cannot have a test schedule and a no-test statement")
        stores = [u for s in self.fuel_stores for u in s.unit_ids]
        if len(set(stores)) != len(stores):
            raise ValueError("a unit draws on one fuel store")
        rated = {g.unit_id: g.rated_kw for g in self.gensets}
        for s in self.fuel_stores:
            if isinstance(s.endurance, TankAndBurn):
                for c in s.endurance.curves:
                    r = rated[c.unit_id]
                    if isinstance(r, int) and c.points[-1].output_kw < r:
                        raise ValueError(f"the burn curve of {c.unit_id} stops below its rating")
        switching = (self.start_contingency, self.detection_s, self.transfer_s)
        if not self.gensets:
            if not all(isinstance(v, NotApplicable) for v in switching):
                raise ValueError("with no gensets, contingency, detection and transfer are n/a")
        elif any(isinstance(v, NotApplicable) for v in switching):
            raise ValueError("with gensets, contingency, detection and transfer are stated")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------- series and bundle


@dataclass(frozen=True)
class MinuteOutputArray:
    """A co-located source's one-minute output in integer watts, ``(1 | draws, days x 1440)``,
    on the load model's naive local clock (D170), read through the same load anchor."""

    source_id: str
    site_id: str
    record_class: RecordClass
    horizon_start: date
    output_w: np.ndarray

    def __post_init__(self) -> None:
        o = self.output_w
        if o.ndim != 2 or o.shape[1] % MINUTES_PER_DAY or o.dtype != np.int64:
            raise ValueError("output_w is int64 watts, (rows, whole days x 1440)")
        if (o < 0).any():
            raise ValueError("output is non-negative")
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")

    @property
    def horizon_end(self) -> date:
        return self.horizon_start + timedelta(days=self.output_w.shape[1] // MINUTES_PER_DAY)

    @property
    def digest(self) -> str:
        h = hashlib.sha256(b"gridq.adequacy.onsite_series/1")
        h.update(
            f"{self.source_id}|{self.horizon_start.isoformat()}|{self.output_w.shape}".encode()
        )
        h.update(self.output_w.astype("<i8").tobytes())
        return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class OnsiteGeneration:
    """The declared model and the output series its `MinuteSeriesRef` entries name. Its hash is
    the model's: each series is bound into the model by digest."""

    model: OnsiteGenerationModel
    series: tuple[MinuteOutputArray, ...] = field(default=())

    def __post_init__(self) -> None:
        refs = {
            c.source_id: c.output.series_digest
            for c in self.model.colocated
            if isinstance(c.output, MinuteSeriesRef)
        }
        got = {a.source_id: a for a in self.series}
        if len(got) != len(self.series) or set(got) != set(refs):
            raise ValueError("one output series per source that states a minute series")
        for sid, a in got.items():
            if a.digest != refs[sid]:
                raise ValueError(f"the series for {sid} is not the one the model names")
            if a.site_id != self.model.site_id:
                raise ValueError(f"the series for {sid} names another site")

    def content_hash(self) -> str:
        return self.model.content_hash()

    def series_for(self, source_id: str) -> MinuteOutputArray:
        return next(a for a in self.series if a.source_id == source_id)


# --------------------------------------------------------------------------- asks


def _evidence(*types: EvidenceType) -> tuple[EvidenceType, ...]:
    return types


def missing_onsite_asks(model: OnsiteGenerationModel) -> tuple[tuple[str, DossierAsk], ...]:
    """``(field, ask)`` for every unknown and every unstated fact a unit needs for credit, in a
    fixed order. Each removes cover on the safe side; none blocks the run."""
    site = model.site_id
    holder = AskHolder.HOST if model.declared_by.role == "host" else AskHolder.CUSTOMER
    out: list[tuple[str, DossierAsk]] = []

    def add(name: str, text: str, closes: tuple[EvidenceType, ...] = ()) -> None:
        out.append((name, ask(text, site_id=site, holder=holder, closes_with=closes)))

    tech = _evidence(EvidenceType.TECHNICAL_REPORT, EvidenceType.COMMISSIONING_RECORD)
    for g in model.gensets:
        u = g.unit_id
        if isinstance(g.rated_kw, Unknown):
            add(f"{u}.rated_kw", f"State the rated output (kW) of genset {u}", tech)
        if isinstance(g.start_to_load_s, Unknown):
            add(
                f"{u}.start_to_load_s",
                f"State the time (seconds) genset {u} takes from a call to carrying rated load",
                tech,
            )
        if isinstance(g.operation, Unknown):
            add(
                f"{u}.operation",
                f"State how genset {u} runs: standby only, in parallel with the grid (with its "
                "G99 approval), or islanded failover",
                _evidence(EvidenceType.G99_APPROVAL),
            )
        if isinstance(g.curtailment_cover, Unknown):
            add(
                f"{u}.curtailment_cover",
                f"State whether the permit of genset {u} lets it run to cover a grid import "
                f"restriction, not only emergencies and tests (IC-4 has no permit type yet: "
                f"gap {G10.gap_id})",
            )
    limited = {u for r in model.run_limits for u in r.unit_ids} | {
        u for n in model.no_run_hour_limit for u in n.unit_ids
    }
    tested = {u for t in model.test_runs for u in t.unit_ids} | {
        u for n in model.no_test_runs for u in n.unit_ids
    }
    fuelled = {u for s in model.fuel_stores for u in s.unit_ids}
    for g in model.gensets:
        u = g.unit_id
        if u not in limited:
            add(
                f"{u}.run_limits",
                f"State the permit and emissions run-hour limits covering genset {u} (MCPD, "
                f"Specified Generator, other permit or planning condition), or that none applies "
                f"(IC-4 has no permit type yet: gap {G10.gap_id})",
            )
        if u not in tested:
            add(
                f"{u}.test_runs",
                f"State the test-run schedule of genset {u}, or that it is not test-run",
            )
        if u not in fuelled:
            add(f"{u}.fuel", f"State the fuel store feeding genset {u} and its endurance")
    for r in model.run_limits:
        lid = r.limit_id
        if isinstance(r.cap_s, Unknown):
            add(
                f"{lid}.cap_s",
                f"State the run-hour cap of limit {lid} (from the permit or registration; "
                f"IC-4 gap {G10.gap_id})",
            )
        if isinstance(r.window, Unknown):
            add(
                f"{lid}.window",
                f"State the window of limit {lid}: calendar year or rolling days",
            )
        if isinstance(r.counts, Unknown):
            add(
                f"{lid}.counts",
                f"State what limit {lid} counts: all running, tests only, or non-emergency running",
            )
        if isinstance(r.used, Unknown):
            add(
                f"{lid}.used",
                f"State the run hours already counted against limit {lid} and the date they are "
                "counted to (the run-hour log)",
                _evidence(EvidenceType.OPERATIONAL_DATA_RECORD),
            )
    for t in model.test_runs:
        sid = t.schedule_id
        if isinstance(t.recurrence, Unknown):
            add(f"{sid}.recurrence", f"State when test schedule {sid} recurs")
        if isinstance(t.duration_s, Unknown):
            add(f"{sid}.duration_s", f"State how long each run of test schedule {sid} lasts")
        if isinstance(t.mode, Unknown):
            add(
                f"{sid}.mode",
                f"State how test schedule {sid} runs: off load, on a load bank, or on site load",
            )
    for s in model.fuel_stores:
        st = s.store_id
        if isinstance(s.endurance, Unknown):
            add(
                f"{st}.endurance",
                f"State the endurance of fuel store {st}: seconds at rated output, or its "
                "usable volume and each unit's burn curve, or that it is a continuous supply",
                tech,
            )
        if isinstance(s.endurance, TankAndBurn):
            curved = {c.unit_id for c in s.endurance.curves}
            for u in s.unit_ids:
                if u not in curved:
                    add(f"{st}.{u}.burn", f"State the burn curve of genset {u} (ml/h by kW)", tech)
        if isinstance(s.opening, Unknown):
            add(f"{st}.opening", f"State the level of fuel store {st} at the period start")
        if isinstance(s.resupply, Unknown):
            add(
                f"{st}.resupply",
                f"State the resupply lead time of fuel store {st}, or that it is not resupplied",
            )
    for c in model.colocated:
        if isinstance(c.output, Unknown):
            add(
                f"{c.source_id}.output",
                f"State the output of co-located source {c.source_id}: a one-minute series "
                "(metered or modelled), or a firm kW for a dispatchable plant",
                _evidence(EvidenceType.METERED_DATA_RECORD),
            )
    for name in ("start_contingency", "detection_s", "transfer_s"):
        if isinstance(getattr(model, name), Unknown):
            text = {
                "start_contingency": "State the genset start contingency: every committed unit "
                "starts, or the k largest are assumed not to",
                "detection_s": "State the time (seconds) from loss of supply to the genset call",
                "transfer_s": "State the time (seconds) from a set on load to the protected load "
                "transferred",
            }[name]
            add(name, text)
    return tuple(out)
