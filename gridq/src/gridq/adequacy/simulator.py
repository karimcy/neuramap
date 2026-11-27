"""``adequacy_simulator`` (WP-SITE-04, spec A3-01): `run_adequacy`.

Every scenario of an IC-2 set is run chronologically against every load draw (the full cross
product, path weight ``w_s / N``), with the battery's state of energy carried through the whole
scenario and recharge simulated, at one minute (or one second inside event windows). Thermal flex
comes only through IC-11 (`gridq.adequacy.thermal`). Two postures (D174):

- ``reserve_held``: no trading; the battery is recharged toward its held level. Always run.
- ``envelope_adversary``: with an IC-3 envelope, the worst dispatch the envelope allows: the full
  merchant kW is traded every step outside events (down to floor + energy held for services) and
  stops only when an event's notice arrives. This bounds every envelope-compliant dispatch.

The resilience floor binds both; a shortfall is unserved energy, never a draw on the floor. A run
whose inputs are not all there returns `NotComputed` with its data asks; a contract error (an
export set, a site mismatch, too few draws) raises.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from fractions import Fraction
from math import lcm
from typing import TYPE_CHECKING, Literal, overload

import numpy as np

from gridq.adequacy._common import J_PER_KWH, ExactRational, ask, combine_record_class
from gridq.adequacy.estimators import (
    PPM,
    bootstrap_mean_interval,
    clopper_pearson_ppm,
)
from gridq.adequacy.floor import (
    Approved,
    FloorPartition,
    ResilienceFloorPolicy,
    Sized,
    Unsigned,
    admit_floor_signoff,
    approval_label,
)
from gridq.adequacy.inputs import (
    SIMULATION_FIELDS,
    BessParameters,
    OneWay,
    RoundTripOnly,
    efficiency_value,
    missing_bess_asks,
)
from gridq.adequacy.kernel import (
    GAP_CENSORED,
    GAP_FEASIBLE,
    GAP_INFEASIBLE,
    KernelInputs,
    ScenarioOutcome,
    Trace,
    run_kernel,
)
from gridq.adequacy.load import (
    MINUTES_PER_DAY,
    LoadMapping,
    LoadSource,
    local_start_date,
    map_scenario_minutes,
    resolve_anchor,
)
from gridq.adequacy.result import (
    INTERVAL_SCOPE,
    KERNEL_VERSION,
    WARNINGS,
    AdequacyConfig,
    AdequacyResult,
    Computed,
    Conformance,
    Eue,
    EventLolp,
    Finding,
    InputBinding,
    Interval,
    Lolp,
    Margin,
    MarginAt,
    NotComputed,
    PostureRun,
    Recharge,
    ScenarioMargin,
    StatedImportCapacity,
    WorstGap,
    simulation_hash_of,
)
from gridq.adequacy.thermal import FlexCall, flex_arrays, plan_flex
from gridq.contracts._base import NotApplicable, not_applicable
from gridq.contracts.curtailment import CurtailmentScenarioSet, RestrictionSeries, Scenario
from gridq.contracts.dispatch_envelope import DispatchEnvelope, Withheld, terms_at
from gridq.contracts.evidence import EvidenceObject
from gridq.contracts.thermal_flex import ThermalFlexAvailability
from gridq.datacentre.synthesiser import minimum_draws, order_statistic_interval
from gridq.ops.dossier_model import AskHolder, DossierAsk

if TYPE_CHECKING:
    from gridq.adequacy.onsite import OnsiteGeneration
    from gridq.adequacy.result_v1_1 import AdequacyResultV1_1
    from gridq.adequacy.standby import GenerationArrays, ScenarioGeneration

__all__ = ["render_summary", "run_adequacy", "simulate_scenario"]

Posture = Literal["reserve_held", "envelope_adversary"]
Arm = Literal["off", "ic11"]
#: Exact-integer headroom: efficiency denominators above this could overflow int64 products.
_MAX_DEN = 10**6


class _Missing(Exception):
    def __init__(self, reason: str, asks: Iterable[DossierAsk]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.asks = tuple(asks)


def _mw_to_w(mw: float) -> int:
    return int(np.floor(mw * 1e6 + 0.5))


def _eta(e: OneWay | RoundTripOnly) -> tuple[int, int]:
    f = efficiency_value(e)
    if f.denominator > _MAX_DEN:
        raise ValueError(f"efficiency denominators are limited to {_MAX_DEN}")
    return f.numerator, f.denominator


def _cells_for_minutes(
    partition: FloorPartition, horizon_start: date, idx: np.ndarray
) -> np.ndarray:
    band_of = partition.band_index_by_minute()
    n_g, n_b = len(partition.day_groups), len(partition.bands)
    days = idx // MINUTES_PER_DAY
    out = np.empty(idx.shape[0], dtype=np.int64)
    for d in np.unique(days):
        day = horizon_start + timedelta(days=int(d))
        si = next(i for i, s in enumerate(partition.seasons) if day.month in s.months)
        gi = next(
            i for i, g in enumerate(partition.day_groups) if day.isoweekday() in g.iso_weekdays
        )
        sel = days == d
        out[sel] = (si * n_g + gi) * n_b + band_of[idx[sel] % MINUTES_PER_DAY]
    return out


def _depth_w(s: Scenario) -> np.ndarray:
    if isinstance(s.minute, RestrictionSeries):
        vals = s.minute.dense()
        return np.asarray([_mw_to_w(v) for v in vals], dtype=np.int64)
    hh = np.asarray([_mw_to_w(v) for v in s.half_hourly.dense()], dtype=np.int64)
    return np.repeat(hh, 30)


class _Prepared:
    """Everything one scenario needs that does not depend on the posture."""

    def __init__(
        self,
        scenario: Scenario,
        mapping: LoadMapping,
        shift: timedelta,
        fac: np.ndarray,
        it: np.ndarray,
        cells: np.ndarray,
        permit_w: np.ndarray,
        restricted: np.ndarray,
        idx: np.ndarray | None = None,
    ) -> None:
        self.scenario = scenario
        #: The load-horizon minute each scenario minute reads (for co-located output series).
        self.idx = idx
        self.mapping = mapping
        self.shift = shift
        self.fac = fac
        self.it = it
        self.cells = cells
        self.permit_w = permit_w
        self.restricted = restricted


def _prepare(
    s: Scenario,
    load: LoadSource,
    policy: ResilienceFloorPolicy,
    cfg: AdequacyConfig,
    p_ref_w: int,
) -> _Prepared:
    ident = load.identity
    horizon = (ident.horizon_start, ident.horizon_end)
    found = resolve_anchor(s.scenario_id, s.period_start, cfg.anchors, horizon)
    site = policy.site_id
    if found is None:
        raise _Missing(
            f"scenario {s.scenario_id}: no load date maps to its local start",
            [
                ask(
                    f"State a load anchor date for scenario {s.scenario_id} (its month and day "
                    "are not in the load horizon), or supply a load horizon that covers it",
                    site_id=site,
                )
            ],
        )
    anchor, basis = found
    local0 = local_start_date(s.period_start)
    shift_days = (anchor - local0).days
    m = s.half_hourly.n_intervals * 30
    idx, skipped, repeated = map_scenario_minutes(
        s.period_start, m, horizon_start=ident.horizon_start, shift_days=shift_days
    )
    total = (ident.horizon_end - ident.horizon_start).days * MINUTES_PER_DAY
    if idx.min() < 0 or idx.max() >= total:
        raise _Missing(
            f"scenario {s.scenario_id}: load_horizon_short",
            [
                ask(
                    f"Supply load covering scenario {s.scenario_id} mapped from {anchor} "
                    f"({m} minutes); the load horizon is {horizon[0]} to {horizon[1]}",
                    site_id=site,
                )
            ],
        )
    lo, hi = int(idx.min()), int(idx.max()) + 1
    fac_all, it_all = load.minutes(lo, hi)
    fac, it = fac_all[:, idx - lo], it_all[:, idx - lo]
    cells = _cells_for_minutes(policy.partition, ident.horizon_start, idx)
    depth = _depth_w(s)
    permit = np.maximum(p_ref_w - depth, 0)
    mapping = LoadMapping(
        scenario_id=s.scenario_id,
        scenario_local_start=local0,
        load_anchor_date=anchor,
        anchor_basis=basis,
        dst_skipped_minutes=skipped,
        dst_repeated_minutes=repeated,
    )
    return _Prepared(s, mapping, timedelta(days=shift_days), fac, it, cells, permit, depth > 0, idx)


def _plan_calls(surface: ThermalFlexAvailability, p: _Prepared) -> tuple[FlexCall, ...]:
    calls: list[FlexCall] = []
    recovery = 0
    for i, e in enumerate(p.scenario.events):
        c = plan_flex(
            surface,
            e,
            event_index=i,
            period_start=p.scenario.period_start,
            shift=p.shift,
            recovery_until_minute=recovery,
        )
        if c.outcome == "called":
            recovery = c.recovery_end_minute
        calls.append(c)
    return tuple(calls)


def _fine(s: Scenario, m: int, resolution_s: int) -> np.ndarray:
    fine = np.zeros(m, dtype=bool)
    if resolution_s == 1:
        for e in s.events:
            a = (e.start - s.period_start).total_seconds()
            b = (e.end - s.period_start).total_seconds()
            fine[int(a // 60) : min(m, -int(-b // 60))] = True
    return fine


def net_after_flex(p: _Prepared, calls: tuple[FlexCall, ...]) -> np.ndarray:
    """Facility load less delivered thermal flex plus rebound, integer W ``(N, M)`` (D170)."""
    m = p.fac.shape[1]
    flex_w, rebound_w = flex_arrays(calls, m)
    cooling = np.maximum(p.fac - p.it, 0)
    delivered = np.minimum(flex_w[None, :], cooling)
    net: np.ndarray = p.fac - delivered + rebound_w[None, :]
    return net


def floor_terms(p: _Prepared, policy: ResilienceFloorPolicy) -> tuple[np.ndarray, np.ndarray]:
    """Per scenario minute: the policy's floor (kWh) and resilience reservation (kW)."""
    assert isinstance(policy.state, Sized)
    floor_kwh = np.asarray(
        [policy.floor_kwh_for_cell(int(c)) for c in range(len(policy.partition.cell_ids))],
        dtype=np.int64,
    )[p.cells]
    res_kw = np.asarray(
        [policy.resilience_kw_for_cell(int(c)) for c in range(len(policy.partition.cell_ids))],
        dtype=np.int64,
    )[p.cells]
    return floor_kwh, res_kw


