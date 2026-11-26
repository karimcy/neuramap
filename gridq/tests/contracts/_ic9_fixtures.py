"""IC-9 synthetic fixtures: committed schema-test data for the compute turn-down contract.

Five declarations and one compiled timeline, in ``fixtures/ic9/`` (their own folder, so the 1.0.0
fixture globs and golden table one level up are undisturbed):

- ``ic9_declaration_gpu_campus``: a GPU campus (``SYNTHETIC-DC-GPU-CAMPUS``) offering three
  envelopes under two SLA classes. Two envelopes (checkpoint-and-pause, power-cap) are
  alternatives in one exclusion group under a deferred batch-training class; the third moves
  inference off site under a class whose energy does not return. The stacking cap binds below
  what the envelopes could stack to, and a retained IT floor is declared. A winter/summer x
  weekday/weekend x four-band template, with one unknown cell; a dated offer and a dated
  blackout override it.
- ``ic9_declaration_gpu_campus_successor``: a re-declaration of the same site that supersedes the
  first from 1 July 2027 (a deeper checkpoint envelope). With the first, it exercises `active_at`.
- ``ic9_declaration_relative_curve``: a neocloud site (``SYNTHETIC-DC-GPU-NEOCLOUD``) declaring
  its turn-down as a share of running IT load with a kW cap, under a slowed class whose energy
  return is unknown, on a trialled basis.
- ``ic9_declaration_clock_change_spring`` and ``ic9_declaration_clock_change_autumn``: one
  overnight envelope at a site (``SYNTHETIC-DC-GPU-CLOCK``) over the weeks the GB clock goes
  forward (28 March 2027) and back (31 October 2027).
- ``ic9_timeline_clock_change_spring``: the spring declaration compiled (`resolve`), for a
  consumer that reads the timeline and trusts it by hash with no clock logic of its own.

All values are synthetic. Every declaration is ``record_class: synthetic_fixture``; site, operator
and SLA class ids and labels start ``SYNTHETIC-``; every stated price is a
``synthetic_placeholder``, not an offer; evidence hashes are SHA-256 of a synthetic placeholder
text, not of any real document. The kW, durations, notices and SLA limits exercise the guards;
none describes a real operator, workload or trial. ``test_ic9_*`` asserts the committed files equal
a fresh build. Regenerate with ``python tests/contracts/_ic9_fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURES_IC9 = Path(__file__).resolve().parent / "fixtures" / "ic9"
CAMPUS = FIXTURES_IC9 / "ic9_declaration_gpu_campus.synthetic.json"
CAMPUS_SUCCESSOR = FIXTURES_IC9 / "ic9_declaration_gpu_campus_successor.synthetic.json"
RELATIVE = FIXTURES_IC9 / "ic9_declaration_relative_curve.synthetic.json"
CLOCK_SPRING = FIXTURES_IC9 / "ic9_declaration_clock_change_spring.synthetic.json"
CLOCK_AUTUMN = FIXTURES_IC9 / "ic9_declaration_clock_change_autumn.synthetic.json"
CLOCK_SPRING_TIMELINE = FIXTURES_IC9 / "ic9_timeline_clock_change_spring.synthetic.json"

CAMPUS_SITE = "SYNTHETIC-DC-GPU-CAMPUS"
CHECKPOINT = "env-checkpoint"
POWER_CAP = "env-power-cap"
MIGRATE = "env-migrate"
SHARE = "env-share-of-load"
OVERNIGHT = "env-overnight"
BATCH = "SYNTHETIC-CLASS-BATCH-TRAINING"
MOVABLE = "SYNTHETIC-CLASS-MOVABLE-INFERENCE"
THROTTLE = "SYNTHETIC-CLASS-THROTTLED"


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _placeholder_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC PLACEHOLDER: {label}".encode()).hexdigest()


def _contracts() -> Any:
    from gridq.contracts import compute_turndown

    return compute_turndown


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic fixture: {reason}"}


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": f"synthetic fixture: {reason}"}


def _evidence(label: str) -> dict[str, str]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": "1.1.0",
        "evidence_hash": _placeholder_hash(label),
    }


def _band(band_id: str, start: int, end: int) -> dict[str, Any]:
    return {"band_id": band_id, "start_minute": start, "end_minute": end}


def _offered(*envelope_ids: str) -> dict[str, Any]:
    return {"kind": "offered", "envelope_ids": sorted(envelope_ids)}


def _unavailable(code: str) -> dict[str, str]:
    return {"kind": "unavailable", "reason_code": code}


def _cells(
    seasons: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    bands: list[dict[str, Any]],
    grid: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    return [
        {
            "season_id": s["season_id"],
            "day_group_id": g["day_group_id"],
            "band_id": b["band_id"],
            "state": grid[s["season_id"]][g["day_group_id"]][i],
        }
        for s in seasons
        for g in groups
        for i, b in enumerate(bands)
    ]


def _absolute(*points: tuple[int, int]) -> dict[str, Any]:
    return {
        "kind": "absolute_kw",
        "points": [{"max_hold_min": h, "turn_down_kw": kw} for h, kw in points],
    }


def _response(notice_s: int, ramp_s: int) -> dict[str, Any]:
    return {"kind": "response_terms", "min_notice_s": notice_s, "ramp_s": ramp_s}


def _price(
    *,
    availability: dict[str, Any] | None = None,
    utilisation: dict[str, Any] | None = None,
    per_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A placeholder price: each component a placeholder amount, or not charged."""
    return {
        "kind": "price_offer",
        "currency": "GBP",
        "availability": availability or _na("no availability fee in this placeholder"),
        "utilisation": utilisation or _na("no utilisation fee in this placeholder"),
        "per_event": per_event or _na("no event fee in this placeholder"),
        "price_basis": {"kind": "synthetic_placeholder"},
    }


