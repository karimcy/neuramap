#!/usr/bin/env python3
"""Industrial / warehouse land (OSM) within 3 km of Spanish DISTRIBUTION demand nodes (CNMC, >=5 MW).

The existing sites/ES.geojson only anchors to REE transmission nodes (>=100 MW). This adds the small
pockets: 344 CNMC distribution nodes with 5-116 MW of published demand capacity. Same schema as the
other sites files (area_ha, dist_km, node, node_mw, osm_id, anchor_kind) plus kv and layer.
Merges into sites/ES.geojson, keeping the transmission-anchored features. Each polygon is assigned to
its nearest node across both sets, so nothing is double-counted.

Usage: python3 pipelines/es_dist_sites.py
"""
import json, math, os, re, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'sites', 'ES.geojson')
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'es_dist_sites')
RADIUS_M = 3000
MIN_MW = 5
BATCH = 40
ENDPOINTS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']


def load_nodes():
    src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
    F = json.loads(re.search(r'const FREE=(\{.*?\});\n', src, re.S).group(1))
    return [n for n in F['ES']['nodes'] if n.get('lay') == 'es_cnmc_dist' and n.get('kind') == 'demand' and (n.get('mw') or 0) >= MIN_MW]


def overpass(q):
    last = None
    for attempt in range(6):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(), headers={'User-Agent': 'OpportunityMap/1.0 (research)'})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(20 * (attempt + 1))
    raise last


def area_ha(coords):
    if len(coords) < 3:
        return 0.0
    lat0 = sum(c[1] for c in coords) / len(coords)
    kx = 111320 * math.cos(math.radians(lat0)); ky = 110540
    s = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:] + coords[:1]):
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(s) / 2 / 10000


def hav_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def main():
    os.makedirs(CACHE, exist_ok=True)
    nodes = load_nodes()
    print(f'{len(nodes)} CNMC distribution nodes >= {MIN_MW} MW')
    ways = {}
    for i in range(0, len(nodes), BATCH):
        cf = os.path.join(CACHE, f'batch_{i // BATCH:03d}.json')
        if os.path.exists(cf):
            d = json.load(open(cf))
        else:
            parts = ''.join(f'way["landuse"~"^(industrial|warehouse)$"](around:{RADIUS_M},{n["lat"]},{n["lon"]});' for n in nodes[i:i + BATCH])
            d = overpass(f'[out:json][timeout:240];({parts});out geom;')
            json.dump(d, open(cf, 'w')); time.sleep(5)
        for el in d.get('elements', []):
            if el.get('type') == 'way' and el.get('geometry'):
                ways[el['id']] = el
        print(f'  batch {i // BATCH + 1}/{math.ceil(len(nodes) / BATCH)}: {len(ways)} polygons so far', flush=True)

    existing = json.load(open(OUT)) if os.path.exists(OUT) else {'type': 'FeatureCollection', 'features': []}
    trans = [f for f in existing['features'] if f['properties'].get('layer') != 'es_cnmc_dist']
    trans_ids = {f['properties'].get('osm_id') for f in trans}
    feats = []
    for wid, el in ways.items():
        if wid in trans_ids:
            continue   # already anchored to a transmission node
        coords = [[p['lon'], p['lat']] for p in el['geometry']]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        c = (sum(p[1] for p in coords[:-1]) / (len(coords) - 1), sum(p[0] for p in coords[:-1]) / (len(coords) - 1))
        best = min(nodes, key=lambda n: hav_km(c, (n['lat'], n['lon'])))
        dk = hav_km(c, (best['lat'], best['lon']))
        if dk > RADIUS_M / 1000 + 0.3:
            continue
        name = best['n'].replace('_', ' ').title()
        feats.append({'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [[[round(x, 5), round(y, 5)] for x, y in coords]]},
                      'properties': {'area_ha': round(area_ha(coords[:-1]), 3), 'dist_km': round(dk, 3), 'node': name, 'node_mw': best['mw'],
                                     'kv': best.get('kv'), 'osm_id': wid, 'anchor_kind': 'demand', 'layer': 'es_cnmc_dist', 'municipio': best.get('reg')}})
    for f in trans:
        f['properties'].setdefault('layer', 'es_transport')
    out = {'type': 'FeatureCollection', 'features': trans + feats}
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    small = sum(1 for f in feats if f['properties']['node_mw'] < 100)
    print(f'wrote {OUT}: {len(trans)} transmission-anchored + {len(feats)} distribution-anchored ({small} on nodes < 100 MW), {os.path.getsize(OUT) // 1024} KB')


if __name__ == '__main__':
    main()
