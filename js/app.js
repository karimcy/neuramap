/* ═══════════════════════════════════════════════════════════════
   OpportunityMap — European Grid Connection Atlas · Neura Energy
   Data model: FREE (inline markets) · MANIFEST (lazy) · MATRIX (grid)
   ═══════════════════════════════════════════════════════════════ */
'use strict';

// [row key, full label (tooltips/aria), short label (matrix display)]
const ROWS = [
  ['transmission', 'Transmission demand ≥100 MW',      'Transmission ≥100 MW'],
  ['distribution', 'Distribution demand ≥5 MW',        'Distribution ≥5 MW'],
  ['generation',   'Generation proxy (grid strength)', 'Generation proxy'],
  ['queued',       'Queued requests — NOT headroom',   'Queued — not headroom'],
  ['indicative',   'Flagged feasible — no MW',         'Feasible — no MW'],
  ['lines',        'Grid lines',                       'Grid lines'],
  ['sites',        'Candidate sites (OSM industrial)', 'Candidate sites'],
  ['assets',       'Solar & BESS assets — built + pipeline (REPD × TEC)', 'Solar & BESS assets'],
];
for (const cc of Object.keys(MATRIX)) {
  MATRIX[cc].assets = cc === 'GB'
    ? { url: 'data/gb_assets.geojson' }
    : { gap: 'GB only so far — built from REPD × TEC; other markets in the backlog' };
}
const COUNTRIES = {}; Object.assign(COUNTRIES, FREE);
const layerVisible = {};
let curThr = 5;

// ── deal-screen state (firming business model: flexible connection + BESS) ──
let dealMode = false;
let dealTarget = 50;      // MW target load
let dealColor = 'fit';    // funnel node colouring: 'fit' score tiers | 'raw' headroom ramp
let dealDur = 8;          // battery duration hours (2 or 8)
// firm-uplift on published headroom when a flexible connection is firmed by BESS:
// 2 h ×2.6 = the overview's day-profile (firm potential 30% → 78% of connection);
// 8 h ×3.4 = indicative — same power, 4× energy rides longer constraint windows
const FIRM_UPLIFT = { 2: 2.6, 8: 3.4 };
let ctxGen = 0;           // score-context generation (bump invalidates cached scores)
let ctxDirty = true;
const BESS_EUR_PER_KWH = 145;
const TIER_COLOR = { prime: '#34d399', strong: '#2dd4bf', possible: '#60a5fa', weak: '#475569' };
const ASSET_COLOR = { battery: '#22d3ee', hybrid: '#a3e635', solar: '#fbbf24' };
const assetState = { group: null, visible: false, loaded: false, counts: null };
const TIER_LABEL = { prime: 'Prime', strong: 'Strong', possible: 'Possible', weak: 'Weak' };

// official per-country line/network reference pages (no government site has per-line permalinks)
const LINELINKS = { ES: [
  ['CNMC network map', 'https://maparee.cnmc.es'],
  ['REE capacity page', 'https://www.ree.es/es/clientes/consumidor/acceso-conexion/conoce-la-capacidad-de-acceso'],
]};

/* ── Map ─────────────────────────────────────────────────── */
const EUROPE_BBOX = [35.5, -11, 62.5, 26];   // mainland Iberia → GB → southern Scandinavia
const map = L.map('map', { zoomControl: false, preferCanvas: true, worldCopyJump: true })
  .setView([44.5, -1.5], 5);
L.control.zoom({ position: 'topleft' }).addTo(map);
// home control — reset to the initial Spain framing
const HomeControl = L.Control.extend({
  onAdd() {
    const div = L.DomUtil.create('div', 'leaflet-bar leaflet-control-home');
    const a = L.DomUtil.create('a', '', div);
    a.href = '#'; a.title = 'Reset view'; a.setAttribute('role', 'button'); a.setAttribute('aria-label', 'Reset view');
    a.innerHTML = '<svg viewBox="0 0 16 16" width="13" height="13" style="vertical-align:-2px"><path d="M2.5 8 8 2.8 13.5 8M4.2 7v6h7.6V7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    L.DomEvent.on(a, 'click', e => { L.DomEvent.stop(e); flyTo(EUROPE_BBOX); });
    return div;
  },
});
new HomeControl({ position: 'topleft' }).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains: 'abcd', maxZoom: 19,
}).addTo(map);

// ONE shared canvas renderer for every vector layer: with stacked canvases only the
// topmost receives pointer events, so multi-pane canvas kills click-through. A single
// canvas hit-tests all layers and fires on the topmost drawn. Draw/hit order = add
// order; drawNodes() re-adds node markers so they stay on top after any layer toggle.
const CANVAS = L.canvas({ padding: 0.3, tolerance: 9 });

const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmt = n => (n || 0).toLocaleString('en-US');
// Root-absolute `/data/...` breaks on GitHub Pages (`/neuramap/`). Keep paths page-relative.
function assetUrl(u) {
  if (!u) return u;
  if (/^(https?:|data:)/i.test(u)) return u;
  return u.startsWith('/') ? u.slice(1) : u;
}

function col(mw) { return mw >= 500 ? '#800026' : mw >= 250 ? '#bd0026' : mw >= 100 ? '#e31a1c' : mw >= 50 ? '#fc4e2a' : mw >= 20 ? '#fd8d3c' : '#feb24c'; }
function rad(mw) { return Math.max(4, Math.min(26, Math.sqrt(mw) * 0.7)); }

const groups = {}, lineGroups = {}, regGroup = L.layerGroup(), lockGroup = L.layerGroup().addTo(map);
function grp(id) { if (!groups[id]) groups[id] = L.layerGroup().addTo(map); return groups[id]; }

/* ── Toast ───────────────────────────────────────────────── */
const toastEl = document.getElementById('toast');
let toastTimer = null;
function toast(msg, { spin = false, ttl = 2600 } = {}) {
  clearTimeout(toastTimer);
  toastEl.innerHTML = (spin ? '<span class="spin"></span>' : '') + esc(msg);
  toastEl.hidden = false;
  if (!spin) toastTimer = setTimeout(() => { toastEl.hidden = true; }, ttl);
}
function toastHide() { clearTimeout(toastTimer); toastEl.hidden = true; }

/* ── Popups ──────────────────────────────────────────────── */
function popup(p, srcs, cc) {
  const kindCls = p.kind === 'queued' ? 'warn' : p.kind === 'indicative' ? 'teal' : '';
  const kind = p.kind === 'generation' ? 'generation (injection) — grid-strength proxy'
    : p.kind === 'queued' ? '<b>queued request</b> — capacity already claimed, not headroom'
    : p.kind === 'indicative' ? '<b>operator-flagged viable</b> — no figure published'
    : 'available connection headroom';
  let s = `<div class="pp-head"><span class="pp-name">${esc(p.n)}</span>` +
          (p.kv ? `<span class="pp-kv">${esc(p.kv)} kV</span>` : '') +
          (p.meas ? '<span class="meas-badge">measured</span>' : '') + `</div>`;
  if (p.reg) s += `<div class="pp-reg">${esc(p.reg)}</div>`;
  s += p.kind === 'indicative'
    ? `<div class="pp-kind ${kindCls}">${kind}</div>`
    : `<div class="pp-mw">${fmt(p.mw)}<small> MW</small></div><div class="pp-kind ${kindCls}">${kind}</div>`;
  const meta = [];
  if (p.ch !== undefined) {
    meta.push(`CEP-CH ${p.ch} · CEP-SH ${p.sh} · No-CEP ${p.nc} MW${p.conc ? ' · <b style="color:#f7a8c4">concurso</b>' : ''}`);
    meta.push(`Total node margin: <b>${fmt(p.tot)} MW</b>`);
  } else if (p.occ !== undefined && p.occ !== null) {
    meta.push(`occupied ${p.occ} · queued ${p.q || 0} MW`);
  }
  if (p.note) meta.push(esc(p.note));
  if (meta.length) s += `<div class="pp-meta">${meta.join('<br>')}</div>`;
  if (p.meas) {
    const m = p.meas, dPub = m.min_headroom_mw - m.published_avail_mw;
    s += `<div class="pp-meas"><div class="pp-meas-head">Measured profile · E-Redes 15-min</div>` +
      `<div class="pp-deal-grid">` +
      `<span>firmable added load</span><b>${m.firm_2h_mw.toFixed(0)} MW · 2 h &nbsp;/&nbsp; ${m.firm_8h_mw.toFixed(0)} MW · 8 h</b>` +
      `<span>metered peak / mean</span><b>${m.peak_mw.toFixed(1)} / ${m.mean_mw.toFixed(1)} MW</b>` +
      `<span>min headroom (measured)</span><b>${m.min_headroom_mw.toFixed(1)} MW</b>` +
      `<span>published availability</span><b>${m.published_avail_mw.toFixed(1)} MW <span class="dv ${dPub < 0 ? 'dv-over' : 'dv-under'}">${dPub >= 0 ? '+' : ''}${dPub.toFixed(1)}</span></b>` +
      `</div>` +
      `<div class="deal-sub">metered ${esc(m.period)} · ${fmt(m.samples)} samples · firm rating ${m.firm_mw.toFixed(0)} MW</div></div>`;
  }
  const s0 = srcs && srcs[p.lay] && srcs[p.lay][p.si || 0];
  if (p.code || s0) {
    s += `<div class="pp-src">`;
    if (p.code) s += `node ${esc(p.code)}`;
    if (s0 && s0.u) s += `${p.code ? ' · ' : ''}Source: <a href="${s0.u}" target="_blank" rel="noopener">${esc(s0.op || 'operator')}</a>${s0.d ? ' · edition ' + s0.d : ''}`;
    s += `</div>`;
  }
  if (dealMode && cc && (p.meas || (p.kind === 'demand' && p.mw >= 5))) s += firmingBlock(p, cc);
  return s;
}

function lineFeaturePopup(cc, p) {
  const c = COUNTRIES[cc] || {};
  const nm = p.name ? `${p.name}${p.ref ? ' (' + p.ref + ')' : ''}` : `${p.voltage_kv || '?'} kV line`;
  const srcTag = p.name_src === 'ree' ? '(REE canonical name)' : p.name_src === 'endpoints' ? '(named by endpoint substations)' : '';
  let s = `<div class="pp-head"><span class="pp-name">${esc(nm)}</span></div>`;
  if (srcTag) s += `<div class="pp-reg">${srcTag}</div>`;
  s += `<div class="pp-reg">${p.voltage_kv || '?'} kV${p.operator ? ' · ' + esc(p.operator) : ''}</div>`;
  if (p.avail) {
    s += `<div class="pp-mw">${fmt(p.avail)}<small> MW strongest node headroom ≤12 km</small></div>`;
    if (p.avail_node) s += `<div class="pp-kind">at ${esc(p.avail_node)}</div>`;
  } else {
    s += `<div class="pp-kind">no published demand-headroom node within 12 km</div>` +
         `<div class="deal-sub">not “full” — this market publishes little or no node-level demand headroom along here</div>`;
  }
  const meta = [];
  if (p.ins_ini && p.ins_fin && !p.name_src) meta.push(`${esc(p.ins_ini)} → ${esc(p.ins_fin)}`);
  // seasonal thermal rating (REE GeoRed) — prefer summer, REE's most-quoted season
  const seasons = [['cap_mva_summer','summer'],['cap_mva_spring','spring'],['cap_mva_autumn','autumn'],['cap_mva_winter','winter']];
  const rated = seasons.find(x => p[x[0]] !== undefined);
  if (rated) {
    meta.push(`thermal rating: <b>${p[rated[0]]} MVA (${rated[1]})</b>`);
    meta.push(`<span class="pp-warnnote">a design ceiling, not spare capacity — live line flows are not public</span>`);
  }
  if (meta.length) s += `<div class="pp-meta">${meta.join('<br>')}</div>`;
  const links = [], seenLinks = new Set();
  if (p.osm_id) links.push(`<a href="https://www.openstreetmap.org/way/${p.osm_id}" target="_blank" rel="noopener">OSM way</a>`);
  [...(c.line_links || []), ...(LINELINKS[cc] || [])].forEach(x => {
    if (seenLinks.has(x[0])) return; seenLinks.add(x[0]);   // one link per operator label
    links.push(`<a href="${x[1]}" target="_blank" rel="noopener">${esc(x[0])}</a>`);
  });
  if (links.length) s += `<div class="pp-src">${links.slice(0, 6).join(' · ')}</div>`;
  return s;
}

