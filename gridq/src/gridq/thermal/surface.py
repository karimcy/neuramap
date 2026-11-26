"""From certified slots to an IC-11 v1.1.0 surface (D165, D168).

**The producer seam.** The builder reads only `FlexProducer` answers: per (season, half-hour
slot) a `SlotAnswer` (kW by duration, or zero flex, or unknown, with a reason) and, per declared
point, a `Rebound`. The certificate is one producer. A future interface-level ThermaFlex adapter
(research note 35: Q2 answers "largest holdable kW and rebound, or none" for a site, start, hold
and band) would be another, built by asking Q2 at starts across each slot and keeping the lowest
answer; the same builder then gives an IC-11 surface the same conformance tests can judge.
Nothing for that adapter is built here.

**Bands (G-c over G-b).** Seasons are the caller's. Unless the caller pins a grid, bands are
proposed by merging adjacent half-hour slots jointly across seasons, cheapest first, where the
cost is the firm kW x minutes lost (a band's curve is the lowest of its slots'), until at most
``MAX_BANDS`` remain. A change of cell kind in any season is always a band edge.

**Curves (D-b).** A cell's curve is its lowest slot's kW-by-duration staircase from 15 minutes:
one point at each duration after which the kW drops, at most ``MAX_POINTS`` (dropping the points
whose loss of kW x minutes is smallest). kW and durations are certified integers (rounded down),
rebounds are rounded up, and limits are stated as declared.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
from pydantic import BaseModel

from gridq.contracts._base import Unknown, unknown
from gridq.contracts.evidence import EvidenceRef
from gridq.contracts.thermal_flex import (
    CLOCK,
    AvailabilityCell,
    CustomerSlaLimit,
    EvidenceClass,
    Flex,
    FlexPoint,
    LimitSource,
    MeasurementPoint,
    ModelledBasis,
    Rebound,
    TemperatureBand,
    ThermalFlexAvailability,
    ThermalLimits,
    TimeBand,
    Unavailable,
    ZeroFlex,
)
from gridq.thermal.certificate import (
    MAX_DURATION_MIN,
    MIN_DURATION_MIN,
    SLOTS_PER_DAY,
    Certificate,
    SlotAnswer,
    build_envelopes,
    certify,
)
from gridq.thermal.inputs import (
    CoefficientSet,
    DriverSeries,
    PlantInputs,
    SiteEvidenced,
    Stated,
    SurfaceRequest,
    UnavailableWindow,
)
from gridq.thermal.plant import (
    Resolved,
    Unresolved,
    binding_band,
    curve_validity,
    max_power,
    resolve,
)
from gridq.thermal.site_requests import PlantDataRequest, TopologyAssessment, plant_data_request

__all__ = [
    "MAX_BANDS",
    "MAX_POINTS",
    "METHOD_VERSION",
    "MODEL",
    "FlexProducer",
    "SurfaceResult",
    "build_surface",
    "input_hash",
    "propose_bands",
    "staircase",
]

MODEL: Final = "gridq.thermal.thermal_plant_model"
METHOD_VERSION: Final = "1.0.0"
MAX_BANDS: Final = 6
MAX_POINTS: Final = 12


class FlexProducer(Protocol):
    """Anything that answers per slot and per declared point (the Q2-shaped seam)."""

    def answer(self, season_index: int, slot: int) -> SlotAnswer: ...

    def rebound(
        self, season_index: int, slots: Sequence[int], duration_min: int, kw: int
    ) -> Rebound: ...


# --------------------------------------------------------------------------------------------
# Slot statuses, bands, staircase
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Status:
    kind: str  # flex | zero_flex | unknown | unavailable
    reason: str | None
    unavailable: UnavailableWindow | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        code = self.unavailable.reason_code.value if self.unavailable else None
        return (self.kind, code)


def _statuses(
    producer: FlexProducer, request: SurfaceRequest
) -> tuple[list[list[_Status]], list[list[np.ndarray | None]]]:
    status: list[list[_Status]] = []
    kw: list[list[np.ndarray | None]] = []
    for si, season in enumerate(request.seasons):
        row_s: list[_Status] = []
        row_k: list[np.ndarray | None] = []
        for s in range(SLOTS_PER_DAY):
            win = next(
                (
                    w
                    for w in request.unavailable
                    if set(w.months) & set(season.months)
                    and w.start_minute <= s * 30 < w.end_minute
                ),
                None,
            )
            if win is not None:
                row_s.append(_Status("unavailable", win.detail, win))
                row_k.append(None)
                continue
            a = producer.answer(si, s)
            row_s.append(_Status(a.status, a.reason))
            row_k.append(a.kw_by_duration if a.status == "flex" else None)
        status.append(row_s)
        kw.append(row_k)
    return status, kw


def _stack(rows: Sequence[np.ndarray | None]) -> np.ndarray:
    """The curves of slots that all hold one (a flex band never has a slot without a curve)."""
    held = [r for r in rows if r is not None]
    if len(held) != len(rows):
        raise ValueError("a flex band has a slot without a certified curve")
    return np.stack(held)


def _area(curve: np.ndarray) -> float:
    return float(curve[MIN_DURATION_MIN:].sum())


def propose_bands(
    status: list[list[_Status]], kw: list[list[np.ndarray | None]]
) -> tuple[tuple[int, int], ...]:
    """Half-hour slot ranges ``[a, b)`` of the proposed bands (G-c)."""
    bands = [(s, s + 1) for s in range(SLOTS_PER_DAY)]

    def sig(a: int, b: int) -> tuple[tuple[str, str | None], ...] | None:
        keys = {tuple(status[si][s].key for si in range(len(status))) for s in range(a, b)}
        return next(iter(keys)) if len(keys) == 1 else None

    def frontier(si: int, a: int, b: int) -> np.ndarray:
        return np.min(_stack([kw[si][s] for s in range(a, b)]), axis=0)

    def cost(i: int) -> float | None:
        (a, b), (c, e) = bands[i], bands[i + 1]
        if sig(a, e) is None:
            return None
        lost = 0.0
        for si in range(len(status)):
            if status[si][a].kind != "flex":
                continue
            fa, fb = frontier(si, a, b), frontier(si, c, e)
            lost += (b - a) * _area(fa) + (e - c) * _area(fb) - (e - a) * _area(np.minimum(fa, fb))
        return lost

    while True:
        costs = [(cost(i), i) for i in range(len(bands) - 1)]
        allowed = [(c, i) for c, i in costs if c is not None]
        if not allowed:
            break
        c, i = min(allowed)
        if c > 0 and len(bands) <= MAX_BANDS:
            break
        bands[i : i + 2] = [(bands[i][0], bands[i + 1][1])]
    return tuple(bands)


def staircase(curve: np.ndarray) -> list[tuple[int, int]]:
    """The (duration, kW) corner points of a kW-by-duration curve from 15 minutes, at most
    ``MAX_POINTS``."""
    pts = [
        (d, int(curve[d]))
        for d in range(MIN_DURATION_MIN, MAX_DURATION_MIN + 1)
        if curve[d] >= 1 and (d == MAX_DURATION_MIN or curve[d + 1] < curve[d])
    ]
    while len(pts) > MAX_POINTS:
        best: tuple[float, int] | None = None
        for i, (d, k) in enumerate(pts):
            prev = pts[i - 1][0] if i else 0
            nxt = pts[i + 1][1] if i + 1 < len(pts) else 0
            loss = (k - nxt) * (d - prev)
            if best is None or loss < best[0]:
                best = (loss, i)
        assert best is not None
        del pts[best[1]]
    return pts


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}{minute % 60:02d}"


def _slot_words(reasons: dict[str, list[int]]) -> str:
    parts = []
    for reason, ss in sorted(reasons.items()):
        span = ", ".join(f"{_hhmm(s * 30)}" for s in ss[:4]) + ("..." if len(ss) > 4 else "")
        parts.append(f"{reason} (slots from {span})" if len(reasons) > 1 else reason)
    return "; ".join(parts)


def _cell(
    producer: FlexProducer,
    si: int,
    slots: Sequence[int],
    status: list[_Status],
    kw: list[np.ndarray | None],
) -> Flex | ZeroFlex | Unavailable | Unknown:
    kinds = [status[s] for s in slots]
    for kind in ("unavailable", "unknown", "zero_flex"):
        hit = [s for s in slots if status[s].kind == kind]
        if not hit:
            continue
        by: dict[str, list[int]] = {}
        for s in hit:
            by.setdefault(status[s].reason or kind, []).append(s)
        words = _slot_words(by)
        if kind == "unavailable":
            w = next(k.unavailable for k in kinds if k.unavailable is not None)
            assert w is not None
            return Unavailable(kind="unavailable", reason_code=w.reason_code, detail=words)
        if kind == "unknown":
            return unknown(words)
        return ZeroFlex(kind="zero_flex", reason=words)
    curve = np.min(_stack([kw[s] for s in slots]), axis=0)
    pts = staircase(curve)
    if not pts:
        return ZeroFlex(
            kind="zero_flex",
            reason=f"no 1 kW hold of {MIN_DURATION_MIN} min is firm from every start in the band",
        )
    return Flex(
        kind="flex",
        curve=tuple(
            FlexPoint(duration_min=d, reduction_kw=k, rebound=producer.rebound(si, slots, d, k))
            for d, k in pts
        ),
    )


# --------------------------------------------------------------------------------------------
# Limits, hash, surface
# --------------------------------------------------------------------------------------------


def _limits(inputs: PlantInputs) -> ThermalLimits:
    ashrae = inputs.limits.ashrae
    lo, hi = binding_band(inputs)
    binding = TemperatureBand(point=MeasurementPoint.IT_INLET, min_tenths_c=lo, max_tenths_c=hi)
    sla = inputs.limits.customer_sla
    if binding == ashrae.temperature:
        source = LimitSource.ASHRAE
    elif isinstance(sla, CustomerSlaLimit) and binding == sla.temperature:
        source = LimitSource.CUSTOMER_SLA
    else:
        source = LimitSource.OPERATOR_MARGIN

    def timing(p: Stated | Unknown) -> int | Unknown:
        return int(p.value) if isinstance(p, Stated) else p

    return ThermalLimits(
        ashrae=ashrae,
        customer_sla=sla,
        binding=binding,
        binding_source=source,
        chiller_min_run_min=timing(inputs.min_run_min),
        chiller_min_off_min=timing(inputs.min_off_min),
    )


def _array_digest(a: np.ndarray | None) -> str | None:
    if a is None:
        return None
    return hashlib.sha256(np.ascontiguousarray(a, dtype="<f8").tobytes()).hexdigest()


def input_hash(
    inputs: PlantInputs,
    drivers: DriverSeries,
    request: SurfaceRequest,
    bands: Sequence[tuple[int, int]],
) -> str:
    """``sha256:`` of everything the surface depends on, the band grid included."""
    payload = {
        "model": MODEL,
        "method_version": METHOD_VERSION,
        "plant": inputs.model_dump(mode="json"),
        "drivers": {
            "start_utc": drivers.start_utc.isoformat(),
            "temperature_kind": drivers.temperature_kind,
            "provenance": drivers.provenance.model_dump(mode="json"),
            "heat_lo": _array_digest(drivers.heat_lo_kwth),
            "heat_hi": _array_digest(drivers.heat_hi_kwth),
            "temperature_lo": _array_digest(drivers.temperature_lo_c),
            "temperature_hi": _array_digest(drivers.temperature_hi_c),
        },
        "request": request.model_dump(mode="json"),
        "bands": [list(b) for b in bands],
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence_refs(inputs: PlantInputs, drivers: DriverSeries) -> tuple[EvidenceRef, ...]:
    refs: dict[str, EvidenceRef] = {}

    def visit(v: object) -> None:
        if isinstance(v, SiteEvidenced):
            refs[v.evidence.evidence_hash] = v.evidence
        elif isinstance(v, Stated | CoefficientSet):
            visit(v.provenance)
        elif isinstance(v, BaseModel):
            for name in type(v).model_fields:
                visit(getattr(v, name))
        elif isinstance(v, tuple):
            for x in v:
                visit(x)

    visit(inputs)
    visit(drivers.provenance)
    return tuple(refs[h] for h in sorted(refs))


@dataclass(frozen=True)
class SurfaceResult:
    """The IC-11 surface, the certificate behind it (None when the plant is unresolved), and
    the data request that would replace every unknown or stand-in figure."""

    surface: ThermalFlexAvailability
    certificate: Certificate | None
    data_request: PlantDataRequest | None
    stand_ins: tuple[str, ...]


class _Unresolved:
    """A producer for a plant that cannot be resolved: every slot unknown, with the reason."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def answer(self, season_index: int, slot: int) -> SlotAnswer:
        return SlotAnswer("unknown", None, self._reason)

    def rebound(
        self, season_index: int, slots: Sequence[int], duration_min: int, kw: int
    ) -> Rebound:
        raise AssertionError("an unresolved plant declares no point")


