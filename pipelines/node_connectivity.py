#!/usr/bin/env python3
"""Per-node connectivity metrics — distance to nearest fibre route,
interconnection facility (PeeringDB) and subsea landing point.

Feeds the funnel score's connectivity component and the results page.
Output: data/node_connectivity.json  { "CC|NODE": {fib, fac, land} }  (km)
"""
import json
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'node_connectivity.json')
CELL = 0.25   # degrees — spatial grid for fibre vertices


def load_nodes():
    """All demand nodes with coords, from the app's own data files."""
    nodes = []
    datajs = open(os.path.join(ROOT, 'js', 'data.js')).read()
    free = json.loads(re.search(r'const FREE=(.*?);\n', datajs).group(1))
    manifest = json.loads(re.search(r'const MANIFEST=(.*?);\n', datajs).group(1))
    markets = dict(free)
    for m in manifest:
        f = os.path.join(ROOT, m['data_url'].lstrip('/'))
        if os.path.exists(f):
            d = json.load(open(f))
            markets[d['cc']] = d
    for cc, c in markets.items():
        for p in c.get('nodes', []):
            if p.get('kind') == 'demand' and p.get('lat') and (p.get('mw') or 0) >= 5:
                nodes.append((cc, p['n'], p['lat'], p['lon']))
    return nodes


def dist_km(lat1, lon1, lat2, lon2):
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111.32
    dy = (lat2 - lat1) * 110.57
    return math.hypot(dx, dy)


def build_grid(points):
    grid = {}
    for lat, lon in points:
        grid.setdefault((int(lat / CELL), int(lon / CELL)), []).append((lat, lon))
    return grid


def nearest(grid, lat, lon, max_rings=12):
    ci, cj = int(lat / CELL), int(lon / CELL)
    best = None
    for ring in range(max_rings + 1):
        cells = []
        if ring == 0:
            cells = [(ci, cj)]
        else:
            for di in range(-ring, ring + 1):
                for dj in (-ring, ring):
                    cells.append((ci + di, cj + dj))
            for dj in range(-ring + 1, ring):
                for di in (-ring, ring):
                    cells.append((ci + di, cj + dj))
        for cell in cells:
            for plat, plon in grid.get(cell, []):
                d = dist_km(lat, lon, plat, plon)
                if best is None or d < best:
                    best = d
        # once we have a hit, one extra ring guarantees correctness at cell scale
        if best is not None and best < (ring - 1) * CELL * 110:
            break
    return best


def main():
    telco = json.load(open(os.path.join(ROOT, 'data', 'telco.geojson')))
    fib_pts, fac_pts, land_pts = [], [], []
    for f in telco['features']:
        cls = f['properties']['cls']
        g = f['geometry']
        if cls == 'fibre':
            lines = g['coordinates'] if g['type'] == 'MultiLineString' else [g['coordinates']]
            for line in lines:
                for i, c in enumerate(line):
                    if i % 2 == 0:            # every 2nd vertex is plenty at backbone scale
                        fib_pts.append((c[1], c[0]))
        elif cls == 'facility':
            fac_pts.append((g['coordinates'][1], g['coordinates'][0]))
        elif cls == 'landing':
            land_pts.append((g['coordinates'][1], g['coordinates'][0]))
    fib_grid, fac_grid, land_grid = build_grid(fib_pts), build_grid(fac_pts), build_grid(land_pts)
    print(f'fibre vertices {len(fib_pts)} · facilities {len(fac_pts)} · landings {len(land_pts)}')

    nodes = load_nodes()
    out = {}
    for cc, name, lat, lon in nodes:
        out[cc + '|' + name] = {
            'fib': round(nearest(fib_grid, lat, lon) or 999, 1),
            'fac': round(nearest(fac_grid, lat, lon) or 999, 1),
            'land': round(nearest(land_grid, lat, lon) or 999, 1),
        }
    json.dump(out, open(OUT, 'w'))
    fib = sorted(v['fib'] for v in out.values())
    fac = sorted(v['fac'] for v in out.values())
    q = lambda a, p: a[int(p * len(a))]
    print(f'{len(out)} nodes → {OUT} ({os.path.getsize(OUT) // 1024} KB)')
    print(f'fibre km: p25={q(fib, .25)} p50={q(fib, .5)} p75={q(fib, .75)} p95={q(fib, .95)}')
    print(f'facility km: p25={q(fac, .25)} p50={q(fac, .5)} p75={q(fac, .75)} p95={q(fac, .95)}')


if __name__ == '__main__':
    main()
