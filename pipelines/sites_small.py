#!/usr/bin/env python3
"""Industrial / warehouse land (OSM) within 3 km of every demand node with >= 5 MW of published headroom.

Rebuilds sites/<CC>.geojson so small pockets (5-50 MW distribution substations) are first-class, not just
the >=20 MW (GB, Nordics) or >=100 MW (Spain) anchors of the original build.

Method
  * nodes: every node with kind == 'demand' and mw >= 5 in the app's own data (js/data.js FREE or data/CC.json)
  * land: OSM ways tagged landuse=industrial|warehouse, fetched per 0.5° tile that contains a node (bbox queries
    are far faster on Overpass than around-point unions); cached per tile
  * each polygon is assigned to its NEAREST eligible node; kept if the centroid is within 3 km and the
    polygon is >= 0.5 ha (smaller fragments cannot host a 5 MW load)
  * schema matches the existing files: area_ha, dist_km, node, node_mw, osm_id, anchor_kind (+ kv, layer)

Land source: Geofabrik country extract converted with
    ogr2ogr -f GeoJSONSeq landuse_<country>.geojsonl <country>-latest.osm.pbf multipolygons \
            -where "landuse IN ('industrial','warehouse')" -select osm_id,osm_way_id,landuse
  in $OSM_DIR when present; otherwise Overpass per 0.5° tile (slow when the public servers are loaded).

Usage: OSM_DIR=/path python3 pipelines/sites_small.py ES GB SE NO FI
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'osm_tiles')
RADIUS_KM, MIN_MW, MIN_HA, TILE = 3.0, 5, 0.5, 0.5
OSM_DIR = os.environ.get('OSM_DIR', '/private/tmp/claude-501/-Users-karimarnous-NeuraMaterials/120df23a-95a4-4062-b8b5-6f90bd42e366/scratchpad/osm')
GEOFABRIK = {'ES': 'spain', 'GB': 'great-britain', 'SE': 'sweden', 'NO': 'norway', 'FI': 'finland', 'PT': 'portugal'}
ENDPOINTS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']
UA = {'User-Agent': 'OpportunityMap/1.0 (grid research)', 'Accept': 'application/json'}


def nodes_for(cc):
    src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
    free = json.loads(re.search(r'const FREE=(\{.*?\});\n', src, re.S).group(1))
    d = free[cc] if cc in free else json.load(open(os.path.join(ROOT, 'data', f'{cc}.json')))
    out, seen = [], set()
    for n in d['nodes']:
        if n.get('kind') != 'demand' or not isinstance(n.get('mw'), (int, float)) or n['mw'] < MIN_MW or n.get('lat') is None:
            continue
        key = (n['n'], round(n['lat'], 3), round(n['lon'], 3))
        if key in seen:
            continue
        seen.add(key); out.append(n)
    return out


def overpass(q):
    last = None
    for attempt in range(12):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(3 + 2 * attempt)
    raise last


def tile_ways(lat0, lon0):
    os.makedirs(CACHE, exist_ok=True)
    cf = os.path.join(CACHE, f't_{lat0:.1f}_{lon0:.1f}.json')
    if os.path.exists(cf):
        return json.load(open(cf))
    pad = 0.035   # ~3 km so polygons just outside the tile but near an inside node are caught
    bb = f'{lat0 - pad},{lon0 - pad},{lat0 + TILE + pad},{lon0 + TILE + pad}'
    d = overpass(f'[out:json][timeout:180];way["landuse"~"^(industrial|warehouse)$"]({bb});out geom;')
    ways = [{'id': e['id'], 'g': [[round(p['lon'], 5), round(p['lat'], 5)] for p in e['geometry']]} for e in d.get('elements', []) if e.get('type') == 'way' and e.get('geometry')]
    json.dump(ways, open(cf, 'w'), separators=(',', ':'))
    time.sleep(1)
    return ways


def area_ha(c):
    if len(c) < 3:
        return 0.0
    lat0 = sum(p[1] for p in c) / len(c); kx = 111320 * math.cos(math.radians(lat0)); ky = 110540
    s = sum((x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky) for (x1, y1), (x2, y2) in zip(c, c[1:] + c[:1]))
    return abs(s) / 2 / 10000


def hav_km(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(math.radians, (la1, lo1, la2, lo2))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def build(cc):
    nodes = nodes_for(cc)
    grid = {}
    for n in nodes:
        grid.setdefault((math.floor(n['lat'] / 0.05), math.floor(n['lon'] / 0.05)), []).append(n)
    tiles = sorted({(math.floor(n['lat'] / TILE) * TILE, math.floor(n['lon'] / TILE) * TILE) for n in nodes})
    print(f'{cc}: {len(nodes)} demand nodes >= {MIN_MW} MW, {len(tiles)} tiles', flush=True)
    ways = {}
    gl = os.path.join(OSM_DIR, f'landuse_{GEOFABRIK[cc]}.geojsonl') if cc in GEOFABRIK else None
    if gl and os.path.exists(gl):
        # Geofabrik extract (ogr2ogr multipolygons layer, landuse industrial|warehouse) — preferred over Overpass
        for line in open(gl):
            f = json.loads(line); g = f['geometry']; pr = f['properties']
            polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
            outer = max(polys, key=lambda p: len(p[0]))[0]   # largest outer ring stands for the site
            wid = pr.get('osm_way_id') or pr.get('osm_id')
            ways[int(wid) if str(wid).isdigit() else wid] = [[round(x, 5), round(y, 5)] for x, y in outer]
        print(f'  {cc}: {len(ways)} polygons from {os.path.basename(gl)}', flush=True)
        tiles = []
    for i, (la, lo) in enumerate(tiles):
        for w in tile_ways(la, lo):
            ways[w['id']] = w['g']
        if (i + 1) % 10 == 0 or i + 1 == len(tiles):
            print(f'  {cc} tiles {i + 1}/{len(tiles)} · {len(ways)} polygons', flush=True)
    feats = []
    for wid, g in ways.items():
        ring = g if g[0] == g[-1] else g + [g[0]]
        pts = ring[:-1]
        ha = area_ha(pts)
        if ha < MIN_HA:
            continue
        cy = sum(p[1] for p in pts) / len(pts); cx = sum(p[0] for p in pts) / len(pts)
        gy, gx = math.floor(cy / 0.05), math.floor(cx / 0.05)
        best, bd = None, 1e9
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for n in grid.get((gy + dy, gx + dx), []):
                    dk = hav_km(cy, cx, n['lat'], n['lon'])
                    if dk < bd:
                        best, bd = n, dk
        if not best or bd > RADIUS_KM:
            continue
        feats.append({'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [ring]},
                      'properties': {'area_ha': round(ha, 2), 'dist_km': round(bd, 2), 'node': best['n'], 'node_mw': best['mw'], 'kv': best.get('kv'),
                                     'osm_id': wid, 'anchor_kind': 'demand', 'layer': best.get('lay'), 'reg': best.get('reg')}})
    out = os.path.join(ROOT, 'sites', f'{cc}.geojson')
    json.dump({'type': 'FeatureCollection', 'features': feats}, open(out, 'w'), separators=(',', ':'))
    small = sum(1 for f in feats if f['properties']['node_mw'] < 50)
    nn = len({f['properties']['node'] for f in feats})
    print(f'{cc}: wrote {len(feats)} plots on {nn} nodes ({small} plots on nodes < 50 MW), {os.path.getsize(out) // 1024} KB', flush=True)


if __name__ == '__main__':
    for cc in (sys.argv[1:] or ['ES']):
        build(cc.upper())
    print('SITES DONE')
