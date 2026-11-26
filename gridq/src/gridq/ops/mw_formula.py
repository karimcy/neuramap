"""A machine-checkable formula for a demonstrator-MW definition (WP-OPS-11, D143).

A candidate definition is a frozen, unit-checked expression tree. Leaves are typed `Term`s (unit,
grain, time base, measurement point) and exact rational `Const`s. Operators add, subtract, scale,
clip, sum over sites, assets or services, aggregate over a time window, and restrict to the
intervals or sites that meet named qualifying conditions. Nothing here binds a term to a data
source: the bindings live in a separate table (`gridq.ops.mw_bindings`), so re-binding a term when
the telemetry contract (NEW-IC-14) or the verifier interface (IC-8) lands does not change the hash a
signed agreement cites.

**Slots, never defaults (D147).** What the spec leaves open (the statistic, the window,
the resolution, coincidence across sites, the gap policy, the basis of a definition) is an explicit
`ChoiceSlot` with every admissible option listed, or a `NumberSlot` with no value. A definition
carries no default filling. `resolve` turns a template plus a complete `Filling` into a concrete
tree whose content hash is the variant id; only WP-OPS-02 agrees a filling.

**Exact evaluation.** `evaluate` uses `fractions.Fraction` throughout. Unknown is strict: an
unknown term makes the result unknown, with every missing term listed, and is never bounded away.
A missing sample is a gap, never zero. Conditions use three-valued logic: an unknown predicate at
an interval is a gap at that interval.

**No preference.** No model here has a score, rank, status, preferred or default field.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Any, Final, Literal, Union
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Unknown,
    UtcInstant,
    content_hash,
)
from gridq.contracts.evidence import EvidenceRef

__all__ = [
    "CLOCK",
    "Add",
    "AllOf",
    "AnyOf",
    "Branch",
    "ChoiceSlot",
    "ClipLow",
    "Compare",
    "Condition",
    "Const",
    "DayTypes",
    "Env",
    "Evaluation",
    "Filling",
    "FlagIs",
    "FormulaError",
    "GapUnknownIfAny",
    "Grain",
    "MinCoverage",
    "MissingTerm",
    "MwDefinition",
    "Not",
    "NumberFill",
    "NumberSlot",
    "OnFail",
    "Option",
    "PercentileStat",
    "Q",
    "QualityGood",
    "Reading",
    "Resolution",
    "Scale",
    "Choose",
    "SiteEnv",
    "SlotRef",
    "Stat",
    "Sub",
    "Sum",
    "Term",
    "TermRef",
    "TimeAgg",
    "TimeBase",
    "Unit",
    "Where",
    "Window",
    "evaluate",
    "render_kpi_formula",
    "render_text",
    "resolve",
    "variant_hash",
]

CLOCK: Final = ZoneInfo("Europe/London")
_SLOT_ID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]*$")]
_ID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]*$")]


class FormulaError(ValueError):
    """A formula, a filling or an environment is malformed."""


class Unit(StrEnum):
    KW = "kw"
    MW = "mw"
    KWH = "kwh"
    MIN = "min"
    RATIO = "ratio"
    FLAG = "flag"
    KW_PER_MW = "kw_per_mw"


class Grain(StrEnum):
    """What one value of a term describes. Order: asset and service are finer than site."""

    ASSET = "asset"
    SERVICE = "service"
    SITE = "site"
    PORTFOLIO = "portfolio"


_GRAIN_RANK: Final = {Grain.ASSET: 0, Grain.SERVICE: 0, Grain.SITE: 1, Grain.PORTFOLIO: 2}


class TimeBase(StrEnum):
    STATIC = "static"
    SERIES = "series"


# --------------------------------------------------------------------------------------------
# Numbers, slots and slot payloads
# --------------------------------------------------------------------------------------------


class Q(ContractModel):
    """An exact rational. No float ever enters a formula or its hash."""

    kind: Literal["q"]
    num: int
    den: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _reduced(self) -> Q:
        if math.gcd(self.num, self.den) != 1:
            raise ValueError("a rational is stored in lowest terms")
        return self

    @property
    def value(self) -> Fraction:
        return Fraction(self.num, self.den)

    @classmethod
    def of(cls, x: Fraction | int) -> Q:
        f = Fraction(x)
        return cls(kind="q", num=f.numerator, den=f.denominator)


class SlotRef(ContractModel):
    """A place in the tree that a filling must supply."""

    kind: Literal["slot"]
    slot_id: _SLOT_ID


class Stat(ContractModel):
    kind: Literal["stat"]
    name: Literal["mean", "max", "min"]


class PercentileStat(ContractModel):
    """Nearest-rank percentile: the value at rank ceil(p x n) of the n values sorted ascending."""

    kind: Literal["percentile"]
    p: Q | SlotRef = Field(discriminator="kind")


class Resolution(ContractModel):
    kind: Literal["resolution"]
    seconds: Literal[60, 1800]


class GapUnknownIfAny(ContractModel):
    kind: Literal["gap_unknown_if_any"]


class MinCoverage(ContractModel):
    """The result exists only when this share of qualifying intervals has a reading."""

    kind: Literal["min_coverage"]
    ratio: Q | SlotRef = Field(discriminator="kind")


class OnFail(ContractModel):
    kind: Literal["on_fail"]
    value: Literal["exclude_interval", "contributes_zero", "site_not_counted"]


class DayTypes(ContractModel):
    kind: Literal["day_types"]
    value: Literal["all", "weekdays", "weekends"]


class Window(ContractModel):
    """Local GB days ``first_day`` to ``last_day`` inclusive, the day types, and the local
    minute span [start_minute, end_minute) of each day. An interval is in the window when its
    start is."""

    kind: Literal["window"]
    first_day: IsoDate
    last_day: IsoDate
    period_citation: NonEmptyStr
    day_types: DayTypes | SlotRef = Field(discriminator="kind")
    start_minute: Q | SlotRef = Field(discriminator="kind")
    end_minute: Q | SlotRef = Field(discriminator="kind")

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if self.last_day < self.first_day:
            raise ValueError("a window ends on or after it starts")
        return self


Payload = Annotated[
    Stat | PercentileStat | Resolution | GapUnknownIfAny | MinCoverage | OnFail | DayTypes | Window,
    Field(discriminator="kind"),
]


class Option(ContractModel):
    """One admissible option of a choice slot. A structural option (a `Choose` branch) has no
    payload: the branch is in the tree."""

    option_id: _ID
    label: NonEmptyStr
    payload: Payload | NotApplicable


class ChoiceSlot(ContractModel):
    """A closed choice. Options are in canonical (lexicographic) order; there is no default."""

    kind: Literal["choice_slot"]
    slot_id: _SLOT_ID
    label: NonEmptyStr
    options: tuple[Option, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _canonical(self) -> ChoiceSlot:
        ids = [o.option_id for o in self.options]
        if ids != sorted(set(ids)):
            raise ValueError(f"slot {self.slot_id}: options are in canonical order, unique")
        return self


class NumberSlot(ContractModel):
    """A number the agreement must state. It has no value and no bounds here."""

    kind: Literal["number_slot"]
    slot_id: _SLOT_ID
    label: NonEmptyStr
    unit: Unit


Slot = Annotated[ChoiceSlot | NumberSlot, Field(discriminator="kind")]


# --------------------------------------------------------------------------------------------
# Terms, nodes and conditions
# --------------------------------------------------------------------------------------------


class Term(ContractModel):
    """A leaf quantity. Its source is not here: see the definition's binding table."""

    term_id: _ID
    label: NonEmptyStr
    unit: Unit
    grain: Grain
    time_base: TimeBase
    measurement_point: NonEmptyStr
    #: What kind of record a value of this term is: a meter reading, a contracted or registered
    #: document term, a modelled figure, or a figure an independent verifier recomputes.
    evidence_class: Literal[
        "contracted_document",
        "metered",
        "modelled",
        "operational_telemetry",
        "registered_document",
        "verifier_recompute",
    ]


