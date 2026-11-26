"""The facility power model: one vectorised function, used at half-hourly and one-minute resolution.

Decision D81 (Fable 5.2), a component model:

``P_fac = L + P_ups_loss + P_dist + sum(P_aux) + P_cool`` and ``PUE = P_fac / L`` (an output, NaN
where ``L = 0``; never an input). The term structure is Zhang & Liu (2022) Eq. (3) (S2); no number
is taken from S2. The cooling plant is WP-THERM-04 method v1 §6.4's regression form, so a plant
described here is described in the coefficients the cooling-flex baseline later fits.

Every array carries the draw on axis 0, so one call evaluates the whole ensemble. Nothing here
clips silently: each bound that bites is returned as a per-interval flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import numpy as np

from gridq.datacentre.distributions import Fixed, Stated, latin_hypercube
from gridq.datacentre.inputs import FORM_TERMS, HALF_HOURS_PER_DAY, CoolingPlant, DcLoadInputs
from gridq.datacentre.sources import Citation

__all__ = [
    "Components",
    "DrawParameters",
    "ParameterSpace",
    "PlantModel",
    "cooling_terms",
    "evaluate",
]

_EPS: Final = 1e-9


# --------------------------------------------------------------------------- parameter space


@dataclass(frozen=True)
class _Group:
    """One hypercube dimension (or none, when every member is Fixed): inputs that move together."""

    label: str
    members: tuple[Stated, ...]

    @property
    def is_fixed(self) -> bool:
        return all(isinstance(s.distribution, Fixed) for s in self.members)


@dataclass(frozen=True)
class ParameterSpace:
    """Every `Stated` input, grouped into hypercube dimensions in a fixed, input-set order."""

    groups: tuple[_Group, ...]

    @classmethod
    def of(cls, inputs: DcLoadInputs) -> ParameterSpace:
        g: list[_Group] = []
        for p in inputs.phases:
            g.append(_Group(f"phase[{p.phase_id}].fill_days", (p.fill_days,)))
            g.append(_Group(f"phase[{p.phase_id}].slip_increment_days", (p.slip_increment_days,)))
        g.append(_Group("utilisation.level_scale", (inputs.utilisation.level_scale,)))
        eff = inputs.ups.efficiency
        if eff.kind == "curve":
            g.append(_Group("ups.efficiency_curve", tuple(pt.efficiency for pt in eff.points)))
        else:
            g.append(_Group("ups.efficiency", (eff.efficiency,)))
        g.append(_Group("distribution_loss.fraction", (inputs.distribution_loss.fraction,)))
        for a in inputs.auxiliaries:
            g.append(_Group(f"auxiliary[{a.name}].value", (a.value,)))
        c = inputs.cooling
        if c.ups_heat_fraction is not None:
            g.append(_Group("cooling.ups_heat_fraction", (c.ups_heat_fraction,)))
        if c.distribution_heat_fraction is not None:
            g.append(_Group("cooling.distribution_heat_fraction", (c.distribution_heat_fraction,)))
        if c.switch_temperature_c is not None:
            g.append(_Group("cooling.switch_temperature_c", (c.switch_temperature_c,)))
        for r, regime in enumerate(c.regimes):
            if regime.coefficients is not None:
                for j, coef in enumerate(regime.coefficients):
                    g.append(_Group(f"cooling.regime[{r}].b{j}", (coef,)))
            else:
                assert regime.duty_points is not None
                g.append(
                    _Group(
                        f"cooling.regime[{r}].duty_points",
                        tuple(dp.cooling_kw for dp in regime.duty_points),
                    )
                )
        v = inputs.variability
        if v.sd_fraction is not None:
            g.append(_Group("variability.sd_fraction", (v.sd_fraction,)))
        if v.correlation_minutes is not None:
            g.append(_Group("variability.correlation_minutes", (v.correlation_minutes,)))
        if v.dips is not None:
            g.append(_Group("variability.dips.depth_fraction", (v.dips.depth_fraction,)))
        return cls(groups=tuple(g))

    @property
    def dimensions(self) -> tuple[str, ...]:
        """The labels of the groups that carry uncertainty, in hypercube column order."""
        return tuple(gr.label for gr in self.groups if not gr.is_fixed)

    @property
    def citations(self) -> tuple[Citation, ...]:
        """Every preset citation the inputs carry, once each, in input order."""
        seen: list[Citation] = []
        for gr in self.groups:
            for s in gr.members:
                if s.citation is not None and s.citation not in seen:
                    seen.append(s.citation)
        return tuple(seen)

    def sample(self, seed: int, draws: int) -> tuple[dict[str, np.ndarray], np.ndarray]:
        """Values ``{label: (draws, members)}`` and the hypercube ``(draws, dimensions)``."""
        dims = self.dimensions
        cube = latin_hypercube(seed, draws, len(dims))
        column = {label: i for i, label in enumerate(dims)}
        values: dict[str, np.ndarray] = {}
        for gr in self.groups:
            if gr.is_fixed:
                u = np.full(draws, 0.5)
            else:
                u = cube[:, column[gr.label]]
            values[gr.label] = np.stack([s.ppf(u) for s in gr.members], axis=1)
        return values, cube


# --------------------------------------------------------------------------- the plant, per draw


def cooling_terms(form: str, x: np.ndarray, t: np.ndarray) -> list[np.ndarray]:
    """WP-THERM-04 method v1 §6.4: the regressors of form C1, C2 or C3, in coefficient order."""
    terms = [np.ones(np.broadcast_shapes(np.shape(x), np.shape(t))), x, t]
    if FORM_TERMS[form] >= 4:
        terms.append(x * t)
    if FORM_TERMS[form] >= 5:
        terms.append(x * x)
    return [np.broadcast_to(term, terms[0].shape) for term in terms]


def _solve_duty_points(
    cooling: CoolingPlant, regime_index: int, cooling_kw: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares coefficients for every draw's duty-point powers, and each draw's RMS residual.

    ``cooling_kw`` is ``(draws, points)``. Refuses a set of points that cannot identify the form's
    coefficients (rank below the number of coefficients), rather than returning an arbitrary fit.
    """
    regime = cooling.regimes[regime_index]
    assert regime.duty_points is not None
    x = np.array([dp.x_kw for dp in regime.duty_points], dtype=float)
    t = np.array([dp.ambient_c for dp in regime.duty_points], dtype=float)
    design = np.stack(cooling_terms(cooling.form, x, t), axis=1)
    terms = FORM_TERMS[cooling.form]
    if np.linalg.matrix_rank(design) < terms:
        raise ValueError(
            f"cooling regime {regime_index}: the duty points cannot identify the {terms} "
            f"coefficients of form {cooling.form} (they do not vary enough in load and ambient)"
        )
    coef, *_ = np.linalg.lstsq(design, cooling_kw.T, rcond=None)
    residual = cooling_kw - (design @ coef).T
    return coef.T, np.sqrt(np.mean(residual**2, axis=1))


