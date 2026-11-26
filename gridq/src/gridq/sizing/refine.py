"""Seeded convergent refinement for the sizing search (M4 stage B) — deterministic DE.

A small, dependency-free differential-evolution loop over the continuous (power, duration) box.
It is **deterministic by construction**: a fixed seed drives a private `numpy` generator, the
population update is order-stable, candidates are rounded to a fixed precision before evaluation
(so float noise cannot create distinct near-duplicate solves), and every evaluation is memoised —
identical inputs always converge to the identical sizing.

The evaluator returns an *orderable rank key* (bigger is better — the sizing objective's covenant
gate + ranking projected onto a numeric tuple); this module never looks inside the key, so the
selection policy stays in `gridq.sizing.objective` where it belongs. No £/% constants live here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

RankKey = tuple[float, ...]
Candidate = tuple[float, float]  # (power_kw, duration_hours), rounded


@dataclass(frozen=True)
class RefineTrace:
    """The refinement outcome: the best candidate plus every distinct evaluation, in order."""

    best: Candidate
    best_key: RankKey
    evaluated: list[Candidate]  # distinct rounded candidates, insertion-ordered
    generations_run: int
    converged: bool  # True when the best stalled (vs hitting the generation cap)


def _rounded(power: float, duration: float, bounds: tuple[float, float, float, float]) -> Candidate:
    p_lo, p_hi, d_lo, d_hi = bounds
    p = round(float(np.clip(power, p_lo, p_hi)), 3)
    d = round(float(np.clip(duration, d_lo, d_hi)), 2)
    return (p, d)


def refine_sizing(
    evaluate: Callable[[float, float], RankKey],
    *,
    power_bounds: tuple[float, float],
    duration_bounds: tuple[float, float],
    seed: int,
    seed_candidates: Sequence[Candidate] = (),
    pop_size: int = 12,
    max_generations: int = 16,
    mutation: float = 0.7,
    crossover: float = 0.9,
    stall_generations: int = 5,
) -> RefineTrace:
    """Run seeded DE over the (power, duration) box; return the best candidate + full trace.

    ``seed_candidates`` (e.g. the coarse sweep's best cells) are injected into the initial
    population so the refiner starts from the known-good region; the rest of the population is
    drawn from the seeded generator. Total evaluations are bounded by
    ``pop_size * (max_generations + 1)`` (~200 at the defaults) and memoisation makes repeat
    visits free.
    """
    p_lo, p_hi = float(power_bounds[0]), float(power_bounds[1])
    d_lo, d_hi = float(duration_bounds[0]), float(duration_bounds[1])
    if not (p_lo < p_hi and d_lo < d_hi):
        raise ValueError(
            f"refine_sizing needs a non-degenerate box, got power {power_bounds} "
            f"duration {duration_bounds}"
        )
    bounds = (p_lo, p_hi, d_lo, d_hi)
    rng = np.random.default_rng(seed)

    memo: dict[Candidate, RankKey] = {}

    def score(power: float, duration: float) -> tuple[Candidate, RankKey]:
        cand = _rounded(power, duration, bounds)
        if cand not in memo:
            memo[cand] = evaluate(cand[0], cand[1])
        return cand, memo[cand]

    # Initial population: injected seeds first (deduplicated, in order), then random fill.
    population: list[Candidate] = []
    keys: list[RankKey] = []
    for p, d in seed_candidates:
        cand, key = score(p, d)
        if cand not in population:
            population.append(cand)
            keys.append(key)
        if len(population) >= pop_size:
            break
    while len(population) < pop_size:
        p = p_lo + rng.random() * (p_hi - p_lo)
        d = d_lo + rng.random() * (d_hi - d_lo)
        cand, key = score(p, d)
        if cand in population:
            continue  # rounding collision — draw again (deterministic: same rng stream)
        population.append(cand)
        keys.append(key)

    best_idx = max(range(pop_size), key=lambda i: (keys[i], -population[i][0], -population[i][1]))
    best, best_key = population[best_idx], keys[best_idx]

    stalled = 0
    generations = 0
    converged = False
    for _ in range(max_generations):
        generations += 1
        improved = False
        for i in range(pop_size):
            # DE/rand/1/bin: three distinct partners, none equal to i.
            choices = [j for j in range(pop_size) if j != i]
            a, b, c = rng.choice(choices, size=3, replace=False)
            pa, pb, pc = population[a], population[b], population[c]
            trial_p = pa[0] + mutation * (pb[0] - pc[0])
            trial_d = pa[1] + mutation * (pb[1] - pc[1])
            # Binomial crossover with a guaranteed-mutant coordinate.
            keep_dim = int(rng.integers(0, 2))
            if rng.random() > crossover and keep_dim != 0:
                trial_p = population[i][0]
            if rng.random() > crossover and keep_dim != 1:
                trial_d = population[i][1]
            cand, key = score(trial_p, trial_d)
            if key > keys[i]:
                population[i], keys[i] = cand, key
                if (key, -cand[0], -cand[1]) > (best_key, -best[0], -best[1]):
                    best, best_key = cand, key
                    improved = True
        if improved:
            stalled = 0
        else:
            stalled += 1
            if stalled >= stall_generations:
                converged = True
                break

    return RefineTrace(
        best=best,
        best_key=best_key,
        evaluated=list(memo.keys()),
        generations_run=generations,
        converged=converged,
    )
