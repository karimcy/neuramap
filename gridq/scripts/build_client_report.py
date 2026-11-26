"""Build the two client-facing feasibility reports from published run records.

TWO documents, not nine: one for the Grundon metered estate (five sites) and one for
Harwell. They are consultancy reports, meant to be read start to finish by someone who
does not model batteries for a living: what behind-the-meter storage is and why it
matters, how the site was validated, what the model found, and what the result rests on.

WHAT THIS SCRIPT MAY DO. It reads `quote.json` and `site.json` from a published run and
renders them. It formats, it groups, it draws. It NEVER computes a financial number
(CLAUDE.md section 8): every pound, percent and kW on the page is read from the record.
Where a figure is a plain sum of stated record values (a revenue total across streams),
the page says so on the line itself.

WHAT THESE DOCUMENTS ARE. Study-basis output on the named `all_streams_enabled_study_v1`
basis (D57): every revenue stream modelled as open, every contract, consent, gate, award
and registration ASSUMED APPROVED for the site and the battery size. That is stamped at
the top of both reports and repeated in the assumptions section. A study-basis figure is
never an underwriting output and never an offer (D46).

INVOICE-ANCHORED VARIANTS ARE EXCLUDED. Only metered sites appear here, on the owner's
direction. The two invoice-anchored twins are published separately and share a `site_id`
with their metered counterpart, so mixing them into one document would put two different
answers under one site name.

Run: `python scripts/build_client_report.py`
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs"
OUT = REPO / "deliverables"

# ---------------------------------------------------------------------------
# The runs. Keyed by the run id published by scripts/run_d57_campaign.py.
# ---------------------------------------------------------------------------

GRUNDON = [
    ("quote_run_66d2dadeaa8e520d", "Bishops Cleeve Ash Plant"),
    ("quote_run_7d71c307a8b9b681", "Bishops Cleeve MRF"),
    ("quote_run_1923d4b0f7c2e810", "Knowl Hill"),
    ("quote_run_890c51f0e774218d", "Ewelme MRF"),
    ("quote_run_53277c6ddcd19603", "Tanhouse"),
]
HARWELL = [("quote_run_39fb87f5f0d6f0c1", "Harwell Campus")]

# ---------------------------------------------------------------------------
# Palette. Neura amber is the accent; the light-surface steps below were chosen
# by running the data-viz validator, not by eye. The four categorical slots pass
# every hard gate on the all-pairs list (the form that binds for a pie), with CVD
# separation in the 6-8 floor band, which is legal because every slice carries a
# direct label and the full table sits beside the chart.
# ---------------------------------------------------------------------------

INK = "#12161b"
INK_2 = "#4a5560"
MUTED = "#6d7883"
LINE = "#dde3e9"
LINE_SOFT = "#eef2f6"
SURFACE = "#ffffff"
SOFT = "#f7f9fb"

AMBER = "#b8730a"  # family 1 and the discharge direction
BLUE = "#2a6fd0"  # family 2 and the charge direction
GREEN = "#0a7d4b"  # family 3 and state of energy
MAGENTA = "#c2427e"  # family 4
NEG = "#b0202c"
BRAND = "#ffb454"  # the Neura accent itself, for rules and marks on dark

#: Sequential amber ramp for magnitude (the sizing surface). Light to dark.
RAMP = ["#f7dcb4", "#f0c485", "#e5a850", "#d38f24", "#b8730a", "#985e05", "#7a4a00"]

# ---------------------------------------------------------------------------
# Revenue stream families. The grouping is presentational: it decides colour and
# the order of the table, never a number.
# ---------------------------------------------------------------------------

FAMILIES = [
    (
        "Network and capacity charges avoided",
        AMBER,
        [
            "DUOS_RED_BAND_AVOIDANCE",
            "AGREED_CAPACITY_REDUCTION",
            "MIC_ASC_HEADROOM",
            "PPA_OR_BILL_SAVINGS",
        ],
    ),
    (
        "Grid flexibility services",
        BLUE,
        [
            "LOCAL_FLEX_PICLO",
            "DFS",
            "BALANCING_MECHANISM",
            "ANCILLARY_DC_DM_DR",
            "LOAD_SERVICE",
        ],
    ),
    ("Energy trading", GREEN, ["TARIFF_ARBITRAGE", "WHOLESALE_P415"]),
    ("Capacity Market", MAGENTA, ["CAPACITY_MARKET"]),
]

STREAM_LABEL = {
    "PPA_OR_BILL_SAVINGS": "PPA or bill savings",
    "TARIFF_ARBITRAGE": "Tariff arbitrage",
    "DUOS_RED_BAND_AVOIDANCE": "DUoS red band avoidance",
    "MIC_ASC_HEADROOM": "MIC and ASC headroom",
    "CAPACITY_MARKET": "Capacity Market",
    "LOCAL_FLEX_PICLO": "Local flexibility",
    "LOAD_SERVICE": "Load service",
    "BALANCING_MECHANISM": "Balancing Mechanism",
    "ANCILLARY_DC_DM_DR": "Dynamic Containment, Moderation, Regulation",
    "DFS": "Demand Flexibility Service",
    "AGREED_CAPACITY_REDUCTION": "Agreed capacity reduction",
    "WHOLESALE_P415": "Wholesale, via P415",
}

STREAM_NOTE = {
    "PPA_OR_BILL_SAVINGS": (
        "Carried in full by tariff arbitrage and DUoS red band avoidance. Held at zero "
        "here so no pound is counted twice."
    ),
    "MIC_ASC_HEADROOM": (
        "Carried in full by agreed capacity reduction. Held at zero here so no pound is "
        "counted twice."
    ),
}

STREAM_WHAT = {
    "DUOS_RED_BAND_AVOIDANCE": (
        "Discharging through the distribution network's red band, when the unit rate is highest."
    ),
    "AGREED_CAPACITY_REDUCTION": (
        "Shaving the site peak so a smaller agreed supply capacity can be contracted."
    ),
    "MIC_ASC_HEADROOM": "Freeing capacity headroom under the site's import limit.",
    "PPA_OR_BILL_SAVINGS": "The reduction in the delivered energy bill.",
    "LOCAL_FLEX_PICLO": "Turning down against the local network operator's constrained hours.",
    "DFS": "Answering the national demand flexibility calls in the site's own zone.",
    "BALANCING_MECHANISM": "Bidding the battery into the national balancing market.",
    "ANCILLARY_DC_DM_DR": "Holding frequency response, the fastest of the grid services.",
    "LOAD_SERVICE": "Firming the site's own load against a volatile grid.",
    "TARIFF_ARBITRAGE": "Charging on cheap half hours and discharging on expensive ones.",
    "WHOLESALE_P415": "Selling into the wholesale market through the P415 route.",
    "CAPACITY_MARKET": "Being available on the winter stress events the Capacity Market pays for.",
}


# ---------------------------------------------------------------------------
# Record loading. Nothing here derives a value; it only reaches for one.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One published run, unpacked. Every field is a read, never a computation."""

    run_id: str
    display: str
    quote: dict
    site: dict

    @property
    def opt(self) -> dict:
        return self.quote["optimisation_result"]

    @property
    def fin(self) -> dict:
        return self.quote["financial_case"]

    @property
    def grid(self) -> dict:
        return self.quote["sizing_grid"]

    @property
    def load(self) -> dict:
        return self.site["load_data"]

    @property
    def conn(self) -> dict:
        return self.site["connection"]

    @property
    def power_kw(self) -> float:
        return float(self.opt["selected_power_kw"])

    @property
    def energy_kwh(self) -> float:
        return float(self.opt["selected_energy_kwh"])

    @property
    def duration_h(self) -> float:
        return float(self.opt["duration_hours"])

    @property
    def irr_pct(self) -> float | None:
        v = self.fin.get("irr")
        return None if v is None else float(v)

    @property
    def payback_years(self) -> float | None:
        v = self.fin.get("payback_years")
        return None if v is None else float(v)

    @property
    def capex_gbp(self) -> float:
        return float(self.fin["capital_recovery"]["capital_outlay_gbp"])

    @property
    def lifetime_surplus_gbp(self) -> float:
        return float(self.fin["capital_recovery"]["lifetime_net_cashflow_gbp"])

    @property
    def streams(self) -> list[dict]:
        return list(self.opt["value_streams"])

    @property
    def revenue_total(self) -> float:
        """The plain sum of the per-stream figures the record states. Labelled as such
        wherever it appears, because a total the record does not itself carry is a sum
        the reader is entitled to check."""
        return sum(float(v["annual_revenue_gbp"] or 0.0) for v in self.streams)

    @property
    def dispatch(self) -> dict:
        return self.quote["dispatch_profile"]


def load(run_id: str, display: str) -> Site:
    d = RUNS / run_id
    return Site(
        run_id=run_id,
        display=display,
        quote=json.loads((d / "quote.json").read_text()),
        site=json.loads((d / "site.json").read_text()),
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def no_dashes(text: str) -> str:
    """House style: no em or en dashes in anything a client reads.

    Applied to the finished document, never to the record. The site names carry them
    ("Grundon - Ewelme MRF, grid import"), so the document seam is the only place that
    catches every route onto the page.
    """
    # The spaced form is handled FIRST. Replacing a bare em dash and leaving its surrounding
    # spaces produced "the BILLED TCR band , the LV band-2 stack", which reads as a typo.
    for a, b in ((" — ", ", "), ("— ", ", "), (" —", ", "), ("—", ", ")):
        text = text.replace(a, b)
    for a, b in ((" – ", " - "), ("–", "-"), ("−", "-")):
        text = text.replace(a, b)
    return text


def gbp(v: float | None, dp: int = 0) -> str:
    return "n/a" if v is None else f"£{v:,.{dp}f}"


def num(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{dp}f}"


def pct(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{dp}f}%"


def e(text: str) -> str:
    return escape(str(text))


# ---------------------------------------------------------------------------
# SVG primitives. Hand rolled so the figures are inline, self contained and print
# without a network request.
# ---------------------------------------------------------------------------


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def txt(
    x: float,
    y: float,
    s: str,
    *,
    size: float = 11,
    fill: str = MUTED,
    anchor: str = "start",
    weight: str = "400",
    mono: bool = False,
) -> str:
    fam = (
        "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
        if mono
        else "Inter,-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
    )
    return (
        f'<text x="{_f(x)}" y="{_f(y)}" font-size="{_f(size)}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}" font-family="{fam}">{e(s)}</text>'
    )


def line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    stroke: str,
    w: float = 1,
    dash: str = "",
    *,
    op: float = 1.0,
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" '
        f'stroke="{stroke}" stroke-width="{_f(w)}" opacity="{_f(op)}"{d}/>'
    )


def rect(
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str,
    *,
    r: float = 0,
    op: float = 1.0,
    stroke: str = "",
) -> str:
    st = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
    return (
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(max(w, 0))}" height="{_f(max(h, 0))}" '
        f'fill="{fill}" rx="{_f(r)}" opacity="{_f(op)}"{st}/>'
    )


def poly(
    points: list[tuple[float, float]],
    fill: str,
    *,
    op: float = 1.0,
    stroke: str = "",
    sw: float = 1,
) -> str:
    pts = " ".join(f"{_f(x)},{_f(y)}" for x, y in points)
    st = f' stroke="{stroke}" stroke-width="{_f(sw)}"' if stroke else ""
    return f'<polygon points="{pts}" fill="{fill}" opacity="{_f(op)}"{st}/>'


def path(d: str, *, fill: str = "none", stroke: str = "", sw: float = 1.6, op: float = 1.0) -> str:
    st = (
        f' stroke="{stroke}" stroke-width="{_f(sw)}" stroke-linejoin="round" stroke-linecap="round"'
        if stroke
        else ""
    )
    return f'<path d="{d}" fill="{fill}" opacity="{_f(op)}"{st}/>'


def circle(cx: float, cy: float, r: float, fill: str, *, stroke: str = "", sw: float = 1.5) -> str:
    st = f' stroke="{stroke}" stroke-width="{_f(sw)}"' if stroke else ""
    return f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}" fill="{fill}"{st}/>'


