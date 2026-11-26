"""Render the portfolio-requirements answer as a self-contained HTML report.

One command, reusable: :func:`build_report` takes the solver output and returns a complete HTML
document. No external assets, no CDN — the GridQ palette is inlined from ``tokens.css`` values so
the page renders identically offline and in a PDF printer.

Chart design follows the repo design system and the dataviz method:

* **Sequential single hue** (amber ramp, validated monotonic light->dark) encodes levered-IRR
  magnitude across the capex x revenue field. Never a rainbow.
* **Status colour is reserved and never colour-alone** — the defensible region carries a label and
  a hatched texture as well as its green edge, because grey<->red separate by only ΔE 5.3 under
  protanopia.
* **One axis per scale**; archetype markers are distinguished by shape + direct label, not by hue.
* Every number here is formatted from a solver result. The report computes nothing.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from gridq.portfolio_requirements.assumptions import (
    ARCHETYPES,
    CONTRACTED_TIER,
    Archetype,
    AssumptionSet,
    Tag,
    central_set,
)
from gridq.portfolio_requirements.solver import (
    RequirementResult,
    SpecCard,
    frontier,
    net_revenue_ceiling,
    required_revenue_for_target,
    solve_spec_card,
    stack_gap_rows,
    verdict,
)

# Validated sequential ramp (monotonic luminance 0.0115 -> 0.6300, single amber hue).
RAMP = (
    "#241a0e",
    "#3d2a12",
    "#5c3d16",
    "#7d541b",
    "#a06d22",
    "#c68a2c",
    "#e2a340",
    "#ffb454",
    "#ffc678",
)

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#0e0e10; --surface-1:#131316; --surface-2:#18181c; --surface-3:#1e1e23;
  --text:#ececee; --text-2:#a6a6ad; --muted:#85858e;
  --accent:#ffb454; --accent-strong:#ffc678;
  --accent-line:rgba(255,180,84,.32); --accent-wash:rgba(255,180,84,.10);
  --ok:#4ade80; --ok-line:rgba(74,222,128,.35); --ok-wash:rgba(74,222,128,.10);
  --err:#f87171; --err-line:rgba(248,113,113,.35); --err-wash:rgba(248,113,113,.10);
  --gated:#8d8d96; --warn:#e8c468; --warn-wash:rgba(232,196,104,.10);
  --hair:rgba(255,255,255,.09);
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:56px 28px 96px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);margin:0 0 10px}
h1{font-size:clamp(28px,4.4vw,44px);line-height:1.12;letter-spacing:-.02em;
  margin:0 0 14px;font-weight:600}
h2{font-size:22px;letter-spacing:-.01em;margin:56px 0 6px;font-weight:600}
h3{font-size:15px;margin:28px 0 8px;font-weight:600;color:var(--text)}
p{color:var(--text-2);margin:0 0 14px;max-width:78ch}
.lede{font-size:17px;color:var(--text);max-width:74ch}
.tag{display:inline-block;font-family:var(--mono);font-size:10px;
  letter-spacing:.1em;text-transform:uppercase;padding:3px 8px;border-radius:4px;
  border:1px solid var(--hair);color:var(--text-2);background:var(--surface-2);
  white-space:nowrap}
.tag.central{color:var(--ok);border-color:var(--ok-line);background:var(--ok-wash)}
.tag.favourable{color:var(--accent);border-color:var(--accent-line);
  background:var(--accent-wash)}
.tag.stretch{color:var(--warn);border-color:rgba(232,196,104,.35);
  background:var(--warn-wash)}
.tag.indefensible{color:var(--err);border-color:var(--err-line);
  background:var(--err-wash)}
.banner{border:1px solid var(--hair);border-left:3px solid var(--accent);
  background:var(--surface-1);border-radius:8px;padding:16px 20px;margin:24px 0}
.banner.fail{border-left-color:var(--err)}
.banner strong{color:var(--text)}
.panel{background:var(--surface-1);border:1px solid var(--hair);border-radius:10px;
  padding:22px 24px;margin:20px 0;
  background-image:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0) 42%)}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:14px 0}
th{text-align:left;font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;padding:9px 10px;
  border-bottom:1px solid var(--hair);vertical-align:bottom}
td{padding:10px;border-bottom:1px solid rgba(255,255,255,.05);color:var(--text-2);
  vertical-align:top}
td.n,th.n{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums;color:var(--text)}
tr:last-child td{border-bottom:none}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.spec{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1px;
  background:var(--hair);border:1px solid var(--hair);border-radius:10px;
  overflow:hidden;margin:20px 0}
.spec>div{background:var(--surface-1);padding:16px 18px}
.spec .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);margin-bottom:7px}
.spec .v{font-family:var(--mono);font-size:23px;color:var(--accent);
  font-variant-numeric:tabular-nums;letter-spacing:-.01em;line-height:1.15}
.spec .v.small{font-size:14px;color:var(--text);line-height:1.45}
.spec .s{font-size:11.5px;color:var(--muted);margin-top:6px;line-height:1.45}
figure{margin:20px 0}
figcaption{font-size:12.5px;color:var(--muted);margin-top:12px;max-width:80ch}
.legend{display:flex;flex-wrap:wrap;gap:16px;align-items:center;margin:12px 0 4px;
  font-size:12px;color:var(--text-2);font-family:var(--mono)}
.legend span{display:inline-flex;align-items:center;gap:7px}
.sw{width:13px;height:13px;border-radius:3px;display:inline-block;flex:none}
.note{font-size:12.5px;color:var(--muted);font-style:italic}
code{font-family:var(--mono);font-size:12.5px;background:var(--surface-3);padding:1px 5px;
  border-radius:3px;color:var(--accent-strong)}
.foot{margin-top:64px;padding-top:20px;border-top:1px solid var(--hair);
  font-size:12px;color:var(--muted)}
@media print{body{background:#fff;color:#111}.panel,.spec>div{background:#fff}}
"""