@dataclass(frozen=True)
class DrawParameters:
    """Every input's value in every draw (axis 0 is the draw)."""

    slip_days: np.ndarray  # (N, P) cumulative: phase k's slip is the sum of increments 0..k
    fill_days: np.ndarray  # (N, P)
    level_scale: np.ndarray  # (N,)
    ups_loss_per_module_kw: np.ndarray | None  # (N, K) at the curve's load fractions
    ups_efficiency: np.ndarray | None  # (N,) for a constant efficiency
    distribution_fraction: np.ndarray  # (N,)
    auxiliary_values: np.ndarray  # (N, A)
    ups_heat_fraction: np.ndarray | None  # (N,)
    distribution_heat_fraction: np.ndarray | None  # (N,)
    coefficients: np.ndarray  # (N, R, terms)
    duty_point_rms_kw: dict[int, np.ndarray]  # regime -> (N,)
    switch_temperature_c: np.ndarray | None  # (N,)
    sd_fraction: np.ndarray | None  # (N,)
    correlation_minutes: np.ndarray | None  # (N,)
    dip_depth: np.ndarray | None  # (N,)

    @classmethod
    def build(cls, inputs: DcLoadInputs, values: dict[str, np.ndarray]) -> DrawParameters:
        phases = inputs.phases
        increments = np.concatenate(
            [values[f"phase[{p.phase_id}].slip_increment_days"] for p in phases], axis=1
        )
        fill = np.concatenate([values[f"phase[{p.phase_id}].fill_days"] for p in phases], axis=1)
        n = increments.shape[0]
        eff = inputs.ups.efficiency
        loss: np.ndarray | None = None
        constant: np.ndarray | None = None
        if eff.kind == "curve":
            eta = values["ups.efficiency_curve"]  # (N, K)
            fractions = np.array([pt.load_fraction for pt in eff.points], dtype=float)
            # Loss space (D81): efficiency = output / input, so a module carrying
            # `lf * rating` loses `lf * rating * (1 / eta - 1)`.
            loss = fractions[None, :] * inputs.ups.module_rating_kw * (1.0 / eta - 1.0)
        else:
            constant = values["ups.efficiency"][:, 0]
        aux = (
            np.concatenate(
                [values[f"auxiliary[{a.name}].value"] for a in inputs.auxiliaries], axis=1
            )
            if inputs.auxiliaries
            else np.zeros((n, 0))
        )
        c = inputs.cooling
        terms = FORM_TERMS[c.form]
        coefficients = np.empty((n, len(c.regimes), terms))
        rms: dict[int, np.ndarray] = {}
        for r, regime in enumerate(c.regimes):
            if regime.coefficients is not None:
                coefficients[:, r, :] = np.concatenate(
                    [values[f"cooling.regime[{r}].b{j}"] for j in range(terms)], axis=1
                )
            else:
                coefficients[:, r, :], rms[r] = _solve_duty_points(
                    c, r, values[f"cooling.regime[{r}].duty_points"]
                )

        def one(label: str) -> np.ndarray | None:
            return values[label][:, 0] if label in values else None

        return cls(
            slip_days=np.cumsum(increments, axis=1),
            fill_days=fill,
            level_scale=values["utilisation.level_scale"][:, 0],
            ups_loss_per_module_kw=loss,
            ups_efficiency=constant,
            distribution_fraction=values["distribution_loss.fraction"][:, 0],
            auxiliary_values=aux,
            ups_heat_fraction=one("cooling.ups_heat_fraction"),
            distribution_heat_fraction=one("cooling.distribution_heat_fraction"),
            coefficients=coefficients,
            duty_point_rms_kw=rms,
            switch_temperature_c=one("cooling.switch_temperature_c"),
            sd_fraction=one("variability.sd_fraction"),
            correlation_minutes=one("variability.correlation_minutes"),
            dip_depth=one("variability.dips.depth_fraction"),
        )


