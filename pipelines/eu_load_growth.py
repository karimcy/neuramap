#!/usr/bin/env python3
"""EU curtailment-enabled headroom — Duke's "Rethinking Load Growth" method, applied to Europe.

Replicates Norris, Profeta, Patino-Echeverri & Cowie-Haskell (Nicholas Institute, Duke, Feb 2025)
step by step. Where their text and Appendix C define the method, this file follows it; the few
place where a European winter-peaking system needs an adaptation is marked DUKE-ADAPT below and
goes one step beyond the report's own footnote-20 exception, always in the optimistic direction.
Optimistic sensitivities are opt-in and labelled; the headline otherwise follows the report.

Duke method (report §Analysis of Curtailment-Enabled Headroom, Appendix B/C):
  Data      hourly load, nine years (2016–2024), per balancing authority; missing/zero values
            interpolated, spikes corrected (App. B).                      → here: nine most recent
            full years available per market (default 2017–2025), hourly means of sub-hourly data,
            same cleaning.
  Threshold two seasonal peak thresholds per system: max SUMMER peak (Jun–Aug) and max WINTER
            peak (Dec–Feb) observed across all years. Summer threshold applies Apr–Oct, winter
            threshold Nov–Mar (footnote 20).                                → 'duke' ceiling.
            DUKE-ADAPT: in four US cases (AZPS winter, FPL winter, CAISO summer and winter) the
            seasonal peak fell "within one month" of the core window and the report used that
            peak as the threshold (footnote 20). European winter-peaking systems have April and
            October loads well above the Jun–Aug maximum, so with the strict core-month threshold
            existing load already exceeds the summer threshold and the goal-seek returns 0 GW
            (GB, DE). We therefore take each threshold as the maximum over ALL the months it
            governs (summer Apr–Oct, winter Nov–Mar): identical to the report wherever the
            seasonal peak falls in the core months, one month further than the report's
            exception otherwise, and never below the report's threshold. The strict core-month
            variant is stored alongside for comparison.
  Curtail   Curtailment_t(L) = max(0, Demand_t + L − Threshold_t), MWh per hour; annual total ÷
            (L × 8,760) = annual curtailment rate; the rate for L is the AVERAGE of the annual
            rates over the N years (Appendix C). Goal-seek L so that the average rate equals the
            limit (0.25 %, 0.5 %, 1 %, 5 %); bisection here, root_scalar in the report.
  Stats     average hours/yr with any curtailment; hours retaining ≥50 / ≥75 / ≥90 % of the new
            load; mean curtailment-event duration (consecutive hours, any magnitude); seasonal
            split of curtailed energy (winter Dec–Feb vs summer Jun–Aug); aggregate and seasonal
            load factors; curtailment-rate-vs-load-addition curve in 0.25 %-of-peak steps.

Optimistic sensitivities (not Duke; off by default): ceiling 'period' = one threshold, the
all-time peak; capability margin ×1.03 / ×1.05 on the thresholds. Battery-firmed variant
(chronological SOC simulation, from v1) is reported separately.

Outputs data/eu_load_growth.json and pipelines/reports/eu_load_growth.md.
Usage: python3 pipelines/eu_load_growth.py [--years 2017-2025] [--markets de,fr,...] [--zones SE1,..]
       [--no-battery] [--sleep 15]
"""
import argparse
import datetime
import json
import os
import sys
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eu_flexible_headroom as v1  # noqa: E402  (data fetch + SOC simulation reused)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_JSON = os.path.join(ROOT, 'data', 'eu_load_growth.json')
OUT_MD = os.path.join(ROOT, 'pipelines', 'reports', 'eu_load_growth.md')

