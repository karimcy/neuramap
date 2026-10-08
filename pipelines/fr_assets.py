#!/usr/bin/env python3
"""FR solar & battery asset layer — national production/storage register (ODRE).

Source: "Registre national des installations de production et de stockage
d'électricité", published on ODRE (Open Data Réseaux Énergies) by RTE with
Enedis + the local DSOs. Open, no key. One export call, ~5 MB.

What makes it worth having: every row carries the **poste source** (primary
substation) it connects to, the **connection voltage** (BT / HTA / 63–400 kV)
and the **grid operator**, so it is a distribution-grid asset layer, not just a
plant list. Storage rows carry `energiestockable` (kWh) — the duration that
REPD/TEC do not publish for GB.

Register scope is the CONNECTED fleet (regime "En service"), not a queue; the
French pipeline is already covered by the Caparéseau S3REnR layer.

Positions: the register publishes no coordinates. Points are commune centroids
(geo.api.gouv.fr) with a deterministic sub-km spread so co-located rows stay
clickable, and are flagged `geo: "commune"` — indicative, not the plant fence.
Each row is additionally matched to a Caparéseau map node where the poste-source
code resolves unambiguously.

Output: data/fr_assets.geojson — point per registered installation >= 1 MW.

Run: python3 pipelines/fr_assets.py [--refresh]
"""
import collections
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'fr_register')
OUT = os.path.join(ROOT, 'data', 'fr_assets.geojson')
NODES = os.path.join(ROOT, 'data', 'FR.json')

DATASET = 'registre-national-installation-production-stockage-electricite-agrege'
CATALOG = 'https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets'
ODRE = f'{CATALOG}/{DATASET}'
POSTES = f'{CATALOG}/postes-electriques-rte'          # code_poste -> nom_poste dictionary
GEOAPI = 'https://geo.api.gouv.fr'

FIELDS = ('nominstallation,postesource,tensionraccordement,puismaxinstallee,'
          'puismaxrac,energiestockable,filiere,technologie,typestockage,regime,'
          'gestionnaire,commune,codeinseecommune,departement,region,'
          'datemiseenservice_date,nbinstallations,energieannuelleglissanteinjectee,'
          'codes3renr,moderaccordement')
MIN_KW = 1000.0
KEEP = {'Solaire', 'Stockage non hydraulique'}

# The register writes the poste source as an RTE 5-character code ("FRAIS");
# Caparéseau map nodes are spelled out ("FRAISES"). postes-electriques-rte is the
# official code -> name dictionary, which turns a guess into a named join.
# Anything that still fails falls back to nearest-node, and is labelled as such —
# a proximity hit is a display convenience, never evidence of a connection.
NEAREST_KM = 15.0
CONFIDENT = {'code_name', 'name'}


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'OpportunityMap/1.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise
            print(f'   retry {attempt + 1} after {e}')
            time.sleep(2 + 3 * attempt)


REFRESH = '--refresh' in sys.argv       # ODRE re-publishes the register roughly monthly


def cached(name, fetch):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not REFRESH:
        return json.load(open(path))
    data = fetch()
    json.dump(data, open(path, 'w'))
    return data


