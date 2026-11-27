"""WP-THERM-05 acceptance (2): comfort/cost mode behaviour is unchanged (D-NEW-THERM05-1).
SYNTHETIC fixture only.

Proved as **non-interference over arbitrary policies**: in comfort/cost mode the controller's
command is the policy's command object, every minute, whatever the readings and whatever
instructions arrive; in the IT-safety mode with no event and no recovery it is too, unless the
safety filter must change it (logged). The reference policy's trajectory is golden-pinned.
Fidelity to ThermaFlex's own comfort/cost mode is a named ask under AWAIT-19, not shown here.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

import _thermal_fixture as fx
import _thermal_harness as h
import numpy as np
from _thermal_closed_loop import staging, violations

from gridq.contracts.thermal_flex import Rejected, ResponseReason
from gridq.thermal.comfort_cost import Observation, PlantCommand, SetpointHoldingPolicy
from gridq.thermal.controller import ObjectiveMode

MIN = timedelta(minutes=1)
T0 = datetime(2027, 5, 12, 0, 0, tzinfo=UTC)
#: sha256 of the reference policy's command trajectory over the pinned SYNTHETIC day (both
#: variants), pinned once from this code; never re-pinned.
GOLDEN = {
    "base": "sha256:6fba0f68c5e3bf3c1804701539c13c916ca6bfd6f44dad63288c302c97beece1",
    "tight": "sha256:b1f627dfca6d4f3009ee354101be55c94a69f614d056b62e5692a446567c5772",
}


class RandomPolicy:
    """A seeded arbitrary policy: any unit count, any extraction (even absurd ones)."""

    def __init__(self, seed: int, n: int, baseline: bool = False) -> None:
        self.rng = np.random.default_rng(seed)
        self.n = n
        self.baseline = baseline
        self.returned: list[PlantCommand] = []

    @property
    def baseline_equivalent(self) -> bool:
        return self.baseline

    def command(self, obs: Observation) -> PlantCommand:
        c = PlantCommand(int(self.rng.integers(0, self.n + 1)), float(self.rng.normal(0, 2000)))
        self.returned.append(c)
        return c


class NoisyPolicy:
    """A seeded plausible policy: the plant's own staging, extraction within 1 kW of the load."""

    def __init__(self, seed: int, raw: dict[str, object]) -> None:
        self.rng = np.random.default_rng(seed)
        self.raw = raw
        self.returned: list[PlantCommand] = []

    @property
    def baseline_equivalent(self) -> bool:
        return False

    def command(self, obs: Observation) -> PlantCommand:
        x = obs.heat_kwth
        c = PlantCommand(staging(self.raw, x), x + float(self.rng.uniform(-1.0, 1.0)))
        self.returned.append(c)
        return c


class UnsafePolicy:
    """Extracts 250 kW less than the load, always: it would overheat the hall."""

    baseline_equivalent = False

    def __init__(self, raw: dict[str, object]) -> None:
        self.raw = raw
        self.returned: list[PlantCommand] = []

    def command(self, obs: Observation) -> PlantCommand:
        c = PlantCommand(staging(self.raw, obs.heat_kwth), obs.heat_kwth - 250.0)
        self.returned.append(c)
        return c


def _trajectory_hash(commands: list[PlantCommand]) -> str:
    rows = [[c.running_units, c.extraction_kwth.hex()] for c in commands]
    return "sha256:" + hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _instructions(variant: str, rng: np.random.Generator, n: int) -> h.Sends:
    """Arbitrary instructions over the run, each received at its issue."""
    surface = fx.built(variant).surface
    out = []
    for k in range(n):
        start = T0 + int(rng.integers(40, 1300)) * MIN
        d = int(rng.integers(1, 300))
        kw = int(rng.integers(1, 80))
        ins = h.instruction(surface, start, d, kw, f"SYNTHETIC-CC-{k}", notice_min=30)
        out.append((ins.issued_at, ins))
    return out


def test_the_reference_policy_trajectory_is_pinned_in_both_modes() -> None:
    """The clean-room reference policy's commands over a SYNTHETIC day, in comfort/cost mode
    and in the IT-safety mode with nothing asked of it: identical, and equal to the pin."""
    for variant in fx.VARIANTS:
        hashes = []
        for mode in ObjectiveMode:
            ctrl = h.controller(variant, mode=mode)
            run = h.simulate(variant, T0, 1440, "random", 11, ctrl=ctrl)
            assert all(s.command is s.base for s in run.steps)
            hashes.append(_trajectory_hash([s.command for s in run.steps]))
        assert hashes[0] == hashes[1] == GOLDEN[variant], (variant, hashes)


