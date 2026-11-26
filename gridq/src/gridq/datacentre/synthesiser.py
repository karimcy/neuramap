"""The ensemble: half-hourly primary series, one-minute windows, bands, aggregates and attribution.

Decisions D82 and D83 (Fable 5.2), as built here:

**Half-hourly is primary.** Each draw's half-hourly facility power is the model evaluated at the
half-hourly IT load and ambient. That is what the series *is*: "model evaluated at half-hourly mean
load and ambient". A back-test (spec 16.2) must therefore fit WP-THERM-04's coefficients on
half-hourly means, never on one-minute values.

**One minute is an exactly conditioned disaggregation of it.** IT load at one minute is
``L_hh * (1 + e)``. ``e`` is a stationary AR(1) over each calendar day (1,440 minutes), conditioned
so every half-hour's mean is exactly zero (``e = x - S Aᵀ (A S Aᵀ)⁻¹ A x``, Matheron's rule for a
Gaussian conditioned on its block means), plus an optional synchronous dip train on the absolute
minute index, de-meaned per half-hour. Its randomness is keyed by ``(seed, MINUTE_STREAM, draw,
day ordinal)``, so any window is a slice of the same full series. Each component of facility power
is the exact model at the one-minute load, shifted per half-hour by a constant so its half-hour
mean equals the half-hourly value (the shift is the Jensen gap, and is reported). The tangent
approximation is not used: it is wrong at every kink (a bound, a UPS curve knot, a dip).

Known artefacts, stated rather than hidden: the AR(1) restarts at midnight (each day is drawn
alone), ambient is held across each half-hour (it is never interpolated to minutes), and a minute
that would breach ``[0, energised IT capacity]`` has its half-hour's deviations shrunk by the
smallest factor that fits, and is counted.

**Bands are a two-layer, replayable ensemble.** Epistemic inputs are drawn once per draw by a
seeded Latin hypercube and held over the horizon; the one-minute stream is aleatory and disjoint
from them. Levels P05/P10/P50/P90/P95 are pointwise (``numpy.quantile`` ``method="inverted_cdf"``),
are not simultaneous, must not be summed across intervals, and are not calibrated until the spec
16.2 back-test. The central series is the per-interval **mean**, labelled "expected", never "P50".
Every band edge and aggregate carries a distribution-free 95% binomial order-statistic interval for
its simulation error, and ``draws`` is refused below the size that interval needs at P05/P95.
"""

from __future__ import annotations

import calendar
import functools
import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Final

import numpy as np

from gridq.datacentre.distributions import (
    DIP_PHASE_STREAM,
    MINUTE_STREAM,
    PARAMETER_STREAM,
    standard_normals,
    uniforms,
)
from gridq.datacentre.inputs import HALF_HOURS_PER_DAY, MINUTES_PER_DAY, DcLoadInputs
from gridq.datacentre.model import (
    Components,
    DrawParameters,
    ParameterSpace,
    PlantModel,
    evaluate,
)
from gridq.datacentre.sources import Citation
from gridq.datacentre.workload_flex import (
    NO_ENVELOPE_DECLARED,
    NoEnvelopeDeclared,
    WorkloadFlex,
    WorkloadFlexBinding,
    bind,
)
from gridq.provenance.hashing import hash_json, stable_json_dumps

__all__ = [
    "BLOCK_MINUTES",
    "CONFIDENCE",
    "LEVELS",
    "METHOD_VERSION",
    "SCHEME_ID",
    "Aggregate",
    "Attribution",
    "MinuteWindow",
    "Synthesis",
    "minimum_draws",
    "order_statistic_interval",
    "synthesise",
]

METHOD_VERSION: Final = "dc_load_model 1.0.0"
#: The sampling scheme. Part of the hash manifest: a change to it is a change to every series.
SCHEME_ID: Final = "lhs-argsort+pcg64-raw53+box-muller/v1"
LEVELS: Final = (0.05, 0.10, 0.50, 0.90, 0.95)
LEVEL_NAMES: Final = {0.05: "P05", 0.10: "P10", 0.50: "P50", 0.90: "P90", 0.95: "P95"}
CONFIDENCE: Final = 0.95
BLOCK_MINUTES: Final = MINUTES_PER_DAY // HALF_HOURS_PER_DAY
QUANTILE_DEFINITION: Final = "numpy.quantile(method='inverted_cdf'), pointwise per interval"
#: Days of one-minute series computed together (bounds memory, not results: days are independent).
_MINUTE_CHUNK_DAYS: Final = 7
_ROUND: Final = 6


# --------------------------------------------------------------------------- order statistics


