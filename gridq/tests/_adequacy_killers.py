"""Checks that pass on the real ``gridq.adequacy`` and must fail on each seeded mutant (WP-SITE-04).

Each ``k_*`` takes the modules to exercise (the real ones by default, a mutant in
``test_adequacy_mutants.py``) and raises ``AssertionError`` when the floor is not honoured.
`independent_floor_check` re-derives the floor in force at every step from the policy itself, not
from anything the kernel used, so a kernel that reads the wrong floor is caught.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from fractions import Fraction
from types import ModuleType
from typing import Any

import numpy as np
from tests import _adequacy_support as A

from gridq.adequacy import floor as floor_mod
from gridq.adequacy import kernel as kernel_mod
from gridq.adequacy import simulator as sim_mod
from gridq.adequacy._common import J_PER_KWH
from gridq.adequacy.floor import FloorPartition, ResilienceFloorPolicy
from gridq.adequacy.kernel import Trace

AS_OF = date(2027, 1, 10)


def independent_floor_check(
    trace: Trace, policy: ResilienceFloorPolicy, partition: FloorPartition, day0: date
) -> None:
    """Every draw that discharged in a step ends it at or above the policy's floor for that
    minute, and no step lowers a draw that is below the floor. Raises AssertionError."""
    for t, before, after, out in zip(
        trace.t_s, trace.soe_before, trace.soe_after, trace.discharged_j, strict=True
    ):
        minute = t // 60
        day = day0 + timedelta(days=minute // 1440)
        cell = partition.cell_index(day.month, day.isoweekday(), minute % 1440)
        f = policy.floor_kwh_for_cell(cell) * J_PER_KWH
        assert bool((after[out > 0] >= f).all()), f"t={t}: discharged below the floor {f} J"
        assert bool(((after >= f) | (after >= before)).all()), f"t={t}: lowered below the floor"


def run_traced(
    ss: Any,
    load: Any,
    policy: ResilienceFloorPolicy,
    bess: Any,
    *,
    posture: str = "reserve_held",
    thermal: bool = False,
    resolution_s: int = 60,
    sim: ModuleType = sim_mod,
    skip: bool = True,
) -> list[tuple[Trace, Any]]:
    """Run every scenario of ``ss`` through ``sim``'s kernel wiring with a trace."""
    cfg = A.config(resolution_s=resolution_s)
    env = A.envelope() if posture == "envelope_adversary" else None
    surface = A.surface() if thermal else None
    out = []
    for sc in ss.scenarios:
        p = sim._prepare(sc, load, policy, cfg, 1_000_000)
        calls = sim._plan_calls(surface, p) if surface is not None else ()
        tr = Trace()
        o = sim.simulate_scenario(
            p,
            posture=posture,
            calls=calls,
            policy=policy,
            bess=bess,
            envelope=env,
            cfg=cfg,
            skip=skip,
            trace=tr,
        )
        out.append((tr, o))
    return out


def _drain_case(initial: int = 600) -> tuple[Any, Any, ResilienceFloorPolicy, Any]:
    load = A.minute_load(draws=4, days=2)
    b = A.bess(initial=initial, operating=initial)
    policy = floor_mod.size_floor(A.requirement(kw=200), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("drain", Fraction(1), [(60, 360, 1.0, 0.0)])])
    return ss, load, policy, b


def k_drain(sim: ModuleType = sim_mod, resolution_s: int = 60) -> None:
    """A six-hour total restriction from 600 kWh: the battery stops at the floor, never below."""
    ss, load, policy, b = _drain_case()
    if resolution_s == 1:
        ss = A.scenario_set([("drain", Fraction(1), [(60, 45, 1.0, 0.0)])])
        b = A.bess(initial=260, operating=260)
        policy = floor_mod.size_floor(A.requirement(kw=200), b, A.flat_partition(), load=load)
    for tr, o in run_traced(ss, load, policy, b, sim=sim, resolution_s=resolution_s):
        independent_floor_check(tr, policy, A.flat_partition(), A.DAY0)
        assert o.floor_breaches == 0
        assert int(o.margin_j.min()) >= 0
        assert bool(o.any_unserved.all()), "the drain case must exhaust the energy above the floor"


def k_dispatch_unit(kernel: ModuleType = kernel_mod) -> None:
    """The choke point honours the floor whatever the purpose floor says."""
    soe = np.asarray([500, 250, 100], dtype=np.int64)
    req = np.asarray([1000, 1000, 1000], dtype=np.int64)
    for purpose in kernel.Purpose:
        got, new = kernel._dispatch(soe, req, purpose, 300, 0)
        assert new.tolist() == [300, 250, 100], (purpose, new.tolist())
        assert got.tolist() == [200, 0, 0]
        got, new = kernel._dispatch(soe, req, purpose, 300, 400)
        assert new.tolist() == [400, 250, 100]


