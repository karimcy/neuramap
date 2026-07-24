/* ═══════════════════════════════════════════════════════════════
   OpportunityMap — Results workspace
   Unified screen over grid nodes (headroom) + GB assets (potential
   capacity), with filters, sorting, detail panel and CRM state
   shared with the map page via localStorage.
   Scoring formulas mirror js/app.js — keep in sync.
   ═══════════════════════════════════════════════════════════════ */
'use strict';

const COUNTRIES = {}; Object.assign(COUNTRIES, FREE);
let siteCounts = {}, ptProfiles = {}, records = [], queuedByCC = {};
let target = 50, dur = 8;
const BESS_EUR_PER_KWH = 145;
const TIER_COLOR = { prime: '#34d399', strong: '#2dd4bf', possible: '#60a5fa', weak: '#475569' };

/* CRM state (interop with map page) */
let pins = [];
try { pins = JSON.parse(localStorage.getItem('oppmap_pipeline') || '[]'); } catch (e) {}
let assetCrm = {};
try { assetCrm = JSON.parse(localStorage.getItem('oppmap_assets_crm') || '{}'); } catch (e) {}
let notes = {};
try { notes = JSON.parse(localStorage.getItem('oppmap_notes') || '{}'); } catch (e) {}
const STAGES = ['—', 'Screened', 'Contacted', 'Verifying', 'Term sheet'];
const savePins = () => localStorage.setItem('oppmap_pipeline', JSON.stringify(pins));
const saveAssetCrm = () => localStorage.setItem('oppmap_assets_crm', JSON.stringify(assetCrm));
const saveNotes = () => localStorage.setItem('oppmap_notes', JSON.stringify(notes));

const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmt = n => (n || 0).toLocaleString('en-US');
function assetUrl(u) {
  if (!u) return u;
  if (/^(https?:|data:)/i.test(u)) return u;
  return u.startsWith('/') ? u.slice(1) : u;
}

/* ── scoring (mirror of app.js) ── */
function layerKindOf(cc, p) {
  const c = COUNTRIES[cc]; if (!c) return '';
  const L_ = (c.layers || []).find(x => x.id === p.lay);
  return (L_ && L_.layer) || '';
}
function queuedNear(cc, p) {
  const q = queuedByCC[cc];
  if (!q || !q.length) return null;
  const cosLat = Math.cos(p.lat * Math.PI / 180);
  let sum = 0;
  for (const n of q) {
    const dx = (n.lon - p.lon) * cosLat * 111.32, dy = (n.lat - p.lat) * 110.57;
    if (dx * dx + dy * dy <= 900) sum += n.mw || 0;
  }
  return sum;
}
function scoreNode(cc, p) {
  if (p.kind !== 'demand' || !(p.mw >= 5)) return null;
  const T = target, H = p.mw;
  const gap = Math.max(0, T - Math.min(H, T));
  const gapRatio = gap / T;
  const wedge = gapRatio === 0 ? 55 : gapRatio <= 0.15 ? 85 : gapRatio <= 0.45 ? 100
    : gapRatio <= 0.65 ? 78 : gapRatio <= 0.85 ? 45 : 12;
  const kvN = parseFloat(p.kv) || 0;
  const lk = layerKindOf(cc, p);
  const infra = kvN >= 380 ? 100 : kvN >= 200 ? 85 : kvN >= 90 ? 65 : kvN > 0 ? 50
    : lk === 'transmission' ? 80 : 50;
  const nSites = siteCounts[cc + '|' + p.n] || 0;
  const sites = nSites >= 3 ? 100 : nSites >= 1 ? 75 : 35;
  const qMW = queuedNear(cc, p);
  const queue = qMW === null ? 55 : qMW <= 0 ? 40 : 40 + 60 * Math.min(1, qMW / 300);
  const score = Math.round(0.45 * wedge + 0.20 * infra + 0.15 * sites + 0.20 * queue);
  const tier = score >= 78 ? 'prime' : score >= 63 ? 'strong' : score >= 48 ? 'possible' : 'weak';
  return { score, tier, gap, nSites, qMW };
}
const bessCapexM = gap => gap * dur * 1000 * BESS_EUR_PER_KWH / 1e6;

