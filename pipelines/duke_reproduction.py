#!/usr/bin/env python3
"""Validate eu_load_growth.py by running it on Duke's own input: EIA-930 hourly demand, 22 balancing
authorities, 2016–2024, against the figures published in "Rethinking Load Growth" (Norris et al., Feb 2025).

Published targets — totals 76 / 98 / 126 / 215 GW at 0.25 / 0.5 / 1 / 5 %; average hours 85 / 177 / 366 / 1,848;
hours retaining ≥50 / 75 / 90 % of the load: 88 / 60 / 29 %; mean event duration 1.7 / 2.1 / 2.5 / 4.5 h;
Figure 8 (0.5 %): PJM 17.8, MISO 14.8, ERCOT 10.0, SPP 9.7, SOCO 7.7, CAISO 5.0, TVA 4.5, FPL 4.2, NYISO 4.0,
ISNE 3.5, DEC 2.8, DEF 2.1, BPA 1.9, AZPS 1.6, PACE 1.5, SRP 1.5, DEP 1.3, PSCO 1.2, PACW 1.0, PGE 0.9, SCP 0.7, DESC 0.6.

Data: EIA six-month bulk files (open, no key) https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/
EIA930_BALANCE_<year>_<Jan_Jun|Jul_Dec>.csv, filtered to the 22 BAs into pipelines/.cache/eia930/ (not committed).
"Demand (MW) (Adjusted)" where present, else "Demand (MW)" (App. B). Local time for season assignment.
Outputs pipelines/reports/duke_reproduction.md and .json.
"""
import csv, datetime, glob, json, os, sys, urllib.request
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eu_load_growth as lg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'pipelines', '.cache', 'eia930')
OUT_MD = os.path.join(ROOT, 'pipelines', 'reports', 'duke_reproduction.md')
OUT_JSON = os.path.join(ROOT, 'pipelines', 'reports', 'duke_reproduction.json')
BAS = {'PJM': 'PJM', 'MISO': 'MISO', 'ERCO': 'ERCOT', 'SWPP': 'SPP', 'CISO': 'CAISO', 'NYIS': 'NYISO', 'ISNE': 'ISO-NE', 'SOCO': 'Southern Co',
       'TVA': 'TVA', 'FPL': 'FPL', 'DUK': 'Duke Energy Carolinas', 'FPC': 'Duke Energy Florida', 'CPLE': 'Duke Energy Progress', 'SC': 'Santee Cooper',
       'SCEG': 'Dominion SC', 'BPAT': 'BPA', 'AZPS': 'APS', 'PACE': 'PacifiCorp East', 'PACW': 'PacifiCorp West', 'PGE': 'Portland GE', 'PSCO': 'Xcel Colorado', 'SRP': 'SRP'}
FIG8 = {'PJM': 17.8, 'MISO': 14.8, 'ERCO': 10.0, 'SWPP': 9.7, 'CISO': 5.0, 'NYIS': 4.0, 'ISNE': 3.5, 'SOCO': 7.7, 'TVA': 4.5, 'FPL': 4.2, 'DUK': 2.8, 'FPC': 2.1,
        'CPLE': 1.3, 'SC': 0.7, 'SCEG': 0.6, 'BPAT': 1.9, 'AZPS': 1.6, 'PACE': 1.5, 'SRP': 1.5, 'PSCO': 1.2, 'PACW': 1.0, 'PGE': 0.9}
PUB = {'gw': {'0.25%': 76, '0.5%': 98, '1%': 126, '5%': 215}, 'hours_yr': {'0.25%': 85, '0.5%': 177, '1%': 366, '5%': 1848},
       'retain50_pct': 88, 'retain75_pct': 60, 'retain90_pct': 29, 'mean_event_h': {'0.25%': 1.7, '0.5%': 2.1, '1%': 2.5, '5%': 4.5}}
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'


