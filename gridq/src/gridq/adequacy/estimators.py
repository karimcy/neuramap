"""Exact estimators for the adequacy result (D171). No floats reach a hashed value.

- **Clopper-Pearson on a ppm grid.** The binomial tails are evaluated in exact integer arithmetic
  at ``p = j / 10^6``; the lower bound is the largest grid point whose upper tail is within
  ``alpha / 2`` (so it rounds down), the upper bound the smallest whose lower tail is (rounds up).
  Across scenarios the error budget is split (Bonferroni, ``alpha / S`` each) and the bounds are
  weighted and summed, so the combined interval holds at the stated confidence.
- **Bootstrap** of the expected unserved energy: indices come from a counter-mode SHA-256 stream
  (``sha256("gridq.adequacy.bootstrap/1|{seed}|{counter}")`` read as eight big-endian uint32, with
  rejection sampling), sums are integers, and the interval is ranks 50 and 1,950 of 2,000.

Every interval here covers the **load-draw sampling error only**: not the scenario weights, not
the load model's own structure, not the battery parameters. The result says so.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from fractions import Fraction
from math import comb
from typing import Final

__all__ = [
    "BOOTSTRAP_HI_RANK",
    "BOOTSTRAP_LO_RANK",
    "BOOTSTRAP_RESAMPLES",
    "PPM",
    "bootstrap_mean_interval",
    "clopper_pearson_ppm",
    "index_stream",
]

PPM: Final = 1_000_000
BOOTSTRAP_RESAMPLES: Final = 2000
BOOTSTRAP_LO_RANK: Final = 50
BOOTSTRAP_HI_RANK: Final = 1950


def _upper_tail_scaled(n: int, k: int, j: int) -> int:
    """``P(X >= k) x PPM^n`` for ``X ~ Bin(n, j / PPM)``, exactly."""
    q = PPM - j
    return sum(comb(n, i) * j**i * q ** (n - i) for i in range(k, n + 1))


def _lower_tail_scaled(n: int, k: int, j: int) -> int:
    """``P(X <= k) x PPM^n``."""
    q = PPM - j
    return sum(comb(n, i) * j**i * q ** (n - i) for i in range(0, k + 1))


def clopper_pearson_ppm(k: int, n: int, alpha: Fraction) -> tuple[int, int]:
    """Two-sided exact interval for ``k`` successes in ``n``, in ppm, rounded outward."""
    if not 0 <= k <= n or n < 1:
        raise ValueError("need 0 <= k <= n and n >= 1")
    half = alpha / 2
    scale = PPM**n
    bound = half * scale  # compare tail x PPM^n <= half x PPM^n

    if k == 0:
        lo = 0
    else:
        # Largest j with P(X >= k | j) <= half; the tail increases with j.
        a, b = 0, PPM
        while a < b:
            mid = (a + b + 1) // 2
            if _upper_tail_scaled(n, k, mid) <= bound:
                a = mid
            else:
                b = mid - 1
        lo = a
    if k == n:
        hi = PPM
    else:
        # Smallest j with P(X <= k | j) <= half; the tail decreases with j.
        a, b = 0, PPM
        while a < b:
            mid = (a + b) // 2
            if _lower_tail_scaled(n, k, mid) <= bound:
                b = mid
            else:
                a = mid + 1
        hi = a
    return lo, hi


def index_stream(seed: int, n: int, count: int) -> list[int]:
    """``count`` uniform indices in ``[0, n)`` from the counter-mode SHA-256 stream."""
    limit = (2**32 // n) * n
    out: list[int] = []
    counter = 0
    while len(out) < count:
        block = hashlib.sha256(f"gridq.adequacy.bootstrap/1|{seed}|{counter}".encode()).digest()
        counter += 1
        for w in range(8):
            v = int.from_bytes(block[4 * w : 4 * w + 4], "big")
            if v < limit:
                out.append(v % n)
                if len(out) == count:
                    break
    return out


def bootstrap_mean_interval(values: Sequence[int], seed: int) -> tuple[int, int]:
    """Ranks 50 and 1,950 of 2,000 resampled **sums** of ``values`` (divide by ``len``)."""
    n = len(values)
    idx = index_stream(seed, n, n * BOOTSTRAP_RESAMPLES)
    sums = sorted(
        sum(values[i] for i in idx[b * n : (b + 1) * n]) for b in range(BOOTSTRAP_RESAMPLES)
    )
    return sums[BOOTSTRAP_LO_RANK - 1], sums[BOOTSTRAP_HI_RANK - 1]
