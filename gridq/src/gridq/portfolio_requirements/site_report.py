"""Per-site deliverables: the information pack and the slide deck.

Two outputs from one completed set of site results, both self-contained HTML rendered from the
repo design system (no CDN, no build step, printable to PDF):

* :func:`build_info_pack` — consultancy-grade long-form pack: executive summary, site overview,
  data foundations, the Pessimistic/Central/Optimistic range, revenue stack, financing, returns,
  assumptions register with classifications, blocking verifications, next steps.
* :func:`build_deck` — 16:9 slides, one idea per slide, custom layouts.

**The interface formats record values; it never computes one.** Every figure traces to a
:class:`~gridq.portfolio_requirements.sites.SiteResult`.
"""

from __future__ import annotations

import html

from gridq.portfolio_requirements.report import CSS as BASE_CSS
from gridq.portfolio_requirements.sites import (
    CASES,
    EXPECTED_LEVERED,
    SiteCase,
    SiteResult,
    cases_for,
    evaluate_site,
)

TAG_ORDER = {"Central": 0, "Favourable": 1, "Stretch": 2, "Indefensible": 3}

PACK_CSS = (
    BASE_CSS
    + """
.range{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--hair);
  border:1px solid var(--hair);border-radius:10px;overflow:hidden;margin:22px 0}
.range>div{background:var(--surface-1);padding:18px 20px}
.range .case{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.range .irr{font-family:var(--mono);font-size:30px;letter-spacing:-.02em;line-height:1.05;
  font-variant-numeric:tabular-nums}
.range .irr.pos{color:var(--accent)}
.range .irr.neg{color:var(--err)}
.range .sub{font-size:12px;color:var(--muted);margin-top:8px;line-height:1.5}
.range dl{margin:14px 0 0;font-size:12.5px;display:grid;grid-template-columns:auto 1fr;
  gap:4px 10px}
.range dt{color:var(--muted)}
.range dd{margin:0;font-family:var(--mono);color:var(--text);text-align:right;
  font-variant-numeric:tabular-nums}
.block{border:1px solid var(--err-line);background:var(--err-wash);border-radius:8px;
  padding:16px 20px;margin:20px 0}
.block .h{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--err);margin-bottom:8px;font-weight:600}
.bars{margin:16px 0}
.bar{display:grid;grid-template-columns:190px 1fr 74px;align-items:center;gap:12px;
  margin-bottom:7px;font-size:13px}
.bar .lab{color:var(--text-2);text-align:right}
.bar .track{height:20px;background:var(--surface-3);border-radius:3px;overflow:hidden}
.bar .fill{height:100%;border-radius:3px}
.bar .val{font-family:var(--mono);text-align:right;color:var(--text);
  font-variant-numeric:tabular-nums;font-size:12.5px}
ul{color:var(--text-2);margin:0 0 14px;padding-left:20px;max-width:78ch}
li{margin-bottom:7px}
.pill{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:.09em;
  text-transform:uppercase;padding:2px 7px;border-radius:3px;background:var(--surface-3);
  color:var(--text-2);border:1px solid var(--hair)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13.5px;margin:14px 0}
.kv dt{color:var(--muted)}
.kv dd{margin:0;color:var(--text);font-family:var(--mono);
  font-variant-numeric:tabular-nums}
"""
)

DECK_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#0e0e10; --surface-1:#131316; --surface-2:#18181c; --surface-3:#1e1e23;
  --text:#ececee; --text-2:#a6a6ad; --muted:#85858e;
  --accent:#ffb454; --accent-strong:#ffc678; --ok:#4ade80; --err:#f87171;
  --warn:#e8c468; --hair:rgba(255,255,255,.10);
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
body{margin:0;background:#08080a;color:var(--text);font-family:var(--sans)}
.slide{position:relative;width:100%;max-width:1280px;aspect-ratio:16/9;margin:0 auto 26px;
  background:var(--bg);border:1px solid var(--hair);border-radius:10px;overflow:hidden;
  padding:58px 66px;display:flex;flex-direction:column;
  background-image:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0) 38%)}
.slide.cover{justify-content:flex-end}
.n{position:absolute;top:26px;right:32px;font-family:var(--mono);font-size:11px;
  color:var(--muted);letter-spacing:.1em}
