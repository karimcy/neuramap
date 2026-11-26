"""``resilience_floor_policy`` sizing (D172): the formula, its states and its asks."""

from __future__ import annotations

import json
from fractions import Fraction
from math import ceil

import numpy as np
import pytest
from tests import _adequacy_killers as K
from tests import _adequacy_support as A

from gridq.adequacy import AutonomyRequirement, resilience_terms, size_floor
from gridq.adequacy._common import ceil_snapped
from gridq.adequacy.floor import Indeterminate, Infeasible, Sized
from gridq.contracts._base import NotApplicable


def _sized(p: object) -> Sized:
    state = p.state  # type: ignore[attr-defined]
    assert isinstance(state, Sized), state
    return state


def test_stated_kw_formula() -> None:
    K.k_sizing()
    p = size_floor(A.requirement(kw=200), A.bess(), A.flat_partition(), load=A.minute_load())
    s = _sized(p)
    assert (s.flat_floor_kwh, s.fail_safe_floor_kwh, s.flat_resilience_kw) == (211, 211, 200)
    assert p.derivation.window_minutes == 60  # type: ignore[union-attr]


def test_duration_rounds_up_to_whole_minutes() -> None:
    p = size_floor(
        A.requirement(kw=60, seconds=61),
        A.bess(eta=Fraction(1)),
        A.flat_partition(),
        load=A.minute_load(),
    )
    assert _sized(p).flat_floor_kwh == 2  # 60 kW x 2 min = 2 kWh


def test_snap_then_ceil() -> None:
    assert ceil_snapped(Fraction(211) + Fraction(1, 10**7)) == 211
    assert ceil_snapped(Fraction(211) + Fraction(1, 10**5)) == 212
    assert ceil_snapped(Fraction(211)) == 211


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (
            {
                "kind": "stated_margin",
                "basis": "fraction",
                "value": {"numerator": 1, "denominator": 10},
            },
            232,
        ),
        (
            {"kind": "stated_margin", "basis": "kwh", "value": {"numerator": 5, "denominator": 1}},
            216,
        ),
    ],
)
def test_stated_margin(margin: dict[str, object], expected: int) -> None:
    p = size_floor(
        A.requirement(kw=200, margin=margin), A.bess(), A.flat_partition(), load=A.minute_load()
    )
    assert _sized(p).flat_floor_kwh == expected


def _brute(it_w: np.ndarray, frac: Fraction, n: int, q: Fraction | None) -> tuple[int, int]:
    p = (it_w * frac.numerator + frac.denominator - 1) // frac.denominator
    starts = p.shape[1] - n + 1
    e = np.stack([p[:, s : s + n].sum(axis=1) for s in range(starts)], axis=1)
    if q is None:
        per_start = e.max(axis=0)
    else:
        idx = ceil(q * (e.shape[0] - 1))
        per_start = np.sort(e, axis=0)[idx]
    return int(per_start.max()), int(p.max())


