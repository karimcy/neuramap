/* ═══════════════ LOAD GROWTH — the European "Rethinking Load Growth" panel ═══════════════
   Reads data/eu_load_growth.json (pipelines/eu_load_growth.py): per zone, per capability
   ceiling, per margin, per curtailment tolerance → GW of new flexible load, hours curtailed,
   events, share of curtailment hours retaining ≥50 % of the load, duration histogram, LDC.
   Controls: tolerance slider · ceiling · capability margin · (battery uptime reuses the
   existing #availSlider). Everything below re-renders from one state object. */
(function () {
  const $ = id => document.getElementById(id);
  const root = $('accLoadGrowth');
  if (!root) return;
  const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const fmtGW = v => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2));

  let D = null;
  const st = { tolIdx: 2, ceiling: 'duke', margin: '1.00', zone: null, year: 'pooled' };
  const CEIL_LABEL = { duke: 'Duke seasonal thresholds', period: 'all-time peak (optimistic)' };
  const ZONE_CENTRE = { // for fly-to; countries only, rough bounding boxes [S, W, N, E]
    GB: [49.9, -8.2, 58.7, 1.8], DE: [47.3, 5.9, 55.1, 15.0], FR: [42.3, -4.8, 51.1, 8.2], ES: [36.0, -9.3, 43.8, 3.3],
    PT: [36.9, -9.5, 42.2, -6.2], IT: [36.6, 6.6, 47.1, 18.5], PL: [49.0, 14.1, 54.8, 24.2], SE: [55.3, 11.0, 69.1, 24.2],
    NO: [57.9, 4.6, 71.2, 31.1], FI: [59.8, 20.6, 70.1, 31.6], DK: [54.5, 8.0, 57.8, 12.7], AT: [46.4, 9.5, 49.0, 17.2],
    HR: [42.4, 13.5, 46.6, 19.4], IE: [51.4, -10.5, 55.4, -6.0], NL: [50.7, 3.3, 53.6, 7.2], BE: [49.5, 2.5, 51.5, 6.4],
    CH: [45.8, 5.9, 47.8, 10.5], CZ: [48.5, 12.1, 51.1, 18.9],
  };

  function tol() { return D.limits[st.tolIdx]; }
  function cell(z) { return z.results[st.ceiling][st.margin][tol()]; }
  function zones() { return Object.keys(D.zones).filter(c => !D.zones[c].bidding_zone); }
  function allZones() { return Object.keys(D.zones); }

  function render() {
    if (!D) return;
    const t = tol();
    $('lgTolLabel').textContent = t;
    $('lgCeilNote').textContent = `${CEIL_LABEL[st.ceiling]} · ×${st.margin}`;
    // ── headline tiles (countries only, non-double-counted)
    const cs = zones();
    const totGW = cs.reduce((s, c) => s + cell(D.zones[c]).gw, 0);
    const peakGW = cs.reduce((s, c) => s + D.zones[c].peak_gw, 0);
    const hrs = cs.length ? cs.reduce((s, c) => s + cell(D.zones[c]).hours_yr, 0) / cs.length : 0;
    const ret = cs.map(c => cell(D.zones[c]).retain50_pct).filter(v => v != null);
    const retAvg = ret.length ? ret.reduce((a, b) => a + b, 0) / ret.length : null;
    const availLbl = (typeof dealAvail === 'string') ? dealAvail : '99.9%';
    const bat = cs.reduce((s, c) => {
      const b = D.zones[c].battery && D.zones[c].battery.duke_latest_year;
      return s + (b && b['8h'] && b['8h'][availLbl] != null ? b['8h'][availLbl] : 0);
    }, 0);
    const hasBat = cs.some(c => D.zones[c].battery && D.zones[c].battery.duke_latest_year);
    $('lgTiles').innerHTML =
      `<div class="kpi hero" title="Sum over ${cs.length} markets of the maximum constant new load whose curtailed energy is ≤ ${t} of its potential, against the ${CEIL_LABEL[st.ceiling]} ×${st.margin}. System-level; not nodal; not additive with published nodal headroom.">` +
      `<b>${fmtGW(totGW)}<span class="unit">GW</span></b><span>new flexible load · ≤${t} curtailment · ${cs.length} markets</span></div>` +
      `<div class="kpi sys" title="New load as a share of the summed market peaks (${peakGW.toFixed(0)} GW). Duke's US figure at 1% is about 17% of peak.">` +
      `<b>${(100 * totGW / peakGW).toFixed(1)}<span class="unit">%</span></b><span>of summed peak demand</span></div>` +
      `<div class="kpi" title="Average across markets of hours per year with any curtailment of the new load. Duke (US): 85 / 177 / 366 h at 0.25 / 0.5 / 1%.">` +
      `<b>${hrs.toFixed(0)}<span class="unit">h/yr</span></b><span>hours with some curtailment (market avg)</span></div>` +
      `<div class="kpi" title="Share of curtailment hours in which at least half of the new load keeps running (partial curtailment). Duke (US): 88%.">` +
      `<b>${retAvg == null ? '–' : retAvg.toFixed(0)}<span class="unit">%</span></b><span>of curtailed hours keep ≥50% of the load</span></div>` +
      (hasBat ? `<div class="kpi sys" title="Chronological battery simulation on the latest full year against the Duke thresholds: new load made effectively firm by an 8 h battery at the uptime chosen by the slider above.">` +
      `<b>${fmtGW(bat)}<span class="unit">GW</span></b><span>firm with 8 h battery · ≥${availLbl} uptime</span></div>` : '');
    try { window.dispatchEvent(new CustomEvent('lg:change', { detail: { gw: totGW, limit: t, ceiling: st.ceiling, margin: st.margin, n: cs.length } })); } catch (e) { /* no-op */ }
    // ── Duke comparison strip
    const dk = D.duke_reference;
    const ea = D.eu_avg || {};
    const us = D.us_same_engine && D.us_same_engine[t];
    $('lgDuke').innerHTML =
      `<b>Europe, ${cs.length} markets, ${D.years[0]}–${D.years[D.years.length - 1]}</b>: <b class="num">${fmtGW(totGW)} GW</b> at ${t} · ` +
      `<b class="num">${ea.hours_yr ? ea.hours_yr[t] : '–'} h/yr</b> curtailed · <b class="num">${ea.retain50_pct ? ea.retain50_pct[t] : '–'}%</b> of those hours keep ≥50% · events <b class="num">${ea.mean_event_h ? ea.mean_event_h[t] : '–'} h</b>.<br>` +
      `<b>US as Duke published it</b> (22 balancing authorities, 744 GW, 2016–24): <b class="num">${dk.gw[t] ?? '–'} GW</b> · <b class="num">${dk.hours_yr[t] ?? '–'} h/yr</b> · <b class="num">${dk.retain50_pct}%</b> · <b class="num">${dk.mean_event_h[t] ?? '–'} h</b>.` +
      (us ? `<br><b>US on this engine</b> (same EIA-930 input): <b class="num">${us.gw} GW</b> · <b class="num">${us.hours_yr} h/yr</b> · <b class="num">${us.retain50_pct}%</b> · <b class="num">${us.mean_event_h} h</b> — GW, hours and retention reproduce; the report's event-duration definition does not, so compare durations on this engine only.` : '');
    // ── zone ranking
    const rows = allZones().map(c => ({ c, z: D.zones[c], r: cell(D.zones[c]) })).sort((a, b) => b.r.gw - a.r.gw);
    const maxGW = rows.length ? rows[0].r.gw : 1;
    $('lgRank').innerHTML = rows.map((o, i) =>
      `<div class="rank-row lg-row${st.zone === o.c ? ' sel' : ''}" data-z="${esc(o.c)}" role="button" tabindex="0" title="${esc(o.z.name)} · ${o.z.years[0]}–${o.z.years[o.z.years.length - 1]} · peak ${o.z.peak_gw} GW · load factor ${o.z.load_factor}">` +
      `<span class="rk">${i + 1}</span>` +
      `<span class="lg-name">${esc(o.z.bidding_zone ? o.c : o.z.name)}<i class="lg-bar" style="width:${(100 * o.r.gw / maxGW).toFixed(1)}%"></i></span>` +
      `<span class="lg-val num"><b>${fmtGW(o.r.gw)}</b> GW <small>${o.r.pct_peak}%</small></span></div>`).join('');
    root.querySelectorAll('.lg-row').forEach(el => {
      const go = () => { st.zone = el.dataset.z; render(); const bb = ZONE_CENTRE[st.zone]; if (bb && typeof flyTo === 'function') { try { flyTo(bb); } catch (e) { /* ignore */ } } };
      el.onclick = go; el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    });
    // ── selected zone detail
    if (!st.zone || !D.zones[st.zone]) st.zone = D.zones.GB ? 'GB' : allZones()[0];
    renderZone(D.zones[st.zone]);
  }

  function renderZone(z) {
    const r = cell(z);
    const years = z.years;
    $('lgZoneTitle').textContent = `${z.name}${z.bidding_zone ? ' (bidding zone)' : ''} · ${years[0]}–${years[years.length - 1]}`;
    const py = z.per_year[st.ceiling][tol()];
    const pyStr = Object.keys(py).map(y => `${y}: ${fmtGW(py[y])}`).join(' · ');
    $('lgZoneStats').innerHTML =
      `<div><span class="lbl">new load</span><b class="num">${fmtGW(r.gw)} GW</b> <small>${r.pct_peak}% of ${z.peak_gw} GW peak</small></div>` +
      `<div><span class="lbl">curtailed</span><b class="num">${r.hours_yr} h/yr</b> <small>${r.events_yr} events · mean ${r.mean_event_h} h · max ${r.max_event_h} h</small></div>` +
      `<div><span class="lbl">retains ≥50 / 75 / 90%</span><b class="num">${r.retain50_pct == null ? '–' : r.retain50_pct + '%'}</b> <small>/ ${r.retain75_pct ?? '–'}% / ${r.retain90_pct ?? '–'}% of curtailed hours</small></div>` +
      `<div><span class="lbl">curtailed energy</span><b class="num">${r.winter_share_pct ?? '–'}%</b> <small>winter (Dec–Feb) · ${r.summer_share_pct ?? '–'}% summer (Jun–Aug)</small></div>` +
      `<div><span class="lbl">thresholds</span><small class="num">summer ${z.thresholds_gw.summer_window} GW (Apr–Oct${tw(z, 'summer_window')}) · winter ${z.thresholds_gw.winter_window} GW (Nov–Mar${tw(z, 'winter_window')})</small></div>` +
      `<div><span class="lbl">by year</span><small class="num">${pyStr}</small></div>`;
    $('lgChart').innerHTML = ldcSvg(z, r) + curveSvg(z, r) + monthSvg(z, r) + histSvg(z, r);
  }

  // ── load-duration curve with ceiling line and headroom band (inline SVG, no library)
  function ldcSvg(z, r) {
    const W = 320, H = 150, pl = 34, pr = 6, pt = 10, pb = 20;
    const ldc = st.year === 'pooled' || !z.ldc_by_year_gw[st.year] ? z.ldc_gw : z.ldc_by_year_gw[st.year];
    const m = parseFloat(st.margin);
    const caps = st.ceiling === 'duke' ? [['winter', z.thresholds_gw.winter_window * m], ['summer', z.thresholds_gw.summer_window * m]] : [['peak', z.peak_gw * m]];
    const capGW = Math.max(...caps.map(c => c[1]));
    const yMax = Math.max(capGW, ldc[0]) * 1.04, yMin = 0;
    const x = i => pl + (i / (ldc.length - 1)) * (W - pl - pr);
    const y = v => pt + (1 - (v - yMin) / (yMax - yMin)) * (H - pt - pb);
    const line = ldc.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    // headroom band: between load and min(cap, load + ΔP) — the part of ΔP that fits under the ceiling
    const band = ldc.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(Math.min(capGW, v + r.gw)).toFixed(1)}`).join(' ') +
      ' ' + ldc.slice().reverse().map((v, j) => { const i = ldc.length - 1 - j; return `L${x(i).toFixed(1)},${y(v).toFixed(1)}`; }).join(' ') + ' Z';
    const ticks = [0, 25, 50, 75, 100];
    let s = `<svg class="lg-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Load duration curve for ${esc(z.name)} with the capability ceiling and the new-load band">`;
    s += `<path d="${band}" class="lg-band"/>`;
    caps.forEach(([lab, v]) => {
      s += `<line x1="${pl}" x2="${W - pr}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="lg-cap"/>`;
      s += `<text x="${W - pr}" y="${(y(v) - 3).toFixed(1)}" class="lg-txt" text-anchor="end">${lab} threshold ${v.toFixed(1)} GW</text>`;
    });
    s += `<path d="${line}" class="lg-ldc"/>`;
    ticks.forEach(p => { const xx = x(Math.round((ldc.length - 1) * p / 100)); s += `<text x="${xx.toFixed(1)}" y="${H - 5}" class="lg-tick" text-anchor="middle">${p}%</text>`; });
    [0, 0.5, 1].forEach(f => { const v = yMin + f * (yMax - yMin); s += `<text x="${pl - 4}" y="${(y(v) + 3).toFixed(1)}" class="lg-tick" text-anchor="end">${v.toFixed(0)}</text>`; });
    s += `<text x="${pl}" y="${pt - 1}" class="lg-txt">GW · load duration (share of hours)</text>`;
    s += `</svg>`;
    return s;
  }
  function histSvg(z, r) {
    const labels = D.hist_labels, h = r.hist || [];
    const W = 320, H = 74, pl = 34, pr = 6, pt = 14, pb = 18;
    const n = labels.length, max = Math.max(1, ...h);
    const bw = (W - pl - pr) / n;
    let s = `<svg class="lg-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Curtailment event durations">`;
    s += `<text x="${pl}" y="${pt - 4}" class="lg-txt">events by duration · ${years(z)} pooled</text>`;
    h.forEach((v, i) => {
      const bh = (v / max) * (H - pt - pb);
      s += `<rect x="${(pl + i * bw + 3).toFixed(1)}" y="${(H - pb - bh).toFixed(1)}" width="${(bw - 6).toFixed(1)}" height="${bh.toFixed(1)}" rx="2" class="lg-hbar"/>`;
      if (v) s += `<text x="${(pl + i * bw + bw / 2).toFixed(1)}" y="${(H - pb - bh - 2).toFixed(1)}" class="lg-tick" text-anchor="middle">${v}</text>`;
      s += `<text x="${(pl + i * bw + bw / 2).toFixed(1)}" y="${H - 5}" class="lg-tick" text-anchor="middle">${esc(labels[i])}</text>`;
    });
    s += `</svg>`;
    return s;
  }
  // per-month share of curtailed energy (where in the year the new load is cut)
  function monthSvg(z, r) {
    const me = r.month_energy_pct; if (!me) return '';
    const W = 320, H = 86, pl = 8, pr = 8, pt = 14, pb = 16, gap = 3;
    const n = 12, bw = (W - pl - pr - gap * (n - 1)) / n, max = Math.max(1, ...me);
    const names = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
    const winter = m => m === 11 || m <= 1, summer = m => m >= 5 && m <= 7;
    let s = `<svg class="lg-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Curtailed energy by month for ${esc(z.name)}">`;
    me.forEach((v, i) => {
      const x = pl + i * (bw + gap), h = (v / max) * (H - pt - pb), cls = winter(i) ? 'lg-hbar lg-m-winter' : summer(i) ? 'lg-hbar lg-m-summer' : 'lg-hbar lg-m-shoulder';
      s += `<rect x="${x.toFixed(1)}" y="${(H - pb - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" class="${cls}"><title>${names[i]}: ${v}% of curtailed energy · ${r.month_hours_yr ? r.month_hours_yr[i] : '–'} h/yr</title></rect>`;
      if (v >= 8) s += `<text x="${(x + bw / 2).toFixed(1)}" y="${(H - pb - h - 2).toFixed(1)}" class="lg-tick" text-anchor="middle">${Math.round(v)}</text>`;
      s += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 4}" class="lg-tick" text-anchor="middle">${names[i]}</text>`;
    });
    const shoulder = me.reduce((a, v, i) => a + (winter(i) || summer(i) ? 0 : v), 0);
    s += `<text x="${pl}" y="${pt - 4}" class="lg-txt">curtailed energy by month · ${Math.round(shoulder)}% in shoulder months</text></svg>`;
    return s;
  }
  // Duke Figure 9: curtailment rate vs load addition as % of peak (0.25% steps)
  function curveSvg(z, r) {
    const cv = z.curve && z.curve[st.ceiling]; if (!cv) return '';
    const W = 320, H = 120, pl = 34, pr = 6, pt = 12, pb = 18;
    const xs = cv.pct_peak, ys = cv.rate_pct;
    const xMax = 30, yMax = 10;
    const x = v => pl + (Math.min(v, xMax) / xMax) * (W - pl - pr);
    const y = v => pt + (1 - Math.sqrt(Math.min(v, yMax) / yMax)) * (H - pt - pb);
    let path = '';
    xs.forEach((v, i) => { if (ys[i] <= yMax) path += `${path ? 'L' : 'M'}${x(v).toFixed(1)},${y(ys[i]).toFixed(1)} `; });
    let s = `<svg class="lg-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Curtailment rate versus load addition for ${esc(z.name)}">`;
    [0.25, 0.5, 1, 5].forEach(l => { s += `<line x1="${pl}" x2="${W - pr}" y1="${y(l).toFixed(1)}" y2="${y(l).toFixed(1)}" class="lg-grid"/><text x="${pl - 4}" y="${(y(l) + 3).toFixed(1)}" class="lg-tick" text-anchor="end">${l}%</text>`; });
    s += `<path d="${path}" class="lg-ldc"/>`;
    s += `<circle cx="${x(r.pct_peak).toFixed(1)}" cy="${y(parseFloat(tol())).toFixed(1)}" r="3.5" class="lg-dot"/>`;
    [0, 10, 20, 30].forEach(p => { s += `<text x="${x(p).toFixed(1)}" y="${H - 5}" class="lg-tick" text-anchor="middle">${p}%</text>`; });
    s += `<text x="${pl}" y="${pt - 2}" class="lg-txt">curtailment rate vs new load (% of peak) · dot = chosen limit</text></svg>`;
    return s;
  }
  const MN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const tw = (z, k) => { const w = z.threshold_when && z.threshold_when[k]; return w && w.year ? `, set ${MN[w.month - 1]} ${w.year}` : ''; };
  const years = z => `${z.years[0]}–${z.years[z.years.length - 1]}`;

  // ── controls
  $('lgTol').oninput = e => { st.tolIdx = +e.target.value; render(); };
  root.querySelectorAll('#lgCeil button').forEach(b => b.onclick = () => { root.querySelectorAll('#lgCeil button').forEach(x => x.classList.remove('on')); b.classList.add('on'); st.ceiling = b.dataset.c; render(); });
  root.querySelectorAll('#lgMargin button').forEach(b => b.onclick = () => { root.querySelectorAll('#lgMargin button').forEach(x => x.classList.remove('on')); b.classList.add('on'); st.margin = b.dataset.m; render(); });
  const avail = $('availSlider');
  if (avail) avail.addEventListener('input', () => setTimeout(render, 0));
  // year picker for the LDC
  root.addEventListener('click', e => {
    const b = e.target.closest('#lgYear button'); if (!b) return;
    root.querySelectorAll('#lgYear button').forEach(x => x.classList.remove('on')); b.classList.add('on'); st.year = b.dataset.y; render();
  });

  fetch('data/eu_load_growth.json').then(r => r.ok ? r.json() : null).then(d => {
    if (!d || !d.zones || !Object.keys(d.zones).length) { root.hidden = true; return; }
    D = d;
    $('lgTol').max = String(D.limits.length - 1);
    st.tolIdx = Math.min(st.tolIdx, D.limits.length - 1);
    const ys = D.years || [];
    $('lgYear').innerHTML = `<button data-y="pooled" class="on">pooled</button>` + ys.map(y => `<button data-y="${y}">${y}</button>`).join('');
    $('lgMeta').textContent = `${D.years[0]}–${D.years[D.years.length - 1]} measured load · ${Object.keys(D.zones).length} zones · generated ${D.generated}`;
    root.hidden = false;
    render();
  }).catch(() => { root.hidden = true; });
})();