.eb{font-family:var(--mono);font-size:11px;letter-spacing:.17em;text-transform:uppercase;
  color:var(--accent);margin:0 0 16px}
.slide h2{font-size:38px;line-height:1.1;letter-spacing:-.022em;margin:0 0 18px;
  font-weight:600;text-wrap:balance}
.slide.cover h1{font-size:60px;line-height:1.03;letter-spacing:-.03em;margin:0 0 20px;
  font-weight:600;text-wrap:balance}
.slide p{color:var(--text-2);font-size:17px;line-height:1.55;margin:0 0 14px;max-width:74ch}
.big{font-family:var(--mono);font-size:104px;line-height:1;letter-spacing:-.035em;
  color:var(--accent);font-variant-numeric:tabular-nums}
.big.neg{color:var(--err)}
.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--hair);
  border:1px solid var(--hair);border-radius:8px;overflow:hidden;margin-top:8px}
.trio>div{background:var(--surface-1);padding:22px}
.trio .c{font-family:var(--mono);font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--muted);margin-bottom:12px}
.trio .v{font-family:var(--mono);font-size:44px;line-height:1;letter-spacing:-.028em;
  color:var(--accent);font-variant-numeric:tabular-nums}
.trio .v.neg{color:var(--err)}
.trio .s{font-size:13px;color:var(--muted);margin-top:12px;line-height:1.5}
.slide ul{color:var(--text-2);font-size:17px;line-height:1.5;padding-left:24px;margin:0}
.slide li{margin-bottom:13px}
.slide li strong{color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:15px}
th{text-align:left;font-family:var(--mono);font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--muted);font-weight:500;padding:9px 10px;
  border-bottom:1px solid var(--hair)}
td{padding:11px 10px;border-bottom:1px solid rgba(255,255,255,.055);color:var(--text-2)}
td.n2,th.n2{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;
  color:var(--text)}
.foot{margin-top:auto;padding-top:20px;font-family:var(--mono);font-size:11px;
  color:var(--muted);letter-spacing:.05em;border-top:1px solid var(--hair)}
.flow{display:flex;align-items:center;gap:0;margin:18px 0;flex-wrap:wrap}
.node{background:var(--surface-2);border:1px solid var(--hair);border-radius:7px;
  padding:14px 18px;font-size:14px;color:var(--text);text-align:center;min-width:118px}
.node .sm{display:block;font-family:var(--mono);font-size:10.5px;color:var(--muted);margin-top:5px}
.arw{color:var(--accent);font-size:20px;padding:0 12px;flex:none}
@media print{body{background:#fff}.slide{page-break-after:always;margin:0;border:none;
  border-radius:0;max-width:none}}
