#!/usr/bin/env python3
"""Phase 2.1 — match our GB funnel nodes to (a) NESO Grid Supply Point regions and
(b) ETYS transmission substations/buses.

  Input : data/GB.json                    (funnel nodes, each with lat/lon)
          data/networks/gb_etys.json       (built by gb_network.py)
          NESO "GIS Boundaries for GB Grid Supply Points" dataset (CKAN, anonymous):
            - GSP region polygons  (WGS84 GeoJSON, newest edition)
            - GSP-Gnode-DirectConnect-Region lookup CSV (gnode/gsp coordinates)
  Output: data/networks/gb_node_map.json
            each GB node -> { gsp{...}, etys_bus{...}, confidence }

--------------------------------------------------------------------------------
METHOD (explicit, per record)

GSP assignment
  * point_in_polygon : node (lon,lat) falls inside a GSP region MultiPolygon.
                       gsp_distance_km = 0. This is the authoritative case.
  * nearest_centroid : node is outside every polygon (offshore / island / gap);
                       we take the nearest polygon and record the haversine
                       distance from the node to that polygon's representative
                       point. Lower confidence, distance flagged.

ETYS bus assignment
  ETYS App B has NO coordinates. The GSP dataset's lookup CSV *does* carry a
  latitude/longitude per Gnode (grid node). We join Gnode -> ETYS site by the
  4-char site code (gnode_name[:4] == ETYS token[:4]); ~294/602 ETYS sites get a
  coordinate this way (325 using the GSP centroid as fallback). For each GB node
  we take the nearest coordinate-bearing ETYS site by haversine (method
  nearest_gnode_coord) and, among that site's buses, pick the one whose voltage
  matches the node's kV (else the highest-voltage bus). distance_km is reported;
  matches beyond ~15 km are low confidence (the true nearest ETYS site may simply
  lack a published coordinate, common in far-north SHET territory).

CONFIDENCE (overall label)
  high   : GSP by point_in_polygon AND ETYS distance <= 5 km
  medium : GSP by point_in_polygon AND ETYS distance <= 15 km, OR
           GSP nearest_centroid <= 5 km with ETYS <= 15 km
  low    : anything else (far ETYS match or node outside all GSP polygons far)

Nothing is dropped; unmatched fields are null with a reason in `_notes`.
"""
import argparse
import csv
import io
import json
import math
import os
import sys
import zipfile

import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "pipelines", ".cache", "neso")
GB_JSON = os.path.join(ROOT, "data", "GB.json")
NET_JSON = os.path.join(ROOT, "data", "networks", "gb_etys.json")
OUT = os.path.join(ROOT, "data", "networks", "gb_node_map.json")

UA = "Mozilla/5.0 (OpportunityMap/neura data pipeline; +anonymous NESO GSP ingest)"
CKAN = "https://api.neso.energy/api/3/action/package_show?id=gis-boundaries-for-gb-grid-supply-points"

# Newest GSP-region polygon edition, WGS84 GeoJSON extracted from its zip.
GEOJSON_FILE = os.path.join(CACHE, "GSP_regions_4326_20260209.geojson")
GSP_ZIP_URL = ("https://api.neso.energy/dataset/2810092e-d4b2-472f-b955-d8bea01f9ec0/"
               "resource/5dfab3dd-f192-40ab-b97f-b365a594293c/download/gsp_regions_20260209.zip")
GEOJSON_MEMBER = "Proj_4326/GSP_regions_4326_20260209.geojson"
LOOKUP_FILE = os.path.join(CACHE, "gsp_gnode_lookup.csv")
LOOKUP_URL = ("https://api.neso.energy/dataset/2810092e-d4b2-472f-b955-d8bea01f9ec0/"
              "resource/bbe2cc72-a6c6-46e6-8f4e-48b879467368/download/"
              "gsp_gnode_directconnect_region_lookup.csv")


