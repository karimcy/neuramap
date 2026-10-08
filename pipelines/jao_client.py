#!/usr/bin/env python3
"""WP-JAO — client for the JAO publication tools (Core + Nordic flow-based domains).

The JAO publication tool exposes hourly, per-CNEC flow-based capacity data with NO
authentication: operator zone-to-slack PTDFs, element ratings (Fmax/Imax), reliability
margin (FRM), and remaining available margin (RAM = the TSO-computed transmission
headroom) — plus contingency ("CNEC") definitions and substation names. This is the
calibration ground truth for our DC power-flow engine (POWERFLOW_PLAN §2.3b).

Regions / base URLs (open, no key):
  core   https://publicationtool.jao.eu/core/api/data/{endpoint}     DE/FR/PL/AT/BE/NL/CZ/... (since Jun 2022)
  nordic https://publicationtool.jao.eu/nordic/api/data/{endpoint}   FI/SE/NO/DK             (since Oct 2024)

Endpoints used here (both regions expose the same names):
  finalComputation   per-CNEC final flow-based domain (PTDFs, RAM, Fmax, Imax, FRM, ...)
  maxNetPos          min/max net position per bidding zone / hub, one row per hour

The API returns EVERY row for the requested [FromUtc, ToUtc) window in a single
response (no server-side paging), so a Core finalComputation hour is ~12.8k rows /
~22 MB. We therefore fetch in hourly chunks, throttle between live calls, retry with
backoff, and cache each raw chunk immutably under pipelines/.cache/jao/ keyed by a
hash of (region, endpoint, from, to). Re-runs are served from cache — byte-identical,
never re-fetched.

Usage:
  # Fetch a window (hourly chunks), print a one-line summary:
  python3 pipelines/jao_client.py fetch --region core   --endpoint finalComputation \
      --from 2025-01-15T00:00:00Z --to 2025-01-15T03:00:00Z

  # Inspect the schema of the first cached/fetched record:
  python3 pipelines/jao_client.py schema --region nordic --endpoint finalComputation \
      --from 2025-01-15T00:00:00Z --to 2025-01-15T01:00:00Z

  # Sample a few hours from BOTH regions & both endpoints, print schema report:
  python3 pipelines/jao_client.py sample
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "pipelines", ".cache", "jao")
MANIFEST = os.path.join(CACHE, "_manifest.json")

REGIONS = {
    "core": "https://publicationtool.jao.eu/core/api/data",
    "nordic": "https://publicationtool.jao.eu/nordic/api/data",
}

# --- polite client behaviour -------------------------------------------------
MIN_INTERVAL_S = 2.0      # min wall-clock gap between LIVE calls (cache hits are free)
MAX_RETRIES = 5
BACKOFF_BASE_S = 3.0      # retry sleep = BACKOFF_BASE_S * 2**attempt (+ Retry-After)
TIMEOUT_S = 180
USER_AGENT = "NeuraEnergy-OpportunityMap/WP-JAO (research; contact karimcy@gmail.com)"

_last_call_ts = 0.0       # module-level throttle state


# --- time helpers ------------------------------------------------------------
def parse_utc(s):
    """Parse an ISO-8601 instant to a tz-aware UTC datetime. Accepts trailing 'Z'."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_utc(dt):
    """JAO wire format: YYYY-MM-DDTHH:MM:SSZ."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- cache / provenance ------------------------------------------------------
def _window_hash(region, endpoint, from_utc, to_utc):
    key = f"{region}|{endpoint}|{fmt_utc(from_utc)}|{fmt_utc(to_utc)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _cache_path(region, endpoint, from_utc, to_utc):
    h = _window_hash(region, endpoint, from_utc, to_utc)
    return os.path.join(CACHE, f"{region}_{endpoint}_{h}.json")


def _load_manifest():
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST))
        except (ValueError, OSError):
            return {}
    return {}


def _record_manifest(entry):
    os.makedirs(CACHE, exist_ok=True)
    man = _load_manifest()
    man[entry["hash"]] = entry
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST)


# --- HTTP with throttle + retry ---------------------------------------------
def _throttle():
    global _last_call_ts
    wait = MIN_INTERVAL_S - (time.time() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.time()


def _get(url, params):
    """GET with rate-limit + exponential backoff. Returns parsed JSON dict."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            r = requests.get(
                url, params=params,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as e:
            last_err = e
            sleep = BACKOFF_BASE_S * (2 ** attempt)
            print(f"    ! network error ({e}); retry in {sleep:.0f}s", file=sys.stderr)
            time.sleep(sleep)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            retry_after = float(r.headers.get("Retry-After", 0) or 0)
            sleep = max(retry_after, BACKOFF_BASE_S * (2 ** attempt))
            last_err = f"HTTP {r.status_code}"
            print(f"    ! {last_err} from {url}; retry in {sleep:.0f}s", file=sys.stderr)
            time.sleep(sleep)
            continue
        # non-retryable
        raise RuntimeError(f"HTTP {r.status_code} from {url}: {r.text[:300]}")
    raise RuntimeError(f"gave up after {MAX_RETRIES} retries ({last_err}) for {url}")


