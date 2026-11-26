"""The plant: its power curve, its two thermal nodes, and its binding limits, as numbers.

`resolve` turns `PlantInputs` into a `NumericPlant`, or names every parameter that is unknown.

**Power (D164).** ``P(x, T) = b0 + b1 x + b2 T + b3 x T + b4 x^2`` (C1 has b3 = b4 = 0,
C2 has b4 = 0), with a second regime at and above ``T*`` under free cooling. A reduction of ``K``
kW below the baseline ``P(x0, T)`` leaves the chillers extracting ``x0 - delta`` where
``P(x0, T) - P(x0 - delta, T) = K``. With ``S = dP/dx(x0) = b1 + b3 T + 2 b4 x0``::

    delta = 2 K / (S + sqrt(S^2 - 4 b4 K))            (b4 > 0; delta = K / S when b4 = 0)

``delta`` falls as ``x0`` rises (``S`` rises) and is monotone in ``T`` within a regime, so over a
box ``[x_lo, x_hi] x [T_lo, T_hi]`` its maximum is at ``x_lo`` and a temperature corner (``T*`` on
both sides when the box straddles it). These corner facts hold only where the curve is valid
on the box; `curve_validity` checks that (``dP/dx > 0`` at the corners, ``dP/dT >= 0`` at both
``x`` ends, ``b4 >= 0``, ``P > 0``) and a box that fails is unknown, never extrapolated.

**Thermal nodes (D163 M-b).** Deviations from the setpoint-holding baseline: ``w`` the
chilled-water loop, ``a`` the hall (IT inlet)::

    C_w w' = F + UA (a - w)        C_a a' = UA (w - a)

where ``F`` (kW) is the heat the chillers do not extract (the deficit). The system is cooperative
(Metzler) with ``F`` entering positively, so a larger ``F`` path gives larger ``w`` and ``a``
everywhere (comparison principle). In the coordinates ``E = C_w w + C_a a`` (stored heat, kJ,
``E' = F``) and ``d = a - w`` (``d' = -lam d - F / C_w``, ``lam = UA (1/C_a + 1/C_w)``) each
constant-``F`` minute has the exact solution used by the certificate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np

from gridq.contracts.thermal_flex import CustomerSlaLimit, HumidityLimit
from gridq.thermal.inputs import (
    Capacitance,
    CapacitanceParam,
    CoefficientSet,
    Param,
    PlantInputs,
    Stated,
    VolumeTimesRhoCp,
)
from gridq.thermal.psychro import ceiling_for_min_rh, floor_for_max_rh

__all__ = [
    "SETTLE_MARGIN_K",
    "NumericPlant",
    "PowerCurve",
    "Resolved",
    "Unresolved",
    "binding_band",
    "curve_validity",
    "max_power",
    "max_slope",
    "resolve",
    "settle_minutes",
]

#: Temperature margin (K) held back on every thermal check for the residual hall-loop
#: imbalance an event may start from once a recovery is declared complete (D169 (5)).
#: A method constant, not a site figure.
SETTLE_MARGIN_K: Final = 0.01


@dataclass(frozen=True)
class PowerCurve:
    """C-form coefficients per regime (rows ``b0..b4``, zero-padded) and ``T*``."""

    coefficients: np.ndarray  # shape (n_regimes, 5)
    switch_temperature_c: float | None

    def regime(self, t: np.ndarray) -> np.ndarray:
        if self.switch_temperature_c is None:
            return np.zeros(np.shape(t), dtype=np.intp)
        return (np.asarray(t) >= self.switch_temperature_c).astype(np.intp)

    def _b(self, reg: np.ndarray) -> tuple[np.ndarray, ...]:
        c = self.coefficients[reg]
        return c[..., 0], c[..., 1], c[..., 2], c[..., 3], c[..., 4]

    def power(self, x: np.ndarray, t: np.ndarray, reg: np.ndarray) -> np.ndarray:
        b0, b1, b2, b3, b4 = self._b(reg)
        return b0 + b1 * x + b2 * t + b3 * x * t + b4 * x * x

    def dp_dx(self, x: np.ndarray, t: np.ndarray, reg: np.ndarray) -> np.ndarray:
        _, b1, _, b3, b4 = self._b(reg)
        return b1 + b3 * t + 2.0 * b4 * x

    def dp_dt(self, x: np.ndarray, reg: np.ndarray) -> np.ndarray:
        _, _, b2, b3, _ = self._b(reg)
        return b2 + b3 * x

    def headroom(
        self, x0: np.ndarray, floor: np.ndarray, t: np.ndarray, reg: np.ndarray
    ) -> np.ndarray:
        """``P(x0, T) - P(floor, T)``: the largest reduction that keeps extraction at the floor."""
        _, b1, _, b3, b4 = self._b(reg)
        return (b1 + b3 * t) * (x0 - floor) + b4 * (x0 * x0 - floor * floor)

    def deficit(self, x0: np.ndarray, t: np.ndarray, reg: np.ndarray, k: np.ndarray) -> np.ndarray:
        """``delta`` with ``P(x0) - P(x0 - delta) = K`` (stable root); ``inf`` if none exists."""
        _, b1, _, b3, b4 = self._b(reg)
        s = b1 + b3 * t + 2.0 * b4 * x0
        disc = s * s - 4.0 * b4 * k
        with np.errstate(invalid="ignore", divide="ignore"):
            root = np.sqrt(np.maximum(disc, 0.0))
            out = 2.0 * k / (s + root)
        return np.where((disc < 0) | (s <= 0), np.inf, out)

    def corners(
        self, t_lo: np.ndarray, t_hi: np.ndarray
    ) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Temperature corners of ``[t_lo, t_hi]`` with their regime and a validity mask: the two
        ends, and ``T*`` from each side where the interval straddles it."""
        lo, hi = np.asarray(t_lo, dtype=float), np.asarray(t_hi, dtype=float)
        always = np.ones(lo.shape, dtype=bool)
        out = [(lo, self.regime(lo), always), (hi, self.regime(hi), always)]
        ts = self.switch_temperature_c
        if ts is not None:
            straddle = (lo < ts) & (ts <= hi)
            tsa = np.full(lo.shape, ts)
            out.append((tsa, np.zeros(lo.shape, dtype=np.intp), straddle))
            out.append((tsa, np.ones(lo.shape, dtype=np.intp), straddle))
        return out


