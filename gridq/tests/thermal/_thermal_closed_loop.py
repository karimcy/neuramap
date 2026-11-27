"""An independent closed-loop plant for WP-THERM-05 (D208): the SYNTHETIC fixture
plant stepped one minute at a time with a controller in the loop, written from the physics and
importing nothing from ``gridq.thermal``. It is given the plant as plain numbers (`RAW` from
`_thermal_fixture`), and each minute the heat to extract ``x``, the dry bulb ``T`` and the
controller's command (running chillers, extraction kWth).

**Units (anti-cycling).** Each of ``n`` chillers is on or off since some minute. A start is refused
for a unit off for less than ``min_off`` minutes, a stop for a unit on for less than ``min_run``.
A failed unit (`Plant.fail`) is off and never starts again.
A command for ``k`` units starts the longest-off eligible units or stops the longest-on eligible
ones, as far as the timers allow.

**Extraction.** The running units extract ``Q`` clipped to ``[k r q, k q]`` (minimum part load and
capacity). ``F = x - Q`` is the heat not extracted, constant over the minute.

**Thermal nodes, exactly.** ``C_loop w' = F + UA (a - w)``, ``C_hall a' = UA (w - a)``. The
sum ``S = C_loop w + C_hall a`` is the zero mode (``S' = F``) and the difference ``g = a - w`` the
decaying mode (``g' = -k g - F / C_loop``, ``k = UA (1 / C_hall + 1 / C_loop)``). Over a minute
of constant inputs each node is ``alpha + beta t + gamma exp(-k t)``, which has at most one
stationary point: the minute's extremes are taken at both ends and there, in closed form, so a
peak between samples is never missed (the pass criterion is zero tolerance, round-off aside).

**Limits** are checked in absolute terms against the fixture's own numbers: the IT inlet is
``inlet_hi + a`` against the binding maximum and ``inlet_lo + a`` against the binding minimum and
the maximum RH at the highest operating dew point; the loop is ``setpoint + w`` against its band;
the rate of change is the inlet's change over every 60- and 15-minute window of minute samples.

**Rebound accounting.** Power is ``P(x, T)`` per regime; the kW the controller cut is
``P(x, T) - P(Q, T)`` (negative during recovery: the rebound).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ROUND_OFF_K = 1e-9


def _magnus_hpa(t: float) -> float:
    # WMO No-8 / JCOMM TR 63 eq. (3a), retyped so this plant shares no code with the model.
    return 6.112 * math.exp(17.62 * t / (243.12 + t))


def rh_percent(t: float, dew: float) -> float:
    return 100.0 * _magnus_hpa(dew) / _magnus_hpa(t)


def power(raw: dict[str, Any], x: float, t: float) -> float:
    b = raw["coeffs"][1] if t >= raw["t_star"] else raw["coeffs"][0]
    return b[0] + b[1] * x + b[2] * t + b[3] * x * t


def staging(raw: dict[str, Any], x: float) -> int:
    """The plant's own staging (the fixture's rule): ``ceil(x / q) + standby``, capped."""
    return int(min(raw["n"], max(1, math.ceil(x / raw["q_unit"] - 1e-12)) + raw["standby"]))


@dataclass
class Minute:
    """What the plant did in one minute."""

    minute: int
    running: int
    extraction_kwth: float
    deficit_kw: float
    cut_kw: float
    w_end: float
    a_end: float
    w_lo: float
    w_hi: float
    a_lo: float
    a_hi: float


@dataclass
class Plant:
    raw: dict[str, Any]
    x0: float
    w: float = 0.0
    a: float = 0.0
    on: list[bool] = field(default_factory=list)
    since: list[int] = field(default_factory=list)
    log: list[Minute] = field(default_factory=list)
    #: Units out of service (a fault): off, and never started again.
    failed: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.on:
            k = staging(self.raw, self.x0)
            self.on = [i < k for i in range(self.raw["n"])]
            self.since = [-(10**9)] * self.raw["n"]

    @property
    def running(self) -> int:
        return sum(self.on)

    def locked_off(self, minute: int) -> int:
        """Units off and not yet allowed to restart."""
        return sum(
            1
            for on, s in zip(self.on, self.since, strict=True)
            if not on and minute - s < self.raw["min_off"]
        )

    def fail(self, minute: int, unit: int) -> None:
        """A running or idle unit trips and stays out of service."""
        if self.on[unit]:
            self.on[unit], self.since[unit] = False, minute
        self.failed.add(unit)

    def _can_start(self, minute: int, i: int) -> bool:
        return (
            not self.on[i]
            and i not in self.failed
            and minute - self.since[i] >= self.raw["min_off"]
        )

    def startable(self, minute: int) -> int:
        """Units off, in service and past their minimum off time."""
        return sum(1 for i in range(self.raw["n"]) if self._can_start(minute, i))

    def stoppable(self, minute: int) -> int:
        """Units on and past their minimum run time."""
        return sum(
            1
            for on, s in zip(self.on, self.since, strict=True)
            if on and minute - s >= self.raw["min_run"]
        )

    def _stage(self, minute: int, want: int) -> None:
        want = max(0, min(self.raw["n"], want))
        order = sorted(range(self.raw["n"]), key=lambda i: (self.since[i], i))
        while self.running < want:
            idx = next((i for i in order if self._can_start(minute, i)), None)
            if idx is None:
                break
            self.on[idx], self.since[idx] = True, minute
        while self.running > want:
            idx = next(
                (i for i in order if self.on[i] and minute - self.since[i] >= self.raw["min_run"]),
                None,
            )
            if idx is None:
                break
            self.on[idx], self.since[idx] = False, minute

    def step(self, minute: int, x: float, t: float, running: int, extraction: float) -> Minute:
        r = self.raw
        self._stage(minute, running)
        k = self.running
        q = min(max(float(extraction), k * r["min_plr"] * r["q_unit"]), k * r["q_unit"])
        f = x - q
        cw, ca, ua = r["c_loop"], r["c_hall"], r["ua"]
        c = cw + ca
        lam = ua * (1.0 / ca + 1.0 / cw)
        s0 = cw * self.w + ca * self.a
        g0 = self.a - self.w
        g_inf = -f / (cw * lam)
        amp = g0 - g_inf

        def nodes(tt: float) -> tuple[float, float]:
            s = s0 + f * tt
            g = g_inf + amp * math.exp(-lam * tt)
            return (s - ca * g) / c, (s + cw * g) / c

        samples = [0.0, 60.0]
        # w' = (f + ca lam amp e^{-lam t}) / c ; a' = (f - cw lam amp e^{-lam t}) / c.
        for coef in (ca * lam * amp, -cw * lam * amp):
            if coef != 0.0:
                z = -f / coef
                if math.exp(-lam * 60.0) < z < 1.0:
                    samples.append(-math.log(z) / lam)
        pts = [nodes(tt) for tt in samples]
        w1, a1 = nodes(60.0)
        rec = Minute(
            minute=minute,
            running=k,
            extraction_kwth=q,
            deficit_kw=f,
            cut_kw=power(r, x, t) - power(r, q, t),
            w_end=w1,
            a_end=a1,
            w_lo=min(p[0] for p in pts),
            w_hi=max(p[0] for p in pts),
            a_lo=min(p[1] for p in pts),
            a_hi=max(p[1] for p in pts),
        )
        self.w, self.a = w1, a1
        self.log.append(rec)
        return rec


def violations(raw: dict[str, Any], log: list[Minute], tol: float = ROUND_OFF_K) -> list[str]:
    """Every thermal limit the run broke (empty: none), judged on the minutes' exact extremes and
    the minute samples' rate windows."""
    out: list[str] = []
    if not log:
        return out
    a_hi = max(m.a_hi for m in log)
    a_lo = min(m.a_lo for m in log)
    w_hi = max(m.w_hi for m in log)
    w_lo = min(m.w_lo for m in log)
    if raw["inlet_hi"] + a_hi > raw["binding_max"] + tol:
        over = raw["inlet_hi"] + a_hi - raw["binding_max"]
        out.append(f"IT inlet above the binding maximum by {over:.3g} K")
    if raw["inlet_lo"] + a_lo < raw["binding_min"] - tol:
        out.append("IT inlet below the binding minimum")
    if rh_percent(raw["inlet_lo"] + a_lo, raw["dew_hi"]) > raw["max_rh"] + 1e-9:
        out.append("IT inlet RH above the maximum")
    if raw["setpoint"] + w_hi > raw["loop_max"] + tol:
        out.append(f"loop above its band by {raw['setpoint'] + w_hi - raw['loop_max']:.3g} K")
    if raw["setpoint"] + w_lo < raw["loop_min"] - tol:
        out.append(f"loop below its band by {raw['loop_min'] - raw['setpoint'] - w_lo:.3g} K")
    a = np.array([0.0] + [m.a_end for m in log])
    rho = raw["rate_c_per_h"]
    for win, cap in ((60, rho), (15, rho / 4.0)):
        if len(a) > win:
            worst = float(np.max(np.abs(a[win:] - a[:-win])))
            if worst > cap + tol:
                out.append(f"inlet rate over {win} min {worst:.4g} K above {cap:.4g} K")
    return out