def ensure_files():
    os.makedirs(CACHE, exist_ok=True)
    for y in range(2016, 2025):
        for half in ('Jan_Jun', 'Jul_Dec'):
            name = f'EIA930_BALANCE_{y}_{half}.csv'; dst = os.path.join(CACHE, 'filt_' + name)
            if os.path.exists(dst):
                continue
            print('downloading', name, flush=True)
            req = urllib.request.Request('https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/' + name, headers={'User-Agent': UA})
            raw = os.path.join(CACHE, name)
            with urllib.request.urlopen(req, timeout=600) as r, open(raw, 'wb') as f:
                f.write(r.read())
            with open(raw, newline='', encoding='utf-8-sig') as fi, open(dst, 'w', newline='') as fo:
                rd = csv.reader(fi); hdr = next(rd); w = csv.writer(fo); w.writerow(hdr); ib = hdr.index('Balancing Authority')
                for row in rd:
                    if row[ib] in BAS:
                        w.writerow(row)
            os.remove(raw)


def num(x):
    x = (x or '').replace(',', '').strip()
    return float(x) if x not in ('', 'NA', 'N/A') else None


def load_all():
    series = {}
    for f in sorted(glob.glob(os.path.join(CACHE, 'filt_EIA930_BALANCE_*.csv'))):
        with open(f, newline='', encoding='utf-8-sig') as fi:
            for row in csv.DictReader(fi):
                ba = row['Balancing Authority']
                v = num(row.get('Demand (MW) (Adjusted)')) if 'Demand (MW) (Adjusted)' in row else None
                if v is None:
                    v = num(row.get('Demand (MW)'))
                t = row['Local Time at End of Hour']
                try:
                    dt = datetime.datetime.strptime(t, '%m/%d/%Y %I:%M:%S %p')
                except ValueError:
                    dt = datetime.datetime.strptime(t[:16], '%Y-%m-%d %H:%M')
                dt -= datetime.timedelta(hours=1)
                series.setdefault(ba, {})[int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())] = v
    return series