@dataclass(frozen=True)
class NumericPlant:
    """Every figure the certificate needs, resolved, in kW, kJ/K, °C and minutes."""

    n_chillers: int
    unit_kwth: float
    min_plr: float
    standby: int
    min_run_min: int | None
    min_off_min: int | None
    curve: PowerCurve

    c_loop: float
    c_hall: float
    ua: float

    setpoint_c: float
    loop_min_c: float
    loop_max_c: float
    inlet_lo_c: float
    inlet_hi_c: float
    #: Highest and lowest allowed hall temperature once band and humidity are both applied.
    hall_ceiling_c: float
    hall_floor_c: float
    #: Rate-of-change limit at the IT inlet, °C per hour.
    rate_c_per_h: float

    @property
    def c_total(self) -> float:
        return self.c_loop + self.c_hall

    @property
    def lam(self) -> float:
        """Decay rate of the hall-loop imbalance ``d``, 1/s."""
        return self.ua * (1.0 / self.c_hall + 1.0 / self.c_loop)

    @property
    def capacity_kwth(self) -> float:
        return self.n_chillers * self.unit_kwth

    @property
    def settle_eps(self) -> float:
        """Residual ``|d|`` (K) at which a recovery counts as settled: its effect on either node
        is at most `SETTLE_MARGIN_K`."""
        return SETTLE_MARGIN_K * self.c_total / max(self.c_loop, self.c_hall)

    @property
    def loop_up(self) -> float:
        return self.loop_max_c - self.setpoint_c - SETTLE_MARGIN_K

    @property
    def hall_up(self) -> float:
        return self.hall_ceiling_c - self.inlet_hi_c - SETTLE_MARGIN_K

    @property
    def under_allow(self) -> float:
        """How far either node may fall below its baseline (K)."""
        return (
            min(self.setpoint_c - self.loop_min_c, self.inlet_lo_c - self.hall_floor_c)
            - SETTLE_MARGIN_K
        )

    @property
    def f_rate(self) -> float:
        """The largest ``|F|`` (kW) that keeps the inlet within the rate limit:
        ``|a'| <= max|F| / (C_w + C_a)`` (D169 (2))."""
        return self.rate_c_per_h * self.c_total / 3600.0

    @property
    def u_cap(self) -> float:
        """Recovery surplus cap (kW): the rate limit, and the undershoot bound
        ``C_a^2 u / (UA C^2) <= allowance`` (D169 (3))."""
        under = max(self.under_allow, 0.0) * self.ua * self.c_total**2 / self.c_hall**2
        return min(self.f_rate, under)

    def coefficients_negative_b4(self) -> bool:
        return bool(np.any(self.curve.coefficients[:, 4] < 0))

    def n_on(self, x: np.ndarray) -> np.ndarray:
        """Running units under the plant's own staging at extraction ``x``."""
        need = np.maximum(np.ceil(np.asarray(x) / self.unit_kwth - 1e-12), 1.0)
        return np.minimum(self.n_chillers, need + self.standby)


