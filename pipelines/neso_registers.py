#!/usr/bin/env python3
"""Ingest two anonymous NESO data-portal registers and emit a summary report.

  1. TEC Register  (dataset: transmission-entry-capacity-tec-register)
       Every transmission-connected (and embedded ≥ threshold) project's
       Transmission Entry Capacity, now Connections-Reform Gate-tagged.
  2. Historic Demand Data (dataset: historic-demand-data) — latest year CSV.
       Half-hourly national/transmission demand + embedded RES + interconnectors.

Both are pulled fresh from the NESO CKAN API (api.neso.energy, no auth),
cached under pipelines/.cache/neso/, and summarised into
pipelines/reports/neso_summary.md:
  * project counts by Gate / status / stage / host TO / agreement type
  * total TEC (MW) by plant type (raw combos AND split per technology)
  * demand-data coverage check (date range, periods/day, DST days, gaps)

Resource selection is DYNAMIC (queried from CKAN each run) so the script keeps
working when NESO rotates the twice-weekly TEC file or publishes a new year.

QUIRKS handled:
  * TEC 'Plant Type' is a ';'-delimited multi-technology field (60 combos) —
    we report both the raw combo tally and a per-technology MW split.
  * GB half-hourly days are NOT always 48 periods: the spring clock-change day
    has 46 and the autumn day has 50. The coverage check treats {46,48,50} as
    valid and only flags days outside that set, plus any missing calendar dates.
"""
import argparse
import csv
import datetime as dt
import os
import re
import sys
from collections import Counter, defaultdict

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "pipelines", ".cache", "neso")
REPORT = os.path.join(ROOT, "pipelines", "reports", "neso_summary.md")

UA = "Mozilla/5.0 (OpportunityMap/neura data pipeline; +anonymous NESO ingest)"
CKAN = "https://api.neso.energy/api/3/action/package_show?id={}"
TEC_DS = "transmission-entry-capacity-tec-register"
DEMAND_DS = "historic-demand-data"


def log(m):
    print(m, file=sys.stderr)


def ckan_resources(dataset):
    r = requests.get(CKAN.format(dataset), headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"CKAN package_show failed for {dataset}")
    return d["result"]