/* ── Search index ────────────────────────────────────────── */
// nodes always indexed (even below MW threshold or with layer off); lines index as their layer loads
const searchIndex = [];
function indexNodesFor(cc) {
  const c = COUNTRIES[cc]; if (!c || !c.nodes || c._nodesIndexed) return; c._nodesIndexed = true;
  for (const p of c.nodes) {
    if (!p.n) continue;
    searchIndex.push({ label: p.n, sub: `${c.name} · node${p.mw ? ' · ' + p.mw + ' MW' : ''}`, cc, kind: 'node', lat: p.lat, lon: p.lon, ref: p });
  }
}
function indexLinesFor(cc, gj) {
  const c = COUNTRIES[cc]; if (!gj || c._linesIndexed) return; c._linesIndexed = true;
  for (const f of gj.features || []) {
    if (f.geometry.type !== 'LineString' || !f.properties || !f.properties.name) continue;
    const coords = f.geometry.coordinates; const mid = coords[Math.floor(coords.length / 2)];
    searchIndex.push({ label: f.properties.name, sub: `${c.name} · line${f.properties.voltage_kv ? ' · ' + f.properties.voltage_kv + ' kV' : ''}`, cc, kind: 'line', lat: mid[1], lon: mid[0], ref: f });
  }
}

/* ── Nodes ───────────────────────────────────────────────── */
const nodeMarkers = [];
let _dnPending = false;
function drawNodes() {   // coalesced: many toggles in one frame → one redraw
  if (_dnPending) return;
  _dnPending = true;
  requestAnimationFrame(() => { _dnPending = false; _drawNodesNow(); });
}
function _drawNodesNow() {
  for (const id in groups) groups[id].clearLayers();
  nodeMarkers.length = 0;
  const toDraw = [];
  for (const cc in COUNTRIES) {
    indexNodesFor(cc);
    for (const p of COUNTRIES[cc].nodes) {
      if (dealMode) {
        // deal screen: every demand node ≥5 MW in every loaded market, layer toggles ignored
        // (plus measured-profile nodes, whose firmable MW is metered rather than published)
        if (!p.meas && (p.kind !== 'demand' || !(p.mw >= 5))) continue;
      } else {
        if (!layerVisible[p.lay]) continue;
        // indicative (feasible-point) nodes carry no MW — they bypass the MW threshold
        if (p.kind !== 'indicative' && p.mw < curThr) continue;
      }
      toDraw.push([cc, p]);
    }
  }
  // big circles first so small ones stay hoverable on top
  toDraw.sort((a, b) => (b[1].mw || 0) - (a[1].mw || 0));
  for (const [cc, p] of toDraw) {
    const stroke = p.kv == 400 ? '#f5f7ff' : (p.kv >= 220 ? '#aab4c8' : '#7f8aa0');
    const sc = dealMode ? dealScore(cc, p) : null;
    // queued (request-pipeline) purple + indicative (no-MW feasible) teal sit outside the red ramp
    const m = p.kind === 'indicative' && !dealMode
      ? L.circleMarker([p.lat, p.lon], { renderer: CANVAS, radius: 6, color: '#5eead4', weight: 1.4, fillColor: '#0d9488', fillOpacity: .85 })
      : L.circleMarker([p.lat, p.lon], { renderer: CANVAS, radius: rad(sc ? sc.H : p.mw),
          // measured-profile nodes carry the accent ring — metered evidence, not published
          color: sc && sc.meas ? '#ff9b1f' : sc && dealColor === 'fit' ? '#dfe7f0' : (p.kind === 'queued' ? '#c4b5fd' : stroke),
          weight: sc && sc.meas ? 1.8 : p.kv == 400 ? 1.6 : 1,
          fillColor: sc && dealColor === 'fit' ? TIER_COLOR[sc.tier] : (p.kind === 'queued' ? '#8a5cf6' : col(sc ? sc.H : p.mw)),
          fillOpacity: sc ? .82 : .78 });
    m.bindPopup(() => popup(p, COUNTRIES[cc].srcs, cc), { maxWidth: 340 });
    const mwTip = p.kind === 'indicative' ? ''
      : sc && sc.meas ? ` · <span class="num">${fmt(Math.round(sc.H))} MW measured</span>`
      : ` · <span class="num">${fmt(p.mw)} MW</span>`;
    m.bindTooltip(`<b>${esc(p.n)}</b>${mwTip}${sc ? ` · fit <b>${sc.score}</b>` : ''}`, { sticky: true, direction: 'top', opacity: 1 });
    const baseR = m.getRadius(), baseW = m.options.weight;
    m.on('mouseover', () => { m.setRadius(baseR + 3); m.setStyle({ weight: baseW + 1 }); });
    m.on('mouseout', () => { m.setRadius(baseR); m.setStyle({ weight: baseW }); });
    grp(p.lay).addLayer(m);
    nodeMarkers.push({ n: p.n, cc, marker: m, lat: p.lat, lon: p.lon });
  }
}

/* ── Lines ───────────────────────────────────────────────── */
let lineColorMode = 'volt';   // 'volt' (by kV = capacity class) | 'avail' (nearby available MW)
function lineStyle(f) {
  const p = f.properties || {};
  if (lineColorMode === 'avail') {
    const a = p.avail || 0;
    return { color: a >= 100 ? '#bd0026' : a >= 25 ? '#fc4e2a' : a >= 5 ? '#fd8d3c' : '#3a4658', weight: a >= 25 ? 2 : 1, opacity: a > 0 ? .9 : .35 };
  }
  const kv = p.voltage_kv;
  return { color: kv >= 380 ? '#7cc0ff' : kv >= 200 ? '#9aa6bf' : '#5b6680', weight: kv >= 380 ? 1.7 : 1, opacity: .7 };
}
async function ensureLines(cc) {
  const c = COUNTRIES[cc]; if (!c || lineGroups[cc]) return lineGroups[cc];
  let gj = c.lines;
  if (!gj && c.lines_url) { try { gj = await (await fetch(assetUrl(c.lines_url))).json(); } catch (e) { return null; } }
  if (!gj) return null;
  lineGroups[cc] = L.geoJSON(gj, {
    renderer: CANVAS,
    style: lineStyle, filter: f => f.geometry.type === 'LineString',
    onEachFeature: (f, l) => {
      l.bindPopup(lineFeaturePopup(cc, f.properties || {}), { maxWidth: 340 });
      l.on('mouseover', () => l.setStyle({ weight: (lineStyle(f).weight || 1) + 2.5 }));
      l.on('mouseout', () => l.setStyle(lineStyle(f)));
    },
  });
  indexLinesFor(cc, gj);
  return lineGroups[cc];
}
function restyleLines() { for (const cc in lineGroups) { if (lineGroups[cc]) lineGroups[cc].setStyle(lineStyle); } }
function hasLines(c) { return !!(c && (c.lines || c.lines_url)); }
function flyTo(bbox) { map.flyToBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: [24, 24], duration: 1.1 }); }

/* ── Spain region bubbles ────────────────────────────────── */
(function () {
  const c = COUNTRIES.ES; if (!c || !c.regs) return;
  c.regs.forEach(g => {
    const cm = L.circleMarker([g.lat, g.lon], { renderer: CANVAS,
      radius: Math.max(10, Math.min(60, Math.sqrt(g.mw) * 0.62)),
      color: '#4cc9f0', weight: 1.5, fillColor: '#4361ee', fillOpacity: .22 });
    cm.bindTooltip(`<b>${esc(g.n)}</b>: <span class="num">${fmt(g.mw)} MW</span> · ${g.cnt} nodes`, { sticky: true });
    cm.bindPopup(`<div class="pp-head"><span class="pp-name">${esc(g.n)}</span></div>` +
      `<div class="pp-mw">${fmt(g.mw)}<small> MW DC-grade capacity</small></div>` +
      `<div class="pp-kind">${g.cnt} nodes (${g.geo} mapped)</div>` +
      `<div class="pp-meta">Top: <b>${esc(g.top)}</b> (${fmt(g.topmw)} MW)</div>`);
    regGroup.addLayer(cm);
  });
})();

/* ── Lazy-country load rectangles ────────────────────────── */
// neutral "click to load" rectangle for a not-yet-loaded country (open access)
function loadRect(m) {
  const ext = m.extent || m.bbox;
  const r = L.rectangle([[ext[0], ext[1]], [ext[2], ext[3]]], {
    renderer: CANVAS,
    color: '#7cc0ff', weight: 1, dashArray: '5,5', fillColor: '#7cc0ff', fillOpacity: .05,
  });
  r.bindTooltip(`${m.name} — click to load`, { sticky: true });
  r.on('click', () => loadCountry(m.cc, m.data_url));
  r._cc = m.cc; lockGroup.addLayer(r);
}
function removeLoadRect(cc) { lockGroup.eachLayer(l => { if (l._cc === cc) lockGroup.removeLayer(l); }); }

const _loading = {};
async function loadCountry(cc, url, { quiet = false } = {}) {
  if (COUNTRIES[cc] || _loading[cc]) return true;          // already loaded / in flight
  _loading[cc] = true;
  const mf = MANIFEST.find(x => x.cc === cc);
  if (!quiet) toast(`Loading ${mf ? mf.name : cc}…`, { spin: true });
  let data; try { const r = await fetch(assetUrl(url)); if (!r.ok) throw new Error(r.status); data = await r.json(); }
  catch (e) { _loading[cc] = false; if (!quiet) toast(`Couldn't load ${mf ? mf.name : cc}`); return false; }
  COUNTRIES[cc] = data;
  // loading a country only adds its grid lines; node layers stay toggleable but hidden
  (data.layers || []).forEach(L_ => layerVisible[L_.id] = false);
  if (cc === 'PT') attachMeasured();
  removeLoadRect(cc); rebuildMatrix(); drawNodes();
  if (hasLines(data)) {
    const lg = await ensureLines(cc);
    if (lg) {
      lg.addTo(map); drawNodes();
      const cb = document.getElementById('lines_' + cc); if (cb) cb.checked = true;
      document.getElementById('linemode').hidden = false;
    }
  }
  if (!quiet) {
    flyTo(data.bbox);
    toast(`${data.name} loaded — ${fmt((data.nodes || []).length)} nodes`);
  }
  ctxDirty = true;
  if (dealMode) { drawNodes(); renderDealRank(); }
  updateLoadAllBtn();
  _loading[cc] = false; return true;
}

