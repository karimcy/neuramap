"""Fetch GB Capacity-Market + ancillary (DC/DM/DR) figures from the NESO Data Portal CKAN API.

**Developer/CLI tool — OFF the pricing path.** Run *by hand* to pull a provenance-pinned public
figure into a versioned assumption release; **never** imported by ``gridq run``, the solver,
finance, quotes, or any pricing code. Sibling of :mod:`gridq.dev.fetch_prices`.

**Never fabricates.** Each resource is fetched raw (keyless CKAN JSON), hashed + sized + counted,
written to a gitignored artifact under ``data/fetched/neso/`` with a committed provenance sidecar,
and a *suggested* YAML assumption block is printed to stdout — this tool **only prints** the
suggestion, it never edits ``assumptions/``. If the fetch fails it raises and writes nothing; the
value it would have set is then landed *unchanged* with a note, never invented. Capacity Market is
CONDITIONAL and DC/DM/DR is GATED upside — neither is ever FIRM, whatever the fetched number.

Resources (NESO Open Data Licence, keyless CKAN ``datastore_search``)
--------------------------------------------------------------------
- ``cm-derating`` — de-rating factors by generating-technology class per auction
  (``d94ff98d-39ba-40d6-9672-7433de0631ac``). We read the ``Storage (Duration Nh)`` rows of the
  latest T-4 auction for the 1h / 2h / 4h duration classes.
- ``cm-clearing`` — auction clearing outcomes (``b1b58919-eaa4-40ea-80df-3d3e526f8223``): per
  auction, clearing price £/kW/yr = ``Forecast Cost (£)`` / (``Capacity Awarded`` MW × 1000).
  We report the latest T-4 (the primary forward auction) and the latest T-1 for context.
- ``eac-results`` — Enduring Auction Capability (Response/Reserve) results summary
  (``596f29ac-0387-4ba4-a6d3-95c243140707``): the DC/DM/DR (DCH/DCL/DMH/DML/DRH/DRL) EFA-block
  clearing prices £/MW/h; we report the mean over the fetched window and the register §10
  indicative annualisation (mean × 8760 h).

Determinism
-----------
The network fetch is isolated in :func:`gridq.dev._fetch_common.http_get_bytes` (monkeypatched in
tests). The transforms (:func:`summarise_*`) and the suggested-YAML builder are pure: identical
records ⇒ identical summary ⇒ identical block. ``fetched_utc`` is always passed into the sidecar.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gridq.dev._fetch_common import (
    FetchError,
    build_sidecar,
    decode_json,
    http_get_bytes,
    sha256_hex,
    write_json,
    write_sidecar,
)

CKAN_BASE_URL = "https://api.neso.energy/api/3/action"
LICENCE = "NESO Open Data Licence"

# --- Resource registry (id + a generous row limit + human name) --------------


@dataclass(frozen=True)
class ResourceSpec:
    key: str
    resource_id: str
    limit: int
    name: str


RESOURCES: dict[str, ResourceSpec] = {
    "cm-derating": ResourceSpec(
        "cm-derating",
        "d94ff98d-39ba-40d6-9672-7433de0631ac",
        2000,
        "CM de-rating factors by technology class",
    ),
    "cm-clearing": ResourceSpec(
        "cm-clearing",
        "b1b58919-eaa4-40ea-80df-3d3e526f8223",
        2000,
        "CM auction clearing outcomes",
    ),
    "eac-results": ResourceSpec(
        "eac-results",
        "596f29ac-0387-4ba4-a6d3-95c243140707",
        60000,
        "EAC (Response/Reserve) results summary",
    ),
}

# The three storage duration classes the assumption library keys on ("1"/"2"/"4").
_DURATION_KEYS = ("1", "2", "4")
# DC / DM / DR frequency-response products (register §10).
_DC_DM_DR_PRODUCTS = frozenset({"DCH", "DCL", "DMH", "DML", "DRH", "DRL"})
_HOURS_PER_YEAR = 8760


# --- URL + network + extraction ---------------------------------------------


def build_search_url(spec: ResourceSpec, *, base_url: str = CKAN_BASE_URL) -> str:
    """Construct the keyless ``datastore_search`` URL for one resource (full-table pull)."""
    return (
        f"{base_url.rstrip('/')}/datastore_search?resource_id={spec.resource_id}&limit={spec.limit}"
    )


def extract_records(payload: Any) -> list[Mapping[str, Any]]:
    """Pull ``result.records`` out of a CKAN ``datastore_search`` payload.

    Raises :class:`FetchError` on a success=false response or an unrecognised shape, so a CKAN
    contract change surfaces loudly rather than silently returning nothing.
    """
    if not isinstance(payload, Mapping):
        raise FetchError(f"CKAN payload is not an object (got {type(payload).__name__}).")
    if payload.get("success") is False:
        raise FetchError(f"CKAN reported success=false: {payload.get('error')}")
    result = payload.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("records"), list):
        raise FetchError("CKAN payload has no result.records array.")
    return [r for r in result["records"] if isinstance(r, Mapping)]


# --- Small pure parsers ------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Parse a numeric cell to float; ``None`` for missing/blank/unparseable (never guessed)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "null", "none"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_auction(name: str) -> tuple[int | None, str | None]:
    """Parse ``"2029-30 (T-4) Four Year Ahead ..."`` → ``(2029, "T-4")`` (year, round).

    Returns ``(None, None)`` when the leading delivery year cannot be read — the row is then
    skipped for latest-auction selection, never mis-ranked.
    """
    year_match = re.match(r"\s*(\d{4})", name)
    year = int(year_match.group(1)) if year_match else None
    round_match = re.search(r"\((T-\d+|TR)\)", name)
    return year, (round_match.group(1) if round_match else None)


def _duration_hours(tech_class: str) -> float | None:
    """Extract the storage duration (hours) from ``"Storage (Duration 4h)"`` → ``4.0``."""
    match = re.search(r"Storage \(Duration ([0-9.]+)h", tech_class)
    return float(match.group(1)) if match else None


# --- Pure transforms (one per resource) --------------------------------------


def summarise_cm_derating(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Latest T-4 storage de-rating factors for the 1h/2h/4h duration classes.

    Picks the auction with the greatest leading delivery year among ``(T-4)`` auctions that carry
    storage-duration rows, then reads the three keyed durations. Raises if none are present (never
    fabricates a factor).
    """
    by_auction: dict[str, dict[str, float]] = {}
    year_of: dict[str, int] = {}
    for rec in records:
        name = str(rec.get("Auction Name", ""))
        year, rnd = parse_auction(name)
        if year is None or rnd != "T-4":
            continue
        hours = _duration_hours(str(rec.get("Generating Technology Class", "")))
        factor = _to_float(rec.get("De-Rating Factor"))
        if hours is None or factor is None:
            continue
        key = str(int(hours)) if hours.is_integer() else str(hours)
        if key in _DURATION_KEYS:
            by_auction.setdefault(name, {})[key] = factor
            year_of[name] = year

    eligible = {a: f for a, f in by_auction.items() if all(k in f for k in _DURATION_KEYS)}
    if not eligible:
        raise FetchError(
            "no T-4 auction with storage 1h/2h/4h de-rating rows found — nothing fabricated."
        )
    latest = max(eligible, key=lambda a: year_of[a])
    factors = {k: round(eligible[latest][k], 4) for k in _DURATION_KEYS}
    return {"auction_name": latest, "delivery_year_start": year_of[latest], "factors": factors}


