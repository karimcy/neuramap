"""The run-time safety filter of the IT-safety objective mode (WP-THERM-05; D-NEW-THERM05-2).

The filter is the **only** safety argument of the controller. Admission and the WP-THERM-03
certificate decide what is promised (the acknowledgement and its reason); the filter decides
what is safe, every minute, from the measured state, whatever the instruction was.

**The state** is the WP-THERM-03 deviation pair (D163 M-b): ``w`` the chilled-water loop and ``a``
the hall (IT inlet), about the setpoint-holding baseline. With ``E = C_w w + C_a a`` (stored heat,
kJ) and ``d = a - w``, a minute of constant deficit ``F`` (kW, heat not extracted) is exact::

    E(t) = E0 + F t          d(t) = d_inf + (d0 - d_inf) exp(-lam t),   d_inf = -F / (C_w lam)
    w = (E - C_a d) / C      a = (E + C_w d) / C                         C = C_w + C_a

so each node is ``alpha + beta t + gamma exp(-lam t)`` over the minute, with at most one
stationary point; the minute's extremes are taken at both ends and there, in closed form.

**The safe set** ``S`` (every bound already holds back `SETTLE_MARGIN_K`)::

    -under <= w <= loop_up     -under <= a <= hall_up     -under <= E / C <= min(loop_up, hall_up)
    |d| <= d_rate = f_rate / (C_w lam)

**Why it is forward-invariant.** Under ``F = 0`` (release: extract exactly the load), ``E`` is
constant and both nodes move monotonically to ``E / C``, which lies inside both node bands, and
``|d|`` decays. So from any state in ``S`` the release keeps every limit for all time, provided
the running units can extract the load (the controller checks that before it stops a unit).
The filter therefore needs only a one-minute look-ahead: a candidate ``F`` is applied only if the
whole minute (exact extremes) stays within the node bands and the minute ends in ``S``; otherwise
the candidate nearest to it that does is applied. ``|F| <= f_rate`` and ``|d| <= d_rate`` give
``|da/dt| = UA |d| / C_a <= rho`` at every instant, so the 60-minute and 15-minute windows of the
rate limit hold too (D169 (2)).

**Monotone in F.** The system is cooperative (Metzler) with ``F`` entering positively: a larger
``F`` raises both nodes over the whole minute. The set of safe ``F`` is an interval, and contains
``0`` from inside ``S``; the filter bisects along the segment from ``0`` to the candidate.

What it does not cover: a plant that differs from its model (parameter mismatch, sensor noise),
and load that the running units cannot carry. Both are named in docs/decisions.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from gridq.thermal.plant import NumericPlant

__all__ = [
    "BISECTION_STEPS",
    "Binding",
    "FilterResult",
    "MinutePath",
    "SafeSet",
    "minute_path",
]

#: Bisection steps for the safe ``F`` nearest the candidate (2^-40 of the segment, ~1e-9 kW).
BISECTION_STEPS: Final = 40

Binding = Literal["rate", "units", "limits", "no_safe_command"]


@dataclass(frozen=True)
class MinutePath:
    """One minute of constant ``F`` from ``(w0, a0)``: the end state and the exact extremes."""

    w_end: float
    a_end: float
    w_lo: float
    w_hi: float
    a_lo: float
    a_hi: float
    e_end: float
    d_end: float


def minute_path(plant: NumericPlant, w0: float, a0: float, f: float) -> MinutePath:
    cw, ca = plant.c_loop, plant.c_hall
    c, lam = plant.c_total, plant.lam
    e0 = cw * w0 + ca * a0
    d0 = a0 - w0
    d_inf = -f / (cw * lam)
    amp = d0 - d_inf

    def at(t: float) -> tuple[float, float, float, float]:
        e = e0 + f * t
        d = d_inf + amp * math.exp(-lam * t)
        return (e - ca * d) / c, (e + cw * d) / c, e, d

    times = [0.0, 60.0]
    # w' = (F + C_a lam amp e^{-lam t}) / C ; a' = (F - C_w lam amp e^{-lam t}) / C
    floor = math.exp(-lam * 60.0)
    for coef in (ca * lam * amp, -cw * lam * amp):
        if coef != 0.0:
            z = -f / coef
            if floor < z < 1.0:
                times.append(-math.log(z) / lam)
    pts = [at(t) for t in times]
    w1, a1, e1, d1 = pts[1]
    return MinutePath(
        w_end=w1,
        a_end=a1,
        w_lo=min(p[0] for p in pts),
        w_hi=max(p[0] for p in pts),
        a_lo=min(p[1] for p in pts),
        a_hi=max(p[1] for p in pts),
        e_end=e1,
        d_end=d1,
    )


@dataclass(frozen=True)
class FilterResult:
    """The deficit applied this minute, and why it differs from the candidate (or ``None``)."""

    deficit_kw: float
    binding: Binding | None


@dataclass(frozen=True)
class SafeSet:
    """The safe set of one plant, and the filter over it. Each bound is read from one property,
    so a test can weaken exactly one guard."""

    plant: NumericPlant

    @property
    def floor(self) -> float:
        """The lowest either node may go (binding minimum, humidity, loop minimum)."""
        return -self.plant.under_allow

    @property
    def loop_top(self) -> float:
        return self.plant.loop_up

    @property
    def hall_top(self) -> float:
        """The highest the hall (IT inlet) may go (binding maximum)."""
        return self.plant.hall_up

    @property
    def f_cap(self) -> float:
        """The largest ``|F|`` (kW) the rate of change allows (D169 (2))."""
        return self.plant.f_rate

    @property
    def d_rate(self) -> float:
        """The largest ``|d|`` at which ``|da/dt| <= rho``: ``rho C_a / (UA 3600)``."""
        return self.f_cap / (self.plant.c_loop * self.plant.lam)

    def contains(self, w: float, a: float) -> bool:
        p = self.plant
        e = p.c_loop * w + p.c_hall * a
        top = min(self.loop_top, self.hall_top)
        return (
            self.floor <= w <= self.loop_top
            and self.floor <= a <= self.hall_top
            and self.floor <= e / p.c_total <= top
            and abs(a - w) <= self.d_rate
        )

    def minute_ok(self, w: float, a: float, f: float) -> bool:
        """The whole minute stays within the node bands and it ends inside the safe set."""
        if abs(f) > self.f_cap:
            return False
        m = minute_path(self.plant, w, a, f)
        return (
            m.w_lo >= self.floor
            and m.w_hi <= self.loop_top
            and m.a_lo >= self.floor
            and m.a_hi <= self.hall_top
            and self.contains(m.w_end, m.a_end)
        )

    def filter(
        self, w: float, a: float, candidate: float, f_min: float, f_max: float
    ) -> FilterResult:
        """The safe deficit nearest ``candidate`` within the units' feasible ``[f_min, f_max]``.

        ``candidate`` is returned unchanged (the same float) when it is feasible and safe.
        Otherwise the segment from the feasible point nearest ``0`` towards the candidate is
        bisected for its safe end; if even that point is unsafe (a state already outside the
        set, or units that cannot extract the load), it is returned with ``no_safe_command``.
        """
        cap = self.f_cap
        lo, hi = max(f_min, -cap), min(f_max, cap)
        binding: Binding | None = None
        f = candidate
        if f > hi or f < lo:
            binding = "rate" if (f > cap or f < -cap) else "units"
            f = min(max(f, lo), hi) if lo <= hi else (f_min if abs(f_min) < abs(f_max) else f_max)
        if lo <= f <= hi and self.minute_ok(w, a, f):
            return FilterResult(f, binding)
        anchor = min(max(0.0, lo), hi) if lo <= hi else f
        if not (lo <= anchor <= hi) or not self.minute_ok(w, a, anchor):
            return FilterResult(anchor, "no_safe_command")
        good, bad = anchor, f
        for _ in range(BISECTION_STEPS):
            mid = 0.5 * (good + bad)
            if self.minute_ok(w, a, mid):
                good = mid
            else:
                bad = mid
        return FilterResult(good, "limits")
