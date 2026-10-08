#!/usr/bin/env python3
"""Opportunity-layer edits to js/app.js and index.html, kept as (old, new) pairs so they can be applied
both to the local working copy and to the hosted base (which does not carry the local-only FR asset work).

apply(text, PATCHES) -> text   (idempotent: a pair whose `new` is already present is skipped)
Run directly to patch the local working copy:  python3 deploy/opportunity_patches.py
"""
import os, sys

APP = [
    # ── site finder: 5 MW floor
    ("let corridorMinMw = 100;", "let corridorMinMw = 5;"),
    # ── site finder: closest-first, one row per node, plot size vs node, upstream and rule flags, CSV
    ("const sorted = props.filter(p => p.area_ha).sort((a, b) => b.area_ha - a.area_ha).slice(0, 12);",
     """// small pockets first-class: plots ≥1 ha, closest to their node first, then larger plots; one row per node
    const seenNode = new Set();
    const sorted = props.filter(p => p.area_ha >= 1)
      .sort((a, b) => (a.dist_km || 0) - (b.dist_km || 0) || b.area_ha - a.area_ha)
      .filter(p => (seenNode.has(p.node) ? false : (seenNode.add(p.node), true))).slice(0, 25);
    corridorExport[cc] = props;"""),
    ("html += `<span class=\"cor-meta\">${sorted.length} sites · <span class=\"num\">${fmt(maxMw)}</span> MW max</span></div>`;",
     "html += `<span class=\"cor-meta\">${sorted.length} nodes · ${fmt(props.length)} plots · <span class=\"num\">${fmt(maxMw)}</span> MW max</span></div>`;"),
    ("let corridorMinMw = 5;", "let corridorMinMw = 5;\nconst corridorExport = {};   // cc -> plot rows currently passing the MW filter (for CSV)"),
    # ── node popup: flexible figure on measured nodes + operator rule and upstream check
    ("      `<span>firmable added load</span><b>${m.firm_2h_mw.toFixed(0)} MW · 2 h &nbsp;/&nbsp; ${m.firm_8h_mw.toFixed(0)} MW · 8 h</b>` +",
     "      (m.flex_mw && m.flex_mw['1%'] != null ? `<span>flexible, ≤1% curtailed</span><b>${m.flex_mw['1%'].toFixed(0)} MW <span class=\"pp-dim\">Duke method vs firm rating</span></b>` : '') +\n"
     "      `<span>firmable added load</span><b>${m.firm_2h_mw.toFixed(0)} MW · 2 h &nbsp;/&nbsp; ${m.firm_8h_mw.toFixed(0)} MW · 8 h</b>` +"),
    ("  if (dealMode && cc && (p.meas || (p.kind === 'demand' && p.mw >= 5))) s += firmingBlock(p, cc);\n  return s;\n}",
     "  if (p.kind === 'demand' && cc) s += ctxBlock(p, cc);\n"
     "  if (dealMode && cc && (p.meas || (p.kind === 'demand' && p.mw >= 5))) s += firmingBlock(p, cc);\n  return s;\n}\n\n"
     """/* ── Node context: operator's flexible-connection terms + upstream check (data/node_context.json) ── */
let nodeCtx = null;
fetch('data/node_context.json').then(r => r.ok ? r.json() : null).then(d => { nodeCtx = d; }).catch(() => {});
const RULE_LBL = { live: 'flexible demand connections offered', law: 'flexible connection in law', restrictive: 'restrictive for BTM batteries', unverified: 'no national product verified' };
function ctxBlock(p, cc) {
  if (!nodeCtx) return '';
  const g = cc === 'GB' ? nodeCtx.gb[p.n] : null;
  const rid = g ? g.rule : nodeCtx.cc[cc];
  const r = rid && nodeCtx.rules[rid];
  let s = '<div class="pp-ctx">';
  if (r) s += `<div class="pp-rule pp-rule-${r.status}"><span class="pp-rule-dot"></span><b>${esc(RULE_LBL[r.status] || r.status)}</b> · ${esc(r.who)}${g && g.dno ? ' (' + esc(g.dno) + ')' : ''}</div>` +
    `<div class="pp-meta pp-dim">${esc(r.text)} <a href="${r.src}" target="_blank" rel="noopener">${esc(r.src_label)}</a> · battery: ${esc(r.battery)}</div>`;
  if (g && g.parent) {
    const tight = g.up === 'tight';
    s += `<div class="pp-up ${tight ? 'pp-up-tight' : 'pp-up-ok'}">${tight ? '⚠ upstream tighter' : '✓ upstream clear'}: ${esc(g.parent)} publishes <b>${fmt(g.parent_mw)} MW</b>` +
      (tight ? ` → deliverable here ≈ <b>${fmt(g.eff_mw)} MW</b>` : '') + '</div>';
  } else if (g) {
    s += `<div class="pp-up pp-up-unk">upstream headroom not published for this node${g.gsp ? ' · GSP ' + esc(g.gsp) : ''}</div>`;
  }
  if (g && g.large) s += `<div class="pp-meta">${g.large.n} accepted large demand connection${g.large.n > 1 ? 's' : ''} here, ${fmt(g.large.mva)} MVA (${Object.entries(g.large.types).map(([k, v]) => esc(k) + ' ' + v).join(', ')}) · NPg register</div>`;
  return s + '</div>';
}"""),
]