class TermRef(ContractModel):
    node: Literal["term"]
    term_id: _ID


class Const(ContractModel):
    """An exact constant, or a number the agreement must state (a `NumberSlot`)."""

    node: Literal["const"]
    value: Q | SlotRef = Field(discriminator="kind")
    unit: Unit


class Add(ContractModel):
    node: Literal["add"]
    args: tuple[Node, ...] = Field(min_length=2)


class Sub(ContractModel):
    node: Literal["sub"]
    lhs: Node
    rhs: Node


class Scale(ContractModel):
    """``body`` times ``factor``. Units: x * ratio -> x; mw * kw_per_mw -> kw."""

    node: Literal["scale"]
    factor: Const
    body: Node


class ClipLow(ContractModel):
    """max(body, floor), pointwise."""

    node: Literal["clip_low"]
    floor: Const
    body: Node


class Sum(ContractModel):
    """Sum of ``body`` over every member of the enclosing scope (sites of the portfolio, assets
    or services of a site). A member that is not counted is left out; an unknown member makes the
    sum unknown. No partial total is produced."""

    node: Literal["sum"]
    over: Literal["sites", "assets", "services"]
    body: Node


class TimeAgg(ContractModel):
    """The statistic of a series over the window, at the resolution, under the gap policy."""

    node: Literal["time_agg"]
    window: Window | SlotRef = Field(discriminator="kind")
    resolution: Resolution | SlotRef = Field(discriminator="kind")
    statistic: Stat | PercentileStat | SlotRef = Field(discriminator="kind")
    gap_policy: GapUnknownIfAny | MinCoverage | SlotRef = Field(discriminator="kind")
    body: Node


class Where(ContractModel):
    """``body`` where every named condition holds; otherwise ``on_fail``."""

    node: Literal["where"]
    conditions: tuple[_ID, ...] = Field(min_length=1)
    on_fail: OnFail | SlotRef = Field(discriminator="kind")
    body: Node


class Branch(ContractModel):
    option_id: _ID
    body: Node


class Choose(ContractModel):
    """A structural choice: the branch the filling picks for ``slot_id``."""

    node: Literal["choose"]
    slot_id: _SLOT_ID
    branches: tuple[Branch, ...] = Field(min_length=2)


Node = Annotated[
    Union[TermRef, Const, Add, Sub, Scale, ClipLow, Sum, TimeAgg, Where, Choose],  # noqa: UP007
    Field(discriminator="node"),
]


class Compare(ContractModel):
    kind: Literal["compare"]
    lhs: Node
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    rhs: Node


class FlagIs(ContractModel):
    """A flag term is true."""

    kind: Literal["flag"]
    term_id: _ID


class QualityGood(ContractModel):
    """Every reading of these series terms at the interval carries the quality flag ``good``
    (for a term finer than the context, every member's reading)."""

    kind: Literal["quality_good"]
    term_ids: tuple[_ID, ...] = Field(min_length=1)


class AllOf(ContractModel):
    kind: Literal["all"]
    preds: tuple[Predicate, ...] = Field(min_length=2)


class AnyOf(ContractModel):
    kind: Literal["any"]
    preds: tuple[Predicate, ...] = Field(min_length=2)


class Not(ContractModel):
    kind: Literal["not"]
    pred: Predicate


Predicate = Annotated[
    Union[Compare, FlagIs, QualityGood, AllOf, AnyOf, Not],  # noqa: UP007
    Field(discriminator="kind"),
]


class Condition(ContractModel):
    """A qualifying condition, stated once and referenced by id from `Where` nodes."""

    condition_id: _ID
    label: NonEmptyStr
    scope: Literal["per_asset", "per_interval", "per_site", "per_window"]
    predicate: Predicate


for _m in (
    Add,
    Sub,
    Scale,
    ClipLow,
    Sum,
    TimeAgg,
    Where,
    Branch,
    Choose,
    Compare,
    AllOf,
    AnyOf,
    Not,
):
    _m.model_rebuild()


class MwDefinition(ContractModel):
    """One candidate definition as a template: terms, conditions, slots and the tree.

    No score, rank, preference, status or default field. ``content_hash`` is the template id;
    `variant_hash` names one filling of it.
    """

    definition_id: Literal[
        "site_load_under_management", "bess_mw_actively_dispatched", "verified_flexible_mw"
    ]
    letter: Literal["A", "B", "C"]
    title: NonEmptyStr
    quantity: NonEmptyStr
    terms: tuple[Term, ...] = Field(min_length=1)
    conditions: tuple[Condition, ...]
    slots: tuple[Slot, ...]
    #: Slot combinations that cannot be filled, each with its reason (listed, never dropped).
    inadmissible: tuple[Inadmissible, ...]
    root: Node

    @model_validator(mode="after")
    def _well_formed(self) -> MwDefinition:
        ids = [t.term_id for t in self.terms]
        if ids != sorted(set(ids)):
            raise ValueError("terms are ordered by id, without repeats")
        cids = [c.condition_id for c in self.conditions]
        if cids != sorted(set(cids)):
            raise ValueError("conditions are ordered by id, without repeats")
        sids = [s.slot_id for s in self.slots]
        if sids != sorted(set(sids)):
            raise ValueError("slots are ordered by id, without repeats")
        _check_template(self)
        return self

    def term(self, term_id: str) -> Term:
        for t in self.terms:
            if t.term_id == term_id:
                return t
        raise FormulaError(f"{self.definition_id}: no term {term_id}")

    def condition(self, condition_id: str) -> Condition:
        for c in self.conditions:
            if c.condition_id == condition_id:
                return c
        raise FormulaError(f"{self.definition_id}: no condition {condition_id}")

    def slot(self, slot_id: str) -> ChoiceSlot | NumberSlot:
        for s in self.slots:
            if s.slot_id == slot_id:
                return s
        raise FormulaError(f"{self.definition_id}: no slot {slot_id}")

    def content_hash(self) -> str:
        return content_hash(self)


