"""Inline-SVG exhibit geometry for the consulting report (E-0).

`consulting_report` embeds finished SVG strings and performs **no arithmetic** (a test asserts that
structurally over its AST). The scaling, positioning and counting every chart needs therefore lives
here, following the precedent of `gridq.app.view`'s chart islands: geometry outside the renderer,
all display text formatted server-side.

**The invariant this module holds.** Geometry only. It may scale, sort, count and position values
that already exist on the run record; it may never create a *displayed* financial figure. Every
visible text node is one of:

1. a record / view-model value passed through `gridq.outputs.formatting`;
2. a fixed categorical string (a series name, a legend entry, a stated reason);
3. a pure-geometry axis label: a clock label from the interval index, or the ``0`` at an axis
   origin. Any kW / kWh / % / ratio tick is a *recorded* value (a series extreme or a recorded
   reference line), never an invented round number.

Coordinates are geometry and are exempt; they are formatted to two decimals so the same record
renders byte-identically every time.

**Self-contained.** No script, no external asset, no remote font, no ``xmlns`` (inline SVG in HTML
needs none, and the report forbids absolute URLs). Pattern and marker ids are namespaced per
exhibit so several charts can share one document. Fonts come from the report stylesheet's system
stacks via `CHART_CSS`.

**Legible light or dark.** Each chart paints its own opaque panel, so the exhibit reads the same
whether the host surface is light or dark: it is a printed brand figure, not a themed app widget.
Colours come only from the brand tokens plus the one signal colour, which is reserved for
covenant failure and for the stated reason an exhibit cannot render.

**Degradation is a first-class state.** Every builder returns either a drawn exhibit or
`unavailable_figure`: a framed panel carrying the reason, in the exhibit's own place in the layout.
Never a blank, never a zero, never an illustration standing in for a record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from gridq.outputs import formatting as fmt
from gridq.outputs.viewmodel import BillSavingsView, StreamRow, SystemSpecView
from gridq.schemas import DispatchProfile, SiteModel, SizingGrid, SizingGridPoint

__all__ = [
    "CHART_CSS",
    "bill_columns",
    "capacity_is_derived",
    "capacity_is_supplied",
    "firming_diagram",
    "load_duration_curve",
    "revenue_stack",
    "sizing_map",
    "spec_triple",
    "system_diagram",
    "unavailable_figure",
]

# --- brand tokens (neura_brand_spec.md). The one non-brand colour is the signal red, used only
# --- for covenant failure and for the stated reason an exhibit cannot render.
_INK = "#1F1F1D"
_GREY = "#8A8A85"
_RULE = "#D8D5CE"
_DASHED = "#9A9A94"
_ORANGE = "#E2551F"
_ORANGE_MID = "#EF8B4E"
_PEACH = "#F5C9A8"
_CREAM = "#EAE7E1"
_CREAM_LIGHT = "#F5F3EF"
_WHITE = "#FFFFFF"
_SIGNAL = "#A5261F"

#: Every chart is drawn on this canvas width and scaled by CSS, so one page has one figure width.
_W = 900.0

#: Stated reasons an exhibit cannot be drawn. Each names the missing input, not the symptom.
_NO_PROFILE = (
    "no representative dispatch day is persisted on this record, so there is no solved day to draw"
)
_NO_GRID = "the run record carries no evaluated sizing grid"
_NO_SITE_CHART = "no site model was supplied alongside the run record"
_NO_LOAD_DURATION = (
    "the run record carries no load-duration projection of the half-hourly series: the full year "
    "exists only as a file reference on the site record, and no percentile summary is persisted"
)
_NO_CAPACITY = "connection capacity unknown: not supplied with the site data"
_CAPACITY_LOWER_BOUND = (
    "Connection capacity unknown: not supplied with the site data. The MIC on the record was set "
    "to the site's own peak, so it is a lower bound on the agreed cap and not a recorded ceiling. "
    "No capacity rule is drawn from it, and no unused capacity is implied."
)
#: The derived-capacity variant. `_CAPACITY_LOWER_BOUND` states the peak-standing-in convention,
#: which is a different and, for a derived value, false account of where the number came from.
_CAPACITY_DERIVED = (
    "Connection capacity not supplied with the site data: the MIC on the record is a labelled "
    "derived assumption, carried with its basis and its confirmation ask. It is not a recorded "
    "agreed capacity, so no capacity rule is drawn from it and no unused capacity is implied."
)


def _capacity_note(site: SiteModel) -> str:
    """Which absent-capacity account applies: a derivation, or the site's own peak standing in."""
    return _CAPACITY_DERIVED if capacity_is_derived(site) else _CAPACITY_LOWER_BOUND


_NO_GRID_IMPORT = (
    "Grid import series not recorded on this run, so the battery contribution is shown against "
    "load only."
)
_NO_RESERVE = (
    "No minimum reserve commitment is recorded on this run, so no reserve rule is drawn. The "
    "dashed rule is the recorded usable capacity."
)
_PROXY_NOTE = "typical-day proxy: guides the search, never a reported figure"
_NO_COVENANT_MEASURE = "debt feasibility not measured: no debt structure on the record (B001)"

CHART_CSS = """
svg.gqfig{display:block;width:100%;height:auto;margin:6px 0 2px;
font-family:"Source Sans 3",Inter,-apple-system,system-ui,sans-serif}
svg.gqfig text{font-size:11.5px}
svg.gqfig .gqt{font-size:11px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
svg.gqfig .gqax{font-size:10.5px;letter-spacing:.04em}
svg.gqfig .gqlg{font-size:11px}
svg.gqfig .gqval{font-size:11px;font-weight:600}
svg.gqfig .gqna{font-size:12.5px;font-style:italic}
svg.gqfig .gqnode{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;font-weight:700}
.figtable{margin:6px 0 2px}
.grouplab{font-variant:small-caps;font-variant-caps:all-small-caps;letter-spacing:.11em;
font-weight:600;color:var(--neura-ink);margin:16px 0 2px;padding-bottom:4px;
border-bottom:1px solid var(--neura-rule)}
.rs{display:grid;grid-template-columns:minmax(120px,11em) 1fr minmax(84px,auto);
align-items:center;gap:4px 12px;margin:4px 0 8px}
.rsspan{grid-column:1/-1}
.rslab{font-size:13px;line-height:1.3}
.rsbar{min-width:60px}
.rsval{font-size:13px;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
svg.gqbar{display:block;width:100%;height:auto}
svg.gqswatch{display:inline-block;width:16px;height:10px;vertical-align:baseline;margin-right:5px}
.rsleg{font-size:11.5px;color:var(--neura-grey);margin:2px 0 6px}
.rskey{margin-right:16px;white-space:nowrap}
.chip{font-size:10px;letter-spacing:.06em;text-transform:uppercase;margin-left:6px;padding:1px 5px;
border:1px solid var(--neura-rule);border-radius:2px;color:var(--neura-grey);white-space:nowrap}
"""


# --- primitives ----------------------------------------------------------------------------


def _n(value: float) -> str:
    """A coordinate as a stable two-decimal string (byte-identical across renders)."""
    return f"{value:.2f}"


def _attr(name: str, value: str | None) -> str:
    return "" if value is None else f' {name}="{value}"'


def _text(
    x: float,
    y: float,
    body: str,
    *,
    cls: str = "gqax",
    fill: str = _INK,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{_n(x)}" y="{_n(y)}" class="{cls}" fill="{fill}" '
        f'text-anchor="{anchor}">{escape(body)}</text>'
    )


def _rect(
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str,
    *,
    stroke: str | None = None,
    width: float = 1.0,
    dash: str | None = None,
    rx: float = 0.0,
    opacity: str | None = None,
) -> str:
    return (
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="{_n(rx)}" '
        f'fill="{fill}"'
        + _attr("stroke", stroke)
        + (f' stroke-width="{_n(width)}"' if stroke else "")
        + _attr("stroke-dasharray", dash)
        + _attr("fill-opacity", opacity)
        + "/>"
    )


def _line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    stroke: str,
    *,
    width: float = 1.0,
    dash: str | None = None,
) -> str:
    return (
        f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" stroke="{stroke}" '
        f'stroke-width="{_n(width)}"' + _attr("stroke-dasharray", dash) + "/>"
    )


def _polyline(points: Sequence[tuple[float, float]], stroke: str, *, width: float = 1.6) -> str:
    body = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
    return (
        f'<polyline points="{body}" fill="none" stroke="{stroke}" stroke-width="{_n(width)}" '
        'stroke-linejoin="round"/>'
    )


def _polygon(
    points: Sequence[tuple[float, float]], fill: str, *, opacity: str | None = None
) -> str:
    body = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
    return f'<polygon points="{body}" fill="{fill}"' + _attr("fill-opacity", opacity) + "/>"


def _circle(cx: float, cy: float, r: float, fill: str, *, stroke: str | None = None) -> str:
    return (
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="{fill}"'
        + _attr("stroke", stroke)
        + (' stroke-width="1.4"' if stroke else "")
        + "/>"
    )


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap (SVG text does not reflow). Deterministic for one input."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _clock(period: int, interval_hours: float) -> str:
    """A clock label for a half-hour index. Pure geometry: an index, not a figure."""
    total_min = int(round(period * interval_hours * 60.0))
    if total_min >= 1440:
        return "24:00"
    hour, minute = divmod(total_min, 60)
    return f"{hour:02d}:{minute:02d}"


def capacity_is_derived(site: SiteModel) -> bool:
    """Whether the recorded MIC is a labelled `DerivedAssumption` rather than a supplied figure.

    The authoritative signal, and the one the heuristics below cannot see. A site that records
    ``connection.mic_kw`` in ``derived_assumptions`` has *said in the record* that the number was
    derived under D45 and needs confirming. Tanhouse does exactly that (its note opens "NO kVA
    FIGURE EXISTS FOR TANHOUSE in the supplied data"), and every heuristic in
    `capacity_is_supplied` passed it anyway, so the pack asserted a "recorded agreed capacity" one
    exhibit below a diagram correctly printing "ASC not recorded".
    """
    # `assumptions_requiring_confirmation`, not the raw field: the field normalises an empty list
    # back to None, so iterating it directly raises on the common case of a site with none.
    return any(d.key == "connection.mic_kw" for d in site.assumptions_requiring_confirmation)


def capacity_is_supplied(site: SiteModel) -> bool:
    """Whether the recorded MIC is a supplied agreed capacity or a stand-in for one.

    `ConnectionDetails.mic_kw` is a required field, so a site whose connection capacity was never
    supplied still carries a number: the portfolio's convention is to set it to the observed or
    modelled peak and record zero import headroom, with the site's own connection note saying so
    (three of the six live site models are in exactly this state). Treating that placeholder as a
    ceiling would draw a capacity rule the record does not carry and imply unused capacity nobody
    has evidenced, so the exhibits detect it and say what is true instead.

    A recorded `DerivedAssumption` is checked **first** and beats every heuristic: it is the
    record's own statement, and the heuristics are inferences from side effects of one particular
    convention. A derivation that does not follow that convention — Tanhouse's 500 kW, the
    smallest standard GB rating above its measured 486.35 kW peak — leaves no side effect for them
    to find, so all three passed it as supplied.
    """
    if capacity_is_derived(site):
        return False
    conn = site.connection
    if conn.asc_kw is not None:
        return True
    if conn.import_headroom_kw == 0.0:
        return False
    peak = site.load_data.max_import_kw
    return not (peak is not None and conn.mic_kw == peak)


