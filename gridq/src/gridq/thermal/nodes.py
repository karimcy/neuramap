"""The two thermal nodes stepped exactly under a constant deficit (WP-TWIN-01, D198).

The plant model's node equations (`gridq.thermal.plant`, D163 M-b), as deviations from the
setpoint-holding baseline::

    C_w w' = F + UA (a - w)        C_a a' = UA (w - a)

In the coordinates ``E = C_w w + C_a a`` (stored heat, kJ) and ``d = a - w`` (the hall-loop
imbalance, K), a constant ``F`` (kW) over ``dt`` seconds has the exact solution::

    E(dt) = E + F dt
    d(dt) = d_inf + (d - d_inf) exp(-lam dt),   d_inf = -F / (C_w lam)

and the nodes are ``w = (E - C_a d) / C`` and ``a = (E + C_w d) / C`` with ``C = C_w + C_a``.

These are the same expressions `gridq.thermal.certificate._sweep` inlines for one minute (its
loop deviation is ``(e - ca * d) / c``). The certificate is left as it is (parallel thermal work
packages edit it and its mutation tests anchor on its text); a tripwire test
(`tests/site_twin/test_twin_thermal.py`) fails if the certificate's lines change, so the two
copies cannot drift apart silently. Folding the certificate onto this module is a follow-up.

Used by the site twin (`gridq.site_twin`), which steps the plant one second at a time.
"""

from __future__ import annotations

import math

from gridq.thermal.plant import NumericPlant

__all__ = ["deviations", "step"]


def step(plant: NumericPlant, e: float, d: float, f: float, dt_s: float) -> tuple[float, float]:
    """``(E, d)`` after ``dt_s`` seconds at a constant deficit ``f`` kW (exact)."""
    if dt_s < 0:
        raise ValueError("time only moves forward")
    d_inf = -f / (plant.c_loop * plant.lam)
    return e + f * dt_s, d_inf + (d - d_inf) * math.exp(-plant.lam * dt_s)


def deviations(plant: NumericPlant, e: float, d: float) -> tuple[float, float]:
    """``(w, a)``: the loop and hall deviations from their baselines, K."""
    c = plant.c_total
    return (e - plant.c_hall * d) / c, (e + plant.c_loop * d) / c
