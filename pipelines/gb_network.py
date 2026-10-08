#!/usr/bin/env python3
"""Phase 2.1 — GB transmission network model from NESO ETYS 2025 Appendix B (+ G).

Builds a bus/branch graph of the GB transmission system (400/275/132 kV, plus a
handful of lower-voltage station buses) from the *anonymous* ETYS workbooks, and
attaches FES nodal demand from Appendix G. Output feeds the DC power-flow engine
(Phase 2.2/2.3) and the node-matching step (`gb_node_match.py`).

  Output: data/networks/gb_etys.json
    { meta, buses[], branches[], branch_changes[], nodal_demand[] }

--------------------------------------------------------------------------------
SOURCES (all anonymous, no auth — see pipelines/.cache/neso/PROVENANCE.md)
  Appendix B  https://www.neso.energy/document/383936/download
              "ETYS 2025 Appendix-B Yr01Yr02.xlsx" — supplementary technical data.
  Appendix G  https://www.neso.energy/document/379136/download
              "Public_ETYS25 Appendix G.xlsx" — FES user (nodal) demand.

--------------------------------------------------------------------------------
WORKBOOK QUIRKS INSPECTED (do NOT assume — these were verified against the file)

* Sheet numbering is NOT what older ETYS editions / the WP brief use. In the 2025
  workbook the actual tables are:
      B-1-1{a,b,c}  Index of Substation Codes      (a=SHET, b=SPT, c=NGET)
      B-2-1{a,b,c}  Circuits 2025/26               <- primary circuit data
      B-2-2{a,b,c}  Circuit Changes 2026/27        <- future-year deltas
      B-3-1{a,b,c}  Transformers 2025/26           <- primary transformer data
      B-3-2{a,b,c}  Transformer Changes 2026/27
      B-4-*         Reactive Compensation          (not used here)
  Suffix -> Transmission Owner:  a = SHET (SHE Transmission), b = SPT, c = NGET.

* NODE NAMING: every transmission node token is EXACTLY 6 chars:
      [0:4] = 4-char site code (joins to the B-1-1 index)
      [4]   = voltage-level digit
      [5]   = busbar / circuit identifier ('-' where unused)
  Voltage-digit map, derived empirically from single-voltage sites in the index
  (1730 tokens checked, only 1 disagreed with its site's index voltages):
      1->132  2->275  3->33  4->400  5->11  6->66  7->25   (kV)

* Data rows start at row index 2 (0-based); row 0 = title, row 1 = header.
  Blank rows inside circuit/transformer sheets are fully-empty separators.

* Circuits carry 4 seasonal ratings (Winter/Spring/Summer/Autumn MVA); we keep
  Winter (peak/limiting) and Summer (min) per the output schema. Transformers
  carry a single Rating (MVA) — we set winter==summer to it.

* R/X/B are "% on 100 MVA" (i.e. per-unit*100 on a 100 MVA base). We store the
  raw workbook value (percent on 100 MVA) unchanged and flag the base in `meta`
  so the PF engine converts deterministically; B is line charging susceptance and
  is NEGATIVE for transformers (magnetising) — that is expected, not an error.

* APPENDIX G: col 0 = node token, cols 2..9 = MW for 8 forward years. The header
  row labels years 25/26..31/32 but the LAST label ("31/32") is a DUPLICATE typo;
  totals increase monotonically so it is really 2032/33. We therefore GENERATE the
  year labels 2025/26..2032/33 from the first year rather than trust the header.
  Some G node tokens are longer than 6 chars: "<node>_<DNOgroup>" (e.g.
  AMEM4A_EPN) — the node's demand split by downstream GSP-group. We aggregate
  (sum) these back to the 6-char base bus per year, and count how many were split.

Anything unexpected is logged to stderr and summarised in meta['anomalies'];
nothing is silently dropped.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import openpyxl
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "pipelines", ".cache", "neso")
OUT = os.path.join(ROOT, "data", "networks", "gb_etys.json")

APPB_URL = "https://www.neso.energy/document/383936/download"
APPG_URL = "https://www.neso.energy/document/379136/download"
APPB_FILE = os.path.join(CACHE, "etys_appendix_b.xlsx")
APPG_FILE = os.path.join(CACHE, "etys_appendix_g.xlsx")

UA = "Mozilla/5.0 (OpportunityMap/neura data pipeline; +anonymous ETYS ingest)"

# Transmission Owner per sheet suffix.
TO_BY_SUFFIX = {"a": "SHET", "b": "SPT", "c": "NGET"}
# Voltage-level digit (5th char of node token) -> kV. See module docstring.
VOLT_DIGIT = {"1": 132, "2": 275, "3": 33, "4": 400, "5": 11, "6": 66, "7": 25}

INDEX_SHEETS = {"a": "B-1-1a", "b": "B-1-1b", "c": "B-1-1c"}
CIRCUIT_SHEETS = {"a": "B-2-1a", "b": "B-2-1b", "c": "B-2-1c"}
CIRCUIT_CHANGE_SHEETS = {"a": "B-2-2a", "b": "B-2-2b", "c": "B-2-2c"}
TRAFO_SHEETS = {"a": "B-3-1a", "b": "B-3-1b", "c": "B-3-1c"}
TRAFO_CHANGE_SHEETS = {"a": "B-3-2a", "b": "B-3-2b", "c": "B-3-2c"}
GEN_SHEET = "demand data 2025"  # Appendix G

ANOMALIES = []


def log(msg):
    print(msg, file=sys.stderr)


def note(msg):
    ANOMALIES.append(msg)
    log("  [anomaly] " + msg)


def download(url, dest, refresh=False):
    if os.path.exists(dest) and not refresh:
        log(f"cached  {os.path.basename(dest)} ({os.path.getsize(dest)} bytes)")
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    log(f"GET     {url}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(r.content)
    log(f"saved   {dest} ({len(r.content)} bytes)  [{r.headers.get('content-disposition','')}]")
    return dest


def _num(v):
    """Best-effort float; None/'' -> None; non-numeric -> None (caller logs)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def voltage_of(token):
    if len(token) < 5:
        return None
    return VOLT_DIGIT.get(token[4])


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def parse_index(wb):
    """site code -> {name, volts:set, to}. TO = the sheet's Transmission Owner."""
    sites = {}
    for suf, sheet in INDEX_SHEETS.items():
        to = TO_BY_SUFFIX[suf]
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 2 or not row or row[0] is None or str(row[0]).strip() == "":
                continue
            code = str(row[0]).strip()
            name = str(row[1]).strip() if row[1] else code
            kv = _num(row[2])
            rec = sites.setdefault(code, {"name": name, "volts": set(), "to": to})
            if kv is not None:
                rec["volts"].add(int(kv))
            if rec["to"] != to:
                note(f"site {code} appears under both {rec['to']} and {to}")
    return sites