LIMITS = [0.0025, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05]   # Duke's four limits (0.25 / 0.5 / 1 / 5 %) plus 1.5 / 2 / 3 %
LIMIT_KEYS = ['0.25%', '0.5%', '1%', '1.5%', '2%', '3%', '5%']
CEILINGS = ['duke', 'period']                    # duke = two seasonal thresholds; period = all-time peak (optimistic)
MARGINS = [1.0, 1.03, 1.05]
HIST_EDGES = [0, 1, 2, 4, 8, 1e9]
HIST_LABELS = ['≤1h', '1–2h', '2–4h', '4–8h', '>8h']
SUMMER_CORE, WINTER_CORE = {6, 7, 8}, {12, 1, 2}
SUMMER_WINDOW, WINTER_WINDOW = {4, 5, 6, 7, 8, 9, 10}, {11, 12, 1, 2, 3}
AVAIL_LADDER = v1.AVAIL_LADDER
BATTERY_DURS = (2, 4, 8)
ZONE_NAMES = {
    'DE': 'Germany (DE-LU)', 'FR': 'France', 'ES': 'Spain', 'PT': 'Portugal', 'IT': 'Italy',
    'PL': 'Poland', 'SE': 'Sweden', 'NO': 'Norway', 'FI': 'Finland', 'GB': 'Great Britain',
    'DK': 'Denmark', 'AT': 'Austria', 'HR': 'Croatia', 'IE': 'Ireland', 'NL': 'Netherlands',
    'BE': 'Belgium', 'CH': 'Switzerland', 'CZ': 'Czechia',
}


# ───────────────────────────── data access ─────────────────────────────
def fetch_bzn_year(bzn, year):
    """energy-charts public_power with bzn= (bidding zone); quarter caching like v1."""
    import urllib.parse
    series = {}
    for qi, (m0, m1) in enumerate([(1, 4), (4, 7), (7, 10), (10, 1)]):
        cachef = os.path.join(v1.CACHE, f'ec_bzn_{bzn}_{year}-q{qi + 1}.json')
        if os.path.exists(cachef):
            d = json.load(open(cachef))
        else:
            url = ('https://api.energy-charts.info/public_power?' + urllib.parse.urlencode(
                {'bzn': bzn, 'start': f'{year}-{m0:02d}-01T00:00Z', 'end': f'{year + (m1 == 1)}-{m1:02d}-01T00:00Z'}))
            d = json.loads(v1._get(url))
            json.dump(d, open(cachef, 'w'))
            v1.time.sleep(5)
        load = next((pt['data'] for pt in d.get('production_types', []) if pt['name'] == 'Load'), None)
        if load:
            for ts, v in zip(d['unix_seconds'], load):
                series[ts] = v
    ts_sorted = sorted(series)
    return [series[t] for t in ts_sorted], ts_sorted, 'energy-charts.info (ENTSO-E mirror, bidding zone)'