def plate(
    x: float,
    y: float,
    label: str,
    *,
    size: float = 10,
    fill: str = MUTED,
    anchor: str = "start",
    weight: str = "400",
    mono: bool = False,
) -> str:
    """Text on an opaque plate, for axis labels that marks would otherwise cross."""
    w = len(label) * size * (0.62 if mono else 0.55) + 6
    x0 = {"start": x - 3, "middle": x - w / 2, "end": x - w + 3}[anchor]
    return rect(x0, y - size + 1.5, w, size + 4, SURFACE, r=2, op=0.95) + txt(
        x, y, label, size=size, fill=fill, anchor=anchor, weight=weight, mono=mono
    )


def svg(w: float, h: float, body: str, alt: str) -> str:
    return (
        f'<svg viewBox="0 0 {_f(w)} {_f(h)}" width="100%" role="img" '
        f'aria-label="{e(alt)}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Inter,-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">'
        f"{body}</svg>"
    )


def ramp_colour(t: float) -> str:
    """Position 0..1 on the sequential amber ramp, clamped."""
    t = min(max(t, 0.0), 1.0)
    idx = min(int(t * len(RAMP)), len(RAMP) - 1)
    return RAMP[idx]


# ---------------------------------------------------------------------------
# Figure 1: the method flowchart. Identical in both reports, because the method is.
# ---------------------------------------------------------------------------

#: The method, at a glance. Every line here is deliberately short: this is a diagram a reader
#: SCANS, and a box carrying three lines of prose stops being a diagram and becomes a paragraph in
#: a border. The detail those sentences used to carry lives in section 3, which is where a reader
#: who wants it will look.
STAGES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "Site data",
        "Everything the model is allowed to know.",
        [
            ("Half-hourly meter data", "A full year of the site's own readings."),
            ("Tariff and network charges", "Energy, DUoS bands and levies, as billed."),
            ("Connection and capacity", "Agreed capacity, metered peak, headroom."),
        ],
    ),
    (
        "Checks and gaps",
        "What the data says, and what it does not.",
        [
            ("Reconcile the sources", "Disagreements stated, never averaged away."),
            ("Derive what is missing", "Nothing left blank. Every derivation labelled."),
            ("Cap the confidence", "The weakest input caps the whole result."),
        ],
    ),
    (
        "Market model",
        "Every way the battery can earn, priced.",
        [
            ("Twelve revenue streams", "Bill savings and network charges to grid services."),
            ("Half-hourly price shapes", "Each stream has its own price and its own hours."),
            ("No double counting", "Where two streams claim one pound, one carries it."),
        ],
    ),
    (
        "Dispatch optimisation",
        "The battery is operated for a whole year.",
        [
            ("A mixed-integer linear program", "17,520 decisions solved together, not in turn."),
            ("Real battery physics", "Power and energy limits, and a cost per cycle."),
            ("One optimal schedule", "All twelve streams optimised at once."),
        ],
    ),
    (
        "Sizing search",
        "Not one battery. Every battery.",
        [
            ("Solve every candidate", "The full year re-solved at each power and duration."),
            ("Score each one", "Its own revenue, costs, capital and return."),
            ("Select", "Strongest return, smallest system as the tie-break."),
        ],
    ),
    (
        "Financial result",
        "The numbers in this report, and what they rest on.",
        [
            ("Cashflow and return", "A 15 year cashflow, a project IRR, a payback."),
            ("Assumptions on the record", "Every derived value and assumed approval."),
            ("Confidence band", "Evidential strength, set by the weakest input."),
        ],
    ),
]


def fig_method_flow() -> str:
    pad_l, top = 14, 26
    spine_x = pad_l + 22
    box_x = spine_x + 44
    width = 980
    col_w = (width - box_x - 14 - 2 * 10) / 3
    body: list[str] = []
    y = top
    heights: list[float] = []
    for _, _, steps in STAGES:
        rows = max(len(_wrap(d, 44)) for _, d in steps)
        heights.append(34 + 15 * rows + 20)

    total = sum(heights) + 26 * (len(STAGES) - 1) + top + 20
    # the spine, drawn first so the chips sit on it
    body.append(line(spine_x, top + 16, spine_x, total - 34, LINE, 2))

    for i, ((name, blurb, steps), h) in enumerate(zip(STAGES, heights, strict=True)):
        cy = y + 16
        body.append(circle(spine_x, cy, 15, AMBER))
        body.append(
            txt(
                spine_x,
                cy + 4.5,
                str(i + 1),
                size=13,
                fill="#ffffff",
                anchor="middle",
                weight="700",
                mono=True,
            )
        )
        body.append(txt(box_x, y + 8, name.upper(), size=12, fill=INK, weight="700"))
        body.append(txt(box_x, y + 24, blurb, size=11.5, fill=MUTED))
        for j, (title, detail) in enumerate(steps):
            bx = box_x + j * (col_w + 10)
            by = y + 34
            lines = _wrap(detail, 44)
            bh = 26 + 15 * len(lines)
            body.append(rect(bx, by, col_w, bh, SOFT, r=5, stroke=LINE))
            body.append(rect(bx, by, 2.5, bh, AMBER, r=1.2))
            body.append(txt(bx + 12, by + 16, title, size=11.5, fill=INK, weight="600"))
            for k, ln in enumerate(lines):
                body.append(txt(bx + 12, by + 32 + 15 * k, ln, size=11, fill=INK_2))
        y += h + 26

    return svg(
        width,
        total,
        "".join(body),
        "The six stages of the validation method, from site data to financial result.",
    )


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if len(trial) > width and cur:
            out.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# Figure 2: the sizing surface, in three dimensions, from one fixed viewpoint.
#
# INTEGRITY NOTE, and it is the reason this figure looks the way it does. Every mark
# is one solved candidate. Nothing is drawn between two solved candidates: no mesh, no
# interpolated skin, no smoothed iso-return contour. A surface drawn across an
# irregular grid would show returns at sizes the solver never evaluated, which in a
# document where every number must trace to the record is a fabricated figure. Columns
# standing on a floor assert nothing between themselves.
# ---------------------------------------------------------------------------

_OX = 104.0
_XLEN = 448.0
_YDX, _YDY = 122.0, -96.0
_ZPX = 316.0  # pixels spanning the whole evaluated return range


def _proj(ox: float, oy: float, xf: float, yf: float, zpx: float) -> tuple[float, float]:
    return (ox + xf * _XLEN + yf * _YDX, oy + yf * _YDY - zpx)


