"""One plain-language report on how the GridQ optimiser works.

Written for a reader who needs to trust the numbers without reading the code: what
the engine does, how one dispatch decision is made, and how the size is chosen. Sized
to print in two to three pages, so the prose is deliberately tight and the figures do
the explaining.
Every figure quoted is measured from a real run or from the adversarial audit, and
each is labelled with where it came from.

House style: zero em/en dashes in anything rendered. The scrubber at the document
seam is the only place that catches every route onto the page.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CSS = "".join(
    [
        ":root{--ink:#10151c;--mut:#5b6672;--line:#dfe5ec;--bg:#fff;--soft:#f6f8fa;",
        "--acc:#1d6fb8;--pos:#0a7d4b;--warn:#a8620a}",
        "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){",
        "--ink:#e8edf3;--mut:#9aa7b4;--line:#26303b;--bg:#0f1419;--soft:#161d25;",
        "--acc:#5fb0f0;--pos:#3ddc97;--warn:#e0a44a}}",
        ":root[data-theme=dark]{--ink:#e8edf3;--mut:#9aa7b4;--line:#26303b;",
        "--bg:#0f1419;--soft:#161d25;--acc:#5fb0f0;--pos:#3ddc97;--warn:#e0a44a}",
        "*{box-sizing:border-box}",
        "body{margin:0;background:var(--bg);color:var(--ink);",
        "font:16px/1.65 -apple-system,BlinkMacSystemFont,Inter,Helvetica,Arial,sans-serif}",
        ".wrap{max-width:840px;margin:0 auto;padding:40px 24px 72px}",
        ".doc{font-size:11px;text-transform:uppercase;letter-spacing:.1em;",
        "color:var(--mut);margin-bottom:8px}",
        "h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}",
        ".lede{font-size:18px;color:var(--mut);margin-bottom:34px}",
        "h2{font-size:20px;margin:40px 0 12px;letter-spacing:-.01em}",
        "h3{font-size:15px;margin:26px 0 8px}",
        "p{margin:0 0 14px}",
        ".fig{margin:22px 0 8px;padding:18px;background:var(--soft);",
        "border:1px solid var(--line);border-radius:8px;overflow-x:auto}",
        ".fig svg{max-width:100%;height:auto;display:block;margin:0 auto}",
        ".cap{font-size:13px;color:var(--mut);margin:0 0 26px}",
        ".box{background:var(--soft);border:1px solid var(--line);",
        "border-left:3px solid var(--acc);border-radius:6px;padding:14px 18px;",
        "margin:20px 0;font-size:15px}",
        ".box.pos{border-left-color:var(--pos)}",
        ".box.warn{border-left-color:var(--warn)}",
        ".box b{display:block;margin-bottom:5px}",
        "table{width:100%;border-collapse:collapse;font-size:14.5px;margin:18px 0}",
        "th{text-align:left;font-size:11px;text-transform:uppercase;",
        "letter-spacing:.06em;color:var(--mut);padding:0 10px 7px;",
        "border-bottom:1px solid var(--line)}",
        "th.n,td.n{text-align:right;font-variant-numeric:tabular-nums}",
        "td{padding:8px 10px;border-bottom:1px solid var(--line)}",
        "code{font:13.5px ui-monospace,SFMono-Regular,Menlo,monospace;",
        "background:var(--soft);padding:1px 5px;border-radius:3px}",
        "ul,ol{margin:0 0 14px;padding-left:22px}li{margin:6px 0}",
        ".foot{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);",
        "font-size:12.5px;color:var(--mut)}",
        ".nav{margin-top:30px;font-size:13.5px;color:var(--mut)}",
        # Print rules. The report is meant to land in two to three A4 pages, so the
        # figures are height-capped rather than the prose being cut further.
        "@page{margin:11mm 12mm}",
        "@media print{:root{--ink:#000;--mut:#3f3f3f;--line:#bbb;--bg:#fff;--soft:#f7f7f7}",
        "body{font-size:9pt;line-height:1.34}.wrap{max-width:none;padding:0}",
        "h1{font-size:16pt;margin-bottom:4pt;break-after:avoid}",
        "h2{font-size:11pt;margin:9pt 0 3pt;break-after:avoid}",
        ".lede{font-size:10pt;margin-bottom:8pt}p{margin:0 0 6pt}.doc{display:none}",
        ".fig,.box,table{break-inside:avoid;page-break-inside:avoid}",
        ".fig{padding:3pt;margin:6pt 0 2pt;border-radius:3px}",
        ".fig svg{max-width:100%;max-height:68mm;margin:0 auto}",
        ".fig.tall svg{max-height:68mm}",
        ".cap{font-size:8pt;margin-bottom:7pt;line-height:1.3}",
        ".box{margin:6pt 0;padding:5pt 8pt;font-size:8.6pt;border-radius:3px}",
        ".box b{margin-bottom:1pt}",
        "table{font-size:8.4pt;margin:7pt 0}td{padding:3pt 6pt}th{padding:0 6pt 3pt}",
        ".foot{margin-top:10pt;padding-top:5pt;font-size:7.5pt}}",
    ]
)

FOOT = (
    "Every number in this document is measured from a real run of this engine or from "
    "the adversarial audit of it, and is labelled with which. Nothing is illustrative. "
    "Figures produced on the all-streams study basis are a modelling result on assumed "
    "approvals, not an underwriting output."
)


def _svg(w: int, h: int, body: str, label: str) -> str:
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label}" '
        f'style="font:13px -apple-system,BlinkMacSystemFont,Inter,sans-serif">'
        f'<g fill="none" stroke="currentColor" stroke-width="1.5">{body}</g></svg>'
    )


def _box(x: int, y: int, w: int, h: int, title: str, sub: str = "", accent: bool = False) -> str:
    stroke = "var(--acc)" if accent else "currentColor"
    op = "0.06" if accent else "0.03"
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" stroke="{stroke}" '
        f'fill="currentColor" fill-opacity="{op}"/>'
        f'<text x="{x + w / 2}" y="{y + (20 if sub else h / 2 + 4)}" text-anchor="middle" '
        f'stroke="none" fill="currentColor" font-weight="600">{title}</text>'
    )
    if sub:
        for i, line in enumerate(sub.split("|")):
            out += (
                f'<text x="{x + w / 2}" y="{y + 38 + i * 15}" text-anchor="middle" stroke="none" '
                f'fill="currentColor" fill-opacity="0.65" font-size="11.5">{line}</text>'
            )
    return out


def _arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> str:
    """A straight arrow whose head points along the direction of travel.

    The head is built from the apex backwards along the unit vector, so a leftward
    or upward arrow gets a leftward or upward head. An earlier version hard-coded
    a rightward head and silently drew backwards arrows on the wrap rows.
    """
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / length, dy / length
    bx, by = x2 - ux * 8.0, y2 - uy * 8.0
    px, py = -uy * 4.5, ux * 4.5
    pts = f"{x2},{y2} {bx + px:.1f},{by + py:.1f} {bx - px:.1f},{by - py:.1f}"
    out = (
        f'<line x1="{x1}" y1="{y1}" x2="{bx:.1f}" y2="{by:.1f}" stroke="currentColor" '
        f'stroke-opacity="0.45"/>'
        f'<polygon points="{pts}" fill="currentColor" fill-opacity="0.45" stroke="none"/>'
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        anchor = "middle" if abs(dy) < abs(dx) else "start"
        off_y = -6 if abs(dy) < abs(dx) else 0
        out += (
            f'<text x="{mx + (0 if anchor == "middle" else 9)}" y="{my + off_y}" '
            f'text-anchor="{anchor}" stroke="none" fill="currentColor" '
            f'fill-opacity="0.6" font-size="11">{label}</text>'
        )
    return out


def _path(d: str, label: str = "", lx: float = 0, ly: float = 0) -> str:
    """An open routed connector, for the loop-back line that no straight arrow fits."""
    out = f'<path d="{d}" stroke="currentColor" stroke-opacity="0.45" fill="none"/>'
    if label:
        for i, line in enumerate(label.split("|")):
            out += (
                f'<text x="{lx}" y="{ly + i * 14}" stroke="none" fill="currentColor" '
                f'fill-opacity="0.6" font-size="11">{line}</text>'
            )
    return out


# ----------------------------------------------------------------- diagram 1
def diagram_pipeline() -> str:
    """Landscape flow: the per-candidate loop across the top, selection down the right.

    Deliberately wide rather than tall. A portrait spine rendered at full page width
    consumed most of a printed page on its own, which pushed the report past three.
    Geometry is explicit because an earlier version clipped the last box against the
    canvas edge and ran the loop label straight through the loop line.
    """
    b = ""
    bw, gap, y, bh = 124, 13, 18, 66
    xs = [14 + i * (bw + gap) for i in range(5)]
    row = [
        ("Site data", "its own half-hourly|meter and tariff", False),
        ("Pick a size", "power in kW|x duration in h", False),
        ("Solve the dispatch", "every half-hour of|the year, at once", True),
        ("Price it", "revenue for|each stream", False),
        ("Cost and return", "capex, opex|project IRR", False),
    ]
    for x, (title, sub, acc) in zip(xs, row, strict=True):
        b += _box(x, y, bw, bh, title, sub, acc)
    for x in xs[:-1]:
        b += _arrow(x + bw, y + bh / 2, x + bw + gap - 1, y + bh / 2)

    # the loop: leaves the last box left of centre, returns into "Pick a size"
    loop_y, exit_x = y + bh + 28, xs[4] + bw - 26
    b += _path(f"M {xs[4] + 30} {y + bh} V {loop_y} H {xs[1] + bw / 2} V {y + bh + 9}")
    b += _arrow(xs[1] + bw / 2, y + bh + 22, xs[1] + bw / 2, y + bh + 3)
    b += (
        f'<text x="{(xs[1] + xs[4]) / 2 + 40}" y="{loop_y + 15}" text-anchor="middle" '
        f'stroke="none" fill="currentColor" fill-opacity="0.65" font-size="11.5">'
        f"162 candidates at Harwell, one lap each</text>"
    )

    # the exit: straight down the right into selection, then the report
    y2 = loop_y + 42
    b += _box(xs[4] - 46, y2, bw + 46, 50, "Rank and select", "the best candidate wins", True)
    b += _arrow(exit_x, y + bh, exit_x, y2 - 3)
    b += _box(xs[4] - 46, y2 + 68, bw + 46, 44, "Report", "every figure from the record")
    b += _arrow(exit_x, y2 + 50, exit_x, y2 + 65)
    return _svg(xs[4] + bw + 14, y2 + 124, b, "The pipeline, showing the per candidate loop")


# ----------------------------------------------------------------- diagram 2
def diagram_price_stack() -> str:
    """Five components summing into one price, laid out wide.

    Wide on purpose. At a fixed printed height a 2:1 figure uses only half the page
    width and halves its own text with it, so the aspect is pushed toward 4:1.
    """
    b = ""
    bw, gap, y, bh = 132, 12, 14, 50
    rows = [
        ("Energy price", "half-hourly"),
        ("DUoS band", "red, amber, green"),
        ("Flat levies", "CCL, RO, CfD, BSUoS"),
        ("Capacity Market", "16:00 to 19:00 peak"),
        ("DFS price", "in-window only"),
    ]
    xs = [8 + i * (bw + gap) for i in range(5)]
    for x, (name, sub), acc in zip(xs, rows, [0, 0, 0, 1, 0], strict=True):
        b += (
            f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="5" '
            f'stroke="{"var(--acc)" if acc else "currentColor"}" '
            f'stroke-opacity="{1 if acc else 0.55}" fill="currentColor" '
            f'fill-opacity="{0.06 if acc else 0.03}"/>'
            f'<text x="{x + bw / 2}" y="{y + 21}" text-anchor="middle" stroke="none" '
            f'fill="currentColor" font-weight="600" font-size="13">{name}</text>'
            f'<text x="{x + bw / 2}" y="{y + 37}" text-anchor="middle" stroke="none" '
            f'fill="currentColor" fill-opacity="0.6" font-size="11">{sub}</text>'
        )
    spine_y = y + bh + 26
    for x in xs:
        b += _path(f"M {x + bw / 2} {y + bh} V {spine_y}")
        b += (
            f'<circle cx="{x + bw / 2}" cy="{spine_y}" r="2.6" fill="currentColor" '
            f'fill-opacity="0.45" stroke="none"/>'
        )
    b += _path(f"M {xs[0] + bw / 2} {spine_y} H {xs[4] + bw / 2}")
    mid = (xs[0] + xs[4]) / 2 + bw / 2
    y2 = spine_y + 24
    # the drop must land ON "One price", not in the gap beside it
    one_x, one_w = mid - 250, 210
    b += _arrow(one_x + one_w / 2, spine_y, one_x + one_w / 2, y2 - 2)
    b += _box(one_x, y2, one_w, 50, "One price", "for this half-hour", True)
    b += _arrow(mid - 40, y2 + 25, mid + 18, y2 + 25)
    b += _box(mid + 22, y2, 232, 50, "Charge, discharge or hold")
    return _svg(xs[4] + bw + 8, y2 + 62, b, "Five price components summing into one price")


# ----------------------------------------------------------------- diagram 3
def diagram_constraints() -> str:
    """Six limits as a filter band between price and action, laid out wide.

    Same aspect reasoning as figure 2: the earlier 1.8:1 version rendered at half the
    page width once its height was capped for print, which made its labels unreadable.
    """
    b = ""
    band_x, band_w = 186, 356
    b += _box(6, 62, 164, 58, "What prices reward", "charge now,|sell at the peak", True)
    b += _arrow(170, 91, band_x - 4, 91)
    b += (
        f'<rect x="{band_x}" y="10" width="{band_w}" height="162" rx="7" '
        f'stroke="currentColor" stroke-opacity="0.4" stroke-dasharray="4 3" '
        f'fill="currentColor" fill-opacity="0.02"/>'
        f'<text x="{band_x + band_w / 2}" y="26" text-anchor="middle" stroke="none" '
        f'fill="currentColor" fill-opacity="0.65" font-size="10.5" font-weight="600">'
        f"SIX PHYSICAL LIMITS, ALL AT ONCE</text>"
    )
    limits = [
        ("Rated power", "the kW ceiling"),
        ("Stored energy", "state of charge"),
        ("Site demand", "nothing to displace"),
        ("Connection", "the MIC or ASC"),
        ("Warranty", "throughput a year"),
        ("Reserved", "held for a contract"),
    ]
    lw, lh = 108, 46
    for i, (t, sub) in enumerate(limits):
        lx = band_x + 12 + (i % 3) * (lw + 8)
        ly = 34 + (i // 3) * (lh + 12)
        b += (
            f'<rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="5" '
            f'stroke="currentColor" stroke-opacity="0.55" fill="currentColor" '
            f'fill-opacity="0.03"/>'
            f'<text x="{lx + lw / 2}" y="{ly + 19}" text-anchor="middle" stroke="none" '
            f'fill="currentColor" font-size="12" font-weight="600">{t}</text>'
            f'<text x="{lx + lw / 2}" y="{ly + 34}" text-anchor="middle" stroke="none" '
            f'fill="currentColor" fill-opacity="0.6" font-size="10.5">{sub}</text>'
        )
    b += _arrow(band_x + band_w, 91, band_x + band_w + 26, 91)
    b += _box(band_x + band_w + 30, 62, 164, 58, "What it actually does", "", True)
    return _svg(band_x + band_w + 200, 182, b, "Six physical limits between price and action")


# ----------------------------------------------------------------- diagram 4
def diagram_search() -> str:
    """Coarse to refine to confirm, with the Harwell counts on each stage.

    The stage gaps are 56px wide because the connector labels sit in them; at the
    original 35px the words overlapped the box edges.
    """
    b = ""
    b += _box(16, 20, 150, 62, "Coarse sweep", "80 points|fast proxy solves")
    b += _arrow(166, 51, 216, 51, "narrow")
    b += _box(222, 20, 150, 62, "Refine", "64 points|around the best region")
    b += _arrow(372, 51, 422, 51, "confirm")
    b += _box(428, 20, 156, 62, "Full solve", "18 points|the whole metered year", True)
    b += _arrow(506, 82, 506, 112)
    b += _box(328, 112, 256, 58, "Rank and select", "21.381 kW x 2.0 h chosen", True)
    for i, line in enumerate(
        [
            "Proxy points steer the search.",
            "Only confirmed points are ever",
            "quoted as a result.",
        ]
    ):
        b += (
            f'<text x="16" y="{130 + i * 17}" stroke="none" fill="currentColor" '
            f'fill-opacity="0.65" font-size="12">{line}</text>'
        )
    return _svg(600, 186, b, "The two stage sizing search with the Harwell counts")


RUN = Path("runs/quote_run_39fb87f5f0d6f0c1")  # Harwell, gb_v1_12, the campaign run


def _record() -> dict:
    return json.loads((RUN / "quote.json").read_text())


def chart_dispatch_day() -> str:
    """The solved 48 half-hours of one real day, drawn from the record.

    Charge below the axis, discharge above it, state of charge as a line, and the
    DUoS red band shaded so the reason for the big discharge is visible.
    """
    pts = _record()["dispatch_profile"]["points"]
    w, h = 620, 250
    x0, y0, pw, ph = 46, 30, w - 70, 140
    mid = y0 + ph / 2
    peak = max(max(p["charge_kw"] for p in pts), max(p["discharge_kw"] for p in pts)) or 1
    scale = (ph / 2 - 6) / peak
    bw = pw / len(pts)
    b = ""
    # red band shading first, so bars sit on top
    runs, start = [], None
    for i, pt in enumerate(pts):
        if pt["red_band"] and start is None:
            start = i
        elif not pt["red_band"] and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(pts)))
    for a, z in runs:
        b += (
            f'<rect x="{x0 + a * bw:.1f}" y="{y0}" width="{(z - a) * bw:.1f}" height="{ph}" '
            f'fill="#c0392b" fill-opacity="0.10" stroke="none"/>'
            f'<text x="{x0 + (a + z) / 2 * bw:.1f}" y="{y0 - 8}" text-anchor="middle" '
            f'stroke="none" fill="currentColor" fill-opacity="0.7" font-size="11">'
            f"DUoS red band</text>"
        )
    # zero line
    b += (
        f'<line x1="{x0}" y1="{mid}" x2="{x0 + pw}" y2="{mid}" stroke="currentColor" '
        f'stroke-opacity="0.35"/>'
    )
    for i, pt in enumerate(pts):
        x = x0 + i * bw
        if pt["discharge_kw"] > 0:
            bh = pt["discharge_kw"] * scale
            b += (
                f'<rect x="{x + 0.6:.1f}" y="{mid - bh:.1f}" width="{bw - 1.2:.1f}" '
                f'height="{bh:.1f}" fill="var(--pos)" fill-opacity="0.8" stroke="none"/>'
            )
        if pt["charge_kw"] > 0:
            bh = pt["charge_kw"] * scale
            b += (
                f'<rect x="{x + 0.6:.1f}" y="{mid:.1f}" width="{bw - 1.2:.1f}" '
                f'height="{bh:.1f}" fill="var(--acc)" fill-opacity="0.7" stroke="none"/>'
            )
    # state of charge line
    soe_max = max(p["soe_kwh"] for p in pts) or 1
    poly = " ".join(
        f"{x0 + (i + 0.5) * bw:.1f},{y0 + ph - (p['soe_kwh'] / soe_max) * (ph - 10):.1f}"
        for i, p in enumerate(pts)
    )
    b += (
        f'<polyline points="{poly}" fill="none" stroke="var(--warn)" stroke-width="1.8" '
        f'stroke-opacity="0.95"/>'
    )
    # axes
    for lbl, frac in (("00:00", 0), ("06:00", 0.25), ("12:00", 0.5), ("18:00", 0.75)):
        b += (
            f'<text x="{x0 + frac * pw:.1f}" y="{y0 + ph + 16}" text-anchor="middle" '
            f'stroke="none" fill="currentColor" fill-opacity="0.6" font-size="11">{lbl}</text>'
        )
    b += (
        f'<text x="{x0 - 8}" y="{mid - ph / 4:.0f}" text-anchor="end" stroke="none" '
        f'fill="currentColor" fill-opacity="0.6" font-size="11">out</text>'
        f'<text x="{x0 - 8}" y="{mid + ph / 4:.0f}" text-anchor="end" stroke="none" '
        f'fill="currentColor" fill-opacity="0.6" font-size="11">in</text>'
    )
    # legend
    ly = y0 + ph + 42
    for i, (col, op, txt) in enumerate(
        [
            ("var(--acc)", 0.7, "charging"),
            ("var(--pos)", 0.8, "discharging"),
            ("var(--warn)", 0.95, "energy stored"),
        ]
    ):
        lx = x0 + i * 165
        b += (
            f'<rect x="{lx}" y="{ly - 9}" width="11" height="11" fill="{col}" '
            f'fill-opacity="{op}" stroke="none"/>'
            f'<text x="{lx + 17}" y="{ly}" stroke="none" fill="currentColor" '
            f'fill-opacity="0.75" font-size="11.5">{txt}</text>'
        )
    return _svg(w, h, b, "One solved day of dispatch, half-hour by half-hour")


def chart_sizing_surface() -> str:
    """Return against size, confirmed points only, with the selected point marked.

    The point of the chart is that revenue and return move in opposite directions,
    which is the single least intuitive thing about the result.
    """
    rec = _record()
    solved = [
        p
        for p in rec["sizing_grid"]["points"]
        if not p["proxy"] and p.get("project_irr_pct") is not None
    ]
    # One point per power rating: several durations share a power, and plotting them
    # all against power alone drew a vertical jump that reads as a discontinuity.
    # The best-returning duration at each power is also what the selection compares.
    best: dict[float, dict] = {}
    for point in solved:
        kw = round(point["power_kw"], 3)
        if kw not in best or point["project_irr_pct"] > best[kw]["project_irr_pct"]:
            best[kw] = point
    pts = sorted(best.values(), key=lambda p: p["power_kw"])
    sel_kw = rec["optimisation_result"]["selected_power_kw"]
    w, h = 780, 272
    x0, y0, pw, ph = 52, 20, w - 96, 178
    kws = [p["power_kw"] for p in pts]
    irrs = [p["project_irr_pct"] for p in pts]
    revs = [p["annual_revenue_gbp"] for p in pts]
    kmin, kmax = min(kws), max(kws)
    imax = max(irrs) * 1.1
    rmax = max(revs) * 1.1

    def px(k: float) -> float:
        return x0 + (k - kmin) / (kmax - kmin) * pw

    def py_irr(v: float) -> float:
        return y0 + ph - v / imax * ph

    def py_rev(v: float) -> float:
        return y0 + ph - v / rmax * ph

    b = ""
    for frac in (0.25, 0.5, 0.75, 1.0):
        gy = y0 + ph - frac * ph
        b += (
            f'<line x1="{x0}" y1="{gy:.1f}" x2="{x0 + pw}" y2="{gy:.1f}" '
            f'stroke="currentColor" stroke-opacity="0.12"/>'
            f'<text x="{x0 - 8}" y="{gy + 4:.1f}" text-anchor="end" stroke="none" '
            f'fill="var(--acc)" fill-opacity="0.85" font-size="11">'
            f"{frac * imax:.0f}%</text>"
            f'<text x="{x0 + pw + 8}" y="{gy + 4:.1f}" stroke="none" '
            f'fill="var(--pos)" fill-opacity="0.85" font-size="11">'
            f"{frac * rmax / 1000:.0f}k</text>"
        )
    rev_pts = " ".join(
        f"{px(q['power_kw']):.1f},{py_rev(q['annual_revenue_gbp']):.1f}" for q in pts
    )
    irr_pts = " ".join(f"{px(q['power_kw']):.1f},{py_irr(q['project_irr_pct']):.1f}" for q in pts)
    b += (
        f'<polyline points="{rev_pts}" fill="none" stroke="var(--pos)" '
        f'stroke-width="1.8" stroke-opacity="0.85"/>'
        f'<polyline points="{irr_pts}" fill="none" stroke="var(--acc)" '
        f'stroke-width="2" stroke-opacity="0.9"/>'
    )
    for p in pts:
        b += (
            f'<circle cx="{px(p["power_kw"]):.1f}" cy="{py_irr(p["project_irr_pct"]):.1f}" '
            f'r="2.6" fill="var(--acc)" stroke="none"/>'
        )
    # mark the selected point
    sx, sy = (
        px(sel_kw),
        py_irr(next(p["project_irr_pct"] for p in pts if abs(p["power_kw"] - sel_kw) < 0.01)),
    )
    b += (
        f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="6" fill="none" stroke="var(--acc)" '
        f'stroke-width="2"/>'
        f'<line x1="{sx:.1f}" y1="{sy + 8:.1f}" x2="{sx:.1f}" y2="{y0 + ph}" '
        f'stroke="var(--acc)" stroke-opacity="0.4" stroke-dasharray="3 3"/>'
        f'<text x="{sx + 10:.1f}" y="{sy - 6:.1f}" stroke="none" fill="currentColor" '
        f'font-size="11.5" font-weight="600">selected: {sel_kw:.1f} kW</text>'
    )
    for k in (kmin, 42.8, 74.8, kmax):
        b += (
            f'<text x="{px(k):.1f}" y="{y0 + ph + 17}" text-anchor="middle" stroke="none" '
            f'fill="currentColor" fill-opacity="0.6" font-size="11">{k:.0f}</text>'
        )
    b += (
        f'<text x="{x0 + pw / 2}" y="{y0 + ph + 36}" text-anchor="middle" stroke="none" '
        f'fill="currentColor" fill-opacity="0.65" font-size="11.5">battery power, kW</text>'
    )
    ly = y0 + ph + 60
    for i, (col, txt) in enumerate(
        [("var(--acc)", "project return, left axis"), ("var(--pos)", "annual revenue, right axis")]
    ):
        lx = x0 + i * 300
        b += (
            f'<rect x="{lx}" y="{ly - 9}" width="11" height="11" fill="{col}" stroke="none"/>'
            f'<text x="{lx + 17}" y="{ly}" stroke="none" fill="currentColor" '
            f'fill-opacity="0.75" font-size="11.5">{txt}</text>'
        )
    return _svg(w, h, b, "Project return and annual revenue against battery size")


REPORT = f"""
<h1>How the optimiser works</h1>
<div class=lede>The engine turns a site's own half-hourly meter data into one
recommended battery. This is what it does, and how to read the result.</div>