def fetch_country_year(code, year):
    """Return (values, unix timestamps, source) at native resolution; None values kept for cleaning."""
    if code == 'GB':
        vals, dt_h, src, months = v1.fetch_gb_year(year)
        # NESO rows are half-hourly in settlement order; synthesise timestamps
        t0 = int(datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
        ts = [t0 + i * 1800 for i in range(len(vals))]
        return vals, ts, src
    import urllib.parse
    series = {}
    for qi, (m0, m1) in enumerate([(1, 4), (4, 7), (7, 10), (10, 1)]):
        cachef = os.path.join(v1.CACHE, f'ec_{code.lower()}_{year}-q{qi + 1}.json')
        if os.path.exists(cachef):
            d = json.load(open(cachef))
        else:
            url = ('https://api.energy-charts.info/public_power?' + urllib.parse.urlencode(
                {'country': code.lower(), 'start': f'{year}-{m0:02d}-01T00:00Z', 'end': f'{year + (m1 == 1)}-{m1:02d}-01T00:00Z'}))
            d = json.loads(v1._get(url))
            json.dump(d, open(cachef, 'w'))
            v1.time.sleep(5)
        load = next((pt['data'] for pt in d.get('production_types', []) if pt['name'] == 'Load'), None)
        if load:
            for ts, v in zip(d['unix_seconds'], load):
                series[ts] = v
    ts_sorted = sorted(series)
    return [series[t] for t in ts_sorted], ts_sorted, 'energy-charts.info (ENTSO-E mirror)'


# ───────────────────────────── Duke Appendix B: cleaning, hourly ─────────────────────────────
def to_hourly_clean(vals, ts):
    """Hourly means (Duke's data is hourly), then: missing/zero → linear interpolation; spikes
    (>1.5× both neighbours) and drops (<0.5× both neighbours) → mean of neighbours. Returns
    (hourly MW array, hour-start timestamps array, cleaning log)."""
    ts = np.asarray(ts, dtype=np.int64)
    v = np.array([np.nan if x is None else float(x) for x in vals], dtype=float)
    hour = (ts // 3600) * 3600
    uniq, inv = np.unique(hour, return_inverse=True)
    sums = np.zeros(len(uniq)); cnts = np.zeros(len(uniq))
    ok = ~np.isnan(v) & (v > 0)
    np.add.at(sums, inv[ok], v[ok]); np.add.at(cnts, inv[ok], 1)
    h = np.where(cnts > 0, sums / np.maximum(cnts, 1), np.nan)
    log = {'hours': int(len(h)), 'missing_or_zero': int(np.isnan(h).sum())}
    # fill gaps by linear interpolation (Duke App. B)
    if np.isnan(h).any():
        idx = np.arange(len(h)); good = ~np.isnan(h)
        h = np.interp(idx, idx[good], h[good])
    # spikes / drops relative to both neighbours (anywhere in the series)
    prev = np.roll(h, 1); nxt = np.roll(h, -1)
    spike = (h > 1.5 * np.maximum(prev, nxt)); drop = (h < 0.5 * np.minimum(prev, nxt))
    bad = spike | drop; bad[0] = bad[-1] = False
    log['spikes_fixed'] = int(spike.sum()); log['drops_fixed'] = int(drop.sum())
    h[bad] = (prev[bad] + nxt[bad]) / 2
    # Duke App. B "erroneous peaks": one- or two-hour excursions in the top 2 % of the series that sit more than
    # 10 % above both bounding hours. System load cannot ramp 10 % up and 10 % down within an hour at its peak;
    # these are reporting errors (e.g. PJM 28–29 Jul 2020 at 192 / 176 GW against a ~150 GW peak) and would
    # otherwise set the thresholds. Replaced by linear interpolation across the block.
    top = np.quantile(h, 0.98); fixed = []
    for _ in range(2):
        n = len(h); flag = np.zeros(n, dtype=bool)
        one = (h > top) & (h > 1.10 * np.maximum(np.roll(h, 1), np.roll(h, -1))); one[0] = one[-1] = False
        flag |= one
        if n > 4:
            lo = np.minimum(h[1:-2], h[2:-1]); bound = np.maximum(h[:-3], h[3:])
            two = (lo > top) & (lo > 1.10 * bound)
            idx = np.where(two)[0] + 1
            flag[idx] = True; flag[idx + 1] = True
        if not flag.any():
            break
        idx = np.arange(n); good = ~flag
        fixed.extend(int(i) for i in np.where(flag)[0])
        h = np.where(flag, np.interp(idx, idx[good], h[good]), h)
    log['peak_errors_fixed'] = len(fixed)
    log['peak_errors_at'] = [datetime.datetime.utcfromtimestamp(int(uniq[i])).strftime('%Y-%m-%d %H') for i in fixed[:12]]
    return h, uniq, log


def months_of(uniq_ts):
    return np.array([datetime.datetime.utcfromtimestamp(int(t)).month for t in uniq_ts], dtype=int)


# ───────────────────────────── Duke thresholds and goal-seek ─────────────────────────────
def duke_thresholds(L_by_year, M_by_year, strict=False):
    """Two thresholds across all years: summer and winter maxima. strict=True uses Duke's core
    months (Jun–Aug, Dec–Feb) only; default (DUKE-ADAPT, see module docstring) uses the full
    windows the thresholds govern (Apr–Oct, Nov–Mar) so existing load never exceeds its own threshold."""
    allL = np.concatenate([L_by_year[y] for y in L_by_year]); allM = np.concatenate([M_by_year[y] for y in L_by_year])
    s_months = SUMMER_CORE if strict else SUMMER_WINDOW
    w_months = WINTER_CORE if strict else WINTER_WINDOW
    s_max = allL[np.isin(allM, list(s_months))].max()
    w_max = allL[np.isin(allM, list(w_months))].max()
    return float(s_max), float(w_max)


def threshold_when(L_by_year, M_by_year, months):
    """(year, month) in which the maximum over `months` occurred — provenance for the threshold."""
    best = (None, None, -1.0)
    for y in sorted(L_by_year):
        sel = np.isin(M_by_year[y], list(months))
        if sel.any():
            i = int(np.argmax(np.where(sel, L_by_year[y], -1.0)))
            if L_by_year[y][i] > best[2]:
                best = (y, int(M_by_year[y][i]), float(L_by_year[y][i]))
    return {'year': best[0], 'month': best[1]}


def threshold_series(M, s_max, w_max):
    return np.where(np.isin(M, list(SUMMER_WINDOW)), s_max, w_max)


def annual_rate(L_y, T_y, Ladd):
    """Duke App. C: annual curtailed MWh / (L × 8,760)."""
    curt = np.maximum(0.0, L_y + Ladd - T_y).sum()
    return curt / (Ladd * 8760.0)


def avg_rate(years, L_by_year, T_by_year, Ladd):
    return float(np.mean([annual_rate(L_by_year[y], T_by_year[y], Ladd) for y in years]))


def goal_seek(years, L_by_year, T_by_year, limit, hi):
    lo, h = 0.0, hi
    for _ in range(50):
        mid = (lo + h) / 2
        if mid <= 0:
            break
        if avg_rate(years, L_by_year, T_by_year, mid) <= limit:
            lo = mid
        else:
            h = mid
    return lo


def curtail_stats(years, L_by_year, T_by_year, M_by_year, Ladd):
    """Duke's hour statistics, averaged per year: hours with any curtailment; hours retaining
    ≥50/75/90 % of the new load; mean event duration; seasonal split of curtailed energy."""
    hrs = []; r50 = []; r75 = []; r90 = []; runs_all = []; w_e = 0.0; s_e = 0.0; tot_e = 0.0
    month_e = np.zeros(12); month_h = np.zeros(12)
    for y in years:
        c = np.maximum(0.0, L_by_year[y] + Ladd - T_by_year[y])
        over = c > 0
        hrs.append(over.sum())
        r50.append((over & (c <= 0.5 * Ladd)).sum()); r75.append((over & (c <= 0.25 * Ladd)).sum()); r90.append((over & (c <= 0.1 * Ladd)).sum())
        d = np.diff(np.concatenate([[0], over.astype(int), [0]]))
        runs = np.where(d == -1)[0] - np.where(d == 1)[0]
        runs_all.extend(runs.tolist())
        M = M_by_year[y]
        w_e += c[np.isin(M, list(WINTER_CORE))].sum(); s_e += c[np.isin(M, list(SUMMER_CORE))].sum(); tot_e += c.sum()
        np.add.at(month_e, M - 1, c); np.add.at(month_h, M - 1, over.astype(float))
    hrs_avg = float(np.mean(hrs))
    runs_all = np.asarray(runs_all, dtype=float)
    return {
        'hours_yr': round(hrs_avg, 1),
        'hours_retain50_yr': round(float(np.mean(r50)), 1), 'hours_retain75_yr': round(float(np.mean(r75)), 1), 'hours_retain90_yr': round(float(np.mean(r90)), 1),
        'retain50_pct': round(100 * float(np.mean(r50)) / hrs_avg, 1) if hrs_avg else None,
        'retain75_pct': round(100 * float(np.mean(r75)) / hrs_avg, 1) if hrs_avg else None,
        'retain90_pct': round(100 * float(np.mean(r90)) / hrs_avg, 1) if hrs_avg else None,
        'events_yr': round(len(runs_all) / len(years), 1),
        'mean_event_h': round(float(runs_all.mean()), 2) if len(runs_all) else 0.0,
        'max_event_h': round(float(runs_all.max()), 1) if len(runs_all) else 0.0,
        'hist': np.histogram(runs_all, bins=HIST_EDGES)[0].tolist() if len(runs_all) else [0] * len(HIST_LABELS),
        'winter_share_pct': round(100 * w_e / tot_e, 1) if tot_e else None,
        'summer_share_pct': round(100 * s_e / tot_e, 1) if tot_e else None,
        'month_energy_pct': [round(100 * float(v) / tot_e, 1) for v in month_e] if tot_e else [0] * 12,
        'month_hours_yr': [round(float(v) / len(years), 1) for v in month_h],
    }


# ───────────────────────────── per zone ─────────────────────────────
def analyse_zone(code, years_wanted, is_bzn=False, do_battery=True, strict_check=True):
    L_by_year, M_by_year, logs, src = {}, {}, {}, None
    for y in years_wanted:
        try:
            vals, ts, src = (fetch_bzn_year(code, y) if is_bzn else fetch_country_year(code, y))
        except Exception as e:  # noqa: BLE001
            print(f'  {code} {y}: unavailable ({str(e)[:80]})'); continue
        if len(vals) == 0:
            print(f'  {code} {y}: empty'); continue
        h, uniq, log = to_hourly_clean(vals, ts)
        if len(h) < 8000:   # Duke excluded 2015 for incomplete reporting (fewer than half the hours)
            print(f'  {code} {y}: incomplete ({len(h)} h) — excluded'); continue
        L_by_year[y] = h; M_by_year[y] = months_of(uniq); logs[y] = log
    if not L_by_year:
        return None
    years = sorted(L_by_year)
    allL = np.concatenate([L_by_year[y] for y in years]); allM = np.concatenate([M_by_year[y] for y in years])
    peak = float(allL.max())
    s_max, w_max = duke_thresholds(L_by_year, M_by_year)
    s_core, w_core = duke_thresholds(L_by_year, M_by_year, strict=True)
    # seasonal load factors (Duke: average seasonal load as share of the seasonal maximum)
    sm = np.isin(allM, list(SUMMER_CORE)); wm = np.isin(allM, list(WINTER_CORE))
    z = {
        'name': ZONE_NAMES.get(code, code), 'bidding_zone': bool(is_bzn), 'years': years, 'n_years': len(years),
        'source': src, 'cleaning': logs,
        'peak_gw': round(peak / 1000, 2), 'peak_by_year_gw': {str(y): round(float(L_by_year[y].max()) / 1000, 2) for y in years},
        'mean_gw': round(float(allL.mean()) / 1000, 2), 'load_factor': round(float(allL.mean() / peak), 3),
        'load_factor_summer': round(float(allL[sm].mean() / allL[sm].max()), 3) if sm.any() else None,
        'load_factor_winter': round(float(allL[wm].mean() / allL[wm].max()), 3) if wm.any() else None,
        'thresholds_gw': {'summer_window': round(s_max / 1000, 2), 'winter_window': round(w_max / 1000, 2),
                          'summer_core': round(s_core / 1000, 2), 'winter_core': round(w_core / 1000, 2)},
        'threshold_when': {'summer_window': threshold_when(L_by_year, M_by_year, SUMMER_WINDOW), 'winter_window': threshold_when(L_by_year, M_by_year, WINTER_WINDOW)},
        'threshold_note': ('Duke-exact: seasonal peaks fall inside Jun–Aug / Dec–Feb'
                           if abs(s_max - s_core) < 1 and abs(w_max - w_core) < 1 else
                           'DUKE-ADAPT: seasonal peak outside Jun–Aug / Dec–Feb; threshold = max over the months it governs (one month beyond the report\'s footnote-20 exception)'),
        'ldc_gw': [round(float(v) / 1000, 3) for v in np.quantile(allL, np.linspace(1, 0, 201))],
        'ldc_by_year_gw': {str(y): [round(float(v) / 1000, 3) for v in np.quantile(L_by_year[y], np.linspace(1, 0, 201))] for y in years},
        'results': {}, 'per_year': {}, 'curve': {}, 'battery': {},
    }
    for kind in CEILINGS:
        z['results'][kind] = {}; z['per_year'][kind] = {}; z['curve'][kind] = {}
        for m in MARGINS:
            if kind == 'duke':
                T_by_year = {y: threshold_series(M_by_year[y], s_max * m, w_max * m) for y in years}
            else:
                T_by_year = {y: np.full(len(L_by_year[y]), peak * m) for y in years}
            cell = {}
            for lim, key in zip(LIMITS, LIMIT_KEYS):
                Ladd = goal_seek(years, L_by_year, T_by_year, lim, hi=peak)
                st = curtail_stats(years, L_by_year, T_by_year, M_by_year, Ladd) if Ladd > 0 else {}
                cell[key] = {'gw': round(Ladd / 1000, 3), 'pct_peak': round(100 * Ladd / peak, 1), **st}
                if m == 1.0:
                    z['per_year'][kind][key] = {str(y): round(1000 * goal_seek([y], L_by_year, T_by_year, lim, hi=peak) / 1e6, 3) for y in years}
            z['results'][kind][f'{m:.2f}'] = cell
            if m == 1.0:
                # Duke Figure 9 / App. A: curtailment rate vs load addition in 0.25 %-of-peak steps (to 30 % of peak)
                steps = [peak * k * 0.0025 for k in range(1, 121)]
                z['curve'][kind] = {'pct_peak': [round(k * 0.25, 2) for k in range(1, 121)],
                                    'rate_pct': [round(100 * avg_rate(years, L_by_year, T_by_year, s), 3) for s in steps]}
    if strict_check:
        # Duke-strict thresholds (core months only) at margin 1.00 for the comparison table
        T_strict = {y: threshold_series(M_by_year[y], s_core, w_core) for y in years}
        z['results']['duke_strict_core'] = {'1.00': {key: {'gw': round(goal_seek(years, L_by_year, T_strict, lim, hi=peak) / 1000, 3)} for lim, key in zip(LIMITS, LIMIT_KEYS)}}
    if do_battery:
        # battery-firmed load (v1 SOC simulation) on the latest year, Duke thresholds, margin 1.00
        y = years[-1]
        caps = threshold_series(M_by_year[y], s_max, w_max)
        z['battery']['duke_latest_year'] = {}
        for dur in BATTERY_DURS:
            z['battery']['duke_latest_year'][f'{dur}h'] = {
                lab: round(v1.battery_firm_t(L_by_year[y].tolist(), 1.0, caps.tolist(), dur, uptime_min=sm) / 1000, 2) for lab, sm in AVAIL_LADDER}
    return z


# ───────────────────────────── main ─────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2017-2025', help='nine most recent full years, as Duke used 2016–2024')
    ap.add_argument('--markets', default='de,fr,es,pt,it,pl,se,no,fi,gb')
    ap.add_argument('--zones', default='')
    ap.add_argument('--no-battery', action='store_true')
    ap.add_argument('--sleep', type=int, default=15)
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split('-')); years = list(range(y0, y1 + 1))
    v1.time = types.SimpleNamespace(sleep=lambda s: time.sleep(args.sleep))
    os.makedirs(v1.CACHE, exist_ok=True)

    zones = {}
    for code in [c.strip().upper() for c in args.markets.split(',') if c.strip()]:
        print(f'{code}: {years[0]}–{years[-1]}'); t0 = time.time()
        z = analyse_zone(code, years, do_battery=not args.no_battery)
        if not z:
            print(f'  {code}: no data'); continue
        zones[code] = z; r = z['results']['duke']['1.00']
        print(f"  {code}: {z['years'][0]}–{z['years'][-1]} ({z['n_years']} yrs) peak {z['peak_gw']} GW · thresholds S {z['thresholds_gw']['summer_window']} / W {z['thresholds_gw']['winter_window']} GW · "
              f"Duke @0.5%: {r['0.5%']['gw']} GW ({r['0.5%']['pct_peak']}% of peak, {r['0.5%']['hours_yr']} h/yr, retain50 {r['0.5%']['retain50_pct']}%) · {time.time() - t0:.0f}s")
    for bz in [z.strip() for z in args.zones.split(',') if z.strip()]:
        print(f'{bz}: bidding zone {years[0]}–{years[-1]}')
        z = analyse_zone(bz, years, is_bzn=True, do_battery=not args.no_battery)
        if z:
            zones[bz] = z; r = z['results']['duke']['1.00']
            print(f"  {bz}: peak {z['peak_gw']} GW · Duke @0.5%: {r['0.5%']['gw']} GW ({r['0.5%']['pct_peak']}%)")

    countries = [c for c, z in zones.items() if not z['bidding_zone']]
    totals = {kind: {f'{m:.2f}': {key: round(sum(zones[c]['results'][kind][f'{m:.2f}'][key]['gw'] for c in countries), 2) for key in LIMIT_KEYS}
                     for m in MARGINS} for kind in CEILINGS}
    avg_hours = {key: round(float(np.mean([zones[c]['results']['duke']['1.00'][key].get('hours_yr', 0) for c in countries])), 0) for key in LIMIT_KEYS} if countries else {}
    avg_ret50 = {key: round(float(np.mean([zones[c]['results']['duke']['1.00'][key].get('retain50_pct') or 0 for c in countries])), 0) for key in LIMIT_KEYS} if countries else {}
    avg_dur = {key: round(float(np.mean([zones[c]['results']['duke']['1.00'][key].get('mean_event_h', 0) for c in countries])), 1) for key in LIMIT_KEYS} if countries else {}
    battery_totals = {}
    for dur in BATTERY_DURS:
        battery_totals[f'{dur}h'] = {lab: round(sum((zones[c].get('battery', {}).get('duke_latest_year', {}).get(f'{dur}h', {}).get(lab) or 0) for c in countries), 2) for lab, _ in AVAIL_LADDER}
    latest = max((zones[c]['years'][-1] for c in countries), default=None)
    result = {
        'period': f'{latest} load, thresholds {years[0]}–{years[-1]}' if latest else '',
        'battery_totals_gw': battery_totals if not args.no_battery else None,
        'method': ('Duke "Rethinking Load Growth" (Norris et al., Feb 2025) replicated on European hourly load: two seasonal '
                   'peak thresholds per market over the study years (summer applied Apr–Oct, winter Nov–Mar), hourly curtailment '
                   'max(0, demand + L − threshold), annual curtailed energy ÷ (L × 8,760) averaged over years, goal-seek L for '
                   '0.25/0.5/1/5 % limits; hours, retention (≥50/75/90 %), event duration and seasonal split as in the report. '
                   'DUKE-ADAPT: where a European seasonal peak falls outside Jun–Aug / Dec–Feb the threshold is the maximum over the '
                   'months it governs (Apr–Oct / Nov–Mar); the report itself moved one month outside the core window in four US cases '
                   '(footnote 20), this goes to the full window and is never below the report\'s threshold. "period" ceiling and capability margins are optimistic '
                   'sensitivities, not Duke. System-level; no transmission constraints; no reserve margin; not nodal.'),
        'duke_reference': {'gw': {'0.25%': 76, '0.5%': 98, '1%': 126, '5%': 215}, 'hours_yr': {'0.25%': 85, '0.5%': 177, '1%': 366, '5%': 1848},
                           'retain50_pct': 88, 'retain75_pct': 60, 'retain90_pct': 29, 'mean_event_h': {'0.25%': 1.7, '0.5%': 2.1, '1%': 2.5, '5%': 4.5},
                           'us_peak_gw': 744, 'source': 'Norris, Profeta, Patino-Echeverri, Cowie-Haskell, Nicholas Institute, Duke University, Feb 2025'},
        'us_same_engine': (json.load(open(os.path.join(ROOT, 'pipelines', 'reports', 'duke_reproduction.json')))['summary']['same_engine']
                           if os.path.exists(os.path.join(ROOT, 'pipelines', 'reports', 'duke_reproduction.json')) else None),
        'years': years, 'limits': LIMIT_KEYS, 'ceilings': CEILINGS, 'margins': [f'{m:.2f}' for m in MARGINS], 'hist_labels': HIST_LABELS,
        'zones': zones, 'totals_gw': totals,
        'eu_avg': {'hours_yr': avg_hours, 'retain50_pct': avg_ret50, 'mean_event_h': avg_dur, 'summed_peak_gw': round(sum(zones[c]['peak_gw'] for c in countries), 1)},
        'generated': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(result, open(OUT_JSON, 'w'))

    lines = ['# EU curtailment-enabled headroom — Duke method replicated', '',
             f'Generated {result["generated"]}. Years requested {years[0]}–{years[-1]}. Markets {len(countries)}, bidding zones {len(zones) - len(countries)}.', '',
             '## Totals (countries), GW of new load at each curtailment limit', '',
             '| Ceiling | margin | ' + ' | '.join(LIMIT_KEYS) + ' |', '|---|---|' + '---|' * len(LIMIT_KEYS)]
    for kind in CEILINGS:
        for m in result['margins']:
            lines.append(f'| {kind} | ×{m} | ' + ' | '.join(str(totals[kind][m][k]) for k in LIMIT_KEYS) + ' |')
    lines += ['', f"EU average (Duke ×1.00): hours/yr {avg_hours} · retain≥50% {avg_ret50} · mean event h {avg_dur}",
              'Duke (US, 22 BAs, 744 GW peak): 76 / 98 / 126 / 215 GW; 85 / 177 / 366 / 1,848 h; 88 / 60 / 29 % retain ≥50/75/90 %; 1.7 / 2.1 / 2.5 / 4.5 h.', '',
              '## Per market, Duke thresholds ×1.00', '',
              '| Market | Years | Peak GW | Thresholds S/W GW | LF | 0.25% | 0.5% | 1% | 5% | h/yr @0.5% | ≥50% @0.5% | event h @0.5% | strict-core @0.5% | 8h battery @99.9% |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for code in sorted(zones):
        z = zones[code]; r = z['results']['duke']['1.00']; sc = z['results'].get('duke_strict_core', {}).get('1.00', {}).get('0.5%', {}).get('gw', '')
        b = z.get('battery', {}).get('duke_latest_year', {}).get('8h', {}).get('99.9%', '')
        lines.append(f"| {code} | {z['years'][0]}–{z['years'][-1]} ({z['n_years']}) | {z['peak_gw']} | {z['thresholds_gw']['summer_window']} / {z['thresholds_gw']['winter_window']} | {z['load_factor']} | "
                     f"{r['0.25%']['gw']} | {r['0.5%']['gw']} | {r['1%']['gw']} | {r['5%']['gw']} | {r['0.5%'].get('hours_yr', '')} | {r['0.5%'].get('retain50_pct', '')} | {r['0.5%'].get('mean_event_h', '')} | {sc} | {b} |")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    open(OUT_MD, 'w').write('\n'.join(lines) + '\n')
    print(f'\nTotals Duke ×1.00: {totals["duke"]["1.00"]}\nwrote {OUT_JSON} and {OUT_MD}')


if __name__ == '__main__':
    main()