def _log_frac(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


def _nice_log_ticks(lo: float, hi: float) -> list[float]:
    out: list[float] = []
    for base in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if lo <= base <= hi:
            out.append(float(base))
    if not out or out[0] > lo * 1.05:
        out.insert(0, round(lo, 1))
    if out[-1] < hi * 0.95:
        out.append(round(hi, 0))
    return out


def fig_sizing_3d(s: Site) -> str:
    """The evaluated sizing space, from one fixed viewpoint.

    The reference plane is ZERO RETURN, not the bottom of the data. Every column stands on
    that plane, so its height reads directly as "how far above break even" and the columns
    hanging below it read as what they are: sizes that lose money. A plane at the minimum
    would put the whole population above the floor and hide that distinction.
    """
    pts = [p for p in s.grid["points"] if p.get("project_irr_pct") is not None]
    if not pts:
        return ""
    powers = [float(p["power_kw"]) for p in pts]
    durs = [float(p["duration_hours"]) for p in pts]
    irrs = [float(p["project_irr_pct"]) for p in pts]
    plo, phi = min(powers), max(powers)
    dlo, dhi = min(durs), max(durs)
    zhi = max(max(irrs), 0.0)
    true_lo = min(min(irrs), 0.0)
    # The vertical axis is CLIPPED below, at twice the height of the positive lobe. Without it a
    # site whose worst candidate loses 24% a year spends four fifths of the figure on how bad the
    # bad sizes are, and the peak the reader came for is a smear at the top. Clipped columns are
    # drawn to the floor and tipped with a chevron, the floor is named in the legend, and the
    # caption says so: a cut that is declared is a scale choice, a cut that is silent is not.
    zlo = max(true_lo, -2.0 * zhi) if zhi > 0 else true_lo
    clipped = zlo > true_lo + 1e-9
    scale = _ZPX / ((zhi - zlo) or 1.0)

    top_pad = 74.0
    oy = top_pad + zhi * scale - _YDY  # the zero plane's front left corner
    height = oy + abs(zlo) * scale + 46
    width = _OX + _XLEN + _YDX + 84

    def P(xf: float, yf: float, v: float) -> tuple[float, float]:
        return _proj(_OX, oy, xf, yf, v * scale)

    def cf(v: float) -> str:
        return "#aeb8c2" if v <= 0 else ramp_colour(v / zhi if zhi > 0 else 0.0)

    body: list[str] = []

    # ---- the zero plane, which is the floor of this figure ----
    plane = [P(0, 0, 0), P(1, 0, 0), P(1, 1, 0), P(0, 1, 0)]
    body.append(poly(plane, "#eef2f6", op=0.9, stroke="#c9d2db", sw=1))
    p_ticks = _nice_log_ticks(plo, phi)
    d_ticks = _nice_log_ticks(dlo, dhi)
    for t in p_ticks:
        xf = _log_frac(t, plo, phi)
        a, b = P(xf, 0, 0), P(xf, 1, 0)
        body.append(line(a[0], a[1], b[0], b[1], "#dbe2e9", 0.7))
        body.append(line(a[0], a[1], a[0], a[1] + 5, "#c9d2db", 1))
        body.append(plate(a[0], a[1] + 17, f"{t:g}", fill=MUTED, anchor="middle", mono=True))
    for t in d_ticks:
        yf = _log_frac(t, dlo, dhi)
        a, b = P(0, yf, 0), P(1, yf, 0)
        body.append(line(a[0], a[1], b[0], b[1], "#dbe2e9", 0.7))
        body.append(line(b[0] + 3, b[1], b[0] + 10, b[1], "#c9d2db", 1))
        body.append(plate(b[0] + 15, b[1] + 4, f"{t:g}h", fill=MUTED, mono=True))

    # ---- the vertical return axis, hinged on the front left corner of the plane ----
    o = P(0, 0, 0)
    ax = o[0] - 20
    hi_pt, lo_pt = P(0, 0, zhi), P(0, 0, zlo)
    body.append(line(ax, lo_pt[1], ax, hi_pt[1], "#c9d2db", 1.2))
    step = 5.0 if (zhi - zlo) <= 45 else 10.0
    t = math.ceil(zlo / step) * step
    while t <= zhi + 1e-9:
        pt = P(0, 0, t)
        body.append(line(ax - 4, pt[1], ax, pt[1], "#c9d2db", 1))
        body.append(
            plate(
                ax - 8,
                pt[1] + 3.5,
                f"{t:g}%",
                fill=NEG if t == 0 else MUTED,
                anchor="end",
                mono=True,
            )
        )
        t += step

    # ---- the candidates, painted back to front ----
    sel = s.grid.get("selected") or {}
    sel_key = (
        round(float(sel.get("power_kw", -1)), 3),
        round(float(sel.get("duration_hours", -1)), 3),
    )
    marked: tuple[float, float] | None = None
    order = sorted(
        pts, key=lambda p: (-_log_frac(float(p["duration_hours"]), dlo, dhi), -float(p["power_kw"]))
    )
    for p in order:
        xf = _log_frac(float(p["power_kw"]), plo, phi)
        yf = _log_frac(float(p["duration_hours"]), dlo, dhi)
        v = float(p["project_irr_pct"])
        foot, head = P(xf, yf, 0), P(xf, yf, v)
        col = cf(v)
        is_sel = (round(float(p["power_kw"]), 3), round(float(p["duration_hours"]), 3)) == sel_key
        screening = bool(p.get("proxy"))
        cut = v < zlo
        if cut:
            head = P(xf, yf, zlo)
        body.append(
            line(
                foot[0],
                foot[1],
                head[0],
                head[1],
                col,
                0.9 if screening else 1.9,
                op=0.5 if v <= 0 else 1.0,
            )
        )
        if is_sel:
            marked = head
        if cut:
            body.append(
                path(
                    f"M {_f(head[0] - 3.4)} {_f(head[1] - 4)} L {_f(head[0])} {_f(head[1] + 1)} "
                    f"L {_f(head[0] + 3.4)} {_f(head[1] - 4)}",
                    stroke=col,
                    sw=1.3,
                    op=0.65,
                )
            )
        elif screening:
            body.append(circle(head[0], head[1], 2.5, SURFACE, stroke=col, sw=1.3))
        else:
            body.append(
                circle(head[0], head[1], 5.4 if is_sel else 3.4, col, stroke=SURFACE, sw=1.1)
            )

    # ---- the selection, called out by name ----
    if marked is not None:
        body.append(circle(marked[0], marked[1], 9.5, "none", stroke=INK, sw=1.5))
        lx, ly = marked[0] - 4, marked[1] - 46
        body.append(line(marked[0], marked[1] - 10, marked[0], ly + 6, INK, 1))
        body.append(txt(lx, ly - 26, "Selected", size=11.5, fill=INK, weight="700"))
        body.append(
            txt(
                lx,
                ly - 12,
                f"{num(s.power_kw, 1)} kW x {num(s.duration_h, 1)} h",
                size=10.5,
                fill=INK_2,
                mono=True,
            )
        )
        body.append(txt(lx, ly + 2, f"{pct(s.irr_pct)} a year", size=10.5, fill=INK_2, mono=True))

    # ---- axis titles and the ramp legend ----
    mid_p = P(0.5, 0, 0)
    body.append(
        plate(
            mid_p[0],
            mid_p[1] + 37,
            "Battery power (kW, log scale)",
            size=11,
            fill=INK,
            anchor="middle",
            weight="600",
        )
    )
    mid_d = P(1, 1, 0)
    body.append(txt(mid_d[0] + 14, mid_d[1] - 34, "Duration", size=11, fill=INK, weight="600"))
    body.append(txt(mid_d[0] + 14, mid_d[1] - 21, "(hours)", size=11, fill=INK, weight="600"))
    body.append(txt(20, 26, "Project return, % a year", size=11, fill=INK, weight="600"))
    body.append(txt(20, 41, "Break even is the shaded plane", size=10.5, fill=MUTED))

    lx0, ly0 = width - 214, 22
    body.append(txt(lx0, ly0, "RETURN", size=10, fill=MUTED, weight="700"))
    for i, c in enumerate(RAMP):
        body.append(rect(lx0 + i * 15, ly0 + 8, 13, 10, c, r=1.5))
    body.append(txt(lx0, ly0 + 31, "low", size=9.5, fill=MUTED))
    body.append(txt(lx0 + len(RAMP) * 15 - 2, ly0 + 31, "high", size=9.5, fill=MUTED, anchor="end"))
    body.append(rect(lx0, ly0 + 42, 13, 10, "#aeb8c2", r=1.5))
    body.append(txt(lx0 + 19, ly0 + 51, "loses money", size=9.5, fill=MUTED))
    body.append(circle(lx0 + 6, ly0 + 68, 3.4, AMBER, stroke=SURFACE, sw=1.1))
    body.append(txt(lx0 + 19, ly0 + 72, "solid: full year solve", size=9.5, fill=MUTED))
    body.append(circle(lx0 + 6, ly0 + 84, 2.5, SURFACE, stroke=AMBER, sw=1.3))
    body.append(txt(lx0 + 19, ly0 + 88, "hollow: screening solve", size=9.5, fill=MUTED))
    if clipped:
        body.append(
            path(
                f"M {_f(lx0 + 2.6)} {_f(ly0 + 100)} L {_f(lx0 + 6)} {_f(ly0 + 105)} "
                f"L {_f(lx0 + 9.4)} {_f(ly0 + 100)}",
                stroke="#aeb8c2",
                sw=1.3,
            )
        )
        body.append(txt(lx0 + 19, ly0 + 105, f"continues below {zlo:.0f}%", size=9.5, fill=MUTED))

    alt = (
        f"Three dimensional view of {len(pts)} solved battery sizes for {s.display}: power on one "
        f"axis, duration on the second, project return on the vertical, with columns standing on a "
        f"break even plane. The selected size is {num(s.power_kw, 1)} kW by {num(s.duration_h, 1)} "
        f"hours at {pct(s.irr_pct)}."
    )
    return svg(width, height, "".join(body), alt)


# ---------------------------------------------------------------------------
# Figure 3: where the money comes from.
# ---------------------------------------------------------------------------

SHORT = {
    "PPA_OR_BILL_SAVINGS": "Bill savings",
    "TARIFF_ARBITRAGE": "Arbitrage",
    "DUOS_RED_BAND_AVOIDANCE": "DUoS red band",
    "MIC_ASC_HEADROOM": "Capacity headroom",
    "CAPACITY_MARKET": "Capacity Market",
    "LOCAL_FLEX_PICLO": "Local flexibility",
    "LOAD_SERVICE": "Load service",
    "BALANCING_MECHANISM": "Balancing Mechanism",
    "ANCILLARY_DC_DM_DR": "Frequency response",
    "DFS": "Demand Flexibility",
    "AGREED_CAPACITY_REDUCTION": "Agreed capacity",
    "WHOLESALE_P415": "Wholesale (P415)",
}

FAMILY_OF = {st: (name, col) for name, col, sts in FAMILIES for st in sts}


def _ordered_streams(s: Site) -> list[dict]:
    """Family order first, value order within family, so each family is one arc."""
    rank = {st: (i, j) for i, (_, _, sts) in enumerate(FAMILIES) for j, st in enumerate(sts)}
    return sorted(
        s.streams,
        key=lambda v: (rank.get(v["stream"], (99, 99))[0], -float(v["annual_revenue_gbp"] or 0.0)),
    )


def _place(
    labels: list[tuple[float, str, str, str]], top: float, bottom: float, gap: float
) -> list[float]:
    """Push overlapping leader labels apart without changing their order."""
    ys = [y for y, _, _, _ in labels]
    for i in range(1, len(ys)):
        if ys[i] - ys[i - 1] < gap:
            ys[i] = ys[i - 1] + gap
    overflow = ys[-1] - bottom if ys else 0
    if overflow > 0:
        ys = [y - overflow for y in ys]
    for i in range(len(ys) - 2, -1, -1):
        if ys[i + 1] - ys[i] < gap:
            ys[i] = ys[i + 1] - gap
    if ys and ys[0] < top:
        ys = [y + (top - ys[0]) for y in ys]
    return ys


def fig_revenue_pie(s: Site) -> str:
    rows = [v for v in _ordered_streams(s) if float(v["annual_revenue_gbp"] or 0.0) > 0]
    total = s.revenue_total
    if not rows or total <= 0:
        return ""
    W, H = 646, 434
    cx, cy, r, ri = 302.0, 216.0, 128.0, 74.0
    body: list[str] = []
    ang = -math.pi / 2
    left: list[tuple[float, str, str, str]] = []
    right: list[tuple[float, str, str, str]] = []

    for v in rows:
        val = float(v["annual_revenue_gbp"])
        frac = val / total
        sweep = frac * 2 * math.pi
        mid = ang + sweep / 2
        col = FAMILY_OF.get(v["stream"], ("", MUTED))[1]
        big = 1 if sweep > math.pi else 0
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        x2, y2 = cx + r * math.cos(ang + sweep), cy + r * math.sin(ang + sweep)
        xi1, yi1 = cx + ri * math.cos(ang + sweep), cy + ri * math.sin(ang + sweep)
        xi2, yi2 = cx + ri * math.cos(ang), cy + ri * math.sin(ang)
        d = (
            f"M {_f(x1)} {_f(y1)} A {_f(r)} {_f(r)} 0 {big} 1 {_f(x2)} {_f(y2)} "
            f"L {_f(xi1)} {_f(yi1)} A {_f(ri)} {_f(ri)} 0 {big} 0 {_f(xi2)} {_f(yi2)} Z"
        )
        # the 2px surface stroke IS the gap between segments
        body.append(path(d, fill=col, stroke=SURFACE, sw=2))
        entry = (mid, SHORT.get(v["stream"], v["stream"]), gbp(val), f"{frac * 100:.0f}%")
        (right if math.cos(mid) >= 0 else left).append(entry)
        ang += sweep

    body.append(
        txt(cx, cy - 6, gbp(total), size=25, fill=INK, anchor="middle", weight="700", mono=True)
    )
    body.append(txt(cx, cy + 13, "a year, gross", size=10.5, fill=MUTED, anchor="middle"))
    body.append(
        txt(cx, cy + 29, "sum of the stated streams", size=9.5, fill=MUTED, anchor="middle")
    )

    for side, entries in (("r", right), ("l", left)):
        sign = 1 if side == "r" else -1
        entries.sort(key=lambda t: cy + r * math.sin(t[0]))
        ys = _place([(cy + r * math.sin(m), n, mo, sh) for m, n, mo, sh in entries], 28, H - 30, 34)
        for (mid, name, money, share), y in zip(entries, ys, strict=True):
            px = cx + (r + 5) * math.cos(mid)
            py = cy + (r + 5) * math.sin(mid)
            ex = cx + (r + 26) * sign
            lx = cx + (r + 34) * sign
            anchor = "start" if side == "r" else "end"
            body.append(
                path(
                    f"M {_f(px)} {_f(py)} L {_f(ex)} {_f(y)} L {_f(lx - 5 * sign)} {_f(y)}",
                    stroke=LINE,
                    sw=1,
                )
            )
            body.append(txt(lx, y - 2, name, size=11, fill=INK, anchor=anchor, weight="600"))
            body.append(
                txt(
                    lx,
                    y + 12,
                    f"{money}  ({share})",
                    size=10.5,
                    fill=MUTED,
                    anchor=anchor,
                    mono=True,
                )
            )

    alt = (
        f"Donut chart of the {len(rows)} earning revenue streams for {s.display}, "
        f"totalling {gbp(total)} a year."
    )
    return svg(W, H, "".join(body), alt)


def fig_family_key() -> str:
    """The four families, once, so the pie colours read without a per-figure legend."""
    body: list[str] = []
    x = 0.0
    for name, col, _ in FAMILIES:
        body.append(rect(x, 4, 11, 11, col, r=2.5))
        body.append(txt(x + 18, 13.5, name, size=11, fill=INK_2))
        x += 22 + len(name) * 6.05
    return svg(x, 20, "".join(body), "Colour key for the four revenue stream families.")


# ---------------------------------------------------------------------------
# Figure 4: one solved day, half hour by half hour.
#
# Four panels, one shared time axis, and deliberately NOT one chart with two vertical
# scales. Pence per kWh, kW and kWh are three different units; stacking them on a single
# axis would make the comparison between them a drawing accident rather than a fact.
# ---------------------------------------------------------------------------


def _nice_top(v: float) -> float:
    """Round an axis top up to 1, 2, 2.5 or 5 times a power of ten."""
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
        if v <= m * mag * (1 + 1e-9):
            return m * mag
    return 10 * mag


def _tick_label(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"{round(v):,d}"
    return f"{v:,.1f}"


def fig_dispatch(s: Site) -> str:
    dp = s.dispatch
    pts = list(dp["points"])
    if not pts:
        return ""
    W = 830.0
    L, R = 74.0, 26.0
    PW = W - L - R
    n = len(pts)
    step = PW / n

    def px(i: float) -> float:
        return L + i * step

    panels = [
        ("Delivered price", "p/kWh", 78.0),
        ("Site load", "kW", 78.0),
        ("Battery", "kW", 92.0),
        ("Stored energy", "kWh", 62.0),
    ]
    gap = 30.0
    tops: list[float] = []
    y = 26.0
    for _, _, h in panels:
        tops.append(y)
        y += h + gap
    H = y + 12

    body: list[str] = []

    def frame(idx: int, title: str, unit: str, hi: float, lo: float = 0.0) -> tuple[float, float]:
        top, h = tops[idx], panels[idx][2]
        base = top + h
        body.append(rect(L, top, PW, h, SOFT, op=0.5))
        body.append(line(L, base, L + PW, base, LINE, 1))
        body.append(txt(L, top - 8, title.upper(), size=10, fill=INK, weight="700"))
        body.append(txt(L + PW, top - 8, unit, size=10, fill=MUTED, anchor="end", mono=True))
        span = (hi - lo) or 1.0
        marks = [hi, hi / 2] if lo >= 0 else [hi, lo]
        for v in marks:
            yy = base - (v - lo) / span * h
            body.append(line(L, yy, L + PW, yy, LINE, 0.7, "3 3"))
            body.append(
                txt(L - 7, yy + 3.5, _tick_label(v), size=9.5, fill=MUTED, anchor="end", mono=True)
            )
        zz = base - (0 - lo) / span * h
        body.append(line(L, zz, L + PW, zz, LINE, 1))
        body.append(txt(L - 7, zz + 3.5, "0", size=9.5, fill=MUTED, anchor="end", mono=True))
        return base, span

    # ---- red band strips, drawn behind every panel so the eye ties price to action ----
    # A site behind a private wire pays no distribution charge at all, and its record says so with
    # a `not_applicable` DUoS entry. The band CLOCK still exists on such a site, so drawing the
    # shading and a "DUoS unit rate" key would tell the reader that a red band is being avoided
    # when there is no charge there to avoid. Where the rate is flat zero, both come off.
    duos_applies = max((float(p["duos_rate_gbp_per_kwh"]) for p in pts), default=0.0) > 0
    runs: list[tuple[int, int]] = []
    for i, p in enumerate(pts):
        if duos_applies and p.get("red_band"):
            if runs and runs[-1][1] == i:
                runs[-1] = (runs[-1][0], i + 1)
            else:
                runs.append((i, i + 1))
    for a, b in runs:
        body.append(rect(px(a), 18, (b - a) * step, tops[-1] + panels[-1][2] - 18, NEG, op=0.055))

    # ---- panel 1: the two priced components of an imported unit ----
    en = [float(p["energy_price_gbp_per_kwh"]) * 100 for p in pts]
    du = [float(p["duos_rate_gbp_per_kwh"]) * 100 for p in pts]
    hi1 = _nice_top(max(max(en), max(du)) * 1.08)
    base1, span1 = frame(0, "Delivered price", "p/kWh", hi1)
    series_1 = [(en, INK_2), (du, NEG)] if duos_applies else [(en, INK_2)]
    for series, col in series_1:
        d = " ".join(
            f"{'M' if i == 0 else 'L'} {_f(px(i + 0.5))} {_f(base1 - v / span1 * panels[0][2])}"
            for i, v in enumerate(series)
        )
        body.append(path(d, stroke=col, sw=1.7))
    ly = tops[0] - 8
    body.append(rect(L + 224, ly - 3, 9, 3, INK_2, r=1.5))
    body.append(txt(L + 238, ly, "Wholesale energy", size=9.5, fill=MUTED))
    if duos_applies:
        body.append(rect(L + 350, ly - 3, 9, 3, NEG, r=1.5))
        body.append(txt(L + 364, ly, "DUoS unit rate", size=9.5, fill=MUTED))
        if runs:
            body.append(rect(L + 468, ly - 6, 9, 8, NEG, r=1.5, op=0.2))
            body.append(txt(L + 482, ly, "shaded: the DUoS red band", size=9.5, fill=MUTED))
    else:
        body.append(
            txt(L + 350, ly, "No distribution charge applies at this site", size=9.5, fill=MUTED)
        )

    # ---- panel 2: what the site was drawing ----
    ld = [float(p["site_load_kw"]) for p in pts]
    hi2 = _nice_top(max(ld) * 1.08)
    base2, span2 = frame(1, "Site load", "kW", hi2)
    d = (
        f"M {_f(px(0))} {_f(base2)} "
        + " ".join(
            f"L {_f(px(i + 0.5))} {_f(base2 - v / span2 * panels[1][2])}" for i, v in enumerate(ld)
        )
        + f" L {_f(px(n))} {_f(base2)} Z"
    )
    body.append(path(d, fill=INK_2, op=0.13))
    d2 = " ".join(
        f"{'M' if i == 0 else 'L'} {_f(px(i + 0.5))} {_f(base2 - v / span2 * panels[1][2])}"
        for i, v in enumerate(ld)
    )
    body.append(path(d2, stroke=INK_2, sw=1.7))

    # ---- panel 3: what the battery did about it ----
    ch = [float(p["charge_kw"]) for p in pts]
    di = [float(p["discharge_kw"]) for p in pts]
    top3 = _nice_top(max(max(di), 0.001) * 1.15)
    bot3 = -_nice_top(max(max(ch), 0.001) * 1.15)
    base3, span3 = frame(2, "Battery", "kW", top3, bot3)
    zero3 = base3 - (0 - bot3) / span3 * panels[2][2]
    bw = max(step - 1.6, 1.0)
    for i, (c, dd) in enumerate(zip(ch, di, strict=True)):
        if dd > 0:
            h = dd / span3 * panels[2][2]
            body.append(rect(px(i) + 0.8, zero3 - h, bw, h, AMBER, r=1))
        if c > 0:
            h = c / span3 * panels[2][2]
            body.append(rect(px(i) + 0.8, zero3, bw, h, BLUE, r=1))
    ly2 = tops[2] - 8
    body.append(rect(L + 340, ly2 - 7, 9, 8, AMBER, r=1.5))
    body.append(txt(L + 354, ly2, "Discharging to the site", size=9.5, fill=MUTED))
    body.append(rect(L + 500, ly2 - 7, 9, 8, BLUE, r=1.5))
    body.append(txt(L + 514, ly2, "Charging into the battery", size=9.5, fill=MUTED))

    # ---- panel 4: the state of charge that ties the day together ----
    soe = [float(p["soe_kwh"]) for p in pts]
    cap = s.energy_kwh
    hi4 = _nice_top(max(max(soe), cap) * 1.08)
    base4, span4 = frame(3, "Stored energy", "kWh", hi4)
    d4 = " ".join(
        f"{'M' if i == 0 else 'L'} {_f(px(i + 0.5))} {_f(base4 - v / span4 * panels[3][2])}"
        for i, v in enumerate(soe)
    )
    body.append(path(d4, stroke=GREEN, sw=1.9))
    ycap = base4 - cap / span4 * panels[3][2]
    body.append(line(L, ycap, L + PW, ycap, GREEN, 1, "4 3"))
    body.append(
        txt(
            L + PW - 3,
            ycap - 5,
            f"usable capacity {num(cap, 1)} kWh",
            size=9.5,
            fill=GREEN,
            anchor="end",
        )
    )

    # ---- the shared clock ----
    hours = float(dp.get("interval_hours") or 0.5)
    for i in range(0, n + 1, max(1, int(round(4 / hours)))):
        x = px(i)
        body.append(line(x, base4, x, base4 + 4, LINE, 1))
        body.append(
            txt(
                x,
                base4 + 16,
                f"{int(i * hours) % 24:02d}:00",
                size=9.5,
                fill=MUTED,
                anchor="middle",
                mono=True,
            )
        )

    alt = (
        f"Four panel chart of one solved day for {s.display}: delivered price, site load, battery "
        f"charge and discharge, and stored energy across {n} half hours."
    )
    return svg(W, H, "".join(body), alt)


# ---------------------------------------------------------------------------
# Figure 5: the same surface read straight on, so the peak can be read off an axis.
# ---------------------------------------------------------------------------


def fig_sizing_2d(s: Site) -> str:
    pts = [p for p in s.grid["points"] if p.get("project_irr_pct") is not None]
    if not pts:
        return ""
    W, H = 430.0, 292.0
    L, R, T, B = 42.0, 12.0, 56.0, 44.0
    pw, ph = W - L - R, H - T - B
    powers = [float(p["power_kw"]) for p in pts]
    irrs = [float(p["project_irr_pct"]) for p in pts]
    plo, phi = min(powers), max(powers)
    raw_lo, raw_hi = min(irrs), max(irrs)
    pad = ((raw_hi - raw_lo) or 1.0) * 0.09
    ilo, ihi = raw_lo - pad, raw_hi + pad
    span = ihi - ilo

    def X(v: float) -> float:
        return L + _log_frac(v, plo, phi) * pw

    def Y(v: float) -> float:
        return T + ph - (v - ilo) / span * ph

    body: list[str] = []
    body.append(rect(L, T, pw, ph, SOFT, op=0.5))
    stepv = 5.0 if span <= 40 else 10.0
    t = math.ceil(ilo / stepv) * stepv
    while t <= ihi + 1e-9:
        body.append(line(L, Y(t), L + pw, Y(t), LINE, 0.7))
        body.append(txt(L - 6, Y(t) + 3.5, f"{t:g}", size=9.5, fill=MUTED, anchor="end", mono=True))
        t += stepv
    if ilo < 0 < ihi:
        body.append(line(L, Y(0), L + pw, Y(0), NEG, 1.1))
    for tv in _nice_log_ticks(plo, phi):
        body.append(line(X(tv), T + ph, X(tv), T + ph + 4, LINE, 1))
        body.append(
            txt(X(tv), T + ph + 16, f"{tv:g}", size=9.5, fill=MUTED, anchor="middle", mono=True)
        )

    for p in pts:
        if p.get("proxy"):
            body.append(
                circle(X(float(p["power_kw"])), Y(float(p["project_irr_pct"])), 2.4, "#c3ccd5")
            )

    sel_dur = round(s.duration_h, 3)
    lane = sorted(
        (p for p in pts if round(float(p["duration_hours"]), 3) == sel_dur and not p.get("proxy")),
        key=lambda p: float(p["power_kw"]),
    )
    for p in pts:
        if not p.get("proxy") and round(float(p["duration_hours"]), 3) != sel_dur:
            body.append(
                circle(
                    X(float(p["power_kw"])),
                    Y(float(p["project_irr_pct"])),
                    2.9,
                    "#e0b878",
                    stroke=SURFACE,
                    sw=1,
                )
            )
    if len(lane) > 1:
        d = " ".join(
            f"{'M' if i == 0 else 'L'} {_f(X(float(p['power_kw'])))} "
            f"{_f(Y(float(p['project_irr_pct'])))}"
            for i, p in enumerate(lane)
        )
        body.append(path(d, stroke=AMBER, sw=2))
        for p in lane:
            body.append(
                circle(
                    X(float(p["power_kw"])),
                    Y(float(p["project_irr_pct"])),
                    3.1,
                    AMBER,
                    stroke=SURFACE,
                    sw=1.1,
                )
            )

    if s.irr_pct is not None:
        sx, sy = X(s.power_kw), Y(s.irr_pct)
        body.append(circle(sx, sy, 8, "none", stroke=INK, sw=1.6))
        dy = 22 if sy - T < 34 else -14
        body.append(
            txt(
                sx + 12,
                sy + dy,
                f"{num(s.power_kw, 1)} kW",
                size=10,
                fill=INK,
                weight="700",
                mono=True,
            )
        )
        body.append(txt(sx + 12, sy + dy + 12, pct(s.irr_pct), size=10, fill=INK_2, mono=True))

    body.append(txt(0, 11, "RETURN AGAINST BATTERY POWER", size=10, fill=INK, weight="700"))
    body.append(
        txt(
            L + pw / 2, H - 8, "Battery power (kW, log scale)", size=10, fill=MUTED, anchor="middle"
        )
    )
    body.append(txt(0, T - 8, "% a year", size=9.5, fill=MUTED, mono=True))
    body.append(circle(4, 26, 2.4, "#c3ccd5"))
    body.append(txt(12, 29.5, "screening solve", size=9.5, fill=MUTED))
    body.append(circle(112, 26, 2.9, "#e0b878", stroke=SURFACE, sw=1))
    body.append(txt(121, 29.5, "full year solve", size=9.5, fill=MUTED))
    body.append(circle(216, 26, 3.1, AMBER, stroke=SURFACE, sw=1))
    body.append(
        txt(225, 29.5, f"full year solve at {num(s.duration_h, 1)} h", size=9.5, fill=MUTED)
    )

    return svg(
        W,
        H,
        "".join(body),
        f"Project return against battery power for {s.display}, all solved candidates.",
    )


# ---------------------------------------------------------------------------
# Figure 6: the estate at a glance. Five sites, one axis each, no summing.
# ---------------------------------------------------------------------------


def fig_estate(sites: list[Site]) -> str:
    W = 830.0
    rowh = 46.0
    L = 176.0
    H = 44 + rowh * len(sites) + 16
    body: list[str] = []
    cols = [
        ("Annual revenue", lambda s: s.revenue_total, gbp, AMBER),
        ("Project return", lambda s: s.irr_pct, lambda v: pct(v), BLUE),
        ("Payback", lambda s: s.payback_years, lambda v: f"{v:,.1f} yr", GREEN),
    ]
    colw = (W - L - 24) / 3
    for c, (title, _, _, _) in enumerate(cols):
        body.append(txt(L + c * colw, 22, title.upper(), size=10, fill=MUTED, weight="700"))
    for c, (_, get, _, _) in enumerate(cols):
        vals = [get(s) or 0 for s in sites]
        top = max(vals) or 1
        for r, s in enumerate(sites):
            y = 44 + r * rowh
            v = get(s) or 0
            body.append(rect(L + c * colw, y + 12, (colw - 26) * (v / top), 11, cols[c][3], r=2))
            body.append(rect(L + c * colw, y + 12, colw - 26, 11, LINE, r=2, op=0.35))
            body.append(rect(L + c * colw, y + 12, (colw - 26) * (v / top), 11, cols[c][3], r=2))
            body.append(
                txt(
                    L + c * colw,
                    y + 8,
                    cols[c][2](get(s)),
                    size=11,
                    fill=INK,
                    weight="600",
                    mono=True,
                )
            )
    for r, s in enumerate(sites):
        y = 44 + r * rowh
        body.append(txt(0, y + 14, s.display, size=11.5, fill=INK, weight="600"))
        body.append(
            txt(
                0,
                y + 27,
                f"{num(s.power_kw, 1)} kW x {num(s.duration_h, 1)} h",
                size=10,
                fill=MUTED,
                mono=True,
            )
        )
        if r:
            body.append(line(0, y - 6, W, y - 6, LINE_SOFT, 1))
    return svg(
        W, H, "".join(body), "Annual revenue, project return and payback for each metered site."
    )


# ---------------------------------------------------------------------------
# The page. Light canvas, hairline structure, one accent, tabular numerals, and a
# print stylesheet, because these get printed.
# ---------------------------------------------------------------------------

CSS = f"""
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:{SOFT};color:{INK};
 font:16px/1.62 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
 font-feature-settings:"kern" 1,"liga" 1}}
.page{{max-width:1000px;margin:0 auto;background:{SURFACE};padding:0 64px 90px}}
.mono,td.n,th.n,.kpi .v{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 font-variant-numeric:tabular-nums}}

/* cover */
.cover{{padding:74px 0 40px;border-bottom:1px solid {LINE}}}
.brand{{display:flex;align-items:center;gap:11px;margin-bottom:52px}}
.brand .lg{{height:30px;width:auto;display:block}}
.brand .dv{{margin-left:auto;font-size:11px;letter-spacing:.13em;color:{MUTED};font-weight:600}}
h1{{font-size:41px;line-height:1.14;letter-spacing:-.025em;margin:0 0 16px;font-weight:700}}
.lede{{font-size:19px;line-height:1.55;color:{INK_2};max-width:66ch;margin:0 0 30px}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));
 gap:15px 30px;font-size:12.5px;color:{MUTED};
 padding-top:22px;border-top:1px solid {LINE_SOFT}}}
.meta b{{display:block;color:{INK};font-weight:600;font-size:13px;
 margin-top:2px;overflow-wrap:anywhere;line-height:1.35}}

/* the basis notice: never optional */
.basis{{background:#fffaf1;border:1px solid #f0d9ae;border-left:3px solid {AMBER};
 border-radius:7px;padding:16px 20px;margin:34px 0 0;font-size:13.5px;
 line-height:1.6;color:#5c4415}}
.basis b{{color:#3d2c08}}

/* structure */
h2{{font-size:12px;letter-spacing:.13em;text-transform:uppercase;color:{MUTED};
 font-weight:700;margin:0 0 8px}}
h3{{font-size:28px;letter-spacing:-.02em;line-height:1.2;margin:0 0 18px;font-weight:700}}
h4{{font-size:17px;letter-spacing:-.01em;margin:34px 0 10px;font-weight:700}}
h5{{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:{MUTED};
 font-weight:700;margin:30px 0 12px;padding-bottom:7px;border-bottom:1px solid {LINE}}}
section{{padding:56px 0;border-bottom:1px solid {LINE}}}
section:last-of-type{{border-bottom:0}}
p{{margin:0 0 15px;max-width:74ch}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:34px}}
.two > div > h4:first-child{{margin-top:0}}
.two p{{max-width:none}}
ul{{margin:0 0 15px;padding-left:20px;max-width:74ch}}
li{{margin:6px 0}}
a{{color:{AMBER}}}

/* contents */
.toc{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:2px 26px;
 font-size:13.5px;margin:0}}
.toc div{{padding:7px 0;border-bottom:1px solid {LINE_SOFT};display:flex;gap:12px}}
.toc span{{color:{MUTED};font-weight:600;min-width:22px}}

/* KPIs */
.kpis{{display:grid;gap:12px;margin:6px 0 30px;
 grid-template-columns:repeat(auto-fit,minmax(132px,1fr))}}
.kpi{{border:1px solid {LINE};border-radius:8px;padding:15px 17px;background:{SOFT}}}
.kpi .l{{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
 color:{MUTED};font-weight:600}}
.kpi .v{{font-size:26px;font-weight:700;letter-spacing:-.02em;margin-top:6px;line-height:1.1}}
.kpi .s{{font-size:11.5px;color:{MUTED};margin-top:4px}}

/* figures */
figure{{margin:26px 0 30px}}
figure svg{{display:block;width:100%;height:auto}}
figcaption{{font-size:13px;color:{INK_2};margin-top:12px;line-height:1.55;max-width:88ch}}
figcaption b{{color:{INK};font-weight:600}}
.figrow{{display:grid;grid-template-columns:1.05fr .95fr;gap:30px;align-items:start}}
.figrow figure svg{{max-width:540px}}
.key{{margin:0 0 4px}}
.key svg{{width:auto;max-width:100%;height:20px}}

/* tables */
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin:6px 0 4px}}
th{{text-align:left;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:{MUTED};
 font-weight:700;padding:0 10px 8px;border-bottom:1px solid {LINE};vertical-align:bottom}}
td{{padding:9px 10px;border-bottom:1px solid {LINE_SOFT};vertical-align:top}}
th.n,td.n{{text-align:right}}
tbody tr.sel td{{background:#fffaf1}}
tfoot td{{font-weight:700;border-top:1.5px solid {LINE};border-bottom:0;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}}
.sw{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:8px;vertical-align:1px}}
.zero td{{color:{MUTED}}}
.note{{font-size:12.5px;color:{MUTED};line-height:1.55;margin:10px 0 0;max-width:88ch}}
.scroll{{overflow-x:auto}}

/* assumptions */
.asm{{border:1px solid {LINE};border-radius:8px;padding:20px 22px;
 margin:0 0 16px;background:{SOFT}}}
.asm h6{{margin:0 0 12px;font-size:13.5px;font-weight:700;letter-spacing:-.005em;color:{INK}}}
.asm table{{margin:0}}
.asm td{{padding:7px 10px;font-size:13px}}
.asm td:first-child{{width:34%;font-weight:600}}
.asm td:last-child{{color:{INK_2}}}

/* refs + footer */
.refs{{font-size:12.5px;color:{MUTED};line-height:1.6;counter-reset:r}}
.refs div{{padding:6px 0 6px 30px;position:relative;border-bottom:1px solid {LINE_SOFT}}}
.refs div:before{{counter-increment:r;content:counter(r);position:absolute;left:0;top:6px;
 font-weight:700;color:{INK_2};font-family:ui-monospace,monospace}}
sup{{font-size:10px;font-weight:700;color:{AMBER};padding-left:1px}}
.foot{{padding:26px 0 0;border-top:1px solid {LINE};font-size:11.5px;color:{MUTED};line-height:1.7}}
.foot code{{font-family:ui-monospace,monospace;color:{INK_2};font-size:11px}}

@media (max-width:820px){{
 .page{{padding:0 22px 60px}} h1{{font-size:31px}} h3{{font-size:23px}}
 .two,.figrow{{grid-template-columns:1fr}}
}}
@media print{{
 body{{background:#fff}} .page{{max-width:none;padding:0 12mm 12mm}}
 section{{break-inside:auto;padding:26px 0}} figure,table,.asm,.kpi{{break-inside:avoid}}
 table{{font-size:12px}} td{{padding:7px 8px}} th{{padding:0 8px 7px}}
 h2,h3,h4,h5{{break-after:avoid}} .cover{{padding-top:12mm}}
 section#s1{{break-before:page;padding-top:8px;border-bottom:none}}  /* intro owns one page */
}}
"""


def page(title: str, body: str) -> str:
    return no_dashes(
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)}</title><style>{CSS}</style></head>"
        f'<body><div class="page">{body}</div></body></html>'
    )


