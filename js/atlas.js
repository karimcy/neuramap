/* ═══════════════════════════════════════════════════════════════
   Neura Atlas bridge — open GB constraint layers from the Neura Constraint Atlas
   Data: data/atlas/ (written by the Atlas's tools/neuramap_export.py; see docs/ATLAS_BRIDGE.md)
   Loaded after app.js. It adds one matrix row and its own panel, and touches no node layer:
   nothing here feeds the deal screen, the fit score or the node popups.
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  const BASE = 'data/atlas/';
  const RK = 'atlas';
  const GB_BOUNDS = [[49.8, -8.7], [60.9, 1.8]];
  const C = {
    unit: '#5ec8f2', unitEdge: '#0b0f14', ring: '#5ec8f2',
    lineQuiet: '#7d8a9c', lineLo: '#ffe08a', lineHi: '#ff8a3d', etys: '#3a4656', ic: '#d9e1ec',
  };

  // ── matrix row: GB only; every other market shows the gap with its reason
  ROWS.push([RK, 'GB constraints — Neura Atlas snapshot (open data: Elexon, NESO, OSM, REPD)', 'GB constraints — Atlas']);
  for (const cc of Object.keys(MATRIX)) {
    MATRIX[cc][RK] = cc === 'GB' ? { url: BASE + 'GB.json', ids: [] } : { gap: 'Neura Atlas covers GB only' };
  }

  const st = {
    manifest: null, snap: null, stamp: null, geo: {}, visible: false,
    sub: { turndown: true, boundaries: true, interconnectors: true },
    groups: { turndown: L.layerGroup(), boundaries: L.layerGroup(), interconnectors: L.layerGroup() },
    attribution: null,
  };

  const getJSON = async url => { const r = await fetch(url); if (!r.ok) throw new Error(url + ' ' + r.status); return r.json(); };
  const n1 = v => (v == null ? '—' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: 1 }));
  const n0 = v => (v == null ? '—' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 }));
  const when = iso => (iso ? iso.replace('T', ' ').replace(/:\d\dZ$/, ' UTC').replace(/Z$/, ' UTC') : '—');
  const mlabel = id => { const m = st.manifest && st.manifest.methods[id]; return m ? (m.ui_label || m.name || id) : id; };
  const ops = lay => [...new Set(((st.manifest && st.manifest.srcs[lay]) || []).map(s => s.op))].join(' · ');
  const foot = (lay, methods) =>
    `<div class="pp-src">Neura Atlas snapshot ${esc(when(st.snap.as_of))} · ${esc(ops(lay))}<br>` +
    `<span class="pp-dim">methods: ${methods.map(esc).join(', ')}</span></div>`;

  async function loadManifest() {
    if (!st.manifest) st.manifest = await getJSON(BASE + 'GB.json');
    return st.manifest;
  }
  async function loadSnapshot(stamp) {
    const m = await loadManifest();
    const s = m.snapshots.find(x => x.stamp === stamp) || m.snapshots[0];
    st.snap = await getJSON(BASE + s.file);
    st.stamp = s.stamp;
    for (const [k, v] of Object.entries(m.geometry)) if (!st.geo[k]) st.geo[k] = await getJSON(BASE + v.file);
  }

  // ── turn-down by drawn wind BM unit
  function drawTurndown() {
    const g = st.groups.turndown; g.clearLayers();
    const td = st.snap.turndown;
    const vol = new Map(td.units.map(u => [u[0], u[1]]));
    const max = Math.max(1, ...td.units.map(u => Math.abs(u[1])));
    for (const key of ['units_osm', 'units_repd']) {
      for (const f of (st.geo[key] && st.geo[key].features) || []) {
        const p = f.properties; const v = vol.get(p.id);
        if (v == null || v === 0) continue;               // absent from the window, or held as zero: no marker
        const ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
        if (p.cls === 'B') g.addLayer(L.circle(ll, { radius: 5000, color: C.ring, weight: 1, dashArray: '4 4', fill: false, interactive: false }));
        const m = L.circleMarker(ll, {
          renderer: CANVAS, radius: 3 + 11 * Math.sqrt(Math.abs(v) / max),
          color: C.unitEdge, weight: 1, fillColor: C.unit, fillOpacity: p.cls === 'A' ? 0.9 : 0.55,
          dashArray: p.cls === 'B' ? '3 3' : null,
        });
        m.bindTooltip(`<b>${esc(p.id)}</b> · <span class="num">${n1(v)} MWh</span> SO-flagged turn-down`, { sticky: true, direction: 'top', opacity: 1 });
        m.bindPopup(() => unitPopup(p, v, key), { maxWidth: 340 });
        g.addLayer(m);
      }
    }
  }
  function unitPopup(p, v, key) {
    const td = st.snap.turndown;
    const osm = /^osm:(node|way|relation)\/(\d+)/.exec(p.loc_ref || '');
    let s = `<div class="pp-head"><span class="pp-name">${esc(p.id)}</span><span class="pp-kv">BM unit · wind</span></div>`;
    s += `<div class="pp-mw">${n1(v)}<small> MWh</small></div>`;
    s += `<div class="pp-kind">${esc(td.measure)}</div>`;
    s += `<div class="pp-meta">${esc(when(td.window.from))} → ${esc(when(td.window.to))} · ${esc(td.basis)}</div>`;
    s += `<div class="pp-meta">Location class <b>${esc(p.cls)}</b>: ${esc(mlabel(p.method))}` +
      (p.cls === 'B' ? ' · drawn with a 5 km ring' : '') + ` · <b>${esc(p.review)}</b></div>`;
    s += `<div class="pp-meta pp-dim">` + (osm ? `OSM <a href="https://www.openstreetmap.org/${osm[1]}/${osm[2]}" target="_blank" rel="noopener">${osm[1]} ${osm[2]}</a>` : '') +
      (p.repd_id ? `${osm ? ' · ' : ''}REPD id ${esc(p.repd_id)}` : '') + ` · location data ${key === 'units_osm' ? 'ODbL' : 'OGL v3.0'}</div>`;
    return s + foot('atlas_turndown', td.methods);
  }

  // ── NESO day-ahead boundary forecasts on ETYS lines
  function stressStrip(g, axis) {
    const w = 300, h = 34, n = axis.n, bw = w / n;
    let bars = '';
    g.stress.forEach((v, i) => {
      if (v == null) return;
      const y = Math.min(1.5, v) / 1.5 * (h - 4);
      bars += `<rect x="${(i * bw).toFixed(2)}" y="${(h - y).toFixed(2)}" width="${Math.max(bw, 0.6).toFixed(2)}" height="${y.toFixed(2)}" fill="${v >= 1 ? C.lineHi : C.lineQuiet}"/>`;
    });
    g.no_limit_modelled.forEach(i => { bars += `<rect x="${(i * bw).toFixed(2)}" y="${h - 3}" width="${Math.max(bw, 0.6).toFixed(2)}" height="3" fill="#5f6d80"/>`; });
    const one = (h - (1 / 1.5) * (h - 4)).toFixed(2);
    return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="forecast stress per settlement period">` +
      `<line x1="0" x2="${w}" y1="${one}" y2="${one}" stroke="#8b98a9" stroke-dasharray="2 2" stroke-width="0.7"/>${bars}</svg>`;
  }
  function groupLine(g) {
    return g.render === 'mapped'
      ? `group ${esc(g.group)} → ${esc(g.etys)}, mapping unverified`
      : `group ${esc(g.group)} → ${esc(g.etys)}: candidate mapping, one evidence type, unverified`;
  }
  function boundaryPopup(name, gs, otf, stale) {
    const b = st.snap.boundaries;
    let s = `<div class="pp-head"><span class="pp-name">${esc(name)}</span><span class="pp-kv">ETYS boundary</span></div>`;
    if (stale) s += `<div class="pp-meta pp-dim">This snapshot's forecast window has ended; the figures are history, not the current forecast.</div>`;
    for (const g of gs) {
      s += `<div class="pp-kind">${groupLine(g)}</div>`;
      s += `<div class="pp-meta"><b>${n0(g.n_forecast_binding)}</b> of ${n0(g.n_sps_held)} SPs with a forecast were forecast binding (stress ≥ 1)` +
        (g.max_stress ? ` · highest forecast stress <b>${g.max_stress.stress.toFixed(2)}</b> at ${esc(when(g.max_stress.sp_start))}` : '') +
        (g.n_no_limit_modelled ? ` · ${n0(g.n_no_limit_modelled)} SPs no limit modelled` : '') +
        (g.n_sps_missing ? ` · ${n0(g.n_sps_missing)} SPs with no forecast` : '') + `</div>`;
      s += stressStrip(g, b.axis);
    }
    if (otf) {
      s += `<div class="pp-meta">NESO OTF weekly limit <b>${n0(otf.limit_mw)} MW</b> (week of ${esc(otf.limit_week)})` +
        (otf.next_forecast_limit_mw != null ? ` · next published ${n0(otf.next_forecast_limit_mw)} MW (week of ${esc(otf.next_forecast_week)})` : '') + `</div>`;
    }
    s += `<div class="pp-meta pp-dim">${esc(b.wording)} ${esc(b.axis.from.slice(0, 10))} → ${esc(b.axis.to.slice(0, 10))}.</div>`;
    return s + foot('atlas_boundaries', b.methods);
  }
  function drawBoundaries() {
    const lg = st.groups.boundaries; lg.clearLayers();
    const b = st.snap.boundaries;
    const stale = Date.now() > Date.parse(b.axis.to);
    const byLine = {};
    for (const g of b.groups) if (g.render !== 'list_only' && g.etys) (byLine[g.etys] = byLine[g.etys] || []).push(g);
    const otfBy = Object.fromEntries(b.otf.rows.map(o => [o.boundary, o]));
    for (const f of st.geo.etys_neso.features) {
      const name = f.properties.boundary;
      const gs = byLine[name] || [];
      const otf = otfBy[name];
      let style = { color: C.etys, weight: 1.2, opacity: 0.8, dashArray: '2 4' };
      if (gs.length) {
        const share = Math.max(...gs.map(g => (g.n_sps_held ? g.n_forecast_binding / g.n_sps_held : 0)));
        const mapped = gs.some(g => g.render === 'mapped');
        style = { color: stale ? C.lineQuiet : (share > 0 ? mix(C.lineLo, C.lineHi, share) : C.lineQuiet),
          weight: 2 + 4 * share, opacity: stale ? 0.55 : 0.95, dashArray: mapped ? null : '8 6' };
      } else if (otf) style = { color: C.lineQuiet, weight: 1.6, opacity: 0.85, dashArray: '2 4' };
      const layer = L.geoJSON(f, { style: () => style, interactive: !!(gs.length || otf) });
      if (gs.length || otf) {
        layer.bindTooltip(gs.length ? `${esc(name)} · ${esc(gs.map(g => g.group).join(', '))}?` : esc(name), { sticky: true, opacity: 1 });
        layer.bindPopup(() => boundaryPopup(name, gs, otf, stale), { maxWidth: 360 });
      }
      lg.addLayer(layer);
    }
  }
  function mix(a, b, t) {
    const p = x => [1, 3, 5].map(i => parseInt(x.slice(i, i + 2), 16));
    const A = p(a), B = p(b);
    return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * Math.min(1, t)).toString(16).padStart(2, '0')).join('');
  }

  // ── interconnectors: NESO outturn rows on OSM cable geometry (neutral colour; one SP on a static map misleads)
  function icStrip(l) {
    const w = 300, h = 40, n = l.flow_mw.length, bw = w / Math.max(1, n);
    const max = Math.max(1, ...l.flow_mw.filter(v => v != null).map(Math.abs));
    let s = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="flow per settlement period"><line x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}" stroke="#5f6d80" stroke-width="0.7"/>`;
    l.flow_mw.forEach((v, i) => {
      if (v == null) return;
      const y = (Math.abs(v) / max) * (h / 2 - 1);
      s += `<rect x="${(i * bw).toFixed(2)}" y="${(v >= 0 ? h / 2 - y : h / 2).toFixed(2)}" width="${Math.max(bw - 0.3, 0.5).toFixed(2)}" height="${y.toFixed(2)}" fill="${v >= 0 ? '#6fb8ff' : '#ff9b1f'}"/>`;
    });
    return s + '</svg>';
  }
  function icPopup(l) {
    const ic = st.snap.interconnectors;
    let s = `<div class="pp-head"><span class="pp-name">${esc(l.label)}</span><span class="pp-kv">GB ↔ ${esc(l.counterparty)}</span></div>`;
    if (l.latest) {
      s += `<div class="pp-mw">${n0(Math.abs(l.latest.flow_mw))}<small> MW ${l.latest.flow_mw > 0 ? 'import to GB' : l.latest.flow_mw < 0 ? 'export from GB' : ''}</small></div>`;
      s += `<div class="pp-kind">latest published: ${esc(l.latest.settlement_date)} SP${l.latest.sp}</div>`;
    } else s += `<div class="pp-kind">no published value in this snapshot</div>`;
    s += icStrip(l);
    s += `<div class="pp-meta">` +
      (l.highest_import ? `highest import ${n0(l.highest_import.flow_mw)} MW at ${esc(when(l.highest_import.sp_start))}` : 'no import in this strip') + ' · ' +
      (l.highest_export ? `highest export ${n0(-l.highest_export.flow_mw)} MW at ${esc(when(l.highest_export.sp_start))}` : 'no export in this strip') + `</div>`;
    s += `<div class="pp-meta pp-dim">${esc(ic.label)}. ${esc(ic.sign)} Blue = import, orange = export; gaps are periods not yet published. ${esc(ic.selection_note)}.</div>`;
    return s + foot('atlas_interconnectors', ic.methods);
  }
  function drawInterconnectors() {
    const lg = st.groups.interconnectors; lg.clearLayers();
    const by = Object.fromEntries(st.snap.interconnectors.links.map(l => [l.key, l]));
    for (const f of st.geo.ic_osm.features) {
      const l = by[f.properties.key]; if (!l) continue;
      const layer = L.geoJSON(f, { style: () => ({ color: C.ic, weight: 2.4, opacity: 0.85 }) });
      layer.bindTooltip(`${esc(l.label)} · GB ↔ ${esc(l.counterparty)}`, { sticky: true, opacity: 1 });
      layer.bindPopup(() => icPopup(l), { maxWidth: 360 });
      lg.addLayer(layer);
    }
  }

  // ── the panel (a card under the coverage matrix)
  function card() {
    let el = document.getElementById('atlasCard');
    if (el) return el;
    el = document.createElement('section');
    el.className = 'card'; el.id = 'atlasCard'; el.hidden = true;
    const anchor = document.getElementById('matrixWrap');
    const host = anchor && anchor.closest('section, .card');
    if (host && host.parentNode) host.parentNode.insertBefore(el, host.nextSibling);
    else document.getElementById('panel').appendChild(el);
    return el;
  }
  function renderCard() {
    const el = card();
    el.hidden = !st.visible;
    if (!st.visible || !st.snap) return;
    const m = st.manifest, s = st.snap, td = s.turndown, cs = td.conservation, b = s.boundaries;
    const stale = Date.now() > Date.parse(b.axis.to);
    const opt = m.snapshots.map(x => `<option value="${esc(x.stamp)}"${x.stamp === st.stamp ? ' selected' : ''}>${esc(when(x.as_of))}</option>`).join('');
    const tog = (k, lbl) => `<label class="tog"><input type="checkbox" data-atlas-sub="${k}" ${st.sub[k] ? 'checked' : ''}><span class="tog-ui"></span>${lbl}</label>`;
    const listOnly = b.groups.filter(g => g.render === 'list_only');
    const idx = s.index.series[0];
    const lastOk = idx ? idx.points.filter(p => p.values).slice(-3) : [];
    let h = `<div class="card-title">GB constraints — Neura Atlas <span class="card-hint">snapshot</span></div>`;
    h += `<div class="inline-ctl"><span class="lbl">Snapshot</span><select id="atlasSnap" class="pipe-stage" aria-label="Atlas snapshot">${opt}</select></div>`;
    h += tog('turndown', 'Wind turn-down by BM unit (SO-flagged)');
    h += `<p class="note"><b>${n0(cs.drawn_units)}</b> drawn units: <b class="num">${n1(cs.drawn_mwh)} MWh</b> · not drawn (no approved location): <b class="num">${n1(cs.not_drawn_mwh)} MWh</b> · GB wind total <b class="num">${n1(cs.gb_wind_total_mwh)} MWh</b>. ` +
      `${esc(when(td.window.from))} → ${esc(when(td.window.to))}, ${n0(td.window.sps_held)} of ${n0(td.window.sps_expected)} settlement periods held. ${td.window.ahead_note ? esc(td.window.ahead_note) + ' ' : ''}${esc(td.basis)}. ${esc(td.location_note)}</p>`;
    h += tog('boundaries', 'NESO day-ahead boundary forecasts on ETYS lines');
    h += `<p class="note">${esc(b.wording)} Line colour and width: share of settlement periods forecast binding. Solid = mapped, dashed = candidate; ${esc(b.mapping_rule)}` +
      (stale ? ' <b>This snapshot is older than its forecast window.</b>' : '') + `</p>`;
    if (listOnly.length) h += `<details class="note"><summary>${n0(listOnly.length)} NESO groups listed without a line</summary>` +
      listOnly.map(g => `<div><b>${esc(g.group)}</b>: ${n0(g.n_forecast_binding)} of ${n0(g.n_sps_held)} SPs forecast binding · <span class="pp-dim">${esc(g.render_reason || '')}</span></div>`).join('') + `</details>`;
    h += tog('interconnectors', 'Interconnector outturn (NESO)');
    h += `<p class="note">${esc(s.interconnectors.label)}.</p>`;
    if (idx) {
      h += `<details class="note"><summary>Curtailment index — ${esc(idx.name)}</summary><div class="pp-dim">${esc(s.index.label)}. ${esc(idx.basis)}.</div>` +
        lastOk.map(p => `<div>${esc(p.period)}: <b class="num">${n0(p.values.net_turndown_mwh)} MWh</b> net turn-down` +
          (p.validation && p.validation.net_turndown_mwh && p.validation.net_turndown_mwh.check ? ` · ${esc(p.validation.net_turndown_mwh.check)}: ${esc(p.validation.net_turndown_mwh.status)}` : '') + `</div>`).join('') +
        `<div class="pp-dim">${esc(mlabel('CIX_GB_v1'))}</div></details>`;
    }
    h += `<details class="note"><summary>Not in this snapshot</summary>` + m.absent.map(a => `<div><b>${esc(a.what)}</b>: ${esc(a.reason)}</div>`).join('') + `</details>`;
    h += `<p class="note pp-dim">${m.attribution.map(esc).join(' · ')} · <a href="https://github.com/karimcy/neuramap/blob/main/docs/ATLAS_BRIDGE.md" target="_blank" rel="noopener">about these layers</a> (docs/ATLAS_BRIDGE.md; licence notices are also inside each data/atlas file)</p>`;
    el.innerHTML = h;
    el.querySelector('#atlasSnap').onchange = async e => { await loadSnapshot(e.target.value); redraw(); };
    el.querySelectorAll('[data-atlas-sub]').forEach(i => { i.onchange = e => { st.sub[i.dataset.atlasSub] = e.target.checked; applyVisibility(); }; });
  }

  function applyVisibility() {
    for (const k of Object.keys(st.groups)) {
      const on = st.visible && st.sub[k];
      if (on && !map.hasLayer(st.groups[k])) st.groups[k].addTo(map);
      if (!on && map.hasLayer(st.groups[k])) map.removeLayer(st.groups[k]);
    }
    const attr = st.manifest ? st.manifest.attribution.join(' · ') : '';
    if (st.visible && attr && st.attribution !== attr) { map.attributionControl.addAttribution(attr); st.attribution = attr; }
    if (!st.visible && st.attribution) { map.attributionControl.removeAttribution(st.attribution); st.attribution = null; }
  }
  function redraw() { drawBoundaries(); drawInterconnectors(); drawTurndown(); applyVisibility(); renderCard(); }

  async function toggle(on) {
    st.visible = on;
    if (on) {
      try { if (!st.snap) await loadSnapshot(null); }
      catch (err) { console.warn('Atlas layers unavailable', err); st.visible = false; syncBox(); return; }
      redraw();
      const v = map.getBounds();
      if (!v.intersects(L.latLngBounds(GB_BOUNDS))) map.flyToBounds(GB_BOUNDS, { padding: [24, 24], duration: 1.1 });
    } else { applyVisibility(); renderCard(); }
  }
  function syncBox() {
    const el = document.getElementById(`row_GB_${RK}`);
    if (!el) return;
    el.checked = st.visible;
    el.onchange = e => toggle(e.target.checked);
  }

  // the matrix is rebuilt whenever a market loads: keep the box's state and handler across rebuilds
  const rebuild0 = rebuildMatrix;
  rebuildMatrix = function () { rebuild0.apply(this, arguments); syncBox(); };   // eslint-disable-line no-func-assign
  // the freshness strip gets the snapshot's own edition date, under the same 60-day rule
  const fresh0 = renderFreshness;
  renderFreshness = function () {                                                  // eslint-disable-line no-func-assign
    fresh0.apply(this, arguments);
    if (!st.manifest) return;
    const d = st.manifest.snapshots[0].as_of.slice(0, 10);
    const age = Math.round((Date.now() - Date.parse(d)) / 86400000);
    const body = document.getElementById('freshBody'), sum = document.getElementById('freshSummary');
    if (!body || !sum) return;
    body.insertAdjacentHTML('beforeend', `${body.innerHTML ? ' · ' : ''}<span class="${age > 60 ? 'stale' : ''}">GB Neura Atlas ${esc(d)}</span>`);
    sum.textContent = sum.textContent.replace(/(\d+) source feeds/, (_, k) => `${+k + 1} source feeds`) + (age > 60 ? ' · Atlas snapshot >60 d old' : '');
  };

  rebuildMatrix();
  loadManifest().then(() => renderFreshness()).catch(err => console.warn('Atlas manifest unavailable', err));
})();