INDEX = [
    ('<span class="brand-links"><a href="results.html">Results ↗</a>',
     '<span class="brand-links"><a href="story.html" class="brand-start">Start here</a><a href="results.html">Results ↗</a>'),
    ('<div class="seg seg-sm" id="cormw"><button data-cmw="100" class="on">100</button><button data-cmw="250">250</button><button data-cmw="500">500</button></div>',
     '<div class="seg seg-sm" id="cormw"><button data-cmw="5" class="on">5</button><button data-cmw="20">20</button><button data-cmw="50">50</button><button data-cmw="100">100</button><button data-cmw="250">250</button></div>'),
    ('<p class="note">Industrial &amp; warehouse land (OSM polygons) within <b>3&nbsp;km</b> of a ≥100&nbsp;MW transmission node — the radius is baked into the data.',
     '<p class="note">Industrial &amp; warehouse land (OSM polygons) within <b>3&nbsp;km</b> of a node with published demand headroom, from 5&nbsp;MW distribution substations up to 400&nbsp;kV — the radius is baked into the data. Small pockets are the point: a 5–50&nbsp;MW node next to an industrial estate is a flexible-connection candidate. Sites are ranked closest first, one row per node.'),
    ('within 3 km of a ≥100 MW node</div>', 'within 3 km of a node with published headroom (≥5 MW)</div>'),
    ('<p>Industrial and warehouse polygons from OpenStreetMap, within 3 km of a transmission node carrying at least 100 MW.',
     '<p>Industrial and warehouse polygons from OpenStreetMap, within 3 km of a node with published demand headroom: distribution substations from 5 MW (GB, Spain, Nordics) up to transmission nodes.'),
    ('     <div id="corridorList"></div>',
     '     <div id="corridorList"></div>\n     <button class="btn-sm" id="corridorCsv" type="button">Download shortlist (CSV)</button>'),
]

CSV_JS = """
/* ── Site finder CSV export ── */
document.getElementById('corridorCsv') && (document.getElementById('corridorCsv').onclick = () => {
  const head = ['country', 'node', 'node_mw', 'kv', 'plot_ha', 'node_mw_per_plot_ha', 'dist_km', 'osm_id', 'operator', 'flex_rule', 'upstream', 'upstream_mw', 'deliverable_mw', 'accepted_large_demand_mva'];
  const rows = [head.join(',')];
  const q = v => (v == null ? '' : /[",\\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
  for (const cc in corridorExport) for (const p of corridorExport[cc]) {
    const g = cc === 'GB' && nodeCtx ? nodeCtx.gb[p.node] : null;
    const rid = g ? g.rule : nodeCtx && nodeCtx.cc[cc];
    const r = rid && nodeCtx && nodeCtx.rules[rid];
    rows.push([cc, p.node, p.node_mw, p.kv, p.area_ha, p.area_ha ? (p.node_mw / p.area_ha).toFixed(2) : '', p.dist_km, p.osm_id, g ? g.dno : '', r ? r.status : '', g ? g.up : '', g ? g.parent_mw : '', g ? g.eff_mw : p.node_mw, g && g.large ? g.large.mva : ''].map(q).join(','));
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([rows.join('\\n')], { type: 'text/csv' }));
  a.download = `site-shortlist-${corridorMinMw}MW.csv`; a.click();
});
"""

CSS = """
/* ── Opportunity context (node popups, site finder) ── */
.pp-ctx{margin-top:8px;border-top:1px solid var(--line);padding-top:6px}
.pp-rule{display:flex;align-items:center;gap:6px;font-size:12px}
.pp-rule-dot{width:8px;height:8px;border-radius:50%;background:#8a93a3;flex:none}
.pp-rule-live .pp-rule-dot{background:#3ddc97}.pp-rule-law .pp-rule-dot{background:#6fb8ff}.pp-rule-restrictive .pp-rule-dot{background:#ff6b6b}
.pp-up{font-size:12px;margin-top:4px}.pp-up-tight{color:#ffb35c}.pp-up-ok{color:#3ddc97}.pp-up-unk{color:var(--faint)}
.btn-sm{margin-top:8px;background:transparent;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:5px 10px;font:inherit;font-size:12px;cursor:pointer}
.btn-sm:hover{border-color:var(--accent)}
.brand-links .brand-start{color:var(--accent);font-weight:600;margin-right:10px}
.story-link{display:inline-block;margin-top:6px;color:var(--accent);font-size:13px;text-decoration:none;border-bottom:1px dotted var(--accent)}
"""


def apply(text, pairs):
    for old, new in pairs:
        if new in text:
            continue
        assert old in text, f'anchor missing: {old[:80]!r}'
        text = text.replace(old, new, 1)
    return text


def apply_all(root):
    p = os.path.join(root, 'js', 'app.js'); s = open(p, encoding='utf-8').read()
    s = apply(s, APP)
    if 'Site finder CSV export' not in s:
        s += CSV_JS
    open(p, 'w', encoding='utf-8').write(s)
    p = os.path.join(root, 'index.html'); s = open(p, encoding='utf-8').read()
    open(p, 'w', encoding='utf-8').write(apply(s, INDEX))
    p = os.path.join(root, 'css', 'app.css'); s = open(p, encoding='utf-8').read()
    if 'Opportunity context (node popups' not in s:
        open(p, 'a', encoding='utf-8').write(CSS)


if __name__ == '__main__':
    apply_all(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print('patched')
