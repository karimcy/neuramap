"""``adequacy_simulator`` behaviour: refusals, data asks, the time base, thermal planning,
recharge and the exact estimators (D170, D171, D174)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from math import comb
from typing import Any

import pytest
from tests import _adequacy_support as A

from gridq.adequacy import run_adequacy, size_floor
from gridq.adequacy.estimators import (
    PPM,
    bootstrap_mean_interval,
    clopper_pearson_ppm,
    index_stream,
)
from gridq.adequacy.load import map_scenario_minutes
from gridq.adequacy.result import Computed, NotComputed
from gridq.adequacy.thermal import plan_flex
from gridq.contracts._base import not_applicable, unknown
from gridq.contracts.curtailment import RestrictionEvent

AS_OF = date(2027, 1, 10)


def _inputs(**bess_over: Any) -> tuple[Any, Any, Any]:
    load = A.minute_load()
    b = A.bess(**bess_over)
    return load, b, size_floor(A.requirement(), b, A.flat_partition(), load=load)


def _set(**over: Any) -> Any:
    return A.scenario_set([("a", Fraction(1), [(600, 60, 0.8, 600.0)])], **over)


# --------------------------------------------------------------------------- refusals and asks


def test_export_set_is_refused() -> None:
    load, b, p = _inputs()
    with pytest.raises(ValueError, match="export"):
        run_adequacy(_set(direction="export"), load, p, b, A.config(), as_of=AS_OF)


def test_site_mismatch_is_refused() -> None:
    load, b, p = _inputs()
    other = A.scenario_set([("a", Fraction(1), [])], site="SYNTHETIC-OTHER")
    with pytest.raises(ValueError, match="names site"):
        run_adequacy(other, load, p, b, A.config(), as_of=AS_OF)


def test_too_few_draws_is_refused() -> None:
    load = A.minute_load(draws=10)
    b = A.bess()
    p = size_floor(A.requirement(), b, A.flat_partition(), load=load)
    with pytest.raises(ValueError, match="draws"):
        run_adequacy(_set(), load, p, b, A.config(), as_of=AS_OF)


def test_bess_must_be_the_sized_one() -> None:
    load, _b, p = _inputs()
    with pytest.raises(ValueError, match="sized from"):
        run_adequacy(_set(), load, p, A.bess(initial=10), A.config(), as_of=AS_OF)


def test_unknown_reference_capacity_asks_then_uses_a_stated_one() -> None:
    load, b, p = _inputs()
    ss = _set(reference_mw=None)
    r = run_adequacy(ss, load, p, b, A.config(), as_of=AS_OF)
    assert isinstance(r.outcome, NotComputed)
    assert any("import capacity" in a.request for a in r.data_requests)
    stated = {"kind": "stated_import_capacity", "kw": 1000, "source": "SYNTHETIC test input"}
    r2 = run_adequacy(ss, load, p, b, A.config(import_capacity=stated), as_of=AS_OF)
    assert isinstance(r2.outcome, Computed)


@pytest.mark.parametrize(
    "field", ["charge_rating_kw", "charge_efficiency", "initial_soe_kwh", "operating_soe_kwh"]
)
def test_unknown_simulation_field_is_not_computed_with_an_ask(field: str) -> None:
    load, b, p = _inputs(unknown=(field,))
    r = run_adequacy(_set(), load, p, b, A.config(), as_of=AS_OF)
    assert isinstance(r.outcome, NotComputed) and r.data_requests
    assert r.approval.kind == "unsigned" and not r.approved_for_use


def test_indeterminate_floor_gives_no_lolp_or_eue() -> None:
    load = A.minute_load()
    b = A.bess()
    p = size_floor(A.requirement(unknown=("duration",)), b, A.flat_partition(), load=load)
    r = run_adequacy(_set(), load, p, b, A.config(), as_of=AS_OF)
    assert isinstance(r.outcome, NotComputed)
    assert "the resilience floor is indeterminate" in r.outcome.reasons
    assert r.approval.reason.value == "floor_unknown"  # type: ignore[union-attr]


def test_scenario_outside_the_load_horizon_asks_or_takes_an_anchor() -> None:
    load, b, p = _inputs()
    ss = _set(start=datetime(2031, 6, 1, tzinfo=UTC))
    r = run_adequacy(ss, load, p, b, A.config(), as_of=AS_OF)
    assert isinstance(r.outcome, NotComputed)
    assert any("load anchor" in a.request for a in r.data_requests)
    anchor = {"scenario_id": "a", "load_anchor_date": A.DAY0.isoformat()}
    r2 = run_adequacy(ss, load, p, b, A.config(anchors=[anchor]), as_of=AS_OF)
    assert isinstance(r2.outcome, Computed)
    assert r2.outcome.mappings[0].anchor_basis == "stated"


def test_envelope_must_cover_the_study_period() -> None:
    start = date(2026, 12, 30)
    load = A.minute_load(start=start)
    b = A.bess()
    p = size_floor(A.requirement(), b, A.flat_partition(), load=load)
    ss = _set(start=datetime(2026, 12, 30, tzinfo=UTC))
    r = run_adequacy(ss, load, p, b, A.config(), as_of=AS_OF, envelope=A.envelope())
    assert isinstance(r.outcome, NotComputed)
    assert any("dispatch envelope" in a.request for a in r.data_requests)


# --------------------------------------------------------------------------- time base


@pytest.mark.parametrize(
    ("day", "skipped", "repeated"),
    [(datetime(2027, 3, 28, tzinfo=UTC), 60, 0), (datetime(2027, 10, 31, tzinfo=UTC), 0, 60)],
)
def test_clock_change_days_are_counted(day: datetime, skipped: int, repeated: int) -> None:
    idx, s, r = map_scenario_minutes(day, 1440, horizon_start=day.date(), shift_days=0)
    assert (s, r) == (skipped, repeated)
    assert idx[0] == (0 if day.month == 3 else 60)  # 00:00 UTC is 01:00 BST in October


# --------------------------------------------------------------------------- thermal planning


def _event(k0: int, dur: int, notice: Any) -> RestrictionEvent:
    t0 = datetime(2027, 1, 11, tzinfo=UTC) + timedelta(minutes=k0)
    return RestrictionEvent(
        start=t0,
        end=t0 + timedelta(minutes=dur),
        mean_depth_mw=0.5,
        peak_depth_mw=0.5,
        notice_s=notice,
        source_ref=not_applicable("synthetic event"),
    )


def _plan(e: RestrictionEvent, *, recovery: int = 0, surface: Any = None, start: Any = None) -> Any:
    return plan_flex(
        surface or A.surface(),
        e,
        event_index=0,
        period_start=start or datetime(2027, 1, 11, tzinfo=UTC),
        shift=timedelta(0),
        recovery_until_minute=recovery,
    )


def test_flex_waits_for_the_surface_notice() -> None:
    c = _plan(_event(600, 60, 0.0))
    assert c.outcome == "called" and c.start_minute == 605 and c.ramp_min == 5
    assert _plan(_event(600, 60, 600.0)).start_minute == 600


def test_unknown_notice_is_treated_as_zero() -> None:
    c = _plan(_event(600, 60, unknown("synthetic: notice not stated")))
    assert c.notice_unknown_treated_as_zero and c.start_minute == 605


def test_event_shorter_than_the_notice_gets_nothing() -> None:
    assert _plan(_event(600, 3, 0.0)).reason == "notice_too_short"


def test_no_call_inside_the_previous_recovery() -> None:
    assert _plan(_event(600, 60, 600.0), recovery=700).reason == "in_recovery"


def test_zero_flex_start_cell_gets_nothing() -> None:
    start = datetime(2027, 7, 12, tzinfo=UTC)
    t0 = start + timedelta(minutes=13 * 60)  # 14:00 BST: summer afternoon, zero flex
    e = RestrictionEvent(
        start=t0,
        end=t0 + timedelta(minutes=30),
        mean_depth_mw=0.5,
        peak_depth_mw=0.5,
        notice_s=3600.0,
        source_ref=not_applicable("synthetic event"),
    )
    assert _plan(e, start=start).reason == "start_cell_zero_flex"


def test_response_terms_unknown_gets_nothing() -> None:
    s = A.surface()
    s2 = type(s).model_validate_json(
        s.model_dump_json().replace(
            '"response":{"kind":"response_terms","min_notice_min":5,"ramp_min":5}',
            '"response":{"kind":"unknown","reason":"not stated"}',
        )
    )
    assert _plan(_event(600, 60, 600.0), surface=s2).reason == "response_terms_unknown"


def test_long_event_holds_only_the_longest_point() -> None:
    s = A.surface()
    c = _plan(_event(600, 1000, 600.0))
    cell = next(x for x in s.cells if (x.season_id, x.band_id) == (c.season_id, c.band_id))
    longest = cell.availability.curve[-1]  # type: ignore[union-attr]
    assert c.hold_min == longest.duration_min and c.reduction_kw == longest.reduction_kw


# --------------------------------------------------------------------------- recharge


def test_recharge_feasible_and_infeasible_gaps() -> None:
    load, b, p = _inputs(initial=900, operating=900, charge_kw=200)
    ss = A.scenario_set(
        [
            ("close", Fraction(1, 2), [(100, 120, 1.0, 0.0), (240, 30, 1.0, 0.0)]),
            ("far", Fraction(1, 2), [(100, 30, 0.9, 0.0), (900, 30, 1.0, 0.0)]),
        ]
    )
    r = run_adequacy(ss, load, p, b, A.config(), as_of=AS_OF)
    assert isinstance(r.outcome, Computed)
    rec = r.outcome.runs[0].recharge
    assert rec.k_infeasible == 72 and rec.k_censored == 144  # type: ignore[union-attr]
    assert rec.worst_gap.scenario_id == "close"  # type: ignore[union-attr]
    assert rec.min_margin_s > 0 and rec.max_shortfall_wh > 0  # type: ignore[union-attr]
    assert rec.p_any_infeasible_gap.as_fraction() == Fraction(1, 2)  # type: ignore[union-attr]


def test_headline_and_bounds() -> None:
    load, b, p = _inputs()
    r = run_adequacy(
        _set(), load, p, b, A.config(), as_of=AS_OF, thermal=A.surface(), envelope=A.envelope()
    )
    assert isinstance(r.outcome, Computed)
    assert r.outcome.headline == ("envelope_adversary", "ic11")
    assert {(x.posture, x.thermal, x.bound) for x in r.outcome.runs} == {
        ("reserve_held", "off", "no_trading"),
        ("reserve_held", "ic11", "no_trading"),
        ("envelope_adversary", "off", "every_envelope_compliant_dispatch"),
        ("envelope_adversary", "ic11", "every_envelope_compliant_dispatch"),
    }
    assert r.outcome.runs[2].recharge.kind == "not_applicable"  # type: ignore[union-attr]
    assert r.outcome.runs[0].eue.per_year_ws.kind == "not_applicable"  # type: ignore[union-attr]
    assert r.outcome.interval_scope == "load_draw_sampling_error_only"


# --------------------------------------------------------------------------- estimators


def _upper_tail(n: int, k: int, p: Fraction) -> Fraction:
    return sum((comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1)), Fraction(0))


@pytest.mark.parametrize(("k", "n"), [(0, 72), (1, 72), (36, 72), (71, 72), (72, 72), (3, 10)])
def test_clopper_pearson_is_exact_and_outward(k: int, n: int) -> None:
    alpha = Fraction(1, 20)
    lo, hi = clopper_pearson_ppm(k, n, alpha)
    assert 0 <= lo <= hi <= PPM
    if k > 0:
        assert _upper_tail(n, k, Fraction(lo, PPM)) <= alpha / 2
        assert _upper_tail(n, k, Fraction(lo + 1, PPM)) > alpha / 2
    if k < n:
        lower_tail = 1 - _upper_tail(n, k + 1, Fraction(hi, PPM))
        assert lower_tail <= alpha / 2
        assert 1 - _upper_tail(n, k + 1, Fraction(hi - 1, PPM)) > alpha / 2
    lo2, hi2 = clopper_pearson_ppm(n - k, n, alpha)
    assert (lo, hi) == (PPM - hi2, PPM - lo2)


def test_index_stream_is_seeded_and_uniform_in_range() -> None:
    a = index_stream(5, 72, 5000)
    assert a == index_stream(5, 72, 5000) and a != index_stream(6, 72, 5000)
    assert min(a) == 0 and max(a) == 71


def test_bootstrap_brackets_the_mean() -> None:
    values = list(range(0, 720, 10))
    lo, hi = bootstrap_mean_interval(values, seed=1)
    assert lo < sum(values) < hi