def haversine(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def norm(s):
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


def fetch_register():
    print('· register export (>= 1 MW, all technologies)')
    return get(f'{ODRE}/exports/json?where=puismaxinstallee%3E%3D{int(MIN_KW)}&select={FIELDS}')


def fetch_postes():
    print('· RTE poste-source dictionary')
    return get(f'{POSTES}/exports/json?select=code_poste,nom_poste,fonction,etat,tension,departement')


def fetch_communes():
    """Commune centroids, one call per department (geo.api.gouv.fr caps bulk queries)."""
    print('· commune centroids')
    deps = get(f'{GEOAPI}/departements?fields=code')
    out = {}
    for i, d in enumerate(deps):
        rows = get(f"{GEOAPI}/departements/{d['code']}/communes?fields=code,centre,nom")
        for c in rows:
            centre = c.get('centre') or {}
            if centre.get('coordinates'):
                lon, lat = centre['coordinates']
                out[c['code']] = [round(lat, 5), round(lon, 5)]
        if i % 20 == 0:
            print(f'   {i}/{len(deps)} departments, {len(out)} communes')
    return out


def spread(lat, lon, k):
    """Deterministic sub-km offset so rows sharing a commune centroid stay clickable."""
    if k == 0:
        return lat, lon
    ring = int((math.sqrt(k) + 1) // 1)
    ang = 2 * math.pi * (k / max(1, 6 * ring))
    r = 0.0045 * ring                                    # ~0.5 km per ring
    return round(lat + r * math.cos(ang), 5), round(lon + r * math.sin(ang) / math.cos(lat * math.pi / 180), 5)


def build_node_index():
    nodes = json.load(open(NODES))['nodes']
    by_name = collections.defaultdict(list)
    for n in nodes:
        nm = norm(n.get('n'))
        if nm:
            by_name[nm].append(n)
    return nodes, by_name


def pick(cands, lat, lon):
    """Nearest of several same-named nodes — they are duplicates across source layers."""
    return min(cands, key=lambda n: haversine(lat, lon, n['lat'], n['lon']))


def match_node(code, lat, lon, nodes, by_name, code2name):
    """Poste-source code -> Caparéseau node: dictionary first, proximity as a labelled fallback."""
    key = norm(code)
    if key and lat is not None:
        if key in by_name:                                  # register already spelled it out
            return pick(by_name[key], lat, lon), 'name', None
        hits = []
        for nm in code2name.get(key, []):
            if norm(nm) in by_name:
                hits.append((nm, pick(by_name[norm(nm)], lat, lon)))
        if hits:
            nm, node = min(hits, key=lambda h: haversine(lat, lon, h[1]['lat'], h[1]['lon']))
            return node, 'code_name', nm
    if lat is None:
        return None, None, None
    best, bestd = None, NEAREST_KM
    for n in nodes:
        d = haversine(lat, lon, n['lat'], n['lon'])
        if d < bestd:
            best, bestd = n, d
    return (best, 'proximity', None) if best else (None, None, None)


def main():
    reg = cached('register.json', fetch_register)
    communes = cached('communes.json', fetch_communes)
    postes = cached('postes.json', fetch_postes)
    code2name = collections.defaultdict(list)
    for p in postes:
        if p.get('code_poste') and p.get('nom_poste'):
            code2name[norm(p['code_poste'])].append(p['nom_poste'])
    nodes, by_name = build_node_index()
    print(f'· {len(reg)} register rows, {len(communes)} communes, '
          f'{len(code2name)} poste codes, {len(nodes)} FR nodes')

    rows = [r for r in reg if r.get('filiere') in KEEP and (r.get('puismaxinstallee') or 0) >= MIN_KW]
    rows.sort(key=lambda r: -(r.get('puismaxinstallee') or 0))

    seen = collections.Counter()
    feats, no_geo, matched, dur_n = [], 0, 0, 0
    for r in rows:
        insee = r.get('codeinseecommune')
        base = communes.get(insee)
        if not base:
            no_geo += 1
            continue
        k = seen[insee]
        seen[insee] += 1
        lat, lon = spread(base[0], base[1], k)

        kw = r['puismaxinstallee']
        stor = r.get('filiere') == 'Stockage non hydraulique'
        kwh = r.get('energiestockable') or 0
        dur = round(kwh / kw, 2) if (stor and kw and kwh) else None
        if dur:
            dur_n += 1
        node, how, poste_name = match_node(r.get('postesource'), lat, lon, nodes, by_name, code2name)
        if how in CONFIDENT:
            matched += 1
        date = r.get('datemiseenservice_date') or ''

        feats.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': (r.get('nominstallation') or '').strip() or None,
                'operator': r.get('gestionnaire'),
                'tech': 'battery' if stor else 'solar',
                'mw': round(kw / 1000, 3),
                'mw_conn': round(r['puismaxrac'] / 1000, 3) if r.get('puismaxrac') else None,
                'dur_h': dur,
                'storage_type': r.get('technologie') if stor else None,
                'status': 'Operational' if r.get('regime') == 'En service' else (r.get('regime') or 'Registered'),
                'year_op': int(date[:4]) if date[:4].isdigit() else None,
                'county': r.get('departement'),
                'region': r.get('region'),
                'commune': r.get('commune'),
                'kv': r.get('tensionraccordement'),
                'poste_source': r.get('postesource'),
                'poste_name': poste_name,
                's3renr': r.get('codes3renr'),
                'n_inst': r.get('nbinstallations'),
                'mwh_year': (round(r['energieannuelleglissanteinjectee'] / 1000)
                             if r.get('energieannuelleglissanteinjectee') else None),
                'geo': 'commune',
                'node': ({'n': node['n'], 'lay': node['lay'], 'mw': node.get('mw'), 'how': how,
                          'conf': 'named' if how in CONFIDENT else 'nearby'}
                         if node else None),
            },
        })

    out = {
        'type': 'FeatureCollection',
        'meta': {
            'source': 'Registre national des installations de production et de stockage '
                      "d'électricité (ODRE / RTE + Enedis + local DSOs)",
            'filter': '>= 1 MW, solar + non-hydro storage',
            'geo': 'commune centroid (geo.api.gouv.fr) — indicative position, '
                   'the register publishes no coordinates',
            'built': time.strftime('%Y-%m-%d'),
        },
        'features': feats,
    }
    json.dump(out, open(OUT, 'w'))

    tech = collections.Counter(f['properties']['tech'] for f in feats)
    kv = collections.Counter(f['properties']['kv'] for f in feats)
    gw = sum(f['properties']['mw'] for f in feats) / 1000
    print(f'\n{len(feats)} assets written · {gw:.2f} GW · {no_geo} without commune geo')
    print('  by tech:   ', dict(tech))
    print('  by voltage:', dict(kv.most_common()))
    how = collections.Counter((f['properties']['node'] or {}).get('how') for f in feats)
    print(f'  substation join (named) {matched}/{len(feats)} '
          f'({100 * matched / max(1, len(feats)):.0f}%) · storage duration on {dur_n}')
    print('  join basis:', dict(how))
    bat = [f['properties'] for f in feats if f['properties']['tech'] == 'battery']
    if bat:
        d = [b['dur_h'] for b in bat if b['dur_h']]
        print(f"  battery: {len(bat)} rows, {sum(b['mw'] for b in bat):.0f} MW, "
              f"median duration {sorted(d)[len(d) // 2]:.2f} h" if d else '')
        for b in sorted(bat, key=lambda x: -x['mw'])[:5]:
            print(f"    {(b['name'] or '—')[:26]:26s} {b['mw']:7.1f} MW  {b['dur_h'] or '?'} h  "
                  f"{b['kv']:6s} {b['commune']}")


if __name__ == '__main__':
    main()