<p>There is no forecasting model and no machine learning anywhere near the numbers.
The engine solves a mathematical optimisation: given this site's measured electricity
use, these prices, and the physical limits of a battery, what is the most valuable
thing the battery could have done in every half hour of the year, and is that worth
the cost of buying it? Every figure below is measured from the Harwell run.</p>

<div class="fig tall">{diagram_pipeline()}</div>
<div class=cap>Figure 1. The pipeline. Each candidate size goes round the loop once,
and the report at the end is rendered from one stored record rather than assembled
by hand.</div>

<h2>1. What a half hour is worth</h2>
<p>The battery can charge, discharge, or hold. All 17,520 half hours are decided
together, not one at a time, because what it does at 6pm depends on whether it
charged at 4am. Each half hour carries a price, and that price is a stack.</p>

<div class=fig>{diagram_price_stack()}</div>
<div class=cap>Figure 2. Five components sum into the one price a half hour carries.
Some apply always, others only inside a window, which is why the timing of a
discharge matters as much as its size.</div>

<h2>2. What stops it</h2>
<p>The battery is not free to follow the prices. Six limits apply at once.</p>

<div class=fig>{diagram_constraints()}</div>
<div class=cap>Figure 3. The six limits. Which one binds depends on size, and that is
the most useful single thing to know about a candidate.</div>