@dataclass(frozen=True)
class _Scale:
    """A clamped linear map from a value span onto a pixel span. Geometry, never a figure."""

    lo: float
    hi: float
    px_lo: float
    px_hi: float

    def __call__(self, value: float) -> float:
        span = self.hi - self.lo
        if span <= 0.0:
            return (self.px_lo + self.px_hi) / 2.0
        clamped = min(max(value, self.lo), self.hi)
        return self.px_lo + (clamped - self.lo) / span * (self.px_hi - self.px_lo)


def _svg(height: float, alt: str, body: str) -> str:
    """Wrap chart body as one accessible, self-contained inline SVG."""
    return (
        f'<svg class="gqfig" viewBox="0 0 {_n(_W)} {_n(height)}" role="img" '
        f'aria-label="{escape(alt)}" preserveAspectRatio="xMidYMid meet">'
        f"{_rect(0.0, 0.0, _W, height, _WHITE)}{body}</svg>"
    )


def _title_band(title: str) -> str:
    """The brand figure title: small caps over a hairline."""
    return _text(0.0, 15.0, title, cls="gqt") + _line(0.0, 24.0, _W, 24.0, _INK, width=1.0)


def _legend(
    x: float, y: float, entries: Sequence[tuple[str, str]], *, width: float = 700.0
) -> tuple[str, float]:
    """A legend, wrapped to ``width``. Returns the markup and the height it occupies.

    The swatch spec is ``fill``, ``fill|stroke``, ``fill|stroke|dash``, or one of those prefixed
    ``dot:`` to draw a disc instead of a rectangle, so a legend entry for a point marker has the
    same shape as the marker it explains.
    """
    out: list[str] = []
    cursor = x
    row = 0
    row_height = 16.0
    for spec, label in entries:
        # Advance-width estimate from the label length: geometry, so a long entry wraps to the next
        # row instead of running off the figure or over the colour ramp.
        entry_width = 30.0 + len(label) * 5.9
        if cursor > x and cursor + entry_width > x + width:
            row = row + 1
            cursor = x
        row_y = y + row * row_height
        shape, _, rest = spec.partition(":")
        is_dot = shape == "dot"
        parts = (rest if is_dot else spec).split("|")
        fill = parts[0] or "none"
        stroke = parts[1] if len(parts) > 1 and parts[1] else None
        dash = parts[2] if len(parts) > 2 and parts[2] else None
        if is_dot:
            out.append(_circle(cursor + 6.0, row_y - 3.5, 4.0, fill, stroke=stroke))
        else:
            out.append(_rect(cursor, row_y - 8.0, 14.0, 10.0, fill, stroke=stroke, dash=dash))
        out.append(_text(cursor + 20.0, row_y, label, cls="gqlg", fill=_INK))
        cursor = cursor + entry_width
    return "".join(out), (row + 1) * row_height


def _notes(x: float, y: float, lines: Sequence[str], *, fill: str = _GREY) -> str:
    """Stacked caption lines beneath a plot, each already wrapped by the caller."""
    return "".join(
        _text(x, y + index * 14.0, line, cls="gqlg", fill=fill) for index, line in enumerate(lines)
    )


def unavailable_figure(title: str, reason: str, *, notes: Sequence[str] = ()) -> str:
    """The framed, reasoned *unavailable* state: an exhibit that keeps its place on the page.

    The frame is the exhibit. A missing input is a finding about the record, so it is drawn rather
    than dropped: the title and hairline hold the layout, and the reason sits in the plot area in
    the signal colour, the same seam `formatting.unavailable` opens in every table row.
    """
    reason_lines = _wrap(fmt.unavailable(reason), 96)
    note_lines = [line for note in notes for line in _wrap(note, 108)]
    box_top = 36.0
    box_height = 34.0 + len(reason_lines) * 17.0
    height = box_top + box_height + 12.0 + len(note_lines) * 14.0 + 8.0
    body = [
        _title_band(title),
        _rect(0.0, box_top, _W, box_height, _CREAM_LIGHT, stroke=_RULE),
        *[
            _text(
                _W / 2.0,
                box_top + 26.0 + index * 17.0,
                line,
                cls="gqna",
                fill=_SIGNAL,
                anchor="middle",
            )
            for index, line in enumerate(reason_lines)
        ],
        _notes(0.0, box_top + box_height + 20.0, note_lines),
    ]
    return _svg(height, f"{title}. {fmt.unavailable(reason)}", "".join(body))


# --- exhibit: the site as connected ---------------------------------------------------------


def _chip(x: float, y: float, label: str, value: str, *, strong: bool) -> str:
    """One capacity chip: a recorded value, or the fixed 'not recorded' string."""
    fill = _CREAM if strong else _WHITE
    return "".join(
        [
            _rect(
                x,
                y,
                150.0,
                30.0,
                fill,
                stroke=_RULE if strong else _DASHED,
                dash=None if strong else "3 3",
            ),
            _text(x + 8.0, y + 12.0, label, cls="gqax", fill=_GREY),
            _text(x + 8.0, y + 24.0, value, cls="gqval", fill=_INK if strong else _GREY),
        ]
    )


def system_diagram(
    *,
    site: SiteModel | None,
    spec: SystemSpecView,
    export_stream_on_record: bool,
) -> str:
    """The physical arrangement and metering boundary being underwritten (brand block diagram).

    Topology and boundary answer in one glance what a table cannot: grid, the recorded capacity
    chips, the import meter and its MPAN, the dashed metering perimeter, the site load and the
    battery block. Every capacity is a recorded value or the fixed "not recorded" chip; the export
    arrow is solid only on a recorded basis, and dashed with its own label otherwise.
    """
    title = "THE SITE AS CONNECTED"
    if site is None:
        return unavailable_figure(title, _NO_SITE_CHART)

    conn = site.connection
    boundary = site.supply_boundary
    load = site.load_data
    export = site.export_arrangement
    route_recorded = export is not None and export.route.value != "UNKNOWN"
    export_basis = conn.mec_kw is not None or route_recorded or export_stream_on_record

    height = 356.0
    grid_x, meter_x = 16.0, 214.0
    peri_x, peri_w = 356.0, 528.0
    bus_x = 424.0
    body: list[str] = [_title_band(title)]

    # The behind-the-meter perimeter, drawn first so every node sits on top of its tint.
    behind = boundary.behind_the_meter
    peri_label = "Behind the meter" if behind is True else "Boundary unconfirmed"
    if behind is False:
        peri_label = "Not behind the meter, as recorded"
    body.append(_rect(peri_x, 64.0, peri_w, 250.0, _CREAM_LIGHT, stroke=_DASHED, dash="6 4"))
    body.append(_text(peri_x + 12.0, 82.0, peri_label, cls="gqax", fill=_GREY))

    # Grid node and the connection bar carrying the recorded capacity chips.
    body.append(_rect(grid_x, 118.0, 104.0, 70.0, _WHITE, stroke=_INK, width=1.6))
    body.append(_text(grid_x + 52.0, 157.0, "GRID", cls="gqnode", anchor="middle"))
    # The connection's own descriptors sit under the node, left-aligned: a long "not recorded"
    # fallback centred on a 104-unit box would run off the left edge of the figure.
    region = conn.dno_region or "DNO region not recorded"
    body.append(_text(grid_x, 204.0, region, cls="gqax", fill=_GREY))
    body.append(_line(grid_x + 104.0, 153.0, meter_x, 153.0, _INK, width=3.0))
    supplied = capacity_is_supplied(site)
    body.append(
        _chip(
            grid_x + 8.0,
            232.0,
            "MIC" if supplied else "MIC (lower bound)",
            fmt.kw(conn.mic_kw),
            strong=supplied,
        )
    )
    asc = conn.asc_kw
    body.append(
        _chip(
            grid_x + 8.0,
            268.0,
            "ASC",
            fmt.kw(asc) if asc is not None else "not recorded",
            strong=asc is not None,
        )
    )
    mec = conn.mec_kw
    body.append(
        _chip(
            grid_x + 8.0,
            304.0,
            "MEC",
            fmt.kw(mec) if mec is not None else "not recorded",
            strong=mec is not None,
        )
    )
    voltage = conn.voltage_level
    body.append(
        _text(
            grid_x,
            218.0,
            voltage.value if voltage is not None else "voltage level not recorded",
            cls="gqax",
            fill=_GREY,
        )
    )

    # Import meter node and its MPAN.
    body.append(_rect(meter_x, 118.0, 104.0, 70.0, _PEACH, stroke=_INK, width=1.4))
    body.append(_text(meter_x + 52.0, 148.0, "METER", cls="gqnode", anchor="middle"))
    mpan = boundary.import_mpan
    body.append(
        _text(
            meter_x + 52.0,
            204.0,
            f"MPAN {mpan}" if mpan else "MPAN not recorded",
            cls="gqax",
            fill=_GREY,
            anchor="middle",
        )
    )
    point = boundary.metering_point
    if point:
        body.append(_text(meter_x + 52.0, 218.0, point, cls="gqax", fill=_GREY, anchor="middle"))
    body.append(_line(meter_x + 104.0, 153.0, bus_x, 153.0, _INK, width=3.0))

    # Site busbar, load block, battery block.
    body.append(_line(bus_x, 110.0, bus_x, 296.0, _INK, width=3.0))
    body.append(_line(bus_x, 132.0, 470.0, 132.0, _INK, width=1.6))
    body.append(_line(bus_x, 258.0, 470.0, 258.0, _INK, width=1.6))
    body.append(_rect(470.0, 104.0, 200.0, 74.0, _WHITE, stroke=_INK, width=1.4))
    body.append(_text(486.0, 128.0, "SITE LOAD", cls="gqnode"))
    annual = load.annual_kwh
    body.append(
        _text(
            486.0,
            146.0,
            fmt.kwh(annual) if annual is not None else "annual consumption not recorded",
            cls="gqval",
            fill=_INK if annual is not None else _GREY,
        )
    )
    peak = load.max_import_kw
    body.append(
        _text(
            486.0,
            164.0,
            f"peak import {fmt.kw(peak)}" if peak is not None else "peak import not recorded",
            cls="gqax",
            fill=_GREY,
        )
    )

    sized = spec.selected_power_kw is not None and spec.selected_energy_kwh is not None
    if sized and spec.selected_power_kw is not None and spec.selected_energy_kwh is not None:
        body.append(_rect(470.0, 224.0, 200.0, 76.0, _ORANGE, rx=2.0))
        body.append(_text(486.0, 248.0, "BATTERY", cls="gqnode", fill=_WHITE))
        body.append(
            _text(
                486.0,
                266.0,
                f"{fmt.kw(spec.selected_power_kw)} / {fmt.kwh(spec.selected_energy_kwh)}",
                cls="gqval",
                fill=_WHITE,
            )
        )
        duration = spec.duration_hours
        body.append(
            _text(
                486.0,
                284.0,
                fmt.hours(duration) if duration is not None else "duration not recorded",
                cls="gqax",
                fill=_WHITE,
            )
        )
    else:
        body.append(_rect(470.0, 224.0, 200.0, 76.0, _WHITE, stroke=_DASHED, dash="5 4", rx=2.0))
        body.append(_text(486.0, 248.0, "BATTERY", cls="gqnode", fill=_GREY))
        for index, line in enumerate(_wrap("no system selected on this record", 26)):
            body.append(_text(486.0, 266.0 + index * 14.0, line, cls="gqax", fill=_GREY))

    # Export path: shown, but its state is recorded rather than assumed.
    arrow_colour = _ORANGE if export_basis else _DASHED
    arrow_dash = None if export_basis else "5 4"
    body.append(_line(bus_x, 96.0, bus_x, 110.0, arrow_colour, width=1.8, dash=arrow_dash))
    body.append(_line(grid_x + 52.0, 96.0, bus_x, 96.0, arrow_colour, width=1.8, dash=arrow_dash))
    body.append(
        _line(grid_x + 52.0, 96.0, grid_x + 52.0, 118.0, arrow_colour, width=1.8, dash=arrow_dash)
    )
    body.append(
        _polygon(
            [
                (grid_x + 52.0, 118.0),
                (grid_x + 46.0, 106.0),
                (grid_x + 58.0, 106.0),
            ],
            arrow_colour,
        )
    )
    if export_basis:
        route = export.route.value if route_recorded and export is not None else None
        counterparty = export.counterparty if export is not None else None
        label = "Export path" if route is None else f"Export path: {fmt.titleise(route)}"
        if counterparty:
            label = f"{label}, {counterparty}"
        body.append(_text(bus_x - 12.0, 90.0, label, cls="gqax", fill=_ORANGE, anchor="end"))
    else:
        body.append(
            _text(
                bus_x - 12.0,
                90.0,
                "export path: no arrangement recorded",
                cls="gqax",
                fill=_GREY,
                anchor="end",
            )
        )

    notes: list[str] = []
    if not supplied:
        notes.extend(_wrap(_capacity_note(site), 132))
    if conn.anm_or_curtailment_notes:
        notes.extend(_wrap(f"Connection note: {conn.anm_or_curtailment_notes}", 132))
    if behind is None:
        notes.extend(
            _wrap(
                "The behind-the-meter flag is unset on the site record, so the perimeter is drawn "
                "as unconfirmed rather than assumed.",
                132,
            )
        )
    body.append(_notes(0.0, 352.0, notes))
    height = 352.0 + len(notes) * 14.0 + 8.0

    alt = (
        f"Site as connected: MIC {fmt.kw(conn.mic_kw)}, {peri_label.lower()}, "
        f"battery {'sized on the record' if sized else 'not selected on this record'}."
    )
    return _svg(height, alt, "".join(body))


