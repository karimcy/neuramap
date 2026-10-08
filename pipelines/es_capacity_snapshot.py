#!/usr/bin/env python3
"""Phase 0.4 — Spain nodal access-capacity snapshot archiver.

Spain's demand access-capacity maps are brand new (first editions Dec 2025 /
Feb 2026) and are *overwritten in place* every month at a fixed URL — the
publishers keep no history. The time dimension therefore does not exist yet.
This script captures it: every run immutably archives the current REE
(transmission) + e-Distribucion (DSO) demand & generation capacity files under
`pipelines/.cache/es_snapshots/YYYY-MM-DD/` with a provenance manifest
(source page, file URL, sha256, edition date), so month-over-month drift becomes
measurable. Designed to be cron'd monthly; re-runs on the same day are no-ops.

Sources (file links are *discovered* from the pages each run, so new monthly
editions are picked up automatically):
  REE transmission (RdT):
    demand     https://www.ree.es/es/clientes/consumidor/acceso-conexion/conoce-la-capacidad-de-acceso
    generation https://www.ree.es/es/clientes/generador/acceso-conexion/conoce-la-capacidad-de-acceso
  e-Distribucion (DSO, per CNMC company code R1-026 / R1-299):
    demand     https://www.edistribucion.com/es/red-electrica/nodos-capacidad-red/capacidad-demanda.html
    generation https://www.edistribucion.com/es/red-electrica/nodos-capacidad-red/capacidad-generacion.html

Also emits pipelines/reports/es_snapshot_summary.md: node counts, total
available vs occupied MW (demand + generation), and drift vs the app's current
FREE.ES stats (parsed from js/data.js).

Usage:
  python3 pipelines/es_capacity_snapshot.py            # archive today + summary
  python3 pipelines/es_capacity_snapshot.py --force    # re-download even if archived
  python3 pipelines/es_capacity_snapshot.py --summary-only   # rebuild MD from newest snapshot
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_ROOT = os.path.join(ROOT, 'pipelines', '.cache', 'es_snapshots')
REPORT = os.path.join(ROOT, 'pipelines', 'reports', 'es_snapshot_summary.md')
DATA_JS = os.path.join(ROOT, 'js', 'data.js')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NeuraOpportunityMap/1.0')
HEADERS = {'User-Agent': UA}
SLEEP = 1.5          # polite delay between file downloads
TIMEOUT = 90

# ── source page definitions ────────────────────────────────────────────────
REE = 'https://www.ree.es'
EDIS = 'https://www.edistribucion.com'
PAGES = [
    {'operator': 'REE', 'kind': 'demand',
     'page': f'{REE}/es/clientes/consumidor/acceso-conexion/conoce-la-capacidad-de-acceso',
     'pat': re.compile(r'href="(/sites/default/files/[^"]*?(\d{4}_\d{2}_\d{2})_GRT_demanda\.csv)"')},
    {'operator': 'REE', 'kind': 'generation',
     'page': f'{REE}/es/clientes/generador/acceso-conexion/conoce-la-capacidad-de-acceso',
     'pat': re.compile(r'href="(/sites/default/files/[^"]*?(\d{4}_\d{2}_\d{2})_GRT_generacion\.csv)"')},
    {'operator': 'e-Distribucion', 'kind': 'demand',
     'page': f'{EDIS}/es/red-electrica/nodos-capacidad-red/capacidad-demanda.html',
     'pat': re.compile(r'href="(/content/dam/[^"]*?(\d{4}_\d{2}_\d{2})_(R\d+)_demanda\.csv)"')},
    {'operator': 'e-Distribucion', 'kind': 'generation',
     'page': f'{EDIS}/es/red-electrica/nodos-capacidad-red/capacidad-generacion.html',
     # generation filename carries an accented 'ó' -> literal or %-encoded in href
     'pat': re.compile(r'href="(/content/dam/[^"]*?(\d{4}_\d{2}_\d{2})_(R\d+)_generaci(?:\xf3n|%C3%B3n|on)\.csv)"')},
]


# ── helpers ─────────────────────────────────────────────────────────────────
def sess():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def num(v):
    """Parse a Spanish-locale numeric cell -> float or None."""
    if v is None:
        return None
    v = v.strip()
    if not v or v.upper() == 'N/A':
        return None
    if re.fullmatch(r'-?\d{1,3}(\.\d{3})+(,\d+)?', v):      # 1.234.567,8  (EU thousands)
        v = v.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'-?\d+,\d+', v):                     # 12,34
        v = v.replace(',', '.')
    elif re.fullmatch(r'-?\d+(\.\d+)?', v):                 # 1234 or 12.34
        pass
    else:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def discover(s, page):
    """Return list of {file_url, edition_date, subcode} for a source page, one
    per (subcode) picking the latest edition date."""
    r = s.get(page['page'], timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text
    best = {}   # subcode -> (edition_date, file_url)
    for m in page['pat'].finditer(html):
        href = m.group(1)
        edition = m.group(2).replace('_', '-')             # YYYY-MM-DD
        subcode = m.group(3) if page['pat'].groups >= 3 else None
        key = subcode or '_'
        if key not in best or edition > best[key][0]:
            best[key] = (edition, href)
    out = []
    base = REE if page['operator'] == 'REE' else EDIS
    for key, (edition, href) in sorted(best.items()):
        out.append({'file_url': base + href, 'edition_date': edition,
                    'subcode': None if key == '_' else key})
    return out


# ── REE parsing (multi-row header, semicolon, Spanish locale) ────────────────
def _ree_load(raw):
    rows = list(csv.reader(io.StringIO(raw), delimiter=';'))
    # header rows precede the first row whose col1 is a numeric substation code
    di = next(i for i, r in enumerate(rows)
              if len(r) > 1 and re.fullmatch(r'\d{3,6}', r[1].strip() or ''))
    headers = rows[:di]
    data = [r for r in rows[di:] if r and r[0].strip()]
    return headers, data


def _ws(s):
    """Collapse all whitespace (incl. embedded newlines in REE headers) to single spaces."""
    return ' '.join(s.split())


def _find_col(headers, *, exact=None, all_of=None):
    """Locate a column by header text (whitespace-normalised). `exact`: a header
    cell in any header row equals this string. `all_of`: list of (row_index, text)
    that must all match."""
    ncols = max(len(r) for r in headers)
    for c in range(ncols):
        cells = [_ws(headers[r][c]) if c < len(headers[r]) else '' for r in range(len(headers))]
        if exact is not None and any(cell == exact for cell in cells):
            return c
        if all_of is not None and all(
                (ri < len(headers) and c < len(headers[ri]) and _ws(headers[ri][c]) == txt)
                for ri, txt in all_of):
            return c
    raise KeyError(f'column not found: exact={exact!r} all_of={all_of!r}')


def parse_ree_demand(raw):
    headers, data = _ree_load(raw)
    c_avail = _find_col(headers, all_of=[
        (0, 'CAPACIDAD DE ACCESO DISPONIBLE PARA SOLICITUDES CRITERIO GENERAL'),
        (1, 'DEMANDA'), (2, 'CEP'), (3, 'CH')])
    c_gr = _find_col(headers, exact='Capacidad de acceso otorgada demanda RdT')
    c_pd = _find_col(headers, exact='Capacidad de acceso solicitada en curso y pendiente resolver demanda RdT')
    c_mg = _find_col(headers, all_of=[
        (0, 'MARGEN CAPACIDAD DE ACCESO TOTAL PARA CONEXIÓN A LA RDT'),
        (1, 'DEMANDA'), (2, 'CEP'), (3, 'CH')])
    return _agg(data, c_avail, c_gr, c_pd, c_mg, threshold=100,
                labels=('available_demand (disponible criterio general CEP/CH)',
                        'granted_demand_RdT', 'pending_demand_RdT', 'total_margin_demand_RdT'))


def parse_ree_generation(raw):
    headers, data = _ree_load(raw)
    # generation "available" headline = MGES (renewable) total access margin
    c_avail = _find_col(headers, all_of=[(0, 'MARGEN DE CAPACIDAD DE ACCESO TOTAL'),
                                         (1, 'Generación'), (2, 'MGES [MW]')])
    c_gr = _find_col(headers, exact='Capacidad de acceso otorgada GEN')
    c_pd = _find_col(headers, exact='Capacidad de acceso solicitada en curso y pendiente resolver GEN')
    c_nod = _find_col(headers, all_of=[(0, 'CRITERIO ESTÁTICO GENERACIÓN'),
                                       (1, 'Capacidad de acceso nodal')])
    return _agg(data, c_avail, c_gr, c_pd, c_nod, threshold=5,
                labels=('available_gen (MGES total access margin)',
                        'granted_gen', 'pending_gen', 'static_gen_nodal_capacity'))


def _agg(data, c_avail, c_gr, c_pd, c_extra, threshold, labels):
    """Aggregate the four chosen columns over all substation rows. `threshold`
    is the MW cut for the like-for-like (app-comparable) subset."""
    n = 0
    tot = {k: 0.0 for k in ('avail', 'granted', 'pending', 'extra')}
    sat = 0            # nodes with 0 available capacity
    ge = {'n': 0, 'avail': 0.0}
    for r in data:
        a = num(r[c_avail]) if c_avail < len(r) else None
        if a is None:
            continue                    # rows without a capacity value for this criterion
        n += 1
        tot['avail'] += a
        tot['granted'] += num(r[c_gr]) or 0.0 if c_gr < len(r) else 0.0
        tot['pending'] += num(r[c_pd]) or 0.0 if c_pd < len(r) else 0.0
        tot['extra'] += num(r[c_extra]) or 0.0 if c_extra < len(r) else 0.0
        if a == 0:
            sat += 1
        if a >= threshold:
            ge['n'] += 1
            ge['avail'] += a
    return {'nodes': n, 'saturated_nodes': sat, 'threshold': threshold,
            'available_mw': round(tot['avail']), 'granted_mw': round(tot['granted']),
            'pending_mw': round(tot['pending']), 'extra_mw': round(tot['extra']),
            'ge_nodes': ge['n'], 'ge_available_mw': round(ge['avail']),
            'labels': labels}


# ── e-Distribucion parsing (single header, semicolon, Spanish locale) ────────
def parse_edis(raw, kind):
    rows = list(csv.reader(io.StringIO(raw), delimiter=';'))
    hdr = [h.strip() for h in rows[0]]

    def col(name_contains):
        for i, h in enumerate(hdr):
            if all(t in strip_accents(h).lower() for t in name_contains):
                return i
        raise KeyError(f'{name_contains} not in {hdr}')

    c_av = col(['capacidad', 'disponible'])
    c_oc = col(['ocupada'])
    c_ad = col(['admitida'])
    data = [r for r in rows[1:] if r and any(c.strip() for c in r)]
    n = 0
    av = oc = ad = 0.0
    sat = 0
    for r in data:
        a = num(r[c_av]) if c_av < len(r) else None
        if a is None:
            continue
        n += 1
        av += a
        oc += num(r[c_oc]) or 0.0 if c_oc < len(r) else 0.0
        ad += num(r[c_ad]) or 0.0 if c_ad < len(r) else 0.0
        if a == 0:
            sat += 1
    return {'nodes': n, 'saturated_nodes': sat, 'available_mw': round(av, 1),
            'occupied_mw': round(oc, 1), 'admitted_pending_mw': round(ad, 1)}


# ── FREE.ES baseline (from js/data.js) ───────────────────────────────────────
def free_es_stats():
    src = open(DATA_JS, encoding='utf-8').read()
    i = src.index('const FREE=') + len('const FREE=')
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    es = json.loads(src[i:end])['ES']
    return es.get('stats', {}), es.get('srcs', {})


# ── archive ──────────────────────────────────────────────────────────────────
def archive(force=False):
    today = dt.date.today().isoformat()
    day_dir = os.path.join(SNAP_ROOT, today)
    os.makedirs(day_dir, exist_ok=True)
    manifest_path = os.path.join(day_dir, 'manifest.json')
    manifest = {'snapshot_date': today, 'files': []}
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path))
    have = {f['file_url'] for f in manifest['files']}

    s = sess()
    fetched, skipped = 0, 0
    for page in PAGES:
        try:
            found = discover(s, page)
        except Exception as e:
            print(f'  ! discover failed {page["operator"]}/{page["kind"]}: {e}')
            continue
        for f in found:
            if f['file_url'] in have and not force:
                skipped += 1
                continue
            sub = f'_{f["subcode"]}' if f['subcode'] else ''
            fname = f'{page["operator"].lower().replace("-", "")}_{page["kind"]}_{f["edition_date"]}{sub}.csv'
            local = os.path.join(day_dir, fname)
            try:
                r = s.get(f['file_url'], timeout=TIMEOUT)
                r.raise_for_status()
            except Exception as e:
                print(f'  ! download failed {fname}: {e}')
                continue
            raw = r.content
            with open(local, 'wb') as fh:
                fh.write(raw)
            entry = {
                'operator': page['operator'], 'kind': page['kind'],
                'subcode': f['subcode'], 'source_page': page['page'],
                'file_url': f['file_url'], 'edition_date': f['edition_date'],
                'local_file': fname, 'sha256': sha256_bytes(raw), 'bytes': len(raw),
                'fetched_at': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            }
            # replace any prior entry for same url (on --force)
            manifest['files'] = [x for x in manifest['files'] if x['file_url'] != f['file_url']]
            manifest['files'].append(entry)
            print(f'  + {fname}  ({len(raw):,} B, ed. {f["edition_date"]})')
            fetched += 1
            time.sleep(SLEEP)

    manifest['files'].sort(key=lambda x: (x['operator'], x['kind'], x['subcode'] or ''))
    manifest['generated_at'] = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump(manifest, open(manifest_path, 'w'), indent=1, ensure_ascii=False)
    print(f'snapshot {today}: {fetched} fetched, {skipped} already-archived (no-op)')
    return day_dir, manifest


# ── summary ──────────────────────────────────────────────────────────────────
def newest_snapshot():
    days = [d for d in os.listdir(SNAP_ROOT)
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d)
            and os.path.exists(os.path.join(SNAP_ROOT, d, 'manifest.json'))]
    if not days:
        return None, None
    day = max(days)
    day_dir = os.path.join(SNAP_ROOT, day)
    return day_dir, json.load(open(os.path.join(day_dir, 'manifest.json')))


def build_summary():
    day_dir, manifest = newest_snapshot()
    if not manifest:
        print('no snapshot to summarise')
        return
    stats, srcs = free_es_stats()

    def read(entry):
        return open(os.path.join(day_dir, entry['local_file']), encoding='utf-8-sig').read()

    parsed = {}          # (operator, kind[, subcode]) -> metrics
    for e in manifest['files']:
        raw = read(e)
        try:
            if e['operator'] == 'REE' and e['kind'] == 'demand':
                parsed[('REE', 'demand')] = (e, parse_ree_demand(raw))
            elif e['operator'] == 'REE' and e['kind'] == 'generation':
                parsed[('REE', 'generation')] = (e, parse_ree_generation(raw))
            elif e['operator'] == 'e-Distribucion':
                parsed[('EDIS', e['kind'], e['subcode'])] = (e, parse_edis(raw, e['kind']))
        except Exception as ex:
            print(f'  ! parse failed {e["local_file"]}: {ex}')

    L = []
    L.append('# Spain nodal access-capacity snapshot — summary\n')
    L.append(f'*Snapshot archived {manifest["snapshot_date"]} · generated '
             f'{manifest.get("generated_at", "")} · WP-IBERIA / Phase 0.4*\n')
    L.append('Spain publishes these files at fixed URLs and overwrites them monthly with '
             'no public history. This archiver captures each edition immutably so '
             'month-over-month drift is measurable — the time dimension no competitor holds.\n')

    # provenance table
    L.append('## Archived this snapshot\n')
    L.append('| Operator | Kind | Sub | Edition | Bytes | sha256 (12) |')
    L.append('|---|---|---|---|--:|---|')
    for e in manifest['files']:
        L.append(f'| {e["operator"]} | {e["kind"]} | {e["subcode"] or "—"} | '
                 f'{e["edition_date"]} | {e["bytes"]:,} | `{e["sha256"][:12]}` |')
    L.append('')

    # REE demand
    if ('REE', 'demand') in parsed:
        e, m = parsed[('REE', 'demand')]
        L.append('## REE transmission — DEMAND access capacity\n')
        L.append(f'Edition **{e["edition_date"]}** · source: {e["source_page"]}\n')
        L.append(f'- Nodes with a demand-capacity value: **{m["nodes"]}** '
                 f'(of which **{m["saturated_nodes"]}** saturated, 0 MW available)')
        L.append(f'- Total AVAILABLE (disponible, criterio general CEP/CH): '
                 f'**{m["available_mw"]:,} MW**')
        L.append(f'- Total GRANTED / occupied (otorgada demanda RdT): **{m["granted_mw"]:,} MW**')
        L.append(f'- Total PENDING (en curso, demanda RdT): **{m["pending_mw"]:,} MW**')
        L.append(f'- Total access margin (RdT demanda): {m["extra_mw"]:,} MW')
        L.append(f'- Nodes with ≥100 MW available: **{m["ge_nodes"]}** '
                 f'totalling {m["ge_available_mw"]:,} MW')
        L.append('')
        # drift vs app
        app = stats.get('es_transport', {})
        srcd = (srcs.get('es_transport') or [{}])[0].get('d', '?')
        L.append('**Drift vs app (FREE.ES `es_transport`)**\n')
        L.append(f'| Metric | App (data.js, ed. {srcd}) | Fresh snapshot (ed. {e["edition_date"]}) | Δ |')
        L.append('|---|--:|--:|--:|')
        an, amw = app.get('nodes', 0), app.get('total_mw', 0)
        L.append(f'| Nodes (app filter ≥100 MW located) | {an} | {m["ge_nodes"]} (≥100 MW) | '
                 f'{m["ge_nodes"] - an:+d} |')
        L.append(f'| Available MW | {amw:,} | {m["ge_available_mw"]:,} (≥100 MW) | '
                 f'{m["ge_available_mw"] - amw:+,} |')
        L.append('')
        L.append('> The app figure is a filtered (≥100 MW, geocoded), single-edition subset; '
                 'the snapshot is the full fresh file. Compare like-for-like on the ≥100 MW row; '
                 'the node-count drop is real saturation between editions.\n')

    # REE generation
    if ('REE', 'generation') in parsed:
        e, m = parsed[('REE', 'generation')]
        L.append('## REE transmission — GENERATION access capacity\n')
        L.append(f'Edition **{e["edition_date"]}** · source: {e["source_page"]}\n')
        L.append(f'- Nodes with a generation-capacity value: **{m["nodes"]}** '
                 f'({m["saturated_nodes"]} with 0 MGES margin)')
        L.append(f'- AVAILABLE (MGES renewable total access margin): **{m["available_mw"]:,} MW**')
        L.append(f'- GRANTED / occupied (otorgada GEN): **{m["granted_mw"]:,} MW**')
        L.append(f'- PENDING (en curso GEN): **{m["pending_mw"]:,} MW**')
        L.append(f'- Static generation nodal capacity (sum): {m["extra_mw"]:,} MW')
        L.append(f'- Nodes with ≥5 MW MGES margin: **{m["ge_nodes"]}** '
                 f'totalling {m["ge_available_mw"]:,} MW')
        L.append('')
        app = stats.get('es_ree_gen', {})
        srcd = (srcs.get('es_ree_gen') or [{}])[0].get('d', '?')
        L.append('**Drift vs app (FREE.ES `es_ree_gen`)**\n')
        L.append(f'| Metric | App (data.js) | Fresh snapshot (ed. {e["edition_date"]}) |')
        L.append('|---|--:|--:|')
        L.append(f'| Nodes | {app.get("nodes", 0)} | {m["nodes"]} |')
        L.append(f'| Available MW (MGES margin) | {app.get("total_mw", 0):,} | {m["available_mw"]:,} |')
        L.append('')
        L.append('> App `es_ree_gen.total_mw` was the MGES access-margin proxy for a prior '
                 'edition + ≥5 MW/geocoding filter; not reconcilable 1:1 with the full fresh sum.\n')

    # e-Distribucion (aggregate over R-codes)
    edis = {k: v for k, v in parsed.items() if k[0] == 'EDIS'}
    if edis:
        L.append('## e-Distribucion (DSO) — demand + generation\n')
        for kind in ('demand', 'generation'):
            entries = [(k, v) for k, v in edis.items() if k[1] == kind]
            if not entries:
                continue
            tn = sum(v[1]['nodes'] for _, v in entries)
            tav = sum(v[1]['available_mw'] for _, v in entries)
            toc = sum(v[1]['occupied_mw'] for _, v in entries)
            tad = sum(v[1]['admitted_pending_mw'] for _, v in entries)
            codes = ', '.join(sorted(k[2] for k, _ in entries))
            ed = entries[0][1][0]['edition_date']
            L.append(f'### {kind.title()} — edition {ed} (codes {codes})\n')
            L.append(f'- Nodes: **{tn}** across {len(entries)} DSO file(s)')
            L.append(f'- AVAILABLE firm capacity: **{tav:,.0f} MW**')
            L.append(f'- OCCUPIED: **{toc:,.0f} MW**')
            L.append(f'- Admitted-pending / not-evaluated: {tad:,.0f} MW')
            for k, (e2, mm) in sorted(entries):
                L.append(f'  - {k[2]}: {mm["nodes"]} nodes · avail {mm["available_mw"]:,.0f} · '
                         f'occ {mm["occupied_mw"]:,.0f} MW')
            L.append('')

    # sanity note
    L.append('## Data-quality / units notes\n')
    L.append('- All figures are MW as published (REE/e-Distribucion report MW directly; no '
             'unit conversion applied). Spanish-locale decimals (comma) parsed.')
    L.append('- REE demand "available" = *Capacidad de acceso disponible para solicitudes '
             'criterio general* (DEMANDA / CEP / CH) — the column the app maps to node `mw`; '
             'verified against CABRA 400 (827 MW).')
    L.append('- REE generation "available" = MGES (renewable) *Margen de capacidad de acceso '
             'total*; generation capacity is split across MGES/MPE/storage buckets that are '
             'not additive per node — MGES is reported as the headline renewable-hosting proxy.')
    L.append('- "Saturated" = 0 MW available at that node under the headline criterion.')
    L.append('- Raw files archived immutably under '
             f'`pipelines/.cache/es_snapshots/{manifest["snapshot_date"]}/` '
             '(see manifest.json for URL + sha256 + edition per file).')
    L.append('')

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    open(REPORT, 'w', encoding='utf-8').write('\n'.join(L))
    print(f'wrote {REPORT}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='re-download even if already archived')
    ap.add_argument('--summary-only', action='store_true', help='rebuild MD from newest snapshot')
    args = ap.parse_args()
    if not args.summary_only:
        archive(force=args.force)
    build_summary()


if __name__ == '__main__':
    main()
