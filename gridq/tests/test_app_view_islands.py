"""W5-2: the view-layer chart islands carry raw record values + pre-formatted labels only.

The islands are the seam between the run record and the client (Plotly) renderer: they pass RAW
record values (the renderer's pixel geometry) and PRE-FORMATTED strings (every bit of visible text),
so the browser positions/draws but never computes or formats a figure. These tests pin that the new
W5-2 islands — dispatch, value-stack, and the surface's Plotly z/feasibility grids — are populated
from a real run, are ``None``/empty-safe, and invent nothing.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.app import view
from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, DemoEngine, load_demo_site
from gridq.assumptions import load_assumptions
from gridq.outputs import build_view_model
from gridq.quotes import generate_quote
from gridq.schemas import QuoteMode


def _demo_run():
    site = load_demo_site()
    return generate_quote(
        site,
        load_assumptions("gb_v0_1"),
        engine=DemoEngine(),
        debt_terms=DEMO_DEBT_TERMS,
        quote_mode=QuoteMode.INDICATIVE,
        extra_warnings=[DEMO_LABEL],
    )


# --- dispatch island --------------------------------------------------------


def test_dispatch_island_none_is_unavailable() -> None:
    assert view.dispatch_island(None) == {"available": False}


def test_dispatch_island_from_demo_is_populated_and_consistent() -> None:
    run = _demo_run()
    island = view.dispatch_island(run.quote.dispatch_profile)
    assert island["available"] is True
    n = len(island["labels"])
    # Every parallel series has one entry per period (the renderer indexes them together).
    for key in (
        "charge_kw",
        "discharge_kw",
        "soe_kwh",
        "site_load_kw",
        "red_band",
        "charge_hover",
        "discharge_hover",
        "soe_hover",
        "load_hover",
        "rows",
    ):
        assert len(island[key]) == n, key
    # Raw values are numbers; labels/hover are strings (formatted server-side).
    assert all(isinstance(v, (int, float)) for v in island["discharge_kw"])
    assert island["labels"][0] == "00:00"
    assert "kW" in island["rows"][0]["discharge"]  # fmt.kw applied
    assert "charge" in island["charge_hover"][0]


def test_hhmm_label_maps_period_to_clock() -> None:
    assert view._hhmm(0, 0.5) == "00:00"
    assert view._hhmm(2, 0.5) == "01:00"
    assert view._hhmm(33, 0.5) == "16:30"  # a weekday red-band half-hour


# --- value-stack island -----------------------------------------------------


def test_value_stack_island_segments_and_totals() -> None:
    run = _demo_run()
    vm = build_view_model(run.quote, run.site)
    island = view.value_stack_island(vm)
    assert island["available"] is True
    # Totals are the view-model's own sums, not re-summed in the island.
    assert island["floor_total"] == vm.split.floor_total_gbp
    assert island["upside_total"] == vm.split.upside_total_gbp
    assert island["floor_total_label"].startswith("£") or island["floor_total_label"].startswith(
        "-£"
    )
    # Each segment carries a raw revenue (bar length) + a pre-formatted £ label + its category.
    for seg in island["segments"]:
        assert isinstance(seg["revenue"], (int, float))
        assert seg["category"] in ("Priced floor", "Gated upside")
        assert "£" in seg["revenue_label"]


# --- surface island (Plotly z + feasibility grids) --------------------------


def test_surface_island_has_rectangular_z_and_feasibility_grids() -> None:
    run = _demo_run()
    island = view.surface_island(run.quote.sizing_grid)
    assert island["available"] is True
    n_dur, n_pow = len(island["durations"]), len(island["powers"])
    assert len(island["z_matrix"]) == n_dur
    assert all(len(row) == n_pow for row in island["z_matrix"])
    assert len(island["feasible_matrix"]) == n_dur
    # Feasibility is 1.0 / 0.0 only — never a coerced pass on missing data.
    flat = [v for row in island["feasible_matrix"] for v in row]
    assert set(flat) <= {0.0, 1.0}


def test_surface_island_none_is_unavailable() -> None:
    assert view.surface_island(None) == {"available": False}


def _pt(power: float, dur: float, z: float | None, stage: str, proxy: bool) -> s.SizingGridPoint:
    return s.SizingGridPoint(
        power_kw=power,
        duration_hours=dur,
        energy_kwh=power * dur,
        project_irr_pct=z,
        priceable=z is not None,
        bankable=False,
        solver_status=s.SolverStatus.OPTIMAL,
        stage=stage,
        proxy=proxy,
    )


def test_surface_island_is_stage_aware_on_a_two_stage_cloud() -> None:
    """WS4: mesh axes come from the lattice stages only; a confirmed value beats a proxy value
    on a shared cell (D32 — no reported figure from a proxy when a confirmed one exists);
    provenance counts are honest."""
    coarse = [
        _pt(100.0, 1.0, 1.0, "coarse", True),
        _pt(100.0, 2.0, 2.0, "coarse", True),
        _pt(200.0, 1.0, 3.0, "coarse", True),
        _pt(200.0, 2.0, 4.0, "coarse", True),
    ]
    refine = [_pt(157.3, 1.7, 6.0, "refine", True)]  # off-lattice: a marker, not a mesh axis
    confirm = [_pt(200.0, 2.0, 3.5, "confirm", False)]  # re-solves a coarse cell on the full series
    grid = s.SizingGrid(
        search_space_size=6,
        bankable=False,
        selection_reason="test cloud",
        points=[*coarse, *refine, *confirm],
        selected=confirm[0],
        search_version="m4-two-stage-v2",
    )
    island = view.surface_island(grid)
    assert island["available"] is True
    # Mesh from the coarse lattice only — the off-lattice refine point never explodes the axes.
    assert island["powers"] == [100.0, 200.0]
    assert island["durations"] == [1.0, 2.0]
    # The shared (200, 2.0) cell carries the CONFIRMED value, not the proxy guidance value.
    assert island["z_matrix"][1][1] == 3.5
    # Provenance is honest and complete.
    assert island["search_version"] == "m4-two-stage-v2"
    assert island["n_proxy"] == 5 and island["n_confirm"] == 1
    assert "typical-day proxy" in island["provenance"]
    # Every point still appears as a marker, carrying its stage/proxy provenance.
    assert len(island["points"]) == 6
    proxy_flags = {(p["power_kw"], p["duration_hours"], p["proxy"]) for p in island["points"]}
    assert (157.3, 1.7, True) in proxy_flags and (200.0, 2.0, False) in proxy_flags
    proxy_tips = [p["tooltip"] for p in island["points"] if p["proxy"]]
    assert all("proxy" in tip for tip in proxy_tips)


def test_surface_island_consumes_a_w6_dense_grid_as_persisted() -> None:
    """WS4: a north-star dense reference surface (regular, complete, stage='grid') is consumed
    directly — full mesh, zero proxies, provenance names the search."""
    pts = [
        _pt(p, d, p / 100.0 + d, "grid", False) for p in (100.0, 150.0, 200.0) for d in (1.0, 2.0)
    ]
    grid = s.SizingGrid(
        search_space_size=len(pts),
        bankable=False,
        selection_reason="dense reference",
        points=pts,
        search_version="w6-dense_grid-v1",
    )
    island = view.surface_island(grid)
    assert island["powers"] == [100.0, 150.0, 200.0]
    assert island["durations"] == [1.0, 2.0]
    assert island["n_proxy"] == 0 and island["n_confirm"] == 0
    assert island["provenance"] == "w6-dense_grid-v1: 6 evaluated sizes"
    assert all(v is not None for row in island["z_matrix"] for v in row)