def log(m):
    print(m, file=sys.stderr)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _download(url, dest, refresh=False):
    if os.path.exists(dest) and not refresh:
        log(f"cached  {os.path.basename(dest)} ({os.path.getsize(dest)} bytes)")
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    log(f"GET     {url}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(r.content)
    log(f"saved   {dest} ({len(r.content)} bytes)")


def ensure_sources(refresh=False):
    _download(LOOKUP_URL, LOOKUP_FILE, refresh)
    if os.path.exists(GEOJSON_FILE) and not refresh:
        log(f"cached  {os.path.basename(GEOJSON_FILE)} ({os.path.getsize(GEOJSON_FILE)} bytes)")
        return
    # download zip and extract the WGS84 geojson member
    log(f"GET     {GSP_ZIP_URL}")
    r = requests.get(GSP_ZIP_URL, headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open(GEOJSON_MEMBER) as src, open(GEOJSON_FILE, "wb") as dst:
            dst.write(src.read())
    log(f"extracted {GEOJSON_FILE} ({os.path.getsize(GEOJSON_FILE)} bytes)")


def load_gsp_polygons():
    gj = json.load(open(GEOJSON_FILE))
    geoms, meta = [], []
    for f in gj["features"]:
        try:
            g = shape(f["geometry"])
        except Exception as e:
            log(f"  [skip] bad geometry fid={f.get('properties', {}).get('fid')}: {e}")
            continue
        if g.is_empty:
            continue
        p = f["properties"]
        geoms.append(g)
        meta.append({"gsp": p.get("GSPs"), "group": p.get("GSPGroup"),
                     "fid": p.get("fid")})
    log(f"GSP polygons: {len(geoms)}")
    return geoms, meta


def load_lookup():
    """Return (site_coord{site->(lat,lon)}, gsp_info{gsp_name->{group,region}})."""
    rows = list(csv.DictReader(open(LOOKUP_FILE, encoding="utf-8-sig")))
    gnode_coord, gsp_coord, gsp_info = {}, {}, {}
    for r in rows:
        gn = (r.get("gnode_name") or "").strip()
        if len(gn) >= 4 and r.get("gnode_lat") and r.get("gnode_lon"):
            gnode_coord.setdefault(gn[:4], (float(r["gnode_lat"]), float(r["gnode_lon"])))
        gsp = (r.get("gsp_name") or "").strip()
        if gsp and r.get("gsp_lat") and r.get("gsp_lon"):
            gsp_coord.setdefault(gsp.split("_")[0][:4], (float(r["gsp_lat"]), float(r["gsp_lon"])))
            gsp_info[gsp] = {"group": r.get("pes_name") or r.get("region_name"),
                             "region": r.get("region_name")}
    # site coordinate = gnode where available, else gsp centroid
    site_coord = dict(gsp_coord)
    site_coord.update(gnode_coord)
    log(f"lookup: {len(rows)} rows -> {len(gnode_coord)} gnode coords, "
        f"{len(site_coord)} site coords (gnode+gsp)")
    return site_coord, gsp_info, len(gnode_coord)


def build_etys_site_index(net, site_coord):
    """site -> {coord, buses:[{id,voltage_kv,name}]} for coordinate-bearing sites."""
    by_site = {}
    for b in net["buses"]:
        s = b["id"][:4]
        by_site.setdefault(s, []).append(b)
    idx = []
    for s, coord in site_coord.items():
        if s in by_site:
            idx.append((s, coord, by_site[s]))
    log(f"ETYS sites with coordinates: {len(idx)} / {len(by_site)}")
    return idx


def pick_bus(buses, node_kv):
    """Choose the representative bus at a site: match node kV, else highest kV."""
    if node_kv:
        exact = [b for b in buses if b.get("voltage_kv") == node_kv]
        if exact:
            return exact[0]
    with_v = [b for b in buses if b.get("voltage_kv")]
    if with_v:
        return max(with_v, key=lambda b: b["voltage_kv"])
    return buses[0]


def confidence(gsp_method, gsp_km, etys_km):
    if etys_km is None:
        return "low"
    if gsp_method == "point_in_polygon" and etys_km <= 5:
        return "high"
    if gsp_method == "point_in_polygon" and etys_km <= 15:
        return "medium"
    if gsp_method == "nearest_centroid" and (gsp_km or 1e9) <= 5 and etys_km <= 15:
        return "medium"
    return "low"


def main():
    ap = argparse.ArgumentParser(description="Match GB funnel nodes to GSPs + ETYS buses.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    ensure_sources(args.refresh)
    net = json.load(open(NET_JSON))
    gb = json.load(open(GB_JSON))
    nodes = gb["nodes"]

    geoms, meta = load_gsp_polygons()
    tree = STRtree(geoms)
    reppts = [g.representative_point() for g in geoms]

    site_coord, gsp_info, n_gnode = load_lookup()
    etys_idx = build_etys_site_index(net, site_coord)

    out = []
    stats = {"total": len(nodes), "gsp_pip": 0, "gsp_centroid": 0, "gsp_none": 0,
             "etys_matched": 0, "conf": {"high": 0, "medium": 0, "low": 0}}
    etys_dists = []

    for i, nd in enumerate(nodes):
        lat, lon = nd.get("lat"), nd.get("lon")
        rec = {"node": nd.get("n"), "lay": nd.get("lay"), "kv": nd.get("kv"),
               "kind": nd.get("kind"), "mw": nd.get("mw"), "lat": lat, "lon": lon,
               "gsp": None, "etys_bus": None, "confidence": "low", "_notes": []}
        if lat is None or lon is None:
            rec["_notes"].append("node missing coordinates")
            stats["gsp_none"] += 1
            out.append(rec)
            continue

        pt = Point(lon, lat)
        # --- GSP: point in polygon, else nearest ---
        gsp_method, gsp_km, mi = None, None, None
        # STRtree.query tests predicate(input, tree_geom); point-in-polygon = within
        cand = tree.query(pt, predicate="within")
        if len(cand) > 0:
            mi = int(cand[0])
            gsp_method, gsp_km = "point_in_polygon", 0.0
            stats["gsp_pip"] += 1
        else:
            ni = int(tree.nearest(pt))
            mi = ni
            rp = reppts[ni]
            gsp_km = round(haversine_km(lat, lon, rp.y, rp.x), 2)
            gsp_method = "nearest_centroid"
            stats["gsp_centroid"] += 1
        m = meta[mi]
        rec["gsp"] = {"name": m["gsp"], "group": m["group"],
                      "region": (gsp_info.get(m["gsp"]) or {}).get("region"),
                      "method": gsp_method, "distance_km": gsp_km}

        # --- ETYS: nearest coordinate-bearing site ---
        best = None
        for s, (slat, slon), buses in etys_idx:
            d = haversine_km(lat, lon, slat, slon)
            if best is None or d < best[0]:
                best = (d, s, buses)
        if best:
            d, s, buses = best
            bus = pick_bus(buses, nd.get("kv"))
            rec["etys_bus"] = {"id": bus["id"], "site": s, "name": bus.get("name"),
                               "voltage_kv": bus.get("voltage_kv"),
                               "distance_km": round(d, 2),
                               "method": "nearest_gnode_coord"}
            etys_dists.append(d)
            stats["etys_matched"] += 1

        etys_km = rec["etys_bus"]["distance_km"] if rec["etys_bus"] else None
        rec["confidence"] = confidence(gsp_method, gsp_km, etys_km)
        stats["conf"][rec["confidence"]] += 1
        out.append(rec)

    etys_dists.sort()
    median = etys_dists[len(etys_dists) // 2] if etys_dists else None
    p90 = etys_dists[int(len(etys_dists) * 0.9)] if etys_dists else None

    result = {
        "meta": {
            "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "inputs": {"gb_nodes": GB_JSON, "network": NET_JSON,
                       "gsp_polygons": os.path.basename(GEOJSON_FILE),
                       "gsp_lookup": os.path.basename(LOOKUP_FILE),
                       "dataset": "NESO gis-boundaries-for-gb-grid-supply-points",
                       "gsp_polygon_edition": "20260209 (WGS84)"},
            "methods": {
                "gsp": "point_in_polygon (authoritative) | nearest_centroid (fallback)",
                "etys_bus": "nearest ETYS site with a Gnode/GSP coordinate (haversine)",
                "etys_coord_bridge": f"{n_gnode} Gnode coords; {len(etys_idx)} ETYS sites coordinatable",
            },
            "stats": {
                **stats,
                "gsp_matched_total": stats["gsp_pip"] + stats["gsp_centroid"],
                "etys_median_km": round(median, 2) if median is not None else None,
                "etys_p90_km": round(p90, 2) if p90 is not None else None,
            },
        },
        "matches": out,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(result, open(args.out, "w"), indent=1)

    log("\n=== gb_node_map.json ===")
    log(f"nodes           {stats['total']}")
    log(f"GSP point-in-polygon {stats['gsp_pip']} | nearest-centroid {stats['gsp_centroid']} "
        f"| none {stats['gsp_none']}")
    log(f"ETYS matched    {stats['etys_matched']}  median {median:.2f} km  p90 {p90:.2f} km"
        if median is not None else "ETYS matched 0")
    log(f"confidence      {stats['conf']}")
    log(f"wrote {args.out}")


if __name__ == "__main__":
    main()