def summarise_cm_clearing(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Latest T-4 (value) + latest T-1 (context) clearing price in £/kW/yr.

    Per auction, price = ``Forecast Cost (£)`` / (``Capacity Awarded`` MW × 1000). The price is
    constant across an auction's delivery-year rows; we take the first computable row per auction.
    """
    price_by_auction: dict[str, float] = {}
    meta: dict[str, tuple[int, str]] = {}
    for rec in records:
        name = str(rec.get("Auction Name", ""))
        year, rnd = parse_auction(name)
        if year is None or rnd is None or name in price_by_auction:
            continue
        awarded = _to_float(rec.get("Capacity Awarded"))
        cost = _to_float(rec.get("Forecast Cost (£)"))
        if awarded is None or cost is None or awarded <= 0:
            continue
        price_by_auction[name] = cost / (awarded * 1000.0)
        meta[name] = (year, rnd)

    def _latest(round_label: str) -> dict[str, Any] | None:
        cands = {a: p for a, p in price_by_auction.items() if meta[a][1] == round_label}
        if not cands:
            return None
        auction = max(cands, key=lambda a: meta[a][0])
        return {
            "auction_name": auction,
            "delivery_year_start": meta[auction][0],
            "clearing_price_gbp_per_kw_yr": round(cands[auction], 2),
        }

    latest_t4 = _latest("T-4")
    if latest_t4 is None:
        raise FetchError("no T-4 clearing outcome found — nothing fabricated.")
    return {"latest_t4": latest_t4, "latest_t1": _latest("T-1")}


def summarise_eac_dc_dm_dr(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Mean DC/DM/DR clearing price (£/MW/h) over the fetched window + the ×8760 annualisation.

    Register §10 indicative equation: ``mean cleared £/MW/h × 8760``. Prices may be negative;
    they are kept verbatim (never floored). Raises if no DC/DM/DR rows are present.
    """
    prices: list[float] = []
    starts: list[str] = []
    for rec in records:
        product = str(rec.get("auctionProduct", "")).strip().upper()
        if product not in _DC_DM_DR_PRODUCTS:
            continue
        price = _to_float(rec.get("clearingPrice"))
        if price is None:
            continue
        prices.append(price)
        start = rec.get("deliveryStart")
        if start is not None:
            starts.append(str(start))
    if not prices:
        raise FetchError("no DC/DM/DR clearing-price rows found — nothing fabricated.")
    mean = sum(prices) / len(prices)
    return {
        "product_count": len(prices),
        "mean_gbp_per_mw_h": round(mean, 4),
        "window_start": min(starts) if starts else None,
        "window_end": max(starts) if starts else None,
        "annualised_gbp_per_mw_yr": round(mean * _HOURS_PER_YEAR, 1),
    }


_SUMMARISERS = {
    "cm-derating": summarise_cm_derating,
    "cm-clearing": summarise_cm_clearing,
    "eac-results": summarise_eac_dc_dm_dr,
}


# --- Suggested YAML block (print-only; never written into assumptions/) -------


def suggested_yaml(
    resource: str,
    summary: Mapping[str, Any],
    *,
    source_url: str,
    sha12: str,
    fetched_date: str,
    sidecar_path: str,
) -> str:
    """Build the *suggested* YAML assumption block for the fetched resource (print-only)."""
    cite = (
        f"NESO Data Portal CKAN {source_url} sha256:{sha12} "
        f"fetched {fetched_date}; sidecar {sidecar_path}"
    )
    if resource == "cm-derating":
        f = summary["factors"]
        return (
            "capacity_market:\n"
            "  de_rating_factor_by_duration_h:\n"
            "    value:\n"
            f'      "1": {f["1"]}\n'
            f'      "2": {f["2"]}\n'
            f'      "4": {f["4"]}\n'
            "    unit: ratio\n"
            f'    source: "{cite} ({summary["auction_name"]})"\n'
            "    status: needs_review\n"
        )
    if resource == "cm-clearing":
        t4 = summary["latest_t4"]
        t1 = summary.get("latest_t1")
        t1_ctx = (
            f" T-1 context: {t1['auction_name']} = {t1['clearing_price_gbp_per_kw_yr']} GBP/kW/yr."
            if t1
            else " (no T-1 outcome fetched)."
        )
        return (
            "capacity_market:\n"
            "  clearing_price_gbp_per_kw_yr:\n"
            f"    value: {t4['clearing_price_gbp_per_kw_yr']}\n"
            "    unit: GBP/kW/yr\n"
            f'    source: "{cite} ({t4["auction_name"]}).{t1_ctx}"\n'
            "    status: needs_review\n"
        )
    # eac-results
    return (
        "ancillary_services:\n"
        "  dc_dm_dr_value_gbp_per_mw_yr:\n"
        f"    value: {summary['annualised_gbp_per_mw_yr']}\n"
        "    unit: GBP/MW/yr\n"
        f'    source: "{cite}"\n'
        "    status: needs_review\n"
        f"    # derivation: mean {summary['mean_gbp_per_mw_h']} GBP/MW/h across DC/DM/DR "
        f"({summary['product_count']} EFA blocks, "
        f"{summary['window_start']}..{summary['window_end']}) "
        f"x {_HOURS_PER_YEAR} h. GATED upside, never FIRM.\n"
    )


# --- Orchestration -----------------------------------------------------------


@dataclass(frozen=True)
class NesoFetchResult:
    resource: str
    out_path: Path
    sidecar_path: Path
    record_count: int
    sha256: str
    summary: dict[str, Any]
    suggested_yaml: str
    source_url: str


def run(
    *,
    resource: str,
    out_path: str | Path,
    base_url: str = CKAN_BASE_URL,
    fetched_utc: datetime | None = None,
    http_get: Any = None,
) -> NesoFetchResult:
    """Fetch → summarise → write artifact + sidecar; return the result (never fabricates).

    ``http_get`` defaults to :func:`http_get_bytes`; tests pass a stub returning captured bytes.
    ``fetched_utc`` is stamped on the sidecar (read from the clock here when ``None`` — the
    orchestration boundary, not a pure function). No artifact is written if the fetch fails.
    """
    if resource not in RESOURCES:
        raise FetchError(f"unknown resource {resource!r}; choose one of {sorted(RESOURCES)}.")
    spec = RESOURCES[resource]
    getter = http_get if http_get is not None else http_get_bytes
    stamp = (
        fetched_utc
        if fetched_utc is not None
        else datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    )

    url = build_search_url(spec, base_url=base_url)
    raw = getter(url)
    payload = decode_json(raw, url=url)
    records = extract_records(payload)
    summary = _SUMMARISERS[resource](records)

    out = Path(out_path)
    sidecar_path = out.with_name(out.stem + ".provenance.json")
    sha = sha256_hex(raw)
    fetched_date = stamp.date().isoformat()

    yaml_block = suggested_yaml(
        resource,
        summary,
        source_url=url,
        sha12=sha[:12],
        fetched_date=fetched_date,
        sidecar_path=str(sidecar_path),
    )

    write_json(
        out,
        {
            "resource": resource,
            "resource_id": spec.resource_id,
            "source_url": url,
            "record_count": len(records),
            "summary": summary,
            "records": [dict(r) for r in records],
        },
    )
    sidecar = build_sidecar(
        raw=raw,
        source_url=url,
        dataset_id=spec.resource_id,
        fetched_utc=stamp,
        licence=LICENCE,
        fetcher="gridq.dev.fetch_neso_ckan",
        fetcher_args={"resource": resource},
        record_count=len(records),
    )
    write_sidecar(sidecar_path, sidecar)

    return NesoFetchResult(
        resource=resource,
        out_path=out,
        sidecar_path=sidecar_path,
        record_count=len(records),
        sha256=sha,
        summary=summary,
        suggested_yaml=yaml_block,
        source_url=url,
    )


def _default_out(resource: str, fetched: date) -> str:
    return f"data/fetched/neso/{resource.replace('-', '_')}_{fetched.isoformat()}.json"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.dev.fetch_neso_ckan",
        description=(
            "Fetch a GB Capacity-Market / ancillary figure from the keyless NESO Data Portal "
            "CKAN API into a provenance-pinned artifact + sidecar, and PRINT a suggested YAML "
            "assumption block. OFF the pricing path; never edits assumptions/; never fabricates."
        ),
    )
    parser.add_argument("--resource", required=True, choices=sorted(RESOURCES))
    parser.add_argument("--out", default=None, help="artifact path (default data/fetched/neso/...)")
    parser.add_argument(
        "--base-url", default=CKAN_BASE_URL, help=f"CKAN base (default {CKAN_BASE_URL})"
    )
    parser.add_argument(
        "--fetched-utc", default=None, help="ISO datetime for the sidecar (default: now)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Prints a summary + the suggested YAML, returns 0; 1 on any fetch failure."""
    args = _build_arg_parser().parse_args(argv)
    fetched_utc = None
    if args.fetched_utc is not None:
        try:
            fetched_utc = datetime.fromisoformat(args.fetched_utc)
        except ValueError as exc:
            print(f"fetch_neso_ckan: bad --fetched-utc ({exc}).", file=sys.stderr)
            return 1
    stamp = fetched_utc or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    out_path = args.out or _default_out(args.resource, stamp.date())

    try:
        result = run(
            resource=args.resource,
            out_path=out_path,
            base_url=args.base_url,
            fetched_utc=stamp,
        )
    except FetchError as exc:
        print(f"fetch_neso_ckan: {exc}", file=sys.stderr)
        return 1

    print(
        f"Fetched {result.record_count} record(s) for {result.resource}; "
        f"sha256={result.sha256[:12]}... -> {result.out_path}"
    )
    print(f"  sidecar: {result.sidecar_path}")
    print(f"  summary: {result.summary}")
    print("  --- suggested YAML (print-only; transcribe by hand into a gb_v1_x release) ---")
    print(result.suggested_yaml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