def _limits(events: Any, cumulative: Any, rest: Any) -> dict[str, Any]:
    return {
        "kind": "call_limits",
        "max_events": events,
        "max_cumulative": cumulative,
        "min_rest": rest,
    }


def _seal(data: dict[str, Any]) -> dict[str, Any]:
    """Fill ``resolved_hash`` from the draft's own compilation (`resolved_hash_of`)."""
    data["resolved_hash"] = _contracts().resolved_hash_of(data)
    return data


def _declaration(**fields: Any) -> dict[str, Any]:
    head = {"contract": "IC-9", "contract_version": "1.0.0", "record_class": "synthetic_fixture"}
    tail = {"measurement_point": "it_load", "clock": "Europe/London"}
    data = {**head, **fields, **tail}
    order = [
        "contract", "contract_version", "record_class", "declaration_id", "site_id",
        "operator_id", "issued_at", "valid_from", "valid_until", "supersedes",
        "measurement_point", "clock", "sla_classes", "envelopes", "availability",
        "resolved_hash", "stacking", "it_reference", "basis",
    ]  # fmt: skip
    data["resolved_hash"] = ""
    return _seal({k: data[k] for k in order})


# ------------------------------------------------------------------ the GPU campus


_SEASONS = [
    {"season_id": "winter", "months": [1, 2, 3, 10, 11, 12]},
    {"season_id": "summer", "months": [4, 5, 6, 7, 8, 9]},
]
_WEEK = [
    {"day_group_id": "weekday", "iso_weekdays": [1, 2, 3, 4, 5]},
    {"day_group_id": "weekend", "iso_weekdays": [6, 7]},
]
_BANDS = [
    _band("night", 0, 420),
    _band("day", 420, 960),
    _band("peak", 960, 1140),
    _band("evening", 1140, 1440),
]


def _campus_grid() -> dict[str, dict[str, list[dict[str, Any]]]]:
    every = _offered(CHECKPOINT, POWER_CAP, MIGRATE)
    training = _offered(CHECKPOINT, POWER_CAP)
    protected = _unavailable("workload_protected")
    return {
        "winter": {
            "weekday": [every, protected, every, training],
            "weekend": [every, training, training, training],
        },
        "summer": {
            "weekday": [every, protected, _offered(MIGRATE), training],
            "weekend": [every, training, training, _unknown("weekend evenings not yet assessed")],
        },
    }