/* ── Layer × country matrix ──────────────────────────────── */
const sitesVisible = {};
function rebuildMatrix() {
  const head = document.getElementById('matrixHead');
  const body = document.getElementById('matrixBody');
  if (!head || !body) return;
  for (const cc in COUNTRIES) {
    const c = COUNTRIES[cc];
    (c.layers || []).forEach(L_ => { if (layerVisible[L_.id] === undefined) layerVisible[L_.id] = false; });
    if (c.sites_url && sitesVisible[cc] === undefined) sitesVisible[cc] = false;
  }
  // stable column order: inline markets first, then manifest order (not load-completion order)
  const allCcs = [...new Set([...Object.keys(FREE), ...MANIFEST.map(m => m.cc), ...Object.keys(COUNTRIES)])];
  head.innerHTML = '<tr><th></th>' + allCcs.map(cc => {
    const c = COUNTRIES[cc]; const mf = MANIFEST.find(x => x.cc === cc);
    const nm = (c || mf || {}).name || cc;
    const loaded = !!c;
    const nodes = loaded ? c.nodes.length : ((mf && mf.teaser && mf.teaser.nodes) || 0);
    const ge5 = loaded
      ? (c.layers || []).reduce((s, L_) => { if (L_.kind && L_.kind !== 'demand') return s;
          const st = c.stats && c.stats[L_.id]; return s + ((st && st.ge_bands && st.ge_bands['5']) || 0); }, 0)
      : ((mf && mf.teaser && mf.teaser.ge5) || 0);
    const tip = nm + ' — ' + fmt(nodes) + ' nodes' + (ge5 ? ' · ' + fmt(ge5) + ' with ≥5 MW headroom' : '') + (loaded ? '' : ' · tap to load this market');
    return `<th class="cc${loaded ? ' loaded' : ''}" data-cc="${cc}" tabindex="0" title="${esc(tip)}">${cc}</th>`;
  }).join('') + '</tr>';
  head.querySelectorAll('th[data-cc]').forEach(th => {
    const cc = th.dataset.cc;
    const mData = MANIFEST.find(x => x.cc === cc);
    const act = async () => {
      let c2 = COUNTRIES[cc];
      if (!c2 && mData) { const ok = await loadCountry(cc, mData.data_url); if (!ok) return; c2 = COUNTRIES[cc]; }
      const bbox = (c2 && (c2.extent || c2.bbox)) || (mData && (mData.extent || mData.bbox));
      if (bbox) flyTo(bbox);
    };
    th.addEventListener('click', act);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
  });
  body.innerHTML = '';
  ROWS.forEach(([rk, rlabel, rshort]) => {
    let tr = `<tr><td class="rowlbl" title="${esc(rlabel)}">${rshort || rlabel}</td>`;
    allCcs.forEach(cc => {
      const mcc = MATRIX[cc] && MATRIX[cc][rk];
      if (!mcc) { tr += `<td></td>`; return; }
      if (mcc.gap) { tr += `<td class="gap" title="${esc(mcc.gap)}">·</td>`; return; }
      if (rk === 'lines') {
        const loaded = !!COUNTRIES[cc];
        const checked = loaded && lineGroups[cc] && map.hasLayer(lineGroups[cc]);
        tr += `<td><input type="checkbox" class="mx" id="lines_${cc}" ${checked ? 'checked' : ''} aria-label="Grid lines ${cc}"></td>`;
      } else if (rk === 'sites') {
        tr += `<td><input type="checkbox" class="mx" id="sites_${cc}" ${sitesVisible[cc] ? 'checked' : ''} aria-label="Candidate sites ${cc}"></td>`;
      } else if (rk === 'assets') {
        tr += `<td><input type="checkbox" class="mx" id="assets_${cc}" ${assetState.visible ? 'checked' : ''} aria-label="Solar and BESS assets ${cc}"></td>`;
      } else {
        const ids = mcc.ids || [];
        const anyOn = ids.some(id => layerVisible[id]);
        tr += `<td><input type="checkbox" class="mx" id="row_${cc}_${rk}" ${anyOn ? 'checked' : ''} aria-label="${esc(rlabel)} ${cc}"></td>`;
      }
    });
    tr += '</tr>';
    body.insertAdjacentHTML('beforeend', tr);
  });
  // wire events after rendering
  allCcs.forEach(cc => {
    const mData = MANIFEST.find(m => m.cc === cc);
    ROWS.forEach(([rk]) => {
      const mcc = MATRIX[cc] && MATRIX[cc][rk];
      if (!mcc || mcc.gap) return;
      if (rk === 'lines') {
        const el = document.getElementById('lines_' + cc);
        if (!el) return;
        el.onchange = async e => {
          document.getElementById('linemode').hidden = false;
          if (mData && !COUNTRIES[cc]) { const ok = await loadCountry(cc, mData.data_url); if (!ok) { e.target.checked = !e.target.checked; return; } }
          const lg = await ensureLines(cc); if (!lg) return;
          if (e.target.checked) { lg.addTo(map); drawNodes(); } else map.removeLayer(lg);
        };
      } else if (rk === 'sites') {
        const el = document.getElementById('sites_' + cc);
        if (!el) return;
        el.onchange = async e => {
          sitesVisible[cc] = e.target.checked;
          updateCorridorVis();
          await rebuildCorridor();
        };
      } else if (rk === 'assets') {
        const el = document.getElementById('assets_' + cc);
        if (!el) return;
        el.onchange = e => toggleAssets(e.target.checked);
      } else {
        const el = document.getElementById('row_' + cc + '_' + rk);
        if (!el) return;
        el.onchange = async e => {
          if (mData && !COUNTRIES[cc]) { const ok = await loadCountry(cc, mData.data_url); if (!ok) { e.target.checked = !e.target.checked; return; } }
          const ids = (mcc.ids || []);
          ids.forEach(id => { layerVisible[id] = e.target.checked;
            if (!e.target.checked && groups[id]) groups[id].clearLayers(); });
          drawNodes();
        };
      }
    });
  });
}

/* ── KPIs + Spain region ranking ─────────────────────────── */
function renderKpis() {
  // deduped screening totals — same substation in two source layers counted once
  const seen = new Set();
  let sumMW = 0, totalNodes = 0;
  for (const cc in COUNTRIES) {
    const c = COUNTRIES[cc];
    totalNodes += (c.nodes || []).length;
    for (const p of c.nodes || []) {
      if (p.kind !== 'demand' || !(p.mw >= 5)) continue;
      const nameKey = cc + '|' + p.n.toUpperCase().replace(/\s+\d{2,3}(\s*\/\s*\d+)?\s*(KV)?$/, '').trim();
      const cellKey = cc + '|' + Math.round(p.lat / 0.03) + '|' + Math.round(p.lon / 0.03);
      if (seen.has(nameKey) || seen.has(cellKey)) continue;
      seen.add(nameKey); seen.add(cellKey);
      sumMW += p.mw;
    }
  }
  MANIFEST.forEach(m => { if (!COUNTRIES[m.cc]) totalNodes += (m.teaser && m.teaser.nodes) || 0; });
  const gw = sumMW / 1000;
  let html = '';
  if (euFlex && euFlex.battery_totals_gw) {
    const bt = euFlex.battery_totals_gw;
    const firmLbl = dealAvail === '100%' ? 'fully firm' : `≥${dealAvail} uptime`;
    const simTip = `Chronological battery simulation on measured ${euFlex.period} system load, per market: ` +
      `the added load draws through the connection; a battery (energy = load × duration) discharges through every ` +
      `hour the system would exceed its observed peak and can recharge only when spare room exists under the peak. ` +
      `Event duration and clustering fully accounted for. Threshold = required UPTIME: share of intervals the ` +
      `load runs at full power (slider below). System-level screening against the observed-peak floor; local network constraints ` +
      `still gate any specific node.`;
    const flexTip = `Norris-style load-duration analysis of measured ${euFlex.period} load: max constant flexible ` +
      `(curtailable) load addable against the observed system peak at the stated energy-curtailment tolerance. ` +
      `No battery — the load itself must flex.`;
    html +=
      `<div class="kpi hero" title="${esc(simTip)}"><b>${Math.round(bt['8h'][dealAvail])}<span class="unit">GW</span></b>` +
      `<span>new load · 8 h battery · ${firmLbl}</span></div>` +
      `<div class="kpi hero" title="${esc(simTip)}"><b>${Math.round(bt['2h'][dealAvail])}<span class="unit">GW</span></b>` +
      `<span>new load · 2 h battery · ${firmLbl}</span></div>` +
      `<div class="kpi sys" title="${esc(simTip)}"><b>${Math.round(bt['4h'][dealAvail])}<span class="unit">GW</span></b>` +
      `<span>4 h battery · ${firmLbl}</span></div>` +
      `<div class="kpi sys" title="${esc(flexTip)}"><b>${Math.round(euFlex.totals_gw['1%'])}<span class="unit">GW</span></b>` +
      `<span>flexible load · ≤1% curtailment, no battery</span></div>`;
  }
  html +=
    `<div class="kpi"><b>${gw.toFixed(0)}<span class="unit">GW</span></b><span>published nodal headroom (screening, non-additive)</span></div>` +
    `<div class="kpi"><b>${Object.keys(COUNTRIES).length}<span class="unit">mkts</span></b><span>${fmt(totalNodes)} grid nodes tracked</span></div>`;
  document.getElementById('kpis').innerHTML = html;
}
let euFlex = null;
const AVAIL_ORDER = ['100%', '99.99%', '99.95%', '99.9%', '99.5%', '99%'];
let dealAvail = '99.9%';
fetch('data/eu_flexible_headroom.json').then(r => r.ok ? r.json() : null)
  .then(d => { if (d) { euFlex = d; document.getElementById('availCtl').hidden = false; renderKpis(); } })
  .catch(() => {});
document.getElementById('availSlider').oninput = e => {
  dealAvail = AVAIL_ORDER[+e.target.value];
  document.getElementById('availLabel').textContent = dealAvail;
  renderKpis();
};

/* ── Measured node profiles (PT pilot — E-Redes 15-min metering) ── */
// data/pt_profiles.json: a year of metered load per substation → LDC + battery test →
// empirically firmable added load at 2 h / 8 h. Joined to map nodes by installation code.
let ptProfiles = null;
function attachMeasured() {
  if (!ptProfiles || !COUNTRIES.PT) return;
  const byCode = {};
  for (const nm in ptProfiles) { const m = ptProfiles[nm]; if (m.code) byCode[m.code] = m; }
  for (const p of COUNTRIES.PT.nodes || []) {
    if (!p.meas && p.code && byCode[p.code]) p.meas = byCode[p.code];
  }
  ctxDirty = true;
  drawNodes(); renderDivergence();
  if (dealMode) renderDealRank();
}
fetch('data/pt_profiles.json').then(r => r.ok ? r.json() : null)
  .then(d => { if (d) { ptProfiles = d; attachMeasured(); } })
  .catch(() => {});

function renderDivergence() {
  const sec = document.getElementById('accMeas');
  if (!ptProfiles || !sec) return;
  sec.hidden = false;
  const rows = Object.entries(ptProfiles).map(([nm, m]) => ({
    nm, m,
    pct: m.published_avail_mw ? (m.min_headroom_mw - m.published_avail_mw) / m.published_avail_mw : 0,
  })).sort((a, b) => a.pct - b.pct);   // worst overstatement first
  const within = rows.filter(r => Math.abs(r.pct) <= 0.15).length;
  const over = rows.filter(r => r.pct < -0.15).length;
  document.getElementById('measSummary').innerHTML =
    `<b>${rows.length}</b> substations metered (full year, 15-min) · ` +
    `<b>${within}</b> within ±15% of published · ` +
    `<b class="dv dv-over">${over}</b> published overstates &gt;15%`;
  const mapped = new Set(((COUNTRIES.PT || {}).nodes || []).filter(p => p.meas).map(p => p.code));
  document.getElementById('measList').innerHTML = rows.map(r =>
    `<div class="rank-row" data-code="${r.m.code}" tabindex="0">` +
    `<span class="rank-name">${esc(r.nm)} <span class="rank-sub">· pub ${r.m.published_avail_mw.toFixed(0)} → meas ${r.m.min_headroom_mw.toFixed(0)} MW${mapped.has(r.m.code) ? '' : ' · not mapped'}</span></span>` +
    `<span class="dv-pill ${r.pct < -0.15 ? 'dv-over' : r.pct > 0.15 ? 'dv-under' : 'dv-ok'}">${r.pct >= 0 ? '+' : ''}${Math.round(r.pct * 100)}%</span></div>`
  ).join('');
  sec.querySelectorAll('#measList .rank-row').forEach(row => {
    const go = () => {
      const p = ((COUNTRIES.PT || {}).nodes || []).find(x => x.code === row.dataset.code);
      if (!p) return;   // 6 pilot substations have no map node (no coordinates published)
      map.flyTo([p.lat, p.lon], 10, { duration: 1.0 });
      const nm2 = nodeMarkers.find(x => x.cc === 'PT' && x.n === p.n);
      if (nm2) nm2.marker.openPopup();
      else L.popup({ maxWidth: 340 }).setLatLng([p.lat, p.lon]).setContent(popup(p, COUNTRIES.PT.srcs, 'PT')).openOn(map);
    };
    row.onclick = go;
    row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
  });
}
renderKpis();
// Spain region ranking — proportional bars
(function () {
  const rank = document.getElementById('regRank');
  const c = COUNTRIES.ES;
  if (c && c.regs && rank) {
    const maxMw = Math.max(...c.regs.map(g => g.mw));
    rank.innerHTML = c.regs.map((g, i) =>
      `<div class="rank-row" data-i="${i}" tabindex="0">` +
      `<span class="rank-bar" style="width:${Math.max(2, g.mw / maxMw * 100)}%"></span>` +
      `<span class="rank-i">${i + 1}</span>` +
      `<span class="rank-name">${esc(g.n)} <span class="rank-sub">· ${g.cnt} nodes</span></span>` +
      `<span class="rank-val">${fmt(g.mw)}<small>MW</small></span></div>`
    ).join('');
    rank.querySelectorAll('.rank-row').forEach(row => {
      const g = c.regs[+row.dataset.i];
      const go = () => map.flyTo([g.lat, g.lon], 7, { duration: 1.1 });
      row.onclick = go;
      row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    });
  }
})();

