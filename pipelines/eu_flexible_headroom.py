#!/usr/bin/env python3
"""EU flexible-load headroom — the European 'Rethinking Load Growth' figure.

Method (Norris et al., Nicholas Institute 2025, applied per market):
take a full year of measured system load L(t); treat the historical peak as the
system's proven serving capability; a new constant flexible load dP is feasible
at curtailment tolerance c if the energy it must shed — sum(max(0, L+dP - peak))
— is at most c of its potential annual energy (dP * hours). Bisect for max dP.

System-level screening, country granularity — NOT nodal, NOT additive with the
nodal published-headroom figures. Sources: Fraunhofer energy-charts (ENTSO-E
mirror, open) for 9 markets; NESO historic demand (open) for GB.

Usage: python3 pipelines/eu_flexible_headroom.py [--year 2025]
"""
import argparse
import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'loadcurves')
OUT_JSON = os.path.join(ROOT, 'data', 'eu_flexible_headroom.json')
OUT_MD = os.path.join(ROOT, 'pipelines', 'reports', 'eu_flexible_headroom.md')

EC_MARKETS = ['de', 'fr', 'es', 'pt', 'it', 'pl', 'se', 'no', 'fi']
TOLERANCES = [0.0025, 0.005, 0.01, 0.05]
# availability ladder: required UPTIME — share of intervals fully served
AVAIL_LADDER = [('100%', 1.0), ('99.99%', 0.9999), ('99.95%', 0.9995),
                ('99.9%', 0.999), ('99.5%', 0.995), ('99%', 0.99)]


def _get(url, timeout=120, attempts=5):
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'OpportunityMap/1.0 research'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < attempts - 1:
                time.sleep(45 * (i + 1))
                continue
            raise


def fetch_ec_year(cc, year):
    """Hourly-or-finer Load series (MW) from energy-charts, monthly chunks, cached."""
    series = {}
    quarters = [(1, 4), (4, 7), (7, 10), (10, 1)]
    for qi, (m0, m1) in enumerate(quarters):
        cachef = os.path.join(CACHE, f'ec_{cc}_{year}-q{qi + 1}.json')
        if os.path.exists(cachef):
            d = json.load(open(cachef))
        else:
            start = f'{year}-{m0:02d}-01T00:00Z'
            end = f'{year + (m1 == 1)}-{m1:02d}-01T00:00Z'
            url = ('https://api.energy-charts.info/public_power?' +
                   urllib.parse.urlencode({'country': cc, 'start': start, 'end': end}))
            d = json.loads(_get(url))
            json.dump(d, open(cachef, 'w'))
            time.sleep(5)
        load = next((pt['data'] for pt in d.get('production_types', []) if pt['name'] == 'Load'), None)
        if not load:
            continue
        for ts, v in zip(d['unix_seconds'], load):
            if v is not None:
                series[ts] = v
    import datetime
    ts_sorted = sorted(series)
    vals = [series[t] for t in ts_sorted]
    months = [datetime.datetime.utcfromtimestamp(t).month for t in ts_sorted]
    dt_h = (ts_sorted[1] - ts_sorted[0]) / 3600 if len(ts_sorted) > 1 else 1.0
    return vals, dt_h, 'energy-charts.info (ENTSO-E mirror)', months


def fetch_gb_year(year):
    """NESO historic demand: ND column (MW), half-hourly."""
    cachef = os.path.join(CACHE, f'neso_gb_{year}.csv')
    if not os.path.exists(cachef):
        pkg = json.loads(_get('https://api.neso.energy/api/3/action/package_show?id=historic-demand-data'))
        res = next(r for r in pkg['result']['resources'] if str(year) in (r.get('name') or ''))
        open(cachef, 'wb').write(_get(res['url']))
    vals, months = [], []
    for row in csv.DictReader(io.StringIO(open(cachef, encoding='utf-8-sig').read())):
        nd = row.get('ND') or row.get('nd')
        sd = row.get('SETTLEMENT_DATE') or row.get('settlement_date') or ''
        if nd:
            try:
                v = float(nd)
            except ValueError:
                continue
            vals.append(v)
            try:
                months.append(int(sd.split('-')[1]))
            except (IndexError, ValueError):
                months.append(1)
    return vals, 0.5, 'NESO historic demand (ND)', months


