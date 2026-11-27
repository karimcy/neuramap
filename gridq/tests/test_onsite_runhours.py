"""WP-SITE-05 acceptance 1: run-hour limits are never exceeded in simulation.

Three layers, each independent of the ledger it checks:

1. a property test over random SYNTHETIC fleets, limits, opening balances, tests and events: an
   independent brute-force recount of every window from the granted runs never exceeds a cap, and
   equals the ledger's own reported peak; fuel likewise;
2. a structural test: `GensetRun` is constructed only in `RunLedger._grant`;
3. mutants: a ledger that skips a limit, misreads the window by one minute, or leaves tests
   uncounted lets the recount find a breach.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import random
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests import _adequacy_support as A
from tests import _onsite_support as O

from gridq.adequacy import simulator as sim
from gridq.adequacy import standby
from gridq.adequacy.floor import size_floor
from gridq.adequacy.standby import GensetRun, RunLedger, ScenarioGeneration, plan_generation

CASES = 40
START = datetime(2027, 1, 12, tzinfo=UTC)


def _prepared(events: list[tuple[int, int, float, Any]], *, seed: int = 7) -> Any:
    load = A.minute_load(days=3, seed=seed)
    b = A.bess()
    policy = size_floor(A.requirement(), b, A.flat_partition(), load=load)
    ss = A.scenario_set([("s1", Fraction(1), events)], start=START)
    p = sim._prepare(ss.scenarios[0], load, policy, A.config(), 1_000_000)
    return p, load


def _plan(gen: Any, events: list[tuple[int, int, float, Any]], seed: int = 7) -> ScenarioGeneration:
    p, load = _prepared(events, seed=seed)
    base = sim.net_after_flex(p, ())
    _arrays, record, _asks = plan_generation(
        gen, p, base, horizon_start=load.identity.horizon_start
    )
    return record


def _check(model_kwargs: dict[str, Any], record: ScenarioGeneration, m: int) -> int:
    """Recount every enforced limit; return how many bound (peak == cap)."""
    bound = 0
    by_id = {lim["limit_id"]: lim for lim in model_kwargs.get("limits", [])}
    for use in record.limits:
        if use.status != "enforced":
            continue
        lim = by_id[use.limit_id]
        assert isinstance(use.counted_from_min, int) and isinstance(use.opening_s, int)
        got = O.recount_peak_s(
            record.runs,
            lim,
            counted_from_min=use.counted_from_min,
            opening_s=use.opening_s,
            year_starts_min=(),
            end_min=m,
        )
        assert isinstance(use.cap_s, int)
        assert got <= max(use.cap_s, use.opening_s), (use.limit_id, got, use.cap_s)
        assert got == use.peak_s, (use.limit_id, got, use.peak_s)
        bound += got == use.cap_s
    for st in model_kwargs.get("stores", []):
        if st["endurance"]["kind"] != "stated_endurance" or st["resupply"]["kind"] != "no_resupply":
            continue
        unit_minutes = {
            (r.unit_id, t)
            for r in record.runs
            if r.unit_id in st["unit_ids"]
            for t in range(max(0, r.start_min), r.end_min)
        }
        opening = st["endurance"]["seconds_at_rated"]
        if st["opening"]["kind"] == "stated_level":
            opening = min(opening, st["opening"]["amount"])
        assert 60 * len(unit_minutes) <= opening, (st["store_id"], len(unit_minutes), opening)
    return bound


def _random_case(rng: random.Random) -> tuple[dict[str, Any], list[tuple[int, int, float, Any]]]:
    units = [f"G{i}" for i in range(rng.randint(1, 3))]
    gensets = [
        O.genset(u, kw=rng.choice([150, 250, 400]), start_s=rng.choice([0, 45, 300])) for u in units
    ]
    limits = []
    for j in range(rng.randint(1, 3)):
        members = sorted(rng.sample(units, rng.randint(1, len(units))))
        window: Any = "calendar_year" if rng.random() < 0.5 else ("rolling_days", rng.randint(1, 2))
        as_of = date(2027, 1, 12) - timedelta(days=rng.randint(0, 3))
        limits.append(
            O.limit(
                f"L{j}",
                members,
                cap_s=rng.choice([0, 600, 1800, 3600, 7200]),
                window=window,
                counts=rng.choice(["all_running", "test_only", "non_emergency"]),
                used=(as_of, rng.choice([0, 300, 1200])),
            )
        )
    tests = []
    if rng.random() < 0.7:
        tests.append(
            O.weekly_test(
                "T1",
                sorted(rng.sample(units, rng.randint(1, len(units)))),
                weekday=rng.randint(1, 7),
                start_minute=rng.randrange(0, 1440, 15),
                duration_s=rng.choice([600, 1800, 3600]),
                mode=rng.choice(["off_load", "load_bank", "site_load"]),
            )
        )
    stores = []
    if rng.random() < 0.5:
        stores.append(
            O.store(
                "F1",
                units,
                seconds_at_rated=rng.choice([1200, 3600, 9000]),
                opening=rng.choice(["full", 900]),
            )
        )
    else:
        stores.append(O.store("F1", units, continuous=True))
    contingency = (
        {"kind": "all_start"} if rng.random() < 0.6 else {"kind": "largest_units_out", "k": 1}
    )
    kwargs = {
        "limits": limits,
        "tests": tests,
        "stores": stores,
        "contingency": contingency,
    }
    events = []
    t = rng.randint(0, 120)
    for _ in range(rng.randint(1, 4)):
        dur = rng.randint(10, 180)
        if t + dur >= 1440:
            break
        events.append((t, dur, rng.choice([0.4, 0.6, 0.9]), rng.choice([None, 0.0, 600.0])))
        t += dur + rng.randint(5, 300)
    return {"gensets": gensets, **kwargs}, events


def _build(case: dict[str, Any]) -> Any:
    kw = {k: v for k, v in case.items() if k != "gensets"}
    return O.bundle(O.model(case["gensets"], **kw))


def test_run_hour_limits_are_never_exceeded_property() -> None:
    rng = random.Random(20261126)
    bound = granted = 0
    for _ in range(CASES):
        case, events = _random_case(rng)
        record = _plan(_build(case), events)
        bound += _check(case, record, 1440)
        granted += sum(r.end_min - r.start_min for r in record.runs)
    # The generator exercises binding limits and real running, not only refusals.
    assert bound >= 5 and granted > 0


def test_a_binding_cap_cuts_cover_and_the_kernel_never_sees_more() -> None:
    gen = O.bundle(
        O.model(
            [O.genset("G1", kw=400, start_s=60)],
            limits=[O.limit("L1", ["G1"], cap_s=1800, used=(date(2027, 1, 12), 600))],
        )
    )
    p, load = _prepared([(100, 60, 0.9, 0.0)])
    arrays, record, _ = plan_generation(
        gen, p, sim.net_after_flex(p, ()), horizon_start=load.identity.horizon_start
    )
    (run,) = record.runs
    # No notice: called at the event start; 1800 s cap less 600 s used is 20 minutes.
    assert (run.start_min, run.end_min, run.cut_by) == (100, 120, ("L1",))
    on = np.flatnonzero(arrays.genset_w)
    assert on.min() == 101 and on.max() == 119


def test_opening_use_is_never_pro_rated_and_an_unknown_one_gives_no_credit() -> None:
    late = O.bundle(
        O.model(
            [O.genset("G1")],
            limits=[O.limit("L1", ["G1"], cap_s=3600, used=(date(2027, 1, 20), 0))],
        )
    )
    record = _plan(late, [(100, 30, 0.9, 0.0)])
    assert record.units[0].credited is False
    assert "dated after the period start" in record.units[0].reasons[0]
    assert all(r.purpose != "cover" for r in record.runs)


def test_a_stale_opening_balance_is_an_advisory_finding_and_tests_pre_roll() -> None:
    gen = O.bundle(
        O.model(
            [O.genset("G1")],
            limits=[
                O.limit(
                    "L1",
                    ["G1"],
                    cap_s=900,
                    window=("rolling_days", 7),
                    used=(date(2027, 1, 9), 300),
                )
            ],
            tests=[O.weekly_test("T1", ["G1"], weekday=7, start_minute=600, duration_s=600)],
        )
    )
    record = _plan(gen, [(100, 30, 0.9, 0.0)])
    codes = {f.code: f.severity for f in record.findings}
    assert codes["OPENING_BALANCE_STALE"] == "advisory"
    # Sunday 10 January 2027 10:00 GMT is pre-rolled (before the Tuesday period start).
    assert any(r.purpose == "test" and r.start_min < 0 for r in record.runs)
    assert record.preroll_min == 3 * 1440


def test_a_test_schedule_over_its_limit_is_nonconforming() -> None:
    gen = O.bundle(
        O.model(
            [O.genset("G1")],
            limits=[O.limit("L1", ["G1"], cap_s=300, counts="test_only")],
            tests=[O.weekly_test("T1", ["G1"], weekday=2, start_minute=600, duration_s=1800)],
        )
    )
    record = _plan(gen, [(100, 30, 0.9, 0.0)])
    (f,) = [f for f in record.findings if f.code == "TEST_SCHEDULE_EXCEEDS_LIMIT"]
    assert f.severity == "nonconforming"
    (t,) = [r for r in record.runs if r.purpose == "test"]
    assert t.end_min - t.start_min == 5 and t.cut_by == ("L1",)


def test_fuel_limits_running_and_a_resupply_refills() -> None:
    base: dict[str, Any] = {
        "limits": [],
    }
    no_refill = O.bundle(
        O.model(
            [O.genset("G1", start_s=0)],
            stores=[O.store("F1", ["G1"], seconds_at_rated=1200)],
            **base,
        )
    )
    events = [(100, 30, 0.9, 0.0), (400, 30, 0.9, 0.0)]
    r1 = _plan(no_refill, events)
    assert sum(r.end_min - r.start_min for r in r1.runs) == 20
    refill = O.bundle(
        O.model(
            [O.genset("G1", start_s=0)],
            stores=[O.store("F1", ["G1"], seconds_at_rated=1200, lead_s=3600)],
            **base,
        )
    )
    r2 = _plan(refill, events)
    assert sum(r.end_min - r.start_min for r in r2.runs) == 40
    assert r2.stores[0].refills == 1


def test_tank_and_burn_follows_the_curve() -> None:
    gen = O.bundle(
        O.model(
            [O.genset("G1", kw=400, start_s=0)],
            stores=[
                O.store("F1", ["G1"], tank_ml=60_000, curves={"G1": [(0, 6_000), (400, 120_000)]})
            ],
        )
    )
    record = _plan(gen, [(100, 120, 0.9, 0.0)])
    (run,) = record.runs
    # At least 2000 ml/min at any share of a 400 kW unit; 60 litres last at most 30 minutes.
    assert 0 < run.end_min - run.start_min <= 30 and run.cut_by == ("F1",)
    use = record.stores[0]
    assert isinstance(use.peak_segment_use, int) and use.peak_segment_use <= 60_000


# --------------------------------------------------------------------------- structure


def test_genset_run_is_constructed_only_in_grant() -> None:
    src = Path(inspect.getsourcefile(standby) or "").read_text()
    tree = ast.parse(src)
    owners: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "GensetRun"
                ):
                    owners.append(node.name)
    assert owners == ["_grant"]
    tests_dir = Path(__file__).parent
    for path in (tests_dir.parent / "src" / "gridq").rglob("*.py"):
        if path.name != "standby.py":
            assert "GensetRun(" not in path.read_text(), path


# --------------------------------------------------------------------------- mutants


def _ledger(window_min: int | None, cap_s: int, counts_test: bool = True) -> RunLedger:
    lim = standby._Limit(
        limit_id="L1",
        units=("G1",),
        cap_s=cap_s,
        window_min=window_min,
        counts_test=counts_test,
        counts_cover=True,
        start=0,
        opening_s=0,
        opening_at=0,
    )
    return RunLedger(length=60, p0=0, limits=[lim], stores=[], year_starts=())


def _brute(runs: list[GensetRun], window_min: int | None) -> int:
    mins = sorted(t for r in runs for t in range(r.start_min, r.end_min))
    if window_min is None:
        return 60 * len(mins)
    return max((60 * sum(1 for x in mins if t - window_min < x <= t) for t in range(60)), default=0)


def _scenario(ledger: RunLedger) -> list[GensetRun]:
    z = np.zeros(10, dtype=np.int64)
    ledger._grant("G1", 0, 3, "test", "T1", z, None)
    ledger._grant("G1", 9, 12, "cover", "event:0", z, 9)
    return ledger.runs


def test_the_unmutated_ledger_holds_in_the_mutant_scenarios() -> None:
    assert _brute(_scenario(_ledger(10, 180)), 10) <= 180
    assert _brute(_scenario(_ledger(None, 180)), None) <= 180


def test_mutant_skipping_a_limit_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunLedger, "_breaches", lambda self, *a, **k: ())
    assert _brute(_scenario(_ledger(None, 180)), None) > 180


def test_mutant_window_off_by_one_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = RunLedger.peak_s

    def short_window(self: RunLedger, lim: Any, extra: Any = None) -> int:
        w = lim.window_min - 1 if lim.window_min else lim.window_min
        return orig(self, dataclasses.replace(lim, window_min=w), extra)

    monkeypatch.setattr(RunLedger, "peak_s", short_window)
    assert _brute(_scenario(_ledger(10, 180)), 10) > 180


def test_mutant_leaving_tests_uncounted_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = RunLedger._counted

    def no_tests(self: RunLedger, lim: Any, extra: Any = None) -> np.ndarray:
        return orig(self, dataclasses.replace(lim, counts_test=False), extra)

    monkeypatch.setattr(RunLedger, "_counted", no_tests)
    assert _brute(_scenario(_ledger(None, 180)), None) > 180