# --- exhibit: the shape of the load --------------------------------------------------------


def load_duration_curve(*, site: SiteModel | None) -> str:
    """The load-duration curve, in the page-1 brand idiom. Gated on a persisted projection (E-2).

    The curve answers how much of the year the site actually uses its connection, and it is the
    most brand-native exhibit there is. It cannot be drawn honestly today: the full-year
    half-hourly series reaches the record only as a file reference, and no percentile projection of
    it is persisted. Computing one here would build a year series inside the report layer, so the
    exhibit states the missing input instead. When the projection is persisted the curve, its cream
    fill and the hatched available-capacity region between the curve and the connection rule are
    drawn from it.
    """
    title = "THE SHAPE OF THE LOAD"
    if site is None:
        return unavailable_figure(title, _NO_SITE_CHART)
    load = site.load_data
    notes: list[str] = []
    basis = load.half_hourly_basis
    if basis == "metered":
        notes.append("Basis on the site record: metered half-hourly data.")
    elif basis is not None:
        profile = load.half_hourly_reference_profile or "reference profile not named"
        scaled = load.half_hourly_scaled_to or "scaling basis not named"
        notes.append(f"Basis on the site record: {basis}, from {profile}, scaled to {scaled}.")
    else:
        notes.append("The site record does not state how the half-hourly series was obtained.")
    peak = load.max_import_kw
    if peak is not None:
        notes.append(f"Recorded peak import: {fmt.kw(peak)}.")
    if capacity_is_supplied(site):
        notes.append(
            f"Recorded connection capacity: {fmt.kw(site.connection.mic_kw)}. The hatched "
            "available-capacity region needs the curve, so neither is drawn."
        )
    else:
        notes.extend(_wrap(_capacity_note(site), 128))
    return unavailable_figure(title, _NO_LOAD_DURATION, notes=notes)


# --- exhibit: how the battery firms the connection ------------------------------------------


def _mode_shares(profile: DispatchProfile) -> list[str]:
    """How the battery spent the persisted day, counted off it (as `utilisation_island` counts).

    Reported as counts of recorded half-hours rather than as shares. A share would be a percentage
    this module divided into existence, and the count answers the same question without authoring a
    figure: the reader can see the denominator.
    """
    points = profile.points
    total = len(points)
    if not total:
        return []
    counted = (
        ("Charging", sum(1 for p in points if p.charge_kw > 0.0)),
        (
            "Discharging in the DUoS red band",
            sum(1 for p in points if p.discharge_kw > 0.0 and p.red_band),
        ),
        (
            "Discharging off-peak",
            sum(1 for p in points if p.discharge_kw > 0.0 and not p.red_band),
        ),
        ("Idle", sum(1 for p in points if p.charge_kw <= 0.0 and p.discharge_kw <= 0.0)),
    )
    return [f"{label} {count} of {total} half-hours" for label, count in counted if count > 0]


