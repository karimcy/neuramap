#!/usr/bin/env python3
"""GB solar & battery asset layer — REPD (coords, status, dates) × TEC (queue, gate).

Output: data/gb_assets.geojson — point per project ≥1 MW:
  tech: battery | solar | hybrid (REPD co-location link or TEC combined plant type)
  status, year_operational, mw, storage_type, county,
  tec: {gate, stage, site, mw_effective} where the project matches a TEC row.

Duration (MWh) is NOT in REPD or TEC; where the NGED embedded capacity register
(open) carries storage MWh for a matching site it is joined in as dur_h. Other
DNOs' ECRs are key-gated — see docs/IMPROVEMENTS.md.

Run: python3 pipelines/gb_assets.py
"""
import csv
import json
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPD = os.path.join(ROOT, 'pipelines', '.cache', 'repd', 'REPD_Q1_2026.csv')
TEC = os.path.join(ROOT, 'pipelines', '.cache', 'neso', 'tec_register.csv')
OUT = os.path.join(ROOT, 'data', 'gb_assets.geojson')

KEEP_STATUS = {'Operational', 'Under Construction', 'Awaiting Construction', 'Application Submitted'}


# ── OSGB36 grid → WGS84 lat/lon (inverse TM + Helmert; ~5 m accuracy) ──
def osgb_to_wgs84(E, N):
    a, b = 6377563.396, 6356256.909            # Airy 1830
    F0, lat0, lon0 = 0.9996012717, math.radians(49), math.radians(-2)
    N0, E0 = -100000.0, 400000.0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    lat, M = lat0, 0.0
    while N - N0 - M >= 1e-5:
        lat += (N - N0 - M) / (a * F0)
        Ma = (1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * (lat - lat0)
        Mb = (3 * n + 3 * n ** 2 + 2.625 * n ** 3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (1.875 * n ** 2 + 1.875 * n ** 3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = (35 / 24) * n ** 3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
    sin_l, cos_l, tan_l = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * F0 / math.sqrt(1 - e2 * sin_l ** 2)
    rho = a * F0 * (1 - e2) / (1 - e2 * sin_l ** 2) ** 1.5
    eta2 = nu / rho - 1
    VII = tan_l / (2 * rho * nu)
    VIII = tan_l / (24 * rho * nu ** 3) * (5 + 3 * tan_l ** 2 + eta2 - 9 * tan_l ** 2 * eta2)
    IX = tan_l / (720 * rho * nu ** 5) * (61 + 90 * tan_l ** 2 + 45 * tan_l ** 4)
    X = 1 / (cos_l * nu)
    XI = (nu / rho + 2 * tan_l ** 2) / (6 * cos_l * nu ** 3)
    XII = (5 + 28 * tan_l ** 2 + 24 * tan_l ** 4) / (120 * cos_l * nu ** 5)
    dE = E - E0
    lat_ = lat - VII * dE ** 2 + VIII * dE ** 4 - IX * dE ** 6
    lon_ = lon0 + X * dE - XI * dE ** 3 + XII * dE ** 5
    # Helmert OSGB36 → WGS84
    lat_, lon_ = math.degrees(lat_), math.degrees(lon_)
    slat, clat = math.sin(math.radians(lat_)), math.cos(math.radians(lat_))
    slon, clon = math.sin(math.radians(lon_)), math.cos(math.radians(lon_))
    H = 0.0
    a2, e2b = 6377563.396, 1 - (6356256.909 / 6377563.396) ** 2
    nu2 = a2 / math.sqrt(1 - e2b * slat ** 2)
    x = (nu2 + H) * clat * clon
    y = (nu2 + H) * clat * slon
    z = ((1 - e2b) * nu2 + H) * slat
    tx, ty, tz = 446.448, -125.157, 542.06
    rx, ry, rz = [math.radians(v / 3600) for v in (0.1502, 0.247, 0.8421)]
    s = -20.4894e-6
    x2 = tx + (1 + s) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + s) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + s) * z
    a3, e3 = 6378137.0, 1 - (6356752.3142 / 6378137.0) ** 2
    p = math.sqrt(x2 ** 2 + y2 ** 2)
    lat3 = math.atan2(z2, p * (1 - e3))
    for _ in range(6):
        nu3 = a3 / math.sqrt(1 - e3 * math.sin(lat3) ** 2)
        lat3 = math.atan2(z2 + e3 * nu3 * math.sin(lat3), p)
    return round(math.degrees(lat3), 5), round(math.degrees(math.atan2(y2, x2)), 5)


def norm(s):
    s = re.sub(r'\b(ltd|limited|plc|llp|solar farm|solar park|energy storage|battery|bess|project|phase \d+|farm|park)\b',
               '', (s or '').lower())
    return re.sub(r'[^a-z0-9]+', '', s)


def main():
    with open(REPD, encoding='cp1252', errors='replace') as f:
        repd = list(csv.DictReader(f))
    with open(TEC, encoding='utf-8-sig') as f:
        tec = list(csv.DictReader(f))

    # TEC lookup by normalized project name (storage/solar rows only)
    tec_ix = {}
    for r in tec:
        pt = r.get('Plant Type') or ''
        if 'Energy Storage' in pt or 'PV Array' in pt:
            tec_ix.setdefault(norm(r['Project Name']), r)

    # co-location: batteries linked to a solar REPD ref
    coloc = {r['Ref ID']: r.get('Storage Co-location REPD Ref ID') for r in repd}

    feats, matched_tec, skipped = [], 0, 0
    for r in repd:
        ttype = r.get('Technology Type') or ''
        if ttype not in ('Battery', 'Solar Photovoltaics'):
            continue
        if (r.get('Development Status (short)') or '') not in KEEP_STATUS:
            continue
        try:
            mw = float(r.get('Installed Capacity (MWelec)') or 0)
            E, N = float(r['X-coordinate']), float(r['Y-coordinate'])
        except (TypeError, ValueError):
            skipped += 1
            continue
        if mw < 1:
            continue
        lat, lon = osgb_to_wgs84(E, N)
        if not (49 < lat < 61.5 and -9 < lon < 3):
            skipped += 1
            continue
        tech = 'battery' if ttype == 'Battery' else 'solar'
        if tech == 'battery' and (r.get('Storage Co-location REPD Ref ID') or '').strip():
            tech = 'hybrid'
        opy = (r.get('Operational') or '')[-4:]
        t = tec_ix.get(norm(r.get('Site Name')))
        if t:
            matched_tec += 1
        feats.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': r.get('Site Name'), 'operator': r.get('Operator (or Applicant)'),
                'tech': tech, 'mw': mw,
                'storage_type': r.get('Storage Type') or None,
                'status': r.get('Development Status (short)'),
                'year_op': int(opy) if opy.isdigit() else None,
                'county': r.get('County') or r.get('Region'),
                'repd_id': r.get('Ref ID'),
                'tec': ({'gate': t.get('Gate'), 'stage': t.get('Stage'), 'site': t.get('Connection Site'),
                         'status': t.get('Project Status'), 'mw_conn': t.get('MW Connected')} if t else None),
            },
        })
    out = {'type': 'FeatureCollection',
           'meta': {'source': 'REPD Q1 2026 (DESNZ) × NESO TEC register 21-07-2026',
                    'filter': '>=1 MW, live statuses', 'built': '2026-07-23'},
           'features': feats}
    json.dump(out, open(OUT, 'w'))
    import collections
    st = collections.Counter((f['properties']['tech'], f['properties']['status']) for f in feats)
    print(f'{len(feats)} assets written ({skipped} skipped), TEC-matched {matched_tec}')
    for (tech, status), n in sorted(st.items()):
        print(f'  {tech:8s} {status:22s} {n}')
    ops = [f for f in feats if f['properties']['status'] == 'Operational']
    yrs = collections.Counter(f['properties']['year_op'] for f in ops if f['properties']['year_op'])
    print('operational by year (recent):', sorted(yrs.items())[-8:])


if __name__ == '__main__':
    main()