def parse_circuits(wb, sheets, seen_buses):
    """Return list of branch dicts from a circuit-sheet group."""
    out = []
    for suf, sheet in sheets.items():
        tso = TO_BY_SUFFIX[suf]
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 2 or not row:
                continue
            n1 = str(row[0]).strip() if row[0] is not None else ""
            n2 = str(row[1]).strip() if row[1] is not None else ""
            if not n1 or not n2:  # fully-empty separator row
                continue
            # cols: 0 N1,1 N2,2 OHLkm,3 Cablekm,4 Type,5 R,6 X,7 B,
            #       8 Winter,9 Spring,10 Summer,11 Autumn
            r, x, b = _num(row[5]), _num(row[6]), _num(row[7])
            if r is None or x is None:
                note(f"{sheet} row{i}: non-numeric R/X for {n1}-{n2}; keeping with nulls")
            seen_buses.add(n1)
            seen_buses.add(n2)
            out.append({
                "from": n1, "to": n2,
                "type": str(row[4]).strip() if row[4] else "OHL",
                "r": r, "x": x, "b": b,
                "rating_mva_winter": _num(row[8]),
                "rating_mva_summer": _num(row[10]),
                "ohl_km": _num(row[2]), "cable_km": _num(row[3]),
                "tso": tso,
            })
    return out