def firming_diagram(
    *,
    profile: DispatchProfile | None,
    site: SiteModel | None,
    solver_note: str | None = None,
) -> str:
    """How the battery firms the connection, from the real solved representative day.

    Two stacked panels on a shared 00:00 to 24:00 clock axis, in the page-2 brand composition. The
    top panel carries the recorded site load over its cream fill, the solved discharge as the solid
    orange block that covers it, and the solved charge as the peach recharge shoulders, against the
    flat recorded capacity rules. The bottom panel carries the recorded state-of-energy trace in
    kWh against the recorded usable capacity.

    Two honesty rules the geometry obeys. No record carries a half-hourly available-import series,
    so capacity is drawn as the flat recorded rule and never as the reference figure's dipping
    curve. No record carries a grid-import series either, so the "from the grid" area is not drawn
    and is not derived from load, discharge and charge: the panel says so in frame.
    """
    title = "HOW THE BATTERY FIRMS THE CONNECTION"
    if profile is None or not profile.points:
        extra = [] if solver_note is None else _wrap(solver_note, 128)
        return unavailable_figure(title, _NO_PROFILE, notes=extra)

    points = profile.points
    interval = profile.interval_hours
    conn = None if site is None else site.connection
    # A MIC that is really the site's own peak is not a ceiling, so no rule is drawn from it.
    supplied = site is not None and capacity_is_supplied(site)
    mic = conn.mic_kw if conn is not None and supplied else None
    asc = None if conn is None else conn.asc_kw

    plot_x, plot_r = 84.0, 700.0
    top_y, top_b = 62.0, 250.0
    soc_y, soc_b = 306.0, 412.0

    # The kW span is the largest recorded value on the panel: a record value, chosen not computed.
    kw_candidates = [p.site_load_kw for p in points]
    kw_candidates.extend(p.discharge_kw for p in points)
    kw_candidates.extend(p.charge_kw for p in points)
    if mic is not None:
        kw_candidates.append(mic)
    if asc is not None:
        kw_candidates.append(asc)
    kw_top = max(kw_candidates)
    kw_scale = _Scale(0.0, kw_top, top_b, top_y)

    soe_candidates = [p.soe_kwh for p in points]
    soe_candidates.append(profile.energy_kwh)
    soe_top = max(soe_candidates)
    soe_scale = _Scale(0.0, soe_top, soc_b, soc_y)

    span = plot_r - plot_x
    step = span / float(len(points))

    def x_of(index: int) -> float:
        return plot_x + index * step

    body: list[str] = [_title_band(title)]

    # Panel grounds, then the recorded DUoS red-band half-hours as light full-height bands behind
    # every series (the brand's shaded-band device; the label sits above the plot, clear of the
    # right margin where the recorded capacity rules are named).
    body.append(_rect(plot_x, top_y, span, top_b - top_y, _WHITE, stroke=_RULE))
    body.append(_rect(plot_x, soc_y, span, soc_b - soc_y, _WHITE, stroke=_RULE))
    red_periods = [index for index, p in enumerate(points) if p.red_band]
    if red_periods:
        for index in red_periods:
            body.append(_rect(x_of(index), top_y, step, top_b - top_y, _CREAM_LIGHT))
            body.append(_rect(x_of(index), soc_y, step, soc_b - soc_y, _CREAM_LIGHT))
        first, last = red_periods[0], red_periods[-1]
        bracket_y = top_y - 10.0
        body.append(_line(x_of(first), bracket_y, x_of(last) + step, bracket_y, _DASHED))
        body.append(
            _text(
                (x_of(first) + x_of(last) + step) / 2.0,
                bracket_y - 5.0,
                "DUoS red band",
                cls="gqax",
                fill=_GREY,
                anchor="middle",
            )
        )

    # Stepped series: one flat segment per recorded half-hour (never interpolated between them).
    def steps(values: Sequence[float], scale: _Scale) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for index, value in enumerate(values):
            y = scale(value)
            out.append((x_of(index), y))
            out.append((x_of(index) + step, y))
        return out

    load_steps = steps([p.site_load_kw for p in points], kw_scale)
    charge_steps = steps([p.charge_kw for p in points], kw_scale)
    discharge_steps = steps([p.discharge_kw for p in points], kw_scale)
    base = kw_scale(0.0)
    body.append(_polygon([(plot_x, base), *load_steps, (plot_r, base)], _CREAM))
    body.append(_polygon([(plot_x, base), *charge_steps, (plot_r, base)], _PEACH))
    body.append(_polygon([(plot_x, base), *discharge_steps, (plot_r, base)], _ORANGE))
    body.append(_polyline(load_steps, _INK, width=1.6))

    # Capacity rules: flat, recorded, labelled with the recorded value.
    if mic is not None:
        y = kw_scale(mic)
        body.append(_line(plot_x, y, plot_r, y, _INK, width=1.4))
        body.append(_text(plot_r + 10.0, y - 4.0, "MIC", cls="gqax", fill=_INK))
        body.append(_text(plot_r + 10.0, y + 10.0, fmt.kw(mic), cls="gqval", fill=_INK))
    if asc is not None:
        y = kw_scale(asc)
        body.append(_line(plot_x, y, plot_r, y, _DASHED, width=1.2, dash="6 4"))
        body.append(_text(plot_r + 10.0, y - 4.0, "ASC", cls="gqax", fill=_GREY))
        body.append(_text(plot_r + 10.0, y + 10.0, fmt.kw(asc), cls="gqval", fill=_GREY))

    # Declared provisioning windows: the recorded import cap inside the recorded windows.
    caps: list[str] = []
    requirements = [] if site is None else (site.provisioning_requirements or [])
    for requirement in requirements:
        cap = requirement.import_cap_kw
        if cap is None:
            continue
        y = kw_scale(cap)
        body.append(_line(plot_x, y, plot_r, y, _ORANGE_MID, width=1.4, dash="2 3"))
        body.append(
            _text(plot_r + 10.0, y + 4.0, f"cap {fmt.kw(cap)}", cls="gqval", fill=_ORANGE_MID)
        )
        caps.append(
            f"Declared obligation {requirement.requirement_id}: window import cap "
            f"{fmt.kw(cap)} over {len(requirement.windows)} recorded window(s)."
        )

    # Axes. kW/kWh ticks are recorded extremes; the origin and clock labels are pure geometry.
    body.append(_text(plot_x - 10.0, top_y + 4.0, fmt.kw(kw_top), cls="gqval", anchor="end"))
    body.append(_text(plot_x - 10.0, top_b, "0", cls="gqax", fill=_GREY, anchor="end"))
    body.append(_text(0.0, top_y - 12.0, "SITE POWER (kW)", cls="gqax", fill=_GREY))
    body.append(_text(plot_x - 10.0, soc_y + 4.0, fmt.kwh(soe_top), cls="gqval", anchor="end"))
    body.append(_text(plot_x - 10.0, soc_b, "0", cls="gqax", fill=_GREY, anchor="end"))
    body.append(_text(0.0, soc_y - 12.0, "STATE OF ENERGY (kWh)", cls="gqax", fill=_GREY))

    # State of energy: the recorded trace against the recorded usable capacity.
    soe_steps = steps([p.soe_kwh for p in points], soe_scale)
    body.append(_polyline(soe_steps, _ORANGE, width=1.8))
    usable_y = soe_scale(profile.energy_kwh)
    body.append(_line(plot_x, usable_y, plot_r, usable_y, _DASHED, width=1.2, dash="6 4"))
    body.append(_text(plot_r + 10.0, usable_y - 4.0, "Usable capacity", cls="gqax", fill=_GREY))
    body.append(
        _text(plot_r + 10.0, usable_y + 10.0, fmt.kwh(profile.energy_kwh), cls="gqval", fill=_GREY)
    )

    # Shared clock axis, drawn once under the state-of-energy panel.
    body.append(_line(plot_x, soc_b, plot_r, soc_b, _INK, width=1.0))
    tick_every = max(1, len(points) // 6)
    for index in range(0, len(points), tick_every):
        x = x_of(index)
        body.append(_line(x, soc_b, x, soc_b + 4.0, _RULE, width=1.0))
        body.append(
            _text(x, soc_b + 16.0, _clock(index, interval), cls="gqax", fill=_GREY, anchor="middle")
        )
    body.append(
        _text(
            plot_r,
            soc_b + 16.0,
            _clock(len(points), interval),
            cls="gqax",
            fill=_GREY,
            anchor="middle",
        )
    )

    legend_y = soc_b + 40.0
    legend_markup, legend_height = _legend(
        plot_x,
        legend_y,
        [
            (f"{_CREAM}|{_INK}", "Site load"),
            (_ORANGE, "From the battery"),
            (_PEACH, "Recharge"),
            (f"{_CREAM_LIGHT}|{_RULE}", "DUoS red band"),
            (f"none|{_DASHED}|6 4", "Recorded reference"),
        ],
        width=plot_r - plot_x,
    )
    body.append(legend_markup)

    lines: list[str] = []
    lines.extend(_wrap(_NO_GRID_IMPORT, 132))
    if site is None:
        lines.extend(_wrap(_NO_CAPACITY, 132))
    elif not supplied:
        lines.extend(_wrap(_capacity_note(site), 132))
    lines.extend(_wrap(_NO_RESERVE, 132))
    lines.append(
        f"Representative day ({profile.selection_basis}), a real solved day of the metered "
        "horizon. Not an average over days."
    )
    shares = _mode_shares(profile)
    if shares:
        lines.extend(_wrap(f"Mode shares on this day: {'; '.join(shares)}.", 132))
    lines.extend(caps)
    notes_y = legend_y + legend_height + 12.0
    body.append(_notes(0.0, notes_y, lines))
    height = notes_y + len(lines) * 14.0 + 8.0

    alt = (
        f"Solved representative day: peak site load {fmt.kw(max(p.site_load_kw for p in points))}, "
        f"usable capacity {fmt.kwh(profile.energy_kwh)}, "
        f"{'connection capacity unknown' if mic is None else f'MIC {fmt.kw(mic)}'}."
    )
    return _svg(height, alt, "".join(body))


# --- exhibit: the sizing map ----------------------------------------------------------------

#: Search stages whose points form the regular lattice the cell map draws. Refine and confirm
#: evaluations of a continuous search land between cells and render as markers at their true
#: coordinates (the rule `gridq.app.view` established for the same data).
_LATTICE_STAGES = frozenset({"grid", "coarse"})
#: When one cell was evaluated twice, a full-series value beats a typical-day proxy, then the later
#: stage wins (D32). Never let a proxy value displace a confirmed one.
_STAGE_RANK = {"grid": 0, "coarse": 0, "refine": 1, "confirm": 2}

#: A single-hue ramp, peach to deep orange: colour-blind-safe by lightness alone.
_RAMP: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (245, 201, 168)),
    (0.45, (239, 139, 78)),
    (0.8, (226, 85, 31)),
    (1.0, (150, 48, 12)),
)
_CREAM_RGB = (234, 231, 225)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def _ramp_rgb(t: float) -> tuple[int, int, int]:
    clamped = min(max(t, 0.0), 1.0)
    for index in range(1, len(_RAMP)):
        lo_stop, lo_rgb = _RAMP[index - 1]
        hi_stop, hi_rgb = _RAMP[index]
        if clamped <= hi_stop:
            span = hi_stop - lo_stop
            local = 0.0 if span <= 0.0 else (clamped - lo_stop) / span
            return _mix(lo_rgb, hi_rgb, local)
    return _RAMP[-1][1]


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _readable(rgb: tuple[int, int, int]) -> str:
    """Ink or white text over a ramp cell, by luminance. Geometry, not a judgement."""
    luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return _INK if luminance > 150.0 else _WHITE


def _cell_rank(point: SizingGridPoint) -> tuple[int, int]:
    return (0 if point.proxy else 1, _STAGE_RANK.get(point.stage, 0))


def _axis_position(value: float, values: Sequence[float], centres: Sequence[float]) -> float:
    """Where an off-lattice evaluation sits on an ordinal axis. Interpolates position, not value."""
    if not values:
        return centres[0] if centres else 0.0
    if value <= values[0]:
        return centres[0]
    if value >= values[-1]:
        return centres[-1]
    for index in range(1, len(values)):
        if value <= values[index]:
            lo, hi = values[index - 1], values[index]
            span = hi - lo
            local = 0.0 if span <= 0.0 else (value - lo) / span
            return centres[index - 1] + (centres[index] - centres[index - 1]) * local
    return centres[-1]


#: How much display precision the evaluated-points table may spend to make its rows reconcile.
#: Deep enough for a derived duration (energy over power lands on values like 0.5283 h) against a
#: large power, shallow enough that the column stays readable.
_MAX_POWER_DP = 3
_MAX_DURATION_DP = 4
#: Precision pairs in increasing cost, so the table spends the fewest decimals that work: total
#: decimals first, then power decimals (a duration is the smaller number, so decimals are cheaper
#: there). The ladder starts at (1, 1), which is what `fmt.kw` / `fmt.hours` already print, so a
#: table whose values are exact keeps exactly the precision it has today.
_PRECISION_LADDER: tuple[tuple[int, int], ...] = tuple(
    sorted(
        (
            (power_dp, duration_dp)
            for power_dp in range(1, _MAX_POWER_DP + 1)
            for duration_dp in range(1, _MAX_DURATION_DP + 1)
        ),
        key=lambda pair: (pair[0] + pair[1], pair[0]),
    )
)
_NO_DEBT_MEASURE_COLUMNS = (
    "No evaluated point carries a P90 DSCR or a covenant verdict, so neither column is drawn: "
    f"{_NO_COVENANT_MEASURE}. A column of repeated absences is not evidence, and reading it as one "
    "would be worse than reading this sentence."
)
_RECONCILES_NOTE = (
    "The three columns are shown to the precision at which power times duration reproduces the "
    "recorded energy on every row. The record derives energy from its own power and duration, so "
    "the row is one figure seen three ways and it is displayed as one."
)
_ROUNDED_INDEPENDENTLY = (
    "Each column is the record's own value, rounded on its own, and on some rows the product of "
    "the power and duration shown differs from the energy shown in the last place displayed. The "
    "energy column is the recorded figure; the difference is display rounding, not a discrepancy "
    "in the record."
)
_FULL_SERIES_GROUP = "Evaluated on the full series"
_PROXY_GROUP = "Evaluated on a typical-day proxy"


@dataclass(frozen=True)
class _Precision:
    """Decimal places for the three columns, and whether they hold the two rules.

    ``reconciles``: on every row, the product of the power and duration as displayed reproduces the
    displayed energy. ``distinguishes``: no two rows show the same power and duration while showing
    different energies, which would read as the same candidate evaluated twice to different answers.

    Exactness is not always reachable: a true product can sit arbitrarily close to a display
    boundary, where any rounding of the two factors tips the product across it. The ladder ends at
    its deepest rung, so the residual is a last-place difference rather than a visible error, and
    the table says which of the two states it is in instead of pretending otherwise.
    """

    power_dp: int
    duration_dp: int
    energy_dp: int
    reconciles: bool
    distinguishes: bool


#: One row's arithmetic claim: ``(power_kw, duration_hours, energy_kwh)`` from the record.
_Triple = tuple[float, float, float]
#: Energy precisions to try, finest first: a tenth of a kWh, then whole kWh. A coarser energy column
#: widens the tolerance its two factors have to hit, so it can buy exactness that more decimals on
#: the factors cannot, and whole kWh costs a reader of candidate sizes nothing.
_ENERGY_DP = (1, 0)


def _row_reconciles(triple: _Triple, power_dp: int, duration_dp: int, energy_dp: int) -> bool:
    """The calculator check, run on the strings the reader will actually see."""
    power, duration, energy = triple
    shown_power = float(f"{power:.{power_dp}f}")
    shown_duration = float(f"{duration:.{duration_dp}f}")
    return fmt.kwh_at(shown_power * shown_duration, energy_dp) == fmt.kwh_at(energy, energy_dp)


def _rows_are_distinguishable(
    triples: Sequence[_Triple], power_dp: int, duration_dp: int, energy_dp: int
) -> bool:
    """Two rows may share a displayed size only if they also share a displayed energy."""
    seen: dict[tuple[str, str], str] = {}
    for power, duration, energy in triples:
        key = (fmt.kw_at(power, power_dp), fmt.hours_at(duration, duration_dp))
        shown = fmt.kwh_at(energy, energy_dp)
        if seen.setdefault(key, shown) != shown:
            return False
    return True