class Inadmissible(ContractModel):
    when: tuple[tuple[_SLOT_ID, _ID], ...] = Field(min_length=2)
    reason: NonEmptyStr


MwDefinition.model_rebuild()


# --------------------------------------------------------------------------------------------
# Static checking: units, grains, time bases, slot references
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sig:
    unit: Unit
    grain: Grain | None  # None: a constant, any grain
    time: TimeBase


def _finer(a: Grain | None, b: Grain | None) -> Grain | None:
    if a is None:
        return b
    if b is None:
        return a
    if _GRAIN_RANK[a] == _GRAIN_RANK[b] and a != b:
        raise FormulaError(f"cannot combine {a} and {b} values without a sum")
    return a if _GRAIN_RANK[a] <= _GRAIN_RANK[b] else b


def _either(a: TimeBase, b: TimeBase) -> TimeBase:
    return TimeBase.SERIES if TimeBase.SERIES in (a, b) else TimeBase.STATIC


_SCALE: Final = {
    (Unit.MW, Unit.KW_PER_MW): Unit.KW,
    (Unit.KW, Unit.RATIO): Unit.KW,
    (Unit.KWH, Unit.RATIO): Unit.KWH,
    (Unit.MIN, Unit.RATIO): Unit.MIN,
    (Unit.RATIO, Unit.RATIO): Unit.RATIO,
}
_SUM_BODY: Final = {"sites": Grain.SITE, "assets": Grain.ASSET, "services": Grain.SERVICE}
_SUM_RESULT: Final = {"sites": Grain.PORTFOLIO, "assets": Grain.SITE, "services": Grain.SITE}


def _sig(d: MwDefinition, node: Node, slots: Mapping[str, Any]) -> _Sig:
    if isinstance(node, TermRef):
        t = d.term(node.term_id)
        return _Sig(t.unit, t.grain, t.time_base)
    if isinstance(node, Const):
        return _Sig(node.unit, None, TimeBase.STATIC)
    if isinstance(node, Add | Sub):
        parts = node.args if isinstance(node, Add) else (node.lhs, node.rhs)
        sigs = [_sig(d, p, slots) for p in parts]
        units = {s.unit for s in sigs}
        if len(units) != 1 or Unit.FLAG in units:
            raise FormulaError(f"{d.definition_id}: {node.node} of unlike units {sorted(units)}")
        grain: Grain | None = None
        time = TimeBase.STATIC
        for s in sigs:
            grain = _finer(grain, s.grain)
            time = _either(time, s.time)
        return _Sig(sigs[0].unit, grain, time)
    if isinstance(node, Scale):
        s = _sig(d, node.body, slots)
        unit = _SCALE.get((s.unit, node.factor.unit))
        if unit is None:
            raise FormulaError(f"{d.definition_id}: cannot scale {s.unit} by {node.factor.unit}")
        return _Sig(unit, s.grain, s.time)
    if isinstance(node, ClipLow):
        s = _sig(d, node.body, slots)
        if node.floor.unit != s.unit:
            raise FormulaError(f"{d.definition_id}: clip floor in {node.floor.unit}, body {s.unit}")
        return s
    if isinstance(node, Sum):
        s = _sig(d, node.body, slots)
        if s.grain != _SUM_BODY[node.over]:
            raise FormulaError(
                f"{d.definition_id}: sum over {node.over} needs a {_SUM_BODY[node.over]} body, "
                f"not {s.grain}"
            )
        return _Sig(s.unit, _SUM_RESULT[node.over], s.time)
    if isinstance(node, TimeAgg):
        s = _sig(d, node.body, slots)
        if s.time is not TimeBase.SERIES:
            raise FormulaError(f"{d.definition_id}: a time aggregate needs a series body")
        for ref in (node.window, node.resolution, node.statistic, node.gap_policy):
            _check_ref(d, ref)
        return _Sig(s.unit, s.grain, TimeBase.STATIC)
    if isinstance(node, Where):
        s = _sig(d, node.body, slots)
        time = s.time
        for cid in node.conditions:
            c = d.condition(cid)
            for tid in _pred_terms(c.predicate):
                t = d.term(tid)
                if (
                    s.grain is not None
                    and _GRAIN_RANK[t.grain] < _GRAIN_RANK[s.grain]
                    and not isinstance(c.predicate, QualityGood)
                ):
                    raise FormulaError(
                        f"{d.definition_id}: condition {cid} reads {tid} ({t.grain}) inside a "
                        f"{s.grain} context"
                    )
            if c.scope == "per_interval":
                time = TimeBase.SERIES
        _check_ref(d, node.on_fail)
        return _Sig(s.unit, s.grain, time)
    if isinstance(node, Choose):
        slot = d.slot(node.slot_id)
        if not isinstance(slot, ChoiceSlot):
            raise FormulaError(f"{d.definition_id}: choice on number slot {node.slot_id}")
        if [b.option_id for b in node.branches] != [o.option_id for o in slot.options]:
            raise FormulaError(
                f"{d.definition_id}: choice {node.slot_id} has one branch per option, in order"
            )
        sigs = [_sig(d, b.body, slots) for b in node.branches]
        if len({(s.unit, s.grain) for s in sigs}) != 1:
            raise FormulaError(f"{d.definition_id}: choice {node.slot_id} branches differ")
        time = TimeBase.STATIC
        for s in sigs:
            time = _either(time, s.time)
        return _Sig(sigs[0].unit, sigs[0].grain, time)
    raise FormulaError(f"unknown node {node!r}")


def _check_ref(d: MwDefinition, ref: Any) -> None:
    if isinstance(ref, SlotRef):
        d.slot(ref.slot_id)
    elif isinstance(ref, Window):
        for part in (ref.day_types, ref.start_minute, ref.end_minute):
            _check_ref(d, part)
    elif isinstance(ref, PercentileStat | MinCoverage):
        _check_ref(d, ref.p if isinstance(ref, PercentileStat) else ref.ratio)


def _pred_terms(p: Predicate) -> list[str]:
    if isinstance(p, FlagIs):
        return [p.term_id]
    if isinstance(p, QualityGood):
        return list(p.term_ids)
    if isinstance(p, Compare):
        return _node_terms(p.lhs) + _node_terms(p.rhs)
    if isinstance(p, AllOf | AnyOf):
        return [t for q in p.preds for t in _pred_terms(q)]
    return _pred_terms(p.pred)


def _node_terms(node: Node) -> list[str]:
    if isinstance(node, TermRef):
        return [node.term_id]
    if isinstance(node, Const):
        return []
    if isinstance(node, Add):
        return [t for a in node.args for t in _node_terms(a)]
    if isinstance(node, Sub):
        return _node_terms(node.lhs) + _node_terms(node.rhs)
    if isinstance(node, Choose):
        return [t for b in node.branches for t in _node_terms(b.body)]
    return _node_terms(node.body)