# ---------------------------------------------------------------------------
# References. Every one is held in docs/bess_modelling_requirements.md, the repo-blind
# literature survey. Nothing is cited from memory.
# ---------------------------------------------------------------------------

# The real Neura Energy wordmark, pulled from neura.energy and embedded so the
# report stays a single self-contained file. Source art: assets/neura_logo_source@3x.png.
LOGO_B64 = "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"  # noqa: E501
LOGO_IMG = '<img class="lg" alt="Neura Energy" src="data:image/png;base64,' + LOGO_B64 + '">'

REFS = [
    (
        "Mariaud, A. et al., 'Integrated optimisation of photovoltaic and battery storage systems "
        "for UK commercial buildings', Applied Energy 199:466-478, 2017. DOI "
        "10.1016/j.apenergy.2017.04.067"
    ),
    (
        "NESO, January 2026 Final TNUoS Tariffs for 2026/27, Tables 8 and 10. "
        "neso.energy/document/376336/download"
    ),
    (
        "Engels, J., Claessens, B., Deconinck, G., 'Combined Stochastic Optimization of Frequency "
        "Control and Self-Consumption With a Battery', IEEE Transactions on Smart Grid "
        "10(2):1971-1981, 2019. DOI 10.1109/TSG.2017.2785040"
    ),
    (
        "Xu, B., Zhao, J., Zheng, T., Litvinov, E., Kirschen, D.S., 'Factoring the Cycle Aging "
        "Cost of Batteries Participating in Electricity Markets', IEEE Transactions on Power "
        "Systems 33(2):2248-2259, 2018. DOI 10.1109/TPWRS.2017.2733339"
    ),
    (
        "NESO, 'Demand Flexibility Service Procurement Rules', v5.0, effective 18 May 2026, "
        "Schedule 1 and paras 4.2, 4.3.2. neso.energy/document/379391/download"
    ),
    (
        "Ofgem, 'Decision on P415: Facilitating Access to Wholesale Markets for Flexibility "
        "Dispatched by Virtual Lead Parties'. ofgem.gov.uk"
    ),
    (
        "Fioriti, D., Pellegrino, L., Lutzemberger, G., Micolano, E., Poli, D., 'Optimal sizing of "
        "residential battery systems with multi-year dynamics and a novel rainflow-based model of "
        "storage degradation', Electric Power Systems Research 203:107675, 2021. DOI "
        "10.1016/j.epsr.2021.107675"
    ),
    (
        "National Grid Electricity Distribution, Use of System Charging Statement effective 1 "
        "April 2026, Annex 1. commercial.nationalgrid.co.uk"
    ),
    "Gresham House Energy Storage Fund plc, 'Annual Report and Financial Statements 2025', 2026.",
    (
        "DESNZ, 'Electricity Networks Strategic Framework, Appendix 1: Electricity Networks "
        "Modelling'. gov.uk/government/publications/electricity-networks-strategic-framework"
    ),
    (
        "Ofgem, 'RIIO-2 Electricity Distribution Annual Report 2024 to 2025', published January "
        "2026, DSO incentive section. "
        "ofgem.gov.uk/sites/default/files/2026-01/ED-annual-report-2024-2025.pdf"
    ),
    (
        "DESNZ, 'Accelerating electricity network connections for strategic demand', consultation "
        "published 11 March 2026. gov.uk/government/consultations/"
        "accelerating-electricity-network-connections-for-strategic-demand"
    ),
]


