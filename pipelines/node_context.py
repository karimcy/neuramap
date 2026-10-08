#!/usr/bin/env python3
"""Per-node context for the opportunity layer: who operates the node, whether that operator offers a
flexible (non-firm) demand connection, and whether the network above the node can carry what the node
itself publishes.

Output: data/node_context.json
  rules  {rule_id: {who, status, battery, text, src, src_label}}   status: live | law | restrictive | unverified
  cc     {CC: rule_id}                                              country default
  gb     {"NODE NAME": {dno, rule, gsp, parent, parent_mw, eff_mw, up}}
           parent    upstream node name where it is published (same DNO dataset, or NPg BSP group)
           parent_mw its published demand headroom
           eff_mw    min(own, parent) — what can actually be delivered through the parent
           up        'ok' | 'tight' (parent < own) | 'unknown'

Sources (all anonymous):
  DNO licence areas  northernpowergrid.opendatasoft.com  dataset all_dno_boundaries
  NPg headroom       northernpowergrid.opendatasoft.com  dataset npg_ndp_demand_headroom (BSP and GSP groups)
  NPg large demand   northernpowergrid.opendatasoft.com  dataset large-scale-demand (accepted large demand connections per primary)
  GSP per node       data/networks/gb_node_map.json (NESO GSP polygons, built by gb_node_match.py)
  Rule texts         verified in NeuraMaterials/10_COMPETITOR_MAP.md §3.1 and §3.7 (primary documents, Oct 2026)
"""
import json, os, re, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'node_context')
OUT = os.path.join(ROOT, 'data', 'node_context.json')
NPG = 'https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets/{}/records'

RULES = {
    'gb_ukpn': {'who': 'UK Power Networks', 'status': 'live', 'battery': 'not excluded',
                'text': 'Flexible Connection "is available both for demand and generation"; a contractual Curtailment Limit caps curtailment as a % of hours per year, beyond which UKPN compensates. The only technical condition is that the asset can be flexed under UKPN real-time control.',
                'src': 'https://dso.ukpowernetworks.co.uk/curtailment-and-derms', 'src_label': 'UKPN DSO, curtailment and DERMS'},
    'gb_nged': {'who': 'National Grid Electricity Distribution', 'status': 'live', 'battery': 'not excluded',
                'text': 'Timed and ANM (curtailable) connections open to demand sites from 500 kW; the Curtailment Limit (hours) is a contractual cap and NGED is liable for curtailment beyond it.',
                'src': 'https://commercial.nationalgrid.co.uk/our-network/active-network-management-anm', 'src_label': 'NGED ANM; Curtailment Analysis Data Guidance, Nov 2025'},
    'gb_ssen': {'who': 'SSEN Distribution', 'status': 'live', 'battery': 'not excluded',
                'text': 'First ANM connection for demand agreed June 2024: 6.4 MW of curtailable import with a guaranteed curtailment limit, two years early, bypassing 132 kV constraints.',
                'src': 'https://www.ssen.co.uk/globalassets/about-us/dso/publication--reports/ssen-dso-submission-2025.pdf', 'src_label': 'SSEN DSO Submission 2025'},
    'gb_enwl': {'who': 'Electricity North West', 'status': 'live', 'battery': 'not excluded',
                'text': 'Import-limited, timed and ANM connections, with a Curtailment Index and Cap introduced in quotations.',
                'src': 'https://www.enwl.co.uk/Get-connected/apply-for-a-new-connection/flexible-connections/', 'src_label': 'ENWL flexible connections'},
    'gb_spen': {'who': 'SP Energy Networks', 'status': 'unverified', 'battery': 'unknown',
                'text': 'Runs non-firm generation connections on Smarter Grid Solutions ANM; non-firm DEMAND terms not verified. Ofgem requires a standardised non-firm option for larger users (SCR decision, May 2022).',
                'src': 'https://www.ofgem.gov.uk/publications/access-and-forward-looking-charges-significant-code-review-decision-and-direction', 'src_label': 'Ofgem Access SCR decision, May 2022'},
    'gb_npg': {'who': 'Northern Powergrid', 'status': 'unverified', 'battery': 'unknown',
               'text': 'Non-firm DEMAND terms not verified. Ofgem requires a standardised non-firm option for larger users (SCR decision, May 2022).',
               'src': 'https://www.ofgem.gov.uk/publications/access-and-forward-looking-charges-significant-code-review-decision-and-direction', 'src_label': 'Ofgem Access SCR decision, May 2022'},
    'nl': {'who': 'Netherlands (ACM code)', 'status': 'law', 'battery': 'explicit',
           'text': 'Non-firm transport rights (time-bound 85 %, time-block) in the grid code; ACM ties the product to battery storage and names switching to own supply as the compliance route.',
           'src': 'https://www.acm.nl/nl/publicaties/codebesluit-alternatieve-transportrechten', 'src_label': 'ACM codebesluit alternatieve transportrechten, 16 Jul 2024'},
    'de': {'who': 'Germany (§17(2b) EnWG)', 'status': 'law', 'battery': 'not excluded',
           'text': 'Statutory flexible connection agreement in force since Dec 2025: static or dynamic import limit agreed with the grid operator; technology-neutral.',
           'src': 'https://www.gesetze-im-internet.de/enwg_2005/__17.html', 'src_label': '§17 EnWG'},
    'ie': {'who': 'Ireland (CRU/2025/236)', 'status': 'restrictive', 'battery': 'restricted',
           'text': 'Data centres ≥10 MVA must bring dispatchable generation and/or storage ≥ their import capacity, separately metered and market-registered; a behind-the-meter battery used only to shed import does not comply.',
           'src': 'https://www.cru.ie/about-us/news/the-cru-publishes-its-decision-on-new-electricity-connection-policy-for-data-centres/', 'src_label': 'CRU/2025/236 decision, 12 Dec 2025'},
    'eu': {'who': 'EU guidance (no national product verified)', 'status': 'unverified', 'battery': 'explicit in EU guidance',
           'text': 'Commission guidance (Dec 2025): a company "could connect to the grid without a grid reinforcement if it makes the appropriate investments (self-generation, storage)"; 15 Member States have some flexible connection agreement. The national demand product here has not been verified.',
           'src': 'http://data.europa.eu/eli/C/2025/6703/oj', 'src_label': 'Commission Notice C/2025/6703'},
}
CC_RULE = {'GB': None, 'NL': 'nl', 'DE': 'de', 'IE': 'ie'}
DNO_RULE = {'UKPN': 'gb_ukpn', 'WPD': 'gb_nged', 'SSE': 'gb_ssen', 'ENWL': 'gb_enwl', 'SPEN': 'gb_spen', 'NPG': 'gb_npg'}


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'OpportunityMap/1.0'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def cached(name, fn):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        return json.load(open(p))
    d = fn(); json.dump(d, open(p, 'w')); return d