@dataclass(frozen=True)
class Resolved:
    plant: NumericPlant
    #: Parameter keys whose provenance is a stand-in.
    stand_ins: tuple[str, ...]


@dataclass(frozen=True)
class Unresolved:
    #: Unknown parameter keys, each with its reason.
    missing: tuple[tuple[str, str], ...]
    #: A stated combination the model cannot use (baseline outside its own limits, ...).
    inconsistent: tuple[str, ...]
    stand_ins: tuple[str, ...]


def _value(
    key: str, p: Param | None, missing: list[tuple[str, str]], stand: list[str]
) -> float | None:
    if p is None:
        return None
    if isinstance(p, Stated):
        if p.provenance.kind == "stand_in":
            stand.append(key)
        return p.value
    missing.append((key, p.reason))
    return None


def _capacitance(
    key: str, p: CapacitanceParam, missing: list[tuple[str, str]], stand: list[str]
) -> float | None:
    if isinstance(p, Capacitance):
        return _value(key, p.kj_per_k, missing, stand)
    if isinstance(p, VolumeTimesRhoCp):
        v = _value(f"{key}.volume_m3", p.volume_m3, missing, stand)
        rc = _value(f"{key}.rho_cp_kj_per_m3_k", p.rho_cp_kj_per_m3_k, missing, stand)
        return None if v is None or rc is None else v * rc
    missing.append((key, p.reason))
    return None


def _humidity_bounds(inputs: PlantInputs) -> tuple[int | None, int | None, int | None, int | None]:
    """Tightest (min DP, max DP, min RH, max RH) across ASHRAE and a known SLA humidity limit."""
    h = inputs.limits.ashrae_humidity
    min_dp, max_dp, min_rh, max_rh = (
        h.min_dew_point_tenths_c,
        h.max_dew_point_tenths_c,
        h.min_rh_percent,
        h.max_rh_percent,
    )
    sla = inputs.limits.customer_sla
    if isinstance(sla, CustomerSlaLimit) and isinstance(sla.humidity, HumidityLimit):
        sh = sla.humidity
        lo, hi = sh.min_dew_point_tenths_c, sh.max_dew_point_tenths_c
        min_dp = lo if min_dp is None else max(min_dp, lo)
        max_dp = hi if max_dp is None else min(max_dp, hi)
        mr = sh.max_relative_humidity_percent
        max_rh = mr if max_rh is None else min(max_rh, mr)
    return min_dp, max_dp, min_rh, max_rh


def binding_band(inputs: PlantInputs) -> tuple[int, int]:
    """Tighter of the ASHRAE and (known) SLA temperature bands, tenths °C (A4-02)."""
    a = inputs.limits.ashrae.temperature
    lo, hi = a.min_tenths_c, a.max_tenths_c
    sla = inputs.limits.customer_sla
    if isinstance(sla, CustomerSlaLimit):
        lo, hi = max(lo, sla.temperature.min_tenths_c), min(hi, sla.temperature.max_tenths_c)
    return lo, hi