def _binomial_cdf(n: int, p: float) -> list[float]:
    """``[P(Binomial(n, p) <= k) for k in 0..n]``, from log-space terms so large ``n`` is safe."""
    lp, lq, ln = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    total, out = 0.0, []
    for k in range(n + 1):
        total += math.exp(ln - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * lp + (n - k) * lq)
        out.append(min(total, 1.0))
    return out


@functools.lru_cache(maxsize=256)
def order_statistic_interval(
    n: int, p: float, confidence: float = CONFIDENCE
) -> tuple[int, int] | None:
    """1-based ranks ``(l, u)`` with ``P(X_(l) <= x_p <= X_(u)) >= confidence``, or ``None``.

    Distribution-free: ``P(X_(k) <= x_p) = P(Binomial(n, p) >= k)``. Each tail gets
    ``(1 - confidence) / 2``: ``l`` is the largest rank with ``P(Bin >= l) >= 1 - tail`` and ``u``
    the smallest with ``P(Bin <= u - 1) >= 1 - tail``. ``None`` when ``n`` draws cannot bracket the
    ``p`` quantile at that confidence.
    """
    tail = (1.0 - confidence) / 2.0
    cdf = _binomial_cdf(n, p)

    def below(k: int) -> float:  # P(Bin <= k - 1)
        return 0.0 if k <= 0 else cdf[k - 1]

    lower = next((k for k in range(n, 0, -1) if 1.0 - below(k) >= 1.0 - tail), None)
    upper = next((k for k in range(1, n + 1) if below(k) >= 1.0 - tail), None)
    if lower is None or upper is None:
        return None
    return lower, upper


def minimum_draws(levels: tuple[float, ...] = LEVELS, confidence: float = CONFIDENCE) -> int:
    """The fewest draws for which every level's order-statistic interval exists."""
    n = 1
    while any(order_statistic_interval(n, p, confidence) is None for p in levels):
        n += 1
    return n


# --------------------------------------------------------------------------- result types


@dataclass(frozen=True)
class Aggregate:
    """A per-draw quantity (an energy, a peak), quantiled across draws."""

    name: str
    unit: str
    expected: float
    quantiles: dict[str, float]
    #: Simulation-error interval on each quantile (order statistics, `CONFIDENCE`).
    quantile_ci: dict[str, tuple[float, float]]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit": self.unit,
            "expected": self.expected,
            "quantiles": self.quantiles,
            "quantile_ci": {k: list(v) for k, v in self.quantile_ci.items()},
        }


@dataclass(frozen=True)
class Attribution:
    """First-order variance shares of one output, by input: the ranked data ask.

    A correlation-ratio estimate: each hypercube column is cut into ``bins`` equal strata, and an
    input's raw share ``eta²`` is the variance of the output's bin means over the output's
    variance. With no effect at all ``eta²`` still reads about
    ``binning_bias = (bins - 1) / draws``,
    and summed over many inputs that bias alone can exceed 1. So each share is also given adjusted
    for it, the way adjusted R² is: ``1 - (1 - eta²)(draws - 1) / (draws - bins)``, floored at 0.
    The ranking and ``remainder`` (what the adjusted first-order shares leave unexplained:
    interactions and estimation noise) use the adjusted share.
    """

    output: str
    #: ``(input, adjusted share, raw eta²)``, largest adjusted share first.
    shares: tuple[tuple[str, float, float], ...]
    remainder: float
    bins: int
    binning_bias: float

    def as_dict(self) -> dict[str, object]:
        return {
            "output": self.output,
            "shares": [
                {"input": label, "adjusted_share": adj, "raw_eta_squared": raw}
                for label, adj, raw in self.shares
            ],
            "remainder": self.remainder,
            "bins": self.bins,
            "binning_bias": self.binning_bias,
        }


@dataclass(frozen=True)
class MinuteWindow:
    """One-minute series for a window, every draw, and what the disaggregation did to get them."""

    start: datetime
    end: datetime
    members_kw: np.ndarray  # (N, M) facility
    it_members_kw: np.ndarray  # (N, M)
    expected_kw: np.ndarray  # (M,)
    bands_kw: dict[str, np.ndarray]
    #: Per draw and half-hour overlapping the window: the facility's Jensen gap, kW (half-hourly
    #: value minus the half-hour mean of the exact model at one-minute load).
    jensen_gap_kw: np.ndarray
    jensen_flagged_blocks: int | None
    shrunk_blocks: int
    cooling_capacity_bound_minutes: int
    cooling_cap_residual_kw: float
    realised_sd_fraction: float | None
    window_hash: str


