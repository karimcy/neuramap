"""The facility model's behaviours, each against a hand calculation (decision D81).

UPS losses in loss space (held below the lowest datasheet point, extended and flagged above full
load, redundancy through the module count), the WP-THERM-04 cooling forms and regimes with the §6.2
wet-bulb rule, every bound flagged rather than silent, PUE as an output, slip that never reorders
phases, duty points solved and refused, and the seeded sampling primitives.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from tests._datacentre_support import DRAWS, SEED, ambient, cooling, fixed, make_inputs, uniform

from gridq.contracts.thermal_flex import CoolingTopology
from gridq.datacentre import (
    Auxiliary,
    CoolingRegime,
    DcLoadInputs,
    Discrete,
    DutyPoint,
    Normal,
    Quantiles,
    RampPhase,
    Triangular,
    Uniform,
    Ups,
    UpsConstant,
    UpsCurve,
    UpsPoint,
    synthesise,
)
from gridq.datacentre.distributions import latin_hypercube, standard_normals, uniforms
from gridq.datacentre.model import Components, DrawParameters, ParameterSpace, PlantModel, evaluate
from gridq.datacentre.synthesiser import minimum_draws, order_statistic_interval


def _one_draw(inputs: DcLoadInputs) -> tuple[PlantModel, DrawParameters]:
    values, _ = ParameterSpace.of(inputs).sample(SEED, 1)
    return PlantModel(inputs), DrawParameters.build(inputs, values)


def _eval(
    inputs: DcLoadInputs, load_kw: list[float], temp_c: list[float], cap_kw: float | None = None
) -> Components:
    """One phase, one draw: evaluate at the given IT loads and driving temperatures."""
    plant, params = _one_draw(inputs)
    t = len(load_kw)
    load = np.asarray(load_kw, dtype=float).reshape(1, 1, t)
    cap = np.full((1, 1, t), cap_kw if cap_kw is not None else inputs.phases[0].rated_it_kw)
    live = cap > 0
    return evaluate(
        plant,
        params,
        phase_load=load,
        phase_cap=cap,
        phase_live=live,
        any_live=live[:, 0, :],
        ambient_c=np.asarray(temp_c, dtype=float),
        half_hour_of_day=np.zeros(t, dtype=int),
    )


def _simple(**over):  # type: ignore[no-untyped-def]
    """One certain phase, no auxiliaries, no distribution loss, a C1 plant on the IT load."""
    spec = dict(
        phases=(
            RampPhase(
                phase_id="P1",
                energised_on=date(2027, 3, 1),
                racks=10,
                rack_rated_kw=100.0,
                fill_days=fixed(0.0),
                slip_increment_days=fixed(0.0),
                ups_modules=1,
            ),
        ),
        ups=Ups(
            redundancy="N", module_rating_kw=100.0, efficiency=UpsConstant(efficiency=fixed(0.95))
        ),
        distribution_loss=make_inputs().distribution_loss.model_copy(
            update={"fraction": fixed(0.0)}
        ),
        auxiliaries=(),
        cooling=cooling(
            topology=(CoolingTopology.AIR_COOLED_CHILLERS,),
            load_basis="it_load",
            ups_heat_fraction=None,
            distribution_heat_fraction=None,
            form="C1",
            regimes=(
                CoolingRegime(coefficients=(fixed(10.0), fixed(0.1), fixed(2.0)), duty_points=None),
            ),
            switch_temperature_c=None,
            capacity_kw=None,
        ),
    )
    spec.update(over)
    return make_inputs(**spec)


# --------------------------------------------------------------------------- UPS


def test_constant_ups_efficiency_is_loss_over_output() -> None:
    c = _eval(_simple(), [400.0, 0.0], [10.0, 10.0])
    assert c.ups_loss_kw[0, 0] == pytest.approx(400.0 * (1 / 0.95 - 1))
    assert c.ups_loss_kw[0, 1] == 0.0


def _curve(redundancy: str) -> Ups:
    return Ups(
        redundancy=redundancy,  # type: ignore[arg-type]
        module_rating_kw=100.0,
        efficiency=UpsCurve(
            points=(
                UpsPoint(load_fraction=0.25, efficiency=fixed(0.90)),
                UpsPoint(load_fraction=0.5, efficiency=fixed(0.94)),
                UpsPoint(load_fraction=1.0, efficiency=fixed(0.96)),
            )
        ),
    )


def test_ups_curve_is_interpolated_in_loss_space_and_bounded_honestly() -> None:
    l25, l50, l100 = 25 * (1 / 0.90 - 1), 50 * (1 / 0.94 - 1), 100 * (1 / 0.96 - 1)
    c = _eval(_simple(ups=_curve("N")), [10.0, 37.5, 100.0, 120.0], [10.0] * 4)
    # Below the lowest point the lowest point's loss is held: a stated upper bound.
    assert c.ups_loss_kw[0, 0] == pytest.approx(l25)
    # Between points, loss (not efficiency) is linear in load.
    assert c.ups_loss_kw[0, 1] == pytest.approx((l25 + l50) / 2)
    assert c.ups_loss_kw[0, 2] == pytest.approx(l100)
    # Above full load the last segment is extended, and the interval is flagged.
    assert c.ups_loss_kw[0, 3] == pytest.approx(l100 + 0.2 * (l100 - l50) / 0.5)
    assert c.ups_overload[0].tolist() == [False, False, False, True]


def test_redundancy_sets_the_modules_in_service() -> None:
    l50, l100 = 50 * (1 / 0.94 - 1), 100 * (1 / 0.96 - 1)
    n = _eval(_simple(ups=_curve("N")), [100.0], [10.0])
    n1 = _eval(_simple(ups=_curve("N+1")), [100.0], [10.0])
    two_n = _eval(_simple(ups=_curve("2N")), [100.0], [10.0])
    assert n.ups_loss_kw[0, 0] == pytest.approx(l100)  # one module at 100%
    assert n1.ups_loss_kw[0, 0] == pytest.approx(2 * l50)  # two modules at 50%
    assert two_n.ups_loss_kw[0, 0] == pytest.approx(2 * l50)  # 2N of one module: same here
    assert _eval(_simple(ups=_curve("N+1")), [150.0], [10.0]).ups_overload[0, 0] == np.False_


def test_ups_curve_must_reach_full_load() -> None:
    with pytest.raises(ValueError, match="must end at full load"):
        UpsCurve(
            points=(
                UpsPoint(load_fraction=0.25, efficiency=fixed(0.9)),
                UpsPoint(load_fraction=0.75, efficiency=fixed(0.95)),
            )
        )


# --------------------------------------------------------------------------- cooling


def test_cooling_follows_the_regression_form_and_is_zero_with_nothing_energised() -> None:
    c = _eval(_simple(), [400.0, 0.0], [12.0, 12.0])
    assert c.cooling_kw[0, 0] == pytest.approx(10.0 + 0.1 * 400.0 + 2.0 * 12.0)
    z = _eval(_simple(), [0.0], [12.0], cap_kw=0.0)
    assert z.cooling_kw[0, 0] == 0.0
    assert np.isnan(z.pue[0, 0])
    assert c.pue[0, 0] == pytest.approx(c.facility_kw[0, 0] / 400.0)


def test_cooling_bounds_are_flagged_not_silent() -> None:
    capped = _simple(cooling=_simple().cooling.model_copy(update={"capacity_kw": 50.0}))
    c = _eval(capped, [400.0, 100.0], [12.0, -40.0])
    assert c.cooling_kw[0, 0] == 50.0 and c.cooling_capacity_bound[0, 0]
    assert c.cooling_unbounded_kw[0, 0] == pytest.approx(74.0)
    # 10 + 10 - 80 < 0: floored at zero, and flagged.
    assert c.cooling_kw[0, 1] == 0.0 and c.cooling_floor_bound[0, 1]


def test_free_cooling_switches_regime_at_t_star_on_the_driving_temperature() -> None:
    plant = _simple().cooling.model_copy(
        update={
            "topology": (
                CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS,
                CoolingTopology.FREE_COOLING,
            ),
            "regimes": (
                CoolingRegime(coefficients=(fixed(1.0), fixed(0.01), fixed(0.0)), duty_points=None),
                CoolingRegime(coefficients=(fixed(5.0), fixed(0.2), fixed(1.0)), duty_points=None),
            ),
            "switch_temperature_c": fixed(10.0),
        }
    )
    assert plant.drives_on_wet_bulb
    inputs = _simple(cooling=plant, ambient=ambient(wet=True))
    c = _eval(inputs, [100.0, 100.0], [9.99, 10.0])
    assert c.cooling_kw[0, 0] == pytest.approx(1.0 + 1.0)
    assert c.cooling_kw[0, 1] == pytest.approx(5.0 + 20.0 + 10.0)


def test_wet_bulb_is_required_where_the_topology_is_driven_by_it() -> None:
    towers = _simple().cooling.model_copy(
        update={"topology": (CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS,)}
    )
    with pytest.raises(ValueError, match="must carry wet_bulb_c"):
        _simple(cooling=towers)
    evaporative = _simple().cooling.model_copy(update={"evaporative_heat_rejection": True})
    assert evaporative.drives_on_wet_bulb
    assert not _simple().cooling.drives_on_wet_bulb


def test_the_heat_basis_adds_in_hall_losses_to_the_cooling_load() -> None:
    plant = _simple().cooling.model_copy(
        update={
            "load_basis": "heat_to_cooled_space",
            "ups_heat_fraction": fixed(1.0),
            "distribution_heat_fraction": fixed(0.5),
        }
    )
    aux = (
        Auxiliary(
            name="hall_lights", basis="fixed_kw", value=fixed(8.0), in_hall=True, schedule=None
        ),
        Auxiliary(name="office", basis="fixed_kw", value=fixed(5.0), in_hall=False, schedule=None),
    )
    inputs = _simple(
        cooling=plant,
        auxiliaries=aux,
        distribution_loss=_simple().distribution_loss.model_copy(update={"fraction": fixed(0.02)}),
    )
    c = _eval(inputs, [400.0], [12.0])
    ups = 400.0 * (1 / 0.95 - 1)
    dist = 0.02 * 400.0
    q = 400.0 + ups + 0.5 * dist + 8.0
    assert c.auxiliary_kw[0, 0] == pytest.approx(13.0)
    assert c.cooling_kw[0, 0] == pytest.approx(10.0 + 0.1 * q + 2.0 * 12.0)


def test_duty_points_are_solved_by_least_squares_and_refused_when_unidentifiable() -> None:
    def plane(x: float, t: float) -> float:
        return 3.0 + 0.15 * x + 1.5 * t

    points = tuple(
        DutyPoint(x_kw=x, ambient_c=t, cooling_kw=fixed(plane(x, t)))
        for x, t in ((100.0, 10.0), (500.0, 10.0), (100.0, 30.0), (500.0, 30.0))
    )
    plant = _simple().cooling.model_copy(
        update={"regimes": (CoolingRegime(coefficients=None, duty_points=points),)}
    )
    _, params = _one_draw(_simple(cooling=plant))
    np.testing.assert_allclose(params.coefficients[0, 0], [3.0, 0.15, 1.5], atol=1e-9)
    assert params.duty_point_rms_kw[0][0] == pytest.approx(0.0, abs=1e-9)

    flat = tuple(
        DutyPoint(x_kw=x, ambient_c=20.0, cooling_kw=fixed(10.0 + x / 10))
        for x in (100.0, 200.0, 300.0)
    )
    unidentifiable = _simple().cooling.model_copy(
        update={"regimes": (CoolingRegime(coefficients=None, duty_points=flat),)}
    )
    with pytest.raises(ValueError, match="cannot identify"):
        _one_draw(_simple(cooling=unidentifiable))
    with pytest.raises(ValueError, match="at least 3 duty points"):
        cooling(
            topology=(CoolingTopology.AIR_COOLED_CHILLERS,),
            load_basis="it_load",
            ups_heat_fraction=None,
            distribution_heat_fraction=None,
            form="C1",
            regimes=(CoolingRegime(coefficients=None, duty_points=points[:2]),),
            switch_temperature_c=None,
        )


# --------------------------------------------------------------------------- ramp


def test_slip_never_reorders_phases_and_auxiliaries_start_at_first_energisation() -> None:
    base = make_inputs()
    same_day = tuple(
        p.model_copy(
            update={"energised_on": date(2027, 3, 3), "slip_increment_days": uniform(0.0, 3.0)}
        )
        for p in base.phases
    )
    inputs = base.model_copy(update={"phases": same_day})
    values, _ = ParameterSpace.of(inputs).sample(SEED, DRAWS)
    params = DrawParameters.build(inputs, values)
    assert np.all(np.diff(params.slip_days, axis=1) >= 0)
    result = synthesise(inputs, seed=SEED, draws=DRAWS)
    aux = result.components_kw["auxiliary"]
    first = 2 * 48 + (params.slip_days[:, 0] * 48).astype(int)
    for d in range(DRAWS):
        assert np.all(aux[d, : first[d]] == 0.0)
        assert np.all(result.components_kw["cooling"][d, : first[d]] == 0.0)
        assert aux[d, first[d] + 1] > 0.0


# --------------------------------------------------------------------------- sampling


def test_latin_hypercube_has_one_point_per_stratum_and_is_seeded() -> None:
    cube = latin_hypercube(SEED, 50, 3)
    for d in range(3):
        assert sorted((cube[:, d] * 50).astype(int).tolist()) == list(range(50))
    np.testing.assert_array_equal(cube, latin_hypercube(SEED, 50, 3))
    assert not np.array_equal(cube, latin_hypercube(SEED + 1, 50, 3))
    u = uniforms(SEED, (9, 9), 1000)
    assert u.min() > 0.0 and u.max() < 1.0
    z = standard_normals(SEED, (9, 9), 20001)
    assert abs(z.mean()) < 0.03 and abs(z.std() - 1.0) < 0.03


def test_distribution_families_invert_as_stated() -> None:
    u = np.linspace(0.001, 0.999, 999)
    tri = Triangular(low=0.0, mode=1.0, high=4.0).ppf(u)
    assert tri.min() >= 0.0 and tri.max() <= 4.0
    assert Triangular(low=0.0, mode=1.0, high=4.0).ppf(np.array([0.25]))[0] == pytest.approx(1.0)
    trunc = Normal(mean=0.0, sd=1.0, low=-0.5, high=2.0).ppf(u)
    assert trunc.min() >= -0.5 and trunc.max() <= 2.0
    assert Normal(mean=5.0, sd=2.0, low=None, high=None).ppf(np.array([0.5]))[0] == pytest.approx(5)
    disc = Discrete(values=(1.0, 2.0), weights=(1.0, 3.0)).ppf(u)
    assert set(disc.tolist()) == {1.0, 2.0}
    assert np.mean(disc == 2.0) == pytest.approx(0.75, abs=0.01)
    q = Quantiles(points=((0.0, 1.0), (0.5, 2.0), (1.0, 6.0))).ppf(np.array([0.25, 0.75]))
    np.testing.assert_allclose(q, [1.5, 4.0])
    with pytest.raises(ValueError):
        Uniform(low=1.0, high=1.0)
    with pytest.raises(ValueError):
        Quantiles(points=((0.1, 1.0), (1.0, 2.0)))


def test_order_statistic_intervals() -> None:
    assert minimum_draws() == 72
    assert order_statistic_interval(71, 0.05) is None
    assert order_statistic_interval(72, 0.05) is not None
    lo, hi = order_statistic_interval(80, 0.5)  # type: ignore[misc]
    assert lo < 40.5 < hi