def resolve(inputs: PlantInputs) -> Resolved | Unresolved:
    """Resolve every parameter, or say which are unknown and which combinations are unusable."""
    missing: list[tuple[str, str]] = []
    stand: list[str] = []
    v = {
        k: _value(k, getattr(inputs, k), missing, stand)
        for k in (
            "chiller_count",
            "unit_capacity_kwth",
            "min_part_load_ratio",
            "standby_running",
            "hall_to_loop_ua_kw_per_k",
            "chw_setpoint_c",
            "loop_band_min_c",
            "loop_band_max_c",
            "inlet_baseline_min_c",
            "inlet_baseline_max_c",
            "switch_temperature_c",
        )
    }
    # Anti-cycling times may be unknown: the switch-off strategy is then not offered and
    # recovery uses only the running units (D167 R-c).
    timing: dict[str, int | None] = {}
    for k in ("min_run_min", "min_off_min"):
        p = getattr(inputs, k)
        if isinstance(p, Stated):
            if p.provenance.kind == "stand_in":
                stand.append(k)
            timing[k] = int(p.value)
        else:
            timing[k] = None
    c_loop = _capacitance("loop_capacitance", inputs.loop_capacitance, missing, stand)
    c_hall = _capacitance("hall_capacitance", inputs.hall_capacitance, missing, stand)
    coeffs: list[tuple[float, ...]] = []
    for i, r in enumerate(inputs.regimes):
        if isinstance(r, CoefficientSet):
            if r.provenance.kind == "stand_in":
                stand.append(f"regimes[{i}]")
            coeffs.append(r.values)
        else:
            missing.append((f"regimes[{i}]", r.reason))

    # Humidity: the dew point is invariant through an event (H-a). Unknown operating dew points
    # fall back to the declared dew-point limits when both are declared.
    min_dp, max_dp, min_rh, max_rh = _humidity_bounds(inputs)
    dp: dict[str, float | None] = {}
    for k, fallback in (("dew_point_min_c", min_dp), ("dew_point_max_c", max_dp)):
        p = getattr(inputs, k)
        if isinstance(p, Stated):
            if p.provenance.kind == "stand_in":
                stand.append(k)
            dp[k] = p.value
        elif min_dp is not None and max_dp is not None and fallback is not None:
            dp[k] = fallback / 10.0
        else:
            dp[k] = None
            if (k == "dew_point_min_c" and min_rh is not None) or (
                k == "dew_point_max_c" and max_rh is not None
            ):
                missing.append((k, p.reason))

    if missing:
        return Unresolved(tuple(missing), (), tuple(sorted(set(stand))))

    inconsistent: list[str] = []
    lo_b, hi_b = binding_band(inputs)
    ceiling, floor = hi_b / 10.0, lo_b / 10.0
    td_lo, td_hi = dp["dew_point_min_c"], dp["dew_point_max_c"]
    if td_lo is not None and td_hi is not None and td_lo > td_hi:
        inconsistent.append("dew_point_min_c is above dew_point_max_c")
    if min_dp is not None and td_lo is not None and td_lo < min_dp / 10.0:
        inconsistent.append("the operating dew point is below the declared minimum dew point")
    if max_dp is not None and td_hi is not None and td_hi > max_dp / 10.0:
        inconsistent.append("the operating dew point is above the declared maximum dew point")
    if min_rh is not None and td_lo is not None:
        ceiling = min(ceiling, ceiling_for_min_rh(td_lo, min_rh))
    if max_rh is not None and td_hi is not None:
        floor = max(floor, floor_for_max_rh(td_hi, max_rh))

    rate = inputs.limits.ashrae.max_rate_of_change_tenths_c_per_hour
    if not isinstance(rate, int):
        return Unresolved(
            (("limits.ashrae.max_rate_of_change", rate.reason),), (), tuple(sorted(set(stand)))
        )

    n = int(v["chiller_count"] or 0)
    form_terms = len(coeffs[0])
    padded = np.zeros((len(coeffs), 5))
    for i, c in enumerate(coeffs):
        padded[i, :form_terms] = c
    plant = NumericPlant(
        n_chillers=n,
        unit_kwth=float(v["unit_capacity_kwth"] or 0.0),
        min_plr=float(v["min_part_load_ratio"] or 0.0),
        standby=int(v["standby_running"] or 0),
        min_run_min=timing["min_run_min"],
        min_off_min=timing["min_off_min"],
        curve=PowerCurve(padded, v["switch_temperature_c"]),
        c_loop=float(c_loop or 0.0),
        c_hall=float(c_hall or 0.0),
        ua=float(v["hall_to_loop_ua_kw_per_k"] or 0.0),
        setpoint_c=float(v["chw_setpoint_c"] or 0.0),
        loop_min_c=float(v["loop_band_min_c"] or 0.0),
        loop_max_c=float(v["loop_band_max_c"] or 0.0),
        inlet_lo_c=float(v["inlet_baseline_min_c"] or 0.0),
        inlet_hi_c=float(v["inlet_baseline_max_c"] or 0.0),
        hall_ceiling_c=ceiling,
        hall_floor_c=floor,
        rate_c_per_h=rate / 10.0,
    )
    if n < 1 or plant.unit_kwth <= 0:
        inconsistent.append("chiller_count and unit_capacity_kwth must be positive")
    if not 0.0 <= plant.min_plr < 1.0:
        inconsistent.append("min_part_load_ratio must be in [0, 1)")
    if plant.standby < 0:
        inconsistent.append("standby_running must be non-negative")
    if min(plant.c_loop, plant.c_hall, plant.ua) <= 0:
        inconsistent.append("capacitances and UA must be positive")
    if not plant.loop_min_c <= plant.setpoint_c <= plant.loop_max_c:
        inconsistent.append("chw_setpoint_c is outside its band")
    if plant.inlet_lo_c > plant.inlet_hi_c:
        inconsistent.append("inlet_baseline_min_c is above inlet_baseline_max_c")
    if plant.inlet_hi_c > plant.hall_ceiling_c or plant.inlet_lo_c < plant.hall_floor_c:
        inconsistent.append(
            "the baseline IT inlet band is already outside the binding limits (band and humidity)"
        )
    if plant.coefficients_negative_b4():
        inconsistent.append("b4 < 0: the power curve is concave in load (D164)")
    if (
        timing["min_run_min"] is not None
        and timing["min_off_min"] is not None
        and min(timing["min_run_min"], timing["min_off_min"]) < 0
    ):
        inconsistent.append("min_run_min and min_off_min must be non-negative")
    if inconsistent:
        return Unresolved((), tuple(inconsistent), tuple(sorted(set(stand))))
    return Resolved(plant, tuple(sorted(set(stand))))


