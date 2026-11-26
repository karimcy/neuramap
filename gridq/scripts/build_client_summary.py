"""Build a COMPACT, client-ready one-pager per site from a published run.

Why this exists. The consulting pack is 190-213 KB with 22 exhibits, a 58 KB
assumptions register and a 61 KB per-stream register. That is the right artifact
for diligence and the wrong one to hand a client: nobody reads their way to the
number. This renders the same RECORD, never a recomputation, into one page: the
headline return, the revenue split per stream, the three charts that show HOW
(dispatch day, sizing surface, selection), and a short note on what it rests on.

Every figure is read from `quote.json`. Nothing here computes a financial number
(CLAUDE.md section 8), and nothing here is an underwriting output: the study basis
is stamped at the top of every page.
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gridq.schemas as s  # noqa: E402
from gridq.outputs import report_charts as rc  # noqa: E402

_CSS_PARTS = [
    ":root{--ink:#10151c;--mut:#5b6672;--line:#e3e8ee;--bg:#fff;",
    "--soft:#f6f8fa;--pos:#0a7d4b;--neg:#b0202c}",
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){",
    "--ink:#e8edf3;--mut:#9aa7b4;--line:#26303b;--bg:#0f1419;",
    "--soft:#161d25;--pos:#3ddc97;--neg:#ff6b7a}}",
    ":root[data-theme=dark]{--ink:#e8edf3;--mut:#9aa7b4;--line:#26303b;",
    "--bg:#0f1419;--soft:#161d25;--pos:#3ddc97;--neg:#ff6b7a}",
    "*{box-sizing:border-box}",
    "body{margin:0;background:var(--bg);color:var(--ink);",
    "font:15px/1.55 -apple-system,BlinkMacSystemFont,Inter,Helvetica,Arial,sans-serif}",
    ".wrap{max-width:1060px;margin:0 auto;padding:32px 24px 64px}",
    ".basis{background:var(--soft);border:1px solid var(--line);",
    "border-left:3px solid var(--mut);padding:12px 16px;border-radius:6px;",
    "font-size:13px;color:var(--mut);margin-bottom:28px}",
    "h1{font-size:27px;margin:0 0 4px;letter-spacing:-.02em}",
    ".sub{color:var(--mut);font-size:14px;margin-bottom:28px}",
    "h2{font-size:12.5px;text-transform:uppercase;letter-spacing:.08em;",
    "color:var(--mut);margin:38px 0 14px;padding-bottom:7px;",
    "border-bottom:1px solid var(--line)}",
    ".kpis{display:grid;gap:14px;",
    "grid-template-columns:repeat(auto-fit,minmax(148px,1fr))}",
    ".kpi{background:var(--soft);border:1px solid var(--line);",
    "border-radius:8px;padding:15px 17px}",
    ".kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.06em;",
    "color:var(--mut)}",
    ".kpi .v{font-size:25px;font-weight:600;letter-spacing:-.02em;margin-top:5px}",
    ".pos{color:var(--pos)}.neg{color:var(--neg)}",
    "table{width:100%;border-collapse:collapse;font-size:14px}",
    "th{text-align:left;font-size:11px;text-transform:uppercase;",
    "letter-spacing:.06em;color:var(--mut);padding:0 10px 7px;",
    "border-bottom:1px solid var(--line)}",
    "th.n,td.n{text-align:right;font-variant-numeric:tabular-nums}",
    "td{padding:8px 10px;border-bottom:1px solid var(--line)}",
    "tfoot td{font-weight:600;border-top:2px solid var(--line);border-bottom:0}",
    ".tag{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;",
    "color:var(--mut);border:1px solid var(--line);border-radius:3px;",
    "padding:1px 6px}",
    ".fig{margin:16px 0 6px;overflow-x:auto}",
    ".fig svg{max-width:100%;height:auto}",
    ".cap{font-size:12.5px;color:var(--mut);margin-bottom:22px}",
    "ul{margin:0;padding-left:19px}li{margin:5px 0;font-size:13.5px}",
    ".foot{margin-top:44px;padding-top:14px;",
    "border-top:1px solid var(--line);font-size:11.5px;color:var(--mut);",
    "line-height:1.6}",
]
CSS = "".join(_CSS_PARTS)

_BASIS = (
    "<strong>Feasibility study, not an underwriting output and not an offer.</strong> "
    "Produced on the named all-streams study basis: every revenue stream is modelled "
    "as open, and every contract, consent, gate, award and registration is "
    "<em>assumed approved</em> for this site and this battery size. Those assumptions "
    "are enumerated in the full assumptions register that accompanies this summary. "
    "Figures are modelled from the site's own metered half-hourly data."
)

_CAP_DISPATCH = (
    "One real solved day, half-hour by half-hour: charge, discharge and state of "
    "energy against the site's own load and its DUoS band."
)
_CAP_SURFACE = (
    "The full searched surface. Every point is a solved candidate; the marked point "
    "is the size the optimiser selected."
)
_CAP_SELECT = "Candidates ranked on the metric the search maximises."

_FOOT = (
    "Every figure on this page is read from one run record, stamped with its input "
    "hash, assumption release and code version. Nothing here is recomputed for "
    "presentation. This is a modelling result on assumed approvals: not a quotation, "
    "not a commitment, and not an underwriting output."
)


def _no_dashes(text: str) -> str:
    """House style: zero em/en dashes in anything a client reads.

    The site names themselves carry them (for example "Grundon - Bishops Cleeve
    (Wingmoor Farm) Ash Plant"), so scrubbing at the document seam is the only place
    that catches every route onto the page. Applied to the finished HTML, never to the
    record, so the stored figures and warnings are untouched.
    """
    return text.replace("\u2014", "-").replace("\u2013", "-")


def _money(v: float | None) -> str:
    return "n/a" if v is None else f"GBP {v:,.0f}"


def _kpi_html(label: str, value: str, cls: str) -> str:
    return (
        f"<div class=kpi><div class=l>{escape(label)}</div>"
        f'<div class="v {cls}">{escape(value)}</div></div>'
    )


def build(run_dir: Path) -> tuple[str, str]:
    """Render one site's compact summary. Returns (site name, html)."""
    quote = s.QuoteRecord.model_validate(json.loads((run_dir / "quote.json").read_text()))
    site = s.SiteModel.model_validate(json.loads((run_dir / "site.json").read_text()))
    result, case = quote.optimisation_result, quote.financial_case
    kw = result.selected_power_kw
    name = (site.site_name or site.site_id).strip()

    streams = sorted(
        (
            (v.stream.value, float(v.annual_revenue_gbp or 0.0), v.firmness.value)
            for v in list(result.value_streams) + list(result.gated_upside or [])
        ),
        key=lambda row: -row[1],
    )
    total = sum(row[1] for row in streams)
    per_kw = (total / kw) if kw else 0.0
    irr = None if case is None else case.irr

    kpis = [
        ("System", f"{kw:,.1f} kW" if kw else "none", ""),
        ("Duration", f"{result.duration_hours:,.1f} h" if result.duration_hours else "n/a", ""),
        ("Annual revenue", _money(total), ""),
        ("Per kW", f"GBP {per_kw:,.0f}", ""),
    ]
    if irr is not None:
        kpis.append(("Project IRR", f"{irr:,.2f}%", "pos" if irr > 0 else "neg"))
    if case is not None and case.payback_years is not None:
        kpis.append(("Payback", f"{case.payback_years:,.1f} yr", ""))

    rows = "".join(
        f"<tr><td>{escape(n.replace('_', ' ').title())}</td>"
        f"<td class=n>{v:,.0f}</td>"
        f"<td class=n>{(v / kw if kw else 0):,.1f}</td>"
        f"<td><span class=tag>{escape(f)}</span></td></tr>"
        for n, v, f in streams
        if v > 0
    )
    zeros = [n.replace("_", " ").title() for n, v, _ in streams if v <= 0]
    zero_note = f"Streams modelled at zero: {escape(', '.join(zeros))}." if zeros else ""

    figures = (
        (
            "How the battery trades on its busiest day",
            _CAP_DISPATCH,
            rc.firming_diagram(profile=quote.dispatch_profile, site=site),
        ),
        (
            "Every size evaluated, and the one selected",
            _CAP_SURFACE,
            rc.sizing_surface(grid=quote.sizing_grid),
        ),
        ("How the selection was made", _CAP_SELECT, rc.milp_selection(grid=quote.sizing_grid)),
    )
    figs = "".join(
        f"<h2>{escape(title)}</h2><div class=fig>{svg}</div><div class=cap>{escape(cap)}</div>"
        for title, cap, svg in figures
        if svg
    )

    rests = [
        "Every contract, consent, award, registration and threshold is assumed "
        "approved for this site and battery size.",
        "Revenue is modelled from the site's own metered half-hourly data and "
        "published GB market rates.",
        f"Assumption release {escape(quote.assumption_version or 'unstamped')}; "
        "confidence band "
        f"{escape(str(getattr(quote, 'confidence_band', None) or 'not stated'))}.",
        "The full assumptions register, per stream, accompanies this summary.",
    ]

    html = (
        f"<style>{CSS}</style>"
        f"<div class=wrap>"
        f"<div class=basis>{_BASIS}</div>"
        f"<h1>{escape(name)}</h1>"
        f"<div class=sub>Battery feasibility summary &middot; run "
        f"{escape(quote.quote_id[:20])}</div>"
        f"<h2>Headline</h2>"
        f"<div class=kpis>{''.join(_kpi_html(a, b, c) for a, b, c in kpis)}</div>"
        f"<h2>Where the revenue comes from</h2>"
        f"<table><thead><tr><th>Revenue stream</th><th class=n>GBP/yr</th>"
        f"<th class=n>GBP/kW/yr</th><th>Basis</th></tr></thead>"
        f"<tbody>{rows}</tbody><tfoot><tr><td>Total</td>"
        f"<td class=n>{total:,.0f}</td><td class=n>{per_kw:,.1f}</td><td></td>"
        f"</tr></tfoot></table>"
        f"<div class=cap>{zero_note}</div>"
        f"{figs}"
        f"<h2>What this rests on</h2>"
        f"<ul>{''.join(f'<li>{r}</li>' for r in rests)}</ul>"
        f"<div class=foot>{_FOOT}</div>"
        f"</div>"
    )
    return name, _no_dashes(html)


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for raw in argv[2:]:
        run_dir = Path(raw)
        name, html = build(run_dir)
        # A short readable filename. The full site name runs to 90-odd characters with
        # punctuation and an MPAN, which makes an unusable path, so key off the run's own
        # site_id instead: stable, already unique per site, and free of punctuation.
        site_id = json.loads((run_dir / "site.json").read_text()).get("site_id", run_dir.name)
        slug = str(site_id).lower().replace(" ", "_").replace("/", "-")
        path = out / f"{slug}_summary.html"
        path.write_text(html, encoding="utf-8")
        print(f"  {path.name}  ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