def build_surface(
    inputs: PlantInputs, drivers: DriverSeries, request: SurfaceRequest
) -> SurfaceResult:
    """The IC-11 surface for one plant. Unknown figures give unknown cells and a data request;
    stand-ins give a ``synthetic_fixture`` surface and a data request (D168)."""
    if (drivers.temperature_kind == "wet_bulb") != inputs.drives_on_wet_bulb:
        need = "wet_bulb" if inputs.drives_on_wet_bulb else "dry_bulb"
        raise ValueError(f"this plant is driven by the {need} (WP-THERM-04 method v1 §6.2)")
    res = resolve(inputs)
    certificate: Certificate | None = None
    producer: FlexProducer
    if isinstance(res, Resolved):
        envs = build_envelopes(drivers, request.seasons, request.min_days_per_month)
        certificate = certify(res.plant, envs)
        producer = certificate
        needed: set[str] = set(res.stand_ins)
    else:
        assert isinstance(res, Unresolved)
        parts = [f"{k} unknown ({why})" for k, why in res.missing] + list(res.inconsistent)
        producer = _Unresolved("plant not assessable: " + "; ".join(parts))
        needed = {k for k, _ in res.missing} | set(res.stand_ins)
        if res.inconsistent:
            needed.add("limits")
    stand_ins = set(res.stand_ins)
    if drivers.provenance.kind == "stand_in":
        stand_ins.add("drivers")
    needed |= stand_ins
    if isinstance(inputs.response, Unknown):
        needed.add("response")

    status, kw = _statuses(producer, request)
    if request.bands is not None:
        ranges = tuple((b.start_minute // 30, b.end_minute // 30) for b in request.bands)
    else:
        ranges = propose_bands(status, kw)
    h = input_hash(inputs, drivers, request, ranges)

    bands = tuple(
        TimeBand(band_id=f"{_hhmm(a * 30)}-{_hhmm(b * 30)}", start_minute=a * 30, end_minute=b * 30)
        for a, b in ranges
    )
    cells = tuple(
        AvailabilityCell(
            season_id=season.season_id,
            band_id=band.band_id,
            availability=_cell(producer, si, range(a, b), status[si], kw[si]),
        )
        for si, season in enumerate(request.seasons)
        for band, (a, b) in zip(bands, ranges, strict=True)
    )

    synthetic = bool(stand_ins) or inputs.site_id.startswith("SYNTHETIC-")
    if synthetic and not inputs.site_id.startswith("SYNTHETIC-"):
        site_id = f"SYNTHETIC-{h[7:19]}"
    else:
        site_id = inputs.site_id
    model = MODEL if not stand_ins else f"{MODEL} [stand-ins: {', '.join(sorted(stand_ins))}]"

    cap: int | Unknown
    if certificate is not None and drivers.temperature_lo_c is not None:
        assert drivers.temperature_hi_c is not None
        t_lo = float(np.nanmin(drivers.temperature_lo_c))
        t_hi = float(np.nanmax(drivers.temperature_hi_c))
        bad = curve_validity(certificate.plant, t_lo, t_hi)
        if bad is None:
            cap = math.ceil(max_power(certificate.plant, t_lo, t_hi))
        else:
            cap = unknown(f"the power curve is not usable on the driver range: {bad}")
    else:
        cap = unknown("the plant or the driving temperature is not resolved")

    surface = ThermalFlexAvailability(
        surface_id=request.surface_id,
        site_id=site_id,
        record_class="synthetic_fixture" if synthetic else "registered",
        topology=inputs.topology,
        cooling_electrical_capacity_kw=cap,
        issued_at=request.issued_at,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        clock=CLOCK,
        evidence_class=EvidenceClass.MODELLED,
        evidence=ModelledBasis(
            kind="modelled",
            model=model,
            model_version=METHOD_VERSION,
            input_hash=h,
            calibration_evidence=_evidence_refs(inputs, drivers),
        ),
        limits=_limits(inputs),
        response=inputs.response,
        seasons=request.seasons,
        bands=bands,
        cells=cells,
    )
    data_request = None
    if needed:
        topo = TopologyAssessment(
            status="supported",
            topology=inputs.topology,
            detail="as stated in the plant inputs",
        )
        data_request = plant_data_request(inputs.site_id, frozenset(needed), topo)
    return SurfaceResult(surface, certificate, data_request, tuple(sorted(stand_ins)))
