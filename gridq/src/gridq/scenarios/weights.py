"""Deterministic scenario-weight helpers.

Pure arithmetic over probability weights — no randomness, no Monte Carlo. The rule:
discrete scenario weights should sum to 1.0; if they do not, they are normalised and a
warning is raised so the deviation is never silent. Operates on plain floats so it has no
dependency on the scenario models (keeps the package import graph acyclic).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Tolerance for "weights sum to 1.0". Discrete weights such as 0.25/0.50/0.25 sum exactly;
# this absorbs only floating-point dust, never genuine mis-weighting.
WEIGHT_SUM_TOL = 1e-9


@dataclass(frozen=True)
class NormalisedWeights:
    """Result of normalising a weight vector (deterministic, order-preserving)."""

    weights: tuple[float, ...]
    normalised: bool
    original_sum: float
    warnings: tuple[str, ...] = field(default_factory=tuple)


def weights_sum_ok(weights: Sequence[float], *, tol: float = WEIGHT_SUM_TOL) -> bool:
    """True if ``weights`` already sum to 1.0 within ``tol``."""
    return abs(sum(weights) - 1.0) <= tol


def normalise_weights(
    weights: Sequence[float], *, tol: float = WEIGHT_SUM_TOL
) -> NormalisedWeights:
    """Normalise ``weights`` to sum to 1.0, deterministically.

    - Already-normalised input is returned unchanged (``normalised=False``).
    - Off-sum input is divided by its total and a warning is recorded.
    - A non-positive total cannot be normalised; the function falls back to equal weights
      with a warning (rather than dividing by zero or inventing a distribution).
    """
    vec = tuple(float(w) for w in weights)

    if not vec:
        return NormalisedWeights(
            weights=(),
            normalised=False,
            original_sum=0.0,
            warnings=("no scenario weights to normalise.",),
        )

    total = sum(vec)

    if total <= 0.0:
        n = len(vec)
        equal = tuple(1.0 / n for _ in vec)
        return NormalisedWeights(
            weights=equal,
            normalised=True,
            original_sum=total,
            warnings=(
                f"scenario weights sum to {total:.6g} (<= 0); cannot normalise — "
                f"falling back to equal weights of {1.0 / n:.6g} each.",
            ),
        )

    if abs(total - 1.0) <= tol:
        return NormalisedWeights(weights=vec, normalised=False, original_sum=total, warnings=())

    return NormalisedWeights(
        weights=tuple(w / total for w in vec),
        normalised=True,
        original_sum=total,
        warnings=(
            f"scenario weights summed to {total:.6g}, not 1.0; normalised by dividing by "
            f"the total so they sum to 1.0.",
        ),
    )
