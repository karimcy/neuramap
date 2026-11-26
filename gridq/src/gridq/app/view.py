"""Presentation glue for the Wave-5 UI screens.

This module sits between the deterministic outputs layer and the Jinja templates. It does **not**
price anything: every financial figure is read straight off the `OutputViewModel`
(`gridq.outputs.build_view_model`) and the persisted `QuoteRecord.sizing_grid`, and is turned into a
string only by `gridq.outputs.formatting`. The only arithmetic here is **presentation geometry** for
the charts (axis ranges / which metric to plot) — never the re-derivation of a financial number.

Two responsibilities:

1. **Jinja globals** (`JINJA_GLOBALS`): the `formatting` helpers + a couple of None-aware wrappers,
   so templates interpolate finished strings (e.g. `fmt_gbp(...)`) and never compute.
2. **Chart islands** (`surface_island`, `finance_island`): JSON-serializable dicts carrying the raw
   record values (for the client renderer's pixel geometry) **and** pre-formatted label strings (for
   any text the renderer shows), so the WebGL/canvas code positions and draws but never formats or
   computes a financial figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from gridq.outputs import formatting as fmt

if TYPE_CHECKING:  # avoid importing heavy schema modules at app-import time
    from gridq.outputs import OutputViewModel
    from gridq.schemas.dispatch_profile import DispatchProfile
    from gridq.schemas.sizing import SizingGrid


def fmt_opt(value: object, formatter: Any, *, reason: str = "not available") -> str:
    """Format ``value`` with ``formatter``; ``None`` → the canonical unavailable string."""
    if value is None:
        return fmt.unavailable(reason)
    return str(formatter(value))


#: Callables exposed to templates as globals (registered on the Jinja env in ``app.py``). Templates
#: call these to render record values — they are the *only* number→string path the UI uses.
JINJA_GLOBALS: dict[str, Any] = {
    "fmt_gbp": fmt.gbp,
    "fmt_gbp_compact": fmt.gbp_compact,
    "fmt_gbp_per_kwh": fmt.gbp_per_kwh,
    "fmt_pct": fmt.pct,
    "fmt_ratio": fmt.ratio,
    "fmt_kw": fmt.kw,
    "fmt_kwh": fmt.kwh,
    "fmt_hours": fmt.hours,
    "fmt_years": fmt.years,
    "fmt_date": fmt.date_str,
    "fmt_yes_no": fmt.yes_no,
    "fmt_pass_fail": fmt.pass_fail,
    "fmt_stream": fmt.stream_label,
    "fmt_structure": fmt.structure_label,
    "fmt_titleise": fmt.titleise,
    "fmt_unavailable": fmt.unavailable,
    "fmt_placeholder": fmt.placeholder,
    "fmt_opt": fmt_opt,
}


def _point_tooltip(
    power_kw: float,
    duration_hours: float,
    z_label: str,
    z_value: float | None,
    project_irr_pct: float | None,
    dscr: float | None,
    passes: bool | None,
) -> str:
    """Pre-formatted (server-side) multiline tooltip for one surface point — no JS formatting."""
    lines = [
        f"{fmt.kw(power_kw)} × {fmt.hours(duration_hours)}",
        f"{z_label}: {fmt_opt(z_value, fmt.pct)}",
    ]
    if z_label != "Project IRR":
        lines.append(f"Project IRR: {fmt_opt(project_irr_pct, fmt.pct)}")
    lines.append(f"P90 DSCR: {fmt_opt(dscr, fmt.ratio)}")
    lines.append(f"Covenant: {fmt.pass_fail(passes)}")
    return "\n".join(lines)


#: Resolution of the resampled mesh (per axis). The evaluated grid is coarse (3x3); resampling it
#: onto this many points is what makes the surface and heat-map read as a continuous field rather
#: than a handful of facets. Pure display geometry, deterministic, invents no record value.
_DENSE_STEPS = 40


def _linspace(a: float, b: float, n: int) -> list[float]:
    """``n`` evenly spaced values from ``a`` to ``b`` inclusive (chart-axis geometry)."""
    if n < 2 or b <= a:
        return [a]
    step = (b - a) / (n - 1)
    return [a + step * i for i in range(n)]


def _bracket(xs: list[float], x: float) -> tuple[int, int, float]:
    """Locate ``x`` in ascending ``xs``: bounding indices ``(i0, i1)`` and fraction ``t`` in [0, 1].

    Clamps to the ends (``t=0``) outside the range, so the resample never extrapolates past the
    evaluated grid. Used for rectilinear bilinear interpolation on a non-uniform axis.
    """
    if x <= xs[0]:
        return 0, 0, 0.0
    if x >= xs[-1]:
        last = len(xs) - 1
        return last, last, 0.0
    lo = 0
    while lo + 1 < len(xs) and xs[lo + 1] < x:
        lo += 1
    x0, x1 = xs[lo], xs[lo + 1]
    t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    return lo, lo + 1, t


def _bilinear(
    xs: list[float],
    ys: list[float],
    grid: Sequence[Sequence[float | None]],
    x: float,
    y: float,
) -> float | None:
    """Bilinearly sample ``grid[iy][ix]`` at ``(x, y)``; ``None`` if any bounding corner is None.

    A real hole in the evaluated grid stays a hole (never filled with an invented value); over the
    populated demo grid this yields a smooth, continuous field. Pure geometry, no record number is
    created here, only blended for display.
    """
    ix0, ix1, tx = _bracket(xs, x)
    iy0, iy1, ty = _bracket(ys, y)
    c00, c01 = grid[iy0][ix0], grid[iy0][ix1]
    c10, c11 = grid[iy1][ix0], grid[iy1][ix1]
    if c00 is None or c01 is None or c10 is None or c11 is None:
        return None
    top = c00 * (1.0 - tx) + c01 * tx
    bot = c10 * (1.0 - tx) + c11 * tx
    return top * (1.0 - ty) + bot * ty


def _dense_surface(
    powers: list[float],
    durations: list[float],
    z_matrix: Sequence[Sequence[float | None]],
    feasible_matrix: Sequence[Sequence[float]],
) -> dict[str, Any]:
    """Resample the coarse evaluated grid onto a dense mesh for the continuous surface + heat-map.

    Returns ``{"dense": False}`` when the grid is too sparse to interpolate (fewer than three
    evaluated returns, or a single power/duration) so the client keeps the coarse fallback.

    The evaluated grid is partial: short-duration or otherwise non-viable sizes produce **no**
    return (``None``). To draw one continuous sheet we floor-fill those empty cells to the
    **worst observed return** before resampling, rather than harmonic-filling toward neighbours
    (which would overstate the non-viable region). The fill is display geometry only: a None cell
    reads as "no better than the worst viable size", never as a fabricated figure, and the true
    per-candidate values stay authoritative in the markers and the grid table. ``holes_filled`` and
    ``z_floor`` let the screen disclose how many cells were floored.
    """
    if len(powers) < 2 or len(durations) < 2:
        return {"dense": False}
    known = [v for row in z_matrix for v in row if v is not None]
    if len(known) < 3:
        return {"dense": False}
    floor = min(known)
    holes = sum(1 for row in z_matrix for v in row if v is None)
    filled: list[list[float]] = [[(floor if v is None else v) for v in row] for row in z_matrix]
    dense_powers = _linspace(powers[0], powers[-1], _DENSE_STEPS)
    dense_durations = _linspace(durations[0], durations[-1], _DENSE_STEPS)
    dense_z: list[list[float | None]] = []
    dense_feasible: list[list[float]] = []
    for dy in dense_durations:
        z_row: list[float | None] = []
        f_row: list[float] = []
        for dx in dense_powers:
            z_row.append(_bilinear(powers, durations, filled, dx, dy))
            blended = _bilinear(powers, durations, feasible_matrix, dx, dy)
            f_row.append(0.0 if blended is None else blended)
        dense_z.append(z_row)
        dense_feasible.append(f_row)
    return {
        "dense": True,
        "dense_powers": dense_powers,
        "dense_durations": dense_durations,
        "dense_z": dense_z,
        "dense_feasible": dense_feasible,
        "z_floor": floor,
        "holes_filled": holes,
    }


#: Stages whose points form the REGULAR lattice the mesh interpolates over. The legacy 3x3
#: enumeration and the w6 dense reference stamp "grid"; the two-stage coarse sweep stamps
#: "coarse". Refine/confirm evaluations land BETWEEN lattice cells (a continuous search), so
#: they render as markers over the sheet rather than distorting the rectangular mesh.
_MESH_STAGES = frozenset({"grid", "coarse"})

#: Cell-preference rank when the same (power, duration) was evaluated more than once (a
#: two-stage cloud re-solves candidates): a full-series (non-proxy) value always beats a
#: typical-day proxy value, then the later stage wins. D32's UI rule — prefer ``confirm``.
_STAGE_RANK = {"grid": 0, "coarse": 0, "refine": 1, "confirm": 2}


def _cell_rank(point: Any) -> tuple[int, int]:
    return (0 if point.proxy else 1, _STAGE_RANK.get(point.stage, 0))


def surface_island(sizing_grid: SizingGrid | None) -> dict[str, Any]:
    """Build the 3D sizing-surface data island from the persisted grid (raw + pre-formatted).

    The z-axis is the levered (equity) return where available, else the unlevered project IRR
    (mirrors `SizingGridPoint`'s documented fallback); when neither is available for any point the
    surface is flagged ``has_surface=False`` and the screen shows the honest grid table instead.

    Consumes every persisted cloud shape as-persisted (WS4): the legacy ``enumerate-v1`` 3x3
    grid, the stage-labelled ``m4-two-stage-v2`` cloud (mesh from the coarse sweep; refine /
    confirm evaluations as markers; a confirmed value beats a proxy value on a shared cell,
    per D32 — no reported figure ever comes from a proxy when a confirmed one exists), and the
    ``w6-*-v1`` dense reference surfaces (complete regular grids, consumed directly).
    """
    if sizing_grid is None or not sizing_grid.points:
        return {"available": False}

    pts = sizing_grid.points
    has_levered = any(p.levered_return_pct is not None for p in pts)
    z_field = "levered_return_pct" if has_levered else "project_irr_pct"
    z_label = "Levered return" if has_levered else "Project IRR"
    has_surface = any(getattr(p, z_field) is not None for p in pts)

    # The rectangular mesh comes from the lattice stages only; a continuous cloud's refine /
    # confirm points sit between cells and would explode the axes into a mostly-empty matrix.
    mesh_pts = [p for p in pts if p.stage in _MESH_STAGES] or pts
    powers = sorted({p.power_kw for p in mesh_pts})
    durations = sorted({p.duration_hours for p in mesh_pts})
    sel = sizing_grid.selected
    n_proxy = sum(1 for p in pts if p.proxy)
    n_confirm = sum(1 for p in pts if p.stage == "confirm")

    # Plotly's `surface` trace needs a rectangular z grid [duration][power] plus a parallel
    # feasibility grid for `surfacecolor` (so the P90-DSCR-feasible region is a distinct colour
    # band, not just point colours). Both carry RAW record values (z) / flags — geometry, not a
    # figure. Gaps stay ``None`` (Plotly renders a hole); feasibility is 1.0 only where the covenant
    # is met, else 0.0 (None/not-evaluated reads as not-feasible — never coerced to a pass).
    # Duplicate evaluations of one cell resolve by `_cell_rank`: confirmed beats proxy.
    by_cell: dict[tuple[float, float], Any] = {}
    for p in pts:
        key = (p.power_kw, p.duration_hours)
        cur = by_cell.get(key)
        if cur is None or _cell_rank(p) > _cell_rank(cur):
            by_cell[key] = p
    z_matrix: list[list[float | None]] = []
    feasible_matrix: list[list[float]] = []
    for d in durations:
        z_row: list[float | None] = []
        f_row: list[float] = []
        for kw_val in powers:
            cell = by_cell.get((kw_val, d))
            z_row.append(None if cell is None else getattr(cell, z_field))
            f_row.append(1.0 if (cell is not None and cell.passes_covenant) else 0.0)
        z_matrix.append(z_row)
        feasible_matrix.append(f_row)

    points = []
    for p in pts:
        z_value = getattr(p, z_field)
        tooltip = _point_tooltip(
            p.power_kw,
            p.duration_hours,
            z_label,
            z_value,
            p.project_irr_pct,
            p.min_p90_dscr,
            p.passes_covenant,
        )
        if p.proxy:
            tooltip = f"{tooltip}\nTypical-day proxy: guides the search, never a reported figure"
        elif p.stage == "confirm":
            tooltip = f"{tooltip}\nConfirmed on the full metered series"
        points.append(
            {
                "power_kw": p.power_kw,
                "duration_hours": p.duration_hours,
                "z": z_value,
                "passes_covenant": p.passes_covenant,
                "priceable": p.priceable,
                "stage": p.stage,
                "proxy": p.proxy,
                "is_selected": bool(
                    sel is not None
                    and p.power_kw == sel.power_kw
                    and p.duration_hours == sel.duration_hours
                ),
                "tooltip": tooltip,
            }
        )
    # Colour range for the surface + heat-map: the actual span of evaluated returns (raw record
    # values), so both panels share one scale. None when no point carries a return.
    z_values = [getattr(p, z_field) for p in pts if getattr(p, z_field) is not None]
    z_min = min(z_values) if z_values else None
    z_max = max(z_values) if z_values else None
    sel_z = None if sel is None else getattr(sel, z_field)
    # Honest provenance for the caption: which search produced this surface, and how much of
    # it is search guidance (proxy) vs full-series confirmed evaluation.
    if n_proxy or n_confirm:
        provenance = (
            f"{sizing_grid.search_version}: {len(pts)} evaluations "
            f"({n_confirm} confirmed on the full series, {n_proxy} typical-day proxy; "
            "proxy values guide the search and are never the reported size)"
        )
    else:
        provenance = f"{sizing_grid.search_version}: {len(pts)} evaluated sizes"
    return {
        "available": True,
        "has_surface": has_surface,
        "z_label": z_label,
        "search_version": sizing_grid.search_version,
        "n_points": len(pts),
        "n_proxy": n_proxy,
        "n_confirm": n_confirm,
        "provenance": provenance,
        "powers": powers,
        "durations": durations,
        "power_labels": [fmt.kw(v) for v in powers],
        "duration_labels": [fmt.hours(v) for v in durations],
        "z_matrix": z_matrix,
        "feasible_matrix": feasible_matrix,
        "z_min": z_min,
        "z_max": z_max,
        "points": points,
        "covenant": sizing_grid.covenant,
        "covenant_label": fmt_opt(sizing_grid.covenant, fmt.ratio),
        "selected": (
            None
            if sel is None
            else {
                "power_kw": sel.power_kw,
                "duration_hours": sel.duration_hours,
                "z": sel_z,
                "label": f"{fmt.kw(sel.power_kw)} × {fmt.hours(sel.duration_hours)}",
            }
        ),
        **_dense_surface(powers, durations, z_matrix, feasible_matrix),
    }


def hero_surface_island(sizing_grid: SizingGrid | None) -> dict[str, Any]:
    """Project the sizing-surface island down to what the landing's WebGL hero draws.

    A thin field selection over :func:`surface_island` - no new values are computed here.
    The hero needs only the dense mesh (its axes + z sheet) and the selected optimum; when
    the persisted grid is missing or too sparse to interpolate the island reports
    ``available=False`` and the landing keeps its static SVG fallback. Nothing is ever
    invented for the hero (the F-011 rule).
    """
    s = surface_island(sizing_grid)
    if not (s.get("available") and s.get("has_surface") and s.get("dense")):
        return {"available": False, "dense": False, "has_surface": False}
    return {
        "available": True,
        "dense": True,
        "has_surface": True,
        "z_label": s["z_label"],
        "dense_powers": s["dense_powers"],
        "dense_durations": s["dense_durations"],
        "dense_z": s["dense_z"],
        "selected": s["selected"],
    }


def finance_island(vm: OutputViewModel) -> dict[str, Any]:
    """Build the 2D finance-chart island (P50/P90 cashflows + DSCR-by-year): raw + pre-formatted."""
    cf = vm.cashflows
    dscr = vm.dscr
    cashflows: dict[str, Any] = {"available": bool(cf.available)}
    if cf.available:
        # Pre-zipped rows so the template's accessible fallback table needs no indexing/arithmetic.
        rows = [
            {"year": str(i), "p50": fmt.gbp(p50), "p90": fmt.gbp(p90)}
            for i, (p50, p90) in enumerate(zip(cf.p50, cf.p90, strict=False))
        ]
        cashflows.update(
            {
                "p50": list(cf.p50),
                "p90": list(cf.p90),
                "rows": rows,  # year 0 is the capex year; the rest are operating years.
                # The record's payback year (raw + pre-formatted) so the chart can mark it;
                # a field selection off the view model, never derived from the series here.
                "payback_years": vm.returns.payback_years,
                "payback_label": fmt_opt(vm.returns.payback_years, fmt.years),
            }
        )
    dscr_island: dict[str, Any] = {"available": bool(dscr.available)}
    if dscr.available and dscr.by_year:
        dscr_rows = [
            {"year": str(i), "value": fmt.ratio(v)} for i, v in enumerate(dscr.by_year, start=1)
        ]
        dscr_island.update(
            {
                "by_year": list(dscr.by_year),
                "rows": dscr_rows,
                "covenant": dscr.covenant,
                "covenant_label": fmt_opt(dscr.covenant, fmt.ratio),
                "basis": dscr.basis,
            }
        )
    return {"cashflows": cashflows, "dscr": dscr_island}


def _hhmm(period: int, interval_hours: float) -> str:
    """A clock label (``HH:MM``) for a half-hour index — chart-axis geometry, not a figure."""
    total_min = int(round(period * interval_hours * 60))
    hour, minute = divmod(total_min, 60)
    return f"{hour % 24:02d}:{minute:02d}"


def dispatch_island(profile: DispatchProfile | None) -> dict[str, Any]:
    """Build the half-hourly dispatch-chart island from the persisted representative day.

    Raw per-period series (charge/discharge/SoE/load — the renderer's pixel geometry) plus
    pre-formatted clock labels, hover strings and a fallback-table row set (so the chart's text and
    the no-JS table are formatted server-side, never in the browser). ``{"available": False}`` when
    no dispatch was persisted (no metered HH series / no spec) — the screen shows the honest seam.
    """
    if profile is None or not profile.points:
        return {"available": False}

    pts = profile.points
    interval = profile.interval_hours
    labels = [_hhmm(p.period, interval) for p in pts]
    rows = [
        {
            "time": labels[i],
            "charge": fmt.kw(p.charge_kw),
            "discharge": fmt.kw(p.discharge_kw),
            "soe": fmt.kwh(p.soe_kwh),
            "load": fmt.kw(p.site_load_kw),
            "red": p.red_band,
        }
        for i, p in enumerate(pts)
    ]
    return {
        "available": True,
        "labels": labels,
        "charge_kw": [p.charge_kw for p in pts],
        "discharge_kw": [p.discharge_kw for p in pts],
        "soe_kwh": [p.soe_kwh for p in pts],
        "site_load_kw": [p.site_load_kw for p in pts],
        "red_band": [p.red_band for p in pts],
        # Per-trace pre-formatted hover text (the renderer shows these verbatim — formats nothing).
        "charge_hover": [f"{labels[i]} · charge {r['charge']}" for i, r in enumerate(rows)],
        "discharge_hover": [
            f"{labels[i]} · discharge {r['discharge']}" for i, r in enumerate(rows)
        ],
        "soe_hover": [f"{labels[i]} · SoE {r['soe']}" for i, r in enumerate(rows)],
        "load_hover": [f"{labels[i]} · site load {r['load']}" for i, r in enumerate(rows)],
        "rows": rows,
        "power_label": fmt.kw(profile.power_kw),
        "energy_label": fmt.kwh(profile.energy_kwh),
        "duration_label": fmt.hours(profile.duration_hours),
        "basis_note": (
            f"Representative day ({profile.selection_basis}) of the metered horizon - a real "
            "solved day, illustrative of the dispatch strategy; not an average over days."
        ),
    }


def utilisation_island(profile: DispatchProfile | None) -> dict[str, Any]:
    """How the battery spends its time over the representative dispatch day, as a breakdown.

    Counts each half-hour into a mode (charging, discharging in the peak red band, discharging
    off-peak, or idle/standby) straight off the persisted representative-day dispatch, and returns
    the share of each mode. It is the engine's own solved day, illustrative of the annual pattern;
    no value is invented. ``{"available": False}`` when no dispatch was solved.
    """
    if profile is None or not profile.points:
        return {"available": False}
    pts = profile.points
    total = len(pts)
    charging = sum(1 for p in pts if p.charge_kw > 0)
    peak_discharge = sum(1 for p in pts if p.discharge_kw > 0 and p.red_band)
    off_discharge = sum(1 for p in pts if p.discharge_kw > 0 and not p.red_band)
    idle = sum(1 for p in pts if p.charge_kw <= 0 and p.discharge_kw <= 0)

    def share(count: int) -> str:
        return f"{(100.0 * count / total):.1f}%" if total else "0%"

    raw = [
        ("Charging", charging, "off-peak energy stored"),
        ("Discharging at peak", peak_discharge, "red-band peak shaving and DUoS avoidance"),
        ("Discharging off-peak", off_discharge, "tariff arbitrage"),
        ("Idle / standby", idle, "available, not cycling"),
    ]
    segments = [
        {"label": label, "count": count, "pct": share(count), "note": note}
        for label, count, note in raw
        if count > 0
    ]
    return {
        "available": True,
        "segments": segments,
        "total_periods": total,
        "basis": (
            f"representative day ({profile.selection_basis})"
            if profile.selection_basis
            else "representative day"
        ),
    }


def value_stack_island(vm: OutputViewModel) -> dict[str, Any]:
    """Build the firm-vs-gated value-stack chart island from the view-model's split.

    Each stream becomes a stacked-bar segment under its category (priced floor / gated upside),
    carrying the raw ``annual_revenue_gbp`` (bar length) and pre-formatted £ / stream labels. The
    totals are the view-model's own sums (`ContractedMerchantSplit`); nothing is summed here.
    """
    split = vm.split
    floor_segments = [
        {
            "category": "Priced floor",
            "kind": "floor",
            "stream": fmt.stream_label(r.stream),
            "revenue": r.annual_revenue_gbp,
            "revenue_label": fmt.gbp(r.annual_revenue_gbp),
        }
        for r in split.floor
    ]
    upside_segments = [
        {
            "category": "Gated upside",
            "kind": "upside",
            "stream": fmt.stream_label(r.stream),
            "revenue": r.annual_revenue_gbp,
            "revenue_label": fmt.gbp(r.annual_revenue_gbp),
        }
        for r in split.upside
    ]
    return {
        "available": bool(floor_segments or upside_segments),
        "segments": floor_segments + upside_segments,
        "floor_total": split.floor_total_gbp,
        "floor_total_label": fmt.gbp(split.floor_total_gbp),
        "upside_total": split.upside_total_gbp,
        "upside_total_label": fmt.gbp(split.upside_total_gbp),
    }
