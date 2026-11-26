"""WP-SITE-04 acceptance 1: no simulated path trades or discharges below the resilience floor.

The property test generates event sets and states from a seeded generator (hypothesis is not a
dependency of this repo): random restrictions, notices, loads, floors (flat and stepped), start
and held states (some below the floor), efficiencies, both postures, thermal flex on and off, and
both resolutions. Every step of every draw is then checked against the floor re-derived from the
policy (`independent_floor_check`), not against anything the kernel used.
"""

from __future__ import annotations

import inspect
from fractions import Fraction
from typing import Any

import numpy as np
import pytest
from tests import _adequacy_killers as K
from tests import _adequacy_support as A

from gridq.adequacy import kernel
from gridq.adequacy.floor import Sized, size_floor
from gridq.adequacy.kernel import FloorBreach, Purpose, Trace

ETAS = (Fraction(1), Fraction(19, 20), Fraction(9, 10), Fraction(7, 8))
CASES = 120


def _random_events(
    rng: np.random.Generator, *, max_minutes: int
) -> list[tuple[int, int, float, Any]]:
    events = []
    k = int(rng.integers(0, 120))
    for _ in range(int(rng.integers(0, 5))):
        dur = int(rng.integers(1, max_minutes))
        if k + dur >= 1440:
            break
        depth = float(rng.choice((0.1, 0.25, 0.5, 0.75, 0.9, 1.0)))
        notice: Any = None if rng.random() < 0.3 else float(rng.choice((0, 30, 300, 1800, 7200)))
        events.append((k, dur, depth, notice))
        k += dur + int(rng.integers(0, 300))
    return events