"""


def _pct(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{dp}f}%"


def _k(v: float) -> str:
    return f"£{v / 1000:,.0f}k"


def _cls(v: float | None) -> str:
    return "neg" if (v is None or v < 0) else "pos"


def _results_for(site_key: str) -> dict[str, SiteResult | None]:
    out: dict[str, SiteResult | None] = {}
    for c in cases_for(site_key):
        out[c.case] = evaluate_site(c) if c.proposed else None
    return out


def _range_block(res: dict[str, SiteResult | None], site_key: str) -> str:
    cells = ""
    for i, case in enumerate(CASES):
        r = res.get(case)
        exp = EXPECTED_LEVERED[site_key][i]
        if r is None:
            cells += (
                f'<div><div class="case">{case}</div>'
                f'<div class="irr neg">—</div>'
                f'<div class="sub">No proposal in this case.</div></div>'
            )
            continue
        c = r.case
        bess = f"{c.bess_mw:g} MW / {c.bess_mwh:g} MWh" if c.bess_mw else "none"
        solar = f"{c.solar_kwp:g} kWp" if c.solar_kwp else "none"
        cells += (
            f'<div><div class="case">{case}</div>'
            f'<div class="irr {_cls(r.levered_irr)}">{_pct(r.levered_irr)}</div>'
            f'<div class="sub">levered IRR · unlevered {_pct(r.unlevered_irr)}</div>'
            f"<dl><dt>Battery</dt><dd>{bess}</dd>"
            f"<dt>Solar</dt><dd>{solar}</dd>"
            f"<dt>Revenue/yr</dt><dd>{_k(c.revenue_gbp)}</dd>"
            f"<dt>Capex</dt><dd>{_k(c.capex_gbp)}</dd>"
            f"<dt>Gearing</dt><dd>{r.gearing * 100:.0f}%</dd>"
            f"<dt>DSCR avg</dt><dd>{r.dscr_avg:.2f}×</dd>"
            f"<dt>NPV @8%</dt><dd>{_k(r.npv_at_8pct)}</dd>"
            f"<dt>Payback</dt><dd>"
            f"{f'{r.payback_years:.1f} yr' if r.payback_years else '>15 yr'}</dd>"
            f"<dt>Advisor view</dt><dd>{html.escape(exp)}</dd></dl>"
            f'<div class="sub"><span class="pill">{c.tag.value}</span></div></div>'
        )
    return f'<div class="range">{cells}</div>'


def _revenue_bars(case: SiteCase) -> str:
    if not case.revenue_lines:
        return '<p class="note">No revenue — no proposal in this case.</p>'
    mx = max(case.revenue_lines.values())
    rows = ""
    # Sequential amber ramp by magnitude; the largest line is the brightest.
    for name, val in sorted(case.revenue_lines.items(), key=lambda kv: -kv[1]):
        w = (val / mx * 100) if mx else 0
        shade = "#ffb454" if val == mx else ("#c68a2c" if val > mx * 0.4 else "#7d541b")
        rows += (
            f'<div class="bar"><div class="lab">{html.escape(name)}</div>'
            f'<div class="track"><div class="fill" style="width:{w:.1f}%;'
            f'background:{shade}"></div></div>'
            f'<div class="val">£{val:,.0f}</div></div>'
        )
    rows += (
        f'<div class="bar"><div class="lab"><strong>Total</strong></div>'
        f'<div class="track"></div>'
        f'<div class="val"><strong>£{case.revenue_gbp:,.0f}</strong></div></div>'
    )
    return f'<div class="bars">{rows}</div>'


def build_info_pack(site_key: str, *, data_notes: str = "") -> str:
    """Consultancy-grade information pack for one site."""
    cs = cases_for(site_key)
    label = cs[0].site_label
    res = _results_for(site_key)
    central = next((c for c in cs if c.case == "Central"), cs[0])
    r_central = res.get("Central")
    blocking = next((c.blocking for c in cs if c.blocking), None)

    asms = ""
    seen: set[str] = set()
    for c in cs:
        for a in c.key_assumptions:
            if a not in seen:
                seen.add(a)
                asms += f"<li>{html.escape(a)}</li>"

    case_rows = ""
    for case in CASES:
        r = res.get(case)
        if r is None:
            case_rows += (
                f'<tr><td>{case}</td><td colspan="7"><em>No proposal in this case.</em></td></tr>'
            )
            continue
        c = r.case
        case_rows += (
            f"<tr><td><strong>{case}</strong><br>"
            f'<span class="note">{html.escape(c.driver)}</span></td>'
            f'<td class="n">{c.bess_mw:g}/{c.bess_mwh:g}</td>'
            f'<td class="n">{c.solar_kwp:g}</td>'
            f'<td class="n">{c.revenue_gbp:,.0f}</td>'
            f'<td class="n">{c.capex_gbp:,.0f}</td>'
            f'<td class="n">{_pct(r.unlevered_irr)}</td>'
            f'<td class="n">{_pct(r.levered_irr)}</td>'
            f'<td><span class="tag {c.tag.value.lower()}">{c.tag.value}</span></td></tr>'
        )

    block_html = (
        f'<div class="block"><div class="h">Blocking verification — resolve before proposing</div>'
        f"{html.escape(blocking)}</div>"
        if blocking
        else ""
    )

    return f"""<title>{html.escape(label)} — Information Pack</title>
