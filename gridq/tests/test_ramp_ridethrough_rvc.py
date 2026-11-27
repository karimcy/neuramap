"""WP-SITE-06: the RVC screen's three exact tiers against P28's 3 % limit (D199).

All inputs are SYNTHETIC (``tests/_ramp_ridethrough_support.py``): a 5 MW / 1.65 Mvar BESS at a
fixture PCC. The fault levels and X/R ratios are test inputs, not any network's figures.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from _ramp_ridethrough_support import declaration, study, unknown
from gridq.ops.dossier_model import AskHolder
from gridq.ramp_ridethrough import screen_rvc
from gridq.ramp_ridethrough._common import ceil_sqrt
from gridq.ramp_ridethrough.rvc import EventNotComputed, EventResult


def _events(screen):  # type: ignore[no-untyped-def]
    return {e.event: e for e in screen.events}


def _float_pct(p: float, q: float, sk: float, xr: float | None) -> float:
    if xr is None:
        return 100 * math.hypot(p, q) / sk
    return 100 * (p + q * xr) / (sk * math.hypot(1, xr))


def test_tier_1_passes_on_the_fault_level_bound_alone() -> None:
    ev = _events(screen_rvc(declaration(), study(sk_va=200_000_000, x_over_r=None)))
    trip = ev["trip_at_full_export"]
    assert isinstance(trip, EventResult)
    assert trip.outcome == "pass_bound_sk" and trip.referred_to == "none"
    assert (trip.p_w, trip.q_var) == (5_000_000, 1_650_000)
    est = trip.estimate_pct_ceil.as_fraction()
    expected = _float_pct(5e6, 1.65e6, 2e8, None)
    assert expected <= est < expected + Fraction(1, 10_000) + 1e-9
    assert trip.limit_pct.as_fraction() == 3


def test_tier_2_asks_for_x_over_r_only_when_the_bound_fails() -> None:
    ev = _events(screen_rvc(declaration(), study(sk_va=200_000_000, x_over_r=None)))
    rev = ev["unramped_full_reversal"]  # 10 MW and 3.3 Mvar: the bound is 5.27 %
    assert isinstance(rev, EventNotComputed)
    assert not isinstance(rev.bound_estimate_pct_ceil, type(unknown()))
    assert rev.bound_estimate_pct_ceil.as_fraction() > 3  # type: ignore[union-attr]
    (a,) = rev.requests
    assert a.holder is AskHolder.NETWORK_OPERATOR and "X/R" in a.request


def test_tier_2_equation_3_passes_with_the_network_x_over_r() -> None:
    ev = _events(screen_rvc(declaration(), study(sk_va=200_000_000, x_over_r=5)))
    rev = ev["unramped_full_reversal"]
    assert isinstance(rev, EventResult) and rev.outcome == "pass_eq3_xr"
    expected = _float_pct(10e6, 3.3e6, 2e8, 5.0)
    est = rev.estimate_pct_ceil.as_fraction()
    assert expected <= est < expected + 1e-4 + 1e-9


def test_tier_3_exceeds_and_refers_to_psal() -> None:
    ev = _events(screen_rvc(declaration(), study(sk_va=100_000_000, x_over_r=5)))
    rev = ev["unramped_full_reversal"]
    assert isinstance(rev, EventResult)
    assert rev.outcome == "exceeds_steady_state_limit" and rev.referred_to == "psal_rms"
    assert rev.estimate_pct_ceil.as_fraction() > 3


def test_the_boundary_is_exact_a_change_of_exactly_3_percent_passes() -> None:
    # P = 3 MW, Q = 0 at S"k = 100 MVA is exactly 3 %: passes the bound, no rounding.
    bess = declaration(
        registered_capacity_w=3_000_000,
        rated_import_power_w=3_000_000,
        facility_registered_capacity_w=3_000_000,
        reactive_range={"kind": "reactive_range", "absorb_var": 0, "inject_var": 0},
    )
    ev = _events(screen_rvc(bess, study(sk_va=100_000_000, x_over_r=None)))
    trip = ev["trip_at_full_export"]
    assert isinstance(trip, EventResult) and trip.outcome == "pass_bound_sk"
    assert trip.estimate_pct_ceil.as_fraction() == 3


def test_no_fault_level_is_not_computed_and_never_uses_the_g99_generic_figure() -> None:
    s = screen_rvc(declaration(), study(sk_va=None))
    assert all(isinstance(e, EventNotComputed) for e in s.events)
    asks = {a.request for e in s.events for a in e.requests}  # type: ignore[union-attr]
    assert len(asks) == 1
    (req,) = asks
    assert "minimum short-circuit power" in req and "not the Long Term Development" in req


def test_unknown_category_lists_both_obligations_with_an_ask() -> None:
    s = screen_rvc(declaration(rvc_category=unknown()), study())
    assert [o.category for o in s.obligations] == ["category_2", "category_3"]
    two, three = s.obligations
    assert (two.max_events, two.per) == (4, "calendar_month")
    assert (three.max_events, three.per) == (1, "three_calendar_months")
    assert two.min_separation_s == three.min_separation_s == 600
    assert any("Table 4 category" in a.request for a in s.requests)


def test_screen_states_its_basis_and_what_it_does_not_screen() -> None:
    s = screen_rvc(declaration(), study())
    assert s.v0_basis == "nominal_pre_event_voltage" and s.point_of_evaluation == "pcc"
    assert {n.item for n in s.not_screened} == {
        "transformer_energisation",
        "flicker",
        "time_envelope_conformance",
        "network_voltage_control_interaction",
    }


@pytest.mark.parametrize(
    "value", [Fraction(0), Fraction(1), Fraction(2), Fraction(1, 3), Fraction(10**12 + 7)]
)
def test_ceil_sqrt_rounds_up_exactly(value: Fraction) -> None:
    r = ceil_sqrt(value)
    assert r * r >= value
    assert (r - Fraction(1, 10_000)) ** 2 < value or r == 0