def k_adversary_floor(sim: ModuleType = sim_mod) -> None:
    """Under the envelope adversary the policy floor (527 kWh) binds, not the envelope's 400."""
    load = A.minute_load(draws=4, days=2)
    b = A.bess(initial=1500, operating=1500)
    policy = floor_mod.size_floor(A.requirement(kw=500), b, A.flat_partition(), load=load)
    assert policy.state.flat_floor_kwh == 527  # type: ignore[union-attr]
    ss = A.scenario_set([("adv", Fraction(1), [(900, 120, 1.0, 0.0)])])
    for tr, o in run_traced(ss, load, policy, b, posture="envelope_adversary", sim=sim):
        independent_floor_check(tr, policy, A.flat_partition(), A.DAY0)
        assert int(o.margin_j.min()) >= 0


def k_unsigned(sim: ModuleType = sim_mod) -> None:
    """An unsigned floor is the same hard floor: same simulation, never below it."""
    load = A.minute_load(draws=72, days=2)
    b = A.bess(initial=600, operating=600)
    policy = floor_mod.size_floor(A.requirement(kw=200), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("drain", Fraction(1), [(60, 360, 1.0, 0.0)])])
    signed = A.signoff(floor_mod.floor_statement_hash(policy))  # type: ignore[arg-type]
    unsigned_run = sim.run_adequacy(ss, load, policy, b, A.config(), as_of=AS_OF)
    signed_run = sim.run_adequacy(ss, load, policy, b, A.config(), as_of=AS_OF, evidence=[signed])
    assert unsigned_run.approval.kind == "unsigned" and signed_run.approval.kind == "approved"
    assert unsigned_run.simulation_hash == signed_run.simulation_hash
    for r in (unsigned_run, signed_run):
        for run in r.outcome.runs:
            assert run.margin.min_margin_j >= 0


def step_up_case() -> tuple[Any, Any, ResilienceFloorPolicy, Any, int, int]:
    """IT load 100 kW at night and 900 kW from 08:00, ten-minute autonomy: the night cell floor
    is below the day cell floor. The battery starts between them; a total restriction starts at
    08:00 exactly. Returns (set, load, policy, bess, night floor, day floor)."""
    load = A.minute_load(draws=4, days=2, day_it_kw=900)
    req = A.requirement(fraction=Fraction(1), basis="it", seconds=600)
    probe = floor_mod.size_floor(req, A.bess(), A.banded_partition(), load=load)
    cells = probe.state.cells  # type: ignore[union-attr]
    night, day = cells[0].floor_kwh, cells[1].floor_kwh
    assert isinstance(night, int) and isinstance(day, int) and night + 2 < day
    b = A.bess(initial=day - 1, operating=0)
    policy = floor_mod.size_floor(req, b, A.banded_partition(), load=load)
    ss = A.scenario_set([("step", Fraction(1), [(480, 30, 1.0, 0.0)])])
    return ss, load, policy, b, night, day


def k_step_up(sim: ModuleType = sim_mod) -> None:
    """At the minute the floor steps up, the new floor binds: no discharge from below it."""
    ss, load, policy, b, _, _ = step_up_case()
    for tr, _o in run_traced(ss, load, policy, b, sim=sim):
        independent_floor_check(tr, policy, A.banded_partition(), A.DAY0)


def k_sizing(floor: ModuleType = floor_mod) -> None:
    """200 kW for an hour at 19/20 discharge efficiency: ceil(210.53) = 211 kWh."""
    load = A.minute_load(draws=4, days=2)
    policy = floor.size_floor(A.requirement(kw=200), A.bess(), A.flat_partition(), load=load)
    assert policy.state.flat_floor_kwh == 211


def k_merchant_event(sim: ModuleType = sim_mod) -> None:
    """With two hours' notice the adversary refills before the event and trades nothing inside
    it, so a 300 kW shortfall for two hours is fully served."""
    load = A.minute_load(draws=4, days=2)
    b = A.bess(initial=1500, operating=1500)
    policy = floor_mod.size_floor(A.requirement(kw=200), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("merchant", Fraction(1), [(600, 120, 0.5, 7200.0)])])
    for _tr, o in run_traced(ss, load, policy, b, posture="envelope_adversary", sim=sim):
        assert not bool(o.any_unserved.any()), "the adversary traded inside the event"


def assert_structural(source: str) -> None:
    tree = ast.parse(source)
    allowed = {"_dispatch", "_charge", "full"}
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assert node.target.id not in {"soe", "soe_j"}, "in-place change of the state of energy"
        if not isinstance(node, ast.Assign):
            continue
        names = {
            n.id
            for t in node.targets
            for n in ast.walk(t)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        if "soe" not in names:
            continue
        call = node.value
        assert isinstance(call, ast.Call), f"line {node.lineno}: soe set without a call"
        fn = call.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        assert name in allowed, f"line {node.lineno}: soe set by {name}"