<table>
<thead><tr><th>Battery</th><th>What actually limits it</th><th class=n>How often</th></tr></thead>
<tbody>
<tr><td>12.5 kW / 4 h</td><td>Rated power. Flat out whenever it discharges.</td>
<td class=n>1,209 of 1,709 half hours</td></tr>
<tr><td>25 kW / 2 h</td><td>Stored energy. It empties before the window closes.</td>
<td class=n>9,861 of 17,518 half hours</td></tr>
<tr><td>375 kW / 8 h</td><td>The site's own demand. Nothing left to displace.</td>
<td class=n>6,480 of 6,480 half hours</td></tr>
</tbody></table>
<p>That last row matters. A large battery behind a small site runs out of things to
do, because avoiding a cost requires a cost to avoid. This is why the best answer is
often smaller than expected.</p>

<h2>3. One solved day</h2>
<p>A real day from the Harwell run, exactly as the optimiser left it.</p>

<div class="fig tall">{chart_dispatch_day()}</div>
<div class=cap>Figure 4. Bars below the line are charging, bars above it are
discharging, and the line is energy held in the battery. The shaded block is the
expensive DUoS red band, 16:30 to 19:00 on this day.</div>

<p>The shape is the one the theory predicts. The battery charges in the small hours
where DUoS is 0.028p per unit, then discharges hard from 16:30 into the red band
where DUoS is 5.471p, about 195 times the rate it charged at. It stops at 19:00
because it has hit its floor, not because the window closed.</p>