def _check_template(d: MwDefinition) -> None:
    sig = _sig(d, d.root, {})
    if (sig.unit, sig.grain, sig.time) != (Unit.KW, Grain.PORTFOLIO, TimeBase.STATIC):
        raise FormulaError(
            f"{d.definition_id}: the root is a static portfolio kW value, not "
            f"{sig.unit}/{sig.grain}/{sig.time}"
        )
    used = set(_node_terms(d.root)) | {t for c in d.conditions for t in _pred_terms(c.predicate)}
    declared = {t.term_id for t in d.terms}
    if used != declared:
        raise FormulaError(
            f"{d.definition_id}: declared terms {sorted(declared - used)} unused, "
            f"{sorted(used - declared)} undeclared"
        )
    referenced = set(_slot_refs(d))
    if referenced != {s.slot_id for s in d.slots}:
        raise FormulaError(
            f"{d.definition_id}: slots declared and slots referenced differ: "
            f"{sorted(referenced ^ {s.slot_id for s in d.slots})}"
        )
    for c in d.conditions:
        for p in _walk_preds(c.predicate):
            if isinstance(p, Compare):
                lu, ru = _sig(d, p.lhs, {}).unit, _sig(d, p.rhs, {}).unit
                if lu != ru:
                    raise FormulaError(f"{d.definition_id}: {c.condition_id} compares {lu}, {ru}")
            if isinstance(p, FlagIs) and d.term(p.term_id).unit is not Unit.FLAG:
                raise FormulaError(f"{d.definition_id}: {p.term_id} is not a flag")
            if isinstance(p, QualityGood):
                for tid in p.term_ids:
                    if d.term(tid).time_base is not TimeBase.SERIES:
                        raise FormulaError(f"{d.definition_id}: quality of static {tid}")
    for bad in d.inadmissible:
        for sid, oid in bad.when:
            slot = d.slot(sid)
            if not isinstance(slot, ChoiceSlot) or oid not in {o.option_id for o in slot.options}:
                raise FormulaError(f"{d.definition_id}: inadmissible names {sid}={oid}")


def _walk_preds(p: Predicate) -> Iterable[Predicate]:
    yield p
    if isinstance(p, AllOf | AnyOf):
        for q in p.preds:
            yield from _walk_preds(q)
    elif isinstance(p, Not):
        yield from _walk_preds(p.pred)


def _slot_refs(d: MwDefinition) -> list[str]:
    out: list[str] = []

    def ref(r: Any) -> None:
        if isinstance(r, SlotRef):
            out.append(r.slot_id)
        elif isinstance(r, Window):
            for part in (r.day_types, r.start_minute, r.end_minute):
                ref(part)
        elif isinstance(r, PercentileStat):
            ref(r.p)
        elif isinstance(r, MinCoverage):
            ref(r.ratio)

    def walk(n: Node) -> None:
        if isinstance(n, Const):
            ref(n.value)
        elif isinstance(n, Choose):
            out.append(n.slot_id)
            for b in n.branches:
                walk(b.body)
        elif isinstance(n, Add):
            for a in n.args:
                walk(a)
        elif isinstance(n, Sub):
            walk(n.lhs)
            walk(n.rhs)
        elif isinstance(n, TimeAgg):
            for r in (n.window, n.resolution, n.statistic, n.gap_policy):
                ref(r)
            walk(n.body)
        elif isinstance(n, Where):
            ref(n.on_fail)
            walk(n.body)
        elif isinstance(n, Scale | ClipLow):
            walk(n.factor if isinstance(n, Scale) else n.floor)
            walk(n.body)
        elif isinstance(n, Sum):
            walk(n.body)

    walk(d.root)
    for c in d.conditions:
        for p in _walk_preds(c.predicate):
            if isinstance(p, Compare):
                walk(p.lhs)
                walk(p.rhs)
    for s in d.slots:
        if isinstance(s, ChoiceSlot):
            for o in s.options:
                if not isinstance(o.payload, NotApplicable):
                    ref(o.payload)
    return out


# --------------------------------------------------------------------------------------------
# Filling and resolution
# --------------------------------------------------------------------------------------------


class NumberFill(ContractModel):
    slot_id: _SLOT_ID
    value: Q


class Filling(ContractModel):
    """A complete filling of a template's slots.

    ``agreed`` carries the IC-4 reference of the method agreement that states it (WP-OPS-02);
    ``synthetic_fixture`` is test data and says so. This package never creates an agreed filling.
    """

    record_class: Literal["agreed", "synthetic_fixture"]
    agreement: EvidenceRef | NotApplicable
    choices: tuple[tuple[_SLOT_ID, _ID], ...]
    numbers: tuple[NumberFill, ...]

    @model_validator(mode="after")
    def _shape(self) -> Filling:
        if (self.record_class == "agreed") != isinstance(self.agreement, EvidenceRef):
            raise ValueError("an agreed filling, and only one, cites its method agreement")
        keys = [k for k, _ in self.choices] + [n.slot_id for n in self.numbers]
        if len(keys) != len(set(keys)):
            raise ValueError("each slot is filled once")
        return self