def ref(*ns: int) -> str:
    return "<sup>" + ",".join(str(n) for n in ns) + "</sup>"


def sec_intro() -> str:
    """One page, four questions: what it is, what it unlocks for you, what it unlocks for the
    system, and why now. The sub-headings carry the argument, so a reader who only scans them
    still gets the whole case."""
    return f"""
<section id="s1">
<h2>Section 1</h2>
<h3>Behind the meter storage, and why it matters now</h3>
<div class="two">
<div>
<h4>What it is</h4>
<p>A battery on your side of the meter. It charges when electricity is cheap and discharges when it
is expensive, so the site draws less from the grid at exactly the hours the grid charges most for
it. Nothing about how the site runs changes.</p>

<h4>What it unlocks for the business</h4>
<p>Three things from one asset. It <b>cuts the delivered cost</b> of energy, by moving demand out of
the hours that carry the highest network and policy charges. It <b>frees connection capacity</b>,
because the peak the network sees is lower than the peak the site draws, so the site can grow
without waiting on a connection upgrade. And it <b>earns</b>: the same battery that lowers the bill
is paid by the grid for being available to it.</p>

<p>The third changes the character of the investment. A battery that shaves your own bill saves
money. One that can free or firm capacity somebody else urgently needs is an asset with an outside
buyer, and a contracted payment from another balance sheet is worth far more to a lender than a
saving on your own invoice. The same steel stops being an efficiency project and becomes
infrastructure.</p>
</div>
<div>
<h4>What it unlocks for the system</h4>
<p>The grid must be built for its worst hour, and that hour is worsening fastest of all. Peak demand
could rise from around 58 GW in 2020 to between 130 and 190 GW by 2050.{ref(10)} Each of those
gigawatts is built in cable and steel, then stands idle most of the year. The network is used a
smaller and smaller share of the time, and the regulator&#8217;s answer is not more of it but more
from what exists, &#8220;considering flexible and smart alternatives to network
reinforcement&#8221;.{ref(11)} A battery behind a live meter is exactly that, sited where the
constraint actually bites.</p>

<h4>Why now</h4>
<p>Three things moved at once. <b>The bill changed shape</b>: the transmission demand residual rose
63% in one charging year, from &#163;3,836m to &#163;6,265m,{ref(2)} and distribution charges carry
a red band on weekday late afternoons, the highest unit rate a site pays all day.{ref(8)}
<b>Capacity became scarce</b>: the queue to connect new demand stood at 96 GW at transmission and 29
GW at distribution, data centres alone about 140 projects and roughly 50 GW of it, and government is
consulting on whether users that large should have to provide demand flexibility as a condition of
connecting.{ref(12)} <b>The markets opened</b>: routes once shut below a megawatt now take assets
this size.{ref(5, 6)}</p>

<p>Those streams compete for the same power in the same half hour, so this study solves them as one
problem,{ref(3, 4)} against the <b>shape</b> of your own metered demand rather than an annual
total.{ref(1, 7)}</p>
</div>
</div>
</section>"""