def _precision_for(triples: Sequence[_Triple]) -> _Precision:
    """The cheapest precision on which every row reconciles and no two rows collide.

    Precision is chosen from the record's values, not applied uniformly: a table of exact sizes
    keeps the 1 dp it has today, and only a table with derived durations pays for more decimals.
    """
    for power_dp, duration_dp in _PRECISION_LADDER:
        for energy_dp in _ENERGY_DP:
            if all(
                _row_reconciles(triple, power_dp, duration_dp, energy_dp) for triple in triples
            ) and _rows_are_distinguishable(triples, power_dp, duration_dp, energy_dp):
                return _Precision(
                    power_dp, duration_dp, energy_dp, reconciles=True, distinguishes=True
                )
    power_dp, duration_dp = _PRECISION_LADDER[-1]
    return _Precision(
        power_dp,
        duration_dp,
        _ENERGY_DP[0],
        reconciles=all(
            _row_reconciles(triple, power_dp, duration_dp, _ENERGY_DP[0]) for triple in triples
        ),
        distinguishes=_rows_are_distinguishable(triples, power_dp, duration_dp, _ENERGY_DP[0]),
    )


def spec_triple(
    power_kw: float | None, energy_kwh: float | None, duration_hours: float | None
) -> tuple[str | None, str | None, str | None]:
    """The selected system's power, energy and duration as three strings that multiply.

    The spec panel states the same arithmetic claim the evaluated-points table states, one row per
    figure instead of one column, so it is held to the same rule: the precision is raised until the
    power and duration on the page reproduce the energy on the page. Absent figures stay ``None``
    for the caller to label with its own reason.
    """
    if power_kw is None or energy_kwh is None or duration_hours is None:
        return (
            None if power_kw is None else fmt.kw(power_kw),
            None if energy_kwh is None else fmt.kwh(energy_kwh),
            None if duration_hours is None else fmt.hours(duration_hours),
        )
    precision = _precision_for([(power_kw, duration_hours, energy_kwh)])
    return (
        fmt.kw_at(power_kw, precision.power_dp),
        fmt.kwh_at(energy_kwh, precision.energy_dp),
        fmt.hours_at(duration_hours, precision.duration_dp),
    )


#: The identity of one evaluated point: its coordinates AND which solve produced it.
_PointKey = tuple[float, float, float, str, bool]


def _point_key(point: SizingGridPoint) -> _PointKey:
    """One evaluated point's identity, stage included.

    **The stage is load-bearing, not decoration.** A two-stage search scores a coarse candidate on a
    typical-day proxy and then re-solves the SAME power and duration on the full series, so a
    coordinate pair alone matches two rows: the proxy score and the confirmed solve. Marking both
    SELECTED would tell a reader the search selected a proxy evaluation, which it never does.
    """
    return (point.power_kw, point.duration_hours, point.energy_kwh, point.stage, point.proxy)


def _selected_key(grid: SizingGrid) -> _PointKey | None:
    """The selected point's identity, or ``None`` where the grid marks none.

    A value key rather than object identity: the record round-trips through JSON, so the selected
    point and its twin in ``points`` are equal but not the same object.
    """
    selected = grid.selected
    return None if selected is None else _point_key(selected)


def _return_cell(point: SizingGridPoint, *, levered: bool) -> str:
    """The evaluated return for one point, or a reasoned absence. Never a zero standing in."""
    value = point.levered_return_pct if levered else point.project_irr_pct
    if value is None:
        return escape(fmt.unavailable("no return on this evaluation"))
    return escape(fmt.pct(value))


def _sizing_rows(
    points: Sequence[SizingGridPoint],
    precision: _Precision,
    *,
    with_debt: bool,
    levered: bool,
    selected: _PointKey | None,
) -> str:
    """One group's rows. The debt columns are drawn only where some row carries a debt measure."""
    rows: list[str] = []
    for point in points:
        cells = [
            escape(fmt.kw_at(point.power_kw, precision.power_dp)),
            escape(fmt.hours_at(point.duration_hours, precision.duration_dp)),
            escape(fmt.kwh_at(point.energy_kwh, precision.energy_dp)),
            _return_cell(point, levered=levered),
        ]
        if with_debt:
            cells.append(
                escape(fmt.ratio(point.min_p90_dscr))
                if point.min_p90_dscr is not None
                else escape(fmt.unavailable("no P90 DSCR on this evaluation"))
            )
            cells.append(
                escape("not measured")
                if point.passes_covenant is None
                else escape(fmt.pass_fail(point.passes_covenant))
            )
        cells.append(escape(point.stage))
        cells.append(
            _SELECTED_MARK if selected is not None and _point_key(point) == selected else ""
        )
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return "".join(rows)


def _sizing_group(
    label: str,
    lead: str,
    points: Sequence[SizingGridPoint],
    precision: _Precision,
    *,
    with_debt: bool,
    levered: bool,
    z_label: str,
    selected: _PointKey | None,
) -> str:
    """A titled block of evaluated points, its count taken from the rows it holds."""
    head = ["Power", "Duration", "Energy", z_label]
    if with_debt:
        head.extend(["Min P90 DSCR", "Covenant"])
    head.extend(["Stage", "Selected"])
    head_html = "".join(f"<th>{escape(column)}</th>" for column in head)
    heading = f"{label}: {fmt.count(len(points))}"
    return (
        f'<div class="grouplab">{escape(heading)}</div>'
        f'<p class="rownote">{escape(lead)}</p>'
        f'<div class="scroll"><table class="grid"><tr>{head_html}</tr>'
        f"{_sizing_rows(points, precision, with_debt=with_debt, levered=levered, selected=selected)}"  # noqa: E501
        "</table></div>"
    )


#: Printed in the Selected column of the one row the record marks selected. A word rather than a
#: glyph: the table is read in a browser, in print, and by a screen reader, and only a word survives
#: all three.
_SELECTED_MARK = "SELECTED"

#: Said above the surface table, every time it is drawn. The owner asked for the FULL sizing surface
#: rather than a summary triple, and the sentence states what "full" means here so a reader can
#: check
#: the claim against the row count rather than trusting it.
SIZING_SURFACE_LEAD = (
    "Every candidate size the search evaluated, one row per real solve. This is the whole searched "
    "space and not a selection from it: the row count equals the number of evaluations on the "
    "record. Nothing is interpolated between rows, so a power and duration with no row here was "
    "never solved and no figure is claimed for it."
)
_SIZING_SURFACE_TITLE = "THE FULL SIZING SURFACE, EVERY EVALUATED CANDIDATE"
_NO_SURFACE = (
    "The run record carries no evaluated sizing grid, so the searched space cannot be shown. That "
    "is "
    "an absent record, not an empty search."
)


def _z_is_levered(points: Sequence[SizingGridPoint]) -> bool:
    """Whether any evaluated point carries a levered return, which decides the objective shown.

    The same test `sizing_map` applies, factored out so the map and the table can never label two
    different objectives as one.
    """
    return any(point.levered_return_pct is not None for point in points)


def _z_label(*, levered: bool) -> str:
    return (
        "Levered (equity) return per evaluated cell"
        if levered
        else "Project return per evaluated cell, unlevered"
    )


def _sizing_table(grid: SizingGrid, z_label: str) -> str:
    """The evaluated points as a table: the FULL searched space, always available.

    Four things this table must not do, each of which it did. It must not state a product that does
    not multiply, so the power and duration columns are shown at the precision on which every row
    reconciles against its own recorded energy. It must not present a typical-day proxy score and
    a full-series solve as the same kind of evidence, so the two are separate blocks with the count
    in each heading. It must not carry a column of repeated absences: where no evaluated point
    carries a debt measure, the two debt columns are replaced by one sentence saying so. And it must
    not leave a reader to find the selected size by eye: the row the record marks selected says so.

    **And it carries no per-candidate pound, deliberately.** A revenue column was drafted and then
    removed: a candidate's own priced-floor figure is a figure two standing invariants forbid a pack
    from quoting. A PROXY candidate's revenue reads high and must never appear as a measurement
    (`test_best_candidate_reaches_the_pack::test_a_proxy_candidate_is_never_quoted_as_a_measurement`),
    and a candidate figure must not sit beside a real selected spec, where it reads as a competing
    result (`::test_a_run_that_selected_a_system_carries_no_candidate_block`). The surface's job is
    the space the search covered and the objective it ranked on; the per-candidate pound belongs to
    the best-candidate exhibit, which renders only on a run that selected nothing.

    Once the honest fallback for a grid whose points carry no return, this is now rendered on every
    pack beside the map (`sizing_surface`), on the owner's instruction that the sizing surface be
    legibly full rather than summarised. The two views answer different questions from the same
    rows: the map shows the shape of the space, the table states every figure in it.
    """
    points = grid.points
    precision = _precision_for(
        [(point.power_kw, point.duration_hours, point.energy_kwh) for point in points]
    )
    with_debt = any(
        point.min_p90_dscr is not None or point.passes_covenant is not None for point in points
    )
    levered = _z_is_levered(points)
    selected = _selected_key(grid)
    full_series = [point for point in points if not point.proxy]
    proxied = [point for point in points if point.proxy]

    blocks: list[str] = []
    if full_series:
        blocks.append(
            _sizing_group(
                _FULL_SERIES_GROUP,
                "Solved on the half-hourly series the run used. These are the reportable "
                "evaluations.",
                full_series,
                precision,
                with_debt=with_debt,
                levered=levered,
                z_label=z_label,
                selected=selected,
            )
        )
    if proxied:
        blocks.append(
            _sizing_group(
                _PROXY_GROUP,
                f"Scored on a typical day to steer the search: {_PROXY_NOTE}. Held apart from the "
                "full-series evaluations above because the two are not equivalent evidence.",
                proxied,
                precision,
                with_debt=with_debt,
                levered=levered,
                z_label=z_label,
                selected=selected,
            )
        )

    notes = [
        _RECONCILES_NOTE if precision.reconciles else _ROUNDED_INDEPENDENTLY,
    ]
    if not with_debt:
        notes.append(_NO_DEBT_MEASURE_COLUMNS)
    if selected is None:
        notes.append(
            "No row is marked selected, because the record marks no point selected. Read that as a "
            "search that chose nothing, never as a table that lost the answer."
        )
    note_html = "".join(f'<p class="rownote">{escape(note)}</p>' for note in notes)
    return f'<div class="figtable">{"".join(blocks)}{note_html}</div>'


def sizing_surface(*, grid: SizingGrid | None) -> str:
    """The full evaluated power-by-duration space as a table, with the selected row marked.

    The public entry point for the owner's "clearly shows the full sizing surface" requirement. It
    is the same table the map falls back to, rendered unconditionally: a heat map answers "what
    shape is the space" and a table answers "what exactly did candidate N score", and the owner
    asked for the second explicitly. An absent grid states its absence rather than an empty frame.
    """
    if grid is None or not grid.points:
        return (
            f'<div class="figtable"><div class="grouplab">{escape(_SIZING_SURFACE_TITLE)}</div>'
            f'<p class="rownote">{escape(_NO_SURFACE)}</p></div>'
        )
    z_label = _z_label(levered=_z_is_levered(grid.points))
    return (
        f'<div class="figtable"><div class="grouplab">{escape(_SIZING_SURFACE_TITLE)}</div>'
        f'<p class="rownote">{escape(SIZING_SURFACE_LEAD)}</p>'
        f"{_sizing_table(grid, z_label)}</div>"
    )