def parse_transformers(wb, sheets, seen_buses):
    out = []
    for suf, sheet in sheets.items():
        tso = TO_BY_SUFFIX[suf]
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 2 or not row:
                continue
            n1 = str(row[0]).strip() if row[0] is not None else ""
            n2 = str(row[1]).strip() if row[1] is not None else ""
            if not n1 or not n2:
                continue
            # cols: 0 N1,1 N2,2 R,3 X,4 B,5 Rating
            r, x, b = _num(row[2]), _num(row[3]), _num(row[4])
            rating = _num(row[5])
            seen_buses.add(n1)
            seen_buses.add(n2)
            out.append({
                "from": n1, "to": n2,
                "type": "transformer",
                "r": r, "x": x, "b": b,
                "rating_mva_winter": rating,
                "rating_mva_summer": rating,
                "tso": tso,
            })
    return out


def parse_changes(wb, sheets, kind):
    """Future-year additions/removals. Kept separate from the base graph."""
    out = []
    for suf, sheet in sheets.items():
        tso = TO_BY_SUFFIX[suf]
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 2 or not row:
                continue
            n1 = str(row[0]).strip() if row[0] is not None else ""
            n2 = str(row[1]).strip() if row[1] is not None else ""
            if not n1 or not n2:
                continue
            rec = {"from": n1, "to": n2, "kind": kind, "tso": tso,
                   "year": str(row[2]).strip() if row[2] is not None else None,
                   "status": str(row[3]).strip() if row[3] is not None else None}
            if kind == "circuit":
                rec.update({"type": str(row[6]).strip() if row[6] else None,
                            "r": _num(row[7]), "x": _num(row[8]), "b": _num(row[9]),
                            "rating_mva_winter": _num(row[10]),
                            "rating_mva_summer": _num(row[12])})
            else:  # transformer
                rec.update({"type": "transformer", "r": _num(row[4]),
                            "x": _num(row[5]), "b": _num(row[6]),
                            "rating_mva_winter": _num(row[7]),
                            "rating_mva_summer": _num(row[7])})
            out.append(rec)
    return out