def sec_method(n_sites: int) -> str:
    plural = "each site" if n_sites > 1 else "the site"
    return f"""
<section id="s2">
<h2>Section 2</h2>
<h3>How we validated this for {plural}</h3>
<p>The method below is the same one run on every site in this report, and it is run from
the site's own data rather than from a template. It is a single pipeline: data goes in at
stage one, and the returns in {"sections 4 to " + str(3 + n_sites) if n_sites > 1 else "section 4"}
fall out of stage six. No figure is written by hand at any point.</p>
<figure>{fig_method_flow()}
<figcaption><b>Figure 1.</b> The six stages, and every step inside them. The battery is
operated across a full year of real half hours before any financial number exists, and the
whole year is re-solved for every candidate size.</figcaption>
</figure>
</section>

<section id="s3">
<h2>Section 3</h2>
<h3>The method, in plain terms</h3>
<div class="two">
<div>
<h4>We simulate the battery, we do not estimate it</h4>
<p>Most feasibility work multiplies an average price by an assumed number of cycles. That
approach cannot tell you whether the battery had the energy available at the hour the value
appeared, so it tends to count the same stored kilowatt hour more than once.</p>
<p>This model instead decides what the battery does in every one of the 17,520 half hours of
the year, and decides all of them at the same time. The decision in one half hour is
constrained by the state of charge it inherits from the last and the state it must leave for
the next. That is what makes the answer a schedule the battery could actually run, rather
than a sum of things it could each have done separately.</p>

<h4>All twelve revenue streams compete for one battery</h4>
<p>Twelve ways of earning are priced, from bill savings and network charge avoidance through
to the Capacity Market, local flexibility, the Demand Flexibility Service, the Balancing
Mechanism, frequency response and wholesale. They are not modelled as twelve business cases
to be added together. They are modelled as twelve claims on one shared power rating and one
shared store of energy, and the optimiser decides which claim wins in each half hour.</p>
<p>Where two streams would book the same pound, only one carries it. Bill savings are carried
by tariff arbitrage and red band avoidance; capacity headroom is carried by the agreed
capacity reduction. Both are shown at zero in the tables that follow, with a note saying
where their value sits.</p>
</div>
<div>
<h4>The battery is treated as a physical asset</h4>
<p>Round trip efficiency, power limits and state of energy limits are enforced in every
half hour. Throughput is charged a degradation cost, so a cycle the battery runs has to be
worth more than the life it consumes.{ref(4)} A schedule that would wear the asset out for a
marginal gain is therefore rejected by the model rather than by a later reviewer.</p>

<h4>Every size is tested, not one</h4>
<p>Sizing is not assumed and it is not scaled from a rule of thumb. The full year is solved
again for each combination of power and duration in the search space, and each candidate
carries its own revenue, operating cost, capital cost and return. The figures in section 4
show that whole space. The selected size is the point on it with the strongest project
return, with the smaller system taken where two are close.{ref(7)}</p>

<h4>What is stated, and what is assumed</h4>
<p>Where an input was missing it was derived from published GB sources and recorded as a
labelled assumption with the exact question needed to confirm it. Nothing is left blank and
nothing is quietly set to zero. Section {4 + n_sites} lists the assumptions that matter,
grouped, with the direction each one pushes the result.</p>
</div>
</div>
</section>"""


# ---------------------------------------------------------------------------
# Tables. Each cell is a formatted record value.
# ---------------------------------------------------------------------------


def sizing_table(s: Site) -> str:
    pts = [p for p in s.grid["points"] if p.get("project_irr_pct") is not None]
    full = [p for p in pts if not p.get("proxy")] or pts
    best = sorted(full, key=lambda p: -float(p["project_irr_pct"]))[:5]
    top_rev = max(pts, key=lambda p: float(p.get("annual_revenue_gbp") or 0.0))
    sel = (round(s.power_kw, 3), round(s.duration_h, 3))
    rows: list[str] = []

    def row(p: dict, tag: str = "") -> str:
        key = (round(float(p["power_kw"]), 3), round(float(p["duration_hours"]), 3))
        cls = ' class="sel"' if key == sel else ""
        label = "Selected" if key == sel else tag
        return (
            f"<tr{cls}><td class=mono>{e(num(float(p['power_kw']), 1))}</td>"
            f"<td class='n mono'>{e(num(float(p['duration_hours']), 2))}</td>"
            f"<td class='n mono'>{e(num(float(p['energy_kwh']), 1))}</td>"
            f"<td class='n mono'>{e(gbp(float(p.get('annual_revenue_gbp') or 0)))}</td>"
            f"<td class='n mono'>{e(gbp(float(p.get('annual_operating_cost_gbp') or 0)))}</td>"
            f"<td class='n mono'>{e(pct(float(p['project_irr_pct']), 2))}</td>"
            f"<td>{'Screening estimate' if p.get('proxy') else 'Full year solve'}"
            f"{f'<br><b>{e(label)}</b>' if label else ''}</td></tr>"
        )

    for p in best:
        rows.append(row(p))
    if (round(float(top_rev["power_kw"]), 3), round(float(top_rev["duration_hours"]), 3)) not in {
        (round(float(p["power_kw"]), 3), round(float(p["duration_hours"]), 3)) for p in best
    }:
        rows.append(row(top_rev, "Most revenue evaluated"))

    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Power (kW)</th><th class=n>Duration (h)</th><th class=n>Energy (kWh)</th>"
        "<th class=n>Revenue a year</th><th class=n>Operating cost</th>"
        "<th class=n>Return</th><th>Basis</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def stream_table(s: Site) -> str:
    rows: list[str] = []
    total = s.revenue_total
    for v in _ordered_streams(s):
        st = v["stream"]
        val = float(v["annual_revenue_gbp"] or 0.0)
        cost = float(v["annual_cost_gbp"] or 0.0)
        fam, col = FAMILY_OF.get(st, ("", MUTED))
        note = STREAM_NOTE.get(st, "")
        if not note and val == 0 and cost > 0:
            note = f"Net cost of {gbp(cost)} at this size, so no revenue is claimed."
        elif not note and val == 0:
            gate = v.get("gate_status")
            note = (
                "Not available at this site on the site's own tariff, so it is not claimed."
                if gate == "closed"
                else "The optimiser found no half hour where this stream beat the alternatives."
            )
        share = f"{val / total * 100:.1f}%" if total > 0 and val > 0 else ""
        cls = "" if val > 0 else ' class="zero"'
        rows.append(
            f'<tr{cls}><td><span class=sw style="background:{col}"></span>'
            f"{e(STREAM_LABEL.get(st, st))}</td>"
            f"<td>{e(STREAM_WHAT.get(st, ''))}{(' ' + e(note)) if note else ''}</td>"
            f"<td class='n mono'>{e(gbp(val))}</td><td class='n mono'>{e(share)}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>Revenue stream</th><th>What it is</th>'
        "<th class=n>Per year</th><th class=n>Share</th></tr></thead><tbody>"
        + "".join(rows)
        + f'</tbody><tfoot><tr><td colspan="2">Total, the sum of the stated streams</td>'
        f'<td class="n">{e(gbp(total))}</td><td class="n">100%</td></tr></tfoot></table></div>'
    )


# ---------------------------------------------------------------------------
# The per site results section. The prose here is assembled from THAT site's record,
# not from a shared template: the sites differ in ways a template would state falsely.
# ---------------------------------------------------------------------------

#: Site specific context that lives in the record's own site_name / missing_data and would
#: otherwise read as an error on the page. Text only, never a figure.
CONTEXT = {
    "quote_run_890c51f0e774218d": (
        "This is the grid import supply only, one of the site's two supplies, so the annual "
        "consumption below is not the whole site."
    ),
    "quote_run_53277c6ddcd19603": (
        "This site is served by an energy from waste private wire alongside on site solar, so "
        "the metered import is what remains after both."
    ),
    "quote_run_39fb87f5f0d6f0c1": (
        "No tariff of any kind was supplied for this site, so the delivered cost stack is "
        "modelled on Ewelme's rates by analogy. Confirming the actual tariff is the single "
        "highest value input we can be given."
    ),
}


def kpis(s: Site) -> str:
    cells = [
        (
            "Battery size",
            f"{num(s.power_kw, 1)} kW",
            f"{num(s.energy_kwh, 1)} kWh, {num(s.duration_h, 1)} h",
        ),
        ("Annual revenue", gbp(s.revenue_total), "sum of the stated streams"),
        ("Project return", pct(s.irr_pct), "unlevered, before any debt"),
        ("Payback", f"{num(s.payback_years, 1)} yr", "undiscounted"),
        ("Capital cost", gbp(s.capex_gbp), "modelled, all in"),
    ]
    return (
        '<div class="kpis">'
        + "".join(
            f"<div class=kpi><div class=l>{e(lab)}</div>"
            f"<div class=v>{e(v)}</div><div class=s>{e(sub)}</div></div>"
            for lab, v, sub in cells
        )
        + "</div>"
    )