def sizing_map(
    *,
    grid: SizingGrid | None,
    constraints_binding: Sequence[str] = (),
    solver_note: str | None = None,
) -> str:
    """The evaluated power-by-duration space: one cell per real solve and nothing between.

    A 2D evaluated-cell map, deliberately not a 3D surface and deliberately without interpolated
    iso-return lines. The app's WebGL surface bilinearly interpolates a dense mesh, so it displays
    returns at sizes the solver never evaluated: tolerable in an explorer with tooltips on real
    points, a fabricated figure in a document where every number must trace to the record. Holes
    stay holes. The feasibility boundary emerges from cell colour, which asserts nothing between
    evaluations.
    """
    title = "THE SIZING MAP"
    if grid is None or not grid.points:
        extra = [] if solver_note is None else _wrap(solver_note, 128)
        return unavailable_figure(title, _NO_GRID, notes=extra)

    points = grid.points
    has_levered = any(p.levered_return_pct is not None for p in points)
    z_label = (
        "Levered (equity) return per evaluated cell"
        if has_levered
        else "Project return per evaluated cell, unlevered"
    )

    def z_of(point: SizingGridPoint) -> float | None:
        return point.levered_return_pct if has_levered else point.project_irr_pct

    z_values = [z for z in (z_of(p) for p in points) if z is not None]
    if not z_values:
        return _sizing_table(grid, z_label)

    lattice = [p for p in points if p.stage in _LATTICE_STAGES] or points
    powers = sorted({p.power_kw for p in lattice})
    durations = sorted({p.duration_hours for p in lattice})
    by_cell: dict[tuple[float, float], SizingGridPoint] = {}
    for point in points:
        key = (point.power_kw, point.duration_hours)
        held = by_cell.get(key)
        if held is None or _cell_rank(point) > _cell_rank(held):
            by_cell[key] = point

    plot_x, plot_r, plot_y = 96.0, 690.0, 52.0
    cell_w = (plot_r - plot_x) / float(len(powers))
    cell_h = min(52.0, max(26.0, 220.0 / float(len(durations))))
    plot_b = plot_y + cell_h * len(durations)
    x_centres = [plot_x + cell_w * (index + 0.5) for index in range(len(powers))]
    # Duration increases upward, so the last row sits at the top of the plot.
    y_centres = [plot_b - cell_h * (index + 0.5) for index in range(len(durations))]

    z_lo, z_hi = min(z_values), max(z_values)
    z_span = z_hi - z_lo
    labelled = len(powers) <= 6 and len(durations) <= 6

    body: list[str] = [_title_band(title)]
    body.append(_rect(plot_x, plot_y, plot_r - plot_x, plot_b - plot_y, _WHITE, stroke=_RULE))

    for row, duration in enumerate(durations):
        for column, power in enumerate(powers):
            x = plot_x + cell_w * column
            y = plot_b - cell_h * (row + 1)
            cell = by_cell.get((power, duration))
            z = None if cell is None else z_of(cell)
            if cell is None or z is None:
                body.append(_rect(x, y, cell_w, cell_h, _WHITE, stroke=_RULE, dash="2 3"))
                continue
            t = 0.5 if z_span <= 0.0 else (z - z_lo) / z_span
            rgb = _ramp_rgb(t)
            fails = cell.passes_covenant is False
            if fails:
                rgb = _mix(rgb, _CREAM_RGB, 0.55)
            body.append(_rect(x, y, cell_w, cell_h, _hex(rgb), stroke=_WHITE, width=1.0))
            if labelled:
                body.append(
                    _text(
                        x + cell_w / 2.0,
                        y + cell_h / 2.0 + 4.0,
                        fmt.pct(z),
                        cls="gqval",
                        fill=_GREY if fails else _readable(rgb),
                        anchor="middle",
                    )
                )

    # Stage markers at their true coordinates: a continuous search evaluates between cells.
    for point in points:
        if point.stage not in ("refine", "confirm"):
            continue
        x = _axis_position(point.power_kw, powers, x_centres)
        y = _axis_position(point.duration_hours, durations, y_centres)
        if point.proxy:
            body.append(_circle(x, y, 3.4, "none", stroke=_GREY))
        else:
            body.append(_circle(x, y, 3.4, _INK))

    selected = grid.selected
    if selected is not None:
        # An evaluated cell is marked by outlining the cell, which leaves its own value legible; a
        # selection between cells (a continuous search) is ringed at its true coordinates instead.
        on_lattice = selected.power_kw in powers and selected.duration_hours in durations
        if on_lattice:
            column = powers.index(selected.power_kw)
            row = durations.index(selected.duration_hours)
            x = plot_x + cell_w * column
            y = plot_b - cell_h * (row + 1)
            body.append(_rect(x, y, cell_w, cell_h, "none", stroke=_WHITE, width=3.4))
            body.append(_rect(x, y, cell_w, cell_h, "none", stroke=_INK, width=1.8))
        else:
            x = _axis_position(selected.power_kw, powers, x_centres)
            y = _axis_position(selected.duration_hours, durations, y_centres)
            # A white halo under an ink ring, so the marker reads at either end of the ramp.
            body.append(
                f'<circle cx="{_n(x)}" cy="{_n(y)}" r="10.00" fill="none" stroke="{_WHITE}" '
                'stroke-width="3.6"/>'
            )
            body.append(
                f'<circle cx="{_n(x)}" cy="{_n(y)}" r="10.00" fill="none" stroke="{_INK}" '
                'stroke-width="1.8"/>'
            )

    # Axes: every tick is an evaluated coordinate, formatted.
    for index, power in enumerate(powers):
        body.append(
            _text(
                x_centres[index],
                plot_b + 16.0,
                fmt.kw(power),
                cls="gqax",
                fill=_GREY,
                anchor="middle",
            )
        )
    for index, duration in enumerate(durations):
        body.append(
            _text(
                plot_x - 10.0,
                y_centres[index] + 4.0,
                fmt.hours(duration),
                cls="gqax",
                fill=_GREY,
                anchor="end",
            )
        )
    body.append(_text(0.0, plot_y - 12.0, "DURATION", cls="gqax", fill=_GREY))
    body.append(
        _text(plot_r, plot_b + 32.0, "EVALUATED POWER", cls="gqax", fill=_GREY, anchor="end")
    )

    # Ramp legend: its ends are the actual extremes of the evaluated returns.
    ramp_x, ramp_y, ramp_w = 712.0, plot_y + 8.0, 22.0
    ramp_steps = 24
    ramp_h = 8.0
    for step_index in range(ramp_steps):
        t = 1.0 - step_index / float(ramp_steps - 1)
        body.append(_rect(ramp_x, ramp_y + step_index * ramp_h, ramp_w, ramp_h, _hex(_ramp_rgb(t))))
    body.append(_text(ramp_x + ramp_w + 8.0, ramp_y + 8.0, fmt.pct(z_hi), cls="gqval"))
    body.append(
        _text(
            ramp_x + ramp_w + 8.0,
            ramp_y + ramp_steps * ramp_h,
            fmt.pct(z_lo),
            cls="gqval",
        )
    )
    for index, line in enumerate(_wrap(z_label, 24)):
        body.append(_text(ramp_x, 40.0 + index * 12.0, line, cls="gqax", fill=_GREY))

    legend_y = plot_b + 54.0
    legend_markup, legend_height = _legend(
        plot_x,
        legend_y,
        [
            (f"{_WHITE}|{_RULE}|2 3", "not evaluated"),
            (_hex(_mix(_ramp_rgb(0.7), _CREAM_RGB, 0.55)), "covenant not met"),
            (f"dot:{_INK}", "confirmed on the full series"),
            (f"dot:none|{_GREY}", _PROXY_NOTE),
            (f"none|{_INK}", "selected size"),
        ],
        width=plot_r - plot_x,
    )
    body.append(legend_markup)

    n_proxy = sum(1 for p in points if p.proxy)
    n_confirm = sum(1 for p in points if p.stage == "confirm")
    lines: list[str] = []
    lines.extend(
        _wrap(
            f"{grid.search_version}: {len(points)} evaluations, {n_confirm} confirmed on the full "
            f"series, {n_proxy} typical-day proxy. One cell per real solve; nothing is drawn "
            "between evaluations.",
            132,
        )
    )
    if selected is not None:
        basis = grid.selection_basis
        label = f"{fmt.kw(selected.power_kw)} × {fmt.hours(selected.duration_hours)}"
        if basis == "unlevered":
            lines.extend(
                _wrap(
                    f"Selected point {label}, on an UNLEVERED basis: no debt structure was "
                    "resolved, so no P90 DSCR exists for it and it has neither passed nor failed "
                    f"a covenant. Reason on the record: {grid.selection_reason}",
                    132,
                )
            )
        elif basis == "indicative_fallback":
            lines.extend(
                _wrap(
                    f"Selected point {label}, on an indicative fallback basis: a recommendation, "
                    f"not a feasible optimum. Reason on the record: {grid.selection_reason}",
                    132,
                )
            )
        elif basis is None:
            lines.extend(
                _wrap(
                    f"Selected point {label}. The record does not state the selection basis, "
                    "which is not the same as no basis and is not read as one.",
                    132,
                )
            )
        else:
            lines.extend(
                _wrap(
                    f"Selected point {label}, selection basis {fmt.titleise(basis)}. Reason on the "
                    f"record: {grid.selection_reason}",
                    132,
                )
            )
    else:
        lines.extend(_wrap("No point is marked selected on the recorded grid.", 132))
    if all(p.passes_covenant is None for p in points):
        lines.extend(_wrap(f"Feasibility layer: {_NO_COVENANT_MEASURE}.", 132))
    elif grid.covenant is not None:
        lines.extend(
            _wrap(
                f"Covenant the feasibility flag is measured against: {fmt.ratio(grid.covenant)}.",
                132,
            )
        )
    if constraints_binding:
        lines.extend(
            _wrap(
                "The selected size was limited by: " + ", ".join(constraints_binding) + ".",
                132,
            )
        )
    if solver_note is not None:
        lines.extend(_wrap(solver_note, 132))
    notes_y = legend_y + legend_height + 12.0
    body.append(_notes(0.0, notes_y, lines))
    height = notes_y + len(lines) * 14.0 + 8.0

    alt = (
        f"Sizing map: {len(points)} evaluated sizes, returns from {fmt.pct(z_lo)} to "
        f"{fmt.pct(z_hi)}"
        + (
            "."
            if selected is None
            else f", selected {fmt.kw(selected.power_kw)} by {fmt.hours(selected.duration_hours)}."
        )
    )
    return _svg(height, alt, "".join(body))