def _campus_classes() -> list[dict[str, Any]]:
    return [
        {
            "class_id": BATCH,
            "operator_label": "SYNTHETIC-batch training, checkpoint and resume",
            "workload_action": "deferred",
            "energy_return": {"kind": "returns_later", "return_window_min": 240},
            "call_limits": _limits(
                {"kind": "event_budget", "max_events": 12, "window_days": 30},
                {"kind": "duration_budget", "max_minutes": 1440, "window_days": 30},
                {"kind": "minimum_rest", "minutes": 240},
            ),
            "sla_evidence": _evidence("service-level agreement, batch training class"),
        },
        {
            "class_id": MOVABLE,
            "operator_label": "SYNTHETIC-inference moved to another site",
            "workload_action": "moved",
            "energy_return": {"kind": "no_return"},
            "call_limits": _limits(
                _unknown("the event budget for moved inference is not yet agreed"),
                _na("no cumulative-duration budget"),
                {"kind": "minimum_rest", "minutes": 60},
            ),
            "sla_evidence": _unknown("no service-level document exists"),
        },
    ]


def _campus_envelopes(checkpoint_kw: int) -> list[dict[str, Any]]:
    return [
        {
            "envelope_id": CHECKPOINT,
            "sla_class_id": BATCH,
            "response": _response(900, 120),
            "price": _price(utilisation={"kind": "utilisation_price", "pence_per_mwh": 1000}),
            "curve": _absolute((30, checkpoint_kw), (120, 3000), (240, 2000)),
            "rebound": {
                "kind": "rebound_size",
                "recovery_permille": 1000,
                "max_rebound_kw": _unknown("the resume ramp is not yet measured"),
            },
            "exclusion_group": "training-pool",
        },
        {
            "envelope_id": MIGRATE,
            "sla_class_id": MOVABLE,
            "response": _response(1800, 600),
            "price": _price(
                availability={"kind": "availability_price", "pence_per_mw_per_hour": 500},
                per_event={"kind": "event_fee", "pence": 2000},
            ),
            "curve": _absolute((120, 1500), (480, 1000)),
            "rebound": _na("moved work does not return at this site"),
            "exclusion_group": _na("stacks with either training envelope"),
        },
        {
            "envelope_id": POWER_CAP,
            "sla_class_id": BATCH,
            "response": _response(60, 30),
            "price": _unknown("the operator has not stated a price for power capping"),
            "curve": _absolute((60, 2500), (600, 1500)),
            "rebound": {
                "kind": "rebound_size",
                "recovery_permille": _unknown("the catch-up energy is not yet measured"),
                "max_rebound_kw": _unknown("the catch-up ramp is not yet measured"),
            },
            "exclusion_group": "training-pool",
        },
    ]


_DATED_OFFER = {
    "override_id": "ovr-dated-offer-2027-07-06",
    "start": "2027-07-06T07:00:00Z",
    "end": "2027-07-06T15:00:00Z",
    "state": _offered(MIGRATE),
    "reason": "synthetic fixture: a dated offer of the migration envelope over a protected day",
}
_BLACKOUT = {
    "override_id": "ovr-blackout-2027-12-24",
    "start": "2027-12-24T00:00:00Z",
    "end": "2027-12-27T00:00:00Z",
    "state": _unavailable("operator_withdrawn"),
    "reason": "synthetic fixture: the operator withdraws every envelope over a holiday freeze",
}


def _campus(**fields: Any) -> dict[str, Any]:
    return _declaration(
        site_id=CAMPUS_SITE,
        operator_id="SYNTHETIC-OPERATOR-GPU",
        sla_classes=_campus_classes(),
        availability={
            "seasons": _SEASONS,
            "day_groups": _WEEK,
            "bands": _BANDS,
            "cells": _cells(_SEASONS, _WEEK, _BANDS, _campus_grid()),
            "overrides": [_DATED_OFFER, _BLACKOUT],
        },
        stacking={
            "kind": "stacking",
            "merit_order": [CHECKPOINT, POWER_CAP, MIGRATE],
            "max_concurrent_turn_down_kw": 5000,
        },
        it_reference={"kind": "it_reference", "retained_it_floor_kw": 2000},
        basis={
            "kind": "operator_declared",
            "supporting_evidence": [_evidence("operator workload inventory, GPU campus")],
        },
        **fields,
    )