# --- core fetch primitives ---------------------------------------------------
def fetch_chunk(region, endpoint, from_utc, to_utc, use_cache=True):
    """Fetch ONE [from,to) window in a single API call. Immutable cache by hash.

    Returns the raw JSON envelope dict exactly as JAO returned it (keys include
    'data' plus, for some endpoints, 'totalRows'/'skip'/'take'/'lastModifiedOn').
    """
    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}; expected one of {list(REGIONS)}")
    path = _cache_path(region, endpoint, from_utc, to_utc)
    if use_cache and os.path.exists(path):
        return json.load(open(path))

    url = f"{REGIONS[region]}/{endpoint}"
    params = {"FromUtc": fmt_utc(from_utc), "ToUtc": fmt_utc(to_utc)}
    envelope = _get(url, params)

    os.makedirs(CACHE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(envelope, f)          # store raw, immutable
    os.replace(tmp, path)

    rows = len(envelope.get("data", []) or [])
    _record_manifest({
        "hash": _window_hash(region, endpoint, from_utc, to_utc),
        "region": region, "endpoint": endpoint,
        "from_utc": fmt_utc(from_utc), "to_utc": fmt_utc(to_utc),
        "url": url, "params": params,
        "rows": rows,
        "bytes": os.path.getsize(path),
        "fetched_at": fmt_utc(datetime.now(timezone.utc)),
        "cache_file": os.path.basename(path),
    })
    return envelope


def fetch_window(region, endpoint, from_utc, to_utc, chunk_hours=1, use_cache=True):
    """Fetch [from,to) in `chunk_hours` steps; return the concatenated data records.

    Chunking keeps individual responses small (a Core finalComputation hour is
    ~22 MB) and gives an immutable per-chunk cache. Accepts str or datetime bounds.
    """
    if isinstance(from_utc, str):
        from_utc = parse_utc(from_utc)
    if isinstance(to_utc, str):
        to_utc = parse_utc(to_utc)
    step = timedelta(hours=chunk_hours)
    records, cur = [], from_utc
    while cur < to_utc:
        nxt = min(cur + step, to_utc)
        env = fetch_chunk(region, endpoint, cur, nxt, use_cache=use_cache)
        records.extend(env.get("data", []) or [])
        cur = nxt
    return records


# --- schema inspection helpers ----------------------------------------------
def summarize_record(rec):
    ptdf = sorted(k for k in rec if k.startswith("ptdf") or k.startswith("max") or k.startswith("min"))
    other = sorted(k for k in rec if k not in ptdf)
    return {"n_keys": len(rec), "hub_or_zone_cols": ptdf, "other_cols": other}


def _print_schema(region, endpoint, records):
    if not records:
        print(f"  {region}/{endpoint}: 0 records")
        return
    rec = records[0]
    s = summarize_record(rec)
    print(f"  {region}/{endpoint}: {len(records)} records, {s['n_keys']} keys/record")
    ptdf_cols = [k for k in rec if k.startswith("ptdf")]
    if ptdf_cols:
        print(f"    PTDF hub columns ({len(ptdf_cols)}): {', '.join(sorted(ptdf_cols))}")
    # spotlight the calibration-critical fields when present
    for f in ("cneName", "substationFrom", "substationTo", "tso", "u",
              "fmax", "imax", "frm", "ram", "elementType", "hubFrom", "hubTo",
              "biddingZoneFrom", "biddingZoneTo", "contName"):
        if f in rec:
            print(f"    {f:16s}= {rec[f]}")


# --- CLI ---------------------------------------------------------------------
def _cmd_fetch(a):
    recs = fetch_window(a.region, a.endpoint, a.__dict__["from"], a.to,
                        chunk_hours=a.chunk_hours, use_cache=not a.no_cache)
    print(f"{a.region}/{a.endpoint} {a.__dict__['from']}..{a.to}: "
          f"{len(recs)} records across cached chunk(s) in {CACHE}")


def _cmd_schema(a):
    recs = fetch_window(a.region, a.endpoint, a.__dict__["from"], a.to,
                        chunk_hours=a.chunk_hours, use_cache=not a.no_cache)
    _print_schema(a.region, a.endpoint, recs)


def _cmd_sample(a):
    """Fetch a few sample hours from BOTH regions & both endpoints; print schemas."""
    frm = a.__dict__["from"]
    hours = a.hours
    start = parse_utc(frm)
    to = start + timedelta(hours=hours)
    print(f"Sampling {hours}h from {fmt_utc(start)} across both regions\n")
    plan = [
        ("core", "finalComputation"),
        ("core", "maxNetPos"),
        ("nordic", "finalComputation"),
        ("nordic", "maxNetPos"),
    ]
    for region, endpoint in plan:
        try:
            recs = fetch_window(region, endpoint, start, to, chunk_hours=1)
            _print_schema(region, endpoint, recs)
        except Exception as e:
            print(f"  {region}/{endpoint}: ERROR {e}")
        print()


def build_parser():
    ap = argparse.ArgumentParser(description="JAO publication-tool client (Core + Nordic)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--region", choices=list(REGIONS), default="core")
        p.add_argument("--endpoint", default="finalComputation")
        p.add_argument("--from", default="2025-01-15T00:00:00Z", help="FromUtc (ISO-8601)")
        p.add_argument("--to", default="2025-01-15T01:00:00Z", help="ToUtc (ISO-8601, exclusive)")
        p.add_argument("--chunk-hours", type=int, default=1)
        p.add_argument("--no-cache", action="store_true", help="force live re-fetch")

    pf = sub.add_parser("fetch", help="fetch a window (hourly chunks), cache raw JSON")
    add_common(pf)
    pf.set_defaults(func=_cmd_fetch)

    ps = sub.add_parser("schema", help="fetch a window and print the record schema")
    add_common(ps)
    ps.set_defaults(func=_cmd_schema)

    px = sub.add_parser("sample", help="sample both regions/endpoints and print schemas")
    px.add_argument("--from", default="2025-01-15T00:00:00Z")
    px.add_argument("--hours", type=int, default=2)
    px.set_defaults(func=_cmd_sample)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
