"""The public-source channel of the candidate site dossier: DNO licence area by point-in-polygon.

D111 (Fable 5.2). A public fact may fill a dossier field only when it was retrieved and
cited. The licence area is read from a published DNO licence-area boundary dataset that the owner
downloaded and registered (URL, retrieval date, sha256 of the bytes). The lookup runs locally: a
site's location is never sent to a web service.

The result is "the licence area whose published boundary contains the site's registered point".
It is not the connecting operator: a site inside one licence area can be connected by an IDNO or
a private network, so the ``dno`` field stays an ask until a connection document or MPAN
registration closes it.

A point within ``MIN_MARGIN_KM`` of a boundary, or inside no feature or more than one, is not
evidenced: the location or the dataset is not precise enough to say, and the dossier asks.

Geometry is WGS84 longitude/latitude only (GeoJSON RFC 7946). A dataset that declares any other
CRS is refused rather than reprojected: GridQ carries no projection library, and a silent
mis-projection would put a site in the wrong licence area.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from gridq.ops.dossier_model import LicenceAreaValue, PublicSource, SiteLocation

__all__ = [
    "MIN_MARGIN_KM",
    "BoundaryDataset",
    "BoundaryFeature",
    "LicenceAreaLookup",
    "PublicSourceError",
    "licence_area_at",
    "load_boundary_dataset",
]

#: A point closer than this to any licence-area boundary is not placed in an area.
MIN_MARGIN_KM: Final = 1.0
_EARTH_RADIUS_KM: Final = 6371.0088
_WGS84_CRS_NAMES: Final = frozenset(
    {
        "urn:ogc:def:crs:OGC:1.3:CRS84",
        "urn:ogc:def:crs:OGC::CRS84",
        "urn:ogc:def:crs:EPSG::4326",
        "EPSG:4326",
    }
)

Ring = tuple[tuple[float, float], ...]  # (longitude, latitude) pairs, closed or open
Polygon = tuple[Ring, ...]  # outer ring first, then holes


class PublicSourceError(ValueError):
    """A boundary dataset that cannot be used: bytes that do not match its citation, a non-WGS84
    CRS, or geometry the channel does not read."""


@dataclass(frozen=True)
class BoundaryFeature:
    feature_id: str
    licence_area: str
    polygons: tuple[Polygon, ...]


@dataclass(frozen=True)
class BoundaryDataset:
    """A registered public boundary dataset, verified against its citation's hash."""

    source: PublicSource
    features: tuple[BoundaryFeature, ...]


@dataclass(frozen=True)
class LicenceAreaLookup:
    """The answer for one point: a value, or why there is none."""

    value: LicenceAreaValue | None
    why_not: str


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ring(raw: Any) -> Ring:
    if not isinstance(raw, list) or len(raw) < 3:
        raise PublicSourceError("a polygon ring needs at least three positions")
    out: list[tuple[float, float]] = []
    for pos in raw:
        if not isinstance(pos, list) or len(pos) < 2:
            raise PublicSourceError("a position is [longitude, latitude]")
        lon, lat = float(pos[0]), float(pos[1])
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise PublicSourceError(
                "a position is outside WGS84 degree bounds: the dataset is projected"
            )
        out.append((lon, lat))
    return tuple(out)


def _polygons(geometry: Any) -> tuple[Polygon, ...]:
    if not isinstance(geometry, dict):
        raise PublicSourceError("a feature has no geometry")
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        raise PublicSourceError("a geometry has no coordinates")
    if kind == "Polygon":
        return (tuple(_ring(r) for r in coords),)
    if kind == "MultiPolygon":
        return tuple(tuple(_ring(r) for r in poly) for poly in coords)
    raise PublicSourceError(f"geometry type {kind!r} is not a Polygon or MultiPolygon")


