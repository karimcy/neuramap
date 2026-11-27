"""The twin's battery: the design-time adequacy kernel replayed one segment at a time
(WP-TWIN-01, D197).

The twin does not model the battery. Every change to its state of energy comes out of
`gridq.adequacy.kernel.run_kernel`, the same exact integer equation the adequacy simulator runs
(D170, D174): the twin encodes the setpoint it holds as one minute of kernel inputs (one draw,
``fine`` so the minute is 60 one-second steps), runs the kernel from the present state of energy
and reads ``Trace.soe_after`` one entry per simulated second. A new segment starts at every
minute (the load is per minute) and whenever the setpoint changes; the rest of the old segment is
discarded. ``Trace`` is thereby a supported read interface of the kernel (recorded in the
decision); a follow-up adds the AC and grid joules to it once the parallel SITE work packages have
merged, which removes the one copy below.

The setpoint encoding (after clamping to the ratings), with the battery floor at zero, no merchant
slice, no events and no recharge bookkeeping:

- discharge ``S`` W: ``permit = net - S`` and ``cover_cap = S`` (the kernel's cover step then asks
  the battery for exactly ``S``);
- charge ``C`` W: ``charge_cap = C``, ``permit = net + C`` and the charge target is the usable
  energy (the kernel's charge step then draws exactly ``C`` from the grid until full);
- idle: ``permit = net``, both caps and the target zero.

The meter (`meter_joules`) is the one place the kernel's quantisation is restated: the AC joules a
discharge delivered and the grid joules a charge drew, from the state of energy before and after.
A test (`tests/site_twin/test_twin_electrical.py`) holds it to the kernel and asserts no other
line of this package computes a state of energy.

The twin obeys its setpoint. It does not enforce the import limit: it reports seconds over it.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from gridq.adequacy.inputs import BessParameters, OneWay, RoundTripOnly, efficiency_value
from gridq.adequacy.kernel import KernelInputs, ScenarioOutcome, Trace, run_kernel

__all__ = ["J_PER_KWH", "BessSecond", "BessSpec", "BessTwin", "encode", "meter_joules"]

#: Joules per kWh (the adequacy kernel's unit conversion).
J_PER_KWH = 3_600_000
_MAX_DEN = 10**6


def _eta(e: object) -> tuple[int, int]:
    if not isinstance(e, OneWay | RoundTripOnly):
        raise ValueError("the twin needs a stated efficiency, not unknown")
    f: Fraction = efficiency_value(e)
    if f.denominator > _MAX_DEN:
        raise ValueError(f"efficiency denominators are limited to {_MAX_DEN}")
    return f.numerator, f.denominator


@dataclass(frozen=True)
class BessSpec:
    """The battery in the kernel's units: J, W and exact one-way efficiencies."""

    usable_j: int
    discharge_w: int
    charge_w: int
    eta_discharge: tuple[int, int]
    eta_charge: tuple[int, int]
    soe0_j: int

    def __post_init__(self) -> None:
        if min(self.usable_j, self.discharge_w, self.charge_w) <= 0:
            raise ValueError("usable energy and both ratings are positive")
        if not 0 <= self.soe0_j <= self.usable_j:
            raise ValueError("the initial state of energy lies in [0, usable]")

    @classmethod
    def from_parameters(cls, p: BessParameters) -> BessSpec:
        """The adequacy simulator's own declaration of the battery (D172)."""
        usable, dis = p.usable_energy_kwh, p.discharge_rating_kw
        chg, soe0 = p.charge_rating_kw, p.initial_soe_kwh
        if not (
            isinstance(usable, int)
            and isinstance(dis, int)
            and isinstance(chg, int)
            and isinstance(soe0, int)
        ):
            raise ValueError("the twin needs every battery figure stated, not unknown")
        return cls(
            usable_j=usable * J_PER_KWH,
            discharge_w=dis * 1000,
            charge_w=chg * 1000,
            eta_discharge=_eta(p.discharge_efficiency),
            eta_charge=_eta(p.charge_efficiency),
            soe0_j=soe0 * J_PER_KWH,
        )


def _clamp(spec: BessSpec, setpoint_w: int) -> int:
    """Signed W, load convention (positive charges), inside the ratings."""
    if setpoint_w < 0:
        return -min(-setpoint_w, spec.discharge_w)
    return min(setpoint_w, spec.charge_w)