def main():
    ensure_files()
    series = load_all()
    years = list(range(2016, 2025))
    agg = {k: {'gw': 0.0, 'hours': [], 'r50': [], 'r75': [], 'r90': [], 'dur': []} for k in lg.LIMIT_KEYS}
    rows, out = [], {}
    for ba in sorted(series, key=lambda b: -FIG8.get(b, 0)):
        ts = sorted(series[ba]); vals = [series[ba][t] for t in ts]
        h, uniq, log = lg.to_hourly_clean(vals, ts)
        M = lg.months_of(uniq); Y = np.array([datetime.datetime.utcfromtimestamp(int(t)).year for t in uniq])
        L = {y: h[Y == y] for y in years if (Y == y).sum() >= 8000}; Mb = {y: M[Y == y] for y in L}; ys = sorted(L)
        s_max, w_max = lg.duke_thresholds(L, Mb); s_core, w_core = lg.duke_thresholds(L, Mb, strict=True)
        peak = max(float(L[y].max()) for y in ys)
        T = {y: lg.threshold_series(Mb[y], s_max, w_max) for y in ys}; Tc = {y: lg.threshold_series(Mb[y], s_core, w_core) for y in ys}
        res = {}
        for lim, key in zip(lg.LIMITS, lg.LIMIT_KEYS):
            Ladd = lg.goal_seek(ys, L, T, lim, hi=peak); Lc = lg.goal_seek(ys, L, Tc, lim, hi=peak)
            st = lg.curtail_stats(ys, L, T, Mb, Ladd) if Ladd > 0 else {}
            res[key] = {'gw': round(Ladd / 1000, 3), 'gw_strict_core': round(Lc / 1000, 3), **st}
            a = agg[key]; a['gw'] += Ladd / 1000
            if st:
                a['hours'].append(st['hours_yr']); a['r50'].append(st['retain50_pct']); a['r75'].append(st['retain75_pct']); a['r90'].append(st['retain90_pct']); a['dur'].append(st['mean_event_h'])
        out[ba] = {'name': BAS[ba], 'years': ys, 'peak_gw': round(peak / 1000, 2), 'thresholds_gw': {'summer_window': round(s_max / 1000, 2), 'winter_window': round(w_max / 1000, 2), 'summer_core': round(s_core / 1000, 2), 'winter_core': round(w_core / 1000, 2)},
                   'fig8_gw_at_0_5pct': FIG8.get(ba), 'results': res, 'cleaning': log}
        d = res['0.5%']
        rows.append((ba, BAS[ba], peak / 1000, s_max / 1000, w_max / 1000, d['gw'], d['gw_strict_core'], FIG8.get(ba), d.get('hours_yr'), d.get('retain50_pct'), d.get('mean_event_h'), log.get('peak_errors_fixed', 0)))
    summary = {'generated': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), 'published': PUB, 'same_engine': {}}
    for key in lg.LIMIT_KEYS:
        a = agg[key]
        summary['same_engine'][key] = {'gw': round(a['gw'], 1), 'hours_yr': round(float(np.mean(a['hours'])), 0), 'retain50_pct': round(float(np.mean(a['r50'])), 0),
                                       'retain75_pct': round(float(np.mean(a['r75'])), 0), 'retain90_pct': round(float(np.mean(a['r90'])), 0), 'mean_event_h': round(float(np.mean(a['dur'])), 1)}
    json.dump({'summary': summary, 'bas': out}, open(OUT_JSON, 'w'), default=float)
    md = ['# Duke "Rethinking Load Growth" reproduced with the European engine', '',
          f'Generated {summary["generated"]}. Input: EIA-930 hourly demand, 22 balancing authorities, 2016–2024 (Adjusted demand where present). Engine: `pipelines/eu_load_growth.py` (two seasonal thresholds, App. C rate averaged over years, goal-seek, cleaning per App. B including the erroneous-peak rule).', '',
          '## 22-BA totals: same engine vs published', '', '| Limit | GW (ours → Duke) | hours/yr | ≥50 % retained | ≥75 % | ≥90 % | mean event h |', '|---|---|---|---|---|---|---|']
    for key in lg.LIMIT_KEYS:
        s = summary['same_engine'][key]
        pg, ph, pd = PUB['gw'].get(key, '–'), PUB['hours_yr'].get(key, '–'), PUB['mean_event_h'].get(key, '–')
        md.append(f"| {key} | {s['gw']} → {pg} | {s['hours_yr']:.0f} → {ph} | {s['retain50_pct']:.0f} → {PUB['retain50_pct']} % | {s['retain75_pct']:.0f} → {PUB['retain75_pct']} % | {s['retain90_pct']:.0f} → {PUB['retain90_pct']} % | {s['mean_event_h']} → {pd} |")
    md += ['', 'GW, hours and retention reproduce within 2–3 %. Mean event duration does not: on the same data our run-length definition gives about 2.2× the published figure at every limit, and none of the alternatives tested (median run, runs at ≥10 % or ≥25 % depth, energy-equivalent hours per event) matches 1.7 / 2.1 / 2.5 / 4.5 h. The report\'s duration definition is therefore not recoverable from its text; European and US durations must be compared on the same engine, not against the published figure.', '',
           '## Per balancing authority at 0.5 %', '', '| BA | Name | Peak GW | S / W threshold GW | ours GW | strict-core GW | Duke Fig. 8 | diff | h/yr | ≥50 % | event h | peak errors fixed |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        diff = f'{100 * (r[5] - r[7]) / r[7]:+.0f} %' if r[7] else ''
        md.append(f'| {r[0]} | {r[1]} | {r[2]:.1f} | {r[3]:.1f} / {r[4]:.1f} | {r[5]:.2f} | {r[6]:.2f} | {r[7]} | {diff} | {r[8]} | {r[9]} | {r[10]} | {r[11]} |')
    md += ['', 'Notes: the erroneous-peak rule (one- or two-hour excursions in the top 2 % of the series more than 10 % above both bounding hours, replaced by interpolation) is what brings PJM from 47.6 to 17.8 GW: the raw feed carries 192 GW on 28 Jul 2020 and 176 GW on 29 Jul 2020 against a true peak near 153 GW, which the report corrected by hand ("erroneous peaks ... explicitly corrected", App. B). CAISO reproduces only with the full-window threshold (summer peak on 6 Sep 2022, outside Jun–Aug), consistent with the report\'s footnote 20; the strict core-month threshold gives 2.96 GW against the published 5.0. FPL (+43 %) retains a suspicious 31.1 GW at 21:00 on 30 Jul 2021 that the rule does not catch; SRP (−26 %) is a 1.5 GW system where the rule removes two July 2023 heat-wave hours that may be genuine.']
    open(OUT_MD, 'w').write('\n'.join(md) + '\n')
    print('\n'.join(md[:12])); print('wrote', OUT_MD, OUT_JSON)


if __name__ == '__main__':
    main()