def npg_all(ds, select):
    out, off = [], 0
    while True:
        d = get(NPG.format(ds) + '?' + urllib.parse.urlencode({'limit': 100, 'offset': off, 'select': select}))
        out += d['results']; off += 100
        if off >= d['total_count']:
            return out


def pip(lon, lat, geom):
    def in_ring(r):
        c = False; j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i][0], r[i][1]; xj, yj = r[j][0], r[j][1]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi:
                c = not c
            j = i
        return c
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
    return any(in_ring(p[0]) and not any(in_ring(h) for h in p[1:]) for p in polys)


def norm(s):
    s = (s or '').upper()
    s = re.sub(r'\d+(\.\d+)?\s*(/\s*\d+(\.\d+)?)?\s*KV', '', s)
    s = re.sub(r'\b(BSP|GSP|PRIMARY|GRID|SUPPLY|POINT|SUBSTATION|S/S|STN|S STN)\b', ' ', s)
    return ' '.join(re.sub(r'[^A-Z ]', ' ', s).split())


def main():
    dnos = cached('dno_boundaries.json', lambda: npg_all('all_dno_boundaries', 'longname,geo_shape'))
    npg = cached('npg_headroom.json', lambda: npg_all('npg_ndp_demand_headroom', 'substation_name,voltage_kv,bsp_group,gsp_group,bulk_supply_point_or_primary,demand_headroom_capacity_mw_2026'))
    gb = json.load(open(os.path.join(ROOT, 'data', 'GB.json')))
    gmap = {m['node']: m for m in json.load(open(os.path.join(ROOT, 'data', 'networks', 'gb_node_map.json')))['matches']}
    nodes = [n for n in gb['nodes'] if n['lay'] == 'gb_dist']
    byname = {}
    for n in nodes:
        byname.setdefault(norm(n['n']), []).append(n)

    def parent_of(child, pname):
        """Upstream node by name, never a sibling: the candidate must sit at a higher voltage than the child
        (child kV unknown → candidate must be ≥ 33 kV). Highest-voltage candidate wins."""
        ck = child.get('kv')
        cands = [c for c in byname.get(pname, []) if c is not child and isinstance(c.get('mw'), (int, float)) and c.get('kv')
                 and ((ck and c['kv'] > ck) or (not ck and c['kv'] >= 33))]
        return max(cands, key=lambda c: c['kv']) if cands else None
    # existing large demand customers per primary (NPg only; anonymous dataset `large-scale-demand`)
    lsd = cached('npg_large_demand.json', lambda: npg_all('large-scale-demand', 'gsp_name,bsp_name,psp_name,sumofimport_kva,con_name,status,licence_area_name'))
    large = {}
    for r in lsd:
        k = norm(r.get('psp_name'))
        if k:
            e = large.setdefault(k, {'n': 0, 'kva': 0.0, 'types': {}})
            e['n'] += 1; e['kva'] += float(r.get('sumofimport_kva') or 0)
            t = r.get('con_name') or 'other'; e['types'][t] = e['types'].get(t, 0) + 1
    npg_bsp = {norm(r['substation_name']): r for r in npg if (r.get('bulk_supply_point_or_primary') or '').lower().startswith('supply')}
    npg_prim = {norm(r['substation_name']): r for r in npg if not (r.get('bulk_supply_point_or_primary') or '').lower().startswith('supply')}
    out_gb, stats = {}, {'nodes': 0, 'dno': 0, 'parent': 0, 'tight': 0}
    for n in nodes:
        stats['nodes'] += 1
        dno = next((d['longname'] for d in dnos if d.get('geo_shape') and pip(n['lon'], n['lat'], d['geo_shape']['geometry'])), None)
        key = (dno or '').split(' ')[0].upper()
        rule = DNO_RULE.get(key)
        if dno: stats['dno'] += 1
        parent, pmw = None, None
        nn = norm(n['n'])
        if nn in npg_prim and npg_prim[nn].get('bsp_group'):
            b = npg_bsp.get(norm(npg_prim[nn]['bsp_group']))
            if b and b.get('demand_headroom_capacity_mw_2026') is not None and norm(b['substation_name']) != nn:
                parent, pmw = b['substation_name'] + ' BSP', b['demand_headroom_capacity_mw_2026']
        if parent is None:
            pn = norm(n.get('reg'))
            p = parent_of(n, pn) if pn else None
            if p:
                parent, pmw = p['n'], p['mw']
        if parent: stats['parent'] += 1
        eff = round(min(n['mw'], pmw), 1) if pmw is not None else n['mw']
        up = 'unknown' if pmw is None else ('tight' if pmw < n['mw'] else 'ok')
        if up == 'tight': stats['tight'] += 1
        g = gmap.get(n['n'], {}).get('gsp') or {}
        lg = large.get(nn) if key == 'NPG' else None
        out_gb[n['n']] = {'dno': dno, 'rule': rule, 'gsp': g.get('region') or g.get('name'), 'parent': parent,
                          'parent_mw': round(pmw, 1) if pmw is not None else None, 'eff_mw': eff, 'up': up,
                          'large': {'n': lg['n'], 'mva': round(lg['kva'] / 1000, 1), 'types': lg['types']} if lg else None}
        if lg: stats['large'] = stats.get('large', 0) + 1
    cc = {c: (CC_RULE.get(c) or 'eu') for c in ['ES', 'PT', 'FR', 'IT', 'PL', 'SE', 'NO', 'FI', 'DK', 'AT', 'BE', 'CH', 'CZ', 'HR', 'NL', 'DE', 'IE']}
    json.dump({'rules': RULES, 'cc': cc, 'gb': out_gb, 'stats': stats,
               'note': 'Rule texts verified from primary documents in Oct 2026 (see NeuraMaterials/10_COMPETITOR_MAP.md §3.1, §3.7). "unverified" means no national demand product was confirmed, not that none exists.'},
              open(OUT, 'w'), ensure_ascii=False)
    print('wrote', OUT, stats)


if __name__ == '__main__':
    main()