def test_comfort_cost_commands_are_the_policy_objects_for_arbitrary_policies() -> None:
    """Differential, over seeded arbitrary policies, arbitrary readings (lost telemetry and
    faults included) and arbitrary instructions: the command is the policy's own object every
    minute, the policy is called exactly once per minute, and every instruction is refused."""
    raw = fx.RAW
    res = fx.built("base")
    for seed in range(12):
        rng = np.random.default_rng(seed)
        policy = RandomPolicy(seed, raw["n"], baseline=bool(seed % 2))
        ctrl = h.controller("base", policy=policy, mode=ObjectiveMode.COMFORT_COST)
        sends = sorted(_instructions("base", rng, 6), key=lambda p: p[0])
        q = 0
        for i in range(600):
            at = T0 + i * MIN
            while q < len(sends) and sends[q][0] <= at:
                ack = ctrl.receive(sends[q][1], sends[q][0])
                assert isinstance(ack.response, Rejected)
                q += 1
            lost = rng.random() < 0.05
            obs = Observation(
                at=at,
                heat_kwth=float(rng.uniform(0, 2000)),
                ambient_c=float(rng.uniform(-20, 45)),
                loop_dev_c=math.nan if lost else float(rng.normal(0, 3)),
                inlet_dev_c=math.nan if lost else float(rng.normal(0, 3)),
                running=int(rng.integers(0, 5)),
                startable=int(rng.integers(0, 3)),
                stoppable=int(rng.integers(0, 3)),
                faulted=int(rng.integers(0, 2)),
            )
            rec = ctrl.step(obs)
            assert rec.command is policy.returned[-1] and len(policy.returned) == i + 1
            assert rec.base is rec.command and rec.binding is None
        assert not ctrl.events
        assert res.surface is ctrl.surface


def test_the_it_safety_mode_idle_passes_a_plausible_policy_through_untouched() -> None:
    """In the IT-safety mode with no event (instructions are refused: the policy is not the
    certified baseline) the command is the policy's object every minute of a closed-loop day."""
    for variant in fx.VARIANTS:
        for seed in range(3):
            policy = NoisyPolicy(seed, fx.raw(variant))
            ctrl = h.controller(variant, policy=policy)
            rng = np.random.default_rng(seed)
            sends = _instructions(variant, rng, 4)
            run = h.simulate(variant, T0, 1440, "random", seed, ctrl=ctrl, sends=sends)
            assert len(policy.returned) == 1440
            assert all(s.command is p for s, p in zip(run.steps, policy.returned, strict=True))
            assert all(s.binding is None for s in run.steps)
            for ack in run.acks.values():
                assert isinstance(ack.response, Rejected)
                assert ack.response.reason_code in (
                    ResponseReason.CONTROL_AUTHORITY_NOT_GRANTED,
                    ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,
                )


def test_an_unsafe_policy_is_clamped_in_it_safety_mode_and_untouched_in_comfort_cost() -> None:
    """The one exception to pass-through is a logged safety reversion: the same unsafe policy
    breaks the limits in comfort/cost mode (its commands are not touched) and keeps them in the
    IT-safety mode (the filter binds, and says so)."""
    raw = fx.RAW
    comfort = h.controller("base", policy=UnsafePolicy(raw), mode=ObjectiveMode.COMFORT_COST)
    run = h.simulate("base", T0, 300, "random", 1, ctrl=comfort)
    assert all(s.command is s.base for s in run.steps)
    assert violations(raw, run.log), "the unsafe policy must really be unsafe"
    safe = h.controller("base", policy=UnsafePolicy(raw))
    run = h.simulate("base", T0, 300, "random", 1, ctrl=safe)
    assert violations(raw, run.log) == []
    assert any(s.binding == "limits" for s in run.steps)
    assert all(s.command is s.base for s in run.steps if s.binding is None)


def test_the_reference_policy_is_the_certified_baseline() -> None:
    p = fx.built("base").certificate.plant
    pol = SetpointHoldingPolicy(p)
    obs = Observation(T0, 612.5, 9.0, 0.0, 0.0, 3, 1, 3, 0)
    assert pol.baseline_equivalent
    assert pol.command(obs) == PlantCommand(staging(fx.RAW, 612.5), 612.5)