def resolve(d: MwDefinition, filling: Filling) -> MwDefinition:
    """The concrete variant: every slot on the chosen path replaced by its option or number.

    Refuses an unfilled slot on the chosen path, an unknown option, a number for a slot that is
    not a number slot, and an inadmissible combination. Slots off the chosen path (the percentile
    of a ``mean`` statistic) are not needed. The result has no slots, so `evaluate` can run on it.
    """
    choices = dict(filling.choices)
    numbers = {n.slot_id: n.value for n in filling.numbers}
    for sid in list(choices) + list(numbers):
        slot = d.slot(sid)
        if isinstance(slot, ChoiceSlot) != (sid in choices):
            raise FormulaError(f"{d.definition_id}: slot {sid} filled with the wrong kind")
        if isinstance(slot, ChoiceSlot) and choices[sid] not in {o.option_id for o in slot.options}:
            raise FormulaError(f"{d.definition_id}: {choices[sid]} is not an option of {sid}")
    for bad in d.inadmissible:
        if all(choices.get(s) == o for s, o in bad.when):
            raise FormulaError(f"{d.definition_id}: inadmissible combination: {bad.reason}")

    def choice(sid: str) -> str:
        if sid not in choices:
            raise FormulaError(f"{d.definition_id}: slot {sid} is not filled")
        return choices[sid]

    def payload(sid: str) -> Any:
        slot = d.slot(sid)
        if isinstance(slot, NumberSlot):
            if sid not in numbers:
                raise FormulaError(f"{d.definition_id}: parameter not agreed: {sid}")
            return numbers[sid]
        opt = next(o for o in slot.options if o.option_id == choice(sid))
        if isinstance(opt.payload, NotApplicable):
            raise FormulaError(f"{d.definition_id}: slot {sid} is structural")
        return sub_ref(opt.payload)

    def sub_ref(r: Any) -> Any:
        if isinstance(r, SlotRef):
            return payload(r.slot_id)
        if isinstance(r, Window):
            return r.model_copy(
                update={
                    "day_types": sub_ref(r.day_types),
                    "start_minute": sub_ref(r.start_minute),
                    "end_minute": sub_ref(r.end_minute),
                }
            )
        if isinstance(r, PercentileStat):
            return r.model_copy(update={"p": sub_ref(r.p)})
        if isinstance(r, MinCoverage):
            return r.model_copy(update={"ratio": sub_ref(r.ratio)})
        return r

    def walk(n: Node) -> Node:
        if isinstance(n, Choose):
            picked = choice(n.slot_id)
            return walk(next(b for b in n.branches if b.option_id == picked).body)
        if isinstance(n, Const):
            return n.model_copy(update={"value": sub_ref(n.value)})
        if isinstance(n, Add):
            return n.model_copy(update={"args": tuple(walk(a) for a in n.args)})
        if isinstance(n, Sub):
            return n.model_copy(update={"lhs": walk(n.lhs), "rhs": walk(n.rhs)})
        if isinstance(n, TimeAgg):
            return n.model_copy(
                update={
                    "window": sub_ref(n.window),
                    "resolution": sub_ref(n.resolution),
                    "statistic": sub_ref(n.statistic),
                    "gap_policy": sub_ref(n.gap_policy),
                    "body": walk(n.body),
                }
            )
        if isinstance(n, Where):
            return n.model_copy(update={"on_fail": sub_ref(n.on_fail), "body": walk(n.body)})
        if isinstance(n, Scale):
            return n.model_copy(update={"factor": walk(n.factor), "body": walk(n.body)})
        if isinstance(n, ClipLow):
            return n.model_copy(update={"floor": walk(n.floor), "body": walk(n.body)})
        if isinstance(n, Sum):
            return n.model_copy(update={"body": walk(n.body)})
        return n

    def walk_pred(p: Predicate) -> Predicate:
        if isinstance(p, Compare):
            return p.model_copy(update={"lhs": walk(p.lhs), "rhs": walk(p.rhs)})
        if isinstance(p, AllOf | AnyOf):
            return p.model_copy(update={"preds": tuple(walk_pred(q) for q in p.preds)})
        if isinstance(p, Not):
            return p.model_copy(update={"pred": walk_pred(p.pred)})
        return p

    root = walk(d.root)
    used_conditions = set(_where_conditions(root))
    conditions = tuple(
        c.model_copy(update={"predicate": walk_pred(c.predicate)})
        for c in d.conditions
        if c.condition_id in used_conditions
    )
    used_terms = set(_node_terms(root)) | {t for c in conditions for t in _pred_terms(c.predicate)}
    # Round-trip through JSON so every validator runs on the concrete tree (a variant is itself
    # a definition: units, grains, time bases and the declared-equals-used rules all re-checked).
    variant = d.model_copy(
        update={
            "terms": tuple(t for t in d.terms if t.term_id in used_terms),
            "conditions": conditions,
            "slots": (),
            "inadmissible": (),
            "root": root,
        }
    )
    return MwDefinition.model_validate_json(variant.model_dump_json())


def _where_conditions(n: Node) -> list[str]:
    if isinstance(n, Where):
        return list(n.conditions) + _where_conditions(n.body)
    if isinstance(n, Add):
        return [c for a in n.args for c in _where_conditions(a)]
    if isinstance(n, Sub):
        return _where_conditions(n.lhs) + _where_conditions(n.rhs)
    if isinstance(n, Choose):
        return [c for b in n.branches for c in _where_conditions(b.body)]
    if isinstance(n, Scale | ClipLow | Sum | TimeAgg):
        return _where_conditions(n.body)
    return []


def variant_hash(d: MwDefinition, filling: Filling) -> str:
    """The id of one filled variant: the content hash of the resolved definition."""
    return resolve(d, filling).content_hash()


# --------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One interval's reading of a series term, with its quality flag (NEW-IC-14 to come)."""

    value: Fraction | bool
    quality: Literal["good", "suspect", "bad"] = "good"


#: A term's value in an environment: a static number or flag, a series aligned to the
#: environment's intervals (None at an interval is a gap), or Unknown / NotApplicable.
TermValue = Fraction | bool | tuple[Reading | None, ...] | Unknown | NotApplicable


@dataclass(frozen=True)
class MemberEnv:
    member_id: str
    terms: Mapping[str, TermValue]


@dataclass(frozen=True)
class SiteEnv:
    site_id: str
    terms: Mapping[str, TermValue]
    assets: tuple[MemberEnv, ...] = ()
    services: tuple[MemberEnv, ...] = ()


@dataclass(frozen=True)
class Env:
    """What a verifier recomputes from: the intervals (UTC starts, one resolution) and every
    term's value per site, asset and service."""

    resolution_s: int
    intervals: tuple[datetime, ...]
    sites: tuple[SiteEnv, ...]
    terms: Mapping[str, TermValue] = field(default_factory=dict)


class MissingTerm(ContractModel):
    term_id: _ID
    scope: NonEmptyStr
    reason: NonEmptyStr


class Evaluation(ContractModel):
    """The result of evaluating a resolved definition: exact kW, or why there is none."""

    variant_hash: NonEmptyStr
    result: Q | Unknown | NotApplicable = Field(discriminator="kind")
    missing: tuple[MissingTerm, ...]

    @model_validator(mode="after")
    def _missing_iff_unknown(self) -> Evaluation:
        if bool(self.missing) and not isinstance(self.result, Unknown):
            raise ValueError("missing terms are listed only for an unknown result")
        return self


class _Gap:
    """A series interval with no reading (or an unknown condition there). Never zero."""


class _Excluded:
    """A series interval a qualifying condition removed from the domain."""


GAP: Final = _Gap()
EXCLUDED: Final = _Excluded()
_Cell = Fraction | bool | _Gap | _Excluded


@dataclass
class _Unknown:
    missing: list[tuple[str, str, str]]


@dataclass
class _NA:
    reason: str


_Val = Fraction | bool | list[_Cell] | _Unknown | _NA


@dataclass
class _Ctx:
    d: MwDefinition
    env: Env
    site: SiteEnv | None = None
    member: MemberEnv | None = None
    member_kind: str = ""


def _lookup(ctx: _Ctx, term_id: str) -> tuple[TermValue, str]:
    t = ctx.d.term(term_id)
    if t.grain in (Grain.ASSET, Grain.SERVICE):
        if ctx.member is None or ctx.member_kind != t.grain.value:
            raise FormulaError(f"{term_id} ({t.grain}) read outside a sum over its members")
        scope = f"{ctx.site.site_id if ctx.site else '-'}/{ctx.member.member_id}"
        return ctx.member.terms.get(term_id, _absent(term_id)), scope
    if t.grain is Grain.SITE:
        if ctx.site is None:
            raise FormulaError(f"{term_id} (site) read outside a sum over sites")
        return ctx.site.terms.get(term_id, _absent(term_id)), ctx.site.site_id
    return ctx.env.terms.get(term_id, _absent(term_id)), "portfolio"


