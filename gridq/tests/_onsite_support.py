"""Labelled **synthetic** on-site generation inputs for the WP-SITE-05 tests.

Every number is an illustrative test input chosen to exercise a code path: no rating, start time,
permit cap, run-hour balance, fuel figure or test schedule here describes a real genset, permit or
site. Every site id starts ``SYNTHETIC-`` and every model is ``synthetic_fixture``. Real genset data
per site is a person-only data ask (see the ``missing_onsite_asks`` tests), never one of these.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from fractions import Fraction
from typing import Any

import numpy as np
from tests import _adequacy_support as A

from gridq.adequacy.onsite import OnsiteGeneration, OnsiteGenerationModel
from gridq.adequacy.standby import GensetRun

SITE = A.SITE
UNKNOWN = {"kind": "unknown", "reason": "synthetic: not stated in this test"}


def genset(
    uid: str,
    *,
    kw: int | None = 400,
    start_s: int | None = 120,
    operation: str = "parallel_with_grid",
    cover: str = "permitted",
    technology: str = "diesel_genset",
) -> dict[str, Any]:
    return {
        "unit_id": uid,
        "technology": technology,
        "rated_kw": UNKNOWN if kw is None else kw,
        "start_to_load_s": UNKNOWN if start_s is None else start_s,
        "operation": operation,
        "curtailment_cover": cover,
    }


def limit(
    lid: str,
    units: Iterable[str],
    *,
    cap_s: int | None,
    window: tuple[str, int] | str = "calendar_year",
    counts: str = "all_running",
    used: tuple[date, int] | None = (date(2027, 1, 11), 0),
    basis: str = "mcpd",
) -> dict[str, Any]:
    w: dict[str, Any]
    if window == "calendar_year":
        w = {"kind": "calendar_year"}
    elif window == "unknown":
        w = UNKNOWN
    else:
        assert isinstance(window, tuple)
        w = {"kind": "rolling_days", "days": window[1]}
    return {
        "limit_id": lid,
        "unit_ids": sorted(units),
        "basis": basis,
        "cap_s": UNKNOWN if cap_s is None else cap_s,
        "window": w,
        "counts": counts,
        "used": UNKNOWN
        if used is None
        else {"kind": "used_at", "as_of": used[0].isoformat(), "seconds": used[1]},
    }


def store(
    sid: str,
    units: Iterable[str],
    *,
    seconds_at_rated: int | None = None,
    tank_ml: int | None = None,
    curves: dict[str, list[tuple[int, int]]] | None = None,
    continuous: bool = False,
    opening: Any = "full",
    lead_s: int | None = None,
) -> dict[str, Any]:
    endurance: dict[str, Any]
    if continuous:
        endurance = {"kind": "continuous_supply", "statement": "SYNTHETIC piped gas"}
    elif tank_ml is not None:
        endurance = {
            "kind": "tank_and_burn",
            "usable_ml": tank_ml,
            "curves": [
                {
                    "unit_id": u,
                    "points": [{"output_kw": k, "ml_per_hour": m} for k, m in pts],
                }
                for u, pts in sorted((curves or {}).items())
            ],
        }
    elif seconds_at_rated is not None:
        endurance = {"kind": "stated_endurance", "seconds_at_rated": seconds_at_rated}
    else:
        endurance = UNKNOWN
    na = {"kind": "not_applicable", "reason": "continuous supply"}
    if continuous:
        op: Any = na
        rs: Any = na
    else:
        op = (
            {"kind": "full"}
            if opening == "full"
            else UNKNOWN
            if opening is None
            else {"kind": "stated_level", "amount": opening}
        )
        rs = (
            {"kind": "no_resupply"}
            if lead_s is None
            else {"kind": "resupply_lead", "lead_s": lead_s}
        )
    return {
        "store_id": sid,
        "unit_ids": sorted(units),
        "endurance": endurance,
        "opening": op,
        "resupply": rs,
    }


def weekly_test(
    sid: str,
    units: Iterable[str],
    *,
    weekday: int,
    start_minute: int,
    duration_s: int,
    mode: str = "off_load",
) -> dict[str, Any]:
    return {
        "schedule_id": sid,
        "unit_ids": sorted(units),
        "recurrence": {"kind": "weekly", "iso_weekday": weekday},
        "local_start_minute": start_minute,
        "duration_s": duration_s,
        "mode": mode,
    }


def model(
    gensets: list[dict[str, Any]],
    *,
    limits: list[dict[str, Any]] | None = None,
    no_limit: Iterable[str] | None = None,
    tests: list[dict[str, Any]] | None = None,
    no_tests: Iterable[str] | None = None,
    stores: list[dict[str, Any]] | None = None,
    colocated: list[dict[str, Any]] | None = None,
    contingency: Any = None,
    detection_s: Any = 10,
    transfer_s: Any = 5,
    site: str = SITE,
    record_class: str = "synthetic_fixture",
) -> OnsiteGenerationModel:
    """A synthetic model. Units with no limit, test or store statement get the stated "none"
    (a no-limit statement, a no-test statement, a continuous supply) unless given."""
    ids = [g["unit_id"] for g in gensets]
    lims = limits or []
    limited = {u for lim in lims for u in lim["unit_ids"]}
    nl = sorted(no_limit) if no_limit is not None else [u for u in ids if u not in limited]
    tst = tests or []
    tested = {u for t in tst for u in t["unit_ids"]}
    nt = sorted(no_tests) if no_tests is not None else [u for u in ids if u not in tested]
    sts = stores if stores is not None else ([store("F-ALL", ids, continuous=True)] if ids else [])
    na = {"kind": "not_applicable", "reason": "no gensets"}
    data = {
        "site_id": site,
        "record_class": record_class,
        "statement": "assets_listed" if gensets or colocated else "no_onsite_generation",
        "gensets": gensets,
        "fuel_stores": sts,
        "run_limits": lims,
        "no_run_hour_limit": [{"unit_ids": nl, "reason": "SYNTHETIC: stated none"}] if nl else [],
        "test_runs": tst,
        "no_test_runs": [{"unit_ids": nt, "reason": "SYNTHETIC: stated none"}] if nt else [],
        "colocated": colocated or [],
        "start_contingency": (contingency or {"kind": "all_start"}) if gensets else na,
        "detection_s": detection_s if gensets else na,
        "transfer_s": transfer_s if gensets else na,
        "declared_by": {
            "name": "SYNTHETIC customer (fixture; not a real company)",
            "role": "customer",
            "reference": "SYNTH-GEN-0001",
        },
    }
    return OnsiteGenerationModel.model_validate_json(json.dumps(data))


def bundle(m: OnsiteGenerationModel, series: tuple[Any, ...] = ()) -> OnsiteGeneration:
    return OnsiteGeneration(model=m, series=series)


# --------------------------------------------------------------------------- golden + generation


def golden_generation(*, cap_s: int | None = 3600) -> OnsiteGeneration:
    """The SYNTHETIC genset added to `golden_run`: one 300 kW unit, 90 s to load, a calendar-year
    cap of ``cap_s`` seconds (0: never runs), counted from the period start, continuous fuel."""
    return bundle(
        model(
            [genset("G1", kw=300, start_s=90)],
            limits=[limit("L1", ["G1"], cap_s=cap_s, used=(date(2027, 1, 11), 0))],
        )
    )


def golden_run_with_generation(
    gen: OnsiteGeneration | None = None, *, skip: bool = True, evidence: tuple[Any, ...] = ()
) -> Any:
    """`golden_run`'s inputs, exactly, plus on-site generation."""
    from gridq.adequacy import run_adequacy, size_floor

    draws, m = 72, 2 * 1440
    minute = np.arange(m, dtype=np.int64)[None, :]
    draw = np.arange(draws, dtype=np.int64)[:, None]
    fac = 600_000 + 10_000 * (minute % 7) + 5_000 * draw
    it = (fac * 7) // 10
    load = A.MinuteLoadArray(
        site_id=SITE,
        record_class="synthetic_fixture",
        horizon_start=A.DAY0,
        facility_w=fac,
        it_w=it,
    )
    b = A.bess(initial=1400, operating=1600)
    policy = size_floor(
        A.requirement(fraction=Fraction(1, 2), seconds=1800), b, A.flat_partition(), load=load
    )
    ss = A.scenario_set(
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
        A.config(),
        as_of=date(2027, 1, 10),
        evidence=evidence,
        thermal=A.surface(),
        envelope=A.envelope(),
        skip=skip,
        generation=gen if gen is not None else golden_generation(),
    )


