#!/usr/bin/env python3
"""Connectivity layer — terrestrial fibre + interconnection facilities + subsea.

Sources:
  fibre     ITU transmission-map extract (operational/planned backbone routes);
            cached from the public enersite mirror — re-source from ITU bbmaps
            directly when licensing is formalised (see IMPROVEMENTS).
  facility  PeeringDB /api/fac — carrier-neutral interconnection facilities,
            lat/lon, our 10 markets. Open API.
  subsea    TeleGeography submarinecablemap.com public API — cable
            geometries + landing points, clipped to Europe.

Output: data/telco.geojson (mixed feature classes, prop `cls`).
"""
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'telco')
OUT = os.path.join(ROOT, 'data', 'telco.geojson')
MARKETS = ['ES', 'FI', 'GB', 'FR', 'PL', 'IT', 'DE', 'SE', 'NO', 'PT']
BBOX = (-12.0, 34.0, 32.0, 72.0)   # lon0, lat0, lon1, lat1 — Europe


def get(url, cachename, ttl_ok=True):
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, cachename)
    if os.path.exists(f) and ttl_ok:
        return json.load(open(f))
    req = urllib.request.Request(url, headers={'User-Agent': 'OpportunityMap/1.0 research'})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    json.dump(d, open(f, 'w'))
    time.sleep(1)
    return d


def in_bbox(lon, lat):
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def geom_touches_bbox(geom):
    def coords(g):
        t = g['type']
        if t == 'LineString':
            return g['coordinates']
        if t == 'MultiLineString':
            return [c for line in g['coordinates'] for c in line]
        if t == 'Point':
            return [g['coordinates']]
        return []
    return any(in_bbox(c[0], c[1]) for c in coords(geom))


def main():
    feats = []

    # ── terrestrial fibre (ITU extract) ──
    fibre = get('https://enersite.app/data/telco_lines.geojson', 'itu_fibre.json')
    nf = 0
    for f in fibre.get('features', []):
        if not geom_touches_bbox(f['geometry']):
            continue
        st = (f['properties'] or {}).get('status') or ''
        feats.append({'type': 'Feature', 'geometry': f['geometry'],
                      'properties': {'cls': 'fibre', 'status': st}})
        nf += 1

    # ── PeeringDB facilities ──
    npdb = 0
    for cc in MARKETS:
        d = get(f'https://www.peeringdb.com/api/fac?country={cc}&limit=500',
                f'peeringdb_fac_{cc}.json')
        for x in d.get('data', []):
            lat, lon = x.get('latitude'), x.get('longitude')
            if lat is None or lon is None:
                continue
            feats.append({'type': 'Feature',
                          'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                          'properties': {'cls': 'facility', 'name': x.get('name'),
                                         'city': x.get('city'), 'cc': cc,
                                         'org': (x.get('org_name') or '')[:60]}})
            npdb += 1

    # ── subsea cables + landing points (TeleGeography) ──
    base = 'https://www.submarinecablemap.com/api/v3'
    cables = get(f'{base}/cable/cable-geo.json', 'tg_cables.json')
    nc = 0
    for f in cables.get('features', []):
        if not geom_touches_bbox(f['geometry']):
            continue
        feats.append({'type': 'Feature', 'geometry': f['geometry'],
                      'properties': {'cls': 'subsea', 'name': (f['properties'] or {}).get('name')}})
        nc += 1
    lps = get(f'{base}/landing-point/landing-point-geo.json', 'tg_landing.json')
    nl = 0
    for f in lps.get('features', []):
        lon, lat = f['geometry']['coordinates'][:2]
        if not in_bbox(lon, lat):
            continue
        feats.append({'type': 'Feature', 'geometry': f['geometry'],
                      'properties': {'cls': 'landing', 'name': (f['properties'] or {}).get('name')}})
        nl += 1

    json.dump({'type': 'FeatureCollection',
               'meta': {'built': '2026-07-24',
                        'sources': 'ITU transmission-map extract · PeeringDB /fac · TeleGeography submarine cable map (GitHub)'},
               'features': feats}, open(OUT, 'w'))
    print(f'fibre {nf} · facilities {npdb} · subsea cables {nc} · landings {nl} '
          f'→ {OUT} ({os.path.getsize(OUT) // 1024} KB)')


if __name__ == '__main__':
    main()