/* ── data load ── */
async function loadAll() {
  const lazy = MANIFEST.map(m => fetch(assetUrl(m.data_url)).then(r => r.json()).then(d => { COUNTRIES[m.cc] = d; }).catch(() => {}));
  const extras = [
    fetch('data/site_counts.json').then(r => r.json()).then(d => { siteCounts = d; }).catch(() => {}),
    fetch('data/pt_profiles.json').then(r => r.ok ? r.json() : {}).then(d => { ptProfiles = d; }).catch(() => {}),
    fetch('data/gb_assets.geojson').then(r => r.ok ? r.json() : null).catch(() => null),
  ];
  const [, , assetsGj] = await Promise.all([Promise.all(lazy), extras[0], extras[2], extras[1]]);
  for (const cc in COUNTRIES) {
    queuedByCC[cc] = (COUNTRIES[cc].nodes || []).filter(n => n.kind === 'queued' && n.lat && n.mw);
  }
  buildRecords(assetsGj);
}

function buildRecords(assetsGj) {
  records = [];
  const seen = new Set();
  for (const cc in COUNTRIES) {
    for (const p of COUNTRIES[cc].nodes || []) {
      if (p.kind !== 'demand' || !(p.mw >= 5) || !p.lat) continue;
      const nameKey = cc + '|' + p.n.toUpperCase().replace(/\s+\d{2,3}(\s*\/\s*\d+)?\s*(KV)?$/, '').trim();
      const cellKey = cc + '|' + Math.round(p.lat / 0.03) + '|' + Math.round(p.lon / 0.03);
      if (seen.has(nameKey) || seen.has(cellKey)) continue;
      seen.add(nameKey); seen.add(cellKey);
      records.push({
        key: 'n|' + cc + '|' + p.n, type: 'node', cc, name: p.n, mw: p.mw,
        kv: p.kv || '', region: p.reg || '', lay: p.lay, lat: p.lat, lon: p.lon,
        measured: false, p,
      });
    }
  }
  // PT measured substations (E-Redes pilot) — measured tier
  for (const [name, prof] of Object.entries(ptProfiles)) {
    records.push({
      key: 'n|PT|' + name, type: 'node', cc: 'PT', name, mw: Math.max(5, Math.round(prof.min_headroom_mw)),
      kv: 60, region: 'E-Redes', lay: null, lat: prof.lat || null, lon: prof.lon || null,
      measured: true, prof, p: { kind: 'demand', mw: Math.max(5, Math.round(prof.min_headroom_mw)), kv: 60, n: name },
    });
  }
  for (const f of (assetsGj && assetsGj.features) || []) {
    const p = f.properties;
    records.push({
      key: 'a|' + p.repd_id, type: 'asset', cc: 'GB', name: p.name, mw: p.mw,
      kv: '', region: p.county || '', tech: p.tech, status: p.status, yearOp: p.year_op,
      operator: p.operator, tec: p.tec, repdId: p.repd_id, storageType: p.storage_type,
      lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0],
    });
  }
  rescore();
}
function rescore() {
  for (const r of records) {
    if (r.type === 'node') {
      const sc = scoreNode(r.cc, r.p);
      r.score = sc ? sc.score : null; r.tier = sc ? sc.tier : null;
      r.gap = sc ? sc.gap : null; r.nSites = sc ? sc.nSites : 0; r.qMW = sc ? sc.qMW : null;
    }
  }
}

/* ── filters/sort state ── */
const F = {
  q: '', type: 'all', markets: new Set(), tiers: new Set(['prime', 'strong']),
  status: new Set(['Operational', 'Under Construction', 'Awaiting Construction']),
  tech: new Set(['battery', 'hybrid']), minMw: 0, measured: false, pinned: false,
};
let sortKey = 'mw', sortDir = -1, page = 0, selKey = null;
const PAGE = 150;