# --- exhibit: how the MILP selection was made ------------------------------------------------

_MILP_TITLE = "HOW THE SELECTION WAS MADE"
_NO_MILP = (
    "The run record carries no evaluated sizing grid, so the selection cannot be shown. There is "
    "nothing to rank and no ranking is drawn."
)
_NO_MILP_CONFIRMED = (
    "No candidate on this record was confirmed on the full half-hourly series, so there is no "
    "ranking to draw. A typical-period proxy score steers the search and is never the basis a size "
    "is selected on, so the proxy evaluations are not ranked here in its place."
)
_NO_MILP_SCORES = (
    "No confirmed candidate on this record carries a return, so the candidates cannot be ranked. "
    "The full evaluated space is still published as a table; what is missing is the score the "
    "ranking would order it by."
)
#: How the ranking works, said on the figure rather than left to the methodology section, because
#: this is the exhibit where a reader asks the question.
_MILP_RANKING_NOTE = (
    "Every bar is one candidate that was CONFIRMED by a full half-hourly dispatch solve, ranked on "
    "the objective the record says the search ranked. The selected candidate is the top of this "
    "ranking under the recorded basis; ties break to the smaller system."
)


def milp_selection(*, grid: SizingGrid | None, solver_note: str | None = None) -> str:
    """The confirmed candidates ranked on the recorded objective, with the selected one marked.

    The exhibit that makes the selection legible: which candidate was chosen, out of what, and on
    what basis. The sizing map shows the shape of the searched space and the surface table states
    every figure in it; neither answers "why this one", which is a question about an ORDERING.

    Only candidates the record marks confirmed on the full series are ranked. A typical-period proxy
    score guided the search and was never the basis a size was selected on, so ranking proxy points
    beside confirmed ones would draw a comparison the engine never made. Where the record confirms
    none, the figure says so rather than substituting the proxies.

    The bars are geometry over recorded values: no candidate is re-scored here, nothing is
    interpolated, and a candidate whose return the record does not carry is named in the notes
    rather than being drawn at zero.
    """
    if grid is None or not grid.points:
        return unavailable_figure(_MILP_TITLE, _NO_MILP)
    confirmed = [point for point in grid.points if not point.proxy]
    if not confirmed:
        return unavailable_figure(_MILP_TITLE, _NO_MILP_CONFIRMED)
    levered = _z_is_levered(grid.points)

    def score(point: SizingGridPoint) -> float | None:
        return point.levered_return_pct if levered else point.project_irr_pct

    scored = [point for point in confirmed if score(point) is not None]
    unscored = [point for point in confirmed if score(point) is None]
    if not scored:
        return unavailable_figure(_MILP_TITLE, _NO_MILP_SCORES)
    # Descending on the objective, then ascending on power: the record's own tie-break is the
    # smaller system, and a chart that ordered ties differently would misdescribe the ranking.
    ranked = sorted(scored, key=lambda point: (-(score(point) or 0.0), point.power_kw))
    selected = _selected_key(grid)

    # The same precision rule the surface table is held to: two bars must not carry identical
    # labels, because a reader cannot then tell which one was selected.
    precision = _precision_for(
        [(point.power_kw, point.duration_hours, point.energy_kwh) for point in ranked]
    )
    values = [score(point) or 0.0 for point in ranked]
    hi = max(values)
    lo = min(values)
    # The baseline is zero whenever a candidate scored negative, so a loss reads as a loss and
    # not as a short bar. Otherwise it is the floor of the evaluated range.
    base = min(0.0, lo)
    span = hi - base
    row_h = 22.0
    plot_x, plot_r, plot_y = 200.0, 720.0, 46.0
    plot_w = plot_r - plot_x

    body: list[str] = [_title_band(_MILP_TITLE)]
    body.append(_line(plot_x, plot_y - 8.0, plot_r, plot_y - 8.0, _RULE, width=1.0))
    for index, point in enumerate(ranked):
        value = score(point) or 0.0
        y = plot_y + index * row_h
        is_selected = selected is not None and _point_key(point) == selected
        width = 2.0 if span <= 0.0 else max(2.0, (value - base) / span * plot_w)
        fill = _INK if is_selected else _PEACH
        body.append(_rect(plot_x, y, width, row_h - 8.0, fill, stroke=_WHITE, width=1.0))
        label = (
            f"{fmt.kw_at(point.power_kw, precision.power_dp)} x "
            f"{fmt.hours_at(point.duration_hours, precision.duration_dp)}"
        )
        body.append(
            _text(
                plot_x - 10.0,
                y + row_h - 15.0,
                label,
                cls="gqval" if is_selected else "gqlg",
                fill=_INK,
                anchor="end",
            )
        )
        value_text = fmt.pct(value)
        if is_selected:
            value_text = f"{value_text}  {_SELECTED_MARK}"
        body.append(
            _text(
                plot_x + width + 8.0,
                y + row_h - 15.0,
                value_text,
                cls="gqval",
                fill=_INK,
            )
        )
        if point.passes_covenant is False:
            body.append(
                _text(
                    plot_x - 10.0,
                    y + row_h - 4.0,
                    "covenant not met",
                    cls="gqax",
                    fill=_SIGNAL,
                    anchor="end",
                )
            )
    plot_b = plot_y + len(ranked) * row_h
    body.append(
        _text(plot_x, plot_b + 14.0, _z_label(levered=levered).upper(), cls="gqax", fill=_GREY)
    )
    legend_y = plot_b + 36.0
    legend_markup, legend_height = _legend(
        plot_x,
        legend_y,
        [
            (_INK, "the selected candidate"),
            (_PEACH, "a confirmed candidate that was not selected"),
        ],
        width=plot_w,
    )
    body.append(legend_markup)

    lines: list[str] = []
    lines.extend(_wrap(_MILP_RANKING_NOTE, 132))
    lines.extend(
        _wrap(
            f"Ranked here: {len(ranked)} confirmed candidate(s) of {len(grid.points)} "
            "evaluations on "
            f"the record. {len([p for p in grid.points if p.proxy])} evaluation(s) were "
            "typical-period proxy scores and are excluded from this ranking by design.",
            132,
        )
    )
    if unscored:
        lines.extend(
            _wrap(
                f"{len(unscored)} confirmed candidate(s) carry no return on the record and are NOT "
                "drawn: an unrecorded score is an absence, and drawing it at zero would rank it "
                "below every real loss.",
                132,
            )
        )
    basis = grid.selection_basis
    if selected is None:
        lines.extend(
            _wrap(
                "The record marks no candidate selected, so no bar is marked: the ranking is shown "
                "and the choice is not on the record.",
                132,
            )
        )
    else:
        lines.extend(
            _wrap(
                "Selection basis on the record: "
                f"{'not stated' if basis is None else fmt.titleise(basis)}.",
                132,
            )
        )
        lines.extend(
            _wrap(f"Reason on the record, verbatim: {grid.selection_reason}", 132)
            if grid.selection_reason
            else _wrap("The record states no selection reason.", 132)
        )
    if grid.covenant is not None:
        lines.extend(
            _wrap(
                "Feasibility filter applied to the ranking: a candidate must clear a minimum P90 "
                f"DSCR of {fmt.ratio(grid.covenant)} to be eligible. Where no debt structure "
                "resolved, no candidate was tested and the basis above says so.",
                132,
            )
        )
    if solver_note is not None:
        lines.extend(_wrap(solver_note, 132))
    notes_y = legend_y + legend_height + 12.0
    body.append(_notes(0.0, notes_y, lines))
    height = notes_y + len(lines) * 14.0 + 8.0
    alt = (
        f"Selection ranking: {len(ranked)} confirmed candidates ordered on the recorded objective, "
        f"from {fmt.pct(hi)} down to {fmt.pct(lo)}"
    )
    return _svg(height, alt, "".join(body))


# --- exhibit: the revenue stack, firm and gated ----------------------------------------------

#: The bar canvas every stream row draws on. Uniform scaling only (`meet`, plus ``height:auto`` in
#: the stylesheet), so the hatch keeps its 45 degrees whatever width the page gives the figure.
_BAR_W = 600.0
_BAR_H = 26.0
_BAR_INSET = 4.0
#: Spacing between hatch strokes, in canvas units.
_HATCH_STEP = 7.0
_NO_STREAMS = "the record carries no value stream, priced or held out"
_HATCH_MEANING = (
    "Hatching marks value that is available and not counted: on page 1 of the brand reference it "
    "is unused connection capacity, and here it is recorded revenue held outside the priced floor."
)


def _hatch(x: float, y: float, w: float, h: float, stroke: str) -> str:
    """The brand's signature device: 45-degree strokes, clipped to a rectangle by geometry.

    Each stroke is a real line whose endpoints are solved against the rectangle's edges, rather than
    a tiled `pattern` behind a `clipPath`: both of those are referenced by a fragment URL, and the
    rendered document may carry no ``url(`` at all (`tests/test_report_charts.py`
    ``test_exhibits_are_self_contained``). The device, its angle and its meaning are unchanged:
    hatched is *available and not counted*, never an error, an infeasibility or an absence.
    """
    if w <= 0.0 or h <= 0.0:
        return ""
    strokes: list[str] = []
    steps = int((w + h) / _HATCH_STEP) + 1
    for index in range(steps):
        # Where this stroke would cross the rectangle's top edge if the rectangle were unbounded.
        intercept = x - h + index * _HATCH_STEP
        start = max(0.0, x - intercept)
        end = min(h, x + w - intercept)
        if end <= start:
            continue
        strokes.append(
            _line(
                intercept + start,
                y + start,
                intercept + end,
                y + end,
                stroke,
                width=1.5,
            )
        )
    return "".join(strokes)


def _stream_bar(width: float, kind: str) -> str:
    """One stream's bar: solid for counted revenue, hatched for held-out, outlined for excluded."""
    body: list[str] = []
    height = _BAR_H - _BAR_INSET * 2.0
    drawn = max(width, 1.5) if width > 0.0 else 0.0
    if drawn > 0.0:
        if kind == "floor":
            body.append(_rect(0.0, _BAR_INSET, drawn, height, _ORANGE))
        elif kind == "upside":
            body.append(_rect(0.0, _BAR_INSET, drawn, height, _WHITE))
            body.append(_hatch(0.0, _BAR_INSET, drawn, height, _PEACH))
            body.append(
                _rect(0.0, _BAR_INSET, drawn, height, "none", stroke=_ORANGE_MID, width=1.0)
            )
        else:
            body.append(
                _rect(0.0, _BAR_INSET, drawn, height, _WHITE, stroke=_GREY, width=1.0, dash="3 2")
            )
    return (
        f'<svg class="gqbar" viewBox="0 0 {_n(_BAR_W)} {_n(_BAR_H)}" '
        f'preserveAspectRatio="xMinYMid meet" aria-hidden="true">{"".join(body)}</svg>'
    )


def _stream_count(number: int) -> str:
    """A group's size, in words, from the rows the group holds."""
    return (
        f"{fmt.count_word(number)} stream" if number == 1 else f"{fmt.count_word(number)} streams"
    )