def load_boundary_dataset(
    path: str | Path, *, source: PublicSource, area_property: str
) -> BoundaryDataset:
    """Read a GeoJSON FeatureCollection whose bytes hash to ``source.content_sha256``."""
    data = Path(path).read_bytes()
    if _sha256_bytes(data) != source.content_sha256:
        raise PublicSourceError(
            "the boundary file's bytes do not hash to the cited content_sha256: it is not the "
            "document the citation names"
        )
    doc = json.loads(data.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise PublicSourceError("the boundary dataset is not a GeoJSON FeatureCollection")
    crs = doc.get("crs")
    if crs is not None:
        name = crs.get("properties", {}).get("name") if isinstance(crs, dict) else None
        if name not in _WGS84_CRS_NAMES:
            raise PublicSourceError(
                f"the boundary dataset declares CRS {name!r}; only WGS84 longitude/latitude is "
                "read (reproject it before registering)"
            )
    features: list[BoundaryFeature] = []
    for index, feature in enumerate(doc.get("features") or ()):
        props = feature.get("properties") or {}
        area = props.get(area_property)
        if not isinstance(area, str) or not area.strip():
            raise PublicSourceError(f"feature {index} has no {area_property!r} property")
        fid = feature.get("id")
        features.append(
            BoundaryFeature(
                feature_id=str(fid) if fid is not None else f"feature-{index}",
                licence_area=area.strip(),
                polygons=_polygons(feature.get("geometry")),
            )
        )
    if not features:
        raise PublicSourceError("the boundary dataset has no features")
    return BoundaryDataset(source=source, features=tuple(features))


def _inside_ring(lon: float, lat: float, ring: Ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def _inside_polygon(lon: float, lat: float, polygon: Polygon) -> bool:
    outer, *holes = polygon
    return _inside_ring(lon, lat, outer) and not any(_inside_ring(lon, lat, h) for h in holes)


def _segment_distance_km(
    lon: float, lat: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Distance from the point to segment ab, on a local equirectangular projection (km)."""
    k = math.radians(1.0) * _EARTH_RADIUS_KM
    cos_lat = math.cos(math.radians(lat))

    def xy(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - lon) * k * cos_lat, (p[1] - lat) * k)

    (ax, ay), (bx, by) = xy(a), xy(b)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def _rings(features: Iterable[BoundaryFeature]) -> Iterable[Ring]:
    for f in features:
        for poly in f.polygons:
            yield from poly


def _margin_km(lon: float, lat: float, rings: Iterable[Ring]) -> float:
    best = math.inf
    for ring in rings:
        n = len(ring)
        for i in range(n):
            best = min(best, _segment_distance_km(lon, lat, ring[i], ring[(i + 1) % n]))
    return best


def licence_area_at(dataset: BoundaryDataset, location: SiteLocation) -> LicenceAreaLookup:
    """The licence area containing ``location``, when exactly one does with a clear margin."""
    lon, lat = location.longitude, location.latitude
    containing: Sequence[BoundaryFeature] = [
        f for f in dataset.features if any(_inside_polygon(lon, lat, p) for p in f.polygons)
    ]
    areas = sorted({f.licence_area for f in containing})
    if not areas:
        return LicenceAreaLookup(None, "the registered point lies in no published licence area")
    if len(areas) > 1:
        return LicenceAreaLookup(
            None, f"the registered point lies in {len(areas)} published licence areas at once"
        )
    margin = _margin_km(lon, lat, _rings(dataset.features))
    if margin < MIN_MARGIN_KM:
        return LicenceAreaLookup(
            None,
            f"the registered point is {margin:.2f} km from a licence-area boundary, under the "
            f"{MIN_MARGIN_KM:.0f} km margin the channel needs to place it",
        )
    feature = sorted(containing, key=lambda f: f.feature_id)[0]
    value = LicenceAreaValue(
        kind="licence_area_containing_site",
        licence_area=areas[0],
        boundary_feature=feature.feature_id,
        location=location,
        margin_km=round(margin, 3),
    )
    return LicenceAreaLookup(value, "")
