"""Shared, labelled **synthetic** inputs for the ``gridq.adequacy`` tests (WP-SITE-04).

Every number here is an illustrative test input chosen to exercise a code path. None is a
default, a sourced figure, or a claim about any real site, battery or customer. Every site id
starts ``SYNTHETIC-`` and every record is ``synthetic_fixture``, except the few registered-class
objects built inline by the sign-off tests to prove a synthetic sign-off is refused for them.

The site matches the committed IC-11 and IC-3 synthetic fixtures (``SYNTHETIC-DC-CHILLED-WATER``,
valid through 2027), so a run can read both.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from gridq.adequacy import (
    AdequacyConfig,
    AutonomyRequirement,
    BessParameters,
    FloorPartition,
    MinuteLoadArray,
)
from gridq.contracts.curtailment import CurtailmentScenarioSet, RestrictionSeries
from gridq.contracts.dispatch_envelope import DispatchEnvelope
from gridq.contracts.evidence import EvidenceObject
from gridq.contracts.thermal_flex import ThermalFlexAvailability

SITE = "SYNTHETIC-DC-CHILLED-WATER"
FIX = Path(__file__).resolve().parent / "contracts" / "fixtures"
IC11 = FIX / "ic11_surface_chilled_water_modelled.synthetic.json"
IC3 = FIX / "ic3" / "ic3_envelope_dc_thermal.synthetic.json"
#: A winter Monday; the IC-11 and IC-3 fixtures are valid all of 2027.
DAY0 = date(2027, 1, 11)


def surface() -> ThermalFlexAvailability:
    return ThermalFlexAvailability.model_validate_json(IC11.read_text())


def envelope() -> DispatchEnvelope:
    return DispatchEnvelope.model_validate_json(IC3.read_text())


def _er(f: Fraction | int) -> dict[str, int]:
    f = Fraction(f)
    return {"numerator": f.numerator, "denominator": f.denominator}


def requirement(
    *,
    kw: int | None = 200,
    fraction: Fraction | None = None,
    basis: str = "it",
    seconds: int | None = 3600,
    statistic: dict[str, Any] | None = None,
    margin: dict[str, Any] | None = None,
    site: str = SITE,
    record_class: str = "synthetic_fixture",
    unknown: tuple[str, ...] = (),
) -> AutonomyRequirement:
    """A synthetic autonomy requirement. ``unknown`` names elements to leave Unknown."""
    u = {"kind": "unknown", "reason": "synthetic: not stated in this test"}
    protected: dict[str, Any]
    if fraction is not None:
        protected = {"kind": "modelled_series", "basis": basis, "fraction": _er(fraction)}
    else:
        protected = {"kind": "stated_kw", "kw": kw}
    data: dict[str, Any] = {
        "site_id": site,
        "record_class": record_class,
        "protected_load": protected,
        "coverage": "ride_through_to_standby",
        "duration": {"kind": "stated_duration", "seconds": seconds},
        "statistic": statistic or {"kind": "ensemble_maximum"},
        "margin": margin or {"kind": "not_applicable", "reason": "synthetic: no margin declared"},
        "declared_by": {
            "name": "SYNTHETIC customer (fixture; not a real company)",
            "role": "customer",
            "reference": "SYNTH-REQ-0001",
        },
    }
    for name in unknown:
        data[name] = u
    return AutonomyRequirement.model_validate_json(json.dumps(data))


def bess(
    *,
    usable: int = 2000,
    basis: str = "end_of_life",
    discharge_kw: int = 1000,
    charge_kw: int = 1000,
    eta: Fraction = Fraction(19, 20),
    eta_charge: Fraction | None = None,
    round_trip: bool = False,
    initial: int = 1500,
    operating: int = 1500,
    site: str = SITE,
    record_class: str = "synthetic_fixture",
    unknown: tuple[str, ...] = (),
) -> BessParameters:
    def eff(e: Fraction) -> dict[str, Any]:
        if round_trip:
            return {"kind": "round_trip_only", "eta_rt": _er(e)}
        return {"kind": "one_way", "eta": _er(e)}

    data: dict[str, Any] = {
        "site_id": site,
        "record_class": record_class,
        "usable_energy_kwh": usable,
        "usable_basis": basis,
        "discharge_rating_kw": discharge_kw,
        "charge_rating_kw": charge_kw,
        "discharge_efficiency": eff(eta),
        "charge_efficiency": eff(eta_charge if eta_charge is not None else eta),
        "initial_soe_kwh": initial,
        "operating_soe_kwh": operating,
    }
    for name in unknown:
        data[name] = {"kind": "unknown", "reason": "synthetic: not stated in this test"}
    return BessParameters.model_validate_json(json.dumps(data))


def flat_partition() -> FloorPartition:
    return FloorPartition.model_validate_json(
        json.dumps(
            {
                "seasons": [{"season_id": "all-year", "months": list(range(1, 13))}],
                "day_groups": [{"day_group_id": "all-week", "iso_weekdays": list(range(1, 8))}],
                "bands": [{"band_id": "all-day", "start_minute": 0, "end_minute": 1440}],
            }
        )
    )


def banded_partition() -> FloorPartition:
    """Night (00:00-08:00) and day (08:00-24:00): two cells, for floor step-ups."""
    return FloorPartition.model_validate_json(
        json.dumps(
            {
                "seasons": [{"season_id": "all-year", "months": list(range(1, 13))}],
                "day_groups": [{"day_group_id": "all-week", "iso_weekdays": list(range(1, 8))}],
                "bands": [
                    {"band_id": "night", "start_minute": 0, "end_minute": 480},
                    {"band_id": "day", "start_minute": 480, "end_minute": 1440},
                ],
            }
        )
    )


def minute_load(
    *,
    draws: int = 72,
    days: int = 3,
    seed: int = 7,
    facility_kw: tuple[int, int] = (500, 800),
    it_share: Fraction = Fraction(7, 10),
    start: date = DAY0,
    day_it_kw: int | None = None,
) -> MinuteLoadArray:
    """Synthetic one-minute load, integer watts. ``day_it_kw`` sets a flat IT load from 08:00 to
    24:00 (and 100 kW at night) for floor step-up tests."""
    rng = np.random.default_rng(seed)
    m = days * 1440
    fac = rng.integers(
        facility_kw[0] * 1000, facility_kw[1] * 1000, size=(draws, m), dtype=np.int64
    )
    it = (fac * it_share.numerator) // it_share.denominator
    if day_it_kw is not None:
        minute = np.arange(m) % 1440
        it = np.where(minute >= 480, day_it_kw * 1000, 100_000)[None, :].repeat(draws, axis=0)
        it = it.astype(np.int64)
        fac = np.maximum(fac, it + 50_000)
    return MinuteLoadArray(
        site_id=SITE,
        record_class="synthetic_fixture",
        horizon_start=start,
        facility_w=fac,
        it_w=it,
    )


def scenario_set(
    scenarios: list[tuple[str, Fraction, list[tuple[int, int, float, Any]]]],
    *,
    start: datetime = datetime(2027, 1, 11, tzinfo=UTC),
    n_half_hours: int = 48,
    reference_mw: float | None = 1.0,
    direction: str = "import",
    site: str = SITE,
) -> CurtailmentScenarioSet:
    """Synthetic IC-2 set. Each event is ``(start_minute, minutes, depth_mw, notice_s)``; a
    ``notice_s`` of ``None`` is Unknown. A minute series is always given."""
    sc = []
    m = n_half_hours * 30
    for sid, w, events in scenarios:
        dense = [0.0] * m
        evs = []
        for k0, dur, depth, notice in events:
            for k in range(k0, k0 + dur):
                dense[k] = depth
            evs.append(
                {
                    "start": (start + timedelta(minutes=k0)).isoformat().replace("+00:00", "Z"),
                    "end": (start + timedelta(minutes=k0 + dur)).isoformat().replace("+00:00", "Z"),
                    "mean_depth_mw": depth,
                    "peak_depth_mw": depth,
                    "notice_s": {"kind": "unknown", "reason": "synthetic"}
                    if notice is None
                    else float(notice),
                    "source_ref": {"kind": "not_applicable", "reason": "synthetic event"},
                }
            )
        hh = [sum(dense[30 * i : 30 * i + 30]) / 30 for i in range(n_half_hours)]
        sc.append(
            {
                "scenario_id": sid,
                "label": f"Synthetic scenario {sid}",
                "weight": {"numerator": w.numerator, "denominator": w.denominator},
                "period_start": start.isoformat().replace("+00:00", "Z"),
                "half_hourly": json.loads(RestrictionSeries.from_dense(1800, hh).model_dump_json()),
                "minute": json.loads(RestrictionSeries.from_dense(60, dense).model_dump_json()),
                "events": evs,
            }
        )
    sc.sort(key=lambda s: s["scenario_id"])
    synthetic = site.startswith("SYNTHETIC-")
    data = {
        "contract": "IC-2",
        "contract_version": "1.0.0",
        "set_id": "ic2-synthetic-adequacy-test",
        "site_id": site,
        "direction": direction,
        "signal_basis": "demand_import_restriction",
        "depth_definition": "synthetic: MW below the reference import capacity",
        "reference_capacity_mw": reference_mw
        if reference_mw is not None
        else {"kind": "unknown", "reason": "synthetic: not stated"},
        "connection_agreement": {"kind": "not_applicable", "reason": "synthetic fixture"},
        "evidence_class": "synthetic_fixture" if synthetic else "modelled",
        "provenance": {
            "kind": "synthetic_fixture",
            "statement": "Schema-test data; not a site anyone holds. Values are synthetic.",
            "modelled_on": "WP-SITE-04 adequacy tests",
        },
        "weight_basis": "model_probability",
        "weight_rule": "synthetic: stated test weights",
        "n_half_hours": n_half_hours,
        "scenarios": sc,
    }
    return CurtailmentScenarioSet.model_validate_json(json.dumps(data))


def config(**over: Any) -> AdequacyConfig:
    data: dict[str, Any] = {
        "resolution_s": 60,
        "seed": 11,
        "import_capacity": {"kind": "not_applicable", "reason": "the set states its capacity"},
    }
    data.update(over)
    return AdequacyConfig.model_validate_json(json.dumps(data))


def signoff(document_hash: str, **over: Any) -> EvidenceObject:
    """A synthetic critical-facilities sign-off of ``document_hash``, by default admissible."""
    data: dict[str, Any] = {
        "contract": "IC-4",
        "contract_version": "1.2.0",
        "record_class": "synthetic_fixture",
        "evidence_type": "critical_facilities_signoff",
        "title": "SYNTHETIC critical-facilities sign-off of the resilience floor",
        "issuer": {
            "name": "SYNTHETIC customer (fixture; not a real company)",
            "role": "customer",
            "reference": "SYNTH-CF-0001",
        },
        "document_hash": document_hash,
        "issued_on": "2027-01-04",
        "effective_from": "2027-01-04",
        "expiry": {"kind": "expires_on", "expires_on": "2028-01-04"},
        "scope": {"kind": "site", "site_id": SITE},
        "verification": {
            "kind": "verified",
            "verifier": "SYNTHETIC verifier",
            "verifier_role": "neura_internal",
            "method": "confirmed_with_issuer",
            "verified_on": "2027-01-05",
        },
    }
    for k, v in over.items():
        data[k] = v
    return EvidenceObject.model_validate_json(json.dumps(data))


def golden_run(*, skip: bool = True, evidence: tuple[EvidenceObject, ...] = ()) -> Any:
    """A fully explicit case (integer arrays from a formula, no generator), with thermal flex and
    the envelope: its hashes are pinned cross-platform in ``test_adequacy_reproducible.py``."""
    from gridq.adequacy import run_adequacy, size_floor

    draws, m = 72, 2 * 1440
    minute = np.arange(m, dtype=np.int64)[None, :]
    draw = np.arange(draws, dtype=np.int64)[:, None]
    fac = 600_000 + 10_000 * (minute % 7) + 5_000 * draw
    it = (fac * 7) // 10
    load = MinuteLoadArray(
        site_id=SITE, record_class="synthetic_fixture", horizon_start=DAY0, facility_w=fac, it_w=it
    )
    b = bess(initial=1400, operating=1600)
    policy = size_floor(
        requirement(fraction=Fraction(1, 2), seconds=1800), b, flat_partition(), load=load
    )
    ss = scenario_set(
        [
            ("g1", Fraction(3, 5), [(540, 75, 0.6, 900.0), (1000, 40, 1.0, None)]),
            ("g2", Fraction(2, 5), [(300, 200, 0.45, 0.0)]),
        ]
    )
    return run_adequacy(
        ss,
        load,
        policy,
        b,
        config(),
        as_of=date(2027, 1, 10),
        evidence=evidence,
        thermal=surface(),
        envelope=envelope(),
        skip=skip,
    )