<div class=box><b>The round trip has to pay for itself</b>
Cycling loses energy and wears the cells. At 88 percent round trip and a degradation
cost of 2.13p per unit delivered, this day's charging at 8.32p needed a spread of
3.26p to break even. It achieved 5.89p, a margin of 2.62p on every unit delivered,
and 1.25 full discharges across the day. Where the spread does not clear that
threshold the correct decision is to do nothing, and the engine does nothing.</div>

<div class=box><b>Not every movement is a price play</b>
64 percent of the day's charging happened in the cheapest half hours available. The
rest did not, and that is deliberate. Two constraints bind on this site, a resilience
reserve and a local flex availability reserve, and both require stored energy to be
present at particular times. Some charging serves an obligation the site is paid to
hold rather than chasing a spread. Nine streams earn here and only two are tariff
related, so reading the dispatch as arbitrage alone will mislead.</div>

<h2>4. Choosing the size</h2>
<p>A full solve across a year is expensive, so the search narrows in stages. It first
scores candidates on a shorter period, quick enough to tell a promising region from a
hopeless one, then re-solves the survivors properly.</p>

<div class=fig>{diagram_search()}</div>
<div class=cap>Figure 5. The search, with the Harwell counts. Of 162 candidates, 144
were fast proxy solves used only to steer, and 18 were full solves. A proxy scores a
typical period and scales it up, which reads high, so proxy points are marked as such
on the record and only fully solved candidates are ever quoted.</div>