def _absent(term_id: str) -> Unknown:
    return Unknown(kind="unknown", reason=f"no value supplied for {term_id}")


def _term(ctx: _Ctx, term_id: str) -> _Val:
    value, scope = _lookup(ctx, term_id)
    if isinstance(value, Unknown):
        return _Unknown([(term_id, scope, value.reason)])
    if isinstance(value, NotApplicable):
        return _NA(value.reason)
    t = ctx.d.term(term_id)
    if isinstance(value, tuple):
        if t.time_base is not TimeBase.SERIES:
            raise FormulaError(f"{term_id} is static but a series was supplied")
        if len(value) != len(ctx.env.intervals):
            raise FormulaError(f"{term_id} at {scope}: series length is not the interval count")
        return [GAP if r is None else r.value for r in value]
    if t.time_base is TimeBase.SERIES:
        raise FormulaError(f"{term_id} is a series but a static value was supplied")
    return value


def _merge_unknown(vals: Sequence[_Val]) -> _Unknown | None:
    missing = [m for v in vals if isinstance(v, _Unknown) for m in v.missing]
    return _Unknown(missing) if missing else None


def _pointwise(vals: Sequence[_Val], n: int, fn: Any) -> _Val:
    """Apply ``fn`` across operands, broadcasting statics. Gap dominates, then exclusion."""
    if (u := _merge_unknown(vals)) is not None:
        return u
    for v in vals:
        if isinstance(v, _NA):
            return v
    if not any(isinstance(v, list) for v in vals):
        return fn([v for v in vals])  # type: ignore[misc]
    out: list[_Cell] = []
    for i in range(n):
        cells = [v[i] if isinstance(v, list) else v for v in vals]
        if any(isinstance(c, _Gap) for c in cells):
            out.append(GAP)
        elif any(isinstance(c, _Excluded) for c in cells):
            out.append(EXCLUDED)
        else:
            out.append(fn(cells))
    return out


def _const(c: Const) -> Fraction:
    if isinstance(c.value, SlotRef):
        raise FormulaError(f"parameter not agreed: {c.value.slot_id}: resolve the template first")
    return c.value.value


def _eval(ctx: _Ctx, node: Node) -> _Val:
    n = len(ctx.env.intervals)
    if isinstance(node, TermRef):
        return _term(ctx, node.term_id)
    if isinstance(node, Const):
        return _const(node)
    if isinstance(node, Add):
        return _pointwise([_eval(ctx, a) for a in node.args], n, lambda c: sum(c, Fraction(0)))
    if isinstance(node, Sub):
        return _pointwise([_eval(ctx, node.lhs), _eval(ctx, node.rhs)], n, lambda c: c[0] - c[1])
    if isinstance(node, Scale):
        k = _const(node.factor)
        return _pointwise([_eval(ctx, node.body)], n, lambda c: c[0] * k)
    if isinstance(node, ClipLow):
        lo = _const(node.floor)
        return _pointwise([_eval(ctx, node.body)], n, lambda c: max(c[0], lo))
    if isinstance(node, Sum):
        return _sum(ctx, node)
    if isinstance(node, TimeAgg):
        return _time_agg(ctx, node)
    if isinstance(node, Where):
        return _where(ctx, node)
    raise FormulaError(f"cannot evaluate {node.node}: resolve the template first")


def _members(ctx: _Ctx, over: str) -> list[_Ctx]:
    if over == "sites":
        return [_Ctx(ctx.d, ctx.env, site=s) for s in ctx.env.sites]
    if ctx.site is None:
        raise FormulaError(f"sum over {over} outside a site")
    members = ctx.site.assets if over == "assets" else ctx.site.services
    kind = "asset" if over == "assets" else "service"
    return [_Ctx(ctx.d, ctx.env, site=ctx.site, member=m, member_kind=kind) for m in members]


def _sum(ctx: _Ctx, node: Sum) -> _Val:
    members = _members(ctx, node.over)
    if not members and node.over != "sites":
        # A site with no assets (or services) has an empty sum: zero, which is a fact about the
        # site's recorded members, not a missing value. An empty portfolio has no figure.
        return Fraction(0)
    vals = [_eval(m, node.body) for m in members]
    if (u := _merge_unknown(vals)) is not None:
        return u
    counted = [v for v in vals if not isinstance(v, _NA)]
    if not counted:
        reasons = sorted({v.reason for v in vals if isinstance(v, _NA)})
        return _NA("no member counted" + (f" ({'; '.join(reasons)})" if reasons else ""))
    if not any(isinstance(v, list) for v in counted):
        return sum((v for v in counted if isinstance(v, Fraction)), Fraction(0))
    out: list[_Cell] = []
    for i in range(len(ctx.env.intervals)):
        cells = [v[i] if isinstance(v, list) else v for v in counted]
        if any(isinstance(c, _Gap) for c in cells):
            out.append(GAP)
            continue
        live = [c for c in cells if isinstance(c, Fraction)]
        out.append(sum(live, Fraction(0)) if live else EXCLUDED)
    return out


def _pred(ctx: _Ctx, p: Predicate) -> bool | None | list[bool | None]:
    """Three-valued: True, False, or None (unknown). Series predicates give a list."""
    n = len(ctx.env.intervals)
    if isinstance(p, FlagIs):
        v = _eval(ctx, TermRef(node="term", term_id=p.term_id))
        return _as_truth(v, n)
    if isinstance(p, QualityGood):
        parts: list[bool | None | list[bool | None]] = []
        for tid in p.term_ids:
            parts.append(_quality(ctx, tid))
        return _kleene_all(parts, n)
    if isinstance(p, Compare):
        lhs, rhs = _eval(ctx, p.lhs), _eval(ctx, p.rhs)
        ops = {
            "eq": lambda a, b: a == b,
            "ne": lambda a, b: a != b,
            "lt": lambda a, b: a < b,
            "le": lambda a, b: a <= b,
            "gt": lambda a, b: a > b,
            "ge": lambda a, b: a >= b,
        }
        res = _pointwise([lhs, rhs], n, lambda c: ops[p.op](c[0], c[1]))
        return _as_truth(res, n)
    if isinstance(p, AllOf):
        return _kleene_all([_pred(ctx, q) for q in p.preds], n)
    if isinstance(p, AnyOf):
        negs = [_negate(_pred(ctx, q)) for q in p.preds]
        return _negate(_kleene_all(negs, n))
    return _negate(_pred(ctx, p.pred))