/* ── Init defaults ───────────────────────────────────────── */
// default view = grid lines + ES candidate sites only; node bubbles OFF for every country
for (const cc in FREE) (FREE[cc].layers || []).forEach(L_ => layerVisible[L_.id] = false);
// load-rectangles are added only for markets that fail the startup auto-load below
rebuildMatrix(); drawNodes();

document.querySelectorAll('#thr button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#thr button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); curThr = +b.dataset.t; drawNodes();
});
document.querySelectorAll('#lm button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#lm button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); lineColorMode = b.dataset.lm; restyleLines(); renderLegend();
});
document.getElementById('tReg').onchange = e => { e.target.checked ? regGroup.addTo(map) : map.removeLayer(regGroup); };

// grid lines ON for every inlined (FREE) country; region bubbles stay OFF
(async function () {
  let anyLines = false;
  for (const cc in FREE) {
    if (!hasLines(FREE[cc])) continue;
    anyLines = true;
    const lg = await ensureLines(cc); if (!lg) continue;
    lg.addTo(map);
    const cb = document.getElementById('lines_' + cc); if (cb) cb.checked = true;
  }
  if (anyLines) { document.getElementById('linemode').hidden = false; drawNodes(); }
})();

/* ── Connectivity layer: fibre · PeeringDB facilities · subsea ── */
const telcoState = { group: null, visible: false, loaded: false };
async function ensureTelco() {
  if (telcoState.loaded) return telcoState.group;
  const gj = await (await fetch(assetUrl('data/telco.geojson'))).json();
  const fibre = [], subsea = [], points = [];
  for (const f of gj.features) {
    const cls = f.properties && f.properties.cls;
    if (cls === 'fibre') fibre.push(f);
    else if (cls === 'subsea') subsea.push(f);
    else points.push(f);
  }
  const grp = L.layerGroup();
  // batch into a few GeoJSON layers — one layer per feature freezes the map at ~12k features
  grp.addLayer(L.geoJSON({ type: 'FeatureCollection', features: fibre }, {
    renderer: CANVAS,
    style: f => {
      const op = (f.properties && f.properties.status) !== 'Planned';
      return { color: '#c084fc', weight: 1, opacity: op ? .55 : .22, dashArray: op ? null : '4,5' };
    },
    onEachFeature: (f, layer) => {
      layer.bindTooltip(`fibre backbone · ${esc((f.properties && f.properties.status) || '')}`, { sticky: true });
    },
  }));
  grp.addLayer(L.geoJSON({ type: 'FeatureCollection', features: subsea }, {
    renderer: CANVAS,
    style: { color: '#38bdf8', weight: 1.2, opacity: .5 },
    onEachFeature: (f, layer) => {
      layer.bindTooltip(`<b>${esc((f.properties && f.properties.name) || 'subsea cable')}</b>`, { sticky: true });
    },
  }));
  grp.addLayer(L.geoJSON({ type: 'FeatureCollection', features: points }, {
    renderer: CANVAS,
    pointToLayer: (f, latlng) => {
      const cls = f.properties && f.properties.cls;
      return L.circleMarker(latlng, cls === 'facility'
        ? { renderer: CANVAS, radius: 4, color: '#fda4d4', weight: 1, fillColor: '#f472b6', fillOpacity: .85 }
        : { renderer: CANVAS, radius: 3.5, color: '#7dd3fc', weight: 1, fillColor: '#0ea5e9', fillOpacity: .85 });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties || {}, facility = p.cls === 'facility';
      layer.bindTooltip(facility
        ? `<b>${esc(p.name)}</b> · interconnection facility · ${esc(p.city || '')}`
        : `<b>${esc(p.name)}</b> · cable landing`, { sticky: true, direction: 'top' });
      layer.bindPopup(facility
        ? `<div class="pp-head"><span class="pp-name">${esc(p.name)}</span><span class="pp-kv">facility</span></div>` +
          `<div class="pp-reg">${esc(p.city || '')} · ${esc(p.cc || '')}${p.org ? ' · ' + esc(p.org) : ''}</div>` +
          `<div class="pp-src">carrier-neutral interconnection facility · PeeringDB</div>`
        : `<div class="pp-head"><span class="pp-name">${esc(p.name)}</span><span class="pp-kv">landing</span></div>` +
          `<div class="pp-src">submarine cable landing point · TeleGeography</div>`, { maxWidth: 320 });
    },
  }));
  telcoState.group = grp; telcoState.loaded = true;
  return grp;
}
async function setTelcoVisible(on) {
  telcoState.visible = on;
  const cb = document.getElementById('tTelco'); if (cb) cb.checked = on;
  const grp = await ensureTelco();
  if (on) { grp.addTo(map); drawNodes(); } else map.removeLayer(grp);
  renderLegend();
}
document.getElementById('tTelco').onchange = e => { setTelcoVisible(e.target.checked); };
// on by default — fibre / interconnection / subsea are part of the atlas
setTelcoVisible(true);

/* ── Solar & BESS asset layer (GB: REPD × TEC) ───────────── */
function assetPopup(p) {
  let s = `<div class="pp-head"><span class="pp-name">${esc(p.name)}</span>` +
    `<span class="pp-kv">${p.tech}</span></div>`;
  if (p.operator) s += `<div class="pp-reg">${esc(p.operator)}</div>`;
  s += `<div class="pp-mw">${fmt(p.mw)}<small> MW</small></div>`;
  s += `<div class="pp-kind">${esc(p.status)}${p.year_op ? ` · operational since <b>${p.year_op}</b>` : ''}</div>`;
  const meta = [];
  if (p.storage_type) meta.push(esc(p.storage_type));
  if (p.county) meta.push(esc(p.county));
  if (meta.length) s += `<div class="pp-meta">${meta.join(' · ')}</div>`;
  if (p.tec) {
    s += `<div class="pp-meta">TEC: <b>${esc(p.tec.site || '?')}</b>` +
      `${p.tec.gate ? ' · ' + esc(p.tec.gate) : ''}${p.tec.status ? ' · ' + esc(p.tec.status) : ''}</div>`;
  }
  s += `<div class="pp-src">REPD ${esc(p.repd_id || '')} · duration (MWh) not published in REPD/TEC — DNO register join pending</div>`;
  return s;
}
async function ensureAssets() {
  if (assetState.loaded) return assetState.group;
  const gj = await (await fetch(MATRIX.GB.assets.url)).json();
  const grp = L.layerGroup();
  const counts = { battery: 0, hybrid: 0, solar: 0 };
  for (const f of gj.features) {
    const p = f.properties;
    counts[p.tech] = (counts[p.tech] || 0) + 1;
    const op = p.status === 'Operational';
    const m = L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], {
      renderer: CANVAS, radius: Math.max(3, Math.min(14, Math.sqrt(p.mw) * 0.9)),
      color: op ? '#f0f6ff' : ASSET_COLOR[p.tech], weight: op ? 1.2 : 0.8,
      fillColor: ASSET_COLOR[p.tech], fillOpacity: op ? .85 : .35,
    });
    m.bindPopup(assetPopup(p), { maxWidth: 340 });
    m.bindTooltip(`<b>${esc(p.name)}</b> · ${p.tech} · <span class="num">${fmt(p.mw)} MW</span> · ${esc(p.status)}`,
      { sticky: true, direction: 'top', opacity: 1 });
    grp.addLayer(m);
  }
  assetState.group = grp; assetState.loaded = true; assetState.counts = counts;
  return grp;
}
async function toggleAssets(on) {
  assetState.visible = on;
  const grp = await ensureAssets();
  if (on) { grp.addTo(map); drawNodes(); } else map.removeLayer(grp);
  renderLegend();
}

/* ── Floating legend ─────────────────────────────────────── */
function renderLegend() {
  let s;
  if (dealMode && dealColor === 'fit') {
    s = '<div class="lg-title">Firming fit score</div>';
    s += [['prime', '≥78 · prime — pursue'], ['strong', '63–77 · strong'], ['possible', '48–62 · possible'], ['weak', '<48 · weak']]
      .map(([t, lbl]) => `<div class="lg-row"><i class="lg-dot" style="background:${TIER_COLOR[t]}"></i>${lbl}</div>`).join('');
    s += `<div class="lg-foot">bubble area ∝ headroom · target ${dealTarget} MW</div>`;
  } else {
    const ramp = [['≥500', '#800026'], ['250–500', '#bd0026'], ['100–250', '#e31a1c'], ['50–100', '#fc4e2a'], ['20–50', '#fd8d3c'], ['<20', '#feb24c']];
    s = '<div class="lg-title">Available headroom (MW)</div>';
    s += ramp.map(x => `<div class="lg-row"><i class="lg-dot" style="background:${x[1]}"></i>${x[0]}</div>`).join('');
    if (!dealMode) {
      s += `<div class="lg-row"><i class="lg-dot" style="background:#8a5cf6"></i>queued request — not headroom</div>`;
      s += `<div class="lg-row"><i class="lg-dot" style="background:#0d9488"></i>operator-flagged viable — no figure</div>`;
    } else {
      s += `<div class="lg-foot">funnel mode · raw headroom colours</div>`;
    }
  }
  s += '<div class="lg-sep"></div>';
  if (lineColorMode === 'volt') {
    s += `<div class="lg-row"><i class="lg-line" style="background:#7cc0ff"></i>line ≥380 kV</div>` +
         `<div class="lg-row"><i class="lg-line" style="background:#9aa6bf"></i>200–380 kV</div>` +
         `<div class="lg-row"><i class="lg-line" style="background:#5b6680"></i>&lt;200 kV</div>`;
  } else {
    s += `<div class="lg-row"><i class="lg-line" style="background:#bd0026"></i>≥100 MW headroom nearby</div>` +
         `<div class="lg-row"><i class="lg-line" style="background:#fc4e2a"></i>25–100 MW</div>` +
         `<div class="lg-row"><i class="lg-line" style="background:#fd8d3c"></i>5–25 MW</div>` +
         `<div class="lg-row"><i class="lg-line" style="background:#3a4658"></i>none nearby</div>`;
  }
  if (telcoState.visible) {
    s += '<div class="lg-sep"></div>' +
      `<div class="lg-row"><i class="lg-line" style="background:#c084fc"></i>fibre backbone (dashed = planned)</div>` +
      `<div class="lg-row"><i class="lg-line" style="background:#38bdf8"></i>subsea cable</div>` +
      `<div class="lg-row"><i class="lg-dot" style="background:#f472b6"></i>interconnection facility</div>` +
      `<div class="lg-row"><i class="lg-dot" style="background:#0ea5e9"></i>cable landing point</div>`;
  }
  if (assetState.visible) {
    s += '<div class="lg-sep"></div>' +
      `<div class="lg-row"><i class="lg-dot" style="background:${ASSET_COLOR.battery}"></i>BESS asset (bright = operational)</div>` +
      `<div class="lg-row"><i class="lg-dot" style="background:${ASSET_COLOR.hybrid}"></i>solar + BESS hybrid</div>` +
      `<div class="lg-row"><i class="lg-dot" style="background:${ASSET_COLOR.solar}"></i>solar asset</div>`;
  }
  if (!dealMode) s += '<div class="lg-foot">bubble area ∝ MW · white ring = 400 kV, grey = lower kV</div>';
  document.getElementById('legend').innerHTML = s;
}
renderLegend();
map.fitBounds([[EUROPE_BBOX[0], EUROPE_BBOX[1]], [EUROPE_BBOX[2], EUROPE_BBOX[3]]], { padding: [16, 16] });