def simulate_scenario(
    p: _Prepared,
    *,
    posture: Posture,
    calls: tuple[FlexCall, ...],
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    envelope: DispatchEnvelope | None,
    cfg: AdequacyConfig,
    skip: bool = True,
    trace: Trace | None = None,
    generation: GenerationArrays | None = None,
) -> ScenarioOutcome:
    """Build the per-minute terms for one scenario and posture, and run the kernel.

    ``generation`` (WP-SITE-05) folds on-site output into the net load before the kernel sees
    it (`gridq.adequacy.standby.GenerationArrays.apply`); without it the terms are exactly the
    WP-SITE-04 terms."""
    s = p.scenario
    n, m = p.fac.shape
    flex_w, rebound_w = flex_arrays(calls, m)
    net = net_after_flex(p, calls)
    if generation is not None:
        net = generation.apply(net, p.permit_w)
    floor_kwh, res_kw = floor_terms(p, policy)
    assert isinstance(bess.discharge_rating_kw, int) and isinstance(bess.charge_rating_kw, int)
    assert isinstance(bess.operating_soe_kwh, int) and isinstance(bess.initial_soe_kwh, int)
    assert isinstance(bess.discharge_efficiency, OneWay | RoundTripOnly)
    assert isinstance(bess.charge_efficiency, OneWay | RoundTripOnly)
    floor_j = floor_kwh * J_PER_KWH
    starts = [(e.start - s.period_start).total_seconds() for e in s.events]
    ends = [(e.end - s.period_start).total_seconds() for e in s.events]
    charge_cap = np.full(m, bess.charge_rating_kw * 1000, dtype=np.int64)
    if not cfg.charge_during_restriction:
        charge_cap[p.restricted] = 0
    zeros = np.zeros(m, dtype=np.int64)
    if posture == "reserve_held":
        cover_cap = np.maximum(bess.discharge_rating_kw - res_kw, 0) * 1000
        cover_floor = floor_j
        merchant_w = zeros
        merchant_floor = zeros
        target = np.maximum(bess.operating_soe_kwh * J_PER_KWH, floor_j)
    else:
        assert envelope is not None
        terms = _envelope_minutes(envelope, s, m, p.shift)
        env_floor = np.asarray([t[0] for t in terms], dtype=np.int64)
        held_up = np.asarray([t[1] for t in terms], dtype=np.int64)
        env_res = np.asarray([t[2] for t in terms], dtype=np.int64)
        withheld = np.asarray([t[3] for t in terms], dtype=np.int64)
        merch = np.asarray([t[4] for t in terms], dtype=np.int64)
        ceiling = np.asarray([t[5] for t in terms], dtype=np.int64)
        floor_j = np.maximum(floor_kwh, env_floor) * J_PER_KWH
        cover_floor = floor_j + held_up * J_PER_KWH
        rating = min(bess.discharge_rating_kw, envelope.rated_discharge_kw)
        cover_cap = np.maximum(rating - np.maximum(res_kw, env_res) - withheld, 0) * 1000
        window = np.zeros(m, dtype=bool)
        for e, a, b in zip(s.events, starts, ends, strict=True):
            notice = e.notice_s if isinstance(e.notice_s, float) else 0.0
            window[max(0, int((a - notice) // 60)) : min(m, -int(-b // 60))] = True
        merchant_w = np.where(window, 0, merch * 1000)
        merchant_floor = cover_floor
        target = np.where(window, ceiling * J_PER_KWH, 0)
        charge_cap = np.where(window, charge_cap, 0)
    k = KernelInputs(
        net_w=net,
        permit_w=p.permit_w,
        restricted=p.restricted,
        cover_cap_w=cover_cap.astype(np.int64),
        floor_j=floor_j.astype(np.int64),
        cover_floor_j=cover_floor.astype(np.int64),
        merchant_w=merchant_w.astype(np.int64),
        merchant_floor_j=merchant_floor.astype(np.int64),
        charge_target_j=target.astype(np.int64),
        charge_cap_w=charge_cap.astype(np.int64),
        thermal_active=(flex_w > 0) | (rebound_w > 0),
        fine=_fine(s, m, cfg.resolution_s),
        eta_discharge=_eta(bess.discharge_efficiency),
        eta_charge=_eta(bess.charge_efficiency),
        soe0_j=bess.initial_soe_kwh * J_PER_KWH,
        event_start_s=tuple(starts),
        event_end_s=tuple(ends),
        recharge_metrics=posture == "reserve_held",
        unserved_threshold_w=cfg.unserved_threshold_w,
    )
    return run_kernel(k, skip=skip, trace=trace)


def _envelope_minutes(
    env: DispatchEnvelope, s: Scenario, m: int, shift: timedelta
) -> list[tuple[int, int, int, int, int, int]]:
    """Per minute: (resilience floor kWh, held-up kWh, resilience kW, withheld kW, merchant kW,
    ceiling kWh), read at the study instant on the envelope's 30-minute grid."""
    out: list[tuple[int, int, int, int, int, int]] = []
    for b in range(-(-m // 30)):
        t = terms_at(env, s.period_start + shift + timedelta(minutes=30 * b))
        e = t.energy
        held_up = sum(h.kwh for h in e.held_up)
        held_down = sum(h.kwh for h in e.held_down)
        wd = t.discharge.withheld.kw if isinstance(t.discharge.withheld, Withheld) else 0
        row = (
            e.resilience_floor_kwh,
            held_up,
            t.discharge.resilience_kw,
            wd,
            t.discharge.merchant_kw,
            e.total_kwh() - held_down - e.withheld_kwh,
        )
        out.extend([row] * 30)
    return out[:m]


# --------------------------------------------------------------------------- aggregation


def _er(f: Fraction | int) -> ExactRational:
    return ExactRational.of(f)


def _aggregate(
    outcomes: list[ScenarioOutcome],
    scenarios: tuple[Scenario, ...],
    *,
    posture: Posture,
    arm: Arm,
    cfg: AdequacyConfig,
) -> PostureRun:
    n = outcomes[0].any_unserved.shape[0]
    w = [sc.weight.as_fraction() for sc in scenarios]
    s_count = len(scenarios)
    alpha = Fraction(PPM - cfg.confidence_ppm, PPM)
    alpha_s = alpha / s_count

    def weighted_interval(ks: list[int]) -> Interval:
        lo = hi = Fraction(0)
        for wi, k in zip(w, ks, strict=True):
            a, b = clopper_pearson_ppm(k, n, alpha_s)
            lo += wi * Fraction(a, PPM)
            hi += wi * Fraction(b, PPM)
        return Interval(lo=_er(lo), hi=_er(hi))

    ks = [int(o.any_unserved.sum()) for o in outcomes]
    lolp_point = sum((wi * Fraction(k, n) for wi, k in zip(w, ks, strict=True)), Fraction(0))
    loss = sum(
        (wi * Fraction(int(o.loss_s.sum()), n) for wi, o in zip(w, outcomes, strict=True)),
        Fraction(0),
    )
    per_event: list[EventLolp] = []
    for sc, o in zip(scenarios, outcomes, strict=True):
        n_ev = len(sc.events)
        for e in range(n_ev):
            per_event.append(
                EventLolp(scenario_id=sc.scenario_id, event=e, k=int(o.event_any[e].sum()))
            )
        k_out = int(o.event_any[n_ev].sum())
        if k_out:
            per_event.append(EventLolp(scenario_id=sc.scenario_id, event="outside_event", k=k_out))
    lolp = Lolp(
        point=_er(lolp_point),
        interval=weighted_interval(ks),
        expected_loss_seconds=_er(loss),
        per_event=tuple(per_event),
    )
    # EUE: per-draw totals D_d = sum_s w_s E_sd, held as integers over the common denominator.
    den = lcm(*(wi.denominator for wi in w))
    d_scaled = [
        sum(int(wi * den) * int(o.unserved_j[d]) for wi, o in zip(w, outcomes, strict=True))
        for d in range(n)
    ]
    expected = Fraction(sum(d_scaled), den * n)
    period_s = [sc.half_hourly.n_intervals * 1800 for sc in scenarios]
    if all(ps >= 365 * 86400 for ps in period_s):
        per_year: ExactRational | NotApplicable = _er(
            sum(
                (
                    wi * Fraction(int(o.unserved_j.sum()), n) * Fraction(365 * 86400, ps)
                    for wi, o, ps in zip(w, outcomes, period_s, strict=True)
                ),
                Fraction(0),
            )
        )
    else:
        per_year = not_applicable("a scenario period is shorter than a year")
    ordered = sorted(d_scaled)

    def q_higher(q: Fraction) -> Fraction:
        i = -(-(q * (n - 1)).numerator // (q * (n - 1)).denominator)
        return Fraction(ordered[i], den)

    ranks = order_statistic_interval(n, 0.95, cfg.confidence_ppm / PPM)
    b_lo, b_hi = bootstrap_mean_interval(d_scaled, cfg.seed)
    eue = Eue(
        expected_ws=_er(expected),
        per_year_ws=per_year,
        p50_ws=_er(q_higher(Fraction(1, 2))),
        p95_ws=_er(q_higher(Fraction(19, 20))),
        p95_rank_interval=ranks
        if ranks is not None
        else not_applicable("too few draws to bracket P95"),
        bootstrap=Interval(lo=_er(Fraction(b_lo, den * n)), hi=_er(Fraction(b_hi, den * n))),
    )
    # Margin.
    best: tuple[int, int, int, int] | None = None  # (margin, scenario, draw, offset)
    per_s: list[ScenarioMargin] = []
    for si, (sc, o) in enumerate(zip(scenarios, outcomes, strict=True)):
        d = int(np.lexsort((o.margin_at_s, o.margin_j))[0])
        cand = (int(o.margin_j[d]), si, d, int(o.margin_at_s[d]))
        per_s.append(ScenarioMargin(scenario_id=sc.scenario_id, min_margin_j=cand[0]))
        if best is None or cand < best:
            best = cand
    assert best is not None
    margin = Margin(
        min_margin_j=best[0],
        attained_at=MarginAt(
            scenario_id=scenarios[best[1]].scenario_id, draw=best[2], offset_s=best[3]
        ),
        per_scenario=tuple(per_s),
    )
    recharge: Recharge | NotApplicable
    if posture == "reserve_held":
        recharge = _recharge(outcomes, scenarios, w, n, alpha_s)
    else:
        recharge = not_applicable("recharge is not assessed under the envelope adversary")
    breaches = sum(o.floor_breaches for o in outcomes)
    if breaches:
        from gridq.adequacy.kernel import FloorBreach

        raise FloorBreach(f"{breaches} step(s) discharged below the resilience floor")
    return PostureRun(
        posture=posture,
        thermal=arm,
        bound="no_trading" if posture == "reserve_held" else "every_envelope_compliant_dispatch",
        lolp=lolp,
        eue=eue,
        margin=margin,
        recharge=recharge,
        paths_digests=tuple(
            (sc.scenario_id, o.paths_digest) for sc, o in zip(scenarios, outcomes, strict=True)
        ),
        floor_breaches=0,
    )


def _recharge(
    outcomes: list[ScenarioOutcome],
    scenarios: tuple[Scenario, ...],
    w: list[Fraction],
    n: int,
    alpha_s: Fraction,
) -> Recharge:
    gaps = k_inf = k_cen = 0
    min_margin: int | None = None
    max_short = 0
    any_inf: list[int] = []
    worst: tuple[int, int, int] | None = None  # (-k_infeasible, scenario, gap)
    for si, o in enumerate(outcomes):
        st = o.gap_status
        gaps += st.shape[0]
        k_inf += int((st == GAP_INFEASIBLE).sum())
        k_cen += int((st == GAP_CENSORED).sum())
        feas = st == GAP_FEASIBLE
        if feas.any():
            mm = int(o.gap_margin_s[feas].min())
            min_margin = mm if min_margin is None else min(min_margin, mm)
        if st.size:
            max_short = max(max_short, int(o.gap_shortfall_j.max()))
            any_inf.append(int((st == GAP_INFEASIBLE).any(axis=0).sum()))
            for g in range(st.shape[0]):
                kg = int((st[g] == GAP_INFEASIBLE).sum())
                cand = (-kg, si, g)
                if kg and (worst is None or cand < worst):
                    worst = cand
        else:
            any_inf.append(0)
    p_any = sum((wi * Fraction(k, n) for wi, k in zip(w, any_inf, strict=True)), Fraction(0))
    lo = hi = Fraction(0)
    for wi, k in zip(w, any_inf, strict=True):
        a, b = clopper_pearson_ppm(k, n, alpha_s)
        lo += wi * Fraction(a, PPM)
        hi += wi * Fraction(b, PPM)
    return Recharge(
        gaps=gaps,
        k_infeasible=k_inf,
        k_censored=k_cen,
        min_margin_s=min_margin
        if min_margin is not None
        else not_applicable("no feasible gap was observed"),
        max_shortfall_wh=-(-max_short // 3600),
        p_any_infeasible_gap=_er(p_any),
        p_any_infeasible_gap_interval=Interval(lo=_er(lo), hi=_er(hi)),
        worst_gap=WorstGap(
            scenario_id=scenarios[worst[1]].scenario_id, gap_index=worst[2], k_infeasible=-worst[0]
        )
        if worst is not None
        else not_applicable("no infeasible gap"),
    )


# --------------------------------------------------------------------------- conformance


def _conformance(
    env: DispatchEnvelope, policy: ResilienceFloorPolicy, prepared: list[_Prepared]
) -> Conformance:
    assert isinstance(policy.state, Sized)
    findings: list[Finding] = []
    floor_low: tuple[str, int, int] | None = None
    kw_low: tuple[str, int, int] | None = None
    for p in prepared:
        m = p.fac.shape[1]
        terms = _envelope_minutes(env, p.scenario, m, p.shift)
        for minute in range(0, m, 30):
            c = int(p.cells[minute])
            pol_floor = policy.floor_kwh_for_cell(c)
            pol_kw = policy.resilience_kw_for_cell(c)
            ef, _, ekw, *_ = terms[minute]
            where = f"{(p.scenario.period_start + p.shift + timedelta(minutes=minute)).isoformat()}"
            if ef < pol_floor and floor_low is None:
                floor_low = (where, ef, pol_floor)
            if ekw < pol_kw and kw_low is None:
                kw_low = (where, ekw, pol_kw)
    if floor_low:
        findings.append(
            Finding(
                code="ENVELOPE_FLOOR_BELOW_POLICY",
                detail=f"at {floor_low[0]} the envelope's resilience floor is {floor_low[1]} kWh; "
                f"the policy's is {floor_low[2]} kWh",
            )
        )
    if kw_low:
        findings.append(
            Finding(
                code="ENVELOPE_RESILIENCE_KW_BELOW_PROTECTED_LOAD",
                detail=f"at {kw_low[0]} the envelope reserves {kw_low[1]} kW for resilience; the "
                f"protected load needs {kw_low[2]} kW",
            )
        )
    if env.fail_safe.resilience_floor_kwh < policy.state.fail_safe_floor_kwh:
        findings.append(
            Finding(
                code="ENVELOPE_FAIL_SAFE_FLOOR_BELOW_POLICY",
                detail=f"the envelope's fail-safe floor is {env.fail_safe.resilience_floor_kwh} "
                f"kWh; the policy's lowest cell floor is {policy.state.fail_safe_floor_kwh} kWh",
            )
        )
    return Conformance(
        kind="conformance",
        status="nonconforming" if findings else "conforming",
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- entry point


@overload
def run_adequacy(
    scenario_set: CurtailmentScenarioSet,
    load: LoadSource,
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    cfg: AdequacyConfig,
    *,
    as_of: date,
    evidence: Iterable[EvidenceObject] = (),
    thermal: ThermalFlexAvailability | None = None,
    envelope: DispatchEnvelope | None = None,
    skip: bool = True,
    generation: None = None,
) -> AdequacyResult: ...


@overload
def run_adequacy(
    scenario_set: CurtailmentScenarioSet,
    load: LoadSource,
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    cfg: AdequacyConfig,
    *,
    as_of: date,
    evidence: Iterable[EvidenceObject] = (),
    thermal: ThermalFlexAvailability | None = None,
    envelope: DispatchEnvelope | None = None,
    skip: bool = True,
    generation: OnsiteGeneration,
) -> AdequacyResultV1_1: ...


def run_adequacy(
    scenario_set: CurtailmentScenarioSet,
    load: LoadSource,
    policy: ResilienceFloorPolicy,
    bess: BessParameters,
    cfg: AdequacyConfig,
    *,
    as_of: date,
    evidence: Iterable[EvidenceObject] = (),
    thermal: ThermalFlexAvailability | None = None,
    envelope: DispatchEnvelope | None = None,
    skip: bool = True,
    generation: OnsiteGeneration | None = None,
) -> AdequacyResult | AdequacyResultV1_1:
    """Run the adequacy simulation and attach the floor's sign-off outcome. Pure: reads no clock.

    Without ``generation`` the record is ``gridq.adequacy_result`` 1.0.0, exactly as WP-SITE-04
    made it. With an `OnsiteGeneration` (WP-SITE-05) it is 1.1.0: gensets and co-located output
    are planned per scenario and arm (`gridq.adequacy.standby`), folded into the net load, and the
    same inputs are re-run without them for the record's counterfactual."""
    site = policy.site_id
    ident = load.identity
    if scenario_set.direction != "import":
        raise ValueError("the adequacy simulator models import restrictions; this set is export")
    for name, other in (
        ("scenario set", scenario_set.site_id),
        ("load", ident.site_id),
        ("BESS parameters", bess.site_id),
        *((("thermal surface", thermal.site_id),) if thermal else ()),
        *((("dispatch envelope", envelope.site_id),) if envelope else ()),
        *((("generation model", generation.model.site_id),) if generation else ()),
    ):
        if other != site:
            raise ValueError(f"the {name} names site {other}; the floor policy is for {site}")
    if bess.content_hash() != policy.bess_parameters_hash:
        raise ValueError("the BESS parameters are not the ones the floor policy was sized from")
    if not isinstance(policy.load, NotApplicable) and policy.load != ident:
        raise ValueError("the load is not the one the floor policy was sized from")
    if ident.draws < minimum_draws():
        raise ValueError(f"at least {minimum_draws()} load draws are required")

    record_class = combine_record_class(
        policy.record_class,
        bess.record_class,
        ident.record_class,
        "synthetic_fixture" if scenario_set.evidence_class.value == "synthetic_fixture" else "",
        thermal.record_class if thermal else "",
        envelope.record_class.value if envelope else "",
        generation.model.record_class if generation else "",
    )
    asks: dict[str, DossierAsk] = {a.ask_id: a for a in policy.data_requests}
    if generation is not None:
        from gridq.adequacy.onsite import missing_onsite_asks

        asks.update({a.ask_id: a for _, a in missing_onsite_asks(generation.model)})
    plans: dict[tuple[Arm, str], tuple[GenerationArrays, ScenarioGeneration]] = {}
    reasons: list[str] = []
    if not isinstance(policy.state, Sized):
        reasons.append(f"the resilience floor is {policy.state.kind}")
    for name, a in missing_bess_asks(bess, SIMULATION_FIELDS):
        reasons.append(f"BESS {name} is unknown")
        asks[a.ask_id] = a
    if isinstance(scenario_set.reference_capacity_mw, float):
        p_ref_w: int | None = _mw_to_w(scenario_set.reference_capacity_mw)
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

    prepared: list[_Prepared] = []
    if not reasons:
        assert p_ref_w is not None
        try:
            for sc in scenario_set.scenarios:
                prepared.append(_prepare(sc, load, policy, cfg, p_ref_w))
            if envelope is not None:
                for p in prepared:
                    _envelope_minutes(envelope, p.scenario, p.fac.shape[1], p.shift)
        except _Missing as missing:
            reasons.append(missing.reason)
            asks.update({a.ask_id: a for a in missing.asks})
        except ValueError as err:
            if envelope is None or "valid" not in str(err):
                raise
            reasons.append("the dispatch envelope does not cover the study period")
            a = ask(
                "Supply a dispatch envelope (IC-3) valid over the study period of every scenario",
                site_id=site,
                holder=AskHolder.HOST,
            )
            asks[a.ask_id] = a

    approval = admit_floor_signoff(policy, evidence, as_of=as_of)
    conformance: Conformance | NotApplicable = not_applicable("no dispatch envelope was supplied")
    outcome: Computed | NotComputed
    inputs: InputBinding | NotApplicable
    if reasons:
        outcome = NotComputed(kind="not_computed", reasons=tuple(reasons))
        inputs = not_applicable("not computed")
    else:
        if envelope is not None:
            conformance = _conformance(envelope, policy, prepared)
        arms: tuple[Arm, ...] = ("off", "ic11") if thermal is not None else ("off",)
        postures: tuple[Posture, ...] = (
            ("reserve_held", "envelope_adversary") if envelope is not None else ("reserve_held",)
        )
        calls = {
            p.scenario.scenario_id: _plan_calls(thermal, p) if thermal is not None else ()
            for p in prepared
        }
        if generation is not None:
            from gridq.adequacy.standby import plan_generation

            for arm in arms:
                for p in prepared:
                    sid = p.scenario.scenario_id
                    base = net_after_flex(p, calls[sid] if arm == "ic11" else ())
                    arrays, plan, plan_asks = plan_generation(
                        generation, p, base, horizon_start=ident.horizon_start, thermal=arm
                    )
                    plans[(arm, sid)] = (arrays, plan)
                    asks.update({a.ask_id: a for a in plan_asks})
        runs: list[PostureRun] = []
        for posture in postures:
            for arm in arms:
                outs = [
                    simulate_scenario(
                        p,
                        posture=posture,
                        calls=calls[p.scenario.scenario_id] if arm == "ic11" else (),
                        policy=policy,
                        bess=bess,
                        envelope=envelope,
                        cfg=cfg,
                        skip=skip,
                        generation=plans[(arm, p.scenario.scenario_id)][0]
                        if generation is not None
                        else None,
                    )
                    for p in prepared
                ]
                runs.append(
                    _aggregate(outs, scenario_set.scenarios, posture=posture, arm=arm, cfg=cfg)
                )
        outcome = Computed(
            kind="computed",
            runs=tuple(runs),
            headline=(postures[-1], arms[-1]),
            mappings=tuple(p.mapping for p in prepared),
            flex_calls=tuple((k, v) for k, v in calls.items() if v),
            confidence_ppm=cfg.confidence_ppm,
            interval_scope=INTERVAL_SCOPE,
            warnings=WARNINGS,
        )
        inputs = InputBinding(
            scenario_set_hash=scenario_set.content_hash(),
            thermal_flex_hash=thermal.content_hash()
            if thermal
            else not_applicable("no thermal surface"),
            envelope_hash=envelope.content_hash()
            if envelope
            else not_applicable("no dispatch envelope"),
            load=ident,
            floor_policy_hash=policy.content_hash(),
            bess_parameters_hash=bess.content_hash(),
            config_hash=_config_hash(cfg),
            seed=cfg.seed,
            draws=ident.draws,
            resolution_s=cfg.resolution_s,
            kernel_version=KERNEL_VERSION,
        )
    fields: dict[str, object] = {
        "schema_name": "gridq.adequacy_result",
        "schema_version": "1.0.0",
        "method_version": "gridq.adequacy/1.0.0",
        "site_id": site,
        "record_class": record_class,
        "inputs": inputs,
        "config": cfg,
        "policy": policy,
        "conformance": conformance,
        "outcome": outcome,
        "data_requests": tuple(asks[k] for k in sorted(asks)),
    }
    if generation is not None:
        return _with_generation(
            fields,
            generation,
            tuple(plan for _, plan in plans.values()),
            approval,
            as_of,
            rerun=lambda: run_adequacy(
                scenario_set,
                load,
                policy,
                bess,
                cfg,
                as_of=as_of,
                evidence=evidence,
                thermal=thermal,
                envelope=envelope,
                skip=skip,
            ),
        )
    sim_hash = simulation_hash_of(fields)
    approved_for_use = (
        approval.kind == "approved"
        and isinstance(outcome, Computed)
        and not (isinstance(conformance, Conformance) and conformance.status == "nonconforming")
    )
    return AdequacyResult(
        **fields,  # type: ignore[arg-type]
        simulation_hash=sim_hash,
        as_of=as_of,
        approval=approval,
        approval_label=approval_label(approval),
        approved_for_use=approved_for_use,
    )


def _with_generation(
    fields: dict[str, object],
    generation: OnsiteGeneration,
    plans: tuple[ScenarioGeneration, ...],
    approval: Approved | Unsigned,
    as_of: date,
    rerun: Callable[[], AdequacyResult],
) -> AdequacyResultV1_1:
    """Assemble the 1.1.0 record (D-NEW-SITE05-4): the counterfactual is the 1.0.0 run, re-run."""
    from gridq.adequacy.result_v1_1 import (
        METHOD_VERSION_V1_1,
        AdequacyResultV1_1,
        GenerationSection,
        InputBindingV1_1,
        WithoutGeneration,
        simulation_hash_v1_1,
    )
    from gridq.adequacy.standby import EXCLUSIONS, generation_credit

    outcome = fields["outcome"]
    gh = generation.content_hash()
    without: WithoutGeneration | NotApplicable
    if isinstance(outcome, Computed):
        base = rerun()
        assert isinstance(base.outcome, Computed)
        head = next(r for r in base.outcome.runs if (r.posture, r.thermal) == base.outcome.headline)
        without = WithoutGeneration(
            kind="without_generation",
            simulation_hash=base.simulation_hash,
            headline_lolp=head.lolp.point,
            headline_eue_ws=head.eue.expected_ws,
        )
    else:
        without = not_applicable("not computed")
    status: Literal["conforming", "nonconforming"] = (
        "nonconforming"
        if any(f.severity == "nonconforming" for p in plans for f in p.findings)
        else "conforming"
    )
    section = GenerationSection(
        generation_hash=gh,
        model=generation.model,
        credit=generation_credit(plans, generation),
        status=status,
        plans=plans,
        exclusions=EXCLUSIONS,
        without_generation=without,
    )
    inputs = fields["inputs"]
    v11 = dict(fields)
    v11["schema_version"] = "1.1.0"
    v11["method_version"] = METHOD_VERSION_V1_1
    v11["inputs"] = (
        InputBindingV1_1(
            **{k: getattr(inputs, k) for k in InputBinding.model_fields}, generation_hash=gh
        )
        if isinstance(inputs, InputBinding)
        else inputs
    )
    v11["generation"] = section
    sim_hash = simulation_hash_v1_1(v11)
    conformance = fields["conformance"]
    approved_for_use = (
        approval.kind == "approved"
        and isinstance(outcome, Computed)
        and not (isinstance(conformance, Conformance) and conformance.status == "nonconforming")
        and status == "conforming"
    )
    return AdequacyResultV1_1(
        **v11,  # type: ignore[arg-type]
        simulation_hash=sim_hash,
        as_of=as_of,
        approval=approval,
        approval_label=approval_label(approval),
        approved_for_use=approved_for_use,
    )


def _config_hash(cfg: AdequacyConfig) -> str:
    from gridq.contracts._base import content_hash

    return content_hash(cfg)


def render_summary(result: AdequacyResult) -> str:
    """A short human rendering. The first line is always the approval label."""
    lines = [result.approval_label]
    if not result.approved_for_use:
        lines.append("Not for use: see the approval line, the conformance and the data requests.")
    if isinstance(result.outcome, NotComputed):
        lines.append("No LOLP or EUE: " + "; ".join(result.outcome.reasons))
    else:
        for r in result.outcome.runs:
            lines.append(
                f"{r.posture}/{r.thermal}: LOLP {r.lolp.point.as_float:.6g} "
                f"[{r.lolp.interval.lo.as_float:.6g}, {r.lolp.interval.hi.as_float:.6g}], "
                f"EUE {r.eue.expected_ws.as_float / 3.6e6:.6g} kWh, "
                f"min SoE margin {r.margin.min_margin_j / 3.6e6:.6g} kWh"
            )
    if isinstance(result.approval, Unsigned):
        for a in result.approval.requests:
            lines.append(f"Missing: {a.request}")
    for a in result.data_requests:
        lines.append(f"Data request: {a.request}")
    return "\n".join(lines)