<style>{PACK_CSS}</style>
<div class="wrap">
  <p class="eyebrow">Neura · GridQ · Indicative — pre-diligence analysis</p>
  <h1>{html.escape(label)}</h1>
  <p class="lede">Behind-the-meter generation and storage assessment, sized from the site's own
  metered and invoiced data. Three cases — Pessimistic, Central, Optimistic — each with its
  assumptions stated and classified.</p>

  {block_html}

  <h2>The range</h2>
  {_range_block(res, site_key)}

  <h2>Executive summary</h2>
  <p><strong>Central case:</strong> {html.escape(central.driver)}</p>
  <dl class="kv">
    <dt>Recommended configuration</dt>
    <dd>{
        f"{central.bess_mw:g} MW / {central.bess_mwh:g} MWh battery"
        if central.bess_mw
        else "no battery"
    }{f" + {central.solar_kwp:g} kWp solar" if central.solar_kwp else ""}</dd>
    <dt>Year-1 revenue</dt><dd>£{central.revenue_gbp:,.0f}</dd>
    <dt>Capital cost</dt><dd>£{central.capex_gbp:,.0f}</dd>
    <dt>Operating cost</dt><dd>£{central.opex_gbp:,.0f}/yr</dd>
    <dt>Unlevered IRR</dt><dd>{_pct(r_central.unlevered_irr) if r_central else "n/a"}</dd>
    <dt>Levered IRR</dt><dd>{_pct(r_central.levered_irr) if r_central else "n/a"}</dd>
    <dt>Debt sized at</dt>
    <dd>{f"{r_central.gearing * 100:.0f}% ({r_central.debt_bound_by})" if r_central else "n/a"}</dd>
  </dl>

  <h2>Where the value comes from — Central case</h2>
  {_revenue_bars(central)}

  <h2>All three cases</h2>
  <div class="scroll"><table>
    <thead><tr><th>Case</th><th class="n">BESS<br>MW/MWh</th><th class="n">Solar<br>kWp</th>
    <th class="n">Revenue<br>£/yr</th><th class="n">Capex<br>£</th>
    <th class="n">Unlev.<br>IRR</th><th class="n">Levered<br>IRR</th><th>Class</th></tr></thead>
    <tbody>{case_rows}</tbody>
  </table></div>

  <h2>Method</h2>
  <div class="flow" style="display:flex;align-items:center;flex-wrap:wrap;gap:0">
    <div class="node">Metered &amp; invoiced data<span class="sm">2025 base year</span></div>
    <span class="arw">&#8594;</span>
    <div class="node">Load &amp; generation<span class="sm">per meter</span></div>
    <span class="arw">&#8594;</span>
    <div class="node">Sizing<span class="sm">3 cases</span></div>
    <span class="arw">&#8594;</span>
    <div class="node">Revenue stack<span class="sm">per stream</span></div>
    <span class="arw">&#8594;</span>
    <div class="node">Financing<span class="sm">DSCR-sized debt</span></div>
    <span class="arw">&#8594;</span>
    <div class="node">Returns<span class="sm">IRR / NPV</span></div>
  </div>
  <p>Debt is <strong>sized from cashflow, never asserted</strong>:
  <code>min(60% × capex, PV(CFADS_P90 ÷ DSCR))</code>. A structure that cannot service its own debt
  cannot appear in these numbers. Revenue escalates at 2.5% CPI; battery-attributable revenue fades
  2%/yr, solar 0.5%/yr; 15-year asset life; NPV at 8%.</p>

  <h2>Assumptions &amp; sensitivities</h2>
  <p>Every assumption carrying these numbers, stated. An unstated assumption is a defect.</p>
  <ul>{asms}</ul>
  {data_notes}

  <h2>Next steps</h2>
  <ul>
    {
        "<li><strong>Resolve the blocking verification above.</strong> "
        "Nothing here should be proposed to the client until it is closed.</li>"
        if blocking
        else ""
    }
    <li>Obtain half-hourly data for this site's meters — the client has offered it. It converts
    every shape-dependent figure here from an assumption into a measurement.</li>
    <li>Confirm the tariff decomposition (network, levies, capacity) behind the blended
    delivered rate; that split is where most avoided-cost value sits.</li>
    <li>Site survey: footprint, fire separation, access, and connection protection.</li>
  </ul>

  <div class="foot">
    Indicative — pre-diligence analysis. No underwriting liability is carried at this stage.
    Scenario design reviewed and approved by the Fable advisor (2026-08-05); all arithmetic is
    deterministic and reproducible from <code>gridq.portfolio_requirements.sites</code>.
  </div>