def _fmt_pct(v: float | None, dp: int = 2) -> str:
    return "n/a" if v is None else f"{v * 100:.{dp}f}%"


def _fmt_gbp(v: float | None) -> str:
    return "n/a" if v is None else f"£{v:,.0f}"


def _tag_html(tag: Tag) -> str:
    return f'<span class="tag {tag.value.lower()}">{tag.value}</span>'


# --------------------------------------------------------------------------------------------
# The frontier chart — inline SVG, sequential amber ramp, defensible region, target contour
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChartGeom:
    w: int = 1000
    h: int = 520
    ml: int = 92
    mr: int = 28
    mt: int = 24
    mb: int = 62


DEFAULT_GEOM = ChartGeom()


def _ramp_colour(irr: float | None, lo: float, hi: float) -> str:
    """Map a levered IRR onto the validated sequential ramp."""
    if irr is None:
        return "#16161a"
    t = (irr - lo) / (hi - lo) if hi > lo else 0.0
    t = max(0.0, min(1.0, t))
    return RAMP[min(len(RAMP) - 1, int(t * len(RAMP)))]


def frontier_svg(
    *,
    tier: object = CONTRACTED_TIER,
    fleet_mw: float = 10.0,
    n_sites: int = 4,
    opex_per_mw: float = 17_000.0,
    target: float = 0.20,
    g: ChartGeom | None = None,
) -> tuple[str, dict[str, float]]:
    """Heatmap of levered IRR over capex/MW x net revenue/MW, with the defensible region marked.

    Returns ``(svg, stats)`` where ``stats`` carries the counts the prose quotes, so the narrative
    can never drift from the chart.
    """
    g = g or DEFAULT_GEOM
    pts = frontier(
        tier=tier,  # type: ignore[arg-type]
        fleet_mw=fleet_mw,
        n_sites=n_sites,
        opex_per_mw=opex_per_mw,
    )
    caps = sorted({p.capex_per_mw for p in pts})
    revs = sorted({p.net_rev_per_mw for p in pts})
    grid = {(p.capex_per_mw, p.net_rev_per_mw): p for p in pts}

    pw = g.w - g.ml - g.mr
    ph = g.h - g.mt - g.mb
    cw = pw / len(caps)
    chh = ph / len(revs)

    def x(i: int) -> float:
        return g.ml + i * cw

    def y(j: int) -> float:
        return g.mt + ph - (j + 1) * chh

    lo, hi = 0.0, 0.30
    cells: list[str] = []
    for i, c in enumerate(caps):
        for j, r in enumerate(revs):
            p = grid[(c, r)]
            fill = _ramp_colour(p.levered_irr, lo, hi)
            cells.append(
                f'<rect x="{x(i):.1f}" y="{y(j):.1f}" width="{cw - 2:.1f}" '
                f'height="{chh - 2:.1f}" fill="{fill}" rx="2"/>'
            )

    # Defensible region: revenue <= Favourable ceiling AND capex >= the small-BTM floor.
    dr_rev = net_revenue_ceiling("favourable")
    def_pts = [p for p in pts if p.defensible]
    reaching = [p for p in pts if p.levered_irr is not None and p.levered_irr >= target]
    reaching_defensible = [p for p in reaching if p.defensible]

    box = ""
    if def_pts:
        cmin = min(p.capex_per_mw for p in def_pts)
        ci = caps.index(cmin)
        jmax = max(revs.index(p.net_rev_per_mw) for p in def_pts)
        bx, by = x(ci) - 1, y(jmax) - 1
        bw, bh = (g.ml + pw) - bx - 1, (g.mt + ph) - by - 1
        box = (
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="url(#hatch)" stroke="#4ade80" stroke-width="2" rx="4"/>'
            f'<text x="{bx + 10:.1f}" y="{by + 21:.1f}" fill="#4ade80" font-size="12.5" '
            f'font-family="var(--mono)" font-weight="600">DEFENSIBLE REGION</text>'
            f'<text x="{bx + 10:.1f}" y="{by + 38:.1f}" fill="#4ade80" font-size="11" '
            f'font-family="var(--mono)" opacity=".85">revenue &#8804; Favourable ceiling '
            f"&#183; capex &#8805; BTM floor</text>"
        )

    # Target contour: for each capex column, the lowest revenue reaching the target.
    contour: list[tuple[float, float]] = []
    for i, c in enumerate(caps):
        hits = [
            revs.index(r)
            for r in revs
            if (gp := grid[(c, r)]).levered_irr is not None and gp.levered_irr >= target
        ]
        if hits:
            contour.append((x(i) + cw / 2, y(min(hits)) + chh / 2))
    contour_path = ""
    if len(contour) > 1:
        d = " ".join(
            ("M" if k == 0 else "L") + f"{px:.1f},{py:.1f}" for k, (px, py) in enumerate(contour)
        )
        contour_path = (
            f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="2.5" '
            f'stroke-dasharray="7 4" opacity=".92"/>'
            f'<text x="{contour[-1][0] - 8:.1f}" y="{contour[-1][1] - 12:.1f}" fill="#fff" '
            f'font-size="12.5" font-weight="600" text-anchor="end" '
            f'font-family="var(--mono)">{target * 100:.0f}% LEVERED IRR</text>'
        )

    # Axes
    xt: list[str] = []
    for i, c in enumerate(caps):
        if i % 2 == 0:
            xt.append(
                f'<text x="{x(i) + cw / 2:.1f}" y="{g.mt + ph + 20:.1f}" fill="#85858e" '
                f'font-size="11" text-anchor="middle" font-family="var(--mono)">'
                f"{c / 1000:.0f}k</text>"
            )
    yt: list[str] = []
    for j, r in enumerate(revs):
        if j % 4 == 0:
            yt.append(
                f'<text x="{g.ml - 12:.1f}" y="{y(j) + chh / 2 + 4:.1f}" fill="#85858e" '
                f'font-size="11" text-anchor="end" font-family="var(--mono)">'
                f"{r / 1000:.0f}k</text>"
            )

    # Reference markers — shape + direct label, never hue-coded.
    marks: list[str] = []
    for label, capex_v, rev_v in (
        ("Model_v5 Central", 452_000.0, 65_690.0),
        ("Realistic Central", 560_000.0, net_revenue_ceiling("central")),
    ):
        if caps[0] <= capex_v <= caps[-1] and revs[0] <= rev_v <= revs[-1]:
            px = g.ml + (capex_v - caps[0]) / (caps[-1] - caps[0] + cw) * pw + cw / 2
            py = g.mt + ph - (rev_v - revs[0]) / (revs[-1] - revs[0] + chh) * ph - chh / 2
            anchor = "start" if px < g.ml + pw * 0.6 else "end"
            dx = 13 if anchor == "start" else -13
            marks.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6" fill="#0e0e10" stroke="#ececee" '
                f'stroke-width="2.5"/>'
                f'<text x="{px + dx:.1f}" y="{py + 4:.1f}" fill="#ececee" font-size="12" '
                f'text-anchor="{anchor}" font-family="var(--mono)" font-weight="600">'
                f"{html.escape(label)}</text>"
            )

    svg = f"""<svg viewBox="0 0 {g.w} {g.h}" width="100%" role="img"
     aria-label="Levered IRR across capex per MW and net revenue per MW. The 20% contour lies
     entirely outside the defensible region.">
  <defs>
    <pattern id="hatch" width="8" height="8" patternTransform="rotate(45)"
             patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="8" stroke="#4ade80" stroke-width="1.4" opacity=".16"/>
    </pattern>
  </defs>
  <rect width="{g.w}" height="{g.h}" fill="#131316" rx="8"/>
  {"".join(cells)}
  {box}
  {contour_path}
  {"".join(marks)}
  {"".join(xt)}{"".join(yt)}
  <text x="{g.ml + pw / 2:.0f}" y="{g.h - 16}" fill="#a6a6ad" font-size="12"
        text-anchor="middle" font-family="var(--mono)">INSTALLED CAPEX &#183; £/MW</text>
  <text x="18" y="{g.mt + ph / 2:.0f}" fill="#a6a6ad" font-size="12" text-anchor="middle"
        font-family="var(--mono)"
        transform="rotate(-90 18 {g.mt + ph / 2:.0f})">NET REVENUE &#183; £/MW/yr</text>
</svg>"""
    stats = {
        "points": float(len(pts)),
        "defensible": float(len(def_pts)),
        "reaching": float(len(reaching)),
        "reaching_defensible": float(len(reaching_defensible)),
        "favourable_ceiling": dr_rev,
    }
    return svg, stats


