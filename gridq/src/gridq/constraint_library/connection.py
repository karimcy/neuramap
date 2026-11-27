"""Connection and power-rating limits: the import limit, the export limit and the kVA polygons.

Constraints:

- ``import_limit`` — ``charge_t ≤ min(power, max(0, min(MIC, ASC)·pf − net_load_t − aux − add_t))``
  on the SIGNED net position (D-174), optionally de-rated by a release power factor (B-17);
- ``discharge_rating`` — ``discharge_t ≤ min(power − forced_t, load_t)`` (behind the meter);
- ``export_limit`` — ``export_t ≤ min(power, MEC)`` and the shared inverter row (issue 018 / D43);
- ``connection_kva_polygon`` — the connection's kVA limit as the inscribed octagon in active import
  and residual reactive power, where the site meters both reactive channels (WP-UW-07, D106);
- ``inverter_pq_octagon`` — the inverter's P-Q capability as its inscribed octagon (D106).

Moved verbatim from ``gridq.optimisation.milp_solver`` (WP-SIZE-02). Pure: numbers and arrays in,
bounds and linopy rows out; the caller reads the site and the release.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: Release key, NOT a constant (B-17). Real C&I sites run 0.90–0.95 PF, but no site in this
#: schema carries a reactive/kVArh series unless its file meters both channels (WP-UW-07), so the
#: solver will not invent one: absent key = today's kVA≈kW at unity PF, stated as a labelled
#: warning plus a data ask.
POWER_FACTOR_KEY = "mic_asc_headroom.assumed_power_factor"

#: The INSCRIBED regular octagon of a P-Q circle of radius S: ``|P| + k|Q| <= S`` and
#: ``k|P| + |Q| <= S`` with ``k = sqrt(2) - 1``. Every vertex lies on the circle, so the octagon
#: never claims capability (or connection headroom) the circle does not have. Geometry, not data.
OCTAGON_K = math.sqrt(2.0) - 1.0


def import_limit_kw(mic_kw: float | None, asc_kw: float | None) -> float | None:
    """The DECLARED physical import limit ``min(MIC, ASC)`` over the positive values, or ``None``.

    One convention for the sizer's search box (`two_stage.connection_power_limit_kw`), the annual
    MILP's charge bound and the MPC's applied-charge clip and binding report, so all three agree
    about what the connection supports (N7). The observed peak is never a fallback: it is a fact
    about the load.
    """
    limits = [float(v) for v in (mic_kw, asc_kw) if v is not None and v > 0]
    return min(limits) if limits else None


def resolve_charge_power_factor(pf: float | None) -> tuple[float, str]:
    """The assumed power factor de-rating the kW charge bound against a kVA connection (B-17).

    ``pf`` is the release's ``mic_asc_headroom.assumed_power_factor`` (``None`` when absent).
    Returns ``(power_factor, warning)``. ``1.0`` — today's behaviour — whenever the release carries
    no usable sourced factor, because a power factor is never invented here. The warning states the
    exposure and asks for the data.
    """
    if pf is not None and 0.0 < pf <= 1.0:
        return pf, (
            f"charge headroom de-rated to kVA at power factor {pf:g} (release key "
            f"{POWER_FACTOR_KEY}): the connection limits kVA, not kW, so the usable kW bound is "
            f"min(MIC, ASC) x {pf:g} (B-17). Still an ASSUMED factor, not a metered kVArh series."
        )
    if pf is not None:
        return 1.0, (
            f"{POWER_FACTOR_KEY} = {pf:g} is not a usable power factor (need 0 < pf <= 1) — NOT "
            "applied; the unity-PF bound stands (never silently corrected)."
        )
    return 1.0, (
        "UNITY POWER FACTOR ASSUMED (B-17, data ask): no site in this schema carries a reactive / "
        "kVArh half-hourly series, so kVA is taken as equal to kW throughout — the charge bound "
        "against min(MIC, ASC), the capacity/availability charge on the customer bill, and the "
        "exceeded-capacity headroom. Real C&I sites run 0.90-0.95 PF, so TRUE kVA exceeds the kW "
        "this model uses by ~5-11% and charging at full kW near the MIC can itself trigger the "
        "exceeded-capacity charge this model reports as impossible (DUoS exceeded capacity = "
        "max(2*sqrt(AI^2 + max(RI,RE)^2) - MIC, 0) per half hour, charged for the whole billing "
        "period; excess reactive above 33% of kWh = PF 0.95 is separately chargeable - "
        "docs/bess_constants_register.md, LPN Use of System Charging Statement 2026 paras "
        "2.41/2.48-2.53). No power factor is invented here: supply a metered kVArh series, or a "
        f"sourced {POWER_FACTOR_KEY}, to price the reactive exposure. A file carrying both "
        "reactive channels (reactive_import_kvarh, reactive_export_kvarh) replaces this assumption "
        "with the modelled reactive variable (WP-UW-07)."
    )


def charge_upper_bound_kw(
    power: float,
    charge_conn_limit: float,
    net_load: np.ndarray,
    aux_kw: float,
    addition_kw: np.ndarray | None = None,
) -> np.ndarray:
    """The import limit as the per-instant charge bound (D-174, B-17, issue 017 / D42).

    Headroom is measured against the SIGNED net position: during a PV surplus the wire carries
    energy the other way, so the headroom is genuinely larger than ``conn_limit − load``. The
    parasitic draw ``aux_kw`` and a declared load-service addition ``addition_kw`` (inside its
    windows) occupy import capacity on top of the site load. ``None`` addition is the
    no-obligation model, byte-identical to the pre-D42 bound.
    """
    if addition_kw is None:
        return np.minimum(power, np.maximum(0.0, charge_conn_limit - net_load - aux_kw))
    return np.minimum(power, np.maximum(0.0, charge_conn_limit - net_load - aux_kw - addition_kw))


def discharge_upper_bound_kw(
    power: float, load: np.ndarray, forced_kw: np.ndarray | None = None
) -> np.ndarray:
    """The behind-the-meter discharge bound ``min(power − forced_t, load_t)`` on the CLIPPED load.

    There is no site load to displace while the site is net-exporting (export past the boundary is
    the gated-upside overlay's own variable). A forced load-service draw ``forced_kw`` shares the
    inverter with priced discharge; ``None`` is the no-obligation bound ``min(power, load_t)``.
    """
    if forced_kw is None:
        return np.minimum(power, load)
    return np.minimum(np.maximum(power - forced_kw, 0.0), load)


def export_cap_kw(power: float, mec_kw: float) -> float:
    """The export limit ``min(power, MEC)``, floored at zero (issue 018 / D43, STRUCTURAL)."""
    return float(np.clip(min(power, mec_kw), 0.0, None))


def add_export_inverter_row(
    model: Any,
    export_flow: Any,
    power: float,
    forced_kw: np.ndarray | None,
    n: int,
    coords: Any,
) -> None:
    """Shared inverter: BTM discharge plus past-boundary export cannot together exceed the rated
    power net of any forced load-service draw (``None`` = no obligation)."""
    import xarray as xr

    inverter_cap = (
        np.maximum(power - forced_kw, 0.0) if forced_kw is not None else np.full(n, power)
    )
    model.add_constraints(
        export_flow <= xr.DataArray(inverter_cap, coords=[coords]), name="export_inverter"
    )


def reactive_connection_screen(
    base_kw: np.ndarray,
    charge_ub: np.ndarray,
    site_kvar: np.ndarray,
    limit_kva: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Where the kVA connection polygon needs a row, and where charging must be barred.

    Returns ``(charge_ub, row_mask, n_outside)``. ``base_kw`` is the active import with the battery
    idle. Where the metered site ALREADY sits outside the inscribed polygon of ``limit_kva`` (a
    real breach the battery did not cause), charging is barred and no row is built, which keeps the
    solve feasible without relaxing anything. Elsewhere a row is built only where full charging
    could carry the point outside, so a site well inside its connection adds no row at all.
    """
    k = OCTAGON_K
    p0 = np.abs(base_kw)
    outside = (p0 + k * site_kvar > limit_kva + 1e-9) | (k * p0 + site_kvar > limit_kva + 1e-9)
    capped = np.where(outside, 0.0, charge_ub)
    reach = base_kw + capped
    rows = (~outside) & (site_kvar > 0.0)
    rows &= (reach + k * site_kvar > limit_kva) | (k * reach + site_kvar > limit_kva)
    return capped, rows, int(outside.sum())


def add_inverter_pq_rows(
    model: Any,
    charge: Any,
    export_flow: Any,
    comp: Any,
    power: float,
    forced_kw: np.ndarray,
    mask: np.ndarray,
    coords: Any,
) -> None:
    """The inverter's P-Q capability as its inscribed octagon, where reactive is supplied.

    ``A_t = discharge_t (+ export_t) + charge_t + forced_t >= |P_t|`` (at most one of charge and
    discharge is non-zero at a vertex, so ``A_t`` is the inverter's real power), and
    ``A_t + k c_t <= S``, ``k A_t + c_t <= S`` with ``S`` the rated kW taken as the inverter's kVA.
    """
    import xarray as xr

    k = OCTAGON_K
    m = xr.DataArray(mask, coords=[coords])
    model.add_constraints(
        export_flow + charge + k * comp <= xr.DataArray(power - forced_kw, coords=[coords]),
        name="reactive_inverter_octagon_p",
        mask=m,
    )
    model.add_constraints(
        k * export_flow + k * charge + comp <= xr.DataArray(power - k * forced_kw, coords=[coords]),
        name="reactive_inverter_octagon_q",
        mask=m,
    )


def add_connection_kva_rows(
    model: Any,
    charge: Any,
    discharge: Any,
    comp: Any,
    base_kw: np.ndarray,
    site_kvar: np.ndarray,
    limit_kva: float,
    mask: np.ndarray,
    coords: Any,
) -> None:
    """The connection's kVA limit as the inscribed polygon in active import and residual reactive.

    ``AI_t = base_t + charge_t - discharge_t`` and ``r_t = m_t - c_t``:
    ``AI_t + k r_t <= L`` and ``k AI_t + r_t <= L`` with ``L = min(MIC, ASC)``. Replaces the scalar
    power-factor de-rating whenever reactive is modelled (never both).
    """
    import xarray as xr

    k = OCTAGON_K
    m = xr.DataArray(mask, coords=[coords])
    model.add_constraints(
        charge - discharge - k * comp
        <= xr.DataArray(limit_kva - base_kw - k * site_kvar, coords=[coords]),
        name="reactive_connection_kva_p",
        mask=m,
    )
    model.add_constraints(
        k * charge - k * discharge - comp
        <= xr.DataArray(limit_kva - k * base_kw - site_kvar, coords=[coords]),
        name="reactive_connection_kva_q",
        mask=m,
    )
