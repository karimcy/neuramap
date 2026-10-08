#!/usr/bin/env python3
"""Phase 1 — GB half-hourly substation profiles → per-node load-duration curves
→ empirically firmable MW at 2 h / 8 h battery.

Feeds data/gb_profiles.json, which the app merges into node popups and the
funnel score for GB nodes that have a measured profile.

Data sources (DNO open-data portals, opendatasoft API):
  UKPN  ukpn-132kv-circuit-operational-data-half-hourly   (21.5M records)
        ukpn-primary-transformer-power-flow-historic-monthly
  NPg   live-primary-operational-metering-14-day          (rolling window)
        operational-data-connectivity                     (circuit ↔ substation map)

Both portals require a FREE registered API key:
  UKPN: https://ukpowernetworks.opendatasoft.com  → sign up → account → API keys
  NPg:  https://northernpowergrid.opendatasoft.com → same
Put them in the environment (or a .env next to this script):
  export UKPN_API_KEY=...
  export NPG_API_KEY=...

Usage:
  python3 pipelines/gb_profiles.py --dno ukpn --limit 20     # pilot batch
  python3 pipelines/gb_profiles.py --dno ukpn                # full run (slow, cached)
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache')
OUT = os.path.join(ROOT, 'data', 'gb_profiles.json')

PORTALS = {
    'ukpn': ('https://ukpowernetworks.opendatasoft.com', 'UKPN_API_KEY',
             'ukpn-132kv-circuit-operational-data-half-hourly',
             'ltds_line_name', 'active_power_mw', 'timestamp'),
    'npg': ('https://northernpowergrid.opendatasoft.com', 'NPG_API_KEY',
            'live-primary-operational-metering-14-day',
            None, None, None),   # field names TBC on first authenticated call
}

# Firming-screen anchors from the company overview: battery covers constraint
# events; screening rule = deficit in ≤8% of half-hours AND no contiguous
# deficit event longer than the battery duration.
DEFICIT_HOURS_MAX = 0.08


def _env(key):
    if os.environ.get(key):
        return os.environ[key]
    envfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(envfile):
        for line in open(envfile):
            if line.strip().startswith(key + '='):
                return line.strip().split('=', 1)[1]
    return None


def ods_get(base, key, path, params):
    url = f'{base}{path}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'Authorization': f'Apikey {key}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_series(dno, name_filter, limit_rows=200000):
    """All half-hourly MW samples for one circuit/substation name."""
    base, envkey, dataset, name_f, mw_f, ts_f = PORTALS[dno]
    key = _env(envkey)
    if not key:
        sys.exit(f'missing {envkey} — register (free) at {base} and export the key')
    os.makedirs(CACHE, exist_ok=True)
    cachef = os.path.join(CACHE, f'{dno}-{name_filter[:60].replace("/", "_")}.json')
    if os.path.exists(cachef):
        return json.load(open(cachef))
    rows, offset = [], 0
    while offset < limit_rows:
        d = ods_get(base, key, f'/api/explore/v2.1/catalog/datasets/{dataset}/records', {
            'where': f'{name_f} like "{name_filter}"',
            'select': f'{ts_f},{mw_f}',
            'order_by': ts_f, 'limit': 100, 'offset': offset,
        })
        batch = d.get('results', [])
        rows += [(r[ts_f], r[mw_f]) for r in batch if r.get(mw_f) is not None]
        if len(batch) < 100:
            break
        offset += 100
        time.sleep(0.15)
    json.dump(rows, open(cachef, 'w'))
    return rows


def ldc_stats(samples_mw, site_firm_mw):
    """Load-duration stats + firmable added-load T at 2 h and 8 h battery.

    headroom(t) = firm capacity − load(t). A new load T is 'firmable at
    duration D' when the battery can bridge every deficit: deficit half-hours
    ≤ 8% of the record AND no contiguous deficit event exceeds D hours.
    """
    if not samples_mw or not site_firm_mw:
        return None
    load = sorted(samples_mw, reverse=True)
    n = len(samples_mw)
    peak, mean = load[0], sum(samples_mw) / n
    head = [max(0.0, site_firm_mw - x) for x in samples_mw]

    def firmable(dur_h):
        lo, hi = 0.0, site_firm_mw * 4
        for _ in range(40):                      # bisect on T
            t = (lo + hi) / 2
            deficit = [max(0.0, t - h) for h in head]
            bad = sum(1 for d in deficit if d > 0)
            # longest contiguous deficit event, in hours
            run = longest = 0
            for d in deficit:
                run = run + 0.5 if d > 0 else 0
                longest = max(longest, run)
            if bad / n <= DEFICIT_HOURS_MAX and longest <= dur_h:
                lo = t
            else:
                hi = t
        return round(lo, 1)

    return {
        'peak_mw': round(peak, 1), 'mean_mw': round(mean, 1),
        'util': round(mean / site_firm_mw, 3) if site_firm_mw else None,
        'min_headroom_mw': round(min(head), 1),
        'firm_2h_mw': firmable(2), 'firm_8h_mw': firmable(8),
        'samples': n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dno', choices=PORTALS, default='ukpn')
    ap.add_argument('--limit', type=int, default=20, help='max nodes this run')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gb = json.load(open(os.path.join(ROOT, 'data', 'GB.json')))
    nodes = [p for p in gb['nodes'] if p.get('kind') == 'demand' and (p.get('mw') or 0) >= 5]
    nodes.sort(key=lambda p: -(p.get('mw') or 0))
    print(f'{len(nodes)} GB demand nodes; processing up to {args.limit} ({args.dno})')

    out = {}
    if os.path.exists(OUT):
        out = json.load(open(OUT))
    done = 0
    for p in nodes:
        if done >= args.limit:
            break
        name = p['n']
        if name in out:
            continue
        if args.dry_run:
            print('would fetch:', name)
            done += 1
            continue
        try:
            rows = fetch_series(args.dno, name.split(' ')[0])
        except Exception as e:
            print(f'  ✗ {name}: {e}')
            continue
        samples = [mw for _, mw in rows]
        # site firm capacity estimate: observed peak + published headroom
        stats = ldc_stats(samples, (max(samples) if samples else 0) + (p.get('mw') or 0))
        if stats:
            stats['source'] = args.dno
            out[name] = stats
            print(f'  ✓ {name}: peak {stats["peak_mw"]} MW · util {stats["util"]} · '
                  f'firm 2h {stats["firm_2h_mw"]} / 8h {stats["firm_8h_mw"]} MW ({stats["samples"]} samples)')
            done += 1
    json.dump(out, open(OUT, 'w'), indent=1)
    print(f'\nwrote {OUT} ({len(out)} nodes total)')


if __name__ == '__main__':
    main()
