"""Dev/analysis tool: solve the LP dispatch on the deterministic fixture week and render it
as a single self-contained interactive HTML (three stacked panels, shared time axis) for
visual verification of the solver's behaviour.

NOT product code: nothing under src/gridq/app/ is touched, no number here feeds a quote.
The fixture data is the same deterministic test series the solver tests use (generating
data for a viz/test harness is fine — the "never invent data" rule is about production runs).

Run from the repo root with the venv interpreter:

    .venv/bin/python scripts/inspect_dispatch.py [output.html]

With no argument it writes to a temp directory and opens the HTML in your browser;
pass an explicit output path to keep the file (no auto-open).

Panels:
  A · Price (£/kWh): total = energy + DUoS, with the components, red-band shaded.
  B · Power (kW): discharge (+) / charge (−) bars, site load, net grid import, MIC and
      ±rated-power reference lines.
  C · State (kWh): state of energy with the [0, usable-energy] band.
"""

from __future__ import annotations

import sys
import tempfile
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))  # fixture helpers live with the tests

from _milp_support import multi_rate_site, write_hh_csv  # noqa: E402
from gridq.assumptions import load_assumptions  # noqa: E402
from gridq.dispatch.contract import DispatchProblem  # noqa: E402
from gridq.optimisation.milp_solver import MilpDispatchSolver  # noqa: E402

