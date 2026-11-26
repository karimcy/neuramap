"""The chronological kernel: one fixed-step equation, integer joules, every draw at once.

**One equation (D170, D174).** Each step of ``dt`` seconds (60, or 1 inside an event
window when the run asks for one-second resolution) does, for every load draw at once:

1. *cover*: the import the permit cannot carry, ``need = max(0, load - permit)``, is requested
   from the battery up to the cover power cap, through `_dispatch` with purpose ``EVENT_COVER``;
2. *merchant* (envelope-adversary posture only): the envelope's merchant kW is requested through
   `_dispatch` with purpose ``MERCHANT``;
3. *charge*: headroom under the permit recharges toward the step's target through `_charge`.

`_dispatch` is the **single choke point** for every joule leaving the battery: it never delivers
below ``max(floor, purpose floor)``, so no path can trade or discharge below the resilience floor.
A shortfall is unserved energy, never a draw on the floor. A structural test asserts no other
code in this module subtracts from the state of energy.

**Exact.** Power is integer W, energy integer J (W x s), efficiencies exact rationals
``num/den``: the battery-side request is ``ceil(ac x den / num)`` and, when the floor clamps it,
the AC delivered is ``floor(battery x num / den)``. Every platform gives the same bytes.

**Quiescence skipping.** A minute is *active* when anything can change: a restriction, a flex call
or rebound, any draw short of power, a change of floor, target, merchant kW or cap, a one-second
minute, or minute 0. Outside active minutes the state is a provable fixed point once every draw is
at or above the charge target and (in a merchant period) at or below the merchant floor; the kernel
then jumps to the next active minute. ``skip=False`` runs every step; both give the same
``paths_digest`` (a test asserts it), because a skipped step changes nothing and is not recorded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

import numpy as np

__all__ = [
    "FloorBreach",
    "KernelInputs",
    "Purpose",
    "ScenarioOutcome",
    "Trace",
    "run_kernel",
]

#: Recharge status codes per (gap, draw).
GAP_FEASIBLE: Final = 0
GAP_INFEASIBLE: Final = 1
GAP_CENSORED: Final = 2


class FloorBreach(AssertionError):
    """A step left a draw below the resilience floor after discharging. Unreachable by
    construction; the kernel checks every step independently of `_dispatch` and raises."""


class Purpose(Enum):
    EVENT_COVER = "event_cover"
    MERCHANT = "merchant"
    #: Reserved: a committed service's own dispatch (not simulated in WP-SITE-04).
    SERVICE = "service"


def _dispatch(
    soe_j: np.ndarray,
    request_j: np.ndarray,
    purpose: Purpose,
    floor_j: int,
    purpose_floor_j: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Take up to ``request_j`` from the battery, never below ``max(floor, purpose floor)``.

    The only place the state of energy is reduced. Returns ``(delivered_j, new_soe_j)``."""
    lower = max(floor_j, purpose_floor_j)
    available = np.maximum(soe_j - lower, 0)
    delivered = np.minimum(request_j, available)
    return delivered, soe_j - delivered


def _charge(soe_j: np.ndarray, stored_j: np.ndarray) -> np.ndarray:
    """Add stored energy (already limited to the room below the target)."""
    return soe_j + stored_j


@dataclass(frozen=True)
class KernelInputs:
    """One scenario, prepared. Per-minute arrays have length ``M``; ``net_w`` is ``(N, M)``."""

    net_w: np.ndarray
    permit_w: np.ndarray
    restricted: np.ndarray
    cover_cap_w: np.ndarray
    floor_j: np.ndarray
    cover_floor_j: np.ndarray
    merchant_w: np.ndarray
    merchant_floor_j: np.ndarray
    charge_target_j: np.ndarray
    charge_cap_w: np.ndarray
    thermal_active: np.ndarray
    fine: np.ndarray
    eta_discharge: tuple[int, int]
    eta_charge: tuple[int, int]
    soe0_j: int
    event_start_s: tuple[float, ...]
    event_end_s: tuple[float, ...]
    recharge_metrics: bool
    unserved_threshold_w: int


