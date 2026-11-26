"""Parallel evaluation harness (issue 016 stage 4) — concurrent independent exact-LP solves.

The wider continuous search is only affordable if many full-year dispatch+finance evaluations can
run concurrently. This module holds the harness: score a list of ``(power_kw, energy_kwh)``
points across worker threads, each worker owning its **own** evaluator instance (the dispatch
solver is stateful — ``last_schedule()`` — and every HiGHS solve is itself single-threaded with a
fixed seed, so instances are never shared and per-solve determinism is untouched), with a
**deterministic ordered reduction**: the output is identical to the sequential
``evaluate_many(points)`` regardless of worker scheduling.

`benchmark_parallel_evaluation` wraps the same call with wall-clock measurement — the stage-4
"can we afford the wider scope?" instrument. Its numbers are a **dev artifact**: they are never
persisted onto a quote or any pricing-path object (wall-clock is machine-dependent and
non-deterministic by nature; the ScoredPoints it returns are the deterministic product).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from gridq.sizing.evaluator import ContinuousSizingEvaluator, ScoredPoint

# A zero-argument constructor for a fresh evaluator — called once per worker, so each worker gets
# its own solver state. The factory must be thread-safe to CALL (it is trivially so when it just
# constructs an evaluator over shared read-only site/assumption models).
EvaluatorFactory = Callable[[], ContinuousSizingEvaluator]


def _resolve_workers(n_points: int, max_workers: int | None) -> int:
    """Clamp the worker count to [1, n_points]; default to the machine's CPU count."""
    requested = max_workers if max_workers is not None else (os.cpu_count() or 1)
    return max(1, min(requested, n_points))


def evaluate_points_parallel(
    evaluator_factory: EvaluatorFactory,
    points: Iterable[tuple[float, float]],
    *,
    max_workers: int | None = None,
) -> list[ScoredPoint]:
    """Score points concurrently; results come back in input order (deterministic reduction).

    Points are dealt round-robin into one chunk per worker; each worker builds its own evaluator
    from ``evaluator_factory`` and scores its chunk sequentially; results are reassembled by
    original index. Identical inputs → identical outputs, equal to the sequential path — worker
    scheduling can reorder *execution*, never the *result*.
    """
    pts = list(points)
    if not pts:
        return []
    workers = _resolve_workers(len(pts), max_workers)
    if workers == 1:
        return evaluator_factory().evaluate_many(pts)

    chunks = [list(range(i, len(pts), workers)) for i in range(workers)]

    def _run_chunk(indices: list[int]) -> list[tuple[int, ScoredPoint]]:
        evaluator = evaluator_factory()
        return [(i, evaluator.evaluate(*pts[i])) for i in indices]

    slots: list[ScoredPoint | None] = [None] * len(pts)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for chunk_result in pool.map(_run_chunk, chunks):
            for i, scored in chunk_result:
                slots[i] = scored
    return [s for s in slots if s is not None]


@dataclass(frozen=True)
class SolveBenchmark:
    """Wall-clock economics of one harness run (dev artifact — never persisted to a quote).

    ``seconds_per_solve`` is wall-clock ÷ points (i.e. *effective* per-point cost at this worker
    count, not single-solve latency); ``solves_per_hour`` is its reciprocal scaled to an hour —
    the number that answers whether a dense exact grid of a given resolution is affordable.
    """

    points: int
    workers: int
    wall_clock_s: float
    seconds_per_solve: float
    solves_per_hour: float
    objective_metric: str


def benchmark_parallel_evaluation(
    evaluator_factory: EvaluatorFactory,
    points: Iterable[tuple[float, float]],
    *,
    max_workers: int | None = None,
) -> tuple[list[ScoredPoint], SolveBenchmark]:
    """Run the harness under a wall-clock and return (deterministic results, timing report)."""
    pts = list(points)
    start = time.perf_counter()
    results = evaluate_points_parallel(evaluator_factory, pts, max_workers=max_workers)
    wall = time.perf_counter() - start
    per = wall / len(pts) if pts else 0.0
    return results, SolveBenchmark(
        points=len(pts),
        workers=_resolve_workers(len(pts), max_workers) if pts else 0,
        wall_clock_s=round(wall, 3),
        seconds_per_solve=round(per, 4),
        solves_per_hour=round(3600.0 / per, 1) if per > 0 else 0.0,
        objective_metric=results[0].objective_metric if results else "",
    )