# --- knobs (edit these to vary the week) -----------------------------------------------
DAYS = 7
POWER_KW = 100.0
DURATION_H = 2.0
IMPORT_RATES = {"day": 0.30, "night": 0.10}  # £/kWh by time-of-use band
DUOS_RED_RATE = 0.10  # £/kWh, weekday 16:00–19:00 (None to remove the red band)
ASSUMPTION_VERSION = "gb_v0_1"
# ----------------------------------------------------------------------------------------


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs of ``mask`` as inclusive (start, end) index pairs."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def main() -> int:
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        out.parent.mkdir(parents=True, exist_ok=True)
        auto_open = False
    else:
        out = Path(tempfile.mkdtemp(prefix="gridq_dispatch_")) / "dispatch_week.html"
        auto_open = True

    # --- fixture data + site (deterministic, same series the solver tests use) ---------
    hh_path = out.parent / "dispatch_week_hh.csv"
    rows, _ = write_hh_csv(hh_path, days=DAYS)
    site = multi_rate_site(hh_path, rows=rows)
    site = site.model_copy(
        update={
            "tariff_stack": site.tariff_stack.model_copy(
                update={"import_rates": IMPORT_RATES, "duos_red_rate": DUOS_RED_RATE}
            )
        }
    )

    # --- solve ---------------------------------------------------------------------------
    solver = MilpDispatchSolver()
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(ASSUMPTION_VERSION),
        power_kw=POWER_KW,
        energy_kwh=POWER_KW * DURATION_H,
        duration_hours=DURATION_H,
    )
    result = solver.solve(problem)
    if result.solver_status.value != "optimal":
        print(f"NO DISPATCH: solver_status={result.solver_status.value}")
        for w in result.warnings:
            print(f"  warning: {w}")
        return 1
    schedule = solver.last_schedule()
    assert schedule is not None
    _, ts, _, _ = solver._load_series(str(hh_path))

    # --- derived series -------------------------------------------------------------------
    dt = schedule.interval_hours
    charge = schedule.charge_kw
    discharge = schedule.discharge_kw
    load = schedule.site_load_kw
    soe = schedule.soe_kwh
    energy_price = (
        schedule.energy_price_gbp_per_kwh
        if schedule.energy_price_gbp_per_kwh is not None
        else np.zeros(len(load))
    )
    duos = schedule.duos_rate_gbp_per_kwh
    price = energy_price + duos
    net_import = np.clip(load + charge - discharge, 0.0, None)
    conn = [v for v in (site.connection.mic_kw, site.connection.asc_kw) if v]
    mic = min(conn) if conn else float("nan")

    # --- figure -----------------------------------------------------------------------------
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots  # deferred: only the viz needs plotly

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.28, 0.44, 0.28],
        subplot_titles=(
            "A · Price the LP optimised against (£/kWh)",
            "B · Battery power vs site load and connection (kW)",
            "C · State of energy (kWh)",
        ),
    )
    x = list(ts)
    bar_ms = int(dt * 3_600_000)  # one interval, in ms, left-aligned at period start

    # A · price
    fig.add_trace(
        go.Scatter(
            x=x, y=price, name="total price", line=dict(color="#111111", width=2, shape="hv")
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=energy_price,
            name="energy component",
            line=dict(color="#1f77b4", width=1, shape="hv", dash="dot"),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=duos,
            name="DUoS component",
            line=dict(color="#ff7f0e", width=1, shape="hv", dash="dot"),
        ),
        row=1,
        col=1,
    )

    # B · power
    fig.add_trace(
        go.Bar(
            x=x,
            y=discharge,
            name="discharge (kW)",
            marker_color="#2ca02c",
            width=bar_ms,
            offset=0,
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=x,
            y=-charge,
            name="charge (kW, drawn negative)",
            marker_color="#d62728",
            width=bar_ms,
            offset=0,
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=load, name="site load", line=dict(color="#7f7f7f", width=1.5, shape="hv")
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=net_import,
            name="net grid import (load + charge − discharge)",
            line=dict(color="#9467bd", width=1.5, shape="hv"),
        ),
        row=2,
        col=1,
    )
    fig.add_hline(
        y=mic,
        row=2,
        col=1,
        line=dict(color="#d62728", dash="dash"),
        annotation_text=f"MIC {mic:.0f} kW",
    )
    for y in (POWER_KW, -POWER_KW):
        fig.add_hline(y=y, row=2, col=1, line=dict(color="#bbbbbb", dash="dot"))

    # C · state of energy
    fig.add_hrect(
        y0=0.0, y1=schedule.energy_kwh, row=3, col=1, fillcolor="#dddddd", opacity=0.4, line_width=0
    )
    fig.add_trace(
        go.Scatter(x=x, y=soe, name="state of energy", line=dict(color="#17becf", width=2)),
        row=3,
        col=1,
    )
    fig.add_hline(
        y=schedule.energy_kwh,
        row=3,
        col=1,
        line=dict(color="#999999", dash="dash"),
        annotation_text=f"usable {schedule.energy_kwh:.0f} kWh",
    )

    # red-band shading, aligned across all panels
    for i, j in _mask_runs(schedule.red_band_mask):
        fig.add_vrect(
            x0=ts[i],
            x1=ts[j] + pd.Timedelta(hours=dt),
            fillcolor="#d62728",
            opacity=0.08,
            line_width=0,
            row="all",
            col=1,
        )

    fig.update_layout(
        title=(
            f"LP dispatch — {DAYS} fixture days, {POWER_KW:.0f} kW × {DURATION_H:g} h "
            f"({POWER_KW * DURATION_H:.0f} kWh), {ASSUMPTION_VERSION}"
        ),
        hovermode="x unified",
        barmode="overlay",
        height=950,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=110),
    )
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor")
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05), row=3, col=1)
    fig.update_yaxes(title_text="£/kWh", row=1, col=1)
    fig.update_yaxes(title_text="kW", row=2, col=1)
    fig.update_yaxes(title_text="kWh", row=3, col=1)

    fig.write_html(out, include_plotlyjs=True, full_html=True)

    # --- sanity summary (cross-check these against the chart) ------------------------------
    tot_dis = float(discharge.sum()) * dt
    tot_chg = float(charge.sum()) * dt
    co = int(np.sum((charge > 1e-6) & (discharge > 1e-6)))
    bill_reduction = float((price * (discharge - charge)).sum()) * dt
    print(f"wrote {out}  ({out.stat().st_size / 1e6:.1f} MB, self-contained)")
    print(f"  horizon:            {DAYS} days × 48 half-hours = {len(load)} intervals")
    print(f"  total discharge:    {tot_dis:.1f} kWh")
    print(f"  total charge:       {tot_chg:.1f} kWh")
    print(f"  charge/discharge:   {tot_chg / tot_dis:.4f}  (expect ≈ 1/RTE = {1 / 0.88:.4f})")
    print(f"  peak net import:    {float(net_import.max()):.1f} kW  vs MIC {mic:.0f} kW")
    print(
        f"  SoE range:          [{float(soe.min()):.1f}, {float(soe.max()):.1f}] kWh "
        f"of {schedule.energy_kwh:.0f} usable"
    )
    print(f"  co-dispatch count:  {co}  (expect 0)")
    print(f"  bill reduction:     £{bill_reduction:.2f} over the horizon")
    print(f"  binding:            {result.constraints_binding}")
    if auto_open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
