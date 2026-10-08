/* Story page — "Start here". Reads data/eu_load_growth.json (Duke-method results), data/pockets.json
   (node table), data/pt_profiles.json (metered substations), data/gb_dno.geojson (GB operator areas).
   One global control (curtailment limit) drives sections 01–03. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const f1 = v => (v == null || isNaN(v) ? '–' : v >= 100 ? Math.round(v).toLocaleString('en-GB') : v >= 10 ? v.toFixed(0) : v.toFixed(1));
  const UPTIME = ['100%', '99.99%', '99.95%', '99.9%', '99.5%', '99%'];
  const ORANGE = ['#3a2a17', '#6b4318', '#a35f17', '#d97a15', '#ff9b1f', '#ffc46b'];   // sequential, dark → bright on dark ground
  const STATUS = { live: ['#3ddc97', 'Flexible demand connections offered'], law: ['#6fb8ff', 'In law'], restrictive: ['#ff6b6b', 'Restrictive for BTM batteries'], unverified: ['#8a93a3', 'Not verified'] };
  const ISO_N = { 276: 'DE', 250: 'FR', 724: 'ES', 620: 'PT', 380: 'IT', 616: 'PL', 752: 'SE', 578: 'NO', 246: 'FI', 826: 'GB', 208: 'DK', 40: 'AT', 191: 'HR', 372: 'IE', 528: 'NL', 56: 'BE', 756: 'CH', 203: 'CZ' };
  const NAMES = { DE: 'Germany', FR: 'France', ES: 'Spain', PT: 'Portugal', IT: 'Italy', PL: 'Poland', SE: 'Sweden', NO: 'Norway', FI: 'Finland', GB: 'Great Britain', DK: 'Denmark', AT: 'Austria', HR: 'Croatia', IE: 'Ireland', NL: 'Netherlands', BE: 'Belgium', CH: 'Switzerland', CZ: 'Czechia' };
  const DNO_RULE = { UKPN: 'live', WPD: 'live', SSE: 'live', ENWL: 'live', SPEN: 'unverified', NPG: 'unverified' };
  const st = { lim: '1%', market: 'GB', metric: 'pct', up: '99.9%', band: 'all', land: false };
  let D = null, P = null, PT = null, WORLD = null;

  // ── basemap shared by the three maps (same no-key Esri tiles as the atlas)
  function baseMap(el, opts) {
    const m = L.map(el, Object.assign({ zoomControl: true, scrollWheelZoom: false, attributionControl: true, preferCanvas: true }, opts || {}));
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      { maxNativeZoom: 16, maxZoom: 19, attribution: 'Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors' }).addTo(m);
    m.createPane('labels'); m.getPane('labels').style.zIndex = 650; m.getPane('labels').style.pointerEvents = 'none';
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
      { maxNativeZoom: 16, maxZoom: 19, pane: 'labels', opacity: 0.8 }).addTo(m);
    m.on('click', () => m.scrollWheelZoom.enable());
    m.on('mouseout', () => m.scrollWheelZoom.disable());
    return m;
  }
  const countries = () => Object.keys(D.zones).filter(c => !D.zones[c].bidding_zone);
  const res = (c, lim) => D.zones[c].results.duke['1.00'][lim || st.lim];

  // ═════════ HERO ═════════
  function renderHero() {
    const cs = countries();
    const gw = cs.reduce((a, c) => a + res(c).gw, 0);
    const peak = cs.reduce((a, c) => a + D.zones[c].peak_gw, 0);
    const hrs = cs.reduce((a, c) => a + (res(c).hours_yr || 0), 0) / cs.length;
    const r50 = cs.reduce((a, c) => a + (res(c).retain50_pct || 0), 0) / cs.length;
    $('heroGW').textContent = `${Math.round(gw)} GW`;
    const dk = D.duke_reference.gw[st.lim];
    $('heroTiles').innerHTML =
      tile(`${Math.round(gw)}<small>GW</small>`, `new load across ${cs.length} markets, giving up ${st.lim} of its energy`, true) +
      tile(`${(100 * gw / peak).toFixed(0)}<small>%</small>`, `of those markets' combined ${Math.round(peak)} GW peak`) +
      tile(`${Math.round(hrs)}<small>h/yr</small>`, `hours with any step-down, average market (out of 8,760)`) +
      tile(`${Math.round(r50)}<small>%</small>`, `of those hours still deliver at least half the power`);
    $('heroFine').innerHTML = `Measured hourly load ${D.years[0]}–${D.years[D.years.length - 1]}, Duke University's <i>Rethinking Load Growth</i> method.` +
      (dk ? ` Duke found ${dk} GW for the US (744 GW peak) at the same ${st.lim}.` : '') + ' Method and caveats at the end of the page.';
  }
  const tile = (b, s, hl) => `<div class="st-tile${hl ? ' hl' : ''}"><b class="num">${b}</b><span>${s}</span></div>`;

  function limitControl() {
    const shown = D.limits.filter(l => ['0.5%', '1%', '2%', '3%', '5%'].includes(l));
    $('limitSeg').innerHTML = shown.map(l => `<button data-l="${l}" class="${l === st.lim ? 'on' : ''}">${l}</button>`).join('');
    $('limitSeg').onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      st.lim = b.dataset.l;
      [...$('limitSeg').children].forEach(x => x.classList.toggle('on', x === b));
      renderHero(); renderPeak(); renderMap(); renderBattery();
    };
  }

  // ═════════ 01 PEAK ═════════
  function renderPeak() {
    const cs = countries().sort((a, b) => D.zones[b].peak_gw - D.zones[a].peak_gw);
    if (!$('peakMarket').children.length) {
      $('peakMarket').innerHTML = cs.map(c => `<button data-c="${c}" class="${c === st.market ? 'on' : ''}">${c}</button>`).join('');
      $('peakMarket').onclick = e => { const b = e.target.closest('button'); if (!b) return; st.market = b.dataset.c; [...$('peakMarket').children].forEach(x => x.classList.toggle('on', x === b)); renderPeak(); };
    }
    const z = D.zones[st.market], r = res(st.market);
    const ldc = z.ldc_gw, thrW = z.thresholds_gw.winter_window, thrS = z.thresholds_gw.summer_window, cap = Math.max(thrW, thrS);
    const L = r.gw;
    const W = 720, H = 380, pl = 46, pr = 14, pt = 18, pb = 40;
    const xs = i => Math.sqrt(i / (ldc.length - 1));             // stretch the busy left edge
    const x = s => pl + s * (W - pl - pr);
    const yMax = Math.ceil((cap + L) * 1.06 / 10) * 10, yMin = Math.max(0, Math.floor(Math.min(...ldc) * 0.8 / 10) * 10);
    const y = v => pt + (1 - (v - yMin) / (yMax - yMin)) * (H - pt - pb);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Load duration curve for ${esc(z.name)} with the new load band">`;
    for (let g = yMin; g <= yMax; g += (yMax - yMin) / 4) s += `<line x1="${pl}" x2="${W - pr}" y1="${y(g)}" y2="${y(g)}" class="sv-grid"/><text x="${pl - 6}" y="${y(g) + 4}" class="sv-tick" text-anchor="end">${Math.round(g)}</text>`;
    // band: existing load → existing + L, split at the capability line
    let top = '', base = '', over = '';
    ldc.forEach((v, i) => { const X = x(xs(i)).toFixed(1); top += `${i ? 'L' : 'M'}${X},${y(Math.min(v + L, cap)).toFixed(1)} `; });
    for (let i = ldc.length - 1; i >= 0; i--) base += `L${x(xs(i)).toFixed(1)},${y(ldc[i]).toFixed(1)} `;
    s += `<path d="${top}${base}Z" fill="#ff9b1f" fill-opacity=".55"/>`;
    const overIdx = ldc.map((v, i) => [v, i]).filter(([v]) => v + L > cap);
    if (overIdx.length) {
      let p = ''; overIdx.forEach(([v, i], k) => { p += `${k ? 'L' : 'M'}${x(xs(i)).toFixed(1)},${y(v + L).toFixed(1)} `; });
      for (let k = overIdx.length - 1; k >= 0; k--) p += `L${x(xs(overIdx[k][1])).toFixed(1)},${y(cap).toFixed(1)} `;
      s += `<path d="${p}Z" fill="#ff6b6b" fill-opacity=".9"/>`;
    }
    let lp = ''; ldc.forEach((v, i) => { lp += `${i ? 'L' : 'M'}${x(xs(i)).toFixed(1)},${y(v).toFixed(1)} `; });
    s += `<path d="${lp}" fill="none" stroke="#a8d2ff" stroke-width="2"/>`;
    s += `<line x1="${pl}" x2="${W - pr}" y1="${y(cap)}" y2="${y(cap)}" stroke="#ffffff" stroke-dasharray="5 4" stroke-width="1.5"/>`;
    s += `<text x="${W - pr}" y="${y(cap) - 6}" class="sv-lab" text-anchor="end">highest load served, ${cap.toFixed(1)} GW (winter)</text>`;
    if (thrS < thrW) s += `<line x1="${pl}" x2="${W - pr}" y1="${y(thrS)}" y2="${y(thrS)}" stroke="#ffffff" stroke-opacity=".35" stroke-dasharray="2 4"/><text x="${W - pr}" y="${y(thrS) + 14}" class="sv-tick" text-anchor="end">summer threshold ${thrS.toFixed(1)} GW</text>`;
    [[0, '0'], [0.01, '1%'], [0.05, '5%'], [0.25, '25%'], [0.5, '50%'], [1, '100%']].forEach(([v, t]) => { s += `<text x="${x(Math.sqrt(v))}" y="${H - pb + 18}" class="sv-tick" text-anchor="middle">${t}</text>`; });
    s += `<text x="${(pl + W - pr) / 2}" y="${H - 6}" class="sv-lab" text-anchor="middle">share of hours in the year, busiest first (left edge stretched)</text>`;
    s += `<text x="${pl}" y="${pt - 6}" class="sv-lab">GW</text></svg>`;
    $('peakChart').innerHTML = s;
    $('peakLegend').innerHTML = `<span><i style="background:#a8d2ff"></i>existing load, ${D.years[0]}–${D.years[D.years.length - 1]}</span><span><i style="background:#ff9b1f"></i>new load, ${f1(L)} GW</span><span><i style="background:#ff6b6b"></i>hours it steps down</span><span>· drawn against the winter threshold; the summer one applies April–October</span>`;
    $('peakSentence').textContent = `In ${z.name}, ${f1(L)} GW of new load fits, ${r.pct_peak}% of the peak. It steps down in about ${Math.round(r.hours_yr)} hours a year. In ${Math.round(r.retain50_pct)}% of those hours it keeps more than half its power.`;
  }

  // ═════════ 02 MAP ═════════
  let euMap = null, euLayer = null;
  function renderMap() {
    const cs = countries();
    const val = c => st.metric === 'pct' ? res(c).pct_peak : res(c).gw;
    const vmax = Math.max(...cs.map(val));
    const col = v => ORANGE[Math.min(ORANGE.length - 1, Math.floor((v / vmax) * (ORANGE.length - 0.001)))];
    if (!euMap && WORLD) {
      euMap = baseMap('euMap', { center: [53, 10], zoom: 4, minZoom: 3 });
    }
    if (euMap && WORLD) {
      if (euLayer) euMap.removeLayer(euLayer);
      euLayer = L.geoJSON(WORLD, {
        filter: f => ISO_N[+f.id],
        style: f => { const c = ISO_N[+f.id]; const has = D.zones[c] && !D.zones[c].bidding_zone; return { color: '#0b0f14', weight: 1, fillColor: has ? col(val(c)) : '#2a323d', fillOpacity: has ? 0.85 : 0.5 }; },
        onEachFeature: (f, l) => {
          const c = ISO_N[+f.id], z = D.zones[c];
          if (!z || z.bidding_zone) { l.bindTooltip(`<b>${esc(NAMES[c] || c)}</b><br><span class="m">no hourly series yet</span>`, { className: 'st-tt', sticky: true }); return; }
          const r = res(c);
          l.bindTooltip(`<b>${esc(z.name)}</b><br><span class="num">${f1(r.gw)} GW</span> new load · <span class="num">${r.pct_peak}%</span> of ${f1(z.peak_gw)} GW peak<br>` +
            `<span class="m">${Math.round(r.hours_yr)} h/yr stepping down · ${Math.round(r.retain50_pct)}% of those keep ≥ half</span>`, { className: 'st-tt', sticky: true });
          l.on('mouseover', () => { l.setStyle({ weight: 2, color: '#fff' }); hot(c, true); });
          l.on('mouseout', () => { euLayer.resetStyle(l); hot(c, false); });
        },
      }).addTo(euMap);
    }
    const sorted = cs.slice().sort((a, b) => val(b) - val(a));
    $('mapRank').innerHTML = sorted.map(c => `<div class="st-rrow" data-c="${c}"><span class="n">${esc(D.zones[c].name.replace(' (DE-LU)', ''))}</span><span class="bar"><span style="width:${(100 * val(c) / vmax).toFixed(1)}%"></span></span><span class="v">${st.metric === 'pct' ? res(c).pct_peak + '%' : f1(res(c).gw) + ' GW'}</span></div>`).join('');
    $('mapLegend').innerHTML = `<span>0</span><span class="ramp">${ORANGE.map(c => `<span style="background:${c}"></span>`).join('')}</span><span>${st.metric === 'pct' ? vmax.toFixed(0) + '% of peak' : f1(vmax) + ' GW'}</span><span><i style="background:#2a323d"></i>no hourly series yet</span><span>· at ${st.lim} curtailment</span>`;
  }
  function hot(c, on) { const r = document.querySelector(`.st-rrow[data-c="${c}"]`); if (r) r.classList.toggle('hot', on); }

  // ═════════ 03 BATTERY ═════════
  function renderBattery() {
    const cs = countries().filter(c => D.zones[c].battery && D.zones[c].battery.duke_latest_year);
    const bt = D.battery_totals_gw || {};
    const flexTot = countries().reduce((a, c) => a + res(c).gw, 0);
    $('batTiles').innerHTML = ['2h', '4h', '8h'].map((d, i) => tile(`${Math.round(bt[d] ? bt[d][st.up] : 0)}<small>GW</small>`, `firm with a ${d.replace('h', '-hour')} battery`, i === 2)).join('');
    const rows = cs.map(c => ({ c, flex: res(c).gw, b2: D.zones[c].battery.duke_latest_year['2h'][st.up], b8: D.zones[c].battery.duke_latest_year['8h'][st.up] })).sort((a, b) => b.b8 - a.b8);
    const vmax = Math.max(...rows.map(r => Math.max(r.flex, r.b8)));
    const W = 720, rowH = 30, pl = 120, pr = 60, pt = 8, H = pt + rows.length * rowH + 10;
    const x = v => pl + (v / vmax) * (W - pl - pr);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Flexible versus battery-firmed new load by market">`;
    rows.forEach((r, i) => {
      const y0 = pt + i * rowH;
      s += `<text x="${pl - 10}" y="${y0 + 17}" class="sv-lab" text-anchor="end">${esc(D.zones[r.c].name.replace(' (DE-LU)', ''))}</text>`;
      s += `<rect x="${pl}" y="${y0 + 3}" width="${Math.max(1, x(r.b8) - pl)}" height="10" rx="2" fill="#6fb8ff"><title>${esc(r.c)}: ${f1(r.b8)} GW firm with 8 h battery at ${st.up}</title></rect>`;
      s += `<rect x="${pl}" y="${y0 + 15}" width="${Math.max(1, x(r.flex) - pl)}" height="10" rx="2" fill="#ff9b1f"><title>${esc(r.c)}: ${f1(r.flex)} GW flexible at ${st.lim}</title></rect>`;
      s += `<text x="${x(Math.max(r.b8, r.flex)) + 6}" y="${y0 + 17}" class="sv-val">${f1(r.b8)} / ${f1(r.flex)}</text>`;
    });
    s += '</svg>';
    $('batChart').innerHTML = s;
    $('batLegend').innerHTML = `<span><i style="background:#6fb8ff"></i>firm with an 8-hour battery, ${st.up} uptime</span><span><i style="background:#ff9b1f"></i>flexible, no battery, ${st.lim} curtailment</span><span>· GW, total ${f1(bt['8h'] ? bt['8h'][st.up] : 0)} vs ${f1(flexTot)}</span>`;
  }

  // ═════════ 04 POCKETS ═════════
  let pMap = null, pLayer = null;
  const BANDS = [['all', 'All ≥5 MW', 5, 1e9], ['5–20', '5–20 MW', 5, 20], ['20–50', '20–50 MW', 20, 50], ['50–100', '50–100 MW', 50, 100], ['100+', '100+ MW', 100, 1e9]];
  const BCOL = { '5–20': '#ffc46b', '20–50': '#ff9b1f', '50–100': '#d97a15', '100+': '#a35f17' };
  const bandOf = mw => mw < 20 ? '5–20' : mw < 50 ? '20–50' : mw < 100 ? '50–100' : '100+';
  function renderPockets() {
    if (!P) return;
    const C = Object.fromEntries(P.cols.map((k, i) => [k, i]));
    if (!$('pocketBand').children.length) {
      $('pocketBand').innerHTML = BANDS.map(b => `<button data-b="${b[0]}" class="${b[0] === st.band ? 'on' : ''}">${b[1]}</button>`).join('');
      $('pocketBand').onclick = e => { const b = e.target.closest('button'); if (!b) return; st.band = b.dataset.b; [...$('pocketBand').children].forEach(x => x.classList.toggle('on', x === b)); renderPockets(); };
      $('pocketLand').onchange = e => { st.land = e.target.checked; renderPockets(); };
    }
    const band = BANDS.find(b => b[0] === st.band);
    const rows = P.nodes.filter(r => r[C.mw] >= band[2] && r[C.mw] < band[3] && (!st.land || r[C.plots] > 0)).sort((a, b) => b[C.mw] - a[C.mw]);   // big first so small pockets draw on top
    if (!pMap) pMap = baseMap('pocketMap', { center: [54, 4], zoom: 4, minZoom: 3 });
    if (pLayer) pMap.removeLayer(pLayer);
    const rend = L.canvas({ padding: 0.3 });
    pLayer = L.layerGroup(rows.map(r => {
      const mw = r[C.mw], land = r[C.plots] > 0;
      const m = L.circleMarker([r[C.lat], r[C.lon]], { renderer: rend, radius: Math.max(2.5, Math.min(11, Math.sqrt(mw) * 0.9)), weight: land ? 1.6 : 0, color: '#ffffff', opacity: 0.9, fillColor: BCOL[bandOf(mw)], fillOpacity: 0.8 });
      m.bindPopup(() => pocketPopup(r, C), { maxWidth: 320 });
      return m;
    })).addTo(pMap);
    // table
    const by = {};
    P.nodes.forEach(r => { const cc = r[C.cc], b = by[cc] || (by[cc] = { n: 0, small: 0, land: 0, smallLand: 0, mw: 0 }); b.n++; b.mw += r[C.mw]; if (r[C.mw] < 50) b.small++; if (r[C.plots] > 0) { b.land++; if (r[C.mw] < 50) b.smallLand++; } });
    const ccs = Object.keys(by).sort((a, b) => by[b].n - by[a].n);
    const tot = ccs.reduce((a, c) => { Object.keys(by[c]).forEach(k => a[k] = (a[k] || 0) + by[c][k]); return a; }, {});
    $('pocketTable').innerHTML = '<table><thead><tr><th>Country</th><th>nodes ≥5 MW</th><th>of which &lt;50 MW</th><th>&lt;50 MW with land</th></tr></thead><tbody>' +
      ccs.map(c => `<tr><td>${esc(NAMES[c] || c)}</td><td>${by[c].n.toLocaleString('en-GB')}</td><td>${by[c].small.toLocaleString('en-GB')}</td><td>${by[c].smallLand.toLocaleString('en-GB')}</td></tr>`).join('') +
      `<tr class="tot"><td>Total</td><td>${tot.n.toLocaleString('en-GB')}</td><td>${tot.small.toLocaleString('en-GB')}</td><td>${tot.smallLand.toLocaleString('en-GB')}</td></tr></tbody></table>` +
      `<p class="st-fine">Showing ${rows.length.toLocaleString('en-GB')} nodes. Node-level headroom is published only where operators publish it: GB, Spain and the Nordics at distribution level, the rest mainly at transmission level.</p>`;
    $('pocketLegend').innerHTML = Object.entries(BCOL).map(([k, c]) => `<span><i style="background:${c};border-radius:50%"></i>${k} MW</span>`).join('') + '<span><i style="background:transparent;border:1.5px solid #fff;border-radius:50%"></i>industrial land within 3 km</span>';
  }
  function pocketPopup(r, C) {
    const rule = P.rules ? Object.values(P.rules).find(x => x.status === r[C.rule]) : null;
    let s = `<b>${esc(r[C.name])}</b> <span class="num" style="color:var(--mut)">${esc(r[C.cc])}${r[C.kv] ? ' · ' + r[C.kv] + ' kV' : ''}</span><br>` +
      `<span class="num" style="font-size:20px;color:var(--accent)">${f1(r[C.mw])} MW</span> <span style="color:var(--mut)">published demand headroom</span>`;
    if (r[C.up] === 'tight') s += `<br><span style="color:#ffb35c">⚠ upstream tighter: about ${f1(r[C.eff_mw])} MW deliverable</span>`;
    else if (r[C.up] === 'ok') s += `<br><span style="color:#3ddc97">✓ upstream substation has room</span>`;
    if (r[C.flex1] != null) s += `<br>flexible at 1% curtailment: <b class="num">${f1(r[C.flex1])} MW</b> · 8 h battery: <b class="num">${f1(r[C.firm8h])} MW</b> <span style="color:var(--mut)">(metered)</span>`;
    s += r[C.plots] > 0 ? `<br>${r[C.plots]} industrial plot${r[C.plots] > 1 ? 's' : ''} within 3 km, largest ${f1(r[C.ha_max])} ha, nearest ${f1(r[C.km_min])} km` : '<br><span style="color:var(--mut)">no mapped industrial land within 3 km</span>';
    const sc = STATUS[r[C.rule]] || STATUS.unverified;
    s += `<br><span style="color:${sc[0]}">●</span> ${esc(sc[1])}`;
    s += `<br><a href="index.html" target="_blank" rel="noopener">Open the atlas</a> and search “${esc(r[C.name])}”`;
    return s;
  }

  // ═════════ 05 RULES ═════════
  function renderRules(dno) {
    const rules = Object.entries(P.rules).sort((a, b) => ['live', 'law', 'restrictive', 'unverified'].indexOf(a[1].status) - ['live', 'law', 'restrictive', 'unverified'].indexOf(b[1].status));
    $('ruleList').innerHTML = rules.map(([, r]) => `<div class="st-rule ${r.status}"><b>${esc(r.who)}</b><span class="tag">${esc(STATUS[r.status][1])}</span><br>${esc(r.text)} <a href="${r.src}" target="_blank" rel="noopener">${esc(r.src_label)}</a></div>`).join('');
    const m = baseMap('ruleMap', { center: [53, 6], zoom: 4, minZoom: 3 });
    const CC_STATUS = { NL: 'law', DE: 'law', IE: 'restrictive' };
    if (WORLD) L.geoJSON(WORLD, {
      filter: f => ISO_N[+f.id] && ISO_N[+f.id] !== 'GB',
      style: f => { const s = CC_STATUS[ISO_N[+f.id]] || 'unverified'; return { color: '#0b0f14', weight: 1, fillColor: STATUS[s][0], fillOpacity: s === 'unverified' ? 0.22 : 0.6 }; },
      onEachFeature: (f, l) => { const c = ISO_N[+f.id], s = CC_STATUS[c] || 'unverified'; l.bindTooltip(`<b>${esc(NAMES[c])}</b><br>${esc(STATUS[s][1])}`, { className: 'st-tt', sticky: true }); },
    }).addTo(m);
    if (dno) L.geoJSON(dno, {
      style: f => { const s = DNO_RULE[(f.properties.name || '').split(' ')[0].toUpperCase()] || 'unverified'; return { color: '#0b0f14', weight: 1, fillColor: STATUS[s][0], fillOpacity: s === 'unverified' ? 0.22 : 0.6 }; },
      onEachFeature: (f, l) => { const s = DNO_RULE[(f.properties.name || '').split(' ')[0].toUpperCase()] || 'unverified'; l.bindTooltip(`<b>${esc(f.properties.name.replace('WPD', 'NGED'))}</b><br>${esc(STATUS[s][1])}`, { className: 'st-tt', sticky: true }); },
    }).addTo(m);
    $('ruleLegend').innerHTML = Object.entries(STATUS).map(([, v]) => `<span><i style="background:${v[0]}"></i>${esc(v[1])}</span>`).join('');
  }

  // ═════════ 06 ONE NODE ═════════
  function renderNode() {
    if (!PT) return;
    const names = Object.keys(PT).filter(n => PT[n].flex_mw).sort((a, b) => PT[b].flex_mw['1%'] - PT[a].flex_mw['1%']);
    if (!$('nodePick').children.length) {
      $('nodePick').innerHTML = names.map(n => `<option value="${esc(n)}">${esc(n)} · firm rating ${f1(PT[n].firm_mw)} MW</option>`).join('');
      $('nodePick').onchange = renderNode;
    }
    const n = $('nodePick').value || names[0], m = PT[n];
    const bars = [['Published, firm', m.published_avail_mw, '#a8d2ff', 'what E-Redes says you can connect today'],
      ['Flexible, ≤1% curtailed', m.flex_mw['1%'], '#ff9b1f', 'same substation, Duke method against its firm rating'],
      ['Firm with 2 h battery', m.firm_2h_mw, '#6fb8ff', 'battery covers every shortfall'],
      ['Firm with 8 h battery', m.firm_8h_mw, '#3d8fd6', 'battery covers every shortfall']];
    const vmax = Math.max(...bars.map(b => b[1] || 0)) * 1.12;
    const W = 720, rowH = 48, pl = 190, pr = 70, H = bars.length * rowH + 20;
    const x = v => pl + (v / vmax) * (W - pl - pr);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Published, flexible and battery-firmed capacity at ${esc(n)}">`;
    bars.forEach((b, i) => {
      const y0 = 10 + i * rowH;
      s += `<text x="${pl - 12}" y="${y0 + 24}" class="sv-lab-b" text-anchor="end">${esc(b[0])}<title>${esc(b[3])}</title></text>`;
      s += `<rect x="${pl}" y="${y0 + 8}" width="${Math.max(2, x(b[1] || 0) - pl)}" height="22" rx="3" fill="${b[2]}"/>`;
      s += `<text x="${x(b[1] || 0) + 8}" y="${y0 + 24}" class="sv-val">${f1(b[1])} MW</text>`;
    });
    s += '</svg>';
    $('nodeChart').innerHTML = s;
    const up = m.flex_mw['1%'] - m.published_avail_mw;
    $('nodeFine').innerHTML = `${esc(n)}: metered ${esc(m.period)}, ${m.samples.toLocaleString('en-GB')} readings, peak ${f1(m.peak_mw)} MW against a firm rating of ${f1(m.firm_mw)} MW. ` +
      (up > 0 ? `Accepting 1% curtailment adds <b>${f1(up)} MW</b> (${Math.round(100 * up / m.published_avail_mw)}%) over the published figure.` : 'Here the published figure is already above the flexible one.') +
      ' GB substations get the same treatment once network-operator metering access is in place.';
  }

  // ═════════ boot ═════════
  const getJSON = u => fetch(u).then(r => (r.ok ? r.json() : null)).catch(() => null);
  Promise.all([getJSON('data/eu_load_growth.json'), getJSON('data/pockets.json'), getJSON('data/pt_profiles.json'),
    getJSON('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json'), getJSON('data/gb_dno.geojson')])
    .then(([d, p, pt, world, dno]) => {
      D = d; P = p; PT = pt;
      if (world && window.topojson) WORLD = topojson.feature(world, world.objects.countries);
      if (!D) { $('heroGW').textContent = '–'; return; }
      st.lim = D.limits.includes('1%') ? '1%' : D.limits[0];
      limitControl(); renderHero(); renderPeak();
      $('mapMetric').onclick = e => { const b = e.target.closest('button'); if (!b) return; st.metric = b.dataset.m; [...$('mapMetric').children].forEach(x => x.classList.toggle('on', x === b)); renderMap(); };
      renderMap();
      $('upSlider').oninput = e => { st.up = UPTIME[+e.target.value]; $('upLabel').textContent = st.up; renderBattery(); };
      renderBattery();
      if (P) { renderPockets(); renderRules(dno); }
      renderNode();
    });
})();