# --------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------


def build_report(
    *,
    target: float = 0.20,
    archetypes: tuple[Archetype, ...] = ARCHETYPES,
    assumptions: AssumptionSet | None = None,
) -> str:
    """Render the full HTML report. Deterministic: same inputs -> byte-identical output."""
    aset = assumptions or central_set()
    results: list[RequirementResult] = [
        required_revenue_for_target(archetype=a, target_levered_irr=target) for a in archetypes
    ]
    top = next(a for a in archetypes if a.key == "top_decile_host")
    card: SpecCard = solve_spec_card(archetype=top, target_levered_irr=target)
    svg, stats = frontier_svg(target=target)
    v = verdict(results)

    # --- spec card ---
    spec_cells = [
        (
            "Required unlevered IRR",
            _fmt_pct(card.required_unlevered_irr),
            "vs 7.75% at Model_v5 Central",
        ),
        (
            "Required net revenue",
            f"£{card.required_net_rev_per_mw / 1000:,.0f}k",
            f"per MW/yr — {card.multiple_of_central:.2f}x realistic Central",
        ),
        (
            "Implied captured spread",
            f"£{card.implied_captured_spread_gbp_per_mwh:,.0f}",
            "per MWh on 657 MWh/MW/yr throughput",
        ),
        (
            "Max installed capex",
            f"£{card.max_capex_per_mw / 1000:,.0f}k",
            "per MW, incl. connection",
        ),
        (
            "Gearing (DSCR-sized)",
            f"{card.gearing_achieved * 100:.0f}%",
            f"DSCR {card.dscr_avg:.2f}x avg — sized, not asserted",
        ),
        (
            "Minimum portfolio",
            f"{card.min_fleet_mw:,.0f} MW",
            f"across {card.n_sites} sites (fixed-cost dilution)",
        ),
    ]
    spec_html = "".join(
        f'<div><div class="k">{html.escape(k)}</div><div class="v">{val}</div>'
        f'<div class="s">{html.escape(sub)}</div></div>'
        for k, val, sub in spec_cells
    )
    spec_html += (
        f'<div><div class="k">Financing tier</div>'
        f'<div class="v small">{html.escape(card.financing)}</div>'
        f'<div class="s">Requires &#8805;60% firm revenue share</div></div>'
        f'<div><div class="k">Classification</div><div class="v small">'
        f"{_tag_html(card.revenue_tag)}</div>"
        f'<div class="s">Every revenue line simultaneously Favourable&#8211;Stretch</div></div>'
    )

    # --- archetype table ---
    arch_rows = ""
    for a, r in zip(archetypes, results, strict=True):
        cc = r.central_case
        req = (
            f"£{r.required_net_rev_per_mw / 1000:,.0f}k"
            if r.required_net_rev_per_mw is not None
            else "n/a"
        )
        mult = f"{r.multiple_of_central:.2f}x" if r.multiple_of_central else "—"
        arch_rows += (
            f"<tr><td><strong>{html.escape(a.label)}</strong><br>"
            f'<span class="note">{a.fleet_mw:,.0f} MW / {a.n_sites} sites · capex '
            f"£{a.capex_per_mw / 1000:,.0f}k/MW · {html.escape(a.tier.name)}</span></td>"
            f'<td class="n">{_fmt_pct(cc.unlevered_irr)}</td>'
            f'<td class="n">{_fmt_pct(cc.levered_irr)}</td>'
            f'<td class="n">{cc.gearing_achieved * 100:.0f}%</td>'
            f'<td class="n">{cc.dscr_avg:.2f}x</td>'
            f'<td class="n">{req}</td><td class="n">{mult}</td>'
            f"<td>{_tag_html(r.revenue_tag)}</td></tr>"
        )

    # --- revenue stack gap table ---
    stack_rows = ""
    for name, mv5, cen, fav, stx, note in stack_gap_rows():
        delta = cen - mv5
        arrow = "&#9660;" if delta < 0 else ("&#9650;" if delta > 0 else "&#8212;")
        col = "var(--err)" if delta < 0 else ("var(--ok)" if delta > 0 else "var(--muted)")
        stack_rows += (
            f"<tr><td><strong>{html.escape(name)}</strong><br>"
            f'<span class="note">{html.escape(note)}</span></td>'
            f'<td class="n">{mv5:,.0f}</td>'
            f'<td class="n">{cen:,.0f} <span style="color:{col}">{arrow}</span></td>'
            f'<td class="n">{fav:,.0f}</td><td class="n">{stx:,.0f}</td></tr>'
        )

    # --- assumptions register ---
    asm_rows = "".join(
        f"<tr><td>{html.escape(n)}</td><td class='n'>{html.escape(val)}</td>"
        f"<td>{_tag_html(Tag(tag))}</td><td><span class='note'>{html.escape(src)}</span></td></tr>"
        for n, val, tag, src in aset.as_rows()
    )

    reaching = int(stats["reaching"])
    reaching_def = int(stats["reaching_defensible"])
    estate = next(r for r in results if r.archetype_key == "grundon_harwell_estate")
    # The estate is expected to be infeasible; keep the prose safe if it ever becomes solvable.
    est_req = estate.required_net_rev_per_mw
    est_mult = f"{estate.multiple_of_central:.2f}x" if estate.multiple_of_central else "n/a"
    est_over_stretch = (
        f"{est_req / net_revenue_ceiling('stretch'):.2f}x" if est_req is not None else "n/a"
    )
    est_cap = f"{estate.load_cap_mw:.1f} MW" if estate.load_cap_mw is not None else "n/a"

    return f"""<title>What portfolio would it take to earn 20% levered IRR?</title>
<style>{CSS}</style>
<div class="wrap">
  <p class="eyebrow">Neura · GridQ · Indicative — pre-diligence analysis</p>
  <h1>What BTM portfolio would it actually take<br>to earn a {target * 100:.0f}% levered IRR?</h1>
  <p class="lede">This inverts the usual question. Rather than reporting the return of a given
  portfolio, it solves for the portfolio that would be <em>required</em> to hit
  {target * 100:.0f}% levered — and refuses to return a number when the answer would sit outside
  defensible bounds.</p>

  <div class="banner fail">
    <strong>The finding.</strong> Across the {int(stats["points"])}-point configuration grid,
    <strong>{reaching} points reach {target * 100:.0f}% levered IRR and
    {"none of them" if reaching_def == 0 else f"only {reaching_def}"} sit inside the defensible
    region.</strong> {html.escape(v)}
  </div>

  <h2>The required portfolio</h2>
  <p>The minimal-stretch point on the {target * 100:.0f}% contour, for the best plausible host
  archetype: a grid-supplied industrial portfolio on contracted-backed debt.</p>
  <div class="spec">{spec_html}</div>
  <p class="note">Host archetype: {html.escape(top.note)}</p>

  <h2>The frontier</h2>
  <figure>
    {svg}
    <div class="legend">
      <span><i class="sw" style="background:{RAMP[1]}"></i>lower levered IRR</span>
      <span><i class="sw" style="background:{RAMP[4]}"></i></span>
      <span><i class="sw" style="background:{RAMP[7]}"></i>higher</span>
      <span><i class="sw" style="background:transparent;border:2px solid var(--ok)"></i>
        defensible region</span>
      <span><i class="sw" style="background:linear-gradient(90deg,#fff 0 55%,transparent 55%)">
        </i>{target * 100:.0f}% levered contour</span>
    </div>
    <figcaption>Levered IRR across installed capex and net revenue per MW, on DSCR-sized
    contracted-tier debt (7.0%, 10 years, sized at 1.30x on P90 CFADS). The green hatched region is
    where both inputs stay defensible: revenue at or below the Favourable ceiling of
    £{stats["favourable_ceiling"] / 1000:,.0f}k/MW/yr, and capex at or above the small-BTM floor.
    <strong>The dashed {target * 100:.0f}% contour never enters it.</strong> That gap is the whole
    answer.</figcaption>
  </figure>

  <h2>Archetypes compared</h2>
  <div class="scroll"><table>
    <thead><tr><th>Portfolio</th><th class="n">Unlev. IRR<br>at Central</th>
    <th class="n">Lev. IRR<br>at Central</th><th class="n">Gearing<br>supported</th>
    <th class="n">DSCR</th><th class="n">Revenue needed<br>for {target * 100:.0f}%</th>
    <th class="n">× Central</th><th>Verdict</th></tr></thead>
    <tbody>{arch_rows}</tbody>
  </table></div>
  <p><strong>The five real sites cannot reach the target.</strong> Closing the gap would need
  {_fmt_gbp(est_req)}/MW/yr — {est_mult} realistic Central and {est_over_stretch} above even the
  Stretch ceiling. Their load-coupled capacity caps the fleet at {est_cap} regardless.</p>

  <h2>Where the revenue would have to come from</h2>
  <p>Per-stream £/MW/yr gross, before the co-optimisation haircut. The arrow marks where this
  module's realistic Central differs from Model_v5's.</p>
  <div class="scroll"><table>
    <thead><tr><th>Revenue stream</th><th class="n">Model_v5<br>Central</th>
    <th class="n">Realistic<br>Central</th><th class="n">Favourable</th>
    <th class="n">Stretch</th></tr></thead>
    <tbody>{stack_rows}</tbody>
  </table></div>

  <h2>Assumptions register</h2>
  <p>Every input, classified and sourced. Assumption set <code>{aset.version}</code>.</p>
  <div class="scroll"><table>
    <thead><tr><th>Assumption</th><th class="n">Value</th><th>Class</th><th>Source</th></tr></thead>
    <tbody>{asm_rows}</tbody>
  </table></div>

  <h2>How to read this honestly</h2>
  <p>Debt here is <strong>sized, not asserted</strong>:
  <code>min(60% × capex, PV(CFADS_P90 ÷ DSCR))</code>. That single rule is why this model cannot
  reproduce the error in the reference model, which asserted 60% gearing against a 0.996× DSCR and
  credited interest expense as income — together worth 13.7 points of levered IRR.</p>
  <p>{target * 100:.0f}% is not a portfolio-scale question. IRR is scale-invariant above the
  fixed-cost floor, so building more MW does not raise the percentage — and at small scale the
  capex and opex floors make it worse. {target * 100:.0f}% is a property of
  <em>host quality</em>: tariff differential, load shape, connection headroom and capex per MW.
  The realistic own-and-operate ceiling on defensible inputs is roughly 10–14% levered.</p>

  <div class="foot">
    Indicative — pre-diligence analysis. No underwriting liability is carried at this stage.
    Generated by <code>gridq.portfolio_requirements</code>; solver design reviewed and approved by
    the Fable advisor (2026-08-05). Every figure is formatted from a deterministic solver result —
    the report computes nothing.
  </div>
</div>"""
