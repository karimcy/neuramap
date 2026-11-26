"""Derive the 12-zone DFS basis, and each campaign site's DFS zone, from primary sources.

WHY THIS SCRIPT EXISTS. `gridq.value_stack.gb_streams` carries the 2026/27 DFS zonal basis as
TRANSCRIBED module constants rather than reading NESO's utilisation report at solve time. That is
deliberate: `dfs_util_cur.csv` is a LIVE resource that NESO grows every day, and `input_hash` pins
no data content (CLAUDE.md F-06), so a re-fetch would silently move every DFS pound under an
unchanged run identity. Transcribed constants + this script + a test that recomputes them is the
combination that keeps the numbers both fixed and checkable.

TWO DERIVATIONS, BOTH REPRODUCIBLE.

1. `--basis` (offline, no network): per DFS zone, from
   `data/fetched/neso/dfs_util_cur.csv` at its recorded sha256 -
     * the count of DISTINCT accepted Live Downwards settlement periods over the file's span,
     * the count of distinct delivery DAYS carrying one,
     * the VOLUME-WEIGHTED accepted utilisation price (GBP/MWh),
     * the count of those settlement periods by LOCAL START TIME (the window shape).
   Filters, stated because each one changes the answer: `Status == Accepted` (a rejected bid is not
   a procurement), `Service Requirement Type == Live` (a Test event is not a market outturn), and
   `Event Type == Downwards` (the engine measures DFS delivery on NET DISCHARGE, which is the
   downwards direction; the 10 accepted Upwards bids in the file are 1.0 MW in total at
   GBP 20-45/MWh and belong to the other direction of a bi-directional product).

2. `--zones` (network): each site's DFS zone by POINT-IN-POLYGON of its postcode centroid against
   NESO's official 12-zone GeoJSON, whose sha256 the assumption library already records.
     * Geography: NESO, 'DFS 12 Zones GeoJson Map', https://www.neso.energy/document/376656/download
       - a zip containing `12_Zones_DFS.geojson`, CRS84 (WGS84 lon/lat), 12 features each carrying
       the single property {'Region': 1..12}. Expected zip sha256
       ed3fa1cc89484faff47ff90fdb89c74d0fa860ff9818e729d25929b993daef88, which is the value at
       `ancillary_services.dfs_registered_unit_capacity_mw.zone_names.geojson_sha256`.
     * Centroids: postcodes.io (https://api.postcodes.io), the open API over the ONS Postcode
       Directory / ONS Postcode Database; `/postcodes/{full postcode}` for a full code and
       `/outcodes/{outward code}` for a district centroid. Both are reported so a district centroid
       can be checked against a full code inside it.

Usage:
    python3 scripts/derive_dfs_zonal_basis.py --basis            # print the module constants
    python3 scripts/derive_dfs_zonal_basis.py --basis --check    # compare against the constants
    python3 scripts/derive_dfs_zonal_basis.py --zones            # postcode -> zone (needs network)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UTILISATION_CSV = REPO / "data/fetched/neso/dfs_util_cur.csv"
UTILISATION_SHA256 = "655bd61601692045a9e36e87534ccace13406fb7610d66f64d0a509a58edf280"
GEOJSON_URL = "https://www.neso.energy/document/376656/download"
GEOJSON_ZIP_SHA256 = "ed3fa1cc89484faff47ff90fdb89c74d0fa860ff9818e729d25929b993daef88"

#: The campaign's evidenced locations. Each full postcode is the one the site's OWN record cites
#: (in the local-flex award basis written by D54); the outward code is what `SiteModel.postcode`
#: carries today. Both are resolved so the two can be checked against each other.
CAMPAIGN_POSTCODES: dict[str, tuple[str, str | None]] = {
    "bishops_cleeve_ash_plant": ("GL52", "GL52 2RT"),
    "bishops_cleeve_mrf": ("GL52", "GL52 7RT"),
    "ewelme": ("OX10", "OX10 6PJ"),
    "ewelme_invoice_anchored": ("OX10", "OX10 6PJ"),
    "harwell": ("OX11", "OX11 0QX"),
    "knowl_hill": ("RG10", "RG10 9XY"),
    "knowl_hill_invoice_anchored": ("RG10", "RG10 9XY"),
    "colnbrook": ("SL3", "SL3 0EG"),
    "tanhouse": ("SL3", "SL3 0EG"),
}


#: Additional GB outward codes resolved by the SAME method, at least one per DFS zone, so that a
#: site outside this campaign resolves to its own zone instead of falling to the D57 limb 5b
#: substitution. One is the shared test fixture's own postcode district (LE17), so the test suite
#: exercises a resolved zone rather than a substituted one.
REFERENCE_OUTWARD_CODES: tuple[str, ...] = (
    "AB10",
    "B1",
    "BS1",
    "CA1",
    "CF10",
    "DH1",
    "EH1",
    "G1",
    "HU1",
    "IV1",
    "LA1",
    "LE17",
    "LL30",
    "LS1",
    "M1",
    "ME1",
    "NE1",
    "NG1",
    "NR1",
    "PE1",
    "PL1",
    "SA1",
    "SK1",
    "SO14",
    "SW1A",
    "TR1",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zonal_basis(
    path: Path = UTILISATION_CSV,
) -> dict[int, tuple[int, int, float, dict[tuple[int, int], int]]]:
    """Per zone: (accepted settlement periods, event days, vol-weighted GBP/MWh, slot histogram).

    Raises if the source file's sha256 has moved off the recorded value: the transcribed constants
    describe ONE version of a live file, and a silently re-fetched file must fail loudly rather
    than move a published pound.
    """
    digest = _sha256(path)
    if digest != UTILISATION_SHA256:
        raise SystemExit(
            f"{path} sha256 is {digest}, not the recorded {UTILISATION_SHA256}. NESO grows this "
            "resource daily. Re-derive the module constants against the new file deliberately, "
            "with a changelog entry, rather than letting the numbers move underneath a run id."
        )
    with path.open() as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["Status"] == "Accepted"
            and r["Service Requirement Type"] == "Live"
            and r["Event Type"] == "Downwards"
        ]
    out: dict[int, tuple[int, int, float, dict[tuple[int, int], int]]] = {}
    for zone in sorted({int(r["Zone"]) for r in rows}):
        zrows = [r for r in rows if int(r["Zone"]) == zone]
        periods = {(r["Delivery Date"], r["From_Local"]) for r in zrows}
        days = {r["Delivery Date"] for r in zrows}
        mw = sum(float(r["DFS Procured MW"]) for r in zrows)
        cash = sum(
            float(r["DFS Procured MW"]) * 0.5 * float(r["Utilisation Price GBP per MWh"])
            for r in zrows
        )
        histogram: dict[tuple[int, int], int] = {}
        for _date, slot in sorted(periods):
            hh, mm = slot.split(":")
            key = (int(hh), int(mm))
            histogram[key] = histogram.get(key, 0) + 1
        out[zone] = (
            len(periods),
            len(days),
            round(cash / (mw * 0.5), 3),
            dict(sorted(histogram.items())),
        )
    return out


def print_basis(basis: dict[int, tuple[int, int, float, dict[tuple[int, int], int]]]) -> None:
    print("# generated by scripts/derive_dfs_zonal_basis.py --basis")
    print("_DFS_ZONE_OBSERVED: dict[int, DfsZoneObservation] = {")
    for zone, (periods, days, price, histogram) in basis.items():
        print(f"    {zone}: DfsZoneObservation(")
        print(f"        accepted_settlement_periods={periods},")
        print(f"        event_days={days},")
        print(f"        utilisation_price_gbp_per_mwh={price},")
        print("        window_histogram={")
        for (hh, mm), count in histogram.items():
            print(f"            ({hh}, {mm}): {count},")
        print("        },")
        print("    ),")
    print("}")
    print()
    print("# zone  SPs  days   GBP/MWh   gross GBP/kW/yr on the OBSERVED period count")
    for zone, (periods, days, price, _h) in basis.items():
        gross = periods * 0.5 * price / 1000
        print(f"#  {zone:>4} {periods:>4} {days:>5} {price:>9.3f} {gross:>10.3f}")


def check_basis(basis: dict[int, tuple[int, int, float, dict[tuple[int, int], int]]]) -> int:
    """Compare the recomputation against the module constants. Returns a process exit code."""
    sys.path.insert(0, str(REPO / "src"))
    from gridq.value_stack.gb_streams import _DFS_ZONE_OBSERVED

    bad = 0
    for zone, (periods, days, price, histogram) in basis.items():
        obs = _DFS_ZONE_OBSERVED.get(zone)
        if obs is None:
            print(f"MISSING zone {zone} in _DFS_ZONE_OBSERVED")
            bad += 1
            continue
        for label, got, want in (
            ("accepted_settlement_periods", obs.accepted_settlement_periods, periods),
            ("event_days", obs.event_days, days),
            ("utilisation_price_gbp_per_mwh", obs.utilisation_price_gbp_per_mwh, price),
            ("window_histogram", obs.window_histogram, histogram),
        ):
            if got != want:
                print(f"zone {zone} {label}: constant {got!r} != recomputed {want!r}")
                bad += 1
    extra = set(_DFS_ZONE_OBSERVED) - set(basis)
    if extra:
        print(f"zones in the constants that the source does not carry: {sorted(extra)}")
        bad += 1
    print("OK: the module constants reproduce the source" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


# --- geography ---------------------------------------------------------------


def _rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    return geometry["coordinates"]


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-ring. Deterministic, and no geometry dependency to install."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < crossing:
                inside = not inside
    return inside


def zone_of(lon: float, lat: float, features: list[dict]) -> list[int]:
    hits: set[int] = set()
    for feature in features:
        for poly in _rings(feature["geometry"]):
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                hits.add(int(feature["properties"]["Region"]))
                break
    return sorted(hits)


def _fetch(url: str) -> bytes:
    # A User-Agent is required: neso.energy answers a header-less urllib request with HTTP 403,
    # which is easy to misread as "the document has moved".
    request = urllib.request.Request(url, headers={"User-Agent": "gridq/derive_dfs_zonal_basis"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - https, fixed hosts
        return response.read()


def print_zones() -> None:
    raw = _fetch(GEOJSON_URL)
    digest = hashlib.sha256(raw).hexdigest()
    print(f"NESO 12-zone GeoJSON zip: {len(raw):,} bytes, sha256 {digest}")
    if digest != GEOJSON_ZIP_SHA256:
        print(f"  WARNING: expected {GEOJSON_ZIP_SHA256} - the published geography has changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".geojson"))
        geo = json.loads(zf.read(name))
    features = geo["features"]
    print(f"  {name}: {len(features)} features, crs {geo.get('crs')}")
    seen: dict[str, int] = {}
    for site, (outward, full) in CAMPAIGN_POSTCODES.items():
        line = f"{site:32s} {outward:6s}"
        for label, url in (
            ("outcode", f"https://api.postcodes.io/outcodes/{outward}"),
            *(
                [("full   ", f"https://api.postcodes.io/postcodes/{full.replace(' ', '%20')}")]
                if full
                else []
            ),
        ):
            result = json.loads(_fetch(url))["result"]
            lon, lat = float(result["longitude"]), float(result["latitude"])
            zones = zone_of(lon, lat, features)
            line += f" | {label} {lon:>9.5f},{lat:>8.5f} -> zone {zones}"
            if label == "outcode" and len(zones) == 1:
                seen[outward] = zones[0]
        print(line)
    for outward in REFERENCE_OUTWARD_CODES:
        result = json.loads(_fetch(f"https://api.postcodes.io/outcodes/{outward}"))["result"]
        lon, lat = float(result["longitude"]), float(result["latitude"])
        zones = zone_of(lon, lat, features)
        district = ", ".join(result.get("admin_district") or [])
        print(f"{outward:6s} {lon:>9.5f},{lat:>8.5f} -> zone {zones}  {district}")
        if len(zones) == 1:
            seen[outward] = zones[0]
    print()
    print("_DFS_ZONE_BY_OUTWARD_CODE: dict[str, int] = {")
    for outward, zone in sorted(seen.items()):
        print(f'    "{outward}": {zone},')
    print("}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basis", action="store_true", help="recompute the per-zone basis")
    parser.add_argument("--check", action="store_true", help="compare with the module constants")
    parser.add_argument("--zones", action="store_true", help="resolve postcodes to DFS zones")
    args = parser.parse_args()
    if not (args.basis or args.zones):
        parser.print_help()
        return 2
    code = 0
    if args.basis:
        basis = zonal_basis()
        if args.check:
            code = check_basis(basis)
        else:
            print_basis(basis)
    if args.zones:
        print_zones()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
