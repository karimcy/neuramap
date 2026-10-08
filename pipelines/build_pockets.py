#!/usr/bin/env python3
"""Compact node table for the "Start here" story page: every demand node with >= 5 MW of published headroom,
with the industrial land around it, its upstream check, its operator's flexible-connection status and,
where the node is metered, its Duke-method flexible figure.

Inputs: js/data.js (FREE countries), data/<CC>.json, sites/<CC>.geojson, data/node_context.json, data/pt_profiles.json
Output: data/pockets.json
  cols  column names for each row in `nodes`
  nodes [[cc, name, lat, lon, mw, kv, plots, ha_max, km_min, eff_mw, up, rule_status, flex1, firm8h], ...]
  summary {cc: {nodes, by_band{5-20,20-50,50-100,100+}, with_land, rule_status}}
"""
import datetime, glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANDS = [(5, 20, '5–20'), (20, 50, '20–50'), (50, 100, '50–100'), (100, 1e9, '100+')]


def countries():
    src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
    free = json.loads(re.search(r'const FREE=(\{.*?\});\n', src, re.S).group(1))
    out = dict(free)
    for f in glob.glob(os.path.join(ROOT, 'data', '[A-Z][A-Z].json')):
        cc = os.path.basename(f)[:2]
        out.setdefault(cc, json.load(open(f)))
    return out


def main():
    ctx = json.load(open(os.path.join(ROOT, 'data', 'node_context.json')))
    pt = json.load(open(os.path.join(ROOT, 'data', 'pt_profiles.json')))
    pt_by_code = {v['code']: v for v in pt.values() if v.get('code')}
    rows, summary = [], {}
    for cc, d in sorted(countries().items()):
        site = {}
        sf = os.path.join(ROOT, 'sites', f'{cc}.geojson')
        if os.path.exists(sf):
            for ft in json.load(open(sf))['features']:
                p = ft['properties']; k = p.get('node')
                s = site.setdefault(k, [0, 0.0, 99.0])
                s[0] += 1; s[1] = max(s[1], p.get('area_ha') or 0); s[2] = min(s[2], p.get('dist_km') or 99)
        seen = set()
        sm = summary.setdefault(cc, {'nodes': 0, 'by_band': {b[2]: 0 for b in BANDS}, 'with_land': 0, 'small_with_land': 0,
                                     'rule': ctx['rules'].get(ctx['cc'].get(cc) or '', {}).get('status') if cc != 'GB' else 'mixed'})
        for n in d.get('nodes', []):
            if n.get('kind') != 'demand' or not isinstance(n.get('mw'), (int, float)) or n['mw'] < 5 or n.get('lat') is None:
                continue
            key = (n['n'], round(n['lat'], 3), round(n['lon'], 3))
            if key in seen:
                continue
            seen.add(key)
            g = ctx['gb'].get(n['n']) if cc == 'GB' else None
            rid = g['rule'] if g else ctx['cc'].get(cc)
            rstat = ctx['rules'].get(rid or '', {}).get('status', 'unverified')
            m = pt_by_code.get(n.get('code')) if cc == 'PT' else None
            s = site.get(n['n'])
            rows.append([cc, n['n'], round(n['lat'], 4), round(n['lon'], 4), round(n['mw'], 1), n.get('kv'),
                         s[0] if s else 0, round(s[1], 1) if s else 0, round(s[2], 2) if s else None,
                         g['eff_mw'] if g else round(n['mw'], 1), g['up'] if g else 'unknown', rstat,
                         (m['flex_mw'] or {}).get('1%') if m and m.get('flex_mw') else None, m['firm_8h_mw'] if m else None])
            sm['nodes'] += 1
            for lo, hi, lab in BANDS:
                if lo <= n['mw'] < hi:
                    sm['by_band'][lab] += 1
            if s:
                sm['with_land'] += 1
                if n['mw'] < 50:
                    sm['small_with_land'] += 1
    # Portugal: PT.json carries generation nodes only; demand headroom comes from E-Redes carga-na-subestacao
    # (disponibilidade, worst season of the latest year), placed with the coordinates of the same installation code
    carga_p = os.path.join(ROOT, 'pipelines', '.cache', 'pt_eredes', 'carga_na_subestacao.json')
    if os.path.exists(carga_p):
        carga = json.load(open(carga_p))
        latest = max(int(r['ano']) for r in carga)
        disp = {}
        for r in carga:
            if int(r['ano']) == latest and r.get('disponibilidade') is not None:
                disp[r['codigo_da_instalacao']] = min(disp.get(r['codigo_da_instalacao'], 1e9), float(r['disponibilidade']))
        ptn = {n['code']: n for n in json.load(open(os.path.join(ROOT, 'data', 'PT.json')))['nodes'] if n.get('code')}
        site = {}
        sf = os.path.join(ROOT, 'sites', 'PT.geojson')
        if os.path.exists(sf):
            for ft in json.load(open(sf))['features']:
                p = ft['properties']; sv = site.setdefault(p.get('node'), [0, 0.0, 99.0])
                sv[0] += 1; sv[1] = max(sv[1], p.get('area_ha') or 0); sv[2] = min(sv[2], p.get('dist_km') or 99)
        sm = summary.setdefault('PT', {'nodes': 0, 'by_band': {b[2]: 0 for b in BANDS}, 'with_land': 0, 'small_with_land': 0, 'rule': 'unverified'})
        for code, mw in disp.items():
            n = ptn.get(code)
            if not n or mw < 5:
                continue
            m = pt_by_code.get(code); sv = site.get(n['n'])
            rows.append(['PT', n['n'], round(n['lat'], 4), round(n['lon'], 4), round(mw, 1), n.get('kv'),
                         sv[0] if sv else 0, round(sv[1], 1) if sv else 0, round(sv[2], 2) if sv else None, round(mw, 1), 'unknown',
                         ctx['rules'].get(ctx['cc'].get('PT') or '', {}).get('status', 'unverified'),
                         (m.get('flex_mw') or {}).get('1%') if m else None, m['firm_8h_mw'] if m else None])
            sm['nodes'] += 1
            for lo, hi, lab in BANDS:
                if lo <= mw < hi:
                    sm['by_band'][lab] += 1
            if sv:
                sm['with_land'] += 1
                if mw < 50:
                    sm['small_with_land'] += 1
    summary = {k: v for k, v in summary.items() if v['nodes']}
    out = {'generated': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'),
           'cols': ['cc', 'name', 'lat', 'lon', 'mw', 'kv', 'plots', 'ha_max', 'km_min', 'eff_mw', 'up', 'rule', 'flex1', 'firm8h'],
           'nodes': rows, 'summary': summary, 'rules': ctx['rules']}
    p = os.path.join(ROOT, 'data', 'pockets.json')
    json.dump(out, open(p, 'w'), separators=(',', ':'), ensure_ascii=False)
    print(f'wrote {p}: {len(rows)} nodes, {os.path.getsize(p) // 1024} KB')
    for cc, v in summary.items():
        print(' ', cc, v)


if __name__ == '__main__':
    main()