def curve_validity(
    plant: NumericPlant, t_lo: float, t_hi: float, x_hi: float | None = None
) -> str | None:
    """Why the power curve cannot be used on ``[0, x_hi] x [t_lo, t_hi]``, or ``None``
    (D164): ``P > 0``, ``dP/dx > 0`` at the corners, ``dP/dT >= 0`` at both load
    ends, per regime the box touches."""
    xmax = plant.capacity_kwth if x_hi is None else x_hi
    xs = np.array([0.0, xmax])
    for t, reg, mask in plant.curve.corners(np.array([t_lo]), np.array([t_hi])):
        if not mask[0]:
            continue
        for x in xs:
            p = plant.curve.power(np.array(x), t, reg)[0]
            if not p > 0:
                return f"power curve gives P = {p:.1f} kW <= 0 at x = {x:g} kWth, T = {t[0]:g} °C"
            g = plant.curve.dp_dx(np.array(x), t, reg)[0]
            if not g > 0:
                return f"power curve dP/dx = {g:.4g} <= 0 at x = {x:g} kWth, T = {t[0]:g} °C"
            h = plant.curve.dp_dt(np.array(x), reg)[0]
            if h < 0:
                return f"power curve dP/dT = {h:.4g} < 0 at x = {x:g} kWth (regime {reg[0]})"
    return None


def max_slope(plant: NumericPlant, t_lo: float, t_hi: float) -> float:
    """``max dP/dx`` over ``[0, capacity] x [t_lo, t_hi]`` (a Lipschitz bound in load)."""
    best = 0.0
    for t, reg, mask in plant.curve.corners(np.array([t_lo]), np.array([t_hi])):
        if mask[0]:
            for x in (0.0, plant.capacity_kwth):
                best = max(best, float(plant.curve.dp_dx(np.array(x), t, reg)[0]))
    return best


def max_power(plant: NumericPlant, t_lo: float, t_hi: float) -> float:
    """``max P`` at full extraction over ``[t_lo, t_hi]`` (the cooling electrical capacity)."""
    best = 0.0
    for t, reg, mask in plant.curve.corners(np.array([t_lo]), np.array([t_hi])):
        if mask[0]:
            best = max(best, float(plant.curve.power(np.array(plant.capacity_kwth), t, reg)[0]))
    return best


def settle_minutes(plant: NumericPlant, d0_max: float) -> int:
    """Minutes for an imbalance ``|d| <= d0_max`` to decay to `NumericPlant.settle_eps`."""
    eps = plant.settle_eps
    if d0_max <= eps:
        return 0
    return math.ceil(math.log(d0_max / eps) / plant.lam / 60.0)