def download(url, dest, refresh=False):
    if os.path.exists(dest) and not refresh:
        log(f"cached  {os.path.basename(dest)} ({os.path.getsize(dest)} bytes)")
        return dest
    log(f"GET     {url}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(r.content)
    log(f"saved   {dest} ({len(r.content)} bytes)")
    return dest


def _num(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _parse_date(s):
    """Historic demand dates come in several formats across years."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
def fetch_tec(refresh):
    res = ckan_resources(TEC_DS)
    csvs = [r for r in res["resources"] if (r.get("format") or "").upper() == "CSV"]
    if not csvs:
        raise RuntimeError("no CSV resource in TEC dataset")
    r = csvs[0]  # single TEC Register CSV
    dest = os.path.join(CACHE, "tec_register.csv")
    download(r["url"], dest, refresh)
    return dest, {"title": res.get("title"), "licence": res.get("license_title"),
                  "resource_name": r.get("name"), "url": r["url"],
                  "last_modified": r.get("last_modified") or r.get("created")}


def fetch_demand(refresh):
    res = ckan_resources(DEMAND_DS)
    best, best_year = None, -1
    for r in res["resources"]:
        if (r.get("format") or "").upper() != "CSV":
            continue
        # year lives in the download filename (demanddata_2025.csv /
        # demanddataupdate_2026.csv) — NOT the resource UUID, which can itself
        # contain a spurious '20xx' substring.
        fname = os.path.basename(r.get("url", ""))
        m = re.search(r"_(\d{4})\.csv$", fname) or re.search(r"(\d{4})", fname)
        if m and int(m.group(1)) > best_year:
            best_year, best = int(m.group(1)), r
    if not best:
        raise RuntimeError("no year-tagged CSV in demand dataset")
    dest = os.path.join(CACHE, f"demanddata_{best_year}.csv")
    download(best["url"], dest, refresh)
    return dest, best_year, {"title": res.get("title"), "licence": res.get("license_title"),
                             "resource_name": best.get("name"), "url": best["url"],
                             "last_modified": best.get("last_modified") or best.get("created")}


# --------------------------------------------------------------------------- #
def summarise_tec(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    n = len(rows)
    cats = {c: Counter() for c in ["Gate", "Project Status", "Stage",
                                   "HOST TO", "Agreement Type"]}
    for r in rows:
        for c in cats:
            cats[c][(r.get(c) or "").strip() or "(untagged)"] += 1

    # TEC MW: 'MW Increase / Decrease' is the per-project entry-capacity delta.
    mw_combo = defaultdict(float)   # raw ';'-joined plant-type string
    mw_tech = defaultdict(float)    # split per technology
    mw_total = 0.0
    mw_gate = defaultdict(float)
    for r in rows:
        mw = _num(r.get("MW Increase / Decrease")) or 0.0
        mw_total += mw
        combo = (r.get("Plant Type") or "(unspecified)").strip() or "(unspecified)"
        mw_combo[combo] += mw
        techs = [t.strip() for t in combo.split(";") if t.strip()] or ["(unspecified)"]
        share = mw / len(techs)
        for t in techs:
            mw_tech[t] += share
        gate = (r.get("Gate") or "").strip() or "(untagged)"
        mw_gate[gate] += mw
    return {"n": n, "cats": cats, "mw_total": mw_total, "mw_combo": mw_combo,
            "mw_tech": mw_tech, "mw_gate": mw_gate}


def summarise_demand(path, year):
    rows = list(csv.DictReader(open(path)))
    n = len(rows)
    per_day = defaultdict(int)
    nd_vals = []
    for r in rows:
        per_day[r.get("SETTLEMENT_DATE")] += 1
        v = _num(r.get("ND"))
        if v is not None:
            nd_vals.append(v)
    # normalise dates (multiple source formats) to ISO for sorting/gap checks
    per_day_iso = defaultdict(int)
    unparsed = 0
    for raw, c in per_day.items():
        d = _parse_date(raw)
        if d is None:
            unparsed += 1
            continue
        per_day_iso[d.isoformat()] += c
    dates = sorted(per_day_iso)
    dist = Counter(per_day_iso.values())
    bad_days = {d: c for d, c in per_day_iso.items() if c not in (46, 48, 50)}

    # calendar-gap check between min and max date present
    missing = []
    if dates:
        d0 = dt.date.fromisoformat(dates[0])
        d1 = dt.date.fromisoformat(dates[-1])
        present = set(dates)
        cur = d0
        while cur <= d1:
            if cur.isoformat() not in present:
                missing.append(cur.isoformat())
            cur += dt.timedelta(days=1)

    span_days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days + 1 if dates else 0
    return {"n": n, "year": year, "dates": len(dates), "unparsed_dates": unparsed,
            "date_min": dates[0] if dates else None,
            "date_max": dates[-1] if dates else None,
            "span_days": span_days, "periods_dist": dict(dist),
            "bad_days": bad_days, "missing_days": missing,
            "nd_min": min(nd_vals) if nd_vals else None,
            "nd_max": max(nd_vals) if nd_vals else None,
            "nd_mean": (sum(nd_vals) / len(nd_vals)) if nd_vals else None}


# --------------------------------------------------------------------------- #
def write_report(tec, tec_meta, dem, dem_meta):
    def tbl(counter, key="count"):
        return "\n".join(f"| {k} | {v:,} |" for k, v in
                         sorted(counter.items(), key=lambda kv: -kv[1]))

    lines = []
    A = lines.append
    A("# NESO registers — ingest summary\n")
    A(f"_Generated {dt.datetime.utcnow().isoformat()}Z from anonymous NESO CKAN "
      f"(api.neso.energy). See PROVENANCE.md for URLs/licences._\n")

    # ---- TEC ----
    A("## 1. TEC Register\n")
    A(f"- Source: **{tec_meta['title']}** — {tec_meta['resource_name']}")
    A(f"- Licence: {tec_meta['licence']}")
    A(f"- Resource last modified: {tec_meta['last_modified']}")
    A(f"- **Projects: {tec['n']:,}**  ·  **Total TEC (MW Increase/Decrease): "
      f"{tec['mw_total']:,.1f} MW**\n")

    A("### Projects by Connections-Reform Gate\n")
    A("| Gate | Projects |\n|---|---|")
    A(tbl(tec["cats"]["Gate"]))
    A("\n### TEC (MW) by Gate\n")
    A("| Gate | MW |\n|---|---|")
    A("\n".join(f"| {k} | {v:,.0f} |" for k, v in
                sorted(tec["mw_gate"].items(), key=lambda kv: -kv[1])))

    for label, col in [("Project Status", "Project Status"),
                       ("Connections-Reform Stage", "Stage"),
                       ("Host TO", "HOST TO"),
                       ("Agreement Type", "Agreement Type")]:
        A(f"\n### Projects by {label}\n")
        A(f"| {label} | Projects |\n|---|---|")
        A(tbl(tec["cats"][col]))

    A("\n### TEC (MW) by technology (Plant Type split on ';')\n")
    A("| Technology | MW | (share of total) |\n|---|---|---|")
    for k, v in sorted(tec["mw_tech"].items(), key=lambda kv: -kv[1]):
        A(f"| {k} | {v:,.0f} | {100*v/tec['mw_total']:.1f}% |")

    A("\n<details><summary>TEC (MW) by raw Plant Type combo (top 20)</summary>\n")
    A("\n| Plant Type combo | MW |\n|---|---|")
    for k, v in sorted(tec["mw_combo"].items(), key=lambda kv: -kv[1])[:20]:
        A(f"| {k} | {v:,.0f} |")
    A("\n</details>\n")

    # ---- Demand ----
    A("## 2. Historic Demand Data — coverage check\n")
    A(f"- Source: **{dem_meta['title']}** — {dem_meta['resource_name']} "
      f"(latest year = **{dem['year']}**)")
    A(f"- Licence: {dem_meta['licence']}")
    A(f"- Resource last modified: {dem_meta['last_modified']}")
    A(f"- Rows (half-hourly settlement periods): **{dem['n']:,}**")
    A(f"- Date range: **{dem['date_min']} → {dem['date_max']}** "
      f"({dem['dates']:,} distinct days over a {dem['span_days']}-day span)")
    A(f"- Periods-per-day distribution: `{dem['periods_dist']}` "
      f"(48 = normal; 46 = spring clock-change day; 50 = autumn — all valid)")
    complete = (not dem["missing_days"] and not dem["bad_days"])
    A(f"- Calendar gaps (missing days): **{len(dem['missing_days'])}**"
      + (f" → {dem['missing_days'][:10]}" if dem["missing_days"] else ""))
    A(f"- Days with an unexpected period count (not 46/48/50): **{len(dem['bad_days'])}**"
      + (f" → {dict(list(dem['bad_days'].items())[:10])}" if dem["bad_days"] else ""))
    if dem["nd_min"] is not None:
        A(f"- National Demand (ND, MW): min {dem['nd_min']:,.0f} · "
          f"mean {dem['nd_mean']:,.0f} · max {dem['nd_max']:,.0f}")
    A(f"- **Coverage verdict: {'CONTIGUOUS, no gaps' if complete else 'GAPS DETECTED (see above)'}"
      f"** for the {dem['year']} file"
      + (" — note this is the current, year-to-date file." if dem["date_max"] and
         dem["date_max"] < f"{dem['year']}-12-31" else "."))
    A("")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    open(REPORT, "w").write("\n".join(lines))
    log(f"wrote {REPORT}")


def main():
    ap = argparse.ArgumentParser(description="Ingest NESO TEC + Historic Demand registers.")
    ap.add_argument("--refresh", action="store_true", help="re-download CSVs")
    args = ap.parse_args()

    tec_path, tec_meta = fetch_tec(args.refresh)
    dem_path, dem_year, dem_meta = fetch_demand(args.refresh)

    log("summarising TEC ...")
    tec = summarise_tec(tec_path)
    log("summarising demand ...")
    dem = summarise_demand(dem_path, dem_year)
    write_report(tec, tec_meta, dem, dem_meta)

    log("\n=== NESO registers ===")
    log(f"TEC projects {tec['n']:,}  total {tec['mw_total']:,.0f} MW  "
        f"Gate: {dict(tec['cats']['Gate'])}")
    log(f"Demand {dem['year']}: {dem['n']:,} rows, {dem['date_min']}..{dem['date_max']}, "
        f"periods/day {dem['periods_dist']}, missing {len(dem['missing_days'])}")


if __name__ == "__main__":
    main()