def _case(seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(10_000 + seed)
    draws = int(rng.integers(2, 6))
    stepped = rng.random() < 0.4
    resolution = 1 if rng.random() < 0.15 else 60
    if stepped:
        load = A.minute_load(draws=draws, days=2, seed=seed, day_it_kw=int(rng.integers(200, 900)))
        req = A.requirement(
            fraction=Fraction(int(rng.integers(1, 11)), 10),
            seconds=int(rng.integers(60, 3600)),
        )
        partition = A.banded_partition()
    else:
        lo = int(rng.integers(100, 800))
        load = A.minute_load(draws=draws, days=2, seed=seed, facility_kw=(lo, lo + 200))
        req = A.requirement(kw=int(rng.integers(20, 700)), seconds=int(rng.integers(60, 7200)))
        partition = A.flat_partition()
    eta = ETAS[int(rng.integers(0, len(ETAS)))]
    initial = int(rng.integers(0, 2001))
    operating = int(rng.integers(0, 2001))
    b = A.bess(eta=eta, initial=initial, operating=operating)
    policy = size_floor(req, b, partition, load=load)
    events = _random_events(rng, max_minutes=30 if resolution == 1 else 300)
    ss = A.scenario_set([("s", Fraction(1), events)])
    return {
        "ss": ss,
        "load": load,
        "policy": policy,
        "bess": b,
        "partition": partition,
        "posture": "envelope_adversary" if rng.random() < 0.4 else "reserve_held",
        "thermal": bool(rng.random() < 0.5),
        "resolution_s": resolution,
    }


@pytest.mark.parametrize("seed", range(CASES))
def test_property_no_path_goes_below_the_floor(seed: int) -> None:
    c = _case(seed)
    if not isinstance(c["policy"].state, Sized):
        pytest.skip(
            "this draw of the requirement is infeasible for the battery (covered elsewhere)"
        )
    for tr, outcome in K.run_traced(
        c["ss"],
        c["load"],
        c["policy"],
        c["bess"],
        posture=c["posture"],
        thermal=c["thermal"],
        resolution_s=c["resolution_s"],
    ):
        K.independent_floor_check(tr, c["policy"], c["partition"], A.DAY0)
        assert outcome.floor_breaches == 0
        if c["bess"].initial_soe_kwh >= c["policy"].state.flat_floor_kwh:
            # Started at or above every cell floor: a floor step-up is the only way below it.
            # (Under the adversary the envelope's own floor may sit higher still.)
            if c["partition"] == A.flat_partition() and c["posture"] == "reserve_held":
                assert int(outcome.margin_j.min()) >= 0


def test_property_generator_reaches_the_floor() -> None:
    """The generated cases are not vacuous: many paths are driven down to the floor exactly."""
    touched = 0
    for seed in range(CASES):
        c = _case(seed)
        if not isinstance(c["policy"].state, Sized) or c["resolution_s"] == 1:
            continue
        for _tr, o in K.run_traced(
            c["ss"], c["load"], c["policy"], c["bess"], posture=c["posture"]
        ):
            touched += int((o.margin_j == 0).sum())
    assert touched >= 20


def test_drain_stops_at_the_floor() -> None:
    K.k_drain()


def test_drain_stops_at_the_floor_at_one_second() -> None:
    K.k_drain(resolution_s=1)


def test_dispatch_is_the_floor_choke_point() -> None:
    K.k_dispatch_unit()


def test_envelope_adversary_is_bound_by_the_policy_floor() -> None:
    K.k_adversary_floor()


def test_floor_step_up_binds_at_its_minute() -> None:
    K.k_step_up()
    ss, load, policy, b, _night, day = K.step_up_case()
    ((tr, _o),) = K.run_traced(ss, load, policy, b)
    # The battery starts 1 kWh below the day floor: nothing leaves it from 08:00.
    for t, out in zip(tr.t_s, tr.discharged_j, strict=True):
        if t >= 480 * 60:
            assert not out.any()
    assert day * 3_600_000 > b.initial_soe_kwh * 3_600_000


def test_adversary_stops_trading_on_notice() -> None:
    K.k_merchant_event()


def test_soe_changes_only_through_dispatch_and_charge() -> None:
    """Structural: every assignment to the state of energy in the kernel is `_dispatch`,
    `_charge` or the initial fill, and nothing augments it in place."""
    K.assert_structural(inspect.getsource(kernel))


def test_breach_check_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kernel's own floor check counts a breach if the choke point ever leaked, and the
    aggregation refuses to report such a run."""
    from gridq.adequacy import simulator

    def leaky(soe: Any, req: Any, purpose: Purpose, floor_j: int, pf: int) -> Any:
        got = np.minimum(req, np.maximum(soe, 0))
        return got, soe - got

    monkeypatch.setattr(kernel, "_dispatch", leaky)
    ss, load, policy, b = K._drain_case()
    ((_tr, o),) = K.run_traced(ss, load, policy, b)
    assert o.floor_breaches > 0
    with pytest.raises(FloorBreach):
        simulator._aggregate([o], ss.scenarios, posture="reserve_held", arm="off", cfg=A.config())


@pytest.mark.parametrize("posture", ["reserve_held", "envelope_adversary"])
@pytest.mark.parametrize("thermal", [False, True])
def test_skipping_gives_the_same_paths(posture: str, thermal: bool) -> None:
    """The quiescence skip changes no recorded step: equal paths digests and outcomes."""
    load = A.minute_load(draws=6, days=2)
    b = A.bess(initial=1200, operating=1700)
    policy = size_floor(A.requirement(kw=300), b, A.flat_partition(), load=load)
    ss = A.scenario_set(
        [
            ("a", Fraction(1, 2), [(300, 90, 0.6, 600.0), (700, 45, 1.0, None)]),
            ("b", Fraction(1, 2), [(1000, 200, 0.5, 0.0)]),
        ]
    )
    fast = K.run_traced(ss, load, policy, b, posture=posture, thermal=thermal)
    slow = K.run_traced(ss, load, policy, b, posture=posture, thermal=thermal, skip=False)
    for (tf, of), (ts, os_) in zip(fast, slow, strict=True):
        assert of.paths_digest == os_.paths_digest
        assert of.unserved_j.tolist() == os_.unserved_j.tolist()
        assert of.margin_j.tolist() == os_.margin_j.tolist()
        assert of.margin_at_s.tolist() == os_.margin_at_s.tolist()
        assert of.gap_status.tolist() == os_.gap_status.tolist()
        assert len(tf.t_s) < len(ts.t_s)


def test_one_second_agrees_with_one_minute_for_minute_edges() -> None:
    """Minute-aligned events, no ramp, efficiency 1: the 1 s path equals the 60 s path."""
    load = A.minute_load(draws=4, days=2)
    b = A.bess(eta=Fraction(1), initial=700, operating=900)
    policy = size_floor(A.requirement(kw=200), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("a", Fraction(1), [(300, 40, 0.7, 0.0), (500, 20, 1.0, 0.0)])])
    ((_, m60),) = K.run_traced(ss, load, policy, b)
    ((_, m1),) = K.run_traced(ss, load, policy, b, resolution_s=1)
    assert m1.unserved_j.tolist() == m60.unserved_j.tolist()
    assert m1.margin_j.tolist() == m60.margin_j.tolist()
    assert m1.loss_s.tolist() == m60.loss_s.tolist()
    assert m1.any_unserved.tolist() == m60.any_unserved.tolist()


def test_trace_records_every_processed_step() -> None:
    ss, load, policy, b = K._drain_case()
    ((tr, o),) = K.run_traced(ss, load, policy, b, skip=False)
    assert isinstance(tr, Trace) and len(tr.t_s) == o.steps_processed == 1440