SEASON = {12: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3}


def seasonal_caps(loads, months):
    """Norris-style capability floor: each interval's ceiling is the observed
    peak OF ITS OWN SEASON — a winter-peaking system has not proven it can
    serve winter-peak levels in summer."""
    smax = [0.0] * 4
    for l, m in zip(loads, months):
        s = SEASON[m]
        if l > smax[s]:
            smax[s] = l
    return [smax[SEASON[m]] for m in months]


def curtail_ratio(loads, dt_h, caps, dp):
    """Energy the flexible load dP must shed, as share of its potential energy."""
    shed = sum(min(dp, max(0.0, l + dp - c)) for l, c in zip(loads, caps)) * dt_h
    return shed / (dp * len(loads) * dt_h)


def event_stats(loads, dt_h, caps, dp):
    runs, run = [], 0.0
    for l, cap in zip(loads, caps):
        if l + dp > cap:
            run += dt_h
        elif run:
            runs.append(run)
            run = 0.0
    if run:
        runs.append(run)
    hours_over = sum(runs)
    return {
        'events_per_year': len(runs),
        'mean_event_h': round(hours_over / len(runs), 2) if runs else 0,
        'max_event_h': round(max(runs), 2) if runs else 0,
        'pct_hours_curtailed': round(100 * hours_over / (len(loads) * dt_h), 2),
    }


def battery_firm_t(loads, dt_h, caps, dur_h, rt_eff=0.88, uptime_min=1.0):
    """Max constant load T made effectively firm by a battery of duration dur_h.

    Chronological SOC simulation — captures event duration AND clustering:
    battery (power B=T shortfall cap, energy T*dur_h) discharges through every
    deficit interval and can only recharge through the same connection when there
    is spare room under the observed peak. T qualifies on an UPTIME basis: the
    share of intervals in which the load runs at full T (grid + battery cover it
    completely) must be >= uptime_min. Any interval with a shortfall, however
    small, counts as downtime — the SLA view, not an energy-served view.
    """
    eff = rt_eff ** 0.5
    e_cap_per_t = dur_h  # battery energy per MW of load

    def feasible(t):
        if t <= 0:
            return True
        e_cap = t * e_cap_per_t
        soc = e_cap
        down_h = 0.0
        for l, cap in zip(loads, caps):
            room = cap - l - t           # spare import under the seasonal ceiling
            if room >= 0:
                soc = min(e_cap, soc + min(t, room) * dt_h * eff)   # charge ≤1C
            else:
                need = -room * dt_h       # energy the battery must supply
                give = min(soc * eff, need)
                soc -= give / eff
                if need - give > 1e-9:
                    down_h += dt_h        # any shortfall = downtime for the interval
        total_h = len(loads) * dt_h
        return (total_h - down_h) / total_h >= uptime_min

    lo, hi = 0.0, max(caps)
    for _ in range(40):
        mid = (lo + hi) / 2
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return lo