/* ── Resizable + collapsible panel ───────────────────────── */
(function () {
  const panel = document.getElementById('panel');
  const handle = document.getElementById('panelDrag');
  if (!panel || !handle) return;
  const STORE_KEY = 'oppmap_panelw', MIN_W = 300, MAX_W = 660;
  const saved = parseInt(localStorage.getItem(STORE_KEY), 10);
  if (saved >= MIN_W && saved <= MAX_W) panel.style.width = saved + 'px';
  let dragging = false, startX = 0, startW = 0;
  handle.addEventListener('mousedown', e => {
    e.preventDefault(); dragging = true; startX = e.clientX; startW = panel.offsetWidth;
    handle.classList.add('dragging'); document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  let invPending = false;
  function throttleInvalidate() {
    if (invPending) return; invPending = true;
    requestAnimationFrame(() => { map.invalidateSize(); invPending = false; });
  }
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const w = Math.max(MIN_W, Math.min(MAX_W, startW + e.clientX - startX));
    panel.style.width = w + 'px'; throttleInvalidate();
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return; dragging = false;
    handle.classList.remove('dragging'); document.body.style.cursor = '';
    document.body.style.userSelect = '';
    localStorage.setItem(STORE_KEY, panel.offsetWidth); map.invalidateSize();
  });
  document.getElementById('panelCollapse').onclick = () => {
    document.body.classList.toggle('panel-hidden');
    map.invalidateSize();
  };
})();

/* ── Search ──────────────────────────────────────────────── */
for (const cc in COUNTRIES) indexNodesFor(cc);   // lines self-index as ensureLines() runs
function openSearchResult(hit) {
  map.flyTo([hit.lat, hit.lon], 11, { duration: 1.0 });
  if (hit.kind === 'node') {
    const nm = nodeMarkers.find(x => x.n === hit.label && x.cc === hit.cc);
    if (nm) nm.marker.openPopup();
    else L.popup({ maxWidth: 340 }).setLatLng([hit.lat, hit.lon]).setContent(popup(hit.ref, COUNTRIES[hit.cc].srcs, hit.cc)).openOn(map);
    location.hash = `cc=${hit.cc}&n=${encodeURIComponent(hit.label)}`;
  } else {
    L.popup({ maxWidth: 340 }).setLatLng([hit.lat, hit.lon]).setContent(lineFeaturePopup(hit.cc, hit.ref.properties || {})).openOn(map);
    if (hit.ref.properties && hit.ref.properties.osm_id) location.hash = `cc=${hit.cc}&line=${hit.ref.properties.osm_id}`;
  }
}
let searchHits = [], searchSel = -1;
function runSearch(q) {
  const box = document.getElementById('searchResults');
  q = q.trim().toLowerCase();
  searchSel = -1;
  if (q.length < 2) { box.style.display = 'none'; box.innerHTML = ''; return; }
  searchHits = searchIndex.filter(h => h.label && h.label.toLowerCase().includes(q)).slice(0, 25);
  if (!searchHits.length) { box.style.display = 'block'; box.innerHTML = '<div class="sr-none">No matches</div>'; return; }
  const rx = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'i');
  box.innerHTML = searchHits.map((h, i) =>
    `<div class="sr-item" data-i="${i}" role="option">` +
    `<span class="sr-ico">${h.kind === 'line' ? '⚡' : '●'}</span>` +
    `<span class="sr-main">${esc(h.label).replace(rx, '<mark>$1</mark>')}</span>` +
    `<span class="sr-sub">${esc(h.sub)}</span></div>`
  ).join('');
  box.style.display = 'block';
  box.querySelectorAll('[data-i]').forEach(el => el.onclick = () => pickSearch(+el.dataset.i));
}
function pickSearch(i) {
  const h = searchHits[i]; if (!h) return;
  openSearchResult(h);
  document.getElementById('searchResults').style.display = 'none';
  document.getElementById('search').value = h.label || '';
}
const searchEl = document.getElementById('search');
searchEl.addEventListener('input', e => runSearch(e.target.value));
searchEl.addEventListener('focus', e => { if (e.target.value.trim().length >= 2) runSearch(e.target.value); });
searchEl.addEventListener('keydown', e => {
  const box = document.getElementById('searchResults');
  if (box.style.display !== 'block') return;
  const items = box.querySelectorAll('.sr-item');
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    searchSel = e.key === 'ArrowDown' ? Math.min(searchSel + 1, items.length - 1) : Math.max(searchSel - 1, 0);
    items.forEach((el, i) => el.classList.toggle('sel', i === searchSel));
    if (items[searchSel]) items[searchSel].scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter' && searchSel >= 0) { e.preventDefault(); pickSearch(searchSel); }
  else if (e.key === 'Escape') { box.style.display = 'none'; searchEl.blur(); }
});
document.addEventListener('click', e => {
  if (!e.target.closest('.searchbox')) document.getElementById('searchResults').style.display = 'none';
});
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== searchEl && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault(); searchEl.focus(); searchEl.select();
  }
});

/* ── Freshness ───────────────────────────────────────────── */
function renderFreshness() {
  const rows = []; const now = new Date();
  for (const cc in COUNTRIES) {
    const c = COUNTRIES[cc];
    for (const lay in (c.srcs || {})) {
      const entries = c.srcs[lay]; if (!entries || !entries.length) continue;
      // freshest parseable edition date among this layer's sources wins
      let bestD = null, bestOp = entries[0].op;
      entries.forEach(s => { const d = Date.parse((s.d || '').slice(0, 10)); if (!isNaN(d) && (bestD === null || d > bestD)) { bestD = d; bestOp = s.op; } });
      let ageDays = null; if (bestD !== null) ageDays = Math.round((now - bestD) / 86400000);
      rows.push({ cc, op: bestOp, d: entries[0].d, ageDays });
    }
  }
  if (!rows.length) return;
  const anyStale = rows.some(r => r.ageDays !== null && r.ageDays > 60);
  document.getElementById('freshSummary').textContent =
    `Data freshness — ${rows.length} source feeds${anyStale ? ' · some >60 d old' : ' · all current'}`;
  document.getElementById('freshBody').innerHTML = rows.map(r => {
    const stale = r.ageDays !== null && r.ageDays > 60;
    return `<span class="${stale ? 'stale' : ''}">${r.cc} ${esc(r.op || '')}${r.d ? ' ' + r.d : ''}</span>`;
  }).join(' · ');
}
renderFreshness();
document.getElementById('freshToggle').onclick = () => {
  const strip = document.getElementById('freshStrip');
  const open = strip.classList.toggle('open');
  document.getElementById('freshToggle').setAttribute('aria-expanded', open);
};

/* ── Permalinks: #cc=ES&n=<node> or #cc=ES&line=<osm_id> ─── */
(function () {
  if (!location.hash) return;
  const params = new URLSearchParams(location.hash.slice(1));
  const cc = params.get('cc'); if (!cc) return;
  const wantN = params.get('n'), wantLine = params.get('line');
  const tryRestore = () => {
    const c = COUNTRIES[cc]; if (!c) return false;
    if (wantN) {
      indexNodesFor(cc);
      const p = (c.nodes || []).find(x => x.n === wantN); if (!p) return false;
      map.setView([p.lat, p.lon], 11);
      const nm = nodeMarkers.find(x => x.n === wantN && x.cc === cc);
      if (nm) nm.marker.openPopup();
      else L.popup({ maxWidth: 340 }).setLatLng([p.lat, p.lon]).setContent(popup(p, c.srcs, cc)).openOn(map);
      return true;
    }
    if (wantLine) {
      ensureLines(cc).then(lg => { if (!lg) return;
        lg.eachLayer(l => { const p = (l.feature || {}).properties || {};
          if (String(p.osm_id) === String(wantLine)) { map.setView(l.getBounds ? l.getBounds().getCenter() : l.getLatLng(), 11); l.openPopup(); } }); });
      return true;
    }
    return false;
  };
  // lazy countries only resolve after loadCountry — retry a few times
  let tries = 0; const iv = setInterval(() => { tries++; if (tryRestore() || tries > 20) clearInterval(iv); }, 400);
})();

/* ── Accordions ──────────────────────────────────────────── */
document.querySelectorAll('.acc').forEach(acc => {
  const head = acc.querySelector('.acc-head');
  head.onclick = () => {
    const open = acc.classList.toggle('open');
    head.setAttribute('aria-expanded', open);
  };
});