def capacity_is_derived(s: Site) -> bool:
    """Whether the recorded import capacity is a labelled assumption, not a supplied figure."""
    return any(
        d.get("key") == "connection.mic_kw" for d in (s.site.get("derived_assumptions") or [])
    )


def capacity_is_supplied(s: Site) -> bool:
    """Mirrors `gridq.outputs.report_charts.capacity_is_supplied` against the raw record.

    `mic_kw` is a required field, so a site whose agreed capacity was never supplied still carries
    a number: the convention is to set it to the observed peak and record zero import headroom.
    Printing that under the label "Agreed capacity" would state a stand-in as a site fact, which is
    precisely the derived-versus-supplied distinction the rest of this document turns on. The
    record's own DerivedAssumption is checked first and beats every heuristic.
    """
    if capacity_is_derived(s):
        return False
    conn = s.conn
    if conn.get("asc_kw") is not None:
        return True
    if conn.get("import_headroom_kw") == 0.0:
        return False
    peak = s.load.get("max_import_kw")
    return not (peak is not None and conn.get("mic_kw") == peak)


def capacity_note(s: Site) -> str:
    """What was used in place of an agreed capacity, and the ask that would replace it."""
    if capacity_is_supplied(s):
        return ""
    peak = s.load.get("max_import_kw")
    if capacity_is_derived(s):
        return (
            f"No agreed import capacity was supplied for this site. The model used "
            f"{num(float(s.conn['mic_kw']), 0)} kW, derived as the smallest standard rating above "
            f"the measured peak of {num(float(peak), 1)} kW and recorded as an assumption. "
            f"Confirming the actual agreed capacity is a data ask."
        )
    return (
        f"No agreed import capacity was supplied for this site. The model used the observed peak "
        f"of {num(float(peak), 1)} kW as a lower bound, which leaves no headroom above today's "
        f"demand and constrains the sizes the search can reach. Confirming the actual agreed "
        f"capacity is a data ask."
    )


def site_line(s: Site) -> str:
    ld, cn = s.load, s.conn
    bits = [
        ("Annual import", f"{float(ld['annual_kwh']):,.0f} kWh"),
        ("Metered peak", f"{num(float(ld['max_import_kw']), 1)} kW"),
        (
            "Agreed capacity",
            f"{num(float(cn['mic_kw']), 0)} kW" if capacity_is_supplied(s) else "Not supplied",
        ),
        ("Network operator", str(cn.get("dno_region") or "not supplied").replace("_", " ")),
        ("Data basis", f"{int(ld['half_hourly_rows']):,} metered half hours"),
        ("Period", f"{ld['start_date']} to {ld['end_date']}"),
    ]
    return (
        '<div class="meta">'
        + "".join(f"<div>{e(k)}<b>{e(v)}</b></div>" for k, v in bits)
        + "</div>"
    )


def dispatch_words(s: Site) -> str:
    pts = s.dispatch["points"]
    disc = [p for p in pts if float(p["discharge_kw"]) > 0.01]
    chg = [p for p in pts if float(p["charge_kw"]) > 0.01]
    duos_applies = max((float(p["duos_rate_gbp_per_kwh"]) for p in pts), default=0.0) > 0
    red_disc = [p for p in disc if p.get("red_band")] if duos_applies else []
    peak = max((float(p["discharge_kw"]) for p in disc), default=0.0)
    basis = str(s.dispatch.get("selection_basis") or "").replace("-", " ")
    parts = [
        f"On this day the battery discharged in {len(disc)} half hours and charged in "
        f"{len(chg)} others.",
    ]
    if red_disc:
        parts.append(
            f"Of those discharge half hours, {len(red_disc)} fall inside the DUoS red band, the "
            f"shaded strip, where the unit rate is at its highest."
        )
    parts.append(
        f"Peak discharge reached {num(peak, 1)} kW against a rating of {num(s.power_kw, 1)} kW."
    )
    rates = [float(q["duos_rate_gbp_per_kwh"]) for q in pts]
    if red_disc and max(rates) > min(rates):
        parts.append(
            f"On this site's own tariff the red band unit rate is {max(rates) * 100:,.3f} p/kWh "
            f"against {min(rates) * 100:,.3f} p/kWh at its lowest, which is the step the battery "
            f"is discharging into."
        )
    parts.append(f"The day shown is the {basis} of the modelled year.")
    return " ".join(parts)


def sec_results(s: Site, num_idx: int, *, single: bool) -> str:
    ctx = CONTEXT.get(s.run_id, "")
    lead = STREAM_LABEL.get(
        max(s.streams, key=lambda v: float(v["annual_revenue_gbp"] or 0.0))["stream"], ""
    )
    top3 = sorted(s.streams, key=lambda v: -float(v["annual_revenue_gbp"] or 0.0))[:3]
    top3_share = sum(float(v["annual_revenue_gbp"] or 0.0) for v in top3)
    scored = [p for p in s.grid["points"] if p.get("project_irr_pct") is not None]
    n_screen = len([p for p in scored if p.get("proxy")])
    n_full = len([p for p in scored if not p.get("proxy")])
    share_txt = f"{top3_share / s.revenue_total * 100:.0f}%" if s.revenue_total else "n/a"
    head = (
        f"<h2>Section {num_idx}</h2><h3>{e(s.display)}</h3>"
        if not single
        else f"<h2>Section {num_idx}</h2><h3>The result for {e(s.display)}</h3>"
    )
    return f"""
<section id="r{num_idx}">
{head}
{f"<p>{e(ctx)}</p>" if ctx else ""}
{site_line(s)}
{f'<p class="note">{e(capacity_note(s))}</p>' if capacity_note(s) else ""}
{kpis(s)}

<h5>Sizing</h5>
<p>Every mark in the figure below is a battery size the model actually solved. The search works in
two passes: it screens the whole space on typical periods, then re-solves a shortlist across the
full year of half hours before anything is selected. For {e(s.display)} that was
{n_screen} screening solves and {n_full} full year solves. The selected size is the
strongest of the full year solves, and no screening
estimate is ever the basis of a selection.</p>
<figure>{fig_sizing_3d(s)}
<figcaption><b>Figure {num_idx}.1.</b> The sizing surface for {e(s.display)}, viewed from one
fixed angle. Power runs along the lower axis, duration into the page, and the height of each
column is that size's project return, standing on the break even plane. Solid heads are full year
solves, hollow heads are screening solves. <b>Every column is a solved candidate and nothing is
drawn between them:</b> there is no interpolated skin over this surface, because a smooth sheet
would show returns at sizes the model never evaluated.</figcaption>
</figure>
<div class="figrow">
<div><figure style="margin-top:0">{fig_sizing_2d(s)}
<figcaption><b>Figure {num_idx}.2.</b> The same candidates viewed straight on, so the peak can be
read off an axis. The line joins only the full year solves that share the selected duration, which
keeps it a like for like comparison. Returns fall away on both sides: too small and the fixed
costs dominate, too large and the extra capacity earns less than it costs.</figcaption>
</figure></div>
<div>
<h5 style="margin-top:0">What the surface shows</h5>
<p>Return is not a straight line in size. Below the peak the fixed elements of the capital and
operating cost are spread over too little capacity. Above it, each additional kW is dispatched
into hours that are worth progressively less, because the most valuable half hours of the year
have already been used by the kW before it.</p>
<p>The table below gives the five strongest candidates and, for comparison, the candidate that
earned the most revenue of any size evaluated. The largest system is not the best investment,
and the difference between those two rows is exactly why the search covers the whole space.</p>
</div>
</div>
{sizing_table(s)}
<p class="note">Project return is unlevered: it is the return on the whole capital cost with no
debt in the structure. Operating cost is the modelled annual cost of running the asset at that
size. A full year solve dispatches the candidate across all 17,520 half hours; a screening
estimate scores it on typical periods to steer the search and is never the basis of a selection.
Rows are read from the run record.</p>

<h5>Where the value comes from</h5>
<p>The recommended battery earns {e(gbp(s.revenue_total))} a year across
{len([v for v in s.streams if float(v["annual_revenue_gbp"] or 0) > 0])} streams, with
{e(lead.lower())} the largest single contributor and the top three together making up
{e(share_txt)} of the total. No stream here is a projection of what the site might do
differently. Each is the value the solved schedule actually captured.</p>
<div class="key">{fig_family_key()}</div>
<figure>{fig_revenue_pie(s)}
<figcaption><b>Figure {num_idx}.3.</b> The annual revenue split. Colour groups the streams into
four families; each slice is labelled with its own value and share.</figcaption>
</figure>
{stream_table(s)}

<h5>A day in the life of the battery</h5>
<p>{e(dispatch_words(s))}</p>
<figure>{fig_dispatch(s)}
<figcaption><b>Figure {num_idx}.4.</b> One solved day at {e(s.display)}, half hour by half hour.
Reading down: what an imported unit cost, what the site was drawing, what the battery did, and
how much energy it had left. The battery fills when the delivered price is low and empties into
the expensive hours, and the stored energy trace shows it never asked for more than it
had.</figcaption>
</figure>
</section>"""


# ---------------------------------------------------------------------------
# Assumptions. Grouped, tight, and each one carries the direction it pushes the answer.
# Values are the pinned assumption release the run record names (gb_v1_12) and the run's
# own DerivedAssumption records. Nothing here is a second pricing path.
# ---------------------------------------------------------------------------

ASM_BASIS = [
    (
        "Every revenue stream is open",
        "All twelve modelled and priced, none suppressed.",
        "Raises the result. A stream a site could not access in practice still earns here.",
    ),
    (
        "Every approval is assumed granted",
        (
            "Export consent and MPAN, G99 and G100, route to market, credit assessment, Capacity "
            "Market prequalification, Balancing Mechanism and response unit registration, DFS "
            "registration, network flexibility awards, aggregation, and every product minimum size "
            "including the 1 MW registered unit and the 0.1 MW DFS floor."
        ),
        (
            "Raises the result. None of these is held today, and the report says assumed, never "
            "granted."
        ),
    ),
    (
        "Stacking exclusivity is waived",
        (
            "DFS may not in practice share a meter point with a Balancing Mechanism unit, and is "
            "conditionally barred from the response family. Both are assumed satisfied."
        ),
        "Raises the result. Two stream pairs would in practice compete.",
    ),
    (
        "Optimistic end of cited ranges",
        "Where a sourced range exists, the favourable end is taken. Every value is still cited.",
        "Raises the result.",
    ),
    (
        "No pound is counted twice",
        (
            "Bill savings sit inside arbitrage and red band avoidance; capacity headroom sits "
            "inside the agreed capacity reduction. Both are held at zero."
        ),
        "Neutral, and it is the one rule not relaxed.",
    ),
    (
        "Aggregator share",
        "20% of routed grid market revenue is deducted before the figures shown.",
        "Lowers the result. No signed aggregator agreement exists.",
    ),
]

ASM_MARKET = [
    (
        "Capacity Market",
        (
            "£27.10/kW/yr, the T-4 clearing price for delivery year 2029/30, multiplied by the "
            "same auction's published storage de-rating for the asset's own duration (0.2198 at "
            "two hours)."
        ),
        "Neutral. Both legs are published NESO figures.",
    ),
    (
        "Export price",
        (
            "The mean of Elexon Market Index Data across one settlement day, 13 July 2026, 48 half "
            "hours."
        ),
        (
            "Unknown direction. One summer day is not a forward annual curve, and this is the "
            "weakest price input in the study."
        ),
    ),
    (
        "Demand Flexibility Service",
        (
            "The site's own DFS zone, priced on that zone's measured outturn over the observed "
            "number of called settlement periods."
        ),
        "Neutral. Zonal and measured, not the best zone in the country.",
    ),
    (
        "Distribution charges",
        (
            "Each site's own network operator's published charging statement for 2026/27, resolved "
            "to its red, amber and green bands and clock times."
        ),
        "Neutral.",
    ),
    (
        "Capacity Market levy avoided",
        (
            "£0.30581/kWh inside its own charging window, the top of the published 5.313 to 31.382 "
            "p/kWh range."
        ),
        "Raises the result. An explicit optimistic pick.",
    ),
]