def _stack_group(label: str, rows: Sequence[StreamRow], kind: str, scale: _Scale) -> str:
    """One group of stream bars, its heading counting its own rows."""
    out = [f'<div class="grouplab rsspan">{escape(f"{label}: {_stream_count(len(rows))}")}</div>']
    for row in rows:
        consent = '<span class="chip">consent given</span>' if row.consent_status == "given" else ""
        out.append(
            f'<div class="rslab">{escape(fmt.stream_label(row.stream))}{consent}</div>'
            f'<div class="rsbar">{_stream_bar(scale(row.annual_revenue_gbp), kind)}</div>'
            f'<div class="rsval">{escape(fmt.gbp(row.annual_revenue_gbp))}</div>'
        )
    return "".join(out)


def revenue_stack(
    *,
    floor: Sequence[StreamRow],
    upside: Sequence[StreamRow],
    excluded: Sequence[StreamRow],
) -> str:
    """Which streams make up the priced floor, what sits outside it, and how big each one is.

    A bar per recorded stream, grouped by what it counts towards. Bar length is geometry over the
    record's own annual revenue; every displayed value is that revenue formatted, and it is rendered
    as document text beside the bar rather than inside the drawing, so the figure adds no number of
    its own to the page. Floor bars are solid, held-out upside bars carry the brand hatch, and an
    excluded stream is an outline: the reader sees the proportion a table cannot show.
    """
    groups = (
        ("Priced floor", floor, "floor"),
        ("Merchant upside, held out", upside, "upside"),
        ("Excluded from the priced case", excluded, "excluded"),
    )
    populated = [(label, rows, kind) for label, rows, kind in groups if rows]
    if not populated:
        return (
            '<div class="figtable"><p class="rownote">'
            f'<span class="na">{escape(fmt.unavailable(_NO_STREAMS))}</span></p></div>'
        )

    revenues = [row.annual_revenue_gbp for _, rows, _ in populated for row in rows]
    top = max(revenues)
    scale = _Scale(0.0, top, 0.0, _BAR_W) if top > 0.0 else _Scale(0.0, 0.0, 0.0, 0.0)

    blocks = [_stack_group(label, rows, kind, scale) for label, rows, kind in populated]

    swatch = 'class="gqswatch" viewBox="0 0 18 11" aria-hidden="true"'
    legend = (
        f'<span class="rskey"><svg {swatch}>{_rect(0.0, 0.0, 18.0, 11.0, _ORANGE)}</svg>'
        "counted in the priced floor</span>"
        f'<span class="rskey"><svg {swatch}>{_rect(0.0, 0.0, 18.0, 11.0, _WHITE)}'
        f"{_hatch(0.0, 0.0, 18.0, 11.0, _PEACH)}"
        f"{_rect(0.0, 0.0, 18.0, 11.0, 'none', stroke=_ORANGE_MID, width=1.0)}</svg>"
        "recorded, held outside the floor</span>"
        f'<span class="rskey"><svg {swatch}>'
        f"{_rect(0.0, 0.0, 18.0, 11.0, _WHITE, stroke=_GREY, width=1.0, dash='3 2')}</svg>"
        "excluded from the priced case</span>"
    )
    notes = [_HATCH_MEANING]
    if top <= 0.0:
        notes.append(
            "No stream carries a positive recorded revenue, so no bar is drawn and the recorded "
            "values stand alone."
        )
    note_html = "".join(f'<p class="rownote">{escape(note)}</p>' for note in notes)
    return (
        f'<div class="figtable"><div class="rsleg">{legend}</div>'
        f'<div class="rs">{"".join(blocks)}</div>{note_html}</div>'
    )


# --- exhibit: the bill, before and after ----------------------------------------------------


def bill_columns(*, bill: BillSavingsView) -> str:
    """The annual bill before and after, as paired stacked columns of recorded components.

    Each segment is one recorded line of the bill, labelled with its own recorded value: the two
    battery-invariant fixed lines stay separate rather than being added together, because their
    total is not a figure the record carries. A negative saving renders the same way, in plain
    words: the schema represents that case and the exhibit does not hide it.
    """
    title = "THE BILL, BEFORE AND AFTER"
    notes = _wrap(bill.data_ask, 128) if bill.data_ask else ()
    # Two absences, two statements, selected from the view's own state rather than from whichever
    # string happens to be non-empty. "No customer bill is persisted on this record" was printed
    # over a record that persists one: false in the opposite direction to the saving claim, and
    # just as misleading, because it tells the reader the BEFORE figure does not exist either.
    if not bill.available:
        return unavailable_figure(
            title,
            bill.pending_reason or "no customer bill is persisted on this record",
            notes=notes,
        )
    if not bill.after_side_priced or bill.bill_before_gbp is None or bill.bill_after_gbp is None:
        return unavailable_figure(
            title,
            bill.pending_reason
            or "the persisted bill carries its BEFORE side only, so there is no after-battery "
            "bill to draw against it",
            notes=notes,
        )

    segments = (
        ("Energy", bill.energy_before_gbp, bill.energy_after_gbp, _ORANGE_MID),
        ("DUoS", bill.duos_before_gbp, bill.duos_after_gbp, _ORANGE),
        ("Levies", bill.levies_before_gbp, bill.levies_after_gbp, _PEACH),
        ("Standing charge", bill.standing_gbp, bill.standing_gbp, _RULE),
        ("Capacity charge", bill.capacity_gbp, bill.capacity_gbp, _CREAM),
    )
    decomposed = all(before is not None and after is not None for _, before, after, _ in segments)

    plot_y, plot_b = 66.0, 300.0
    before_x, after_x, col_w = 200.0, 470.0, 150.0
    top = max(bill.bill_before_gbp, bill.bill_after_gbp)
    scale = _Scale(0.0, top, plot_b, plot_y)

    body: list[str] = [_title_band(title)]
    body.append(_line(before_x - 40.0, plot_b, after_x + col_w + 40.0, plot_b, _INK, width=1.0))

    lines: list[str] = []
    if decomposed:
        # A thin segment cannot hold its own value, so it is called out beside the column instead:
        # the before column's callouts go left and the after column's right, and each is pushed
        # clear of the previous one so two thin lines never print on top of each other.
        for column_x, side in ((before_x, 0), (after_x, 1)):
            cursor = 0.0
            callout_floor = plot_y - 14.0
            for label, before, after, colour in segments:
                value = (before if side == 0 else after) or 0.0
                if value <= 0.0:
                    continue
                y_top = scale(cursor + value)
                y_bottom = scale(cursor)
                body.append(_rect(column_x, y_top, col_w, y_bottom - y_top, colour, stroke=_WHITE))
                if y_bottom - y_top >= 15.0:
                    body.append(
                        _text(
                            column_x + col_w / 2.0,
                            (y_top + y_bottom) / 2.0 + 4.0,
                            fmt.gbp(value),
                            cls="gqval",
                            fill=_INK,
                            anchor="middle",
                        )
                    )
                else:
                    mid = (y_top + y_bottom) / 2.0 + 4.0
                    callout_y = max(mid, callout_floor)
                    callout_floor = callout_y + 14.0
                    leader_x = column_x - 8.0 if side == 0 else column_x + col_w + 8.0
                    body.append(
                        _line(
                            column_x if side == 0 else column_x + col_w,
                            mid - 4.0,
                            leader_x,
                            callout_y - 4.0,
                            _RULE,
                        )
                    )
                    body.append(
                        _text(
                            leader_x,
                            callout_y,
                            f"{label} {fmt.gbp(value)}",
                            cls="gqax",
                            fill=_GREY,
                            anchor="end" if side == 0 else "start",
                        )
                    )
                cursor = cursor + value
    else:
        for column_x, total in ((before_x, bill.bill_before_gbp), (after_x, bill.bill_after_gbp)):
            y_top = scale(total)
            body.append(_rect(column_x, y_top, col_w, plot_b - y_top, _ORANGE_MID, stroke=_WHITE))
        lines.append("Component split not exposed on this record, so the totals stand alone.")

    for column_x, label, total in (
        (before_x, "BEFORE", bill.bill_before_gbp),
        (after_x, "AFTER", bill.bill_after_gbp),
    ):
        body.append(
            _text(column_x + col_w / 2.0, plot_b + 18.0, label, cls="gqnode", anchor="middle")
        )
        body.append(
            _text(
                column_x + col_w / 2.0,
                scale(total) - 10.0,
                fmt.gbp(total),
                cls="gqval",
                anchor="middle",
            )
        )

    # The saving bracket: the recorded figure, whichever direction it points.
    saving = bill.annual_savings_gbp
    bracket_y = plot_y - 26.0
    body.append(_line(before_x + col_w / 2.0, bracket_y, after_x + col_w / 2.0, bracket_y, _INK))
    body.append(
        _line(before_x + col_w / 2.0, bracket_y, before_x + col_w / 2.0, bracket_y + 8.0, _INK)
    )
    body.append(
        _line(after_x + col_w / 2.0, bracket_y, after_x + col_w / 2.0, bracket_y + 8.0, _INK)
    )
    if saving is None:
        body.append(
            _text(
                (before_x + after_x + col_w) / 2.0,
                bracket_y - 8.0,
                fmt.unavailable("no annual saving on the record"),
                cls="gqna",
                fill=_SIGNAL,
                anchor="middle",
            )
        )
    else:
        rising = saving < 0.0
        caption = "The bill rises under this dispatch" if rising else "Annual saving"
        body.append(
            _text(
                (before_x + after_x + col_w) / 2.0,
                bracket_y - 8.0,
                f"{caption}: {fmt.gbp(saving)}",
                cls="gqval",
                fill=_SIGNAL if rising else _INK,
                anchor="middle",
            )
        )
        if rising:
            lines.append(
                "The bill rises under this dispatch: round-trip losses are bought at the price "
                "faced, so a negative saving is a real, recorded result."
            )

    if decomposed:
        legend_markup, _ = _legend(
            before_x,
            plot_b + 38.0,
            [
                (_ORANGE_MID, "Energy"),
                (_ORANGE, "DUoS"),
                (_PEACH, "Levies"),
                (f"{_RULE}|{_RULE}", "fixed: unchanged by the battery"),
            ],
            width=_W - before_x,
        )
        body.append(legend_markup)
    if bill.basis:
        lines.extend(_wrap(f"Basis on the record: {bill.basis}", 132))
    before_kwh, after_kwh = bill.import_before_kwh, bill.import_after_kwh
    if before_kwh is not None and after_kwh is not None:
        lines.append(
            f"Imported energy on the record: {fmt.kwh(before_kwh)} before, "
            f"{fmt.kwh(after_kwh)} after."
        )
    body.append(_notes(0.0, plot_b + 62.0, lines))
    height = plot_b + 62.0 + len(lines) * 14.0 + 8.0

    saving_label = (
        fmt.unavailable("no annual saving on the record") if saving is None else fmt.gbp(saving)
    )
    alt = (
        f"Annual bill {fmt.gbp(bill.bill_before_gbp)} before and "
        f"{fmt.gbp(bill.bill_after_gbp)} after, a recorded change of {saving_label}."
    )
    return _svg(height, alt, "".join(body))