/* ── Corridor finder ─────────────────────────────────────── */
const corridorGeos = {}, corridorGroups = {};
let corridorMinMw = 100;
function updateCorridorVis() {
  const anyVisible = Object.values(sitesVisible).some(Boolean);
  document.getElementById('corridorBox').hidden = !anyVisible;
  document.getElementById('corridorOff').hidden = anyVisible;
}
async function ensureCorridor(cc) {
  if (corridorGeos[cc] !== undefined) return corridorGeos[cc];
  const mcc = MATRIX[cc] && MATRIX[cc].sites;
  const url = assetUrl((mcc && mcc.url) || `sites/${cc}.geojson`);
  try { corridorGeos[cc] = await (await fetch(url)).json(); } catch (e) { corridorGeos[cc] = null; }
  return corridorGeos[cc];
}
function slugifyEs(s) {
  return (s || '').toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
function corridorPopup(p, cc) {
  const disclaimer = "Contracted power is only verifiable through the building's meter / connection records (CUPS, DSO enquiry) — shortlist, then verify.";
  const mcc = MATRIX[cc] && MATRIX[cc].sites;
  const anchorKind = mcc && mcc.kind;
  const links = [];
  if (p.osm_id) links.push(`<a href="https://www.openstreetmap.org/way/${p.osm_id}" target="_blank" rel="noopener">OSM way</a>`);
  if (cc === 'ES') {
    links.push(`<a href="https://www1.sedecatastro.gob.es" target="_blank" rel="noopener">Sede Catastro</a>`);
    const slug = slugifyEs(p.municipio || p.node);
    if (slug) links.push(`<a href="https://www.idealista.com/venta-terrenos/${slug}/" target="_blank" rel="noopener">Idealista (${esc(p.municipio || p.node)})</a>`);
  }
  let s = `<div class="pp-head"><span class="pp-name">Candidate industrial site</span></div>` +
    `<div class="pp-reg">area ${(p.area_ha || 0).toFixed(1)} ha · ${(p.dist_km || 0).toFixed(1)} km from node</div>` +
    `<div class="pp-kind">near <b>${esc(p.node || '?')}</b> (${fmt(p.node_mw)} MW)</div>` +
    `<div class="pp-src">${links.join(' · ')}</div>` +
    `<div class="pp-meta pp-warnnote">${disclaimer}</div>`;
  if (anchorKind === 'generation')
    s += `<div class="pp-src">anchored to a generation hosting-capacity node — grid-strength proxy, not demand headroom</div>`;
  return s;
}
async function rebuildCorridor() {
  const list = document.getElementById('corridorList');
  for (const cc in corridorGroups) { if (corridorGroups[cc]) map.removeLayer(corridorGroups[cc]); delete corridorGroups[cc]; }
  const activeCcs = Object.keys(sitesVisible).filter(cc => sitesVisible[cc]);
  if (!activeCcs.length) { list.innerHTML = ''; return; }
  await Promise.all(activeCcs.map(ensureCorridor));   // fetch all sites files concurrently
  let allFeats = [];
  for (const cc of activeCcs) {
    const gj = await ensureCorridor(cc);
    if (!gj) continue;
    const feats = (gj.features || []).filter(f => (f.properties.node_mw || 0) >= corridorMinMw)
      .map(f => ({ ...f, properties: { ...f.properties, _cc: cc } }));
    allFeats = allFeats.concat(feats);
    corridorGroups[cc] = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
      renderer: CANVAS,
      style: { color: '#f9c74f', weight: 1.4, fillColor: '#f9c74f', fillOpacity: .25 },
      pointToLayer: (f, latlng) => L.circleMarker(latlng, { renderer: CANVAS, radius: 5, color: '#f9c74f', fillColor: '#f9c74f', fillOpacity: .7 }),
      onEachFeature: (f, l) => l.bindPopup(corridorPopup(f.properties || {}, cc), { maxWidth: 340 }),
    }).addTo(map);
  }
  const byCC = {};
  for (const f of allFeats) { const cc = f.properties._cc; if (!byCC[cc]) byCC[cc] = []; byCC[cc].push(f.properties); }
  if (!Object.keys(byCC).length) { list.innerHTML = '<p class="note">No sites at this MW threshold.</p>'; return; }
  let html = ''; let firstCC = true;
  for (const cc of activeCcs) {
    const props = byCC[cc]; if (!props || !props.length) continue;
    const cData = COUNTRIES[cc] || MANIFEST.find(m => m.cc === cc) || {};
    const ccName = cData.name || cc;
    const mcc2 = MATRIX[cc] && MATRIX[cc].sites;
    const isProxy = (mcc2 && mcc2.kind) === 'generation';
    const sorted = props.filter(p => p.area_ha).sort((a, b) => b.area_ha - a.area_ha).slice(0, 12);
    const maxMw = sorted.reduce((m, p) => Math.max(m, p.node_mw || 0), 0);
    const isOpen = firstCC; firstCC = false;
    const proxyBadge = isProxy ? `<span class="cor-proxy-badge" title="anchor is a generation hosting-capacity node — grid-strength proxy, NOT demand headroom">proxy</span>` : '';
    html += `<div class="cor-country">`;
    html += `<div class="cor-country-hdr" data-cc="${cc}"><span class="cor-chev${isOpen ? ' open' : ''}">▶</span>`;
    html += `<b class="num">${cc}</b>&ensp;<span style="color:var(--mut)">${esc(ccName)}</span>&ensp;${proxyBadge}`;
    html += `<span class="cor-meta">${sorted.length} sites · <span class="num">${fmt(maxMw)}</span> MW max</span></div>`;
    html += `<div class="cor-country-body${isOpen ? ' open' : ''}">`;
    if (sorted.length) {
      html += '<table class="cor-tbl"><thead><tr><th>#</th><th class="r">ha</th><th class="r">km</th><th>node (MW)</th></tr></thead><tbody>';
      sorted.forEach((p, i) => {
        html += `<tr data-cc="${cc}" data-si="${i}"><td class="r">${i + 1}</td>` +
          `<td class="r">${p.area_ha.toFixed(1)}</td><td class="r">${(p.dist_km || 0).toFixed(1)}</td>` +
          `<td>${esc(p.node || '?')} (<span class="num">${fmt(p.node_mw)}</span>)</td></tr>`;
      });
      html += '</tbody></table>';
    } else {
      html += '<p class="note" style="padding:6px 10px">No sites at this threshold.</p>';
    }
    html += '</div></div>';
  }
  list.innerHTML = html;
  list.querySelectorAll('.cor-country-hdr').forEach(hdr => {
    hdr.onclick = () => {
      const body = hdr.nextElementSibling; const chev = hdr.querySelector('.cor-chev');
      const nowOpen = body.classList.toggle('open'); chev.classList.toggle('open', nowOpen);
    };
  });
  list.querySelectorAll('[data-si][data-cc]').forEach(tr => tr.onclick = e => {
    e.stopPropagation();
    const cc = tr.dataset.cc;
    const sorted2 = (byCC[cc] || []).filter(p => p.area_ha).sort((a, b) => b.area_ha - a.area_ha).slice(0, 12);
    const p = sorted2[+tr.dataset.si]; if (!p) return;
    const gj = corridorGeos[cc]; if (!gj) return;
    const f = gj.features.find(x => x.properties.osm_id === p.osm_id && x.properties.node === p.node);
    if (!f) return;
    const cpt = f.geometry.type === 'Point' ? f.geometry.coordinates : f.geometry.coordinates[0][0];
    map.flyTo([cpt[1], cpt[0]], 14, { duration: 1.0 });
    const cg = corridorGroups[cc]; if (!cg) return;
    cg.eachLayer(l => { if ((l.feature || {}).properties.osm_id === p.osm_id && (l.feature || {}).properties.node === p.node) l.openPopup(); });
  });
}
// corridor data feeds the deal score's land component; rebuilds are chained so
// rapid toggles (select-all) can't interleave two rebuilds
const _origRebuildCorridor = rebuildCorridor;
let _corridorChain = Promise.resolve();
rebuildCorridor = function () {
  _corridorChain = _corridorChain.then(async () => {
    await _origRebuildCorridor();
    ctxDirty = true;
    if (dealMode) { drawNodes(); renderDealRank(); }
  });
  return _corridorChain;
};
document.querySelectorAll('#cormw button').forEach(b => b.onclick = async () => {
  document.querySelectorAll('#cormw button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); corridorMinMw = +b.dataset.cmw; await rebuildCorridor();
});
// (default candidate-sites init moved below marketsReady — see end of file)

/* ═══════════════ DEAL SCREEN — firming opportunity engine ═══════════════
   Business model: lease flexible (non-firm) import capacity at a node,
   firm it with behind-the-meter BESS sized to the curtailment duty.
   Score = where that wedge is most economic, down to site level.      */
const scoreCtx = { sitesByNode: new Map(), queuedByCC: {} };
let connMap = {};   // 'CC|NODE' → {fib, fac, land} km — pipelines/node_connectivity.py
fetch('data/node_connectivity.json').then(r => r.ok ? r.json() : {})
  .then(d => { connMap = d; ctxDirty = true; ctxGen++; if (dealMode) { drawNodes(); renderDealRank(); } })
  .catch(() => {});
// connectivity subscore: distance to fibre backbone + interconnection facility
function connScoreOf(cc, p) {
  const c = connMap[cc + '|' + p.n];
  if (!c) return { conn: 50, fib: null, fac: null, land: null };   // neutral when unknown
  const fibS = c.fib <= 5 ? 100 : c.fib <= 15 ? 75 : c.fib <= 40 ? 50 : 25;
  const facS = c.fac <= 10 ? 100 : c.fac <= 30 ? 75 : c.fac <= 80 ? 50 : 25;
  return { conn: Math.round(0.6 * fibS + 0.4 * facS), fib: c.fib, fac: c.fac, land: c.land };
}
function buildScoreCtx() {
  if (!ctxDirty) return;
  scoreCtx.sitesByNode.clear();
  for (const cc in corridorGeos) {
    const gj = corridorGeos[cc]; if (!gj) continue;
    for (const f of gj.features || []) {
      const key = cc + '|' + (f.properties.node || '');
      scoreCtx.sitesByNode.set(key, (scoreCtx.sitesByNode.get(key) || 0) + 1);
    }
  }
  scoreCtx.queuedByCC = {};
  for (const cc in COUNTRIES) {
    const q = (COUNTRIES[cc].nodes || []).filter(n => n.kind === 'queued' && n.lat && n.mw);
    if (q.length) scoreCtx.queuedByCC[cc] = q;
  }
  ctxDirty = false; ctxGen++;
}
// queued MW within 30 km — evidence of connection demand around the node
function queuedNear(cc, p) {
  const q = scoreCtx.queuedByCC[cc];
  if (!q) return null;                       // market publishes no queue data
  if (p._qGen === ctxGen) return p._q;
  const cosLat = Math.cos(p.lat * Math.PI / 180);
  let sum = 0;
  for (const n of q) {
    const dx = (n.lon - p.lon) * cosLat * 111.32, dy = (n.lat - p.lat) * 110.57;
    if (dx * dx + dy * dy <= 900) sum += n.mw || 0;   // 30 km radius
  }
  p._q = sum; p._qGen = ctxGen;
  return sum;
}
function layerKindOf(cc, p) {
  const c = COUNTRIES[cc]; if (!c) return '';
  const L_ = (c.layers || []).find(x => x.id === p.lay);
  return (L_ && L_.layer) || '';
}
// measured nodes: empirically firmable added load at the selected battery duration
// (from a year of metering) replaces published headroom as the screening figure
const measFirm = p => p.meas ? (dealDur === 2 ? p.meas.firm_2h_mw : p.meas.firm_8h_mw) : null;
function dealScore(cc, p) {
  const mH = measFirm(p);
  if (mH === null && (p.kind !== 'demand' || !(p.mw >= 5))) return null;
  buildScoreCtx();
  const sig = dealTarget + '|' + dealDur + '|' + ctxGen;
  if (p._sc && p._sc.sig === sig) return p._sc.val;
  const T = dealTarget, H = mH !== null ? mH : p.mw;
  const gap = Math.max(0, T - Math.min(H, T));
  const gapRatio = gap / T;
  // The wedge: a battery bridging a moderate shortfall is where firming pays.
  // Full headroom = plain direct connection (still pipeline, weaker wedge);
  // near-total shortfall = battery ≈ the whole load, uneconomic.
  const wedge = gapRatio === 0 ? 55
    : gapRatio <= 0.15 ? 85
    : gapRatio <= 0.45 ? 100
    : gapRatio <= 0.65 ? 78
    : gapRatio <= 0.85 ? 45 : 12;
  const kvN = parseFloat(p.kv) || 0;
  const lk = layerKindOf(cc, p);
  const infra = kvN >= 380 ? 100 : kvN >= 200 ? 85 : kvN >= 90 ? 65 : kvN > 0 ? 50
    : lk === 'transmission' ? 80 : 50;
  const nSites = scoreCtx.sitesByNode.get(cc + '|' + p.n) || 0;
  const sites = nSites >= 3 ? 100 : nSites >= 1 ? 75 : 35;
  const qMW = queuedNear(cc, p);
  const queue = qMW === null ? 55 : qMW <= 0 ? 40 : 40 + 60 * Math.min(1, qMW / 300);
  const cn = connScoreOf(cc, p);
  const score = Math.round(0.40 * wedge + 0.15 * infra + 0.10 * sites + 0.15 * queue + 0.20 * cn.conn);
  const tier = score >= 78 ? 'prime' : score >= 63 ? 'strong' : score >= 48 ? 'possible' : 'weak';
  const val = { score, tier, gap, gapRatio, nSites, qMW, H, meas: mH !== null, fib: cn.fib, fac: cn.fac, land: cn.land };
  p._sc = { sig, val };
  return val;
}
function bessFor(gap) {
  const mwh = gap * dealDur;
  return { mw: gap, mwh, capexM: mwh * 1000 * BESS_EUR_PER_KWH / 1e6 };
}
function firmingBlock(p, cc) {
  const sc = dealScore(cc, p); if (!sc) return '';
  const b = bessFor(sc.gap);
  const pinned = pins.some(x => x.cc === cc && x.n === p.n);
  let s = `<div class="pp-deal"><div class="pp-deal-head">` +
    `<span class="pp-deal-title">Firming screen</span>` +
    `<span class="score-pill tier-${sc.tier}">${sc.score}</span>` +
    `<span class="deal-sub">${TIER_LABEL[sc.tier]}</span></div>`;
  s += `<div class="pp-deal-grid">` +
    `<span>target load</span><b>${dealTarget} MW</b>` +
    `<span>${sc.meas ? `measured firmable · ${dealDur} h` : 'node headroom'}</span><b>${fmt(Math.round(sc.H))} MW</b>` +
    (sc.gap > 0
      ? `<span>firming gap</span><b>${fmt(sc.gap)} MW</b>` +
        `<span>BESS (${dealDur} h)</span><b>${fmt(b.mw)} MW / ${fmt(b.mwh)} MWh</b>` +
        `<span>BESS capex</span><b>≈ €${b.capexM.toFixed(1)} M</b>`
      : `<span>firming gap</span><b>none — direct-connect candidate</b>`) +
    `<span>sites ≤3 km</span><b>${sc.nSites || '0'}</b>` +
    (sc.qMW !== null ? `<span>queued ≤30 km</span><b>${fmt(Math.round(sc.qMW))} MW</b>` : '') +
    (sc.fib !== null ? `<span>fibre backbone</span><b>${sc.fib} km</b>` : '') +
    (sc.fac !== null ? `<span>interconnection</span><b>${sc.fac} km</b>` : '') +
    `</div>`;
  s += `<div class="deal-sub">flexible connection + firming ≈ 12–18 months to power vs 5–7 y conventional</div>`;
  s += `<button class="pin-btn${pinned ? ' pinned' : ''}" data-cc="${cc}" data-n="${esc(p.n)}">${pinned ? '✓ In pipeline' : '★ Add to pipeline'}</button>`;
  return s + `</div>`;
}

/* ── ranked opportunities ── */
function dealCandidates() {
  const out = [];
  for (const cc in COUNTRIES) {
    for (const p of COUNTRIES[cc].nodes || []) {
      const sc = dealScore(cc, p);
      if (sc) out.push({ cc, p, sc });
    }
  }
  out.sort((a, b) => b.sc.score - a.sc.score || (b.p.mw || 0) - (a.p.mw || 0));
  // the same physical substation can appear in two source layers (e.g. ES REE + CNMC),
  // sometimes with slightly different coords and a voltage suffix in one name —
  // keep the best-scoring node per normalized name AND per ~3 km cell
  const seen = new Set(), dedup = [];
  for (const x of out) {
    const nameKey = x.cc + '|' + x.p.n.toUpperCase().replace(/\s+\d{2,3}(\s*\/\s*\d+)?\s*(KV)?$/, '').trim();
    const cellKey = x.cc + '|' + Math.round(x.p.lat / 0.03) + '|' + Math.round(x.p.lon / 0.03);
    if (seen.has(nameKey) || seen.has(cellKey)) continue;
    seen.add(nameKey); seen.add(cellKey); dedup.push(x);
  }
  return dedup;
}
let dealMarket = 'ALL';
function refreshMarketFilter() {
  const sel = document.getElementById('dmarket');
  const cur = sel.value || 'ALL';
  const ccs = Object.keys(COUNTRIES).sort();
  sel.innerHTML = '<option value="ALL">All markets</option>' +
    ccs.map(cc => `<option value="${cc}"${cc === cur ? ' selected' : ''}>${cc} — ${esc(COUNTRIES[cc].name)}</option>`).join('');
  if (cur === 'ALL') sel.value = 'ALL';
}
function renderDealRank() {
  if (!dealMode) return;
  buildScoreCtx();
  refreshMarketFilter();
  const all = dealCandidates();
  const cands = dealMarket === 'ALL' ? all : all.filter(x => x.cc === dealMarket);
  const top = cands.slice(0, 15);
  const el = document.getElementById('dealRank');
  el.innerHTML = top.map((x, i) =>
    `<div class="rank-row" data-cc="${x.cc}" data-n="${esc(x.p.n)}" tabindex="0">` +
    `<span class="rank-i">${i + 1}</span>` +
    `<span class="rank-name">${esc(x.p.n)}${x.sc.meas ? ' <span class="meas-badge">meas</span>' : ''} <span class="rank-sub">· ${x.cc}${x.p.kv ? ' · ' + x.p.kv + ' kV' : ''} · ${fmt(Math.round(x.sc.H))} MW</span></span>` +
    `<span class="score-pill tier-${x.sc.tier}">${x.sc.score}</span></div>`
  ).join('') || '<p class="note">No demand nodes ≥5 MW in loaded markets.</p>';
  el.querySelectorAll('.rank-row').forEach(row => {
    const go = () => {
      const cc = row.dataset.cc, n = row.dataset.n;
      const p = (COUNTRIES[cc].nodes || []).find(x => x.n === n); if (!p) return;
      map.flyTo([p.lat, p.lon], 10, { duration: 1.0 });
      const nm = nodeMarkers.find(x => x.n === n && x.cc === cc);
      if (nm) nm.marker.openPopup();
      else L.popup({ maxWidth: 340 }).setLatLng([p.lat, p.lon]).setContent(popup(p, COUNTRIES[cc].srcs, cc)).openOn(map);
    };
    row.onclick = go;
    row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
  });
  const counts = { prime: 0, strong: 0, possible: 0, weak: 0 };
  let nMeas = 0;
  cands.forEach(x => { counts[x.sc.tier]++; if (x.sc.meas) nMeas++; });
  document.getElementById('dealStats').innerHTML =
    `<b>${fmt(cands.length)}</b> demand nodes screened at <b>${dealTarget} MW</b>` +
    `${dealMarket !== 'ALL' ? ' in ' + dealMarket : ''} — ` +
    `<b>${counts.prime}</b> prime · <b>${counts.strong}</b> strong · ${counts.possible} possible` +
    (nMeas ? ` · <b>${nMeas}</b> with measured profiles` : '') +
    `<br>All demand nodes ≥5 MW in loaded markets are shown, coloured by fit.`;
}

/* ── pipeline (persistent shortlist) ── */
const PIPE_KEY = 'oppmap_pipeline';
let pins = [];
try { pins = JSON.parse(localStorage.getItem(PIPE_KEY) || '[]'); } catch (e) { pins = []; }
const STAGES = ['Screened', 'Contacted', 'Verifying', 'Term sheet'];
function savePins() { localStorage.setItem(PIPE_KEY, JSON.stringify(pins)); renderPipeline(); }
function addPin(cc, p) {
  if (pins.some(x => x.cc === cc && x.n === p.n)) return;
  pins.push({ cc, n: p.n, mw: p.mw, kv: p.kv, lat: p.lat, lon: p.lon, reg: p.reg || '', lay: p.lay, stage: 'Screened' });
  savePins();
}
function removePin(cc, n) { pins = pins.filter(x => !(x.cc === cc && x.n === n)); savePins(); }
function renderPipeline() {
  const card = document.getElementById('pipeCard');
  card.hidden = !pins.length;
  document.getElementById('pipeCount').textContent = pins.length ? `${pins.length} node${pins.length > 1 ? 's' : ''}` : '';
  document.getElementById('pipeList').innerHTML = pins.map((x, i) =>
    `<div class="pipe-row">` +
    `<span class="pipe-name" data-i="${i}" title="Fly to node">${esc(x.n)} <span class="num">${x.cc} · ${fmt(x.mw)} MW</span></span>` +
    `<select class="pipe-stage" data-i="${i}">${STAGES.map(st => `<option${st === x.stage ? ' selected' : ''}>${st}</option>`).join('')}</select>` +
    `<button class="pipe-del" data-i="${i}" title="Remove" aria-label="Remove from pipeline">✕</button></div>`
  ).join('');
  document.querySelectorAll('#pipeList .pipe-name').forEach(el => el.onclick = async () => {
    const x = pins[+el.dataset.i]; if (!x) return;
    const mf = MANIFEST.find(m => m.cc === x.cc);
    if (!COUNTRIES[x.cc] && mf) await loadCountry(x.cc, mf.data_url);
    map.flyTo([x.lat, x.lon], 10, { duration: 1.0 });
    const nm = nodeMarkers.find(m => m.n === x.n && m.cc === x.cc);
    if (nm) nm.marker.openPopup();
    else {
      const p = ((COUNTRIES[x.cc] || {}).nodes || []).find(n => n.n === x.n);
      if (p) L.popup({ maxWidth: 340 }).setLatLng([p.lat, p.lon]).setContent(popup(p, COUNTRIES[x.cc].srcs, x.cc)).openOn(map);
    }
  });
  document.querySelectorAll('#pipeList .pipe-stage').forEach(el => el.onchange = () => {
    pins[+el.dataset.i].stage = el.value; savePins();
  });
  document.querySelectorAll('#pipeList .pipe-del').forEach(el => el.onclick = () => {
    const x = pins[+el.dataset.i]; if (x) removePin(x.cc, x.n);
  });
}
document.addEventListener('click', e => {
  const btn = e.target.closest('.pin-btn'); if (!btn) return;
  const cc = btn.dataset.cc, n = btn.dataset.n;
  const p = ((COUNTRIES[cc] || {}).nodes || []).find(x => x.n === n); if (!p) return;
  const pinned = pins.some(x => x.cc === cc && x.n === n);
  if (pinned) removePin(cc, n); else addPin(cc, p);
  btn.classList.toggle('pinned', !pinned);
  btn.textContent = !pinned ? '✓ In pipeline' : '★ Add to pipeline';
});
document.getElementById('csvBtn').onclick = () => {
  const head = ['market', 'node', 'region', 'kv', 'layer', 'headroom_mw', 'evidence', 'target_mw', 'firming_gap_mw',
    'bess_mw', 'bess_mwh', 'bess_capex_eur_m', 'fit_score', 'tier', 'sites_3km', 'queued_30km_mw',
    'stage', 'lat', 'lon', 'permalink'];
  const rows = pins.map(x => {
    const p = ((COUNTRIES[x.cc] || {}).nodes || []).find(n => n.n === x.n) || x;
    const sc = dealScore(x.cc, p);
    const b = sc ? bessFor(sc.gap) : { mw: '', mwh: '', capexM: '' };
    const link = `${location.origin}${location.pathname}#cc=${x.cc}&n=${encodeURIComponent(x.n)}`;
    return [x.cc, x.n, x.reg, x.kv || '', x.lay, sc ? Math.round(sc.H) : x.mw,
      sc && sc.meas ? 'measured_profile' : 'published', dealTarget,
      sc ? sc.gap : '', b.mw, b.mwh, b.capexM === '' ? '' : b.capexM.toFixed(1),
      sc ? sc.score : '', sc ? sc.tier : '', sc ? sc.nSites : '', sc && sc.qMW !== null ? Math.round(sc.qMW) : '',
      x.stage, x.lat, x.lon, link]
      .map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',');
  });
  const csv = head.join(',') + '\n' + rows.join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `neura-pipeline-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
  toast(`Exported ${pins.length} pipeline node${pins.length > 1 ? 's' : ''} to CSV`);
};

/* ── mode + controls wiring ── */
function updateLoadAllBtn() {
  const btn = document.getElementById('loadAllBtn');
  const missing = MANIFEST.filter(m => !COUNTRIES[m.cc]);
  btn.hidden = !missing.length;
  btn.textContent = `Load all markets (${Object.keys(COUNTRIES).length}/${Object.keys(FREE).length + MANIFEST.length} loaded)`;
}
document.getElementById('loadAllBtn').onclick = async () => {
  const missing = MANIFEST.filter(m => !COUNTRIES[m.cc]);
  toast(`Loading ${missing.length} markets…`, { spin: true });
  await Promise.all(missing.map(m => loadCountry(m.cc, m.data_url)));
  toast('All markets loaded');
  ctxDirty = true;
  if (dealMode) { drawNodes(); renderDealRank(); }
  flyTo([36, -10, 62, 20]);
};
function setMode(mode) {
  dealMode = mode === 'deal';
  document.querySelectorAll('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  document.getElementById('dealCard').hidden = !dealMode;
  const thrCard = document.getElementById('thr').closest('section');
  if (thrCard) thrCard.hidden = dealMode;
  drawNodes(); renderLegend();
  if (dealMode) { buildScoreCtx(); renderDealRank(); updateLoadAllBtn(); }
}
document.querySelectorAll('#modeSeg button').forEach(b => b.onclick = () => setMode(b.dataset.mode));
document.getElementById('dmarket').onchange = e => { dealMarket = e.target.value; renderDealRank(); };
document.getElementById('screenCsvBtn').onclick = () => {
  buildScoreCtx();
  const all = dealCandidates();
  const cands = (dealMarket === 'ALL' ? all : all.filter(x => x.cc === dealMarket)).slice(0, 500);
  const head = ['rank', 'market', 'node', 'region', 'kv', 'layer', 'headroom_mw', 'evidence', 'target_mw', 'firming_gap_mw',
    'bess_mw', 'bess_mwh', 'bess_capex_eur_m', 'fit_score', 'tier', 'sites_3km', 'queued_30km_mw', 'lat', 'lon', 'permalink'];
  const rows = cands.map((x, i) => {
    const b = bessFor(x.sc.gap);
    const link = `${location.origin}${location.pathname}#cc=${x.cc}&n=${encodeURIComponent(x.p.n)}`;
    return [i + 1, x.cc, x.p.n, x.p.reg || '', x.p.kv || '', x.p.lay, Math.round(x.sc.H),
      x.sc.meas ? 'measured_profile' : 'published', dealTarget, x.sc.gap,
      b.mw, b.mwh, b.capexM.toFixed(1), x.sc.score, x.sc.tier, x.sc.nSites,
      x.sc.qMW !== null ? Math.round(x.sc.qMW) : '', x.p.lat, x.p.lon, link]
      .map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',');
  });
  const csv = head.join(',') + '\n' + rows.join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `neura-screen-${dealMarket === 'ALL' ? 'all' : dealMarket}-${dealTarget}mw.csv`;
  a.click(); URL.revokeObjectURL(a.href);
  toast(`Exported ${cands.length} screened nodes to CSV`);
};
document.querySelectorAll('#dtarget button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#dtarget button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); dealTarget = +b.dataset.mw;
  drawNodes(); renderDealRank(); renderLegend();
});
document.querySelectorAll('#ddur button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#ddur button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); dealDur = +b.dataset.h;
  // duration changes measured-node firmable MW → scores and marker sizes move too
  drawNodes(); renderDealRank();
});
document.querySelectorAll('#dcolor button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#dcolor button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); dealColor = b.dataset.c;
  drawNodes(); renderLegend();
});
renderPipeline();
updateLoadAllBtn();