def parse_appendix_g(wbg, seen_buses):
    """FES nodal demand -> list[{bus, year, mw}] aggregated to base 6-char bus."""
    ws = wbg[GEN_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    # header row 6 has year labels; the last is a known duplicate typo, so we
    # generate labels from the first fiscal year (2025/26) instead.
    n_year_cols = 8  # cols index 2..9
    base_start = 2025
    years = [f"{base_start + k}/{str(base_start + k + 1)[-2:]}" for k in range(n_year_cols)]

    # aggregate MW by (base bus token, year)
    agg = defaultdict(float)
    split_nodes = set()
    raw_rows = 0
    for row in rows[8:]:
        if not row or row[0] is None or str(row[0]).strip() == "":
            continue
        tok = str(row[0]).strip()
        raw_rows += 1
        base = tok.split("_")[0]
        if "_" in tok:
            split_nodes.add(base)
        for k in range(n_year_cols):
            mw = _num(row[2 + k])
            if mw is None:
                continue
            agg[(base, years[k])] += mw
        seen_buses.add(base)

    out = [{"bus": bus, "year": yr, "mw": round(mw, 3)}
           for (bus, yr), mw in sorted(agg.items())]
    log(f"App G: {raw_rows} raw node rows -> {len({b for b,_ in agg})} base buses "
        f"({len(split_nodes)} split by GSP-group) x {n_year_cols} years")
    return out, years


def build_buses(seen_buses, sites):
    buses = []
    unknown_site = 0
    unknown_volt = 0
    for tok in sorted(seen_buses):
        code = tok[:4]
        site = sites.get(code)
        kv = voltage_of(tok)
        if site is None:
            unknown_site += 1
            name, to = code, None
        else:
            name, to = site["name"], site["to"]
            if kv is not None and site["volts"] and kv not in site["volts"]:
                note(f"bus {tok}: derived {kv}kV not in site index {sorted(site['volts'])}")
        if kv is None:
            unknown_volt += 1
            note(f"bus {tok}: unknown voltage digit '{tok[4] if len(tok) > 4 else ''}'")
        buses.append({"id": tok, "name": name, "voltage_kv": kv, "to_region": to})
    if unknown_site:
        note(f"{unknown_site} bus tokens had no site-index entry")
    if unknown_volt:
        note(f"{unknown_volt} bus tokens had an unmapped voltage digit")
    return buses


def main():
    ap = argparse.ArgumentParser(description="Build GB ETYS network model JSON.")
    ap.add_argument("--refresh", action="store_true", help="re-download workbooks")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    download(APPB_URL, APPB_FILE, args.refresh)
    download(APPG_URL, APPG_FILE, args.refresh)

    log("parsing Appendix B ...")
    wb = openpyxl.load_workbook(APPB_FILE, read_only=True, data_only=True)
    sites = parse_index(wb)
    seen = set()
    circuits = parse_circuits(wb, CIRCUIT_SHEETS, seen)
    trafos = parse_transformers(wb, TRAFO_SHEETS, seen)
    branches = circuits + trafos
    changes = (parse_changes(wb, CIRCUIT_CHANGE_SHEETS, "circuit")
               + parse_changes(wb, TRAFO_CHANGE_SHEETS, "transformer"))

    log("parsing Appendix G ...")
    wbg = openpyxl.load_workbook(APPG_FILE, read_only=True, data_only=True)
    nodal_demand, years = parse_appendix_g(wbg, seen)

    buses = build_buses(seen, sites)

    # per-TO tallies for the report
    def by_tso(items):
        c = defaultdict(int)
        for it in items:
            c[it["tso"]] += 1
        return dict(c)

    bus_by_to = defaultdict(int)
    for bu in buses:
        bus_by_to[bu["to_region"] or "UNKNOWN"] += 1

    demand_buses = {d["bus"] for d in nodal_demand}
    branch_endpoints = {b["from"] for b in branches} | {b["to"] for b in branches}
    # Exact-token match is often missed because App G tags demand to a specific
    # busbar id (e.g. ABTH20) while circuits use another at the same site+voltage
    # (ABTH21). The honest coverage is at the (site code, voltage digit) level.
    endpoint_siteV = {t[:5] for t in branch_endpoints if len(t) >= 5}
    resolved_exact = len(demand_buses & branch_endpoints)
    resolved_sitev = len({t for t in demand_buses if len(t) >= 5 and t[:5] in endpoint_siteV})

    out = {
        "meta": {
            "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "sources": {
                "appendix_b": {"url": APPB_URL, "file": os.path.basename(APPB_FILE),
                               "edition": "ETYS 2025", "base_year": "2025/26"},
                "appendix_g": {"url": APPG_URL, "file": os.path.basename(APPG_FILE),
                               "edition": "ETYS 2025", "years": years},
            },
            "conventions": {
                "impedance_base": "% on 100 MVA (per-unit*100, 100 MVA base)",
                "node_token": "chars[0:4]=site code, [4]=voltage digit, [5]=busbar id",
                "voltage_digit_kv": VOLT_DIGIT,
                "to_by_suffix": TO_BY_SUFFIX,
                "b_sign": "transformer B is negative (magnetising) — expected",
                "nodal_demand": "Appendix G MW aggregated to 6-char bus, summed over GSP-group splits",
            },
            "counts": {
                "buses": len(buses), "buses_by_to": dict(bus_by_to),
                "branches": len(branches),
                "circuits": len(circuits), "circuits_by_tso": by_tso(circuits),
                "transformers": len(trafos), "transformers_by_tso": by_tso(trafos),
                "branch_changes": len(changes),
                "nodal_demand_rows": len(nodal_demand),
                "nodal_demand_buses": len(demand_buses),
                "nodal_demand_buses_exact_branch_endpoint": resolved_exact,
                "nodal_demand_buses_matched_site_voltage": resolved_sitev,
            },
            "anomalies": ANOMALIES,
        },
        "buses": buses,
        "branches": branches,
        "branch_changes": changes,
        "nodal_demand": nodal_demand,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)

    log("\n=== gb_etys.json ===")
    log(f"buses      {len(buses)}  by TO: {dict(bus_by_to)}")
    log(f"circuits   {len(circuits)}  by TSO: {by_tso(circuits)}")
    log(f"transformers {len(trafos)}  by TSO: {by_tso(trafos)}")
    log(f"branches   {len(branches)} (base year)  + {len(changes)} future changes")
    log(f"nodal_demand {len(nodal_demand)} rows over {len(demand_buses)} buses "
        f"({resolved_exact} exact / {resolved_sitev} at site+voltage level "
        f"map to a branch endpoint)")
    log(f"anomalies  {len(ANOMALIES)}")
    log(f"wrote {args.out}")


if __name__ == "__main__":
    main()