def analyse(vals, dt_h, months):
    caps = seasonal_caps(vals, months)
    peak = max(vals)
    mean = sum(vals) / len(vals)
    out = {
        'peak_gw': round(peak / 1000, 2),
        'mean_gw': round(mean / 1000, 2),
        'load_factor': round(mean / peak, 3),
        'samples': len(vals),
        'dt_h': dt_h,
        'headroom_gw': {},
    }
    for c in TOLERANCES:
        lo, hi = 0.0, peak
        for _ in range(50):
            mid = (lo + hi) / 2
            if mid <= 0:
                break
            if curtail_ratio(vals, dt_h, caps, mid) <= c:
                lo = mid
            else:
                hi = mid
        out['headroom_gw'][f'{c * 100:g}%'] = round(lo / 1000, 2)
        if c == 0.005:
            out['events_at_0.5%'] = event_stats(vals, dt_h, caps, lo)
    # battery-firmable: chronological SOC sim across an availability ladder
    out['battery_firm_gw'] = {}
    for dur in (2, 4, 8):
        out['battery_firm_gw'][f'{dur}h'] = {
            label: round(battery_firm_t(vals, dt_h, caps, dur, uptime_min=sm) / 1000, 2)
            for label, sm in AVAIL_LADDER}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2025)
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)

    markets, failures = {}, []
    for cc in EC_MARKETS:
        try:
            vals, dt_h, src, months = fetch_ec_year(cc, args.year)
            if len(vals) * dt_h < 8000:
                raise ValueError(f'only {len(vals)} samples ({len(vals) * dt_h:.0f} h) — incomplete year')
            markets[cc.upper()] = {**analyse(vals, dt_h, months), 'source': src, 'period': str(args.year)}
            print(f'{cc.upper()}: peak {markets[cc.upper()]["peak_gw"]} GW, '
                  f'lf {markets[cc.upper()]["load_factor"]}, '
                  f'headroom@1% {markets[cc.upper()]["headroom_gw"]["1%"]} GW')
        except Exception as e:
            failures.append((cc.upper(), str(e)))
            print(f'{cc.upper()}: FAILED — {e}')
    try:
        vals, dt_h, src, months = fetch_gb_year(args.year)
        markets['GB'] = {**analyse(vals, dt_h, months), 'source': src, 'period': str(args.year)}
        print(f'GB: peak {markets["GB"]["peak_gw"]} GW, lf {markets["GB"]["load_factor"]}, '
              f'headroom@1% {markets["GB"]["headroom_gw"]["1%"]} GW')
    except Exception as e:
        failures.append(('GB', str(e)))
        print(f'GB: FAILED — {e}')

    totals = {}
    for c in TOLERANCES:
        key = f'{c * 100:g}%'
        totals[key] = round(sum(m['headroom_gw'][key] for m in markets.values()), 1)
    battery_totals = {}
    for dur_key in next(iter(markets.values()))['battery_firm_gw']:
        battery_totals[dur_key] = {
            label: round(sum(m['battery_firm_gw'][dur_key][label] for m in markets.values()), 1)
            for label, _ in AVAIL_LADDER}
    result = {
        'method': "Flexible headroom: Norris-style LDC (energy-curtailment tolerance). Battery firm "
                  "figures: chronological SOC simulation with UPTIME thresholds — share of "
                  "intervals fully served; any shortfall counts the interval as downtime. "
                  "Ceiling = SEASONAL observed peaks (DJF/MAM/JJA/SON, Norris-style capability "
                  "floor). System-level screening; not nodal.",
        'period': str(args.year),
        'markets': markets,
        'totals_gw': totals,
        'battery_totals_gw': battery_totals,
        'failures': failures,
    }
    json.dump(result, open(OUT_JSON, 'w'), indent=1)

    lines = ['# EU flexible-load headroom (Norris-style, system LDC)', '',
             f'Period: {args.year}. Markets: {len(markets)}. '
             f'Totals GW: ' + ' · '.join(f'{k}: {v}' for k, v in totals.items()), '',
             'Battery-firmable totals GW (availability ladder): ' +
             json.dumps(battery_totals), '',
             '| Market | Peak GW | LF | flex@1% | 2h@100% | 2h@99.9% | 8h@100% | 8h@99.9% | 8h@99% | events@0.5%/yr | max h |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for cc in sorted(markets):
        m = markets[cc]
        ev = m.get('events_at_0.5%', {})
        h = m['headroom_gw']
        bf = m['battery_firm_gw']
        lines.append(f"| {cc} | {m['peak_gw']} | {m['load_factor']} | {h['1%']} "
                     f"| {bf['2h']['100%']} | {bf['2h']['99.9%']} | {bf['8h']['100%']} "
                     f"| {bf['8h']['99.9%']} | {bf['8h']['99%']} "
                     f"| {ev.get('events_per_year', '')} | {ev.get('max_event_h', '')} |")
    if failures:
        lines += ['', 'Failures: ' + ', '.join(f'{cc} ({msg})' for cc, msg in failures)]
    open(OUT_MD, 'w').write('\n'.join(lines) + '\n')
    print(f'\nTotals (GW): {totals}')
    print(f'wrote {OUT_JSON} and {OUT_MD}')


if __name__ == '__main__':
    main()