ASM_ASSET = [
    (
        "Installed cost",
        (
            "£700,000 per MW, of which £245/kWh is the energy component. The published benchmark "
            "band is £300,000 to £900,000 per MW."
        ),
        (
            "A sub megawatt system costs more per MW than a grid scale one, so this is likely to "
            "be light."
        ),
    ),
    (
        "Fixed operating cost",
        "£12,000 per MW per year.",
        "Excludes site specific civils, grid connection works and land.",
    ),
    (
        "Degradation",
        (
            "£21.30 per MWh discharged, charged against every unit of throughput inside the "
            "optimiser rather than added afterwards."
        ),
        "Lowers the result, correctly. It is why the model declines marginal cycles.",
    ),
    (
        "Round trip efficiency",
        "88% AC to AC.",
        "Measured systems sit between 83% and 87% when new, so this is at the optimistic end.",
    ),
    (
        "Usable energy window",
        (
            "15% to 95% state of charge, with 365 full cycles a year at two hours as the warranty "
            "limit."
        ),
        "Neutral.",
    ),
]

ASM_FINANCE = [
    (
        "Unlevered basis",
        (
            "No credit assessment or debt term sheet was supplied, so no debt structure was "
            "resolved and no covenant test was run. The returns quoted are project returns on the "
            "full capital cost."
        ),
        (
            "The covenant gate did not fail. It never ran. Supplying a credit assessment is what "
            "turns this into a levered result."
        ),
    ),
    ("Asset life", "15 operating years, undiscounted payback.", "Neutral."),
    (
        "Price sensitivities",
        (
            "The low, central and high cases are identical because no separate low or high price "
            "band was supplied. A spread was not invented to fill the gap."
        ),
        (
            "The report therefore carries no price range, and that is a stated gap rather than a "
            "modelled one."
        ),
    ),
]


def asm_block(title: str, rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f"<tr><td>{e(k)}</td><td>{e(v)}<br><span style='color:{MUTED}'>{e(d)}</span></td></tr>"
        for k, v, d in rows
    )
    return f'<div class="asm"><h6>{e(title)}</h6><table><tbody>{body}</tbody></table></div>'


#: Readable names for the derived-input keys the records actually carry. A raw dotted field
#: path on a client-facing page reads as a leak, not as precision.
ASM_KEY_LABEL = {
    "tariff_stack.duos_red_rate": "DUoS red band unit rate",
    "tariff_stack.duos_amber_rate": "DUoS amber band unit rate",
    "tariff_stack.duos_green_rate": "DUoS green band unit rate",
    "tariff_stack.windowed_levies.CM_peak": "Capacity Market levy, peak window",
    "tariff_stack.levies": "Levy lines on imported energy",
    "local_flex_awards.reference": "Local flexibility award reference",
    "local_flex_awards.committed_power_kw": "Local flexibility committed power",
    "connection.mic_kw": "Agreed import capacity",
    "postcode": "Site postcode",
}


def asm_key(key: str) -> str:
    if key in ASM_KEY_LABEL:
        return ASM_KEY_LABEL[key]
    leaf = key.split(".")[-1].replace("_", " ")
    return leaf[:1].upper() + leaf[1:]


def first_sentences(text: str, limit: int = 230) -> str:
    """Whole sentences up to ``limit``, never a word cut in half.

    The confirmation asks in the record run to several hundred characters. Slicing them at a
    fixed offset left "...which is the only thing that truly confirm" on the page, which reads
    as a broken renderer rather than as an abridged note.
    """
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    out = ""
    for part in text.replace("; ", ". ").split(". "):
        candidate = f"{out}. {part}".strip(". ") if out else part
        if len(candidate) > limit:
            break
        out = candidate
    if not out:
        out = text[:limit].rsplit(" ", 1)[0]
    return out.rstrip(".") + "."


def sec_assumptions(sites: list[Site], num_idx: int) -> str:
    per_site: list[str] = []
    for s in sites:
        rows: list[tuple[str, str, str]] = []
        for d in s.site.get("derived_assumptions") or []:
            kind = d["kind"]
            if kind == "pending_permission":
                val = "Assumed in place, not held. Modelled and shown, never written as granted."
            elif kind == "not_applicable":
                val = "Does not apply at this site, and the zero is evidenced rather than missing."
            else:
                val = f"{d.get('value')} {d.get('unit') or ''}".strip()
            rows.append(
                (asm_key(str(d["key"])), val, first_sentences(d.get("confirmation_ask") or ""))
            )
        asks = [str(m) for m in (s.site.get("missing_data") or [])][:4]
        if asks:
            rows.append(("Outstanding data asks", first_sentences(" ".join(asks), 620), ""))
        if rows:
            per_site.append(asm_block(f"{s.display}: derived inputs and data asks", rows))

    return f"""
<section id="asm">
<h2>Section {num_idx}</h2>
<h3>What this rests on</h3>
<div class="basis">{BASIS_NOTICE}</div>
<p>Everything here changes the answer. Each entry says which way it pushes the result. The full
register, with every source and every value, accompanies this report.</p>

<h5>The basis of the study</h5>
{asm_block("Study basis", ASM_BASIS)}

<h5>Market prices</h5>
{asm_block("Prices and charges", ASM_MARKET)}

<h5>The battery and what it costs</h5>
{asm_block("Asset and cost", ASM_ASSET)}

<h5>Finance</h5>
{asm_block("Financial treatment", ASM_FINANCE)}

<h5>Site inputs that had to be derived</h5>
<p>Where an input was absent it was derived from published sources and labelled, rather than
left blank or quietly set to zero. Each carries the question that would replace it with a fact.</p>
{"".join(per_site)}
</section>"""


# ---------------------------------------------------------------------------
# Cover, contents, references, footer, and the two documents.
# ---------------------------------------------------------------------------

#: The basis statement, cut to its load-bearing sentence and moved OFF the cover into the
#: assumptions section, where a reader is already asking what the numbers rest on. The basis is
#: still STAMPED on the cover and in the footer as the `Study basis` and `Confidence band`
#: fields, which is what D46 and D57 require of every artifact; what moved is the prose, not the
#: label.
BASIS_NOTICE = (
    "<b>A feasibility study, not an underwriting output and not an offer.</b> Every revenue "
    "stream is modelled as open and every contract, consent, award and registration is "
    "<b>assumed approved</b>. The groups below are what that covers."
)


def cover(title: str, lede: str, sites: list[Site], subtitle: str) -> str:
    s = sites[0]
    rows = [
        ("Prepared for", subtitle),
        ("Sites in this report", str(len(sites))),
        ("Data basis", "Metered half hourly"),
        ("Assumption release", str(s.quote["assumption_version"])),
        ("Confidence band", str(s.quote["confidence_band"]).title()),
        # Last on purpose: it is the longest value on the row, so wrapping it here leaves the
        # shorter fields whole instead of orphaning one onto a line of its own.
        ("Study basis", str(s.quote["study_basis_id"])),
    ]
    meta = "".join(f"<div>{e(k)}<b>{e(v)}</b></div>" for k, v in rows)
    return f"""
<div class="cover">
 <div class="brand">{LOGO_IMG}
 <div class="dv">GRIDQ &nbsp;/&nbsp; BEHIND THE METER STORAGE</div></div>
 <h1>{e(title)}</h1>
 <p class="lede">{e(lede)}</p>
 <div class="meta">{meta}</div>
</div>"""


def contents(entries: list[tuple[str, str]]) -> str:
    items = "".join(f"<div><span>{e(n)}</span>{e(t)}</div>" for n, t in entries)
    return (
        f'<section style="padding:34px 0"><h2>Contents</h2><div class="toc">{items}</div></section>'
    )


def sec_refs(num_idx: int) -> str:
    items = "".join(f"<div>{e(r)}</div>" for r in REFS)
    return (
        f'<section id="refs"><h2>Section {num_idx}</h2><h3>References</h3>'
        f'<div class="refs">{items}</div></section>'
    )


def footer(sites: list[Site]) -> str:
    lines = "".join(
        f"<div><code>{e(s.display)}</code> &nbsp; run <code>{e(s.run_id)}</code> &nbsp; "
        f"input hash <code>{e(str(s.quote['input_hash'])[:23])}...</code> &nbsp; "
        f"code <code>{e(str(s.quote['code_version']))}</code></div>"
        for s in sites
    )
    return f"""
<div class="foot">
<p style="max-width:none;margin:0 0 12px">Every figure in this report is read from a stored run
record, each stamped with the input hash, assumption release and code version below. Nothing is
recomputed for presentation and no number is authored. Totals labelled as such are the plain sum
of the record's own stated per stream values.</p>
{lines}
<p style="max-width:none;margin:12px 0 0">Produced on the named study basis
<code>all_streams_enabled_study_v1</code>. Not a quotation, not a commitment, and not an
underwriting output. Neura Energy, {e(str(sites[0].quote["created_at"])[:10])}.</p>
</div>"""


def build_grundon() -> str:
    sites = [load(rid, name) for rid, name in GRUNDON]
    toc = [
        ("1", "Behind the meter storage, and why it matters now"),
        ("2", "How we validated this for each site"),
        ("3", "The method, in plain terms"),
    ]
    toc += [(str(4 + i), s.display) for i, s in enumerate(sites)]
    toc += [(str(4 + len(sites)), "What this rests on"), (str(5 + len(sites)), "References")]

    results = "".join(sec_results(s, 4 + i, single=False) for i, s in enumerate(sites))
    body = (
        cover(
            "Behind the meter battery storage across five Grundon sites",
            "A feasibility study of what a behind the meter battery is worth at each of five "
            "Grundon sites, modelled from a full year of the sites' own metered half hourly "
            "readings. Every size was solved, not assumed, and every revenue stream was priced "
            "against the same battery in the same half hour.",
            sites,
            "Grundon Waste Management",
        )
        + contents(toc)
        + sec_intro()
        + sec_method(len(sites))
        + f"""
<section id="estate">
<h2>The estate at a glance</h2>
<h3>Five metered sites, five separate answers</h3>
<p>Each site below was modelled independently from its own meter, its own tariff and its own
network operator's charges. The sites differ in what storage is worth to them by more than the
difference in their consumption would suggest, which is the shape effect described in section 1.
The figures are shown side by side rather than added together: five separate studies, not one
portfolio number.</p>
<figure>{fig_estate(sites)}
<figcaption><b>Figure 2.</b> Annual revenue, project return and payback for each site's selected
battery. Bars are scaled within each column, so lengths compare across sites within a measure and
not between measures.</figcaption>
</figure>
<p class="note">Only metered sites appear in this report. Two Grundon supplies were additionally
modelled on an invoice anchored basis, and those results are published separately so that two
different answers never sit under one site name.</p>
</section>"""
        + results
        + sec_assumptions(sites, 4 + len(sites))
        + sec_refs(5 + len(sites))
        + footer(sites)
    )
    return page("Grundon: behind the meter battery storage feasibility study", body)


def build_harwell() -> str:
    sites = [load(rid, name) for rid, name in HARWELL]
    s = sites[0]
    toc = [
        ("1", "Behind the meter storage, and why it matters now"),
        ("2", "How we validated this for the site"),
        ("3", "The method, in plain terms"),
        ("4", "The result for Harwell Campus"),
        ("5", "What this rests on"),
        ("6", "References"),
    ]
    body = (
        cover(
            "Behind the meter battery storage at Harwell Campus",
            "A feasibility study of what a behind the meter battery is worth at Harwell, modelled "
            "from a full year of the site's own metered half hourly readings. Every size was "
            "solved, not assumed, and every revenue stream was priced against the same battery in "
            "the same half hour.",
            sites,
            "Harwell Campus",
        )
        + contents(toc)
        + sec_intro()
        + sec_method(1)
        + sec_results(s, 4, single=True)
        + sec_assumptions(sites, 5)
        + sec_refs(6)
        + footer(sites)
    )
    return page("Harwell Campus: behind the meter battery storage feasibility study", body)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, html in (
        ("grundon_feasibility_report.html", build_grundon()),
        ("harwell_feasibility_report.html", build_harwell()),
    ):
        p = OUT / name
        p.write_text(html, encoding="utf-8")
        print(f"wrote {p.relative_to(REPO)}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