def _quality(ctx: _Ctx, term_id: str) -> bool | None | list[bool | None]:
    t = ctx.d.term(term_id)
    if t.grain in (Grain.ASSET, Grain.SERVICE) and ctx.member is None:
        over = "assets" if t.grain is Grain.ASSET else "services"
        return _kleene_all([_quality(m, term_id) for m in _members(ctx, over)], 0, empty=True)
    value, _ = _lookup(ctx, term_id)
    if not isinstance(value, tuple):
        return None
    return [None if r is None else r.quality == "good" for r in value]


def _as_truth(v: _Val, n: int) -> bool | None | list[bool | None]:
    if isinstance(v, _Unknown | _NA):
        return None
    if isinstance(v, list):
        return [c if isinstance(c, bool) else None for c in v]
    return v if isinstance(v, bool) else None


def _negate(v: bool | None | list[bool | None]) -> bool | None | list[bool | None]:
    if isinstance(v, list):
        return [None if c is None else not c for c in v]
    return None if v is None else not v


def _kleene_all(
    parts: Sequence[bool | None | list[bool | None]], n: int, *, empty: bool = False
) -> bool | None | list[bool | None]:
    if not parts:
        return empty
    if not any(isinstance(p, list) for p in parts):
        if any(p is False for p in parts):
            return False
        return None if any(p is None for p in parts) else True
    length = next(len(p) for p in parts if isinstance(p, list))
    out: list[bool | None] = []
    for i in range(length):
        cells = [p[i] if isinstance(p, list) else p for p in parts]
        if any(c is False for c in cells):
            out.append(False)
        else:
            out.append(None if any(c is None for c in cells) else True)
    return out


def _where(ctx: _Ctx, node: Where) -> _Val:
    if not isinstance(node.on_fail, OnFail):
        raise FormulaError("resolve the template first")
    on_fail = node.on_fail.value
    body = _eval(ctx, node.body)
    if isinstance(body, _Unknown | _NA):
        return body
    n = len(ctx.env.intervals)
    truths: list[bool | None | list[bool | None]] = []
    for cid in node.conditions:
        cond = ctx.d.condition(cid)
        t = _pred(ctx, cond.predicate)
        if cond.scope != "per_interval" and isinstance(t, list):
            raise FormulaError(f"condition {cid} is {cond.scope} but reads a series")
        truths.append(t)
    truth = _kleene_all(truths, n)
    if not isinstance(truth, list):
        if truth is None:
            missing = _condition_missing(ctx, node)
            return _Unknown(missing or [("-", _scope(ctx), "a qualifying condition is unknown")])
        if truth:
            return body
        if on_fail == "site_not_counted":
            return _NA(f"qualifying condition not met at {_scope(ctx)}")
        if on_fail == "contributes_zero":
            return [Fraction(0)] * n if isinstance(body, list) else Fraction(0)
        return [EXCLUDED] * n
    if on_fail == "site_not_counted" and any(c is False for c in truth):
        return _NA(f"qualifying condition not met in an interval at {_scope(ctx)}")
    out: list[_Cell] = []
    for i, c in enumerate(truth):
        cell = body[i] if isinstance(body, list) else body
        if c is None:
            out.append(GAP)
        elif c:
            out.append(cell)
        elif on_fail == "contributes_zero":
            out.append(GAP if isinstance(cell, _Gap) else Fraction(0))
        else:
            out.append(EXCLUDED)
    return out


def _condition_missing(ctx: _Ctx, node: Where) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for cid in node.conditions:
        for tid in _pred_terms(ctx.d.condition(cid).predicate):
            try:
                value, scope = _lookup(ctx, tid)
            except FormulaError:
                continue
            if isinstance(value, Unknown):
                out.append((tid, scope, value.reason))
    return out


def _scope(ctx: _Ctx) -> str:
    if ctx.member is not None and ctx.site is not None:
        return f"{ctx.site.site_id}/{ctx.member.member_id}"
    return ctx.site.site_id if ctx.site is not None else "portfolio"


def _in_window(w: Window, start: datetime) -> bool:
    if (
        not isinstance(w.day_types, DayTypes)
        or isinstance(w.start_minute, SlotRef)
        or isinstance(w.end_minute, SlotRef)
    ):
        raise FormulaError("resolve the template first")
    local = start.astimezone(CLOCK)
    day: date = local.date()
    if not (w.first_day <= day <= w.last_day):
        return False
    weekday = local.isoweekday() <= 5
    if w.day_types.value == "weekdays" and not weekday:
        return False
    if w.day_types.value == "weekends" and weekday:
        return False
    minute = Fraction(local.hour * 60 + local.minute)
    return w.start_minute.value <= minute < w.end_minute.value