function pinInfo(r) {
  if (r.type === 'node') {
    const x = pins.find(x => x.cc === r.cc && x.n === r.name);
    return x ? { pinned: true, stage: x.stage } : { pinned: false, stage: null };
  }
  const a = assetCrm[r.repdId];
  return { pinned: !!(a && a.pinned), stage: a && a.stage !== '—' ? a.stage : null };
}
function filtered() {
  const q = F.q.toLowerCase();
  return records.filter(r => {
    if (F.type !== 'all' && r.type !== F.type) return false;
    if (F.markets.size && !F.markets.has(r.cc)) return false;
    if (r.mw < F.minMw) return false;
    if (r.type === 'node') {
      if (r.tier && F.tiers.size && !F.tiers.has(r.tier)) return false;
      if (F.measured && !r.measured) return false;
    } else {
      if (F.status.size && !F.status.has(r.status)) return false;
      if (F.tech.size && !F.tech.has(r.tech)) return false;
      if (F.measured) return false;
    }
    if (F.pinned && !pinInfo(r).pinned) return false;
    if (q) {
      const hay = (r.name + ' ' + (r.operator || '') + ' ' + r.region + ' ' + r.cc).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
function sorted(rows) {
  const dir = sortDir;
  const val = r => {
    switch (sortKey) {
      case 'name': return r.name || '';
      case 'cc': return r.cc;
      case 'type': return r.type === 'asset' ? r.tech : 'node';
      case 'mw': return r.mw || 0;
      case 'score': return r.score ?? -1;
      case 'gap': return r.gap ?? 1e9;
      case 'bess': return r.gap != null ? bessCapexM(r.gap) : 1e9;
      case 'kv': return parseFloat(r.kv) || 0;
      case 'status': return r.type === 'asset' ? (r.yearOp || 9999) : 0;
      default: return 0;
    }
  };
  return rows.sort((a, b) => {
    const va = val(a), vb = val(b);
    if (typeof va === 'string') return dir * va.localeCompare(vb);
    return dir * (va - vb);
  });
}

/* ── table render ── */
const COLS = [
  ['pin', '★'], ['name', 'Name'], ['cc', 'Mkt'], ['type', 'Type'], ['mw', 'MW'],
  ['score', 'Fit'], ['gap', 'Gap MW'], ['bess', 'BESS €M'], ['kv', 'kV'], ['status', 'Status / stage'],
];
function renderHead() {
  document.getElementById('rHead').innerHTML = COLS.map(([k, lbl]) =>
    `<th data-k="${k}">${lbl}${sortKey === k ? `<span class="dir">${sortDir > 0 ? '▲' : '▼'}</span>` : ''}</th>`).join('');
  document.querySelectorAll('#rHead th').forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    if (k === 'pin') return;
    if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
    page = 0; renderTable();
  });
}
function typeBadge(r) {
  if (r.type === 'node') return `<span class="type-badge type-node">node</span>`;
  return `<span class="type-badge type-${r.tech}">${r.tech}</span>`;
}
function mwLabel(r) {
  if (r.type === 'node') return `${fmt(r.mw)}`;
  return `${fmt(r.mw)}`;
}
function renderTable() {
  renderHead();
  const rows = sorted(filtered());
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  page = Math.min(page, pages - 1);
  const slice = rows.slice(page * PAGE, (page + 1) * PAGE);
  document.getElementById('rBody').innerHTML = slice.map(r => {
    const pi = pinInfo(r);
    const scoreCell = r.score != null
      ? `<span class="score-pill tier-${r.tier}">${r.score}</span>` : '<span class="td-sub">—</span>';
    const statusCell = r.type === 'asset'
      ? `${esc(r.status)}${r.yearOp ? ` <span class="td-sub">· ${r.yearOp}</span>` : ''}`
      : (r.measured ? '<span class="td-sub">measured profile</span>' : '<span class="td-sub">published</span>');
    const stage = pi.stage ? ` <span class="stage-tag">${esc(pi.stage)}</span>` : '';
    const noteDot = notes[r.key] ? ' <span title="has notes">✎</span>' : '';
    return `<tr data-key="${esc(r.key)}" class="${selKey === r.key ? 'sel' : ''}">` +
      `<td><span class="star ${pi.pinned ? 'on' : ''}" data-key="${esc(r.key)}">${pi.pinned ? '★' : '☆'}</span></td>` +
      `<td class="td-name">${typeBadge(r)}${esc(r.name)}${noteDot}<div class="td-sub">${esc(r.region || '')}${r.operator ? ' · ' + esc(r.operator) : ''}</div></td>` +
      `<td class="num">${r.cc}</td>` +
      `<td>${r.type === 'asset' ? (r.status === 'Operational' ? 'built' : 'potential') : 'grid node'}</td>` +
      `<td class="num">${mwLabel(r)}</td>` +
      `<td>${scoreCell}</td>` +
      `<td class="num">${r.gap != null ? fmt(r.gap) : '—'}</td>` +
      `<td class="num">${r.gap != null ? bessCapexM(r.gap).toFixed(1) : '—'}</td>` +
      `<td class="num">${r.kv || '—'}</td>` +
      `<td>${statusCell}${stage}</td></tr>`;
  }).join('');
  document.getElementById('rPager').innerHTML =
    `<button id="pgPrev" ${page === 0 ? 'disabled' : ''}>‹ Prev</button>` +
    `<span>${fmt(rows.length)} results · page ${page + 1}/${pages}</span>` +
    `<button id="pgNext" ${page >= pages - 1 ? 'disabled' : ''}>Next ›</button>`;
  document.getElementById('pgPrev').onclick = () => { page--; renderTable(); };
  document.getElementById('pgNext').onclick = () => { page++; renderTable(); };
  document.querySelectorAll('#rBody tr').forEach(tr => tr.onclick = e => {
    if (e.target.closest('.star')) return;
    openDetail(tr.dataset.key);
  });
  document.querySelectorAll('#rBody .star').forEach(el => el.onclick = e => {
    e.stopPropagation(); togglePin(el.dataset.key);
  });
  renderStats(rows);
}
function renderStats(rows) {
  const nodes = rows.filter(r => r.type === 'node');
  const assets = rows.filter(r => r.type === 'asset');
  const bat = assets.filter(r => r.tech !== 'solar');
  const prime = nodes.filter(r => r.tier === 'prime').length;
  const strong = nodes.filter(r => r.tier === 'strong').length;
  const pinned = rows.filter(r => pinInfo(r).pinned).length;
  document.getElementById('rStats').innerHTML =
    `<div class="card-title">Current screen</div>` +
    `<div class="deal-stats">` +
    `<b>${fmt(rows.length)}</b> results — <b>${fmt(nodes.length)}</b> nodes (${prime} prime · ${strong} strong) · ` +
    `<b>${fmt(assets.length)}</b> assets<br>` +
    `node headroom <b>${fmt(Math.round(nodes.reduce((s, r) => s + r.mw, 0) / 100) / 10)} GW</b> · ` +
    `BESS potential capacity <b>${fmt(Math.round(bat.reduce((s, r) => s + r.mw, 0)))} MW</b><br>` +
    `<b>${pinned}</b> in pipeline</div>`;
}

/* ── CRM actions ── */
function togglePin(key) {
  const r = records.find(x => x.key === key); if (!r) return;
  if (r.type === 'node') {
    const i = pins.findIndex(x => x.cc === r.cc && x.n === r.name);
    if (i >= 0) pins.splice(i, 1);
    else pins.push({ cc: r.cc, n: r.name, mw: r.mw, kv: r.kv, lat: r.lat, lon: r.lon, reg: r.region, lay: r.lay, stage: 'Screened' });
    savePins();
  } else {
    const a = assetCrm[r.repdId] || {};
    a.pinned = !a.pinned; if (a.pinned && !a.stage) a.stage = 'Screened';
    assetCrm[r.repdId] = a; saveAssetCrm();
  }
  renderTable();
  if (selKey === key) openDetail(key);
}
function setStage(key, stage) {
  const r = records.find(x => x.key === key); if (!r) return;
  if (r.type === 'node') {
    let x = pins.find(x => x.cc === r.cc && x.n === r.name);
    if (!x && stage !== '—') {
      x = { cc: r.cc, n: r.name, mw: r.mw, kv: r.kv, lat: r.lat, lon: r.lon, reg: r.region, lay: r.lay, stage };
      pins.push(x);
    } else if (x) {
      if (stage === '—') pins = pins.filter(p => p !== x); else x.stage = stage;
    }
    savePins();
  } else {
    const a = assetCrm[r.repdId] || {};
    a.stage = stage; if (stage !== '—') a.pinned = true;
    assetCrm[r.repdId] = a; saveAssetCrm();
  }
  renderTable();
}

/* ── detail panel ── */
function openDetail(key) {
  selKey = key;
  const r = records.find(x => x.key === key); if (!r) return;
  const d = document.getElementById('rDetail');
  const pi = pinInfo(r);
  let h = `<button class="d-close" id="dClose">✕</button>`;
  h += `<div class="d-name">${esc(r.name)}</div>`;
  h += `<div class="d-sub">${typeBadge(r)} ${r.cc} · ${esc(r.region || '')}${r.operator ? ' · ' + esc(r.operator) : ''}</div>`;
  h += `<div class="d-kv">`;
  if (r.type === 'node') {
    h += `<span>${r.measured ? 'measured min headroom' : 'published headroom'}</span><b>${fmt(r.mw)} MW</b>`;
    if (r.kv) h += `<span>voltage</span><b>${r.kv} kV</b>`;
    if (r.score != null) {
      h += `<span>fit score @ ${target} MW</span><b>${r.score} · ${r.tier}</b>` +
        `<span>firming gap</span><b>${fmt(r.gap)} MW</b>` +
        `<span>BESS (${dur} h)</span><b>${fmt(r.gap)} MW / ${fmt(r.gap * dur)} MWh</b>` +
        `<span>BESS capex</span><b>≈ €${bessCapexM(r.gap).toFixed(1)} M</b>` +
        `<span>sites ≤3 km</span><b>${r.nSites}</b>`;
      if (r.qMW != null) h += `<span>queued ≤30 km</span><b>${fmt(Math.round(r.qMW))} MW</b>`;
    }
    if (r.measured && r.prof) {
      h += `<span>metered peak</span><b>${fmt(r.prof.peak_mw)} MW</b>` +
        `<span>utilisation</span><b>${Math.round((r.prof.util || 0) * 100)}%</b>` +
        `<span>firmable @2 h</span><b>${fmt(r.prof.firm_2h_mw)} MW</b>` +
        `<span>firmable @8 h</span><b>${fmt(r.prof.firm_8h_mw)} MW</b>`;
    }
  } else {
    h += `<span>${r.status === 'Operational' ? 'installed capacity' : 'potential capacity'}</span><b>${fmt(r.mw)} MW</b>` +
      `<span>status</span><b>${esc(r.status)}</b>`;
    if (r.yearOp) h += `<span>operational since</span><b>${r.yearOp}</b>`;
    if (r.storageType) h += `<span>storage type</span><b>${esc(r.storageType)}</b>`;
    h += `<span>duration</span><b>not published</b>`;
    if (r.tec) {
      h += `<span>TEC site</span><b>${esc(r.tec.site || '?')}</b>`;
      if (r.tec.gate) h += `<span>TEC gate</span><b>${esc(r.tec.gate)}</b>`;
      if (r.tec.status) h += `<span>TEC status</span><b>${esc(r.tec.status)}</b>`;
    }
  }
  h += `</div>`;
  h += `<div class="d-sec">Pipeline</div>`;
  h += `<button class="pin-btn ${pi.pinned ? 'pinned' : ''}" id="dPin">${pi.pinned ? '✓ In pipeline' : '★ Add to pipeline'}</button>`;
  h += `<select id="dStage" class="pipe-stage">${STAGES.map(s =>
    `<option ${((pi.stage || '—') === s) ? 'selected' : ''}>${s}</option>`).join('')}</select>`;
  h += `<div class="d-sec">Notes</div>`;
  h += `<textarea id="dNotes" placeholder="Investigation notes — saved locally as you type…">${esc((notes[r.key] || {}).t || '')}</textarea><div class="d-saved" id="dSaved"></div>`;
  h += `<div class="d-sec">Links</div><div class="d-links">`;
  if (r.type === 'node' && !r.measured) {
    h += `<a href="index.html#cc=${r.cc}&n=${encodeURIComponent(r.name)}" target="_blank" rel="noopener">Open on map ↗</a><br>`;
    const c = COUNTRIES[r.cc];
    const s0 = c && c.srcs && c.srcs[r.lay] && c.srcs[r.lay][(r.p && r.p.si) || 0];
    if (s0 && s0.u) h += `<a href="${s0.u}" target="_blank" rel="noopener">Source: ${esc(s0.op || 'operator')}</a><br>`;
  }
  if (r.lat != null && r.lon != null) {
    h += `<a href="https://www.google.com/maps/@${r.lat},${r.lon},14z" target="_blank" rel="noopener">Satellite view ↗</a><br>`;
    h += `<span class="td-sub num">${r.lat && r.lat.toFixed ? r.lat.toFixed(4) : r.lat}, ${r.lon && r.lon.toFixed ? r.lon.toFixed(4) : r.lon}</span>`;
  }
  h += `</div>`;
  d.innerHTML = h;
  d.hidden = false;
  document.getElementById('dClose').onclick = () => { d.hidden = true; selKey = null; renderTable(); };
  document.getElementById('dPin').onclick = () => togglePin(key);
  document.getElementById('dStage').onchange = e => setStage(key, e.target.value);
  let saveTimer = null;
  document.getElementById('dNotes').oninput = e => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (e.target.value.trim()) notes[r.key] = { t: e.target.value, ts: Date.now() };
      else delete notes[r.key];
      saveNotes();
      document.getElementById('dSaved').textContent = 'saved';
      setTimeout(() => { const el = document.getElementById('dSaved'); if (el) el.textContent = ''; }, 1200);
    }, 500);
  };
  document.querySelectorAll('#rBody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.key === key));
}

