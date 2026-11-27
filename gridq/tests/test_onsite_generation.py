"""WP-SITE-05: the ``onsite_generation_model`` record, its asks, and the standby floor seam.

All inputs are SYNTHETIC (`tests._onsite_support`); real genset data is a person-only ask.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from tests import _adequacy_support as A
from tests import _onsite_support as O

from gridq.adequacy import render_floor_statement, size_floor
from gridq.adequacy.floor import (
    HANDOVER_NOTE,
    Derivation,
    Indeterminate,
    Sized,
    StandbyDerivation,
)
from gridq.adequacy.inputs import AutonomyRequirement
from gridq.adequacy.onsite import (
    G10,
    MinuteOutputArray,
    OnsiteGeneration,
    OnsiteGenerationModel,
    missing_onsite_asks,
)
from gridq.contracts._base import Unknown


def _data(m: OnsiteGenerationModel) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(m.model_dump_json())
    return out


def _revalidate(data: dict[str, Any]) -> OnsiteGenerationModel:
    return OnsiteGenerationModel.model_validate_json(json.dumps(data))


# --------------------------------------------------------------------------- the record


def test_a_full_model_validates_and_hashes_stably() -> None:
    m = O.model(
        [O.genset("G1"), O.genset("G2", kw=250, start_s=30)],
        limits=[O.limit("L1", ["G1", "G2"], cap_s=180_000)],
        tests=[O.weekly_test("T1", ["G1", "G2"], weekday=3, start_minute=600, duration_s=1800)],
        stores=[O.store("F1", ["G1", "G2"], seconds_at_rated=36_000, lead_s=7200)],
        colocated=[
            {
                "source_id": "AD1",
                "technology": "anaerobic_digestion",
                "output": {"kind": "stated_firm_kw", "kw": 120},
            }
        ],
        contingency={"kind": "largest_units_out", "k": 1},
    )
    assert m.content_hash() == _revalidate(_data(m)).content_hash()
    assert m.content_hash().startswith("sha256:")


def test_the_synthetic_label_is_enforced() -> None:
    with pytest.raises(ValidationError, match="SYNTHETIC"):
        O.model([O.genset("G1")], site="REAL-SITE")
    with pytest.raises(ValidationError, match="SYNTHETIC"):
        O.model([O.genset("G1")], record_class="registered")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["gensets"].append(d["gensets"][0]), "unit ids are unique"),
        (lambda d: d["no_run_hour_limit"][0].update(unit_ids=["G9"]), "resolves"),
        (
            lambda d: d["run_limits"].append(O.limit("L1", ["G1"], cap_s=10)),
            "limit and a no-limit",
        ),
        (
            lambda d: d["test_runs"].append(
                O.weekly_test("T1", ["G1"], weekday=1, start_minute=0, duration_s=60)
            ),
            "schedule and a no-test",
        ),
        (lambda d: d["fuel_stores"].append(O.store("F2", ["G1"], continuous=True)), "one fuel"),
        (lambda d: d.update(start_contingency={"kind": "not_applicable", "reason": "x"}), "stated"),
        (lambda d: d["gensets"][0].update(technology="solar_pv"), "diesel, gas or CHP"),
    ],
)
def test_structural_rules(mutate: Any, match: str) -> None:
    d = _data(O.model([O.genset("G1")]))
    mutate(d)
    with pytest.raises(ValidationError, match=match):
        _revalidate(d)


def test_pv_never_states_a_firm_kw() -> None:
    with pytest.raises(ValidationError, match="firm kW"):
        O.model(
            [],
            colocated=[
                {
                    "source_id": "PV1",
                    "technology": "solar_pv",
                    "output": {"kind": "stated_firm_kw", "kw": 100},
                }
            ],
        )


def test_burn_curves_are_monotone_and_reach_rated() -> None:
    with pytest.raises(ValidationError, match="non-decreasing"):
        O.model(
            [O.genset("G1")],
            stores=[O.store("F1", ["G1"], tank_ml=1000, curves={"G1": [(0, 900), (400, 800)]})],
        )
    with pytest.raises(ValidationError, match="stops below its rating"):
        O.model(
            [O.genset("G1", kw=400)],
            stores=[O.store("F1", ["G1"], tank_ml=1000, curves={"G1": [(0, 100), (300, 900)]})],
        )


def test_no_generation_is_a_statement_not_an_empty_default() -> None:
    m = O.model([])
    assert m.statement == "no_onsite_generation"
    d = _data(m)
    d["statement"] = "assets_listed"
    with pytest.raises(ValidationError, match="assets_listed needs an asset"):
        _revalidate(d)


def test_series_are_bound_by_digest() -> None:
    arr = MinuteOutputArray(
        source_id="PV1",
        site_id=O.SITE,
        record_class="synthetic_fixture",
        horizon_start=A.DAY0,
        output_w=np.zeros((1, 1440), dtype=np.int64),
    )
    col = {
        "source_id": "PV1",
        "technology": "solar_pv",
        "output": {"kind": "minute_series", "series_digest": arr.digest},
    }
    m = O.model([], colocated=[col])
    assert OnsiteGeneration(model=m, series=(arr,)).content_hash() == m.content_hash()
    other = MinuteOutputArray(
        source_id="PV1",
        site_id=O.SITE,
        record_class="synthetic_fixture",
        horizon_start=A.DAY0,
        output_w=np.ones((1, 1440), dtype=np.int64),
    )
    with pytest.raises(ValueError, match="not the one the model names"):
        OnsiteGeneration(model=m, series=(other,))
    with pytest.raises(ValueError, match="one output series per source"):
        OnsiteGeneration(model=m)


# --------------------------------------------------------------------------- asks


def test_every_unknown_is_a_typed_ask_never_a_default() -> None:
    m = O.model(
        [O.genset("G1", kw=None, start_s=None)],
        limits=[O.limit("L1", ["G1"], cap_s=None, used=None)],
        stores=[O.store("F1", ["G1"], opening=None)],
        contingency=O.UNKNOWN,
        detection_s=O.UNKNOWN,
        transfer_s=O.UNKNOWN,
    )
    fields = {f for f, _ in missing_onsite_asks(m)}
    assert {
        "G1.rated_kw",
        "G1.start_to_load_s",
        "L1.cap_s",
        "L1.used",
        "F1.endurance",
        "F1.opening",
        "start_contingency",
        "detection_s",
        "transfer_s",
    } <= fields
    asks = dict(missing_onsite_asks(m))
    assert G10.gap_id in asks["L1.cap_s"].request
    assert [e.value for e in asks["L1.used"].closes_with] == ["operational_data_record"]


def test_a_unit_with_no_statements_asks_for_each() -> None:
    d = _data(O.model([O.genset("G1")]))
    d["no_run_hour_limit"], d["no_test_runs"], d["fuel_stores"] = [], [], []
    fields = {f for f, _ in missing_onsite_asks(_revalidate(d))}
    assert {"G1.run_limits", "G1.test_runs", "G1.fuel"} <= fields


def test_a_complete_model_asks_nothing() -> None:
    assert missing_onsite_asks(O.golden_generation().model) == ()


# --------------------------------------------------------------------------- standby floor seam


def _standby_requirement(
    m: OnsiteGenerationModel, *, coverage: str = "ride_through_to_standby", kw: int = 600
) -> AutonomyRequirement:
    data = json.loads(A.requirement(kw=kw).model_dump_json())
    data["duration"] = {"kind": "derived_from_standby", "standby_ref": m.content_hash()}
    data["coverage"] = coverage
    return AutonomyRequirement.model_validate_json(json.dumps(data))


def _fleet(**kw: Any) -> OnsiteGenerationModel:
    return O.model(
        [
            O.genset("G1", kw=400, start_s=12),
            O.genset("G2", kw=400, start_s=20),
            O.genset("G3", kw=800, start_s=45),
        ],
        **kw,
    )


def test_standby_derived_duration_is_the_handover() -> None:
    m = _fleet(detection_s=3, transfer_s=7)
    p = size_floor(
        _standby_requirement(m), A.bess(), A.flat_partition(), load=A.minute_load(), standby=m
    )
    assert isinstance(p.state, Sized) and isinstance(p.derivation, StandbyDerivation)
    h = p.derivation.handover
    # 600 kW: G1 alone (400) is short; G1 + G2 at 20 s carry it.
    assert (h.carry_start_s, h.duration_s, h.contingency_k) == (20, 30, 0)
    assert h.standby_model_hash == m.content_hash()
    assert p.derivation.window_seconds == 30 and p.derivation.window_minutes == 1
    stmt = json.loads(render_floor_statement(p))
    assert stmt["format"] == "gridq.resilience_floor_statement/1.1.0"
    assert stmt["handover_note"] == HANDOVER_NOTE and stmt["handover"]["duration_s"] == 30
    # A non-standby policy still renders 1.0.0, unchanged.
    plain = size_floor(A.requirement(), A.bess(), A.flat_partition(), load=A.minute_load())
    assert isinstance(plain.derivation, Derivation)
    assert json.loads(render_floor_statement(plain))["format"].endswith("/1.0.0")


def test_n_plus_k_waits_for_more_units() -> None:
    m = _fleet(contingency={"kind": "largest_units_out", "k": 1}, detection_s=3, transfer_s=7)
    p = size_floor(
        _standby_requirement(m), A.bess(), A.flat_partition(), load=A.minute_load(), standby=m
    )
    assert isinstance(p.derivation, StandbyDerivation)
    # Less the largest: at 20 s 400 kW remains; at 45 s 1600 - 800 = 800 kW carries 600 kW.
    assert p.derivation.handover.carry_start_s == 45


def test_a_fleet_that_cannot_carry_is_indeterminate() -> None:
    m = _fleet(contingency={"kind": "largest_units_out", "k": 2})
    p = size_floor(
        _standby_requirement(m, kw=900),
        A.bess(),
        A.flat_partition(),
        load=A.minute_load(),
        standby=m,
    )
    assert isinstance(p.state, Indeterminate) and "standby_capacity" in p.state.missing


def test_an_unknown_contingency_keeps_it_indeterminate() -> None:
    m = _fleet(contingency=O.UNKNOWN)
    p = size_floor(
        _standby_requirement(m), A.bess(), A.flat_partition(), load=A.minute_load(), standby=m
    )
    assert isinstance(p.state, Indeterminate) and "start_contingency" in p.state.missing


def test_a_unit_of_unknown_rating_is_left_out_with_an_ask() -> None:
    m = O.model(
        [O.genset("G0", kw=None, start_s=5), O.genset("G1", kw=700, start_s=15)],
        detection_s=2,
        transfer_s=3,
    )
    p = size_floor(
        _standby_requirement(m), A.bess(), A.flat_partition(), load=A.minute_load(), standby=m
    )
    assert isinstance(p.derivation, StandbyDerivation)
    assert p.derivation.handover.excluded_units == ("G0",)
    assert p.derivation.handover.duration_s == 20
    assert any("G0" in a.request for a in p.data_requests)


def test_a_mismatched_standby_model_raises() -> None:
    m = _fleet()
    other = _fleet(detection_s=99)
    with pytest.raises(ValueError, match="not the one the requirement names"):
        size_floor(
            _standby_requirement(m),
            A.bess(),
            A.flat_partition(),
            load=A.minute_load(),
            standby=other,
        )


def test_full_outage_autonomy_cannot_derive_from_standby() -> None:
    m = _fleet()
    p = size_floor(
        _standby_requirement(m, coverage="full_outage_autonomy"),
        A.bess(),
        A.flat_partition(),
        load=A.minute_load(),
        standby=m,
    )
    assert isinstance(p.state, Indeterminate) and "coverage" in p.state.missing


def test_a_modelled_protected_load_uses_its_peak() -> None:
    m = _fleet(detection_s=0, transfer_s=0)
    data = json.loads(A.requirement(fraction=Fraction(1, 2)).model_dump_json())
    data["duration"] = {"kind": "derived_from_standby", "standby_ref": m.content_hash()}
    req = AutonomyRequirement.model_validate_json(json.dumps(data))
    load = A.minute_load()
    p = size_floor(req, A.bess(), A.flat_partition(), load=load, standby=m)
    assert isinstance(p.derivation, StandbyDerivation)
    fac, it = load.minutes(0, 3 * 1440)
    assert p.derivation.handover.peak_protected_w == int(((it + 1) // 2).max())
    assert not isinstance(p.derivation.handover.peak_protected_w, Unknown)


def test_without_a_standby_model_it_still_waits() -> None:
    m = _fleet()
    p = size_floor(_standby_requirement(m), A.bess(), A.flat_partition(), load=A.minute_load())
    assert isinstance(p.state, Indeterminate)
    assert any("WP-SITE-05" in a.request for a in p.data_requests)


def test_a_standby_model_for_a_stated_duration_raises() -> None:
    with pytest.raises(ValueError, match="does not derive"):
        size_floor(
            A.requirement(), A.bess(), A.flat_partition(), load=A.minute_load(), standby=_fleet()
        )