@dataclass
class Trace:
    """Every processed step, for the independent floor check (tests only)."""

    t_s: list[int] = field(default_factory=list)
    floor_j: list[int] = field(default_factory=list)
    soe_before: list[np.ndarray] = field(default_factory=list)
    soe_after: list[np.ndarray] = field(default_factory=list)
    discharged_j: list[np.ndarray] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioOutcome:
    any_unserved: np.ndarray  # (N,) bool
    loss_s: np.ndarray  # (N,) int
    unserved_j: np.ndarray  # (N,) int
    event_any: np.ndarray  # (E + 1, N) bool; row E is "outside any event"
    margin_j: np.ndarray  # (N,) int
    margin_at_s: np.ndarray  # (N,) int
    gap_status: np.ndarray  # (G, N) int
    gap_margin_s: np.ndarray  # (G, N) int (feasible only)
    gap_shortfall_j: np.ndarray  # (G, N) int (infeasible only)
    paths_digest: str
    steps_processed: int
    floor_breaches: int


def _ceil_div(a: np.ndarray, b: int) -> np.ndarray:
    return -(-a // b)


def _active_minutes(k: KernelInputs) -> np.ndarray:
    m = k.permit_w.shape[0]
    active = np.zeros(m, dtype=bool)
    active[0] = True
    # Event edges: the recharge bookkeeping reads the state there.
    for edge in (*k.event_start_s, *k.event_end_s):
        for mm in {int(edge // 60), -int(-edge // 60)}:
            if 0 <= mm < m:
                active[mm] = True
    active |= k.restricted | k.thermal_active | k.fine
    active |= (k.net_w > k.permit_w[None, :]).any(axis=0)
    for arr in (
        k.permit_w,
        k.cover_cap_w,
        k.floor_j,
        k.cover_floor_j,
        k.merchant_w,
        k.merchant_floor_j,
        k.charge_target_j,
        k.charge_cap_w,
    ):
        active[1:] |= arr[1:] != arr[:-1]
    return active


def run_kernel(
    k: KernelInputs, *, skip: bool = True, trace: Trace | None = None
) -> ScenarioOutcome:
    n, m = k.net_w.shape
    dn, dd = k.eta_discharge
    cn, cd = k.eta_charge
    soe = np.full(n, k.soe0_j, dtype=np.int64)
    active = _active_minutes(k)
    nxt = np.full(m + 1, m, dtype=np.int64)
    for i in range(m - 1, -1, -1):
        nxt[i] = i if active[i] else nxt[i + 1]

    n_ev = len(k.event_start_s)
    starts = np.asarray(k.event_start_s, dtype=np.float64)
    any_unserved = np.zeros(n, dtype=bool)
    loss_s = np.zeros(n, dtype=np.int64)
    unserved_tot = np.zeros(n, dtype=np.int64)
    event_any = np.zeros((n_ev + 1, n), dtype=bool)
    margin = soe - int(k.floor_j[0])
    margin_at = np.zeros(n, dtype=np.int64)
    # Recharge: gaps follow merged events; gap g follows event-group g.
    groups: list[tuple[float, float]] = []
    for s, e in zip(k.event_start_s, k.event_end_s, strict=True):
        if groups and s <= groups[-1][1]:
            groups[-1] = (groups[-1][0], e)
        else:
            groups.append((s, e))
    n_g = len(groups) if k.recharge_metrics else 0
    group_ends = np.asarray([e for _, e in groups[:n_g]], dtype=np.float64)
    gap_entry = np.zeros((n_g, n), dtype=np.int64)
    gap_target = np.zeros((n_g, n), dtype=np.int64)
    gap_rec_t = np.full((n_g, n), -1, dtype=np.int64)
    gap_status = np.full((n_g, n), GAP_CENSORED, dtype=np.int64)
    gap_margin = np.zeros((n_g, n), dtype=np.int64)
    gap_short = np.zeros((n_g, n), dtype=np.int64)
    g_next_start = 0  # next group whose start we have not reached

    digest = hashlib.sha256(b"gridq.adequacy.paths/1")
    prev_floor: int | None = None
    breaches = 0
    processed = 0
    minute = 0
    while minute < m:
        if skip and not active[minute]:
            lower_m = max(int(k.merchant_floor_j[minute]), int(k.floor_j[minute]))
            quiet = bool((soe >= k.charge_target_j[minute]).all()) and (
                k.merchant_w[minute] == 0 or bool((soe <= lower_m).all())
            )
            if quiet:
                minute = int(nxt[minute])
                continue
        sub = 60 if k.fine[minute] else 1
        dt = 60 // sub
        floor = int(k.floor_j[minute])
        cover_floor = int(k.cover_floor_j[minute])
        permit = int(k.permit_w[minute])
        net = k.net_w[:, minute]
        need_w = np.maximum(net - permit, 0)
        cover_w = np.minimum(need_w, int(k.cover_cap_w[minute]))
        head_w = np.maximum(permit - net, 0) if k.charge_cap_w[minute] > 0 else np.zeros_like(net)
        charge_w = np.minimum(head_w, int(k.charge_cap_w[minute]))
        target = int(k.charge_target_j[minute])
        merch_w = max(0, min(int(k.merchant_w[minute]), int(k.cover_cap_w[minute])))
        merch_floor = int(k.merchant_floor_j[minute])
        for j in range(sub):
            t = minute * 60 + j * dt
            processed += 1
            before = soe
            # Group starts reached: record the recharge target (state on entering the event).
            while g_next_start < n_g and groups[g_next_start][0] <= t:
                gap_entry[g_next_start] = soe
                gap_target[g_next_start] = np.minimum(soe, target)
                g_next_start += 1
            # 1. cover
            ac_req = cover_w * dt
            bat_req = _ceil_div(ac_req * dd, dn)
            got, soe = _dispatch(soe, bat_req, Purpose.EVENT_COVER, floor, cover_floor)
            ac = np.where(got == bat_req, ac_req, (got * dn) // dd)
            short = need_w * dt - ac
            discharged = got
            # 2. merchant
            if merch_w:
                room_w = np.maximum(merch_w - cover_w, 0)
                m_req = _ceil_div(room_w * dt * dd, dn)
                m_got, soe = _dispatch(soe, m_req, Purpose.MERCHANT, floor, merch_floor)
                discharged = discharged + m_got
            # 3. charge
            if target:
                room_j = np.maximum(target - soe, 0)
                grid = np.minimum(charge_w * dt, _ceil_div(room_j * cd, cn))
                stored = np.minimum((grid * cn) // cd, room_j)
                soe = _charge(soe, stored)
            # Independent floor check: any draw that discharged ends at or above the floor.
            bad = (discharged > 0) & (soe < floor)
            if bad.any():
                breaches += int(bad.sum())
            if trace is not None:
                trace.t_s.append(t)
                trace.floor_j.append(floor)
                trace.soe_before.append(before.copy())
                trace.soe_after.append(soe.copy())
                trace.discharged_j.append(discharged.copy())
            unserved_step = short >= k.unserved_threshold_w * dt
            if unserved_step.any():
                any_unserved |= unserved_step
                loss_s += np.where(unserved_step, dt, 0)
                e_idx = int(np.searchsorted(starts, t, side="right")) - 1
                row = n_ev if e_idx < 0 else e_idx
                event_any[row] |= unserved_step
            unserved_tot += short
            mg = soe - floor
            better = mg < margin
            if better.any():
                margin = np.where(better, mg, margin)
                margin_at = np.where(better, t + dt, margin_at)
            # Recharge detection after each group's end, before the next group's start.
            t_end = t + dt
            g = int(np.searchsorted(group_ends, t_end, side="left")) - 1
            if 0 <= g < n_g and (g + 1 >= n_g or t < groups[g + 1][0]):
                hit = (gap_rec_t[g] < 0) & (soe >= gap_target[g])
                gap_rec_t[g] = np.where(hit, t_end, gap_rec_t[g])
            changed = (soe != before) | (short > 0)
            if changed.any() or floor != prev_floor:
                idx = np.flatnonzero(changed).astype("<i8")
                digest.update(np.asarray([t, dt, floor, idx.size], dtype="<i8").tobytes())
                digest.update(idx.tobytes())
                digest.update(soe[idx].astype("<i8").tobytes())
                digest.update(short[idx].astype("<i8").tobytes())
            prev_floor = floor
        minute += 1

    for g in range(n_g):
        if g + 1 >= n_g:
            continue  # the last gap is censored
        nxt_start = groups[g + 1][0]
        rec = gap_rec_t[g]
        ok = (rec >= 0) & (rec <= nxt_start)
        gap_status[g] = np.where(ok, GAP_FEASIBLE, GAP_INFEASIBLE)
        gap_margin[g] = np.where(ok, np.floor(nxt_start - rec).astype(np.int64), 0)
        # Shortfall: the target minus the state entering the next group.
        gap_short[g] = np.where(
            gap_status[g] == GAP_INFEASIBLE, np.maximum(gap_target[g] - gap_entry[g + 1], 0), 0
        )
    return ScenarioOutcome(
        any_unserved=any_unserved,
        loss_s=loss_s,
        unserved_j=unserved_tot,
        event_any=event_any,
        margin_j=margin,
        margin_at_s=margin_at,
        gap_status=gap_status,
        gap_margin_s=gap_margin,
        gap_shortfall_j=gap_short,
        paths_digest="sha256:" + digest.hexdigest(),
        steps_processed=processed,
        floor_breaches=breaches,
    )