/* ── CSV export ── */
document.getElementById('rExport').onclick = () => {
  const rows = sorted(filtered());
  const head = ['type', 'market', 'name', 'operator', 'region', 'mw', 'capacity_kind', 'fit_score', 'tier',
    'gap_mw', 'bess_mwh', 'bess_capex_eur_m', 'kv', 'status', 'year_operational', 'tec_site', 'tec_gate',
    'stage', 'notes', 'lat', 'lon'];
  const lines = rows.map(r => {
    const pi = pinInfo(r);
    return [r.type === 'asset' ? r.tech : 'grid node', r.cc, r.name, r.operator || '', r.region || '', r.mw,
      r.type === 'asset' ? (r.status === 'Operational' ? 'installed' : 'potential') : (r.measured ? 'measured headroom' : 'published headroom'),
      r.score ?? '', r.tier ?? '', r.gap ?? '', r.gap != null ? r.gap * dur : '',
      r.gap != null ? bessCapexM(r.gap).toFixed(1) : '', r.kv || '', r.status || '', r.yearOp || '',
      (r.tec && r.tec.site) || '', (r.tec && r.tec.gate) || '',
      pi.stage || '', (notes[r.key] || {}).t || '', r.lat ?? '', r.lon ?? '']
      .map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',');
  });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([head.join(',') + '\n' + lines.join('\n')], { type: 'text/csv' }));
  a.download = `neura-results-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
};

/* ── filter wiring ── */
function wireSeg(id, cb) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.onclick = () => {
    document.querySelectorAll(`#${id} button`).forEach(x => x.classList.remove('on'));
    b.classList.add('on'); cb(b); page = 0; renderTable();
  });
}
wireSeg('rTarget', b => { target = +b.dataset.mw; rescore(); });
wireSeg('rDur', b => { dur = +b.dataset.h; });
wireSeg('rType', b => { F.type = b.dataset.t; });
function wireChips(id, set) {
  document.querySelectorAll(`#${id} .fchip`).forEach(b => {
    if (b.classList.contains('on')) set.add(b.dataset.v);
    b.onclick = () => {
      b.classList.toggle('on');
      if (b.classList.contains('on')) set.add(b.dataset.v); else set.delete(b.dataset.v);
      page = 0; renderTable();
    };
  });
}
wireChips('rTiers', F.tiers);
wireChips('rStatus', F.status);
wireChips('rTech', F.tech);
document.getElementById('rSearch').oninput = e => { F.q = e.target.value; page = 0; renderTable(); };
document.getElementById('rMinMw').oninput = e => { F.minMw = +e.target.value || 0; page = 0; renderTable(); };
document.getElementById('rMeasured').onchange = e => { F.measured = e.target.checked; page = 0; renderTable(); };
document.getElementById('rPinned').onchange = e => { F.pinned = e.target.checked; page = 0; renderTable(); };

function buildMarketChips() {
  const ccs = [...new Set([...Object.keys(FREE), ...MANIFEST.map(m => m.cc)])];
  document.getElementById('rMarkets').innerHTML =
    ccs.map(cc => `<button class="fchip" data-v="${cc}">${cc}</button>`).join('');
  document.querySelectorAll('#rMarkets .fchip').forEach(b => b.onclick = () => {
    b.classList.toggle('on');
    if (b.classList.contains('on')) F.markets.add(b.dataset.v); else F.markets.delete(b.dataset.v);
    page = 0; renderTable();
  });
}

/* ── init ── */
buildMarketChips();
document.getElementById('rBody').innerHTML =
  '<tr><td colspan="10" style="padding:30px;text-align:center;color:var(--mut)">Loading all markets…</td></tr>';
loadAll().then(() => renderTable());
