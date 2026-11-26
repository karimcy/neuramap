/* GridQ charts - vendored Plotly (no CDN, offline), driven by the server's data islands.
 *
 * Each island carries RAW record values (the chart's pixel geometry: bar heights, surface z, the
 * dispatch series) and PRE-FORMATTED label strings (every bit of visible text: hover, axis ticks).
 * This file positions and draws but never computes or formats a financial figure - that stays
 * server-side in gridq.outputs.formatting / gridq.app.view. If Plotly or a div is unavailable the
 * server-rendered fallback tables remain the source of truth.
 *
 * Plotly is loaded as a vendored /static/plotly.min.js (pinned v2.35.2) - no external CDN, so the
 * surface renders offline and under a strict CSP, consistent with the deterministic-output posture.
 */
(function () {
  "use strict";

  // The live design-system palette, read off :root by static/theme.js at call time - no
  // hand-mirrored constants (the old duplicated palette drifted; see redesign-audit risk notes).
  const T = window.GridQTheme.tokens();
  const FIRM = T.ok, ACCENT = T.accent, STONE = T.gated, STONE_DEEP = T.lineStrong;
  const INK = T.text, MUTED = T.muted, LINE = T.line, EDGE = T.bg;
  const FLOOR_SHADES = window.GridQTheme.rampFirm();
  const UPSIDE_SHADES = window.GridQTheme.rampUpside();
  // Sequential return scale (low → high): charcoal up to the accent amber, so brighter = better
  // return. Shared by the 3D surface and the 2D heat-map so colour means the same in both.
  const RETURN_SCALE = window.GridQTheme.returnScale();

  const FONT = { family: T.mono, color: T.text2, size: 11 };
  const CONFIG = { displaylogo: false, responsive: true,
                   modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };
  const baseLayout = (over) => Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: FONT, margin: { l: 56, r: 16, t: 16, b: 40 },
    showlegend: true, legend: { orientation: "h", y: -0.18, font: { size: 11 } },
  }, over || {});

  function draw(id, data, layout) {
    const el = document.getElementById(id);
    if (!el) return;
    try { window.Plotly.newPlot(el, data, layout, CONFIG); }
    catch (e) { /* fallback table for this chart remains the source of truth */ }
  }

  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Draw-on-scroll: plot with zeroed series immediately (layout settles, no shift), then
  // animate to the REAL record values when the chart first scrolls into view. Purely how
  // the already-plotted values arrive - the data never changes. Reduced motion / no
  // GridQMotion = the final chart at once. finalY[i] is trace i's real y series, or null
  // for traces that should render statically from the start.
  function drawOnScroll(id, data, layout, finalY) {
    const el = document.getElementById(id);
    if (!el) return;
    const motion = window.GridQMotion;
    if (REDUCED || !motion || typeof motion.watch !== "function") { draw(id, data, layout); return; }
    const idx = finalY.map((y, i) => (y ? i : -1)).filter(i => i >= 0);
    const seeded = data.map((t, i) =>
      finalY[i] ? Object.assign({}, t, { y: t.y.map(() => 0) }) : t);
    try {
      window.Plotly.newPlot(el, seeded, layout, CONFIG).then(() => {
        motion.watch(el, () => {
          window.Plotly.animate(el, { data: idx.map(i => ({ y: finalY[i] })), traces: idx },
            { transition: { duration: 900, easing: "cubic-in-out" }, frame: { duration: 900 } });
        });
      });
    } catch (e) { draw(id, data, layout); }
  }

  // A colour range that never collapses (cmin === cmax breaks the colourscale on a flat field).
  function zrange(s) {
    const lo = (s.z_min === null || s.z_min === undefined) ? 0 : s.z_min;
    const hi = (s.z_max === null || s.z_max === undefined) ? lo + 1 : s.z_max;
    return hi > lo ? [lo, hi] : [lo, lo + 1];
  }

  // ---- 3D sizing surface (continuous, resampled mesh) -------------------
  function surface(s) {
    const traces = [];
    const [cmin, cmax] = zrange(s);
    if (s.dense && s.has_surface) {
      traces.push({
        type: "surface", x: s.dense_powers, y: s.dense_durations, z: s.dense_z,
        colorscale: RETURN_SCALE, cmin: cmin, cmax: cmax, opacity: 0.95, hoverinfo: "skip",
        name: "surface",
        colorbar: { title: { text: s.z_label + " %", side: "right" }, thickness: 12,
                    len: 0.7, x: 0.98, outlinewidth: 0, tickfont: { size: 10, color: MUTED } },
        contours: { z: { show: true, usecolormap: true, width: 1, highlight: false } },
        lighting: { ambient: 0.82, diffuse: 0.55, specular: 0.06, roughness: 0.92, fresnel: 0.1 },
        lightposition: { x: 120, y: 220, z: 320 },
      });
    } else if (s.has_surface) {
      traces.push({
        type: "surface", x: s.powers, y: s.durations, z: s.z_matrix,
        colorscale: RETURN_SCALE, cmin: cmin, cmax: cmax, opacity: 0.92, hoverinfo: "skip",
        showscale: true, name: "surface",
      });
    }
    // Evaluated candidates on the surface. Proxy (typical-day) evaluations are search guidance,
    // never reported figures (D32): they render faint and secondary; full-series evaluations
    // read as the real ink dots.
    const ev = s.points.filter(p => !p.is_selected && p.z !== null && p.z !== undefined);
    const evReal = ev.filter(p => !p.proxy);
    const evProxy = ev.filter(p => p.proxy);
    if (evProxy.length) {
      traces.push({
        type: "scatter3d", mode: "markers", name: "Proxy (guidance)",
        x: evProxy.map(p => p.power_kw), y: evProxy.map(p => p.duration_hours),
        z: evProxy.map(p => p.z),
        text: evProxy.map(p => p.tooltip), hovertemplate: "%{text}<extra></extra>",
        marker: { size: 2, color: MUTED, opacity: 0.25, line: { width: 0 } },
      });
    }
    if (evReal.length) {
      traces.push({
        type: "scatter3d", mode: "markers", name: "Evaluated",
        x: evReal.map(p => p.power_kw), y: evReal.map(p => p.duration_hours),
        z: evReal.map(p => p.z),
        text: evReal.map(p => p.tooltip), hovertemplate: "%{text}<extra></extra>",
        marker: { size: 3, color: INK, opacity: 0.45, line: { width: 0 } },
      });
    }
    // Chosen optimum: an accent diamond floated at its own return height, labelled.
    if (s.selected && s.selected.z !== null && s.selected.z !== undefined) {
      traces.push({
        type: "scatter3d", mode: "markers", name: "Chosen optimum",
        x: [s.selected.power_kw], y: [s.selected.duration_hours], z: [s.selected.z],
        text: [s.selected.label], hovertemplate: "%{text}<extra></extra>",
        marker: { size: 6, color: ACCENT, symbol: "diamond", line: { width: 1, color: EDGE } },
      });
    }
    const layout = baseLayout({
      margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false,
      scene: {
        xaxis: { title: "Power (kW)", gridcolor: LINE, color: MUTED, tickfont: { size: 10 },
                 showbackground: false, zerolinecolor: LINE },
        yaxis: { title: "Duration (h)", tickvals: s.durations, ticktext: s.duration_labels,
                 gridcolor: LINE, color: MUTED, tickfont: { size: 10 },
                 showbackground: false, zerolinecolor: LINE },
        zaxis: { title: s.z_label + " (%)", gridcolor: LINE, color: MUTED, tickfont: { size: 10 },
                 showbackground: false, zerolinecolor: LINE },
        camera: { eye: { x: 1.5, y: -1.6, z: 0.85 } }, aspectmode: "cube",
      },
    });
    draw("surface", traces, layout);
  }

  // ---- 2D heat-map: same return field from above, feasible region + optimum --
  function heatmap(s) {
    if (!s.dense || !s.has_surface) return;   // no continuous field → no heat-map (3D stands alone)
    const [zmin, zmax] = zrange(s);
    const traces = [
      { type: "heatmap", x: s.dense_powers, y: s.dense_durations, z: s.dense_z,
        colorscale: RETURN_SCALE, zmin: zmin, zmax: zmax, zsmooth: "best", hoverinfo: "skip",
        colorbar: { title: { text: s.z_label + " %", side: "right" }, thickness: 12, len: 0.9,
                    outlinewidth: 0, tickfont: { size: 10, color: MUTED } } },
    ];
    // Every evaluated size as a dot: covenant-feasible filled firm, the rest hollow stone (with a
    // white ring so they read on any shade). One honest highlight, no inflated "region" contour.
    // Proxy evaluations render in their own faint style REGARDLESS of their covenant flag: a
    // typical-day proxy verdict is search guidance, never presented as a feasible size (D32).
    const pts = s.points || [];
    const proxyPts = pts.filter(p => p.proxy);
    const realPts = pts.filter(p => !p.proxy);
    const feas = realPts.filter(p => p.passes_covenant);
    const rest = realPts.filter(p => !p.passes_covenant);
    if (proxyPts.length) {
      traces.push({
        type: "scatter", mode: "markers", name: "Proxy (guidance)",
        x: proxyPts.map(p => p.power_kw), y: proxyPts.map(p => p.duration_hours),
        text: proxyPts.map(p => p.tooltip), hovertemplate: "%{text}<extra></extra>",
        marker: { size: 4, color: MUTED, opacity: 0.3, line: { width: 0 } },
      });
    }
    if (rest.length) {
      traces.push({
        type: "scatter", mode: "markers", name: "Evaluated",
        x: rest.map(p => p.power_kw), y: rest.map(p => p.duration_hours),
        text: rest.map(p => p.tooltip), hovertemplate: "%{text}<extra></extra>",
        marker: { size: 7, color: T.surface2, line: { width: 1.5, color: STONE_DEEP } },
      });
    }
    if (feas.length) {
      traces.push({
        type: "scatter", mode: "markers", name: "Covenant-feasible",
        x: feas.map(p => p.power_kw), y: feas.map(p => p.duration_hours),
        text: feas.map(p => p.tooltip), hovertemplate: "%{text}<extra></extra>",
        marker: { size: 9, color: FIRM, line: { width: 1.5, color: EDGE } },
      });
    }
    if (s.selected) {
      traces.push({
        type: "scatter", mode: "markers", name: "Chosen optimum",
        x: [s.selected.power_kw], y: [s.selected.duration_hours],
        text: [s.selected.label], hovertemplate: "%{text}<extra></extra>",
        marker: { size: 13, color: ACCENT, symbol: "diamond", line: { width: 1.5, color: EDGE } },
      });
    }
    const layout = baseLayout({
      margin: { l: 56, r: 16, t: 8, b: 40 }, showlegend: false,
      xaxis: { title: "Power (kW)", gridcolor: LINE, color: MUTED, zeroline: false },
      yaxis: { title: "Duration (h)", tickvals: s.durations, ticktext: s.duration_labels,
               gridcolor: LINE, color: MUTED, zeroline: false },
    });
    draw("surface-heatmap", traces, layout);
  }

  // ---- half-hourly dispatch (representative day) ------------------------
  function dispatch(d) {
    const redShapes = [];
    for (let i = 0; i < d.red_band.length; i++) {
      if (d.red_band[i]) {
        redShapes.push({
          type: "rect", xref: "x", yref: "paper", x0: i - 0.5, x1: i + 0.5, y0: 0, y1: 1,
          fillcolor: T.accentWash, line: { width: 0 }, layer: "below",
        });
      }
    }
    const x = d.labels.map((_, i) => i);  // index axis; ticktext supplies the clock labels
    const traces = [
      { type: "bar", name: "Discharge", x: x, y: d.discharge_kw, marker: { color: FIRM },
        text: d.discharge_hover, textposition: "none", hovertemplate: "%{text}<extra></extra>" },
      { type: "bar", name: "Charge (from grid)", x: x, y: d.charge_kw.map(v => -v),
        marker: { color: T.cool }, text: d.charge_hover, textposition: "none",
        hovertemplate: "%{text}<extra></extra>" },
      { type: "scatter", mode: "lines", name: "Site load", x: x, y: d.site_load_kw,
        line: { color: MUTED, width: 1, dash: "dot" }, text: d.load_hover,
        hovertemplate: "%{text}<extra></extra>" },
      { type: "scatter", mode: "lines", name: "State of energy", x: x, y: d.soe_kwh, yaxis: "y2",
        line: { color: INK, width: 2 }, text: d.soe_hover, hovertemplate: "%{text}<extra></extra>" },
    ];
    const ticks = d.labels.map((l, i) => (i % 4 === 0 ? i : null)).filter(v => v !== null);
    const layout = baseLayout({
      barmode: "relative", shapes: redShapes, margin: { l: 56, r: 56, t: 8, b: 40 },
      xaxis: { title: "Time of day", tickvals: ticks, ticktext: ticks.map(i => d.labels[i]),
               gridcolor: LINE, color: MUTED },
      yaxis: { title: "Battery power (kW)", gridcolor: LINE, color: MUTED, zerolinecolor: LINE },
      yaxis2: { title: "SoE (kWh)", overlaying: "y", side: "right", showgrid: false, color: MUTED },
    });
    draw("chart-dispatch", traces, layout);
  }

  // ---- P50 vs P90 annual cashflows: draw-on-scroll area, payback year marked ----
  function cashflows(cf) {
    const x = cf.p50.map((_, i) => i);  // numeric year axis so the payback marker can sit between ticks
    const hover = (key) => cf.rows.map(r => r.year === "0" ? "Year 0 (capex) · " + r[key]
                                                            : "Year " + r.year + " · " + r[key]);
    // P50 is the filled expectation; P90 the conservative line a lender reads against it.
    const traces = [
      { type: "scatter", mode: "lines", name: "P50", x: x, y: cf.p50,
        line: { color: ACCENT, width: 2, shape: "spline", smoothing: 0.6 },
        fill: "tozeroy", fillcolor: T.accentWash,
        text: hover("p50"), hovertemplate: "%{text}<extra></extra>" },
      { type: "scatter", mode: "lines", name: "P90", x: x, y: cf.p90,
        line: { color: FIRM, width: 2, dash: "dot" },
        text: hover("p90"), hovertemplate: "%{text}<extra></extra>" },
    ];
    const shapes = [];
    const annotations = [];
    // The record's payback year (view-model field, pre-formatted label) marked on the axis.
    if (cf.payback_years !== null && cf.payback_years !== undefined) {
      shapes.push({ type: "line", xref: "x", x0: cf.payback_years, x1: cf.payback_years,
                    yref: "paper", y0: 0, y1: 1, line: { color: MUTED, width: 1.5, dash: "dash" } });
      annotations.push({ xref: "x", x: cf.payback_years, yref: "paper", y: 1, yanchor: "bottom",
                         text: "payback " + cf.payback_label, showarrow: false,
                         font: { color: T.text2, size: 10 } });
    }
    // Pin the y axis to the real series extent up front: the draw-on-scroll growth then
    // plays inside the final axes (Plotly.animate does not re-autorange).
    const all = cf.p50.concat(cf.p90);
    const lo = Math.min(0, Math.min.apply(null, all));
    const hi = Math.max.apply(null, all);
    const pad = (hi - lo) * 0.08 || 1;
    const layout = baseLayout({
      shapes: shapes, annotations: annotations, margin: { l: 56, r: 16, t: 22, b: 40 },
      xaxis: { title: "Year", color: MUTED, gridcolor: LINE, dtick: 5, zeroline: false },
      yaxis: { title: "Cashflow (£/yr)", tickprefix: "£", gridcolor: LINE, color: MUTED,
               zerolinecolor: MUTED, range: [lo - pad, hi + pad] },
    });
    drawOnScroll("chart-cashflows", traces, layout, [cf.p50, cf.p90]);
  }

  // ---- P90 DSCR by year + covenant --------------------------------------
  function dscr(d) {
    const x = d.by_year.map((_, i) => i + 1);
    const traces = [
      { type: "scatter", mode: "lines+markers", name: d.basis + " DSCR", x: x, y: d.by_year,
        line: { color: FIRM, width: 2 }, marker: { size: 6, color: FIRM },
        text: d.rows.map(r => "Year " + r.year + " · " + r.value),
        hovertemplate: "%{text}<extra></extra>" },
    ];
    const shapes = [];
    if (d.covenant !== null && d.covenant !== undefined) {
      shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: d.covenant,
                    y1: d.covenant, line: { color: ACCENT, width: 1.5, dash: "dash" } });
    }
    const layout = baseLayout({
      shapes: shapes, showlegend: false,
      xaxis: { title: "Operating year", color: MUTED, gridcolor: LINE, dtick: 1 },
      yaxis: { title: "DSCR (×)", gridcolor: LINE, color: MUTED },
      annotations: shapes.length ? [{ xref: "paper", x: 0.01, y: d.covenant, yanchor: "bottom",
        text: "covenant " + d.covenant_label, showarrow: false, font: { color: ACCENT, size: 10 } }] : [],
    });
    draw("chart-dscr", traces, layout);
  }

  // ---- value stack: firm (priced floor) vs gated upside, segments grow in ----
  function valueStack(v) {
    let fi = 0, ui = 0;
    const traces = v.segments.map((seg) => {
      const floor = seg.kind === "floor";
      const color = floor ? FLOOR_SHADES[fi++ % FLOOR_SHADES.length]
                          : UPSIDE_SHADES[ui++ % UPSIDE_SHADES.length];
      return {
        type: "bar", orientation: "h", name: seg.stream,
        x: [seg.revenue], y: [seg.category], marker: { color: color },
        text: [seg.stream + " · " + seg.revenue_label], textposition: "none",
        hovertemplate: "%{text}<extra></extra>",
      };
    });
    // Pin the x axis to the stacked category totals so the grow-in plays inside final axes.
    const totals = {};
    v.segments.forEach((seg) => { totals[seg.category] = (totals[seg.category] || 0) + seg.revenue; });
    const xmax = Math.max.apply(null, Object.keys(totals).map((k) => totals[k]));
    const layout = baseLayout({
      barmode: "stack", margin: { l: 96, r: 16, t: 8, b: 40 },
      xaxis: { title: "Annual revenue (£/yr)", tickprefix: "£", gridcolor: LINE, color: MUTED,
               range: [0, xmax > 0 ? xmax * 1.06 : 1] },
      yaxis: { color: INK, automargin: true },
    });
    // Segments grow from zero to their record values on first view (horizontal bars animate x).
    const el = document.getElementById("chart-valuestack");
    const motion = window.GridQMotion;
    if (!el) return;
    if (REDUCED || !motion || typeof motion.watch !== "function") {
      draw("chart-valuestack", traces, layout);
      return;
    }
    const seeded = traces.map((t) => Object.assign({}, t, { x: [0] }));
    try {
      window.Plotly.newPlot(el, seeded, layout, CONFIG).then(() => {
        motion.watch(el, () => {
          window.Plotly.animate(el,
            { data: traces.map((t) => ({ x: t.x })), traces: traces.map((_, i) => i) },
            { transition: { duration: 800, easing: "cubic-in-out" }, frame: { duration: 800 } });
        });
      });
    } catch (e) { draw("chart-valuestack", traces, layout); }
  }

  // ---- battery utilisation (animated donut) -----------------------------
  function utilisation(u) {
    const el = document.getElementById("chart-utilisation");
    if (!el || !u || !u.available) return;
    const palette = {
      "Charging": T.cool, "Discharging at peak": ACCENT,
      "Discharging off-peak": FIRM, "Idle / standby": STONE,
    };
    const labels = u.segments.map((s) => s.label);
    const values = u.segments.map((s) => s.count);
    const colors = u.segments.map((s) => palette[s.label] || STONE);
    const trace = {
      type: "pie", labels: labels, marker: { colors: colors, line: { color: EDGE, width: 2 } },
      hole: 0.58, sort: false, direction: "clockwise", rotation: 0,
      textinfo: "percent", textfont: { family: T.mono, size: 12, color: EDGE },
      hovertemplate: "%{label}: %{percent}<extra></extra>",
    };
    const layout = baseLayout({
      margin: { l: 8, r: 8, t: 8, b: 8 },
      legend: { orientation: "v", x: 1, xanchor: "right", y: 0.5, font: { size: 11 } },
    });
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try {
      if (reduce) {
        window.Plotly.newPlot(el, [Object.assign({ values: values }, trace)], layout, CONFIG);
        return;
      }
      const seed = values.map(() => 1);
      window.Plotly.newPlot(el, [Object.assign({ values: seed }, trace)], layout, CONFIG).then(function () {
        window.Plotly.animate(el, { data: [{ values: values }] },
          { transition: { duration: 1000, easing: "cubic-in-out" }, frame: { duration: 1000 } });
      });
    } catch (e) { /* fallback list in the card remains the source of truth */ }
  }

  // ---- public init ------------------------------------------------------
  function init(islands) {
    if (typeof window.Plotly === "undefined") return;  // fallback tables remain the truth
    const I = islands || {};
    if (I.surface && I.surface.available && I.surface.points && I.surface.points.length) {
      surface(I.surface);
      heatmap(I.surface);
    }
    if (I.dispatch && I.dispatch.available) dispatch(I.dispatch);
    if (I.finance && I.finance.cashflows && I.finance.cashflows.available) cashflows(I.finance.cashflows);
    if (I.finance && I.finance.dscr && I.finance.dscr.available) dscr(I.finance.dscr);
    if (I.value_stack && I.value_stack.available) valueStack(I.value_stack);
    if (I.utilisation && I.utilisation.available) utilisation(I.utilisation);
  }

  window.GridQCharts = { init: init };
})();