def _time_agg(ctx: _Ctx, node: TimeAgg) -> _Val:
    w, res, stat, gaps = node.window, node.resolution, node.statistic, node.gap_policy
    if (
        not isinstance(w, Window)
        or not isinstance(res, Resolution)
        or isinstance(stat, SlotRef)
        or isinstance(gaps, SlotRef)
    ):
        raise FormulaError("resolve the template first")
    if res.seconds != ctx.env.resolution_s:
        return _Unknown(
            [
                (
                    "-",
                    _scope(ctx),
                    f"series supplied at {ctx.env.resolution_s} s; the formula "
                    f"aggregates at {res.seconds} s and does not resample",
                )
            ]
        )
    body = _eval(ctx, node.body)
    if isinstance(body, _Unknown | _NA):
        return body
    if not isinstance(body, list):
        raise FormulaError("a time aggregate needs a series")
    domain = [
        c
        for start, c in zip(ctx.env.intervals, body, strict=True)
        if _in_window(w, start) and not isinstance(c, _Excluded)
    ]
    if not domain:
        return _NA(f"no qualifying interval in the window at {_scope(ctx)}")
    values = [c for c in domain if isinstance(c, Fraction)]
    n_gaps = len(domain) - len(values)
    if n_gaps:
        if isinstance(gaps, GapUnknownIfAny):
            return _Unknown([("-", _scope(ctx), f"{n_gaps} of {len(domain)} intervals are gaps")])
        if isinstance(gaps.ratio, SlotRef):
            raise FormulaError("resolve the template first")
        if Fraction(len(values), len(domain)) < gaps.ratio.value:
            return _Unknown(
                [
                    (
                        "-",
                        _scope(ctx),
                        f"coverage {len(values)}/{len(domain)} is below the agreed minimum",
                    )
                ]
            )
    if not values:
        return _Unknown([("-", _scope(ctx), "every qualifying interval is a gap")])
    if isinstance(stat, Stat):
        if stat.name == "mean":
            return sum(values, Fraction(0)) / len(values)
        return max(values) if stat.name == "max" else min(values)
    if isinstance(stat.p, SlotRef):
        raise FormulaError("resolve the template first")
    ordered = sorted(values)
    rank = max(1, math.ceil(stat.p.value * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def evaluate(variant: MwDefinition, env: Env) -> Evaluation:
    """Evaluate a resolved definition on an environment. Exact; no clock; no resampling."""
    if variant.slots:
        raise FormulaError("evaluate a resolved variant, not a template")
    if any(
        b - a != timedelta(seconds=env.resolution_s)
        for a, b in zip(env.intervals, env.intervals[1:], strict=False)
    ):
        raise FormulaError("intervals are consecutive at the stated resolution")
    out = _eval(_Ctx(variant, env), variant.root)
    vh = variant.content_hash()
    if isinstance(out, _Unknown):
        missing = sorted({(t, s, r) for t, s, r in out.missing})
        return Evaluation(
            variant_hash=vh,
            result=Unknown(kind="unknown", reason=f"{len(missing)} input(s) missing"),
            missing=tuple(
                MissingTerm(term_id=t if t != "-" else "interval_coverage", scope=s, reason=r)
                for t, s, r in missing
            ),
        )
    if isinstance(out, _NA):
        return Evaluation(
            variant_hash=vh,
            result=NotApplicable(kind="not_applicable", reason=out.reason),
            missing=(),
        )
    if not isinstance(out, Fraction):
        raise FormulaError("the root did not reduce to a number")
    return Evaluation(variant_hash=vh, result=Q.of(out), missing=())


def utc_half_hours(start: UtcInstant, count: int, resolution_s: int = 1800) -> tuple[datetime, ...]:
    """``count`` consecutive interval starts from ``start`` (a helper for verifiers and tests)."""
    return tuple(start + timedelta(seconds=resolution_s * i) for i in range(count))


# --------------------------------------------------------------------------------------------
# Rendering: one canonical text, and the KPI tracker's four formula keys
# --------------------------------------------------------------------------------------------


def _q_text(q: Q) -> str:
    return str(q.num) if q.den == 1 else f"{q.num}/{q.den}"


def _ref_text(r: Any) -> str:
    if isinstance(r, SlotRef):
        return f"<{r.slot_id}>"
    if isinstance(r, Q):
        return _q_text(r)
    if isinstance(r, Stat):
        return r.name
    if isinstance(r, PercentileStat):
        return f"percentile(nearest rank, p={_ref_text(r.p)})"
    if isinstance(r, Resolution):
        return f"{r.seconds} s"
    if isinstance(r, GapUnknownIfAny):
        return "unknown if any interval is a gap"
    if isinstance(r, MinCoverage):
        return f"at least {_ref_text(r.ratio)} of qualifying intervals read"
    if isinstance(r, OnFail):
        return r.value
    if isinstance(r, DayTypes):
        return r.value
    if isinstance(r, Window):
        return (
            f"local days {r.first_day.isoformat()} to {r.last_day.isoformat()} "
            f"({r.period_citation}), {_ref_text(r.day_types)}, minutes "
            f"[{_ref_text(r.start_minute)}, {_ref_text(r.end_minute)})"
        )
    return str(r)


def _node_text(n: Node) -> str:
    if isinstance(n, TermRef):
        return n.term_id
    if isinstance(n, Const):
        return f"{_ref_text(n.value)} {n.unit.value}"
    if isinstance(n, Add):
        return "(" + " + ".join(_node_text(a) for a in n.args) + ")"
    if isinstance(n, Sub):
        return f"({_node_text(n.lhs)} - {_node_text(n.rhs)})"
    if isinstance(n, Scale):
        return f"({_node_text(n.body)} x {_node_text(n.factor)})"
    if isinstance(n, ClipLow):
        return f"max({_node_text(n.body)}, {_node_text(n.floor)})"
    if isinstance(n, Sum):
        return f"sum_{n.over}({_node_text(n.body)})"
    if isinstance(n, TimeAgg):
        return (
            f"time_agg[stat={_ref_text(n.statistic)}; window={_ref_text(n.window)}; "
            f"resolution={_ref_text(n.resolution)}; gaps={_ref_text(n.gap_policy)}]"
            f"({_node_text(n.body)})"
        )
    if isinstance(n, Where):
        return (
            f"where[{' and '.join(n.conditions)}; otherwise {_ref_text(n.on_fail)}]"
            f"({_node_text(n.body)})"
        )
    return (
        f"choose<{n.slot_id}>{{"
        + " | ".join(f"{b.option_id}: {_node_text(b.body)}" for b in n.branches)
        + "}"
    )


def _pred_text(p: Predicate) -> str:
    if isinstance(p, FlagIs):
        return f"{p.term_id} is true"
    if isinstance(p, QualityGood):
        return "quality is good for " + ", ".join(p.term_ids)
    if isinstance(p, Compare):
        sym = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[p.op]
        return f"{_node_text(p.lhs)} {sym} {_node_text(p.rhs)}"
    if isinstance(p, AllOf):
        return "(" + " and ".join(_pred_text(q) for q in p.preds) + ")"
    if isinstance(p, AnyOf):
        return "(" + " or ".join(_pred_text(q) for q in p.preds) + ")"
    return f"not ({_pred_text(p.pred)})"


def render_text(d: MwDefinition) -> str:
    """One canonical text of the formula (the tree, then its conditions). Same tree, same text."""
    lines = [f"{d.definition_id} [kW] = {_node_text(d.root)}"]
    for c in d.conditions:
        lines.append(f"  {c.condition_id} ({c.scope}): {_pred_text(c.predicate)}")
    return "\n".join(lines)


def _time_aggs(n: Node) -> list[TimeAgg]:
    if isinstance(n, TimeAgg):
        return [n, *_time_aggs(n.body)]
    if isinstance(n, Add):
        return [t for a in n.args for t in _time_aggs(a)]
    if isinstance(n, Sub):
        return _time_aggs(n.lhs) + _time_aggs(n.rhs)
    if isinstance(n, Choose):
        return [t for b in n.branches for t in _time_aggs(b.body)]
    if isinstance(n, Scale | ClipLow | Sum | Where):
        return _time_aggs(n.body)
    return []


def render_kpi_formula(d: MwDefinition) -> dict[str, str]:
    """The four keys the grant pack's KPI tracker will hold (``kpi_tracker.yaml`` ``formula``),
    each derived from the tree, so the words cannot drift from the formula."""
    points = sorted({t.measurement_point for t in d.terms})
    aggs = {
        f"{_ref_text(t.statistic)} over {_ref_text(t.window)} at {_ref_text(t.resolution)}, "
        f"{_ref_text(t.gap_policy)}"
        for t in _time_aggs(d.root)
    }
    return {
        "quantity": f"{d.quantity} [kW, reported in MW]; formula: {_node_text(d.root)}",
        "measurement_point": "; ".join(points),
        "averaging_period": "; ".join(sorted(aggs)),
        "qualifying_conditions": "; ".join(
            f"{c.condition_id} ({c.scope}): {c.label}: {_pred_text(c.predicate)}"
            for c in d.conditions
        )
        or "none",
    }