def build_campus() -> dict[str, Any]:
    return _campus(
        declaration_id="ic9-synthetic-gpu-campus-2027",
        issued_at="2026-12-01T00:00:00Z",
        valid_from="2027-01-01T00:00:00Z",
        valid_until="2028-01-01T00:00:00Z",
        supersedes=_na("the first declaration for this site"),
        envelopes=_campus_envelopes(4000),
    )


def build_campus_successor() -> dict[str, Any]:
    c = _contracts()
    first = c.ComputeTurndownDeclaration.model_validate_json(json.dumps(build_campus()))
    return _campus(
        declaration_id="ic9-synthetic-gpu-campus-2027-h2",
        issued_at="2027-06-01T00:00:00Z",
        valid_from="2027-07-01T00:00:00Z",
        valid_until="2028-01-01T00:00:00Z",
        supersedes=first.content_hash(),
        envelopes=_campus_envelopes(4500),
    )


# ------------------------------------------------------------------ the relative curve


def build_relative() -> dict[str, Any]:
    seasons = [{"season_id": "all-year", "months": list(range(1, 13))}]
    groups = [{"day_group_id": "all-week", "iso_weekdays": [1, 2, 3, 4, 5, 6, 7]}]
    bands = [_band("night", 0, 420), _band("day", 420, 1440)]
    grid = {"all-year": {"all-week": [_offered(SHARE), _unavailable("outside_agreed_window")]}}
    return _declaration(
        declaration_id="ic9-synthetic-neocloud-share-2027h1",
        site_id="SYNTHETIC-DC-GPU-NEOCLOUD",
        operator_id="SYNTHETIC-OPERATOR-NEOCLOUD",
        issued_at="2026-12-01T00:00:00Z",
        valid_from="2027-01-01T00:00:00Z",
        valid_until="2027-07-01T00:00:00Z",
        supersedes=_na("the first declaration for this site"),
        sla_classes=[
            {
                "class_id": THROTTLE,
                "operator_label": "SYNTHETIC-power-capped clusters",
                "workload_action": "slowed",
                "energy_return": _unknown("whether slowed jobs run longer here is not known"),
                "call_limits": _limits(
                    _na("no event budget"),
                    _unknown("the cumulative-duration budget is not yet agreed"),
                    _na("no minimum rest"),
                ),
                "sla_evidence": _unknown("no service-level document exists"),
            }
        ],
        envelopes=[
            {
                "envelope_id": SHARE,
                "sla_class_id": THROTTLE,
                "response": _response(300, 60),
                "price": _price(utilisation={"kind": "utilisation_price", "pence_per_mwh": 800}),
                "curve": {
                    "kind": "share_of_it_load",
                    "points": [
                        {"max_hold_min": 30, "reduction_bp": 3000},
                        {"max_hold_min": 240, "reduction_bp": 2000},
                        {"max_hold_min": 600, "reduction_bp": 1000},
                    ],
                    "cap_kw": 8000,
                },
                "rebound": _unknown("the class's energy return is unknown"),
                "exclusion_group": _na("the only envelope"),
            }
        ],
        availability={
            "seasons": seasons,
            "day_groups": groups,
            "bands": bands,
            "cells": _cells(seasons, groups, bands, grid),
            "overrides": [],
        },
        stacking={
            "kind": "stacking",
            "merit_order": [SHARE],
            "max_concurrent_turn_down_kw": 8000,
        },
        it_reference={"kind": "it_reference", "retained_it_floor_kw": 1000},
        basis={
            "kind": "trialled",
            "trial_evidence": sorted(
                [_evidence("trial event log, neocloud"), _evidence("trial IT meter export")],
                key=lambda r: r["evidence_hash"],
            ),
            "baseline_method": _evidence("baseline method, neocloud trial"),
            "observed_from": "2026-09-01T00:00:00Z",
            "observed_until": "2026-11-01T00:00:00Z",
            "n_events": 6,
        },
    )


