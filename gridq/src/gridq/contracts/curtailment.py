"""IC-2 ``CurtailmentScenarioSet``: frozen contract, version 1.1.0 (roadmap WP-CONN-03, spec A1-06).

A set of probability-weighted restriction scenarios for one site's connection: per scenario a
half-hourly restriction series, a minute series where the data allows it, the restriction events
behind them, and a weight. Producer: A1 (``curtailment_scenario_engine``, WP-CONN-04). Consumers:
A3 adequacy (minute series and events), A5 sizer and A7 finance (half-hourly series and weights),
B2 live reserve policy. Instances are **referenced by hash**
(`CurtailmentScenarioSet.content_hash`), which WP-CONN-06 folds into ``input_hash`` so one set
governs investment, dispatch and verification.

Spec §10A IC-2 "must contain" → this contract:

=========================  ==============================================================
spec IC-2                  field
=========================  ==============================================================
per-scenario half-hourly   ``scenarios[*].half_hourly`` (`RestrictionSeries`, 1800 s)
per-scenario minute        ``scenarios[*].minute`` (60 s `RestrictionSeries` or N/A)
restriction series         depth (MW) below the reference capacity; ``direction``
weights                    ``scenarios[*].weight`` (`Rational`): exact, sum to exactly 1
evidence class             ``evidence_class`` + ``signal_basis`` + ``provenance``
hash                       `CurtailmentScenarioSet.content_hash` (canonical JSON)
=========================  ==============================================================

**Why the weights are exact rationals.** A weight is ``{"numerator": n, "denominator": d}`` in
lowest terms, and the set is refused unless the weights sum to exactly ``1`` in rational
arithmetic (`fractions.Fraction`). There is no tolerance. Reasons: (1) equal weights over three
observed years are ``1/3``, which no finite decimal or binary float represents, so a float
contract would need a tolerance; (2) any tolerance makes "sums to one" a judgement call that two
consumers can answer differently, and lets a set whose weights sum to 0.9999 validate; (3) integers
have exactly one canonical JSON form, so the weights can never perturb the hash. Consumers that
need a float use `Rational.as_float`.

**Why the series are step-encoded.** A minute series over a year is 525,600 values per scenario,
almost all zero. `RestrictionSeries` stores the dense series as its change points (``steps``:
index and value, starting at index 0, strictly increasing, no two adjacent steps equal). That
encoding is unique for a given dense series, so the hash does not depend on how a producer
chose to compress it; `RestrictionSeries.from_dense` is the one encoder and ``dense()`` decodes.

**Time axis.** Every scenario has the same length (``n_half_hours``) and its own ``period_start``
(UTC, on a half-hour boundary). A modelled set usually gives every scenario the same start; a
replay of observed years gives each scenario the window in the year it replays. Consumers index
by offset from ``period_start``. No target year is invented.

**Consistency checks** (all on the contract, so a consumer never has to re-check them):
the minute series, where present, averages to the half-hourly series (±``SERIES_TOLERANCE_MW``);
every interval with a positive depth overlaps a listed event; the energy in the half-hourly
series equals the energy of the events (mean depth x duration, ±``ENERGY_TOLERANCE_MWH``
absolute plus 1e-9 relative, float rounding only); events are sorted, non-overlapping and inside
the scenario's period; scenarios are sorted by id (one canonical order).

Version: ``CONTRACT_VERSION`` (a ``Literal`` on every instance); JSON Schema frozen at
``schemas/ic2_curtailment_scenario_set.v1.1.0.schema.json``; git tag ``GIT_TAG`` (create on
commit). Depends on IC-4 v1.1.0 (`EvidenceRef`, `IssuerRole`) and IC-1 v1.1.0 (by hash).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from math import gcd
from typing import Annotated, Final, Literal, final

from pydantic import Field, model_validator

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
)
from gridq.contracts.evidence import V1_0_0_ISSUER_ROLES, EvidenceRef, IssuerRole

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "ENERGY_TOLERANCE_MWH",
    "GIT_TAG",
    "SERIES_TOLERANCE_MW",
    "SUPPORTED_VERSIONS",
    "ConnectionAgreementRef",
    "CurtailmentScenarioSet",
    "EvidenceClass",
    "ModelledRun",
    "PublishedSource",
    "Rational",
    "ReplayedRealData",
    "RestrictionEvent",
    "RestrictionSeries",
    "Scenario",
    "ScenarioSetRef",
    "SignalBasis",
    "Step",
    "SyntheticShape",
    "WeightBasis",
    "expected_energy_mwh",
    "expected_series",
    "scenario_set_ref",
]

CONTRACT_ID: Final = "IC-2"
CONTRACT_VERSION: Final = "1.1.0"
#: Every version this module reads. The last one is the version it writes.
SUPPORTED_VERSIONS: Final = ("1.0.0", "1.1.0")
GIT_TAG: Final = "contracts/ic2-curtailment-scenario-set/v1.1.0"

HALF_HOUR_S: Final = 1800
MINUTE_S: Final = 60
#: Minute series vs half-hourly series: the largest allowed difference of a half-hour mean (MW).
SERIES_TOLERANCE_MW: Final = 1e-9
#: Series energy vs event energy: absolute allowance (MWh), plus 1e-9 relative. Float rounding only.
ENERGY_TOLERANCE_MWH: Final = 1e-6

NonNegFloat = Annotated[float, Field(ge=0)]
PosFloat = Annotated[float, Field(gt=0)]
_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class EvidenceClass(StrEnum):
    """What the set rests on (roadmap principle: live, replayed real data, or modelled).

    There is no "live": a scenario set is a distribution, never a live reading.
    """

    REPLAYED_REAL_DATA = "replayed_real_data"
    MODELLED = "modelled"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class SignalBasis(StrEnum):
    """Which physical signal the restriction history describes (spec A1-04, WP-CONN-05).

    Published DNO curtailment is almost all generation-export curtailment; using it for a demand
    site's import restriction is a proxy, and this field makes that visible on every set.
    """

    GENERATION_EXPORT_CURTAILMENT = "generation_export_curtailment"
    DEMAND_IMPORT_RESTRICTION = "demand_import_restriction"
    LOADING_HEADROOM = "loading_headroom"


class WeightBasis(StrEnum):
    """How the weights were set. The rule itself is stated in ``weight_rule``."""

    #: Every scenario is one observed period (a year, an event) and all weights are equal.
    EQUAL_OVER_OBSERVED_PERIODS = "equal_over_observed_periods"
    #: Weights are the sampling probabilities of a stochastic model run.
    MODEL_PROBABILITY = "model_probability"


# --------------------------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------------------------


class Rational(ContractModel):
    """An exact weight in (0, 1], in lowest terms: ``{"numerator": 1, "denominator": 3}``."""

    numerator: Annotated[int, Field(ge=1)]
    denominator: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _lowest_terms(self) -> Rational:
        if gcd(self.numerator, self.denominator) != 1:
            raise ValueError(
                "a weight must be in lowest terms (e.g. 1/2, not 2/4): one value, one form"
            )
        if self.numerator > self.denominator:
            raise ValueError("a weight cannot exceed one")
        return self

    @classmethod
    def of(cls, value: Fraction) -> Rational:
        return cls(numerator=value.numerator, denominator=value.denominator)

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def as_float(self) -> float:
        return self.numerator / self.denominator


# --------------------------------------------------------------------------------------------
# Series and events
# --------------------------------------------------------------------------------------------


class Step(ContractModel):
    """From interval ``index`` until the next step, the restriction depth is ``mw``."""

    index: Annotated[int, Field(ge=0)]
    mw: NonNegFloat


class RestrictionSeries(ContractModel):
    """A dense series of restriction depth (MW, mean over each interval), stored as change points.

    The depth is how far the permitted import (or export) is reduced below the set's reference
    capacity in that interval, averaged over the interval. ``0.0`` means unrestricted.
    """

    resolution_s: Literal[60, 1800]
    n_intervals: Annotated[int, Field(ge=1)]
    steps: tuple[Step, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _canonical(self) -> RestrictionSeries:
        if self.steps[0].index != 0:
            raise ValueError("the first step must start at index 0")
        for a, b in zip(self.steps, self.steps[1:], strict=False):
            if b.index <= a.index:
                raise ValueError("step indices must be strictly increasing")
            if b.mw == a.mw:
                raise ValueError(
                    "two adjacent steps have the same value; merge them (the encoding is unique)"
                )
        if self.steps[-1].index >= self.n_intervals:
            raise ValueError("a step index is beyond the end of the series")
        return self

    @classmethod
    def from_dense(
        cls, resolution_s: Literal[60, 1800], values: Sequence[float]
    ) -> RestrictionSeries:
        """The one encoder: dense values → canonical steps."""
        steps: list[Step] = []
        for i, v in enumerate(values):
            v = 0.0 if v == 0 else float(v)
            if not steps or steps[-1].mw != v:
                steps.append(Step(index=i, mw=v))
        return cls(resolution_s=resolution_s, n_intervals=len(values), steps=tuple(steps))

    def dense(self) -> tuple[float, ...]:
        out: list[float] = []
        bounds = [s.index for s in self.steps[1:]] + [self.n_intervals]
        for s, end in zip(self.steps, bounds, strict=True):
            out.extend([s.mw] * (end - s.index))
        return tuple(out)

    @property
    def interval_hours(self) -> float:
        return self.resolution_s / 3600

    def energy_mwh(self) -> float:
        """Restricted energy: sum of depth x interval length."""
        bounds = [s.index for s in self.steps[1:]] + [self.n_intervals]
        return sum(
            s.mw * (end - s.index) * self.interval_hours
            for s, end in zip(self.steps, bounds, strict=True)
        )

    def restricted_hours(self) -> float:
        """Hours with any restriction (depth > 0)."""
        bounds = [s.index for s in self.steps[1:]] + [self.n_intervals]
        return sum(
            (end - s.index) * self.interval_hours
            for s, end in zip(self.steps, bounds, strict=True)
            if s.mw > 0
        )

    def peak_mw(self) -> float:
        return max(s.mw for s in self.steps)


class RestrictionEvent(ContractModel):
    """One restriction event: when, how deep on average, how deep at peak, with what notice.

    ``source_ref`` is the publisher's own event id for replayed data, so any event can be traced
    to the recorded response; a modelled event has none.
    """

    start: UtcInstant
    end: UtcInstant
    mean_depth_mw: NonNegFloat
    peak_depth_mw: NonNegFloat | Unknown
    notice_s: NonNegFloat | Unknown
    source_ref: NonEmptyStr | NotApplicable

    @model_validator(mode="after")
    def _ordered(self) -> RestrictionEvent:
        if self.end <= self.start:
            raise ValueError("an event must end after it starts")
        if isinstance(self.peak_depth_mw, float) and self.peak_depth_mw < self.mean_depth_mw:
            raise ValueError("peak_depth_mw cannot be below mean_depth_mw")
        return self

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def energy_mwh(self) -> float:
        return self.mean_depth_mw * self.hours


def _positive_intervals_uncovered(
    series: RestrictionSeries, start: datetime, events: Sequence[RestrictionEvent]
) -> list[int]:
    step = timedelta(seconds=series.resolution_s)
    bad: list[int] = []
    for i, v in enumerate(series.dense()):
        if v <= 0:
            continue
        t0 = start + i * step
        t1 = t0 + step
        if not any(e.start < t1 and e.end > t0 for e in events):
            bad.append(i)
    return bad


class Scenario(ContractModel):
    """One weighted scenario: its period, its series and its events."""

    scenario_id: _ID
    label: NonEmptyStr
    weight: Rational
    #: UTC start of this scenario's series, on a half-hour boundary.
    period_start: UtcInstant
    half_hourly: RestrictionSeries
    minute: RestrictionSeries | NotApplicable
    events: tuple[RestrictionEvent, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Scenario:
        ps = self.period_start
        if ps.second or ps.microsecond or ps.minute not in (0, 30):
            raise ValueError("period_start must fall on a half-hour boundary (UTC)")
        hh = self.half_hourly
        if hh.resolution_s != HALF_HOUR_S:
            raise ValueError("half_hourly must have resolution_s == 1800")
        if isinstance(self.minute, RestrictionSeries):
            m = self.minute
            if m.resolution_s != MINUTE_S:
                raise ValueError("minute must have resolution_s == 60")
            if m.n_intervals != hh.n_intervals * 30:
                raise ValueError("the minute series must cover exactly the half-hourly period")
            md, hd = m.dense(), hh.dense()
            for k, v in enumerate(hd):
                mean = sum(md[30 * k : 30 * k + 30]) / 30
                if abs(mean - v) > SERIES_TOLERANCE_MW:
                    raise ValueError(
                        f"half-hour {k}: the minute series averages {mean} MW but the half-hourly "
                        f"series says {v} MW"
                    )
        end = ps + timedelta(seconds=HALF_HOUR_S * hh.n_intervals)
        for a, b in zip(self.events, self.events[1:], strict=False):
            if b.start < a.end:
                raise ValueError("events must be sorted by start and must not overlap")
        for e in self.events:
            if e.start < ps or e.end > end:
                raise ValueError("an event lies outside the scenario's period")
        for series in (hh, self.minute):
            if isinstance(series, RestrictionSeries):
                bad = _positive_intervals_uncovered(series, ps, self.events)
                if bad:
                    raise ValueError(
                        f"{len(bad)} restricted interval(s) at {series.resolution_s} s "
                        f"(first index {bad[0]}) are not covered by any listed event"
                    )
        ev = sum(e.energy_mwh() for e in self.events)
        se = hh.energy_mwh()
        if abs(ev - se) > ENERGY_TOLERANCE_MWH + 1e-9 * max(ev, se):
            raise ValueError(
                f"the half-hourly series holds {se} MWh of restriction but the events hold {ev} MWh"
            )
        return self

    @property
    def period_end(self) -> datetime:
        return self.period_start + timedelta(seconds=HALF_HOUR_S * self.half_hourly.n_intervals)


# --------------------------------------------------------------------------------------------
# Provenance (evidence class, reusing IC-4's issuer roles and evidence references)
# --------------------------------------------------------------------------------------------


class PublishedSource(ContractModel):
    """One recorded response from a publisher; its bytes are hashed, as an IC-4 document is.

    ``registered_evidence`` is the IC-4 `EvidenceRef` once the response is registered as evidence
    (type ``regulatory_publication``); until then it is `Unknown` with the reason.
    """

    publisher: NonEmptyStr
    publisher_role: IssuerRole
    dataset: NonEmptyStr
    #: The request URL, without credentials.
    url: NonEmptyStr
    retrieved_at: UtcInstant
    response_sha256: Sha256
    licence: NonEmptyStr
    attribution: NonEmptyStr
    #: Any restriction on who may see the data, as recorded by the data owner's register.
    distribution_gate: NonEmptyStr | NotApplicable
    registered_evidence: EvidenceRef | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _has_bytes(self) -> PublishedSource:
        if self.response_sha256 == EMPTY_SHA256:
            raise ValueError("response_sha256 is the hash of zero bytes: no response was recorded")
        return self


class ReplayedRealData(ContractModel):
    """Scenarios replayed from published observations, by a stated derivation."""

    kind: Literal["replayed_real_data"]
    sources: tuple[PublishedSource, ...] = Field(min_length=1)
    #: How each series and event was derived from the recorded response(s).
    derivation: NonEmptyStr


class ModelledRun(ContractModel):
    """Scenarios produced by the A1 engine (WP-CONN-04), identified by its run inputs."""

    kind: Literal["modelled"]
    engine: NonEmptyStr
    engine_version: NonEmptyStr
    input_hash: Sha256
    seed: Annotated[int, Field(ge=0)] | NotApplicable
    #: IC-4 evidence the run rests on (offer letter, calibration data); may be empty.
    evidence: tuple[EvidenceRef, ...]


class SyntheticShape(ContractModel):
    """Schema-test data with a realistic shape; not a site anyone holds. Consumers must refuse it
    outside tests."""

    kind: Literal["synthetic_fixture"]
    statement: NonEmptyStr
    modelled_on: NonEmptyStr


class ConnectionAgreementRef(ContractModel):
    """The IC-1 agreement the restrictions apply under, by content hash."""

    kind: Literal["connection_agreement_ref"]
    contract: Literal["IC-1"]
    contract_version: Version1x
    agreement_hash: Sha256


_PROVENANCE_FOR: Final = {
    EvidenceClass.REPLAYED_REAL_DATA: "replayed_real_data",
    EvidenceClass.MODELLED: "modelled",
    EvidenceClass.SYNTHETIC_FIXTURE: "synthetic_fixture",
}


# --------------------------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------------------------


@final
class CurtailmentScenarioSet(ContractModel):
    """IC-2 v1.1.0. Weighted restriction scenarios for one site.

    Weights: each is an exact rational {numerator, denominator} in lowest terms, and they must
    sum to exactly 1 in rational arithmetic, with NO tolerance. Why: equal weights over three
    observed periods are 1/3, which no decimal or float represents exactly; any tolerance would
    make "sums to one" a judgement two consumers can answer differently; and integers have one
    canonical JSON form, so weights never perturb the hash.

    Series: depth in MW (mean over each interval), step-encoded (first step at index 0, indices
    strictly increasing, adjacent values different: one encoding per dense series). The minute
    series, where present, must average to the half-hourly one within 1e-9 MW; the half-hourly
    energy must equal the events' energy (mean depth x duration) within 1e-6 MWh plus 1e-9
    relative. Both tolerances absorb float rounding only. Scenarios are sorted by
    ``scenario_id``; events by start, non-overlapping, inside the scenario's period.
    """

    contract: Literal["IC-2"] = CONTRACT_ID
    contract_version: Literal["1.0.0", "1.1.0"] = CONTRACT_VERSION

    set_id: _ID
    site_id: NonEmptyStr
    #: Whether the restriction limits import (a demand site) or export (generation).
    direction: Literal["import", "export"]
    signal_basis: SignalBasis
    #: The publisher's or engine's definition of the depth, in words.
    depth_definition: NonEmptyStr
    reference_capacity_mw: PosFloat | Unknown
    connection_agreement: ConnectionAgreementRef | NotApplicable | Unknown = Field(
        discriminator="kind"
    )

    evidence_class: EvidenceClass
    provenance: ReplayedRealData | ModelledRun | SyntheticShape = Field(discriminator="kind")

    weight_basis: WeightBasis
    #: The rule that produced the weights, in words (e.g. "equal over the observed years").
    weight_rule: NonEmptyStr

    n_half_hours: Annotated[int, Field(ge=1)]
    scenarios: tuple[Scenario, ...] = Field(min_length=1)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("CurtailmentScenarioSet is final: a new shape is a new contract version")

    @model_validator(mode="after")
    def _consistent(self) -> CurtailmentScenarioSet:
        total = sum((s.weight.as_fraction() for s in self.scenarios), Fraction(0))
        if total != 1:
            raise ValueError(f"scenario weights sum to {total}, not exactly 1")
        ids = [s.scenario_id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario_id values must be unique")
        if ids != sorted(ids):
            raise ValueError("scenarios must be sorted by scenario_id (one canonical order)")
        for s in self.scenarios:
            if s.half_hourly.n_intervals != self.n_half_hours:
                raise ValueError(f"scenario {s.scenario_id} does not have n_half_hours intervals")
        if len({isinstance(s.minute, RestrictionSeries) for s in self.scenarios}) > 1:
            raise ValueError("either every scenario has a minute series or none does")
        if self.weight_basis is WeightBasis.EQUAL_OVER_OBSERVED_PERIODS:
            if len({s.weight.as_fraction() for s in self.scenarios}) != 1:
                raise ValueError("weight_basis is equal_over_observed_periods but weights differ")
        if self.provenance.kind != _PROVENANCE_FOR[self.evidence_class]:
            raise ValueError(
                f"evidence_class {self.evidence_class.value} needs provenance of kind "
                f"{_PROVENANCE_FOR[self.evidence_class]}, not {self.provenance.kind}"
            )
        if isinstance(self.reference_capacity_mw, float):
            for s in self.scenarios:
                if s.half_hourly.peak_mw() > self.reference_capacity_mw:
                    raise ValueError(
                        f"scenario {s.scenario_id}: depth exceeds reference_capacity_mw"
                    )
        is_synthetic = self.evidence_class is EvidenceClass.SYNTHETIC_FIXTURE
        if is_synthetic != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the set is synthetic")
        return self

    @model_validator(mode="after")
    def _down_level(self) -> CurtailmentScenarioSet:
        # Down-level rule (WP-BO-06): a set labelled 1.0.0 holds only what 1.0.0 allowed, so every
        # embedded reference is 1.0.0 and every publisher role is a 1.0.0 IC-4 role.
        if self.contract_version != "1.0.0":
            return self
        refs: list[tuple[str, str]] = []
        if isinstance(self.connection_agreement, ConnectionAgreementRef):
            refs.append(("connection_agreement IC-1", self.connection_agreement.contract_version))
        prov = self.provenance
        if isinstance(prov, ModelledRun):
            refs.extend(("provenance.evidence IC-4", r.contract_version) for r in prov.evidence)
        elif isinstance(prov, ReplayedRealData):
            for src in prov.sources:
                if src.publisher_role.value not in V1_0_0_ISSUER_ROLES:
                    raise ValueError(
                        f"publisher_role {src.publisher_role.value} is not in IC-2 1.0.0: label "
                        "the set 1.1.0"
                    )
                if isinstance(src.registered_evidence, EvidenceRef):
                    refs.append(
                        ("registered_evidence IC-4", src.registered_evidence.contract_version)
                    )
        for where, version in refs:
            if version != "1.0.0":
                raise ValueError(
                    f"{where} reference version {version} is not in IC-2 1.0.0: label the set 1.1.0"
                )
        return self

    @property
    def has_minute_series(self) -> bool:
        return isinstance(self.scenarios[0].minute, RestrictionSeries)

    def content_hash(self) -> str:
        """``sha256:<hex>`` of the canonical JSON: the identity consumers and input_hash use."""
        return content_hash(self)


class ScenarioSetRef(ContractModel):
    """A reference to an IC-2 set by content hash (what IC-3 envelopes and run records embed)."""

    kind: Literal["scenario_set_ref"]
    contract: Literal["IC-2"]
    contract_version: Version1x
    scenario_set_hash: Sha256


def scenario_set_ref(obj: CurtailmentScenarioSet) -> ScenarioSetRef:
    return ScenarioSetRef(
        kind="scenario_set_ref",
        contract=CONTRACT_ID,
        contract_version=obj.contract_version,
        scenario_set_hash=obj.content_hash(),
    )


# --------------------------------------------------------------------------------------------
# Consumer helpers: what A3/A5/A7/B2 need without the engine. Pure, deterministic, exact weights.
# --------------------------------------------------------------------------------------------


def _weighted(pairs: Iterable[tuple[Fraction, float]]) -> float:
    # Exact: every float is a dyadic rational, so the weighted sum is computed without rounding
    # and rounded once at the end. The result does not depend on scenario order.
    return float(sum((w * Fraction(v) for w, v in pairs), Fraction(0)))


def expected_series(
    obj: CurtailmentScenarioSet, resolution: Literal["half_hourly", "minute"] = "half_hourly"
) -> tuple[float, ...]:
    """Probability-weighted mean depth per interval, offset from each scenario's start."""
    dense: list[tuple[Fraction, tuple[float, ...]]] = []
    for s in obj.scenarios:
        series = s.half_hourly if resolution == "half_hourly" else s.minute
        if not isinstance(series, RestrictionSeries):
            raise ValueError("this set has no minute series")
        dense.append((s.weight.as_fraction(), series.dense()))
    n = len(dense[0][1])
    return tuple(_weighted((w, d[i]) for w, d in dense) for i in range(n))


def expected_energy_mwh(obj: CurtailmentScenarioSet) -> float:
    """Probability-weighted restricted energy over the period."""
    return _weighted((s.weight.as_fraction(), s.half_hourly.energy_mwh()) for s in obj.scenarios)