@pytest.mark.parametrize("q", [None, Fraction(1, 2), Fraction(19, 20)])
def test_modelled_series_matches_brute_force(q: Fraction | None) -> None:
    load = A.minute_load(draws=72, days=2, seed=3)
    frac = Fraction(3, 4)
    statistic = (
        None
        if q is None
        else {
            "kind": "stated_quantile",
            "q": {"numerator": q.numerator, "denominator": q.denominator},
            "over": "draws",
        }
    )
    req = A.requirement(fraction=frac, seconds=1800, statistic=statistic)
    s = _sized(size_floor(req, A.bess(eta=Fraction(1)), A.flat_partition(), load=load))
    e_wmin, p_w = _brute(load.it_w, frac, 30, q)
    assert s.flat_floor_kwh == ceil_snapped(Fraction(e_wmin, 60_000))
    assert s.flat_resilience_kw == -(-p_w // 1000)


def test_stepped_cells_and_fail_safe() -> None:
    ss, load, policy, b, night, day = K.step_up_case()
    s = _sized(policy)
    assert [c.floor_kwh for c in s.cells] == [night, day]
    assert (s.flat_floor_kwh, s.fail_safe_floor_kwh) == (day, night)
    assert s.cells[1].binding_start == "2027-01-11T08:00:00"
    terms = resilience_terms(policy)
    assert terms.flat_floor_kwh == day and terms.charge_resilience_kw == 0


def test_round_trip_efficiency_is_a_flagged_bound() -> None:
    p = size_floor(
        A.requirement(kw=200),
        A.bess(round_trip=True, eta=Fraction(9, 10)),
        A.flat_partition(),
        load=A.minute_load(),
    )
    assert _sized(p).flat_floor_kwh == 223  # ceil(200 / 0.9)
    assert "discharge_efficiency_bound" in p.derivation.flags  # type: ignore[union-attr]
    assert any("one-way discharge efficiency" in a.request for a in p.data_requests)


def test_non_end_of_life_energy_is_flagged() -> None:
    p = size_floor(
        A.requirement(kw=200),
        A.bess(basis="beginning_of_life"),
        A.flat_partition(),
        load=A.minute_load(),
    )
    assert "floor_checked_against_non_eol_energy" in p.derivation.flags  # type: ignore[union-attr]
    assert any("end of life" in a.request for a in p.data_requests)


def test_floor_above_usable_energy_is_infeasible_not_clamped() -> None:
    p = size_floor(
        A.requirement(kw=900, seconds=3 * 3600), A.bess(), A.flat_partition(), load=A.minute_load()
    )
    assert isinstance(p.state, Infeasible)
    assert p.state.computed_floor_kwh == 2843 and p.state.usable_energy_kwh == 2000


def test_protected_kw_above_rating_is_infeasible() -> None:
    p = size_floor(
        A.requirement(kw=1200, seconds=60), A.bess(), A.flat_partition(), load=A.minute_load()
    )
    assert isinstance(p.state, Infeasible)
    assert any("discharge rating" in r for r in p.state.reasons)


def test_floor_equal_to_usable_energy_leaves_nothing_to_trade() -> None:
    p = size_floor(
        A.requirement(kw=190),
        A.bess(usable=200, initial=200, operating=200),
        A.flat_partition(),
        load=A.minute_load(),
    )
    assert _sized(p).flat_floor_kwh == 200
    assert "no_tradable_energy" in p.derivation.flags  # type: ignore[union-attr]


@pytest.mark.parametrize("field", ["protected_load", "coverage", "duration", "statistic", "margin"])
def test_unknown_requirement_element_is_a_data_ask(field: str) -> None:
    p = size_floor(
        A.requirement(unknown=(field,)), A.bess(), A.flat_partition(), load=A.minute_load()
    )
    assert isinstance(p.state, Indeterminate) and field in p.state.missing
    assert isinstance(p.derivation, NotApplicable) and p.data_requests


@pytest.mark.parametrize(
    "field", ["usable_energy_kwh", "discharge_rating_kw", "discharge_efficiency"]
)
def test_unknown_bess_sizing_field_is_a_data_ask(field: str) -> None:
    p = size_floor(
        A.requirement(), A.bess(unknown=(field,)), A.flat_partition(), load=A.minute_load()
    )
    assert isinstance(p.state, Indeterminate) and field in p.state.missing


def test_standby_derived_duration_waits_for_the_standby_model() -> None:
    data = json.loads(A.requirement().model_dump_json())
    data["duration"] = {"kind": "derived_from_standby", "standby_ref": "SYNTHETIC-standby"}
    req = AutonomyRequirement.model_validate_json(json.dumps(data))
    p = size_floor(req, A.bess(), A.flat_partition(), load=A.minute_load())
    assert isinstance(p.state, Indeterminate)
    assert any("WP-SITE-05" in a.request for a in p.data_requests)


def test_modelled_series_without_load_asks_for_it() -> None:
    p = size_floor(
        A.requirement(fraction=Fraction(1, 2)),
        A.bess(),
        A.flat_partition(),
        load=NotApplicable(kind="not_applicable", reason="no load model"),
    )
    assert isinstance(p.state, Indeterminate) and "load" in p.state.missing


def test_window_longer_than_horizon_is_indeterminate() -> None:
    p = size_floor(
        A.requirement(fraction=Fraction(1, 2), seconds=4 * 86400),
        A.bess(),
        A.flat_partition(),
        load=A.minute_load(days=3),
    )
    assert isinstance(p.state, Indeterminate) and p.state.missing == ("load_horizon",)


def test_site_mismatch_is_refused() -> None:
    with pytest.raises(ValueError, match="different sites"):
        size_floor(
            A.requirement(),
            A.bess(site="SYNTHETIC-OTHER"),
            A.flat_partition(),
            load=A.minute_load(),
        )
