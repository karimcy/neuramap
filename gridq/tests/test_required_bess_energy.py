"""WP-SITE-05 acceptance 2: removing gensets from a fixture increases the required BESS energy.

- the strict fixture: an event that outlasts the genset's start time, so removing the genset
  strictly raises the requirement;
- a property over random SYNTHETIC fixtures: per path, the requirement without gensets is never
  below the requirement with them;
- equality: a genset slower to start than every event displaces nothing;
- the single pass is checked against the kernel itself at ``E*`` (only power-bound shortfall
  remains) and ``E* - 1`` (strictly more unserved).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

import numpy as np
import pytest
from tests import _adequacy_support as A
from tests import _onsite_support as O

from gridq.adequacy import simulator as sim
from gridq.adequacy.floor import size_floor
from gridq.adequacy.inputs import AutonomyRequirement, EnsembleMaximum, StatedQuantile
from gridq.adequacy.required import (
    RequiredComputed,
    RequiredNotComputed,
    path_requirements,
    required_bess_energy,
)
from gridq.adequacy.standby import plan_generation
from gridq.contracts._base import NotApplicable, Unknown

START = datetime(2027, 1, 12, tzinfo=UTC)
MAX = EnsembleMaximum(kind="ensemble_maximum")


def _inputs(
    events: list[tuple[int, int, float, Any]], *, seed: int = 7, draws: int = 72
) -> tuple[Any, Any, Any, Any]:
    load = A.minute_load(days=3, seed=seed, draws=draws)
    b = A.bess()
    policy = size_floor(A.requirement(kw=100, seconds=600), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("s1", Fraction(1), events)], start=START)
    return ss, load, policy, b


def _genset(start_s: int = 120, kw: int = 400) -> Any:
    return O.bundle(O.model([O.genset("G1", kw=kw, start_s=start_s)]))


def _required(events: list[tuple[int, int, float, Any]], gen: Any, **kw: Any) -> Any:
    ss, load, policy, b = _inputs(events)
    return required_bess_energy(
        ss, load, policy, b, A.config(), statistic=MAX, generation=gen, **kw
    )


def test_removing_the_genset_strictly_increases_the_required_energy() -> None:
    r = _required([(200, 240, 0.8, 600.0)], _genset(start_s=120))
    assert isinstance(r.outcome, RequiredComputed)
    o = r.outcome
    assert not isinstance(o.without_gensets, NotApplicable)
    assert o.without_gensets.required_kwh > o.with_gensets.required_kwh
    assert o.genset_displaced_kwh == o.without_gensets.required_kwh - o.with_gensets.required_kwh
    assert r.record_class == "synthetic_fixture" and r.site_id.startswith("SYNTHETIC-")


def test_a_genset_slower_than_every_event_displaces_nothing() -> None:
    # 30-minute events, a 40-minute start: the unit is never online while there is need.
    r = _required([(200, 30, 0.8, 0.0), (700, 30, 0.8, 0.0)], _genset(start_s=2400))
    assert isinstance(r.outcome, RequiredComputed)
    assert r.outcome.genset_displaced_kwh == 0


def test_without_gensets_is_never_below_with_them_per_path() -> None:
    rng = random.Random(5)
    for case in range(12):
        events = []
        t = rng.randint(0, 200)
        for _ in range(rng.randint(1, 3)):
            dur = rng.randint(20, 300)
            if t + dur >= 1440:
                break
            events.append((t, dur, rng.choice([0.5, 0.7, 0.9]), rng.choice([None, 0.0, 900.0])))
            t += dur + rng.randint(10, 300)
        units = [
            O.genset(f"G{i}", kw=rng.choice([100, 300, 600]), start_s=rng.choice([0, 60, 900]))
            for i in range(rng.randint(1, 3))
        ]
        ids = [u["unit_id"] for u in units]
        gen = O.bundle(
            O.model(
                units,
                limits=[O.limit("L1", ids, cap_s=rng.choice([0, 1800, 7200]))],
                contingency=rng.choice(
                    [{"kind": "all_start"}, {"kind": "largest_units_out", "k": 1}]
                ),
            )
        )
        ss, load, policy, b = _inputs(events, seed=case)
        p = sim._prepare(ss.scenarios[0], load, policy, A.config(), 1_000_000)
        base = sim.net_after_flex(p, ())
        arrays, _, _ = plan_generation(gen, p, base, horizon_start=load.identity.horizon_start)
        floor_kwh, res_kw = sim.floor_terms(p, policy)
        m = base.shape[1]
        args = (
            p.permit_w,
            np.maximum(1000 - res_kw, 0) * 1000,
            np.full(m, 1_000_000, dtype=np.int64),
            floor_kwh * 3_600_000,
            np.zeros(m, dtype=bool),
            (19, 20),
            (19, 20),
        )
        with_ = path_requirements(arrays.apply(base, p.permit_w), *args)
        without = path_requirements(arrays.without_gensets().apply(base, p.permit_w), *args)
        assert (without[0] >= with_[0]).all(), case
        assert (without[2] >= with_[2]).all(), case


def _kernel_unserved(events: list[tuple[int, int, float, Any]], gen: Any, kwh: int) -> np.ndarray:
    """Run the real kernel (reserve_held, no flex) with a battery of ``kwh`` usable, started and
    held full, and the same generation plan; return unserved joules per draw."""
    ss, load, _, _ = _inputs(events)
    b = A.bess(usable=kwh, initial=kwh, operating=kwh)
    policy = size_floor(A.requirement(kw=100, seconds=600), b, A.flat_partition(), load=load)
    p = sim._prepare(ss.scenarios[0], load, policy, A.config(), 1_000_000)
    base = sim.net_after_flex(p, ())
    arrays, _, _ = plan_generation(gen, p, base, horizon_start=load.identity.horizon_start)
    out = sim.simulate_scenario(
        p,
        posture="reserve_held",
        calls=(),
        policy=policy,
        bess=b,
        envelope=None,
        cfg=A.config(),
        generation=arrays,
    )
    unserved: np.ndarray = out.unserved_j
    return unserved


@pytest.mark.parametrize("arm", ["with", "without"])
def test_the_single_pass_matches_the_kernel_at_e_star_and_e_star_less_one(arm: str) -> None:
    events = [(200, 240, 0.85, 600.0), (900, 90, 0.95, None)]
    gen = _genset(start_s=300, kw=300)
    r = _required(events, gen)
    assert isinstance(r.outcome, RequiredComputed)
    req = r.outcome.with_gensets if arm == "with" else r.outcome.without_gensets
    assert not isinstance(req, NotApplicable)
    e_star = req.required_kwh
    kernel_gen = (
        gen
        if arm == "with"
        else O.bundle(
            O.model([O.genset("G1", kw=300, start_s=300)], limits=[O.limit("L0", ["G1"], cap_s=0)])
        )
    )
    at = _kernel_unserved(events, kernel_gen, e_star)
    assert int(at.max()) == req.power_bound.max_unserved_ws
    if e_star - 1 >= r.outcome.floor_kwh and e_star > 1:
        below = _kernel_unserved(events, kernel_gen, e_star - 1)
        assert int(below.max()) > int(at.max())


def test_the_statistic_is_stated_or_asked_for() -> None:
    ss, load, policy, b = _inputs([(200, 60, 0.8, 0.0)])
    r = required_bess_energy(
        ss,
        load,
        policy,
        b,
        A.config(),
        statistic=Unknown(kind="unknown", reason="synthetic: not stated"),
    )
    assert isinstance(r.outcome, RequiredNotComputed) and r.data_requests
    q = StatedQuantile.model_validate_json(
        json.dumps(
            {"kind": "stated_quantile", "q": {"numerator": 1, "denominator": 2}, "over": "draws"}
        )
    )
    rq = required_bess_energy(ss, load, policy, b, A.config(), statistic=q)
    rm = required_bess_energy(ss, load, policy, b, A.config(), statistic=MAX)
    assert isinstance(rq.outcome, RequiredComputed) and isinstance(rm.outcome, RequiredComputed)
    assert rq.outcome.with_gensets.required_kwh <= rm.outcome.with_gensets.required_kwh
    assert rq.outcome.with_gensets.ensemble_max_kwh == rm.outcome.with_gensets.required_kwh
    assert isinstance(rm.outcome.without_gensets, NotApplicable)


def test_a_standby_derived_floor_has_no_counterfactual() -> None:
    m = O.model([O.genset("G1", kw=400, start_s=20)], detection_s=2, transfer_s=3)
    data = json.loads(A.requirement(kw=100).model_dump_json())
    data["duration"] = {"kind": "derived_from_standby", "standby_ref": m.content_hash()}
    req = AutonomyRequirement.model_validate_json(json.dumps(data))
    ss, load, _, b = _inputs([(200, 120, 0.8, 0.0)])
    policy = size_floor(req, b, A.flat_partition(), load=load, standby=m)
    r = required_bess_energy(ss, load, policy, b, A.config(), statistic=MAX, generation=O.bundle(m))
    assert isinstance(r.outcome, RequiredComputed)
    assert isinstance(r.outcome.without_gensets, NotApplicable)
    assert any("without the standby plant" in a.request for a in r.data_requests)


def test_the_requirement_reproduces_by_hash() -> None:
    a = _required([(200, 240, 0.8, 600.0)], _genset())
    b = _required([(200, 240, 0.8, 600.0)], _genset())
    assert a.content_hash() == b.content_hash()
    c = _required([(200, 240, 0.8, 600.0)], _genset(kw=500))
    assert c.content_hash() != a.content_hash()