# ------------------------------------------------------------------ the clock-change weeks


def build_clock(valid_from: str, valid_until: str, declaration_id: str) -> dict[str, Any]:
    """One overnight envelope (local 00:00 to 07:00) at one site, over the given validity."""
    seasons = [{"season_id": "all-year", "months": list(range(1, 13))}]
    groups = [{"day_group_id": "all-week", "iso_weekdays": [1, 2, 3, 4, 5, 6, 7]}]
    bands = [_band("night", 0, 420), _band("day", 420, 1440)]
    grid = {"all-year": {"all-week": [_offered(OVERNIGHT), _unavailable("workload_protected")]}}
    return _declaration(
        declaration_id=declaration_id,
        site_id="SYNTHETIC-DC-GPU-CLOCK",
        operator_id="SYNTHETIC-OPERATOR-GPU",
        issued_at="2027-01-15T00:00:00Z",
        valid_from=valid_from,
        valid_until=valid_until,
        supersedes=_na("the only declaration for this window"),
        sla_classes=[
            {
                "class_id": BATCH,
                "operator_label": "SYNTHETIC-batch training, checkpoint and resume",
                "workload_action": "deferred",
                "energy_return": {"kind": "returns_later", "return_window_min": 480},
                "call_limits": _limits(
                    {"kind": "event_budget", "max_events": 3, "window_days": 7},
                    _na("no cumulative-duration budget"),
                    _na("no minimum rest"),
                ),
                "sla_evidence": _unknown("no service-level document exists"),
            }
        ],
        envelopes=[
            {
                "envelope_id": OVERNIGHT,
                "sla_class_id": BATCH,
                "response": _response(3600, 300),
                "price": _price(utilisation={"kind": "utilisation_price", "pence_per_mwh": 1000}),
                "curve": _absolute((60, 1200), (420, 800)),
                "rebound": {
                    "kind": "rebound_size",
                    "recovery_permille": 1000,
                    "max_rebound_kw": 1200,
                },
                "exclusion_group": _na("the only envelope"),
            }
        ],
        availability={
            "seasons": seasons,
            "day_groups": groups,
            "bands": bands,
            "cells": _cells(seasons, groups, bands, grid),
            "overrides": [],
        },
        stacking={
            "kind": "stacking",
            "merit_order": [OVERNIGHT],
            "max_concurrent_turn_down_kw": 1200,
        },
        it_reference={"kind": "it_reference", "retained_it_floor_kw": 0},
        basis={"kind": "operator_declared", "supporting_evidence": []},
    )


def build_clock_spring() -> dict[str, Any]:
    return build_clock(
        "2027-03-25T00:00:00Z", "2027-04-01T00:00:00Z", "ic9-synthetic-clock-spring-2027"
    )


def build_clock_autumn() -> dict[str, Any]:
    return build_clock(
        "2027-10-28T00:00:00Z", "2027-11-04T00:00:00Z", "ic9-synthetic-clock-autumn-2027"
    )


def build_clock_spring_timeline() -> dict[str, Any]:
    c = _contracts()
    declaration = c.ComputeTurndownDeclaration.model_validate_json(json.dumps(build_clock_spring()))
    data: dict[str, Any] = c.resolve(declaration).model_dump(mode="json")
    return data


BUILDERS = {
    CAMPUS: build_campus,
    CAMPUS_SUCCESSOR: build_campus_successor,
    RELATIVE: build_relative,
    CLOCK_SPRING: build_clock_spring,
    CLOCK_AUTUMN: build_clock_autumn,
    CLOCK_SPRING_TIMELINE: build_clock_spring_timeline,
}


def main() -> None:
    FIXTURES_IC9.mkdir(exist_ok=True)
    for path, build in BUILDERS.items():
        path.write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":
    main()
