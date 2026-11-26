"""Psychrometrics for the IT-inlet humidity limits (D167 H-a), WMO No-8 formulae.

The dew point of the hall air is invariant through a cooling-flex event (no moisture is added
or removed by a sensible-only coil excursion), so relative humidity moves only with temperature:
``RH(t) = 100 * e_w(t_d) / e_w(t)`` (JCOMM TR 63 eq. 7a with eq. 3a; the pressure factor f(p)
cancels in the ratio). A minimum RH is therefore a temperature **ceiling** at the lowest dew
point, and a maximum RH a temperature **floor** at the highest dew point.

Every function refuses a temperature outside the source's stated validity range (-45 to 60 °C).
"""

from __future__ import annotations

import math

from gridq.thermal.sources import MAGNUS

__all__ = [
    "ceiling_for_min_rh",
    "dew_point_c",
    "floor_for_max_rh",
    "relative_humidity_percent",
    "saturation_vapour_pressure_hpa",
]


def _guard(t_c: float, what: str) -> None:
    if not (MAGNUS.t_min_c <= t_c <= MAGNUS.t_max_c):
        raise ValueError(
            f"{what} {t_c} °C is outside the WMO No-8 validity range "
            f"{MAGNUS.t_min_c:g} to {MAGNUS.t_max_c:g} °C"
        )


def saturation_vapour_pressure_hpa(t_c: float) -> float:
    """JCOMM TR 63 eq. (3a): saturation vapour pressure over water, hPa."""
    _guard(t_c, "temperature")
    return MAGNUS.a_hpa * math.exp(MAGNUS.b * t_c / (MAGNUS.c_c + t_c))


def dew_point_c(vapour_pressure_hpa: float) -> float:
    """JCOMM TR 63 eq. (6a) with f(p) = 1: the temperature at which ``e_w`` equals the value."""
    if vapour_pressure_hpa <= 0:
        raise ValueError("vapour pressure must be positive")
    g = math.log(vapour_pressure_hpa / MAGNUS.a_hpa)
    t = MAGNUS.c_c * g / (MAGNUS.b - g)
    _guard(t, "dew point")
    return t


def relative_humidity_percent(t_c: float, dew_point: float) -> float:
    """JCOMM TR 63 eq. (7a): RH at ``t_c`` of air whose dew point is ``dew_point``."""
    return 100.0 * saturation_vapour_pressure_hpa(dew_point) / saturation_vapour_pressure_hpa(t_c)


def ceiling_for_min_rh(dew_point_low: float, min_rh_percent: float) -> float:
    """The highest temperature at which air with dew point ``dew_point_low`` keeps at least
    ``min_rh_percent`` RH."""
    return dew_point_c(100.0 * saturation_vapour_pressure_hpa(dew_point_low) / min_rh_percent)


def floor_for_max_rh(dew_point_high: float, max_rh_percent: float) -> float:
    """The lowest temperature at which air with dew point ``dew_point_high`` stays at or below
    ``max_rh_percent`` RH."""
    return dew_point_c(100.0 * saturation_vapour_pressure_hpa(dew_point_high) / max_rh_percent)