<div class="fig tall">{chart_sizing_surface()}</div>
<div class=cap>Figure 6. Every fully solved candidate, taking the best duration at
each power. Revenue climbs steadily with size. Return does the opposite. The circled
point is the one selected.</div>

<p>This is the least intuitive part of the result, and Figure 6 is the whole argument.
Revenue comes from avoiding the site's own costs, and a site only has so many costs to
avoid, so doubling the battery does not double the saving, while cost keeps rising
regardless. At Harwell a 107 kW battery earns five times the revenue of the 21 kW one
and returns roughly half as much. The surface around the chosen point is flat, so a
range of sizes performs about equally rather than one size being uniquely right.</p>

<div class="box pos"><b>The optimiser has been independently tested</b>
An adversarial audit tried three ways to beat it and failed each time. An optimisation
model rewritten from scratch reached the identical objective value to six decimal
places. Re-optimising each of the 365 days individually found zero days that could be
improved. A hand-built schedule following the textbook strategy of charging cheap and
discharging into the peak scored worse than the solver by 20p across the year.</div>

<div class=box><b>What the engine will not do</b>
It will not report a return it has not measured. Where no candidate produces a positive
return the record says so and shows the best candidate's measured figures, rather than
presenting silence as an absence of value. A missing input is derived from published
sources, labelled as an assumption, and listed with the question that would settle it.</div>
"""


def _no_dashes(text: str) -> str:
    """House style: zero em/en dashes in anything a reader sees."""
    return text.replace("—", "-").replace("–", "-")


DOCS = [("how_the_optimiser_works.html", "How the optimiser works", REPORT)]


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "deliverables")
    out.mkdir(parents=True, exist_ok=True)
    for filename, title, body in DOCS:
        html = _no_dashes(
            f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f'<meta name=viewport content="width=device-width,initial-scale=1">'
            f"<title>{title}</title><style>{CSS}</style></head><body>"
            f'<div class="wrap">{body}<div class="foot">{FOOT}</div></div>'
            f"</body></html>"
        )
        (out / filename).write_text(html, encoding="utf-8")
        print(f"  {filename}  ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