/* ── matrix select-all ── */
const matrixBoxes = () => [...document.querySelectorAll('#matrixTbl input[type=checkbox]')];
function updateMatrixAllBtn() {
  const boxes = matrixBoxes();
  document.getElementById('matrixAllBtn').textContent =
    boxes.length && boxes.every(b => b.checked) ? 'Clear all' : 'Select all';
}
document.getElementById('matrixAllBtn').onclick = () => {
  const boxes = matrixBoxes();
  const target = !boxes.every(b => b.checked);
  boxes.forEach(b => { if (b.checked !== target) b.click(); });
  updateMatrixAllBtn();
};
// clicking a row label toggles that layer across every market
document.getElementById('matrixTbl').addEventListener('click', e => {
  const td = e.target.closest('td.rowlbl'); if (!td) return;
  const boxes = [...td.parentElement.querySelectorAll('input[type=checkbox]')];
  if (!boxes.length) return;
  const target = !boxes.every(b => b.checked);
  boxes.forEach(b => { if (b.checked !== target) b.click(); });
  updateMatrixAllBtn();
});
document.getElementById('matrixTbl').addEventListener('change', updateMatrixAllBtn);

// ── every market loads at startup (progress, retry, sites deferred until after) ──
const marketsReady = (async function () {
  const missing = MANIFEST.filter(m => !COUNTRIES[m.cc]);
  if (!missing.length) return;
  let done = 0, failed = [];
  toast(`Loading markets… 0/${missing.length}`, { spin: true });
  await Promise.all(missing.map(async m => {
    let ok = await loadCountry(m.cc, m.data_url, { quiet: true });
    if (!ok) ok = await loadCountry(m.cc, m.data_url, { quiet: true });   // one retry
    if (!ok) failed.push(m);
    done++;
    toast(`Loading markets… ${done}/${missing.length}`, { spin: true });
  }));
  failed.forEach(m => loadRect(m));   // click-to-load only for real failures
  drawNodes(); renderFreshness(); renderKpis(); updateLoadAllBtn(); updateMatrixAllBtn();
  if (dealMode) renderDealRank();
  if (failed.length) toast(`${failed.length} market${failed.length > 1 ? 's' : ''} failed — click the dashed box to retry`);
  else toast(`All ${Object.keys(COUNTRIES).length} markets loaded`);
})();

