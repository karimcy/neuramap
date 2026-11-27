"""The BESS energy a site requires, with and without its gensets (WP-SITE-05, D212).

**Definition.** The required usable energy is the least whole kWh at which a battery that starts
full and is held full (``initial = operating = usable``), at the stated ratings and efficiencies,
never has a cover request clamped by the resilience floor in the ``reserve_held`` posture. What it
still cannot serve is power-bound (need above ``rating - resilience kW``) and no energy fixes it;
that is reported apart, never folded into the energy.

**Exact single pass (sequent peak).** With the battery held full, the kernel's charge step reduces
to ``D <- max(0, D + out - floor(charge_j x num / den))``, so the deficit below full ``D`` does not
depend on the capacity. Per path and per kernel step (per second in a fine minute)::

    out = ceil(min(need_w, cover_cap_w) x dt x den / num)      # battery-side cover request
    P   = D + out                                              # read after cover, before charge
    D   = max(0, P - floor(charge_w x dt x num / den))

and a path is shortfall-free (beyond its power-bound part) exactly when
``usable_j >= max(flat_floor_j, max_t(floor_j[t] + P_t))``. One pass gives the threshold, not a
search result, and a test checks it against the kernel at ``E*`` and ``E* - 1`` kWh.

**Gensets.** The plan never reads the battery's state of energy (`gridq.adequacy.standby`), which is
what keeps ``D`` capacity-free. The record carries the same figure with the gensets removed (the
co-located sources and thermal flex stay), and their difference, ``genset_displaced_kwh``: genset
output only lowers need and never takes charging headroom, so removing them never lowers the
requirement (acceptance 2 of WP-SITE-05, a property test).
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction
from typing import Final, Literal

import numpy as np
from pydantic import Field, model_validator

from gridq.adequacy._common import J_PER_KWH, NonNegInt, RecordClass, ask, combine_record_class
from gridq.adequacy.floor import ResilienceFloorPolicy, Sized, StandbyDerivation
from gridq.adequacy.inputs import (
    BessParameters,
    EnsembleMaximum,
    OneWay,
    RoundTripOnly,
    StatedQuantile,
    efficiency_value,
    missing_bess_asks,
)
from gridq.adequacy.load import LoadIdentity, LoadSource
from gridq.adequacy.result import AdequacyConfig, StatedImportCapacity
from gridq.adequacy.standby import GenerationArrays, plan_generation
from gridq.contracts._base import (
    ContractModel,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
    not_applicable,
)
from gridq.contracts.curtailment import CurtailmentScenarioSet
from gridq.contracts.thermal_flex import ThermalFlexAvailability
from gridq.ops.dossier_model import AskHolder, DossierAsk

__all__ = [
    "ArmRequirement",
    "PowerBound",
    "REQUIRED_SCHEMA",
    "RequiredBessEnergy",
    "RequiredComputed",
    "RequiredNotComputed",
    "path_requirements",
    "required_bess_energy",
]

REQUIRED_SCHEMA: Final = "gridq.adequacy.required_bess_energy"
_REQUIRED_FIELDS: Final = (
    "discharge_rating_kw",
    "discharge_efficiency",
    "charge_rating_kw",
    "charge_efficiency",
)


class PowerBound(ContractModel):
    """What no energy can serve: need above the cover power cap."""

    paths_with_power_shortfall: NonNegInt
    max_unserved_ws: NonNegInt
    max_excess_kw: NonNegInt


class RequiredAt(ContractModel):
    scenario_id: NonEmptyStr
    draw: NonNegInt


class ArmRequirement(ContractModel):
    """One arm (with gensets, or with them removed). ``required_kwh`` is the stated statistic over
    the paths; ``ensemble_max_kwh`` is always reported (it needs no risk level)."""

    required_kwh: NonNegInt
    ensemble_max_kwh: NonNegInt
    #: ``required_kwh`` less the flat resilience floor: the energy for cover.
    cover_kwh: NonNegInt
    binding: RequiredAt
    power_bound: PowerBound


class RequiredComputed(ContractModel):
    kind: Literal["computed"]
    floor_kwh: NonNegInt
    with_gensets: ArmRequirement
    without_gensets: ArmRequirement | NotApplicable
    genset_displaced_kwh: NonNegInt | NotApplicable

    @model_validator(mode="after")
    def _displaced(self) -> RequiredComputed:
        w, wo = self.with_gensets, self.without_gensets
        if isinstance(wo, ArmRequirement):
            if wo.required_kwh < w.required_kwh:
                raise ValueError("removing gensets never lowers the required energy")
            if self.genset_displaced_kwh != wo.required_kwh - w.required_kwh:
                raise ValueError("genset_displaced_kwh is the difference of the two arms")
        elif not isinstance(self.genset_displaced_kwh, NotApplicable):
            raise ValueError("no displaced energy without a counterfactual arm")
        return self


class RequiredNotComputed(ContractModel):
    kind: Literal["not_computed"]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)


class RequiredBinding(ContractModel):
    scenario_set_hash: Sha256
    load: LoadIdentity
    floor_policy_hash: Sha256
    bess_parameters_hash: Sha256
    config_hash: Sha256
    thermal_flex_hash: Sha256 | NotApplicable
    generation_hash: Sha256 | NotApplicable


class RequiredBessEnergy(ContractModel):
    """``gridq.adequacy.required_bess_energy`` 1.0.0 (D212)."""

    schema_name: Literal["gridq.adequacy.required_bess_energy"] = REQUIRED_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    convention: Literal["started_full_held_full"]
    posture: Literal["reserve_held"]
    thermal: Literal["off", "ic11"]
    statistic: EnsembleMaximum | StatedQuantile | Unknown = Field(discriminator="kind")
    inputs: RequiredBinding
    outcome: RequiredComputed | RequiredNotComputed = Field(discriminator="kind")
    data_requests: tuple[DossierAsk, ...]

    @model_validator(mode="after")
    def _asks(self) -> RequiredBessEnergy:
        ids = [a.ask_id for a in self.data_requests]
        if ids != sorted(set(ids)):
            raise ValueError("data_requests are sorted by ask_id, without repeats")
        if isinstance(self.outcome, RequiredNotComputed) and not self.data_requests:
            raise ValueError("a requirement that is not computed says what is missing")
        return self

    def content_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------- the single pass


def _eta(e: OneWay | RoundTripOnly) -> tuple[int, int]:
    f = efficiency_value(e)
    return f.numerator, f.denominator


def path_requirements(
    net_w: np.ndarray,
    permit_w: np.ndarray,
    cover_cap_w: np.ndarray,
    charge_cap_w: np.ndarray,
    floor_j: np.ndarray,
    fine: np.ndarray,
    eta_discharge: tuple[int, int],
    eta_charge: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per draw: the least usable joules ``max_t(floor_j[t] + P_t)`` (not yet rounded or raised to
    the flat floor), the time it binds (seconds from the period start), the power-bound unserved
    joules, and the largest power-bound excess (W). Exact integers, vectorised over draws."""
    n, m = net_w.shape
    dn, dd = eta_discharge
    cn, cd = eta_charge
    d = np.zeros(n, dtype=np.int64)
    best = np.full(n, -1, dtype=np.int64)
    best_t = np.zeros(n, dtype=np.int64)
    unserved = np.zeros(n, dtype=np.int64)
    excess = np.zeros(n, dtype=np.int64)
    for minute in range(m):
        net = net_w[:, minute]
        permit = int(permit_w[minute])
        need = np.maximum(net - permit, 0)
        if not need.any() and not d.any():
            fl = int(floor_j[minute])
            better = fl > best
            if better.any():
                best = np.where(better, fl, best)
                best_t = np.where(better, minute * 60, best_t)
            continue
        cap = int(cover_cap_w[minute])
        cover = np.minimum(need, cap)
        over = need - cover
        excess = np.maximum(excess, over)
        ccap = int(charge_cap_w[minute])
        charge_w = np.minimum(np.maximum(permit - net, 0), ccap) if ccap > 0 else np.zeros_like(net)
        fl = int(floor_j[minute])
        sub = 60 if fine[minute] else 1
        dt = 60 // sub
        out = -(-(cover * dt * dd) // dn)
        stored = (charge_w * dt * cn) // cd
        for j in range(sub):
            peak = d + out
            level = fl + peak
            better = level > best
            if better.any():
                best = np.where(better, level, best)
                best_t = np.where(better, minute * 60 + j * dt, best_t)
            d = np.maximum(peak - stored, 0)
            unserved += over * dt
    return best, best_t, unserved, excess


def _ceil_kwh(j: int) -> int:
    return -(-j // J_PER_KWH)


def _statistic_index(
    values: list[tuple[int, int, int]], weights: list[Fraction], q: Fraction | None
) -> int:
    """Index into ``values`` (sorted ascending) of the weighted "higher" quantile, or the max."""
    if q is None:
        return len(values) - 1
    acc = Fraction(0)
    for i, w in enumerate(weights):
        acc += w
        if acc >= q:
            return i
    return len(values) - 1


def _arm_from(
    rows: list[tuple[Fraction, str, int, int, int, int]],
    flat_floor_j: int,
    floor_kwh: int,
    q: Fraction | None,
) -> ArmRequirement:
    """``rows``: (path weight, scenario id, draw, least joules, unserved J, excess W)."""
    kwh = [(_ceil_kwh(max(flat_floor_j, j)), sid, d, w, u, x) for w, sid, d, j, u, x in rows]
    order = sorted(range(len(kwh)), key=lambda i: (kwh[i][0], kwh[i][1], kwh[i][2]))
    ranked = [(kwh[i][0], i, 0) for i in order]
    idx = _statistic_index(ranked, [kwh[i][3] for i in order], q)
    chosen = kwh[order[idx]]
    top = kwh[order[-1]]
    return ArmRequirement(
        required_kwh=chosen[0],
        ensemble_max_kwh=top[0],
        cover_kwh=max(0, chosen[0] - floor_kwh),
        binding=RequiredAt(scenario_id=chosen[1], draw=chosen[2]),
        power_bound=PowerBound(
            paths_with_power_shortfall=sum(1 for r in kwh if r[4] > 0),
            max_unserved_ws=max(r[4] for r in kwh),
            max_excess_kw=-(-max(r[5] for r in kwh) // 1000),
        ),
    )


# --------------------------------------------------------------------------- entry point


def required_bess_energy(
    scenario_set: CurtailmentScenarioSet,
    load: LoadSource,
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    cfg: AdequacyConfig,
    *,
    statistic: EnsembleMaximum | StatedQuantile | Unknown,
    thermal: ThermalFlexAvailability | None = None,
    generation: object | None = None,
) -> RequiredBessEnergy:
    """The required usable energy with the gensets of ``generation`` (an
    `gridq.adequacy.onsite.OnsiteGeneration`) and with them removed. Pure. Reads only the
    ratings and efficiencies of ``bess``: its usable energy and states are what is being solved."""
    from gridq.adequacy import simulator as sim
    from gridq.adequacy.onsite import OnsiteGeneration, missing_onsite_asks

    gen = generation if isinstance(generation, OnsiteGeneration) else None
    if generation is not None and gen is None:
        raise TypeError("generation must be an OnsiteGeneration")
    site = policy.site_id
    ident = load.identity
    for name, other in (
        ("scenario set", scenario_set.site_id),
        ("load", ident.site_id),
        ("BESS parameters", bess.site_id),
        *((("thermal surface", thermal.site_id),) if thermal else ()),
        *((("generation model", gen.model.site_id),) if gen else ()),
    ):
        if other != site:
            raise ValueError(f"the {name} names site {other}; the floor policy is for {site}")
    if scenario_set.direction != "import":
        raise ValueError("the required energy is for import restrictions; this set is export")
    record_class = combine_record_class(
        policy.record_class,
        bess.record_class,
        ident.record_class,
        "synthetic_fixture" if scenario_set.evidence_class.value == "synthetic_fixture" else "",
        thermal.record_class if thermal else "",
        gen.model.record_class if gen else "",
    )
    asks: dict[str, DossierAsk] = {a.ask_id: a for a in policy.data_requests}
    reasons: list[str] = []
    if not isinstance(policy.state, Sized):
        reasons.append(f"the resilience floor is {policy.state.kind}")
    for name, a in missing_bess_asks(bess, _REQUIRED_FIELDS):
        reasons.append(f"BESS {name} is unknown")
        asks[a.ask_id] = a
    if isinstance(statistic, Unknown):
        reasons.append("the statistic over the load draws is not stated")
        a = ask(
            "State how the required BESS energy is judged over the load ensemble: the ensemble "
            "maximum, or a stated quantile across draws",
            site_id=site,
        )
        asks[a.ask_id] = a
    if gen is not None:
        for _f, a in missing_onsite_asks(gen.model):
            asks[a.ask_id] = a
    p_ref_w: int | None
    if isinstance(scenario_set.reference_capacity_mw, float):
        p_ref_w = sim._mw_to_w(scenario_set.reference_capacity_mw)
    elif isinstance(cfg.import_capacity, StatedImportCapacity):
        p_ref_w = cfg.import_capacity.kw * 1000
    else:
        p_ref_w = None
        reasons.append("the import capacity the restriction depth is measured below is unknown")
        a = ask(
            "State the site's agreed import capacity (kW): the scenario set gives its restriction "
            "depth below a reference capacity it does not state",
            site_id=site,
            holder=AskHolder.HOST,
        )
        asks[a.ask_id] = a

    prepared: list[sim._Prepared] = []
    if not reasons:
        assert p_ref_w is not None
        try:
            prepared = [
                sim._prepare(sc, load, policy, cfg, p_ref_w) for sc in scenario_set.scenarios
            ]
        except sim._Missing as missing:
            reasons.append(missing.reason)
            asks.update({a.ask_id: a for a in missing.asks})

    binding = RequiredBinding(
        scenario_set_hash=scenario_set.content_hash(),
        load=ident,
        floor_policy_hash=policy.content_hash(),
        bess_parameters_hash=bess.content_hash(),
        config_hash=content_hash(cfg),
        thermal_flex_hash=thermal.content_hash()
        if thermal
        else not_applicable("no thermal surface"),
        generation_hash=gen.content_hash() if gen else not_applicable("no generation model"),
    )
    outcome: RequiredComputed | RequiredNotComputed
    if reasons:
        outcome = RequiredNotComputed(kind="not_computed", reasons=tuple(reasons))
    else:
        outcome = _compute(
            prepared, scenario_set, policy, bess, cfg, statistic, thermal, gen, site, asks, ident
        )
    return RequiredBessEnergy(
        site_id=site,
        record_class=record_class,
        convention="started_full_held_full",
        posture="reserve_held",
        thermal="ic11" if thermal is not None else "off",
        statistic=statistic,
        inputs=binding,
        outcome=outcome,
        data_requests=tuple(asks[k] for k in sorted(asks)),
    )


def _compute(
    prepared: Iterable[object],
    scenario_set: CurtailmentScenarioSet,
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    cfg: AdequacyConfig,
    statistic: EnsembleMaximum | StatedQuantile | Unknown,
    thermal: ThermalFlexAvailability | None,
    gen: object | None,
    site: str,
    asks: dict[str, DossierAsk],
    ident: LoadIdentity,
) -> RequiredComputed:
    from gridq.adequacy import simulator as sim
    from gridq.adequacy.onsite import OnsiteGeneration

    assert isinstance(policy.state, Sized)
    assert isinstance(bess.discharge_rating_kw, int) and isinstance(bess.charge_rating_kw, int)
    assert isinstance(bess.discharge_efficiency, OneWay | RoundTripOnly)
    assert isinstance(bess.charge_efficiency, OneWay | RoundTripOnly)
    assert not isinstance(statistic, Unknown)
    q = statistic.q.as_fraction() if isinstance(statistic, StatedQuantile) else None
    flat_floor_j = policy.state.flat_floor_kwh * J_PER_KWH
    horizon_start = ident.horizon_start
    eta_d, eta_c = _eta(bess.discharge_efficiency), _eta(bess.charge_efficiency)
    with_rows: list[tuple[Fraction, str, int, int, int, int]] = []
    without_rows: list[tuple[Fraction, str, int, int, int, int]] = []
    for p in prepared:
        assert isinstance(p, sim._Prepared)
        s = p.scenario
        n, m = p.fac.shape
        calls = sim._plan_calls(thermal, p) if thermal is not None else ()
        base = sim.net_after_flex(p, calls)
        floor_kwh, res_kw = sim.floor_terms(p, policy)
        cover_cap = np.maximum(bess.discharge_rating_kw - res_kw, 0) * 1000
        charge_cap = np.full(m, bess.charge_rating_kw * 1000, dtype=np.int64)
        if not cfg.charge_during_restriction:
            charge_cap[p.restricted] = 0
        fine = sim._fine(s, m, cfg.resolution_s)
        arms: list[tuple[list[tuple[Fraction, str, int, int, int, int]], np.ndarray]] = []
        if isinstance(gen, OnsiteGeneration):
            arrays, _plan, plan_asks = plan_generation(
                gen,
                p,
                base,
                horizon_start=horizon_start,
                thermal="ic11" if thermal is not None else "off",
            )
            asks.update({a.ask_id: a for a in plan_asks})
            arms.append((with_rows, arrays.apply(base, p.permit_w)))
            no_gensets: GenerationArrays = arrays.without_gensets()
            arms.append((without_rows, no_gensets.apply(base, p.permit_w)))
        else:
            arms.append((with_rows, base))
        w = s.weight.as_fraction() / n
        for rows, net in arms:
            least, _at, unserved, excess = path_requirements(
                net, p.permit_w, cover_cap, charge_cap, floor_kwh * J_PER_KWH, fine, eta_d, eta_c
            )
            for d in range(n):
                rows.append((w, s.scenario_id, d, int(least[d]), int(unserved[d]), int(excess[d])))
    floor_kwh_flat = policy.state.flat_floor_kwh
    with_arm = _arm_from(with_rows, flat_floor_j, floor_kwh_flat, q)
    without: ArmRequirement | NotApplicable
    displaced: int | NotApplicable
    if gen is None:
        without = not_applicable("no generation model: there are no gensets to remove")
        displaced = not_applicable("no generation model")
    elif isinstance(policy.derivation, StandbyDerivation):
        without = not_applicable(
            "the floor's duration derives from the standby plant; with the gensets removed it "
            "cannot be sized, so the counterfactual is not computed"
        )
        displaced = not_applicable("the counterfactual is not computed")
        a = ask(
            "State the autonomy duration the resilience reserve must provide without the standby "
            "plant (seconds), to compute the requirement with the gensets removed",
            site_id=site,
        )
        asks[a.ask_id] = a
    else:
        without = _arm_from(without_rows, flat_floor_j, floor_kwh_flat, q)
        displaced = without.required_kwh - with_arm.required_kwh
    return RequiredComputed(
        kind="computed",
        floor_kwh=floor_kwh_flat,
        with_gensets=with_arm,
        without_gensets=without,
        genset_displaced_kwh=displaced,
    )
