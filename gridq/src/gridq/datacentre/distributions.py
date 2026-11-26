"""Stated uncertainty: the distributions an input may carry, and the seeded streams sampling them.

Decision D83 (Fable 5.2): the bands come from the uncertainty the user STATES on each
input, sampled by a seeded Latin hypercube. Three rules follow from it and are enforced here.

- **No distribution, width or shape has a default.** A user states one of the families below. A
  cited preset (`gridq.datacentre.presets`) returns one too, with its `Citation`; where the source
  gives only a range, the shape put on that range (uniform) is labelled as this package's choice.
- **No numpy or scipy distribution sampler is used.** Every random number is an inverse transform
  of raw 53-bit uniforms drawn from a pinned bit generator (PCG64, seeded through `SeedSequence`).
  numpy guarantees a bit generator's stream across releases; it does not guarantee its samplers'.
  The normal inverse is the standard library's `statistics.NormalDist.inv_cdf`.
- **Streams are disjoint and addressable.** A stream is keyed by ``(seed, stream id, ...)``, so the
  parameter draws, the one-minute noise of any (draw, day) and the dip phases never share numbers,
  and any one of them can be regenerated alone.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Annotated, Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gridq.datacentre.sources import Citation

__all__ = [
    "DIP_PHASE_STREAM",
    "MINUTE_STREAM",
    "PARAMETER_STREAM",
    "Discrete",
    "Distribution",
    "Fixed",
    "Normal",
    "Quantiles",
    "Stated",
    "Triangular",
    "Uniform",
    "latin_hypercube",
    "standard_normals",
    "uniforms",
]

#: Stream ids. Changing one changes every series, so they are part of the method version.
PARAMETER_STREAM: Final = 1
MINUTE_STREAM: Final = 2
DIP_PHASE_STREAM: Final = 3

_TWO_POW_53: Final = float(2**53)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fixed(_Frozen):
    """A value stated as certain. It adds no dimension to the hypercube."""

    kind: Literal["fixed"] = "fixed"
    value: float

    def ppf(self, u: np.ndarray) -> np.ndarray:
        return np.full(np.shape(u), self.value, dtype=float)

    def bounds(self) -> tuple[float, float]:
        return (self.value, self.value)


class Uniform(_Frozen):
    kind: Literal["uniform"] = "uniform"
    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> Uniform:
        if not self.low < self.high:
            raise ValueError("uniform needs low < high (state a Fixed value for a point)")
        return self

    def ppf(self, u: np.ndarray) -> np.ndarray:
        return self.low + (self.high - self.low) * np.asarray(u, dtype=float)

    def bounds(self) -> tuple[float, float]:
        return (self.low, self.high)


class Triangular(_Frozen):
    kind: Literal["triangular"] = "triangular"
    low: float
    mode: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> Triangular:
        if not (self.low <= self.mode <= self.high and self.low < self.high):
            raise ValueError("triangular needs low <= mode <= high and low < high")
        return self

    def ppf(self, u: np.ndarray) -> np.ndarray:
        u = np.asarray(u, dtype=float)
        a, c, b = self.low, self.mode, self.high
        split = (c - a) / (b - a)
        left = a + np.sqrt(u * (b - a) * (c - a))
        right = b - np.sqrt((1.0 - u) * (b - a) * (b - c))
        return np.where(u < split, left, right)

    def bounds(self) -> tuple[float, float]:
        return (self.low, self.high)


class Normal(_Frozen):
    """A normal, optionally truncated to ``[low, high]`` (``None`` = that side is open)."""

    kind: Literal["normal"] = "normal"
    mean: float
    sd: float = Field(gt=0)
    low: float | None
    high: float | None

    @model_validator(mode="after")
    def _ordered(self) -> Normal:
        if self.low is not None and self.high is not None and not self.low < self.high:
            raise ValueError("normal truncation needs low < high")
        return self

    def ppf(self, u: np.ndarray) -> np.ndarray:
        dist = NormalDist(self.mean, self.sd)
        lo = 0.0 if self.low is None else dist.cdf(self.low)
        hi = 1.0 if self.high is None else dist.cdf(self.high)
        flat = np.asarray(u, dtype=float).ravel()
        out = np.array([dist.inv_cdf(lo + (hi - lo) * float(p)) for p in flat], dtype=float)
        if self.low is not None or self.high is not None:
            out = np.clip(
                out,
                -np.inf if self.low is None else self.low,
                np.inf if self.high is None else self.high,
            )
        return out.reshape(np.shape(u))

    def bounds(self) -> tuple[float, float]:
        return (
            -math.inf if self.low is None else self.low,
            math.inf if self.high is None else self.high,
        )


class Discrete(_Frozen):
    """One of a stated set of values, with stated weights."""

    kind: Literal["discrete"] = "discrete"
    values: tuple[float, ...] = Field(min_length=1)
    weights: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _weights(self) -> Discrete:
        if len(self.values) != len(self.weights):
            raise ValueError("discrete needs one weight per value")
        if any(w <= 0 for w in self.weights):
            raise ValueError("discrete weights must be positive")
        return self

    def ppf(self, u: np.ndarray) -> np.ndarray:
        cum = np.cumsum(np.asarray(self.weights, dtype=float))
        cum = cum / cum[-1]
        idx = np.searchsorted(cum, np.asarray(u, dtype=float), side="right")
        idx = np.minimum(idx, len(self.values) - 1)
        return np.asarray(self.values, dtype=float)[idx]

    def bounds(self) -> tuple[float, float]:
        return (min(self.values), max(self.values))


class Quantiles(_Frozen):
    """A tabulated inverse CDF: ``(probability, value)`` points from 0 to 1, linear between.

    The form an elicited P0/P10/P50/P90/P100 statement takes.
    """

    kind: Literal["quantiles"] = "quantiles"
    points: tuple[tuple[float, float], ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _monotone(self) -> Quantiles:
        ps = [p for p, _ in self.points]
        xs = [x for _, x in self.points]
        if ps[0] != 0.0 or ps[-1] != 1.0:
            raise ValueError("quantile table must start at probability 0 and end at 1")
        if any(b <= a for a, b in zip(ps, ps[1:], strict=False)):
            raise ValueError("quantile probabilities must strictly increase")
        if any(b < a for a, b in zip(xs, xs[1:], strict=False)):
            raise ValueError("quantile values must not decrease")
        return self

    def ppf(self, u: np.ndarray) -> np.ndarray:
        ps = np.asarray([p for p, _ in self.points], dtype=float)
        xs = np.asarray([x for _, x in self.points], dtype=float)
        return np.interp(np.asarray(u, dtype=float), ps, xs)

    def bounds(self) -> tuple[float, float]:
        return (self.points[0][1], self.points[-1][1])


Distribution = Annotated[
    Fixed | Uniform | Triangular | Normal | Discrete | Quantiles, Field(discriminator="kind")
]


class Stated(_Frozen):
    """An uncertain input: its distribution and who stated it.

    ``citation`` is ``None`` for a value the user stated, and the preset's `Citation` for a value
    taken from a published source. Build one with `Stated.user` or a preset; nothing defaults.
    """

    distribution: Distribution
    citation: Citation | None

    @classmethod
    def user(
        cls, distribution: Fixed | Uniform | Triangular | Normal | Discrete | Quantiles
    ) -> Stated:
        return cls(distribution=distribution, citation=None)

    @property
    def is_fixed(self) -> bool:
        return isinstance(self.distribution, Fixed)

    def ppf(self, u: np.ndarray) -> np.ndarray:
        return self.distribution.ppf(u)

    def bounds(self) -> tuple[float, float]:
        return self.distribution.bounds()


# --------------------------------------------------------------------------- seeded streams


def uniforms(seed: int, key: tuple[int, ...], n: int) -> np.ndarray:
    """``n`` uniforms in the open interval (0, 1) from the stream ``(seed, *key)``.

    Built from the bit generator's raw 64-bit output (top 53 bits, centred in their cell), which
    numpy keeps stable across releases, so the same seed and key give the same numbers everywhere.
    """
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    bits = np.random.PCG64(np.random.SeedSequence(entropy=seed, spawn_key=key))
    raw = bits.random_raw(n)
    return ((raw >> np.uint64(11)).astype(np.float64) + 0.5) / _TWO_POW_53


def latin_hypercube(seed: int, draws: int, dims: int) -> np.ndarray:
    """A seeded Latin hypercube, shape ``(draws, dims)``, in (0, 1).

    Each column holds exactly one point in each of the ``draws`` equal strata, in a random order.
    The order is an argsort of raw uniforms (ties have probability ~2^-53 and break by index), and
    the position inside each stratum is a second raw uniform. Both come from `PARAMETER_STREAM`.
    """
    if draws < 1:
        raise ValueError("draws must be at least 1")
    out = np.empty((draws, dims), dtype=float)
    for d in range(dims):
        order = np.argsort(uniforms(seed, (PARAMETER_STREAM, d, 0), draws), kind="stable")
        jitter = uniforms(seed, (PARAMETER_STREAM, d, 1), draws)
        strata = np.empty(draws, dtype=float)
        strata[order] = np.arange(draws, dtype=float)
        out[:, d] = (strata + jitter) / draws
    return out


def standard_normals(seed: int, key: tuple[int, ...], n: int) -> np.ndarray:
    """``n`` standard normals from the stream ``(seed, *key)``, by the Box-Muller transform.

    One stream supplies both uniform blocks (first half, second half), so a (draw, day) needs one
    generator, and the transform is written out here rather than taken from a numpy sampler.
    """
    half = (n + 1) // 2
    u = uniforms(seed, key, 2 * half)
    u1, u2 = u[:half], u[half:]
    r = np.sqrt(-2.0 * np.log(u1))
    z = np.concatenate([r * np.cos(2.0 * np.pi * u2), r * np.sin(2.0 * np.pi * u2)])
    return z[:n]
