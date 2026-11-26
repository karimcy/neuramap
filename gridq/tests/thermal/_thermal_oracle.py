"""An independent oracle for WP-THERM-03 (D166 F-d): a naive forward-Euler simulation
of the SYNTHETIC fixture plant, written from the physics, importing nothing from
``gridq.thermal``. It is given the plant as plain numbers, a minute path of the heat to extract
and the dry bulb, a declared point (duration, kW) and the controller's strategy settings, and
reports what really happens: whether the kW was held, every temperature and rate reached, and
the rebound.

The plant: ``n`` chillers of ``q_unit`` kWth; the plant's staging runs
``min(n, max(1, ceil(x / q_unit)) + standby)``; a running chiller cannot unload below
``min_plr``. Plant power ``P(x, T) = b0 + b1 x + b2 T + b3 x T`` per regime (``T >= t_star`` the
second). A cut of ``K`` kW sets ``P = P(x, T) - K``; the extraction ``Q`` that gives it is found
by bisection. Loop and hall nodes::

    C_loop dw/dt = (x - Q) + UA (a - w)      C_hall da/dt = UA (w - a)

stepped by forward Euler at ``h = 2 s``. After the hold the controller recovers the stored heat
``C_loop w + C_hall a`` at ``min(spare running capacity, u_cap)`` and stops when it reaches zero.

**Error bound.** In ``E = C_loop w + C_hall a`` Euler is exact (``E' = F`` is constant inside a
step: inputs change only on minutes). The imbalance ``d = a - w`` obeys
``d' = -lam d - F / C_loop``; its Euler recursion contracts by ``1 - h lam`` per step and its local
error is at most ``h^2 / 2 * lam * max|d'|``, so its global error is at most ``h / 2 * max|d'|``.
Each node is ``(E +- C d) / (C_loop + C_hall)``, so its error is at most that of ``d``. The
tolerance is twice the bound, from the run's own largest ``|d'|``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

H_SEC = 2.0
SUB = 30  # Euler steps per minute


def _magnus_hpa(t: float) -> float:
    # WMO No-8 / JCOMM TR 63 eq. (3a), retyped here so the oracle shares no code with the model.
    return 6.112 * math.exp(17.62 * t / (243.12 + t))


def rh_percent(t: float, dew: float) -> float:
    return 100.0 * _magnus_hpa(dew) / _magnus_hpa(t)


@dataclass
class Batch:
    """``n`` independent runs. Paths are (n, horizon) minute arrays."""

    x: np.ndarray
    t: np.ndarray
    duration: np.ndarray
    kw: np.ndarray
    switch_off: np.ndarray  # bool
    keep: np.ndarray  # units kept (switch-off)
    u_cap: float


@dataclass
class Outcome:
    held: np.ndarray
    loop_max: np.ndarray
    loop_min: np.ndarray
    inlet_dev_max: np.ndarray
    inlet_dev_min: np.ndarray
    rate60: np.ndarray
    rate15: np.ndarray
    recovered_by: np.ndarray  # minutes after release when stored heat reached zero (inf: never)
    rebound_kwh: np.ndarray
    rebound_peak_kw: np.ndarray
    deficit_kj: np.ndarray
    surplus_kj: np.ndarray
    stored_end_kj: np.ndarray
    tol_k: float


def _power(raw: dict[str, Any], x: np.ndarray, t: np.ndarray) -> np.ndarray:
    lo, hi = (np.array(c) for c in raw["coeffs"])
    c = np.where((t >= raw["t_star"])[..., None], hi, lo)
    return c[..., 0] + c[..., 1] * x + c[..., 2] * t + c[..., 3] * x * t


def _n_on(raw: dict[str, Any], x: np.ndarray) -> np.ndarray:
    return np.minimum(raw["n"], np.maximum(1, np.ceil(x / raw["q_unit"] - 1e-12)) + raw["standby"])


def simulate(raw: dict[str, Any], b: Batch) -> Outcome:
    n, horizon = b.x.shape
    q, r = raw["q_unit"], raw["min_plr"]
    cw, ca, ua = raw["c_loop"], raw["c_hall"], raw["ua"]
    w = np.zeros(n)
    a = np.zeros(n)
    held = np.ones(n, dtype=bool)
    loop_max = np.zeros(n)
    loop_min = np.zeros(n)
    a_max = np.zeros(n)
    a_min = np.zeros(n)
    a_hist = np.zeros((n, horizon + 1))
    recovered = np.full(n, np.inf)
    reb_kwh = np.zeros(n)
    reb_peak = np.zeros(n)
    deficit = np.zeros(n)
    surplus = np.zeros(n)
    ddot_max = 0.0
    n0 = _n_on(raw, b.x[:, 0])
    stop, lock = raw["min_run"], raw["min_run"] + raw["min_off"]
    for m in range(horizon):
        x, t = b.x[:, m], b.t[:, m]
        in_event = m < b.duration
        base = _power(raw, x, t)
        # The running units during the hold.
        run = np.where(
            b.switch_off,
            np.where(m < stop, n0, np.minimum(n0, b.keep)),
            _n_on(raw, x),
        )
        target = base - b.kw
        floor = run * r * q
        ok = (_power(raw, floor, t) <= target + 1e-9) & (floor <= x)
        lo_q, hi_q = floor.copy(), x.copy()
        for _ in range(60):
            mid = 0.5 * (lo_q + hi_q)
            above = _power(raw, mid, t) > target
            hi_q = np.where(above, mid, hi_q)
            lo_q = np.where(above, lo_q, mid)
        extract = 0.5 * (lo_q + hi_q)
        ok &= extract <= run * q + 1e-9
        held &= ~in_event | ok
        f_event = np.where(in_event, x - extract, 0.0)
        # Recovery after the hold: spare running capacity, capped.
        after = ~in_event
        restart = np.where(b.switch_off, lock, raw["min_off"])
        spare = np.where(
            m < restart,
            np.where(b.switch_off, np.minimum(n0, b.keep), _n_on(raw, x)) * q - x,
            raw["n"] * q - x,
        )
        u_rate = np.clip(np.minimum(spare, b.u_cap), 0.0, None)
        for _ in range(SUB):
            e = cw * w + ca * a
            u = np.where(after & (e > 0), np.minimum(u_rate, e / H_SEC), 0.0)
            done = after & (e <= 0) & ~np.isfinite(recovered)
            recovered = np.where(done, m - b.duration, recovered)
            f = f_event - u
            dw = (f + ua * (a - w)) / cw
            da = ua * (w - a) / ca
            ddot_max = max(ddot_max, float(np.max(np.abs(da - dw))))
            w = w + H_SEC * dw
            a = a + H_SEC * da
            extra = _power(raw, x + u, t) - base
            reb_kwh += np.where(u > 0, extra, 0.0) * H_SEC / 3600.0
            reb_peak = np.maximum(reb_peak, np.where(u > 0, extra, 0.0))
            deficit += f_event * H_SEC
            surplus += u * H_SEC
            loop_max, loop_min = np.maximum(loop_max, w), np.minimum(loop_min, w)
            a_max, a_min = np.maximum(a_max, a), np.minimum(a_min, a)
        a_hist[:, m + 1] = a
    e_end = cw * w + ca * a
    done = ~np.isfinite(recovered) & (e_end <= 1e-9)
    recovered = np.where(done, horizon - b.duration, recovered)
    rate60 = np.max(np.abs(a_hist[:, 60:] - a_hist[:, :-60]), axis=1) if horizon > 60 else 0 * w
    rate15 = np.max(np.abs(a_hist[:, 15:] - a_hist[:, :-15]), axis=1)
    tol = 2.0 * H_SEC / 2.0 * ddot_max
    return Outcome(
        held=held,
        loop_max=loop_max,
        loop_min=loop_min,
        inlet_dev_max=a_max,
        inlet_dev_min=a_min,
        rate60=rate60,
        rate15=rate15,
        recovered_by=recovered,
        rebound_kwh=reb_kwh,
        rebound_peak_kw=reb_peak,
        deficit_kj=deficit,
        surplus_kj=surplus,
        stored_end_kj=e_end,
        tol_k=tol,
    )
