"""Genset dispatch and the run ledger (WP-SITE-05, D211 and D215).

**The plan is draw-independent and never reads the battery** (so the kernel is untouched and the
required energy stays a single pass, `gridq.adequacy.required`). Per scenario and thermal arm:

1. *Co-located output* (series, or a stated firm kW) lowers the net load first.
2. *Tests* (pass 1): every scheduled test on the GB wall clock, from the earliest opening balance to
   the period end, is granted through the ledger. A test is never capacity: it changes neither the
   net load nor the charging headroom.
3. *Cover* (pass 2, chronological): for each restriction event, the ensemble need is the largest
   ``net - permit`` over the draws. The fewest credited units in merit order (the model's order)
   whose ratings, less the k largest committed, cover the event's peak need are committed; each is
   called at ``max(first need - start time, event start - notice)`` and runs to the last minute of
   need. Output is zero until the start time has elapsed, then rated.
4. The genset output array is the rated output of the units online, less the k largest online; the
   kernel sees ``net - min(genset, max(net - permit, 0))``: gensets serve need, never charge.

**Run-hour limits are never exceeded (acceptance 1).** `RunLedger._grant` is the only constructor of
`GensetRun`. It grants the longest prefix of a requested run (whole minutes, rounded outward: a
minute run counts 60 s) under which every enforced limit and fuel store still holds, counting every
window brute-force-equivalently (a calendar year on the GB clock, or every rolling window), opening
use included. A limit over several units counts unit-seconds (a safe reading of a site-wide cap).

**Fuel** is a ledger limit too: a stated endurance burns one unit-second per running second; a tank
burns each unit's curve at its share of the ensemble need (idle while starting), rounded up. A store
is refilled to full only when its resupply lead fits between one run's end and the next call.

Exclusions (recorded on the result): grid-loss running is not simulated; a unit's tests are not
moved for an event; a unit cut by a limit is not replaced by another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import model_validator

from gridq.adequacy._common import ExactRational, NonNegInt, ask
from gridq.adequacy.onsite import (
    AllStart,
    BurnCurve,
    CalendarYear,
    ContinuousSupply,
    FuelStore,
    LargestUnitsOut,
    MinuteSeriesRef,
    MonthlyNthWeekday,
    NoResupply,
    OnsiteGeneration,
    ResupplyLead,
    RollingDays,
    RunHourLimit,
    StatedEndurance,
    StatedFirmKw,
    StatedFull,
    StatedLevel,
    TankAndBurn,
    TestRunSchedule,
    UsedAt,
    Weekly,
)
from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Unknown, not_applicable
from gridq.ops.dossier_model import AskHolder, DossierAsk

if TYPE_CHECKING:
    from gridq.adequacy.simulator import _Prepared

__all__ = [
    "EXCLUSIONS",
    "GenerationArrays",
    "GenerationFinding",
    "GensetRun",
    "LimitUse",
    "RunLedger",
    "ScenarioGeneration",
    "SourceCredit",
    "StoreUse",
    "UnitCredit",
    "plan_generation",
]

_TZ: Final = ZoneInfo("Europe/London")
EXCLUSIONS: Final = (
    "Grid-loss (islanded) running is not simulated: gensets are dispatched only to cover import "
    "restrictions, in parallel with the grid.",
    "A scheduled test is not moved for an event, and a test is never capacity.",
    "A unit whose run a limit cuts short is not replaced by another unit.",
)


# --------------------------------------------------------------------------- records


class GensetRun(ContractModel):
    """One granted run, in minutes from the scenario's period start (negative: pre-rolled before
    it). ``end_min == start_min`` records a request refused outright."""

    unit_id: NonEmptyStr
    purpose: Literal["test", "cover"]
    #: The test schedule id, or ``event:<index>``.
    ref: NonEmptyStr
    start_min: int
    requested_end_min: int
    end_min: int
    #: Cover only: output starts here (call plus start time); the unit runs but gives none before.
    online_min: int | NotApplicable
    #: The limits and stores that cut the run short (empty when granted in full).
    cut_by: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _run(self) -> GensetRun:
        if not self.start_min <= self.end_min <= self.requested_end_min:
            raise ValueError("a run is granted a prefix of what was requested")
        if (self.end_min < self.requested_end_min) != bool(self.cut_by):
            raise ValueError("a run is cut short exactly when a limit or store cut it")
        if (self.purpose == "cover") == isinstance(self.online_min, NotApplicable):
            raise ValueError("a cover run states when it is online; a test does not")
        return self


class LimitUse(ContractModel):
    limit_id: NonEmptyStr
    status: Literal["enforced", "not_enforced"]
    cap_s: NonNegInt | NotApplicable
    opening_s: NonNegInt | NotApplicable
    #: Minutes from the period start at which counting starts (the opening balance's date, or the
    #: year start or window reach when that is later).
    counted_from_min: int | NotApplicable
    #: The largest count in any one window over the scenario (opening use included).
    peak_s: NonNegInt | NotApplicable


class StoreUse(ContractModel):
    store_id: NonEmptyStr
    status: Literal["enforced", "not_enforced", "continuous"]
    unit: Literal["unit_seconds", "ml"] | NotApplicable
    opening: NonNegInt | NotApplicable
    capacity: NonNegInt | NotApplicable
    #: The largest use between refills.
    peak_segment_use: NonNegInt | NotApplicable
    refills: NonNegInt


class UnitCredit(ContractModel):
    unit_id: NonEmptyStr
    credited: bool
    reasons: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _why(self) -> UnitCredit:
        if self.credited == bool(self.reasons):
            raise ValueError("a unit has no credit exactly when a reason says why")
        return self


class SourceCredit(ContractModel):
    source_id: NonEmptyStr
    basis: Literal["minute_series", "stated_firm_kw", "none"]


class GenerationFinding(ContractModel):
    code: Literal["TEST_SCHEDULE_EXCEEDS_LIMIT", "OPENING_BALANCE_STALE"]
    #: ``nonconforming`` withholds approval for use; ``advisory`` does not.
    severity: Literal["nonconforming", "advisory"]
    scenario_id: NonEmptyStr
    detail: NonEmptyStr


class ScenarioGeneration(ContractModel):
    scenario_id: NonEmptyStr
    thermal: Literal["off", "ic11"]
    #: Minutes before the period start that tests were pre-rolled through.
    preroll_min: NonNegInt
    contingency_k: NonNegInt | NotApplicable
    units: tuple[UnitCredit, ...]
    sources: tuple[SourceCredit, ...]
    runs: tuple[GensetRun, ...]
    limits: tuple[LimitUse, ...]
    stores: tuple[StoreUse, ...]
    findings: tuple[GenerationFinding, ...]
    #: Genset energy delivered against need, joules: the largest over draws, and the mean.
    delivered_ws_max: NonNegInt
    delivered_ws_mean: ExactRational
    #: Co-located energy taken off the net load, joules, mean over draws.
    colocated_ws_mean: ExactRational


# --------------------------------------------------------------------------- arrays


@dataclass(frozen=True)
class GenerationArrays:
    """What the kernel sees: co-located output ``(1 | N, M)`` W and genset output ``(M,)`` W."""

    colocated_w: np.ndarray
    genset_w: np.ndarray

    def apply(self, net: np.ndarray, permit_w: np.ndarray) -> np.ndarray:
        """``net - colocated`` (never below zero), then gensets serve need only."""
        n1 = np.maximum(net - self.colocated_w, 0)
        need = np.maximum(n1 - permit_w[None, :], 0)
        out: np.ndarray = n1 - np.minimum(self.genset_w[None, :], need)
        return out

    def without_gensets(self) -> GenerationArrays:
        return GenerationArrays(self.colocated_w, np.zeros_like(self.genset_w))


# --------------------------------------------------------------------------- clock helpers


def _utc_of_local(day: date, minute: int) -> datetime:
    """The UTC instant of a GB wall-clock minute: a non-existent minute moves to the next valid
    one; a repeated minute takes its first occurrence."""
    for step in range(0, 181):
        naive = datetime.combine(day, time()) + timedelta(minutes=minute + step)
        aware = naive.replace(tzinfo=_TZ, fold=0)
        utc = aware.astimezone(UTC)
        if utc.astimezone(_TZ).replace(tzinfo=None) == naive:
            return utc
    raise AssertionError("no valid local minute within three hours")  # pragma: no cover


def _minutes(a: datetime, b: datetime) -> int:
    """Whole minutes from ``a`` to ``b`` (floor)."""
    return int((b - a).total_seconds() // 60)


def _occurs(rec: Weekly | MonthlyNthWeekday, day: date) -> bool:
    if day.isoweekday() != rec.iso_weekday:
        return False
    if isinstance(rec, Weekly):
        return True
    return (day.day - 1) // 7 + 1 == rec.n


def _ml_per_hour(curve: BurnCurve, kw: int) -> Fraction:
    pts = curve.points
    for a, b in zip(pts, pts[1:], strict=False):
        if a.output_kw <= kw <= b.output_kw:
            span = b.output_kw - a.output_kw
            return (
                a.ml_per_hour + Fraction(b.ml_per_hour - a.ml_per_hour) * (kw - a.output_kw) / span
            )
    return Fraction(pts[-1].ml_per_hour)


def _ceil(f: Fraction) -> int:
    return -(-f.numerator // f.denominator)


# --------------------------------------------------------------------------- ledger


@dataclass
class _Limit:
    limit_id: str
    units: tuple[str, ...]
    cap_s: int
    window_min: int | None  # None: calendar year
    counts_test: bool
    counts_cover: bool
    start: int  # axis index from which minutes count
    opening_s: int
    opening_at: int  # axis index the opening use is incurred at


@dataclass
class _Store:
    store_id: str
    units: tuple[str, ...]
    capacity: int
    opening: int
    lead_min: int | None  # None: no refill


@dataclass
class RunLedger:
    """Every unit's running minutes on one axis, and the single grant choke point.

    Axis index ``i`` is minute ``i - p0`` from the period start; fuel counts from ``p0``."""

    length: int
    p0: int
    limits: list[_Limit]
    stores: list[_Store]
    year_starts: tuple[int, ...]  # axis indices of 1 January 00:00 GB inside the axis
    test_mask: dict[str, np.ndarray] = field(default_factory=dict)
    cover_mask: dict[str, np.ndarray] = field(default_factory=dict)
    burn: dict[str, np.ndarray] = field(default_factory=dict)
    runs: list[GensetRun] = field(default_factory=list)

    def _mask(self, table: dict[str, np.ndarray], unit: str) -> np.ndarray:
        if unit not in table:
            table[unit] = np.zeros(self.length, dtype=bool)
        return table[unit]

    def _burn(self, unit: str) -> np.ndarray:
        if unit not in self.burn:
            self.burn[unit] = np.zeros(self.length, dtype=np.int64)
        return self.burn[unit]

    # ----- counting

    def _counted(self, lim: _Limit, extra: tuple[str, int, int, str] | None = None) -> np.ndarray:
        """Unit-minutes counted against ``lim`` per axis minute (a unit-minute counts once)."""
        c = np.zeros(self.length, dtype=np.int64)
        for u in lim.units:
            m = np.zeros(self.length, dtype=bool)
            if lim.counts_test:
                m |= self._mask(self.test_mask, u)
            if lim.counts_cover:
                m |= self._mask(self.cover_mask, u)
            if extra is not None and extra[0] == u:
                _, a, b, purpose = extra
                if (purpose == "test" and lim.counts_test) or (
                    purpose == "cover" and lim.counts_cover
                ):
                    m = m.copy()
                    m[a:b] = True
            c += m
        c[: lim.start] = 0
        return c

    def peak_s(self, lim: _Limit, extra: tuple[str, int, int, str] | None = None) -> int:
        """The largest count (seconds) in any window of ``lim`` from its start, opening included."""
        c = self._counted(lim, extra)
        if lim.window_min is None:
            edges = [lim.start, *(y for y in self.year_starts if y > lim.start), self.length]
            best = 0
            for a, b in zip(edges, edges[1:], strict=False):
                if a >= b:
                    continue
                total = 60 * int(c[a:b].sum())
                if a <= lim.opening_at < b:
                    total += lim.opening_s
                best = max(best, total)
            if lim.opening_at >= self.length:
                best = max(best, lim.opening_s)
            return best
        cs = np.concatenate(([0], np.cumsum(c)))
        t = np.arange(lim.start, self.length)
        if t.size == 0:
            return lim.opening_s
        lo = np.maximum(t - lim.window_min + 1, lim.start)
        win = 60 * (cs[t + 1] - cs[lo])
        win = win + np.where(
            (t >= lim.opening_at) & (t < lim.opening_at + lim.window_min), lim.opening_s, 0
        )
        return int(win.max())

    def store_use(
        self, st: _Store, extra: tuple[str, int, int, np.ndarray] | None = None
    ) -> tuple[int, int, bool]:
        """(largest use between refills, refills, whether every segment fits its level)."""
        p0 = self.p0
        burn = np.zeros(self.length - p0, dtype=np.int64)
        run = np.zeros(self.length - p0, dtype=bool)
        for u in st.units:
            running = self._mask(self.test_mask, u) | self._mask(self.cover_mask, u)
            b = self._burn(u)
            if extra is not None and extra[0] == u:
                _, a, e, rate = extra
                b = b.copy()
                running = running.copy()
                b[a:e] = np.maximum(b[a:e], rate)
                running[a:e] = True
            burn += b[p0:]
            run |= running[p0:]
        if not run.any():
            return 0, 0, True
        d = np.diff(np.concatenate(([0], run.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        cs = np.concatenate(([0], np.cumsum(burn)))
        level = st.opening
        seg = 0
        peak, refills, ok = 0, 0, True
        for i, (s0, s1) in enumerate(zip(starts.tolist(), ends.tolist(), strict=True)):
            if i and st.lead_min is not None and s0 - int(ends[i - 1]) >= st.lead_min:
                refills += 1
                level, seg = st.capacity, 0
            seg += int(cs[s1] - cs[s0])
            peak = max(peak, seg)
            if seg > level:
                ok = False
        return peak, refills, ok

    def _breaches(
        self, unit: str, a: int, b: int, purpose: str, rate: np.ndarray
    ) -> tuple[str, ...]:
        out: list[str] = []
        for lim in self.limits:
            if unit in lim.units and (lim.counts_test if purpose == "test" else lim.counts_cover):
                if self.peak_s(lim, (unit, a, b, purpose)) > lim.cap_s:
                    out.append(lim.limit_id)
        for st in self.stores:
            if unit in st.units:
                aa, bb = max(a, self.p0), max(b, self.p0)
                if aa < bb:
                    r = rate[aa - a : bb - a]
                    if not self.store_use(st, (unit, aa, bb, r))[2]:
                        out.append(st.store_id)
        return tuple(out)

    def _grant(
        self,
        unit: str,
        a: int,
        b: int,
        purpose: Literal["test", "cover"],
        ref: str,
        rate: np.ndarray,
        online: int | None,
    ) -> GensetRun:
        """The longest prefix of ``[a, b)`` under which every enforced limit and store holds.
        The only place a run is granted (a structural test asserts it)."""
        lo, hi = 0, b - a
        if self._breaches(unit, a, b, purpose, rate):
            hi -= 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._breaches(unit, a, a + mid, purpose, rate[:mid]):
                    hi = mid - 1
                else:
                    lo = mid
        g = hi
        cut = () if g == b - a else self._breaches(unit, a, a + g + 1, purpose, rate[: g + 1])
        if g:
            mask = self._mask(self.test_mask if purpose == "test" else self.cover_mask, unit)
            mask[a : a + g] = True
            bn = self._burn(unit)
            bn[a : a + g] = np.maximum(bn[a : a + g], rate[:g])
        run = GensetRun(
            unit_id=unit,
            purpose=purpose,
            ref=ref,
            start_min=a - self.p0,
            requested_end_min=b - self.p0,
            end_min=a + g - self.p0,
            online_min=online - self.p0 if online is not None else not_applicable("a test"),
            cut_by=tuple(sorted(set(cut))),
        )
        self.runs.append(run)
        return run


# --------------------------------------------------------------------------- planning


def _period_start(p: _Prepared) -> datetime:
    ps = p.scenario.period_start
    return ps if ps.tzinfo is not None else ps.replace(tzinfo=UTC)


def _as_of_instant(d: date) -> datetime:
    return _utc_of_local(d, 0)


def _year_start(year: int) -> datetime:
    return _utc_of_local(date(year, 1, 1), 0)


def plan_generation(
    gen: OnsiteGeneration,
    p: _Prepared,
    base_net: np.ndarray,
    *,
    horizon_start: date,
    thermal: Literal["off", "ic11"] = "off",
) -> tuple[GenerationArrays, ScenarioGeneration, tuple[DossierAsk, ...]]:
    """Plan one scenario (D211). ``base_net`` is the net load after thermal flex,
    ``(N, M)`` W. Returns the arrays the kernel folds in, the record, and plan-level asks."""
    model = gen.model
    sc = p.scenario
    sid = sc.scenario_id
    n, m = base_net.shape
    site = model.site_id
    holder = AskHolder.HOST if model.declared_by.role == "host" else AskHolder.CUSTOMER
    asks: list[DossierAsk] = []
    findings: list[GenerationFinding] = []
    reasons: dict[str, list[str]] = {g.unit_id: [] for g in model.gensets}
    ps = _period_start(p)

    # ----- co-located output
    col = np.zeros((1, m), dtype=np.int64)
    sources: list[SourceCredit] = []
    for c in model.colocated:
        if isinstance(c.output, MinuteSeriesRef):
            arr = gen.series_for(c.source_id)
            if arr.horizon_start != horizon_start:
                raise ValueError(f"the series for {c.source_id} is not on the load's clock")
            assert p.idx is not None
            if int(p.idx.max()) >= arr.output_w.shape[1]:
                raise ValueError(f"the series for {c.source_id} does not cover the scenario")
            rows = arr.output_w.shape[0]
            if rows not in (1, n):
                raise ValueError(f"the series for {c.source_id} has neither 1 row nor one per draw")
            col = col + arr.output_w[:, p.idx]
            sources.append(SourceCredit(source_id=c.source_id, basis="minute_series"))
        elif isinstance(c.output, StatedFirmKw):
            col = col + c.output.kw * 1000
            sources.append(SourceCredit(source_id=c.source_id, basis="stated_firm_kw"))
        else:
            sources.append(SourceCredit(source_id=c.source_id, basis="none"))
    net1 = np.maximum(base_net - col, 0)
    need = np.maximum(net1 - p.permit_w[None, :], 0)
    need_env = need.max(axis=0) if n else np.zeros(m, dtype=np.int64)

    # ----- unit credit, part 1: the unit's own facts
    rated: dict[str, int] = {}
    start_min: dict[str, int] = {}
    for g in model.gensets:
        u, r = g.unit_id, reasons[g.unit_id]
        if isinstance(g.rated_kw, int):
            rated[u] = g.rated_kw
        else:
            r.append("rating unknown")
        if isinstance(g.start_to_load_s, int):
            start_min[u] = -(-g.start_to_load_s // 60)
        else:
            r.append("start time unknown")
        if isinstance(g.operation, Unknown):
            r.append("operation unknown")
        elif g.operation != "parallel_with_grid":
            r.append(
                f"runs {g.operation.replace('_', ' ')}: it cannot cover a restriction in "
                "parallel with the grid"
            )
        if isinstance(g.curtailment_cover, Unknown):
            r.append("whether the permit allows restriction cover is unknown")
        elif g.curtailment_cover == "not_permitted":
            r.append("the permit does not allow running to cover a restriction")
    k: int | None
    if isinstance(model.start_contingency, LargestUnitsOut):
        k = model.start_contingency.k
    elif isinstance(model.start_contingency, AllStart):
        k = 0
    else:
        k = None
        for r in reasons.values():
            r.append("start contingency unknown")

    # ----- limits: opening balances and the axis
    limit_specs: list[tuple[RunHourLimit, int, int, int]] = []  # (lim, start, open_s, open_at)
    unenforced: list[RunHourLimit] = []
    stale_gap: dict[str, int] = {}
    for lim in model.run_limits:
        if (
            isinstance(lim.cap_s, Unknown)
            or isinstance(lim.window, Unknown)
            or isinstance(lim.counts, Unknown)
            or isinstance(lim.used, Unknown)
        ):
            unenforced.append(lim)
            for u in lim.unit_ids:
                reasons[u].append(f"limit {lim.limit_id} is not fully stated")
            continue
        assert isinstance(lim.used, UsedAt)
        at = _as_of_instant(lim.used.as_of)
        if at > ps:
            unenforced.append(lim)
            for u in lim.unit_ids:
                reasons[u].append(
                    f"the opening balance of limit {lim.limit_id} is dated after the period start"
                )
            asks.append(
                ask(
                    f"State the run hours counted against limit {lim.limit_id} as at a date on or "
                    f"before {ps.astimezone(_TZ).date().isoformat()} (the balance held is dated "
                    f"{lim.used.as_of.isoformat()}, after the scenario starts)",
                    site_id=site,
                    holder=holder,
                )
            )
            continue
        rel_at = _minutes(ps, at)  # <= 0
        if rel_at < 0:
            stale_gap[lim.limit_id] = -rel_at * 60
        if isinstance(lim.window, CalendarYear):
            year0 = _minutes(ps, _year_start(ps.astimezone(_TZ).year))
            if rel_at >= year0:
                start, opening = rel_at, lim.used.seconds
            else:
                start, opening = year0, 0
        else:
            assert isinstance(lim.window, RollingDays)
            reach = -lim.window.days * 1440
            if rel_at >= reach:
                start, opening = rel_at, lim.used.seconds
            else:
                start, opening = reach, 0
        limit_specs.append((lim, start, opening, rel_at))
    p0 = max([0, *(-s for _, s, _, _ in limit_specs)])
    length = p0 + m
    origin = ps - timedelta(minutes=p0)
    limits: list[_Limit] = []
    for lim, start, opening, rel_at in limit_specs:
        assert isinstance(lim.cap_s, int) and not isinstance(lim.counts, Unknown)
        limits.append(
            _Limit(
                limit_id=lim.limit_id,
                units=lim.unit_ids,
                cap_s=lim.cap_s,
                window_min=lim.window.days * 1440 if isinstance(lim.window, RollingDays) else None,
                counts_test=True,
                counts_cover=lim.counts != "test_only",
                start=start + p0,
                opening_s=opening,
                opening_at=rel_at + p0 if opening else start + p0,
            )
        )
    for lid, gap in sorted(stale_gap.items()):
        findings.append(
            GenerationFinding(
                code="OPENING_BALANCE_STALE",
                severity="advisory",
                scenario_id=sid,
                detail=f"limit {lid}: the opening balance is {gap} s before the period start; "
                "scheduled tests in the gap are pre-rolled",
            )
        )
        asks.append(
            ask(
                f"Refresh the run-hour log of limit {lid} to the period start "
                f"({ps.astimezone(_TZ).date().isoformat()})",
                site_id=site,
                holder=holder,
            )
        )
    years = range(
        origin.astimezone(_TZ).year + 1, (ps + timedelta(minutes=m)).astimezone(_TZ).year + 1
    )
    year_starts = tuple(_minutes(origin, _year_start(y)) for y in years)

    # ----- limit statements and tests per unit
    limited = {u for lim in model.run_limits for u in lim.unit_ids}
    nolimit = {u for nl in model.no_run_hour_limit for u in nl.unit_ids}
    tested = {u for t in model.test_runs for u in t.unit_ids}
    notest = {u for nt in model.no_test_runs for u in nt.unit_ids}
    for g in model.gensets:
        u = g.unit_id
        if u not in limited and u not in nolimit:
            reasons[u].append("no run-hour limit statement")
        if u not in tested and u not in notest:
            reasons[u].append("no test-run statement")
    for t in model.test_runs:
        if isinstance(t.recurrence, Unknown) or isinstance(t.duration_s, Unknown):
            for u in t.unit_ids:
                reasons[u].append(f"test schedule {t.schedule_id} is not fully stated")

    # ----- fuel
    store_of: dict[str, FuelStore] = {u: s for s in model.fuel_stores for u in s.unit_ids}
    stores: list[_Store] = []
    store_rows: list[StoreUse] = []
    curve_of: dict[str, BurnCurve] = {}
    for s in model.fuel_stores:
        e = s.endurance
        if isinstance(e, ContinuousSupply):
            store_rows.append(
                StoreUse(
                    store_id=s.store_id,
                    status="continuous",
                    unit=not_applicable("continuous supply"),
                    opening=not_applicable("continuous supply"),
                    capacity=not_applicable("continuous supply"),
                    peak_segment_use=not_applicable("continuous supply"),
                    refills=0,
                )
            )
            continue
        why: str | None = None
        if isinstance(e, Unknown):
            why = f"the endurance of fuel store {s.store_id} is unknown"
        elif isinstance(s.opening, Unknown):
            why = f"the opening level of fuel store {s.store_id} is unknown"
        elif isinstance(e, TankAndBurn):
            curved = {c.unit_id: c for c in e.curves}
            missing = [u for u in s.unit_ids if u not in curved]
            if missing:
                why = f"fuel store {s.store_id} has no burn curve for {', '.join(missing)}"
            curve_of.update(curved)
        if why is not None:
            for u in s.unit_ids:
                reasons[u].append(why)
            store_rows.append(
                StoreUse(
                    store_id=s.store_id,
                    status="not_enforced",
                    unit=not_applicable(why),
                    opening=not_applicable(why),
                    capacity=not_applicable(why),
                    peak_segment_use=not_applicable(why),
                    refills=0,
                )
            )
            continue
        assert isinstance(e, StatedEndurance | TankAndBurn)
        cap = e.seconds_at_rated if isinstance(e, StatedEndurance) else e.usable_ml
        assert isinstance(s.opening, StatedFull | StatedLevel)
        opening = cap if isinstance(s.opening, StatedFull) else min(cap, s.opening.amount)
        lead: int | None = None
        if isinstance(s.resupply, ResupplyLead):
            lead = -(-s.resupply.lead_s // 60)
        else:
            assert isinstance(s.resupply, NoResupply | Unknown)
        stores.append(_Store(s.store_id, s.unit_ids, cap, opening, lead))
    for g in model.gensets:
        if g.unit_id not in store_of:
            reasons[g.unit_id].append("no fuel store")
    enforced_store = {st.store_id for st in stores}

    def burn_rate(u: str, kw: np.ndarray) -> np.ndarray:
        """Fuel per running minute at output ``kw`` (unit-seconds or ml, rounded up)."""
        s = store_of.get(u)
        if s is None or s.store_id not in enforced_store:
            return np.zeros(kw.shape[0], dtype=np.int64)
        if isinstance(s.endurance, StatedEndurance):
            return np.full(kw.shape[0], 60, dtype=np.int64)
        curve = curve_of[u]
        cache: dict[int, int] = {}
        out = np.empty(kw.shape[0], dtype=np.int64)
        for i, x in enumerate(kw.tolist()):
            if x not in cache:
                cache[x] = _ceil(_ml_per_hour(curve, int(x)) / 60)
            out[i] = cache[x]
        return out

    ledger = RunLedger(length=length, p0=p0, limits=limits, stores=stores, year_starts=year_starts)

    # ----- pass 1: tests
    occurrences: list[tuple[int, str, TestRunSchedule]] = []
    end = origin + timedelta(minutes=length)
    for t in model.test_runs:
        if isinstance(t.recurrence, Unknown) or isinstance(t.duration_s, Unknown):
            continue
        day = origin.astimezone(_TZ).date() - timedelta(days=1)
        last_day = end.astimezone(_TZ).date()
        while day <= last_day:
            if _occurs(t.recurrence, day):
                a = _minutes(origin, _utc_of_local(day, t.local_start_minute))
                if 0 <= a < length:
                    occurrences.append((a, t.schedule_id, t))
            day += timedelta(days=1)
    occurrences.sort(key=lambda o: (o[0], o[1]))
    cut_tests: dict[str, int] = {}
    for a, schedule_id, t in occurrences:
        assert isinstance(t.duration_s, int)
        b = min(length, a + -(-t.duration_s // 60))
        for u in t.unit_ids:
            kw = 0 if t.mode == "off_load" else rated.get(u, 0)
            rate = burn_rate(u, np.full(b - a, kw, dtype=np.int64))
            run = ledger._grant(u, a, b, "test", schedule_id, rate, None)
            if run.cut_by:
                cut_tests[schedule_id] = cut_tests.get(schedule_id, 0) + 1
    for schedule_id, count in sorted(cut_tests.items()):
        findings.append(
            GenerationFinding(
                code="TEST_SCHEDULE_EXCEEDS_LIMIT",
                severity="nonconforming",
                scenario_id=sid,
                detail=f"test schedule {schedule_id}: {count} unit test run(s) cut short or "
                "refused to keep within a run-hour limit or fuel store",
            )
        )

    # ----- pass 2: cover
    credited = [g.unit_id for g in model.gensets if not reasons[g.unit_id]]
    online_w = np.zeros((len(credited), m), dtype=np.int64)
    if credited and k is not None:
        last_end: dict[str, int] = {}
        for i, ev in enumerate(sc.events):
            a_s = (ev.start - sc.period_start).total_seconds()
            b_s = (ev.end - sc.period_start).total_seconds()
            lo, hi = max(0, int(a_s // 60)), min(m, -int(-b_s // 60))
            pos = np.flatnonzero(need_env[lo:hi] > 0)
            if pos.size == 0:
                continue
            f, last = lo + int(pos[0]), lo + int(pos[-1])
            peak = int(need_env[f : last + 1].max())
            committed: list[str] = []
            for u in credited:
                committed.append(u)
                rs = sorted((rated[x] for x in committed), reverse=True)
                if (sum(rs) - sum(rs[:k])) * 1000 >= peak:
                    break
            notice = ev.notice_s if isinstance(ev.notice_s, float) else 0.0
            notice_min = int(notice // 60)
            total_kw = sorted((rated[x] for x in committed), reverse=True)
            carry_kw = max(1, sum(total_kw) - sum(total_kw[:k]))
            for u in committed:
                call = max(f - start_min[u], lo - notice_min, 0)
                online = call + start_min[u]
                if last_end.get(u, -1) >= call:
                    call = online = last_end[u]
                if call > last:
                    continue
                share = np.zeros(last + 1 - call, dtype=np.int64)
                seg = need_env[call : last + 1]
                kwv = -(-(seg * rated[u]) // (carry_kw * 1000))
                share[:] = np.minimum(kwv, rated[u])
                share[: max(0, online - call)] = 0
                rate = burn_rate(u, share)
                run = ledger._grant(
                    u, call + p0, last + 1 + p0, "cover", f"event:{i}", rate, online + p0
                )
                granted_end = run.end_min
                last_end[u] = granted_end
                j = credited.index(u)
                if granted_end > online:
                    online_w[j, online:granted_end] = rated[u] * 1000
    if credited and k is not None:
        on = np.sort(online_w, axis=0)  # ascending per minute
        top = on[max(0, len(credited) - k) :].sum(axis=0) if k else 0
        genset_w = on.sum(axis=0) - top
        genset_w = np.maximum(genset_w, 0).astype(np.int64)
    else:
        genset_w = np.zeros(m, dtype=np.int64)
    arrays = GenerationArrays(colocated_w=col.astype(np.int64), genset_w=genset_w)

    # ----- record
    delivered = (np.minimum(genset_w[None, :], need) * 60).sum(axis=1)
    taken = ((base_net - np.maximum(base_net - col, 0)) * 60).sum(axis=1)
    limit_rows: list[LimitUse] = []
    by_id = {lim.limit_id: lim for lim in limits}
    for lim_m in model.run_limits:
        lim_x = by_id.get(lim_m.limit_id)
        if lim_x is None:
            why = "an element is unknown, or the opening balance postdates the period start"
            limit_rows.append(
                LimitUse(
                    limit_id=lim_m.limit_id,
                    status="not_enforced",
                    cap_s=not_applicable(why),
                    opening_s=not_applicable(why),
                    counted_from_min=not_applicable(why),
                    peak_s=not_applicable(why),
                )
            )
            continue
        limit_rows.append(
            LimitUse(
                limit_id=lim_x.limit_id,
                status="enforced",
                cap_s=lim_x.cap_s,
                opening_s=lim_x.opening_s,
                counted_from_min=lim_x.start - p0,
                peak_s=ledger.peak_s(lim_x),
            )
        )
    for st in stores:
        peak, refills, _ok = ledger.store_use(st)
        s_m = next(s for s in model.fuel_stores if s.store_id == st.store_id)
        store_rows.append(
            StoreUse(
                store_id=st.store_id,
                status="enforced",
                unit="unit_seconds" if isinstance(s_m.endurance, StatedEndurance) else "ml",
                opening=st.opening,
                capacity=st.capacity,
                peak_segment_use=peak,
                refills=refills,
            )
        )
    store_rows.sort(key=lambda r: r.store_id)
    record = ScenarioGeneration(
        scenario_id=sid,
        thermal=thermal,
        preroll_min=p0,
        contingency_k=k if k is not None else not_applicable("the start contingency is unknown"),
        units=tuple(
            UnitCredit(
                unit_id=g.unit_id,
                credited=not reasons[g.unit_id],
                reasons=tuple(dict.fromkeys(reasons[g.unit_id])),
            )
            for g in model.gensets
        ),
        sources=tuple(sources),
        runs=tuple(ledger.runs),
        limits=tuple(limit_rows),
        stores=tuple(store_rows),
        findings=tuple(findings),
        delivered_ws_max=int(delivered.max()) if n else 0,
        delivered_ws_mean=ExactRational.of(Fraction(int(delivered.sum()), max(n, 1))),
        colocated_ws_mean=ExactRational.of(Fraction(int(taken.sum()), max(n, 1))),
    )
    return arrays, record, tuple(asks)


def generation_credit(
    plans: tuple[ScenarioGeneration, ...], gen: OnsiteGeneration
) -> Literal["full", "partial", "none"]:
    """``full``: every unit credited and every source read in every plan; ``none``: nothing
    credited anywhere (and something was declared); otherwise ``partial``."""
    units = [u for p in plans for u in p.units]
    srcs = [s for p in plans for s in p.sources]
    if not gen.model.gensets and not gen.model.colocated:
        return "full"
    got = [u.credited for u in units] + [s.basis != "none" for s in srcs]
    if got and all(got):
        return "full"
    if not any(got):
        return "none"
    return "partial"