@dataclass(frozen=True)
class _HalfHourly:
    phase_load: np.ndarray
    phase_cap: np.ndarray
    phase_live: np.ndarray
    any_live: np.ndarray
    ambient: np.ndarray
    half_hour_of_day: np.ndarray
    it_clipped: np.ndarray
    components: Components


@dataclass(frozen=True)
class _MinuteBlock:
    facility: np.ndarray  # (N, M)
    it: np.ndarray  # (N, M)
    jensen: np.ndarray  # (N, B)
    shrunk_blocks: int
    capacity_minutes: int
    cap_residual_kw: float
    ar1_deviation: np.ndarray | None  # (N, M) the conditioned AR(1) part, for the realised SD


# --------------------------------------------------------------------------- the engine


@dataclass(frozen=True)
class _Engine:
    inputs: DcLoadInputs
    seed: int
    draws: int
    plant: PlantModel
    params: DrawParameters
    _projection_cache: dict[float, np.ndarray] = field(default_factory=dict, repr=False)

    # ---- half-hourly

    def half_hourly(self, d0: int, d1: int) -> _HalfHourly:
        inputs, params = self.inputs, self.params
        nd = d1 - d0
        t_mid = d0 + (np.arange(nd * HALF_HOURS_PER_DAY) + 0.5) / HALF_HOURS_PER_DAY
        planned = np.array(
            [(p.energised_on - inputs.horizon_start).days for p in inputs.phases], dtype=float
        )
        energised = planned[None, :] + params.slip_days  # (N, P)
        elapsed = t_mid[None, None, :] - energised[:, :, None]
        live = elapsed >= 0
        fill = params.fill_days[:, :, None]
        frac = (
            np.where(fill > 0, np.clip(elapsed / np.where(fill > 0, fill, 1.0), 0.0, 1.0), 1.0)
            * live
        )
        rated = np.array([p.rated_it_kw for p in inputs.phases], dtype=float)
        cap = rated[None, :, None] * frac

        days = [inputs.horizon_start + timedelta(days=d) for d in range(d0, d1)]
        u = np.concatenate([np.asarray(inputs.utilisation.shape(day.weekday())) for day in days])
        demand = cap * u[None, None, :] * params.level_scale[:, None, None]
        load = np.minimum(demand, cap)
        it_clipped = (demand > cap * (1.0 + 1e-12)).any(axis=1)
        any_live = t_mid[None, :] >= energised.min(axis=1)[:, None]

        ambient = self._ambient(days)
        half_hour_of_day = np.tile(np.arange(HALF_HOURS_PER_DAY), nd)
        comps = evaluate(
            self.plant,
            params,
            phase_load=load,
            phase_cap=cap,
            phase_live=live,
            any_live=any_live,
            ambient_c=ambient,
            half_hour_of_day=half_hour_of_day,
        )
        return _HalfHourly(load, cap, live, any_live, ambient, half_hour_of_day, it_clipped, comps)

    def _ambient(self, days: list[date]) -> np.ndarray:
        amb = self.inputs.ambient
        series = amb.wet_bulb_c if self.inputs.cooling.drives_on_wet_bulb else amb.dry_bulb_c
        assert series is not None
        values = np.asarray(series, dtype=float)
        first = datetime.combine(days[0], datetime.min.time())
        offset = int((first - amb.start).total_seconds() // 60)
        minutes = offset + np.arange(len(days) * HALF_HOURS_PER_DAY) * BLOCK_MINUTES
        return values[minutes // amb.interval_minutes]

    # ---- one minute

    def _projection(self, phi: float) -> np.ndarray:
        """``S Aᵀ (A S Aᵀ)⁻¹`` for the AR(1) correlation ``phi^|i-j|`` and half-hour block means."""
        cached = self._projection_cache.get(phi)
        if cached is not None:
            return cached
        idx = np.arange(MINUTES_PER_DAY)
        sigma = np.power(phi, np.abs(idx[:, None] - idx[None, :]).astype(float))
        s_at = sigma.reshape(MINUTES_PER_DAY, HALF_HOURS_PER_DAY, BLOCK_MINUTES).mean(axis=2)
        a_s_at = s_at.reshape(HALF_HOURS_PER_DAY, BLOCK_MINUTES, HALF_HOURS_PER_DAY).mean(axis=1)
        # Least squares rather than a plain solve: as phi -> 1 the block covariance is near
        # singular, and the conditioned deviation should then tend to zero, not blow up.
        k = np.linalg.lstsq(a_s_at, s_at.T, rcond=None)[0].T
        self._projection_cache[phi] = k
        return k

    def _deviation(self, d0: int, d1: int) -> tuple[np.ndarray, np.ndarray | None]:
        """Relative IT deviation ``e`` ``(N, D, 1440)`` and its AR(1) part, for days [d0, d1)."""
        v, params, n, nd = self.inputs.variability, self.params, self.draws, d1 - d0
        if v.mode == "none":
            return np.zeros((n, nd, MINUTES_PER_DAY)), None
        assert params.sd_fraction is not None and params.correlation_minutes is not None
        ordinals = [
            (self.inputs.horizon_start + timedelta(days=d)).toordinal() for d in range(d0, d1)
        ]
        z = np.empty((n, nd, MINUTES_PER_DAY))
        for i in range(n):
            for j, ordinal in enumerate(ordinals):
                z[i, j] = standard_normals(self.seed, (MINUTE_STREAM, i, ordinal), MINUTES_PER_DAY)
        phi = np.exp(-1.0 / params.correlation_minutes)
        innov = np.sqrt(1.0 - phi**2)[:, None]
        x = np.empty_like(z)
        x[..., 0] = z[..., 0]
        for m in range(1, MINUTES_PER_DAY):
            x[..., m] = phi[:, None] * x[..., m - 1] + innov * z[..., m]
        x *= params.sd_fraction[:, None, None]
        means = x.reshape(n, nd, HALF_HOURS_PER_DAY, BLOCK_MINUTES).mean(axis=3)
        proj = np.stack([self._projection(float(p)) for p in phi])  # (N, 1440, 48)
        # einsum, not matmul: no BLAS threading in the step that fixes every minute's value.
        ar1 = x - np.einsum("nmb,ndb->ndm", proj, means)
        e = ar1.copy()
        if v.dips is not None:
            assert params.dip_depth is not None
            period, duration = v.dips.period_minutes, v.dips.duration_minutes
            phase = np.array(
                [
                    math.floor(uniforms(self.seed, (DIP_PHASE_STREAM, i), 1)[0] * period)
                    for i in range(n)
                ],
                dtype=np.int64,
            )
            absolute = (
                np.asarray(ordinals, dtype=np.int64)[:, None] * MINUTES_PER_DAY
                + np.arange(MINUTES_PER_DAY, dtype=np.int64)[None, :]
            )
            in_dip = ((absolute[None, :, :] + phase[:, None, None]) % period) < duration
            dip = -params.dip_depth[:, None, None] * in_dip
            dip_blocks = dip.reshape(n, nd, HALF_HOURS_PER_DAY, BLOCK_MINUTES)
            dip = (dip_blocks - dip_blocks.mean(axis=3, keepdims=True)).reshape(dip.shape)
            e = e + dip
        return e, ar1

    def minutes(self, d0: int, d1: int) -> _MinuteBlock:
        n, nd = self.draws, d1 - d0
        nb = nd * HALF_HOURS_PER_DAY
        hh = self.half_hourly(d0, d1)
        e, ar1 = self._deviation(d0, d1)
        e_blocks = e.reshape(n, nb, BLOCK_MINUTES)

        # Bounds: shrink a half-hour's deviations by the smallest factor that keeps every minute in
        # [0, energised capacity] for every phase. Its mean stays exactly zero.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(hh.phase_load > 0, hh.phase_cap / hh.phase_load, np.inf).min(axis=1)
            headroom = (ratio - 1.0)[:, :, None]
            limit = np.where(
                e_blocks > 0, headroom / e_blocks, np.where(e_blocks < 0, -1.0 / e_blocks, np.inf)
            )
        scale = np.minimum(1.0, limit.min(axis=2))
        shrunk = int(np.count_nonzero(scale < 1.0))
        e_blocks = e_blocks * scale[:, :, None]
        e_flat = e_blocks.reshape(n, nd * MINUTES_PER_DAY)

        def per_minute(a: np.ndarray) -> np.ndarray:
            return np.repeat(a, BLOCK_MINUTES, axis=-1)

        comps = evaluate(
            self.plant,
            self.params,
            phase_load=per_minute(hh.phase_load) * (1.0 + e_flat[:, None, :]),
            phase_cap=per_minute(hh.phase_cap),
            phase_live=per_minute(hh.phase_live),
            any_live=per_minute(hh.any_live),
            ambient_c=per_minute(hh.ambient),
            half_hour_of_day=per_minute(hh.half_hour_of_day),
        )

        def block_mean(a: np.ndarray) -> np.ndarray:
            return a.reshape(n, nb, BLOCK_MINUTES).mean(axis=2)

        cooling = comps.cooling_kw
        response = self.inputs.cooling.response
        if response.mode == "first_order":
            assert response.time_constant_minutes is not None
            alpha = 1.0 - math.exp(-1.0 / response.time_constant_minutes)
            c = cooling.reshape(n, nd, MINUTES_PER_DAY)
            y = np.empty_like(c)
            y[..., 0] = c[..., 0]  # the filter restarts each day, like the AR(1)
            for m in range(1, MINUTES_PER_DAY):
                y[..., m] = y[..., m - 1] + alpha * (c[..., m] - y[..., m - 1])
            cooling = y.reshape(n, nd * MINUTES_PER_DAY)

        hh_c = hh.components
        jensen = np.zeros((n, nb))
        shifted: dict[str, np.ndarray] = {}
        for name, minute, half in (
            ("ups", comps.ups_loss_kw, hh_c.ups_loss_kw),
            ("dist", comps.distribution_kw, hh_c.distribution_kw),
            ("aux", comps.auxiliary_kw, hh_c.auxiliary_kw),
            ("cool", cooling, hh_c.cooling_kw),
        ):
            delta = half - block_mean(minute)
            jensen += delta
            shifted[name] = minute + per_minute(delta)
        cool = shifted["cool"]
        capacity = self.inputs.cooling.capacity_kw
        cool = np.clip(cool, 0.0, np.inf if capacity is None else capacity)
        residual = float(np.max(np.abs(hh_c.cooling_kw - block_mean(cool)), initial=0.0))
        facility = comps.it_kw + shifted["ups"] + shifted["dist"] + shifted["aux"] + cool
        return _MinuteBlock(
            facility=facility,
            it=comps.it_kw,
            jensen=jensen,
            shrunk_blocks=shrunk,
            capacity_minutes=int(np.count_nonzero(comps.cooling_capacity_bound)),
            cap_residual_kw=residual,
            ar1_deviation=None if ar1 is None else ar1.reshape(n, nd * MINUTES_PER_DAY),
        )


# --------------------------------------------------------------------------- the result


def _rounded_bytes(a: np.ndarray) -> bytes:
    # `+ 0.0` folds -0.0 into 0.0, so a sign bit can never split two equal series.
    return np.ascontiguousarray(np.round(a, _ROUND) + 0.0, dtype="<f8").tobytes()


def _quantiles_with_ci(
    values: np.ndarray,
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Quantiles of a per-draw vector and their order-statistic intervals."""
    ordered = np.sort(values)
    q: dict[str, float] = {}
    ci: dict[str, tuple[float, float]] = {}
    for p in LEVELS:
        name = LEVEL_NAMES[p]
        q[name] = float(np.quantile(values, p, method="inverted_cdf"))
        ranks = order_statistic_interval(len(values), p)
        assert ranks is not None
        ci[name] = (float(ordered[ranks[0] - 1]), float(ordered[ranks[1] - 1]))
    return q, ci


@dataclass(frozen=True)
class Synthesis:
    """A data-centre load ensemble over the horizon. Built by `synthesise`; read, never edited."""

    inputs: DcLoadInputs
    seed: int
    draws: int
    timestamps: tuple[datetime, ...]
    #: Facility kW, ``(draws, intervals)``: the members. A consumer that needs joint behaviour
    #: (WP-SITE-04's sizing) takes members, never band edges.
    members_kw: np.ndarray
    components_kw: dict[str, np.ndarray]
    #: The per-interval mean across draws: the "expected" series, never called P50.
    expected_kw: np.ndarray
    bands_kw: dict[str, np.ndarray]
    band_ci_kw: dict[str, tuple[np.ndarray, np.ndarray]]
    #: The draw nearest the expected series in squared L2 (the medoid under that distance).
    medoid_draw: int
    aggregates: tuple[Aggregate, ...]
    attribution: tuple[Attribution, ...]
    dimensions: tuple[str, ...]
    citations: tuple[Citation, ...]
    flags: dict[str, int]
    warnings: tuple[str, ...]
    manifest: dict[str, object]
    series_hash: str
    _engine: _Engine = field(repr=False)
    #: The IC-9 turn-down declaration bound beside the ensemble (WP-SITE-07), or the sentinel.
    #: Never an input to any series above: `gridq.datacentre.workload_flex`.
    workload_flex: WorkloadFlexBinding | NoEnvelopeDeclared = NO_ENVELOPE_DECLARED

    @property
    def inputs_hash(self) -> str:
        return hash_json(self.inputs.model_dump(mode="json"))

    @property
    def expected_kwh(self) -> np.ndarray:
        """The expected series as energy per half-hour, the unit CORE's series files carry."""
        return self.expected_kw * 0.5

    def one_minute(
        self, start: datetime, end: datetime, *, jensen_gap_flag_kw: float | None
    ) -> MinuteWindow:
        """The one-minute series for ``[start, end)``, every draw.

        A slice of the same full series whatever the window: each day's minutes depend only on
        the seed, the draw and the date. ``jensen_gap_flag_kw`` is the caller's threshold for
        flagging a half-hour's Jensen gap; ``None`` flags nothing (the gaps are returned anyway).
        """
        h0 = datetime.combine(self.inputs.horizon_start, datetime.min.time())
        if start.second or start.microsecond or end.second or end.microsecond:
            raise ValueError("a one-minute window starts and ends on whole minutes")
        m0 = int((start - h0).total_seconds() // 60)
        m1 = int((end - h0).total_seconds() // 60)
        if not (0 <= m0 < m1 <= self.inputs.days * MINUTES_PER_DAY):
            raise ValueError(f"window {start} to {end} is not inside the horizon")
        d0, d1 = m0 // MINUTES_PER_DAY, -(-m1 // MINUTES_PER_DAY)
        block = self._engine.minutes(d0, d1)
        lo, hi = m0 - d0 * MINUTES_PER_DAY, m1 - d0 * MINUTES_PER_DAY
        members = block.facility[:, lo:hi]
        it = block.it[:, lo:hi]
        jensen = block.jensen[:, lo // BLOCK_MINUTES : -(-hi // BLOCK_MINUTES)]
        flagged = (
            None
            if jensen_gap_flag_kw is None
            else int(np.count_nonzero(np.abs(jensen) > jensen_gap_flag_kw))
        )
        realised = (
            None if block.ar1_deviation is None else float(np.std(block.ar1_deviation[:, lo:hi]))
        )
        bands = {
            LEVEL_NAMES[p]: np.quantile(members, p, axis=0, method="inverted_cdf") for p in LEVELS
        }
        expected = members.mean(axis=0)
        digest = hashlib.sha256()
        digest.update(_rounded_bytes(members))
        digest.update(
            stable_json_dumps(
                {
                    "series_hash": self.series_hash,
                    "window": [start.isoformat(), end.isoformat()],
                    "jensen_gap_flag_kw": jensen_gap_flag_kw,
                }
            ).encode("utf-8")
        )
        return MinuteWindow(
            start=start,
            end=end,
            members_kw=members,
            it_members_kw=it,
            expected_kw=expected,
            bands_kw=bands,
            jensen_gap_kw=jensen,
            jensen_flagged_blocks=flagged,
            shrunk_blocks=block.shrunk_blocks,
            cooling_capacity_bound_minutes=block.capacity_minutes,
            cooling_cap_residual_kw=block.cap_residual_kw,
            realised_sd_fraction=realised,
            window_hash=f"sha256:{digest.hexdigest()}",
        )


def _attribute(output: str, y: np.ndarray, cube: np.ndarray, dims: tuple[str, ...]) -> Attribution:
    n = len(y)
    bins = max(2, math.isqrt(n))
    total = float(np.var(y))
    mean = float(y.mean())
    shares: list[tuple[str, float, float]] = []
    for d, label in enumerate(dims):
        b = np.minimum((cube[:, d] * bins).astype(int), bins - 1)
        explained = 0.0
        for k in range(bins):
            sel = b == k
            if sel.any():
                explained += float(sel.sum()) / n * (float(y[sel].mean()) - mean) ** 2
        raw = explained / total if total > 0 else 0.0
        adjusted = max(0.0, 1.0 - (1.0 - raw) * (n - 1) / (n - bins)) if total > 0 else 0.0
        shares.append((label, adjusted, raw))
    shares.sort(key=lambda s: (-s[1], s[0]))
    return Attribution(
        output=output,
        shares=tuple(shares),
        remainder=(1.0 - sum(s[1] for s in shares)) if total > 0 else 0.0,
        bins=bins,
        binning_bias=(bins - 1) / n,
    )


def synthesise(
    inputs: DcLoadInputs,
    *,
    seed: int,
    draws: int,
    workload_flex: WorkloadFlex = NO_ENVELOPE_DECLARED,
) -> Synthesis:
    """Build the ensemble. ``seed`` and ``draws`` are required; neither has a default.

    The same inputs, seed and draws give the same `Synthesis.series_hash` on every run.
    ``workload_flex`` is the operator's optional IC-9 turn-down declaration (WP-SITE-07); its
    default, `NO_ENVELOPE_DECLARED`, leaves IT load untouched, and so does a declaration: it is
    bound beside the ensemble (`Synthesis.workload_flex`), never read by it (D126).
    """
    base = _synthesise_baseline(inputs, seed=seed, draws=draws)
    if workload_flex is NO_ENVELOPE_DECLARED:
        return base
    binding = bind(
        workload_flex,  # type: ignore[arg-type]  # a non-declaration is refused by `bind`
        site_id=inputs.site_id,
        timestamps=base.timestamps,
        series_hash=base.series_hash,
    )
    return replace(base, workload_flex=binding)


def _synthesise_baseline(inputs: DcLoadInputs, *, seed: int, draws: int) -> Synthesis:
    """The ensemble itself, as built before IC-9 existed (the body of `synthesise` until
    WP-SITE-07, moved unchanged). It takes no declaration, so none can reach it."""
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    needed = minimum_draws()
    if draws < needed:
        raise ValueError(
            f"draws={draws} is too few: a distribution-free {CONFIDENCE:.0%} interval on the "
            f"P05 and P95 band edges needs at least {needed} draws"
        )
    space = ParameterSpace.of(inputs)
    values, cube = space.sample(seed, draws)
    params = DrawParameters.build(inputs, values)
    engine = _Engine(inputs, seed, draws, PlantModel(inputs), params)

    days = inputs.days
    hh = engine.half_hourly(0, days)
    comps = hh.components
    members = comps.facility_kw
    components = {
        "it": comps.it_kw,
        "ups_loss": comps.ups_loss_kw,
        "distribution": comps.distribution_kw,
        "auxiliary": comps.auxiliary_kw,
        "cooling": comps.cooling_kw,
    }
    start = datetime.combine(inputs.horizon_start, datetime.min.time())
    timestamps = tuple(
        start + timedelta(minutes=BLOCK_MINUTES * i) for i in range(days * HALF_HOURS_PER_DAY)
    )

    # One-minute pass over the whole horizon, for the per-draw one-minute peak.
    peak_minute = np.full(draws, -np.inf)
    shrunk = capacity_minutes = 0
    cap_residual = 0.0
    jensen_max = 0.0
    for d0 in range(0, days, _MINUTE_CHUNK_DAYS):
        block = engine.minutes(d0, min(days, d0 + _MINUTE_CHUNK_DAYS))
        peak_minute = np.maximum(peak_minute, block.facility.max(axis=1))
        shrunk += block.shrunk_blocks
        capacity_minutes += block.capacity_minutes
        cap_residual = max(cap_residual, block.cap_residual_kw)
        jensen_max = max(jensen_max, float(np.max(np.abs(block.jensen), initial=0.0)))

    expected = members.mean(axis=0)
    ordered = np.sort(members, axis=0)
    bands: dict[str, np.ndarray] = {}
    band_ci: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for p in LEVELS:
        name = LEVEL_NAMES[p]
        bands[name] = np.quantile(members, p, axis=0, method="inverted_cdf")
        ranks = order_statistic_interval(draws, p)
        assert ranks is not None
        band_ci[name] = (ordered[ranks[0] - 1], ordered[ranks[1] - 1])
    medoid = int(np.argmin(((members - expected[None, :]) ** 2).sum(axis=1)))

    # Aggregates, each computed per draw, then quantiled.
    energy = members * 0.5 / 1000.0  # MWh per half-hour
    per_draw: list[tuple[str, str, np.ndarray]] = [
        ("energy_horizon", "MWh", energy.sum(axis=1)),
        ("peak_half_hourly", "kW", members.max(axis=1)),
        ("peak_one_minute", "kW", peak_minute),
    ]
    day_dates = [inputs.horizon_start + timedelta(days=d) for d in range(days)]
    for label, key in (("year", "%Y"), ("month", "%Y-%m")):
        groups: dict[str, list[int]] = {}
        for d, day in enumerate(day_dates):
            groups.setdefault(day.strftime(key), []).append(d)
        for name, ds in groups.items():
            idx = np.concatenate(
                [np.arange(d * HALF_HOURS_PER_DAY, (d + 1) * HALF_HOURS_PER_DAY) for d in ds]
            )
            period_days = (
                (366 if calendar.isleap(int(name[:4])) else 365)
                if label == "year"
                else calendar.monthrange(int(name[:4]), int(name[5:7]))[1]
            )
            suffix = "" if len(ds) == period_days else f" ({len(ds)} of {period_days} days)"
            per_draw.append((f"energy_{label}_{name}{suffix}", "MWh", energy[:, idx].sum(axis=1)))
    aggregates = []
    for name, unit, vec in per_draw:
        q, ci = _quantiles_with_ci(vec)
        aggregates.append(Aggregate(name, unit, float(vec.mean()), q, ci))

    dims = space.dimensions
    attribution = (
        _attribute("energy_horizon", per_draw[0][2], cube, dims),
        _attribute("peak_half_hourly", per_draw[1][2], cube, dims),
    )

    flags = {
        "it_clipped_intervals": int(np.count_nonzero(hh.it_clipped)),
        "ups_overload_intervals": int(np.count_nonzero(comps.ups_overload)),
        "cooling_floor_bound_intervals": int(np.count_nonzero(comps.cooling_floor_bound)),
        "cooling_capacity_bound_intervals": int(np.count_nonzero(comps.cooling_capacity_bound)),
        "one_minute_shrunk_blocks": shrunk,
        "one_minute_cooling_capacity_bound_minutes": capacity_minutes,
    }
    warnings = _warnings(inputs, params, space.citations, flags, cap_residual, jensen_max)

    ambient_json = inputs.ambient.model_dump(mode="json")
    manifest: dict[str, object] = {
        "method": "gridq.datacentre",
        "method_version": METHOD_VERSION,
        "scheme": SCHEME_ID,
        "streams": {
            "parameters": PARAMETER_STREAM,
            "one_minute": MINUTE_STREAM,
            "dip_phase": DIP_PHASE_STREAM,
        },
        "seed": seed,
        "draws": draws,
        "levels": [LEVEL_NAMES[p] for p in LEVELS],
        "quantile_definition": QUANTILE_DEFINITION,
        "simulation_error_interval": (
            f"distribution-free binomial order-statistic interval, {CONFIDENCE:.0%} two-sided"
        ),
        "block_minutes": BLOCK_MINUTES,
        "time_basis": "naive local time, 48 half-hours and 1,440 minutes per day",
        "half_hourly_series": "model evaluated at half-hourly mean load and ambient",
        "inputs_sha256": hash_json(inputs.model_dump(mode="json")),
        "ambient_sha256": hash_json(ambient_json),
        "dimensions": list(dims),
    }
    digest = hashlib.sha256()
    digest.update(_rounded_bytes(members))
    digest.update(_rounded_bytes(expected))
    for p in LEVELS:
        digest.update(_rounded_bytes(bands[LEVEL_NAMES[p]]))
    digest.update(stable_json_dumps(manifest).encode("utf-8"))

    return Synthesis(
        inputs=inputs,
        seed=seed,
        draws=draws,
        timestamps=timestamps,
        members_kw=members,
        components_kw=components,
        expected_kw=expected,
        bands_kw=bands,
        band_ci_kw=band_ci,
        medoid_draw=medoid,
        aggregates=tuple(aggregates),
        attribution=attribution,
        dimensions=dims,
        citations=space.citations,
        flags=flags,
        warnings=warnings,
        manifest=manifest,
        series_hash=f"sha256:{digest.hexdigest()}",
        _engine=engine,
    )


def _warnings(
    inputs: DcLoadInputs,
    params: DrawParameters,
    citations: tuple[Citation, ...],
    flags: dict[str, int],
    cap_residual: float,
    jensen_max: float,
) -> tuple[str, ...]:
    out = [
        "Pre-operational data-centre load MODELLED from site-declared inputs (gridq.datacentre); "
        "no meter or invoice total stands behind it. Bands are pointwise, not simultaneous, must "
        "not be summed across intervals, and are uncalibrated until the spec 16.2 back-test.",
        "The half-hourly series is the model evaluated at half-hourly mean load and ambient; the "
        "one-minute series is conditioned to reproduce it exactly.",
        f"Bands are conditional on the supplied ambient series ({inputs.ambient.source}).",
    ]
    if inputs.ups.efficiency.kind == "constant":
        out.append(
            f"UPS efficiency is a constant, so the {inputs.ups.redundancy} redundancy "
            "configuration has no electrical effect in this series."
        )
    if inputs.variability.mode != "none":
        out.append(
            "The one-minute AR(1) restarts at midnight (each day is drawn alone): a known "
            "artefact of the per-day random access."
        )
    for c in citations:
        out.append(f"Preset used: {c.render()}")
    for r, rms in sorted(params.duty_point_rms_kw.items()):
        out.append(
            f"Cooling regime {r} solved from duty points: RMS residual {float(rms.mean()):.3f} kW "
            f"(mean over draws; max {float(rms.max()):.3f} kW)."
        )
    for name, count in flags.items():
        if count:
            out.append(f"{name}: {count} (draw x interval) bound(s) applied and flagged.")
    if cap_residual > 1e-9:
        out.append(
            f"Capping the re-centred one-minute cooling at its declared capacity left a half-hour "
            f"mean up to {cap_residual:.3f} kW off its half-hourly value."
        )
    out.append(f"Largest one-minute Jensen gap in any half-hour: {jensen_max:.3f} kW.")
    return tuple(out)