@dataclass(frozen=True)
class PlantModel:
    """The static (non-sampled) structure of the plant, read once from the inputs."""

    inputs: DcLoadInputs
    modules_in_service: np.ndarray = field(init=False)  # (P,)
    ups_fractions: np.ndarray | None = field(init=False)  # (K,)
    aux_schedules: np.ndarray = field(init=False)  # (A, 48)

    def __post_init__(self) -> None:
        ups = self.inputs.ups
        object.__setattr__(
            self,
            "modules_in_service",
            np.array(
                [ups.modules_in_service(p.ups_modules) for p in self.inputs.phases], dtype=float
            ),
        )
        fractions = (
            np.array([pt.load_fraction for pt in ups.efficiency.points], dtype=float)
            if ups.efficiency.kind == "curve"
            else None
        )
        object.__setattr__(self, "ups_fractions", fractions)
        schedules = [
            np.ones(HALF_HOURS_PER_DAY) if a.schedule is None else np.asarray(a.schedule, float)
            for a in self.inputs.auxiliaries
        ]
        object.__setattr__(
            self,
            "aux_schedules",
            np.stack(schedules) if schedules else np.zeros((0, HALF_HOURS_PER_DAY)),
        )


@dataclass(frozen=True)
class Components:
    """Facility power by component, kW, shape ``(draws, intervals)``, and the bounds that bit."""

    it_kw: np.ndarray
    ups_loss_kw: np.ndarray
    distribution_kw: np.ndarray
    auxiliary_kw: np.ndarray
    cooling_kw: np.ndarray
    #: The cooling polynomial before any bound, so a caller can see what a bound removed.
    cooling_unbounded_kw: np.ndarray
    ups_overload: np.ndarray  # bool: a module above its rating
    cooling_floor_bound: np.ndarray  # bool: the polynomial went below 0
    cooling_capacity_bound: np.ndarray  # bool: the polynomial exceeded the declared capacity

    @property
    def facility_kw(self) -> np.ndarray:
        return (
            self.it_kw
            + self.ups_loss_kw
            + self.distribution_kw
            + self.auxiliary_kw
            + self.cooling_kw
        )

    @property
    def pue(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(
                self.it_kw > 0, self.facility_kw / np.where(self.it_kw > 0, self.it_kw, 1.0), np.nan
            )


def _ups_loss(
    plant: PlantModel, params: DrawParameters, phase_load: np.ndarray, phase_live: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """UPS loss kW ``(N, T)`` and the overload flag, summed over phases."""
    ups = plant.inputs.ups
    if params.ups_efficiency is not None:
        eta = params.ups_efficiency[:, None]
        load = phase_load.sum(axis=1)
        return load * (1.0 / eta - 1.0), np.zeros(load.shape, dtype=bool)
    assert params.ups_loss_per_module_kw is not None and plant.ups_fractions is not None
    xp = plant.ups_fractions
    fp = params.ups_loss_per_module_kw  # (N, K)
    modules = plant.modules_in_service[None, :, None]
    lf = phase_load / (modules * ups.module_rating_kw)  # (N, P, T)
    # Below the lowest datasheet point the lowest point's loss is held (a stated upper bound);
    # above full load the last segment is extended, and the interval is flagged.
    lf_eff = np.maximum(lf, xp[0])
    idx = np.clip(np.searchsorted(xp, lf_eff, side="right"), 1, len(xp) - 1)
    x0, x1 = xp[idx - 1], xp[idx]
    w = (lf_eff - x0) / (x1 - x0)
    rows = np.arange(fp.shape[0])[:, None, None]
    per_module = fp[rows, idx - 1] + w * (fp[rows, idx] - fp[rows, idx - 1])
    per_module = np.maximum(per_module, 0.0)
    loss = (modules * per_module * phase_live).sum(axis=1)
    overload = ((lf > 1.0 + _EPS) & (phase_live > 0)).any(axis=1)
    return loss, overload


def evaluate(
    plant: PlantModel,
    params: DrawParameters,
    *,
    phase_load: np.ndarray,
    phase_cap: np.ndarray,
    phase_live: np.ndarray,
    any_live: np.ndarray,
    ambient_c: np.ndarray,
    half_hour_of_day: np.ndarray,
) -> Components:
    """Facility power for every draw and interval.

    ``phase_load``, ``phase_cap`` and ``phase_live`` are ``(N, P, T)``: each phase's IT load, its
    energised IT capacity, and whether it is energised (its UPS modules in service). ``any_live`` is
    ``(N, T)``: the first phase is energised. ``ambient_c`` is the driving temperature and
    ``half_hour_of_day`` the schedule index, both ``(T,)``. Works at any resolution.
    """
    inputs = plant.inputs
    it = phase_load.sum(axis=1)
    cap = phase_cap.sum(axis=1)
    live = phase_live.astype(float)

    ups, overload = _ups_loss(plant, params, phase_load, live)

    dist_base = it if inputs.distribution_loss.base == "it_load" else it + ups
    dist = params.distribution_fraction[:, None] * dist_base

    aux_total = np.zeros_like(it)
    aux_in_hall = np.zeros_like(it)
    for a_idx, aux in enumerate(inputs.auxiliaries):
        value = params.auxiliary_values[:, a_idx][:, None]
        sched = plant.aux_schedules[a_idx][half_hour_of_day][None, :]
        if aux.basis == "fixed_kw":
            kw = value * sched * any_live
        elif aux.basis == "fraction_of_energised_capacity":
            kw = value * sched * cap
        else:
            kw = value * sched * it
        aux_total = aux_total + kw
        if aux.in_hall:
            aux_in_hall = aux_in_hall + kw

    cooling = inputs.cooling
    if cooling.load_basis == "it_load":
        x = it
    else:
        assert params.ups_heat_fraction is not None
        assert params.distribution_heat_fraction is not None
        x = (
            it
            + params.ups_heat_fraction[:, None] * ups
            + params.distribution_heat_fraction[:, None] * dist
            + aux_in_hall
        )
    t = np.broadcast_to(ambient_c[None, :], x.shape)
    terms = cooling_terms(cooling.form, x, t)
    polys = [
        sum(
            (params.coefficients[:, r, j][:, None] * term for j, term in enumerate(terms)),
            start=np.zeros_like(x),
        )
        for r in range(len(cooling.regimes))
    ]
    if len(polys) == 2:
        assert params.switch_temperature_c is not None
        raw = np.where(t >= params.switch_temperature_c[:, None], polys[1], polys[0])
    else:
        raw = polys[0]
    energised = cap > 0
    raw = np.where(energised, raw, 0.0)
    floor = energised & (raw < 0)
    cool = np.maximum(raw, 0.0)
    if cooling.capacity_kw is not None:
        ceiling = energised & (raw > cooling.capacity_kw)
        cool = np.minimum(cool, cooling.capacity_kw)
    else:
        ceiling = np.zeros(raw.shape, dtype=bool)

    return Components(
        it_kw=it,
        ups_loss_kw=ups,
        distribution_kw=dist,
        auxiliary_kw=aux_total,
        cooling_kw=cool,
        cooling_unbounded_kw=raw,
        ups_overload=overload,
        cooling_floor_bound=floor,
        cooling_capacity_bound=ceiling,
    )