</div>"""


def build_deck(site_key: str) -> str:
    """~12-slide 16:9 deck for one site. One idea per slide."""
    cs = cases_for(site_key)
    label = cs[0].site_label
    res = _results_for(site_key)
    central = next((c for c in cs if c.case == "Central"), cs[0])
    rc = res.get("Central")
    ro = res.get("Optimistic")
    blocking = next((c.blocking for c in cs if c.blocking), None)

    trio = ""
    for case in CASES:
        r = res.get(case)
        if r is None:
            trio += (
                f'<div><div class="c">{case}</div><div class="v neg">—</div>'
                f'<div class="s">No proposal in this case.</div></div>'
            )
            continue
        c = r.case
        cfg = []
        if c.bess_mw:
            cfg.append(f"{c.bess_mw:g} MW / {c.bess_mwh:g} MWh")
        if c.solar_kwp:
            cfg.append(f"{c.solar_kwp:g} kWp solar")
        trio += (
            f'<div><div class="c">{case}</div>'
            f'<div class="v {_cls(r.levered_irr)}">{_pct(r.levered_irr, 0)}</div>'
            f'<div class="s">{" + ".join(cfg) or "—"}<br>'
            f"{_k(c.revenue_gbp)}/yr on {_k(c.capex_gbp)} capex</div></div>"
        )

    rev_rows = (
        "".join(
            f"<tr><td>{html.escape(n)}</td><td class='n2'>£{v:,.0f}</td></tr>"
            for n, v in sorted(central.revenue_lines.items(), key=lambda kv: -kv[1])
        )
        or "<tr><td colspan='2'><em>No proposal</em></td></tr>"
    )

    asm_items = "".join(
        f"<li>{html.escape(a)}</li>" for a in (cs[1].key_assumptions or cs[0].key_assumptions)[:4]
    )

    slides: list[str] = []

    slides.append(
        f'<section class="slide cover"><div class="n">01</div>'
        f'<p class="eb">Neura · Indicative — pre-diligence analysis</p>'
        f"<h1>{html.escape(label)}</h1>"
        f"<p>Behind-the-meter generation &amp; storage assessment, sized from the site's own "
        f"metered and invoiced data.</p></section>"
    )
    slides.append(
        f'<section class="slide"><div class="n">02</div><p class="eb">The answer</p>'
        f"<h2>Three cases, one honest range</h2>"
        f'<div class="trio">{trio}</div>'
        f'<div class="foot">Levered IRR · debt sized from cashflow at target DSCR, never '
        f"asserted</div></section>"
    )
    if rc:
        slides.append(
            f'<section class="slide"><div class="n">03</div><p class="eb">Central case</p>'
            f"<h2>{html.escape(central.driver)}</h2>"
            f'<div style="display:flex;gap:56px;align-items:flex-start;margin-top:12px">'
            f'<div><div class="big {_cls(rc.levered_irr)}">{_pct(rc.levered_irr, 1)}</div>'
            f'<p style="margin-top:10px">levered IRR</p></div>'
            f"<div><table>"
            f"<tr><td>Unlevered IRR</td><td class='n2'>{_pct(rc.unlevered_irr)}</td></tr>"
            f"<tr><td>Revenue, year 1</td><td class='n2'>£{central.revenue_gbp:,.0f}</td></tr>"
            f"<tr><td>Capital cost</td><td class='n2'>£{central.capex_gbp:,.0f}</td></tr>"
            f"<tr><td>Gearing (DSCR-sized)</td><td class='n2'>{rc.gearing * 100:.0f}%</td></tr>"
            f"<tr><td>DSCR, average</td><td class='n2'>{rc.dscr_avg:.2f}×</td></tr>"
            f"<tr><td>NPV @ 8%</td><td class='n2'>£{rc.npv_at_8pct:,.0f}</td></tr>"
            f"</table></div></div></section>"
        )
    slides.append(
        f'<section class="slide"><div class="n">04</div><p class="eb">Value</p>'
        f"<h2>Where the money comes from</h2>"
        f'<table><thead><tr><th>Revenue stream</th><th class="n2">£/yr, year 1</th></tr></thead>'
        f"<tbody>{rev_rows}</tbody></table>"
        f'<div class="foot">{html.escape(central.driver)}</div></section>'
    )
    slides.append(
        '<section class="slide"><div class="n">05</div><p class="eb">System</p>'
        "<h2>What gets installed</h2>"
        '<div class="flow">'
        '<div class="node">Grid / private wire<span class="sm">import</span></div>'
        '<span class="arw">&#8594;</span>'
        '<div class="node">Site load<span class="sm">metered 2025</span></div>'
        + (
            f'<span class="arw">&#8592;</span><div class="node">Solar'
            f'<span class="sm">{central.solar_kwp:g} kWp</span></div>'
            if central.solar_kwp
            else ""
        )
        + (
            f'<span class="arw">&#8596;</span><div class="node">Battery'
            f'<span class="sm">{central.bess_mw:g} MW / {central.bess_mwh:g} MWh</span></div>'
            if central.bess_mw
            else ""
        )
        + f"</div><p>{html.escape(central.driver)}</p></section>"
    )
    slides.append(
        '<section class="slide"><div class="n">06</div><p class="eb">Method</p>'
        "<h2>How the numbers are built</h2>"
        '<div class="flow">'
        '<div class="node">Meter data<span class="sm">2025</span></div>'
        '<span class="arw">&#8594;</span>'
        '<div class="node">Sizing<span class="sm">3 cases</span></div>'
        '<span class="arw">&#8594;</span>'
        '<div class="node">Revenue stack<span class="sm">per stream</span></div>'
        '<span class="arw">&#8594;</span>'
        '<div class="node">Financing<span class="sm">DSCR-sized</span></div>'
        '<span class="arw">&#8594;</span>'
        '<div class="node">Returns<span class="sm">IRR / NPV</span></div></div>'
        "<p>Debt is sized from the cashflow it has to service, never asserted as a percentage. "
        "A structure that cannot cover its own debt cannot appear in these numbers.</p></section>"
    )
    if blocking:
        slides.append(
            f'<section class="slide"><div class="n">07</div>'
            f'<p class="eb" style="color:var(--err)">Blocking verification</p>'
            f"<h2>One thing must be confirmed first</h2>"
            f'<p style="font-size:19px;color:var(--text)">{html.escape(blocking)}</p></section>'
        )
    slides.append(
        f'<section class="slide"><div class="n">{"08" if blocking else "07"}</div>'
        f'<p class="eb">Assumptions</p><h2>What these numbers rest on</h2>'
        f"<ul>{asm_items}</ul>"
        f'<div class="foot">Full register in the information pack · every assumption '
        f"classified Central / Favourable / Stretch</div></section>"
    )
    if ro:
        slides.append(
            f'<section class="slide"><div class="n">{"09" if blocking else "08"}</div>'
            f'<p class="eb">Upside</p><h2>What the optimistic case needs</h2>'
            f'<div class="big {_cls(ro.levered_irr)}">{_pct(ro.levered_irr, 1)}</div>'
            f'<p style="margin-top:14px">{html.escape(ro.case.driver)}</p>'
            f'<div class="foot">Classification: {ro.case.tag.value} — every carrying assumption '
            f"at the top of its defensible range</div></section>"
        )
    slides.append(
        f'<section class="slide"><div class="n">{"10" if blocking else "09"}</div>'
        f'<p class="eb">Next</p><h2>What we need from you</h2>'
        f"<ul>"
        + ("<li><strong>Resolve the blocking verification.</strong></li>" if blocking else "")
        + "<li><strong>Half-hourly meter data</strong> — converts every shape-dependent figure "
        "here from an assumption into a measurement.</li>"
        "<li><strong>One recent invoice</strong> with the full tariff breakdown — network, levies, "
        "capacity charges.</li>"
        "<li><strong>Site survey access</strong> — footprint, fire separation, connection "
        "protection.</li></ul>"
        '<div class="foot">Neura · Indicative — pre-diligence analysis · '
        "no underwriting liability carried at this stage</div></section>"
    )

    return f"""<title>{html.escape(label)} — Deck</title>
<style>{DECK_CSS}</style>
{"".join(slides)}"""