// default: candidate-sites layer ON for every market that has one
// (waits for the market batch so grid lines render before the 24 MB of site polygons)
(async function () {
  try { await marketsReady; } catch (e) { /* proceed regardless */ }
  for (const cc of Object.keys(MATRIX)) {
    const mcc = MATRIX[cc] && MATRIX[cc].sites;
    if (!mcc || mcc.gap) continue;
    sitesVisible[cc] = true;
    const cb = document.getElementById('sites_' + cc); if (cb) cb.checked = true;
  }
  updateCorridorVis(); rebuildCorridor();
})();


/* ── methodology modal ── */
// mini-maps inside the methodology page: real data, no interaction — each is a tiny
// self-contained Leaflet instance (the main map's shared CANVAS renderer is per-map,
// so these use their own default renderers)
let _methVizDone = false, _methVizTimer = null;
const _methMaps = [];
function initMethViz() {
  if (_methVizDone) return;
  if (document.getElementById('methModal').hidden) return;   // build only while visible (zero-size containers break Leaflet)
  // opened before startup loads finished (e.g. #methodology hash) — retry until the data is in
  if (!(COUNTRIES.ES && COUNTRIES.ES.nodes) || !(COUNTRIES.PT && COUNTRIES.PT.nodes) || corridorGeos.ES === undefined) {
    clearTimeout(_methVizTimer);
    _methVizTimer = setTimeout(initMethViz, 1200);
    return;
  }
  _methVizDone = true;
  const mk = id => {
    const el = document.getElementById(id); if (!el) return null;
    const m = L.map(el, { zoomControl: false, dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false, attributionControl: false });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { subdomains: 'abcd', maxZoom: 19 }).addTo(m);
    return m;
  };
  // 01 · what the map shows — central Spain, real nodes on the red ramp + queued/indicative
  const m1 = mk('methMapNodes');
  if (m1) {
    m1.setView([40.3, -3.7], 7);
    for (const p of (COUNTRIES.ES && COUNTRIES.ES.nodes) || []) {
      if (!p.lat || p.lat < 38.6 || p.lat > 42 || p.lon < -6.2 || p.lon > -1.2) continue;
      const isQ = p.kind === 'queued', isI = p.kind === 'indicative';
      if (p.kind !== 'demand' && !isQ && !isI) continue;
      L.circleMarker([p.lat, p.lon], {
        radius: isI ? 3 : Math.max(2.5, Math.min(11, Math.sqrt(p.mw || 1) * .45)),
        color: isQ ? '#c4b5fd' : isI ? '#5eead4' : 'rgba(245,247,255,.35)', weight: .8,
        fillColor: isQ ? '#8a5cf6' : isI ? '#0d9488' : col(p.mw), fillOpacity: .8,
      }).addTo(m1);
    }
  }
  // 06 · measured tier — greater Lisbon, orange-ring measured nodes over the proxy layer
  const m2 = mk('methMapMeas');
  if (m2) {
    m2.setView([38.82, -9.13], 10);
    for (const p of (COUNTRIES.PT && COUNTRIES.PT.nodes) || []) {
      if (!p.lat) continue;
      if (p.meas) {
        L.circleMarker([p.lat, p.lon], { radius: Math.max(4, Math.min(10, Math.sqrt(p.meas.firm_8h_mw || 9) * 1.1)),
          color: '#ff9b1f', weight: 2, fillColor: col(p.meas.firm_8h_mw || 0), fillOpacity: .85 }).addTo(m2);
      } else {
        L.circleMarker([p.lat, p.lon], { radius: 2.5, color: 'rgba(139,152,169,.4)', weight: .7,
          fillColor: '#5f6d80', fillOpacity: .5 }).addTo(m2);
      }
    }
  }
  // 07 · GB assets — Midlands window, straight from the asset geojson
  const m3 = mk('methMapAssets');
  if (m3 && MATRIX.GB && MATRIX.GB.assets && MATRIX.GB.assets.url) {
    m3.setView([52.6, -1.6], 7);
    fetch(MATRIX.GB.assets.url).then(r => r.ok ? r.json() : null).then(gj => {
      if (!gj) return;
      for (const f of gj.features || []) {
        const p = f.properties, la = f.geometry.coordinates[1], lo = f.geometry.coordinates[0];
        if (la < 51 || la > 54.4 || lo < -3.6 || lo > .6) continue;
        const op = p.status === 'Operational';
        L.circleMarker([la, lo], { radius: Math.max(2, Math.min(9, Math.sqrt(p.mw || 1) * .55)),
          color: op ? 'rgba(240,246,255,.55)' : 'rgba(240,246,255,.2)', weight: op ? .9 : .5,
          fillColor: (typeof ASSET_COLOR !== 'undefined' && ASSET_COLOR[p.tech]) || '#fbbf24',
          fillOpacity: op ? .9 : .4 }).addTo(m3);
      }
    }).catch(() => {});
  }
  // 08 · candidate sites — the ES node with the most industrial polygons within 3 km
  const m4 = mk('methMapSites');
  if (m4) {
    m4.setView([41.65, -0.9], 11);   // vector layers need a view BEFORE addTo, else the renderer never attaches
    buildScoreCtx();
    let best = null, bestN = 0;
    scoreCtx.sitesByNode.forEach((n, k) => { if (k.startsWith('ES|') && n > bestN) { bestN = n; best = k.slice(3); } });
    const gj = corridorGeos.ES;
    const fs = gj && best ? (gj.features || []).filter(f => (f.properties || {}).node === best) : [];
    if (fs.length) {
      const lay = L.geoJSON({ type: 'FeatureCollection', features: fs },
        { style: { color: '#f7c04a', weight: 1.2, fillColor: '#f7c04a', fillOpacity: .35 } }).addTo(m4);
      const p = ((COUNTRIES.ES && COUNTRIES.ES.nodes) || []).find(n => n.n === best);
      if (p && p.lat) L.circleMarker([p.lat, p.lon], { radius: 9, color: '#f5f7ff', weight: 1.5,
        fillColor: col(p.mw), fillOpacity: .85 }).addTo(m4);
      m4.fitBounds(lay.getBounds().pad(.4), { maxZoom: 13 });
    }
  }
  _methMaps.push(...[m1, m2, m3, m4].filter(Boolean));
  requestAnimationFrame(() => _methMaps.forEach(m => m.invalidateSize()));
}
(function () {
  const modal = document.getElementById('methModal');
  const open = () => {
    modal.hidden = false; document.body.style.overflow = 'hidden';
    initMethViz();
    requestAnimationFrame(() => _methMaps.forEach(m => m.invalidateSize()));
  };
  const close = () => { modal.hidden = true; document.body.style.overflow = ''; };
  document.getElementById('methBtn').onclick = open;
  const side = document.getElementById('methOpen2'); if (side) side.onclick = open;
  document.getElementById('methClose').onclick = close;
  modal.querySelector('.mm-backdrop').onclick = close;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !modal.hidden) close(); });
  const cta = document.getElementById('methToFunnel');
  if (cta) cta.onclick = () => { close(); setMode('deal'); };
  if (location.hash === '#methodology') open();
})();