# --------------------------------------------------------------------------- independent recount


def recount_peak_s(
    runs: Iterable[GensetRun],
    lim: dict[str, Any],
    *,
    counted_from_min: int,
    opening_s: int,
    year_starts_min: Iterable[int],
    end_min: int,
) -> int:
    """Brute force, independent of the ledger: for every window (each calendar year segment, or
    every rolling window ending at every minute), the counted unit-seconds plus the opening use;
    return the largest. Minutes are relative to the period start."""
    counts_cover = lim["counts"] != "test_only"
    units = set(lim["unit_ids"])
    per_unit: dict[str, set[int]] = {}
    for r in runs:
        if r.unit_id not in units or (r.purpose == "cover" and not counts_cover):
            continue
        per_unit.setdefault(r.unit_id, set()).update(
            t for t in range(r.start_min, r.end_min) if t >= counted_from_min
        )
    minutes = sorted(t for s in per_unit.values() for t in s)  # unit-minutes, repeats kept
    if lim["window"]["kind"] == "calendar_year":
        edges = sorted({counted_from_min, *[y for y in year_starts_min if y > counted_from_min]})
        edges.append(end_min)
        best = 0
        for i, (a, b) in enumerate(zip(edges, edges[1:], strict=False)):
            total = 60 * sum(1 for t in minutes if a <= t < b) + (opening_s if i == 0 else 0)
            best = max(best, total)
        return best
    w = lim["window"]["days"] * 1440
    best = opening_s
    for t in range(counted_from_min, end_min):
        total = 60 * sum(1 for x in minutes if t - w < x <= t)
        if counted_from_min <= t < counted_from_min + w:
            total += opening_s
        best = max(best, total)
    return best