def encode(spec: BessSpec, setpoint_w: int, net_w: int, soe_j: int) -> KernelInputs:
    """One minute of kernel inputs that makes the kernel's battery follow ``setpoint_w``."""
    sp = _clamp(spec, setpoint_w)

    def one(v: int) -> np.ndarray:
        return np.asarray([v], dtype=np.int64)

    discharge, charge = max(-sp, 0), max(sp, 0)
    return KernelInputs(
        net_w=np.asarray([[net_w]], dtype=np.int64),
        permit_w=one(net_w - discharge + charge),
        restricted=np.zeros(1, dtype=bool),
        cover_cap_w=one(discharge),
        floor_j=one(0),
        cover_floor_j=one(0),
        merchant_w=one(0),
        merchant_floor_j=one(0),
        charge_target_j=one(spec.usable_j if charge else 0),
        charge_cap_w=one(charge),
        thermal_active=np.zeros(1, dtype=bool),
        fine=np.ones(1, dtype=bool),
        eta_discharge=spec.eta_discharge,
        eta_charge=spec.eta_charge,
        soe0_j=soe_j,
        event_start_s=(),
        event_end_s=(),
        recharge_metrics=False,
        unserved_threshold_w=1,
    )


def meter_joules(
    spec: BessSpec, setpoint_w: int, soe_before: int, soe_after: int, dt_s: int = 1
) -> tuple[int, int, int]:
    """``(ac_j, grid_j, short_j)`` of one kernel step: the AC energy a discharge delivered, the
    grid energy a charge drew, and the discharge requested but not delivered. The kernel's own
    quantisation (`gridq.adequacy.kernel.run_kernel`, steps 1 and 3), restated."""
    sp = _clamp(spec, setpoint_w)
    if sp < 0:
        dn, dd = spec.eta_discharge
        ac_req = -sp * dt_s
        bat_req = -(-(ac_req * dd) // dn)
        got = soe_before - soe_after
        ac = ac_req if got == bat_req else (got * dn) // dd
        return ac, 0, ac_req - ac
    if sp > 0:
        cn, cd = spec.eta_charge
        room = max(spec.usable_j - soe_before, 0)
        grid = min(sp * dt_s, -(-(room * cd) // cn))
        return 0, grid, 0
    return 0, 0, 0


@dataclass(frozen=True)
class BessSecond:
    """One simulated second of the battery. ``power_w`` follows the load convention."""

    soe_before_j: int
    soe_after_j: int
    ac_j: int
    grid_j: int
    short_j: int

    @property
    def power_w(self) -> int:
        return self.grid_j - self.ac_j


@dataclass
class _Segment:
    key: tuple[int, int]
    soe_after: list[int]
    outcome: ScenarioOutcome
    pos: int = 0


class BessTwin:
    """The battery, stepped one second at a time by kernel-segment replay."""

    def __init__(self, spec: BessSpec) -> None:
        self.spec = spec
        self.soe_j = spec.soe0_j
        self.kernel_runs = 0
        self._seg: _Segment | None = None

    def _run(self, setpoint_w: int, net_w: int) -> _Segment:
        trace = Trace()
        outcome = run_kernel(
            encode(self.spec, setpoint_w, net_w, self.soe_j), skip=False, trace=trace
        )
        self.kernel_runs += 1
        return _Segment(
            key=(_clamp(self.spec, setpoint_w), net_w),
            soe_after=[int(a[0]) for a in trace.soe_after],
            outcome=outcome,
        )

    def second(self, setpoint_w: int, net_w: int, *, minute_start: bool) -> BessSecond:
        """Advance one second at ``setpoint_w`` with the minute's net load ``net_w``."""
        key = (_clamp(self.spec, setpoint_w), net_w)
        seg = self._seg
        if minute_start or seg is None or seg.key != key or seg.pos >= len(seg.soe_after):
            seg = self._seg = self._run(setpoint_w, net_w)
        before = self.soe_j
        after = seg.soe_after[seg.pos]
        seg.pos += 1
        self.soe_j = after
        ac, grid, short = meter_joules(self.spec, setpoint_w, before, after)
        return BessSecond(before, after, ac, grid, short)
