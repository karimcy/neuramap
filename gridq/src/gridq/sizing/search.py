"""Sizing search engine (issue 016 stage 5) — the dense exact-grid reference search.

The dense grid **is** the reference surface: every point of ``SizingDomain.grid(n_power,
n_energy)`` scored through the exact dispatch+finance evaluator (via the stage-4 parallel
harness), with a deterministic selection. The adaptive refiner (stages 5b/6) is certified
AGAINST this instrument — never the other way round — and per D28 the surface it measures is
piecewise-constant across the {1,2,4}h duration-key boundaries (conservative flooring), so the
reference carries the same steps the refiner must respect.

Selection is **metric-honest** (the issue-016 scope addition): points are ranked first by the
tier of the metric they were actually scored on (levered return > project IRR > priced-floor
fallback — a priceable point always outranks an unpriceable one), then by value within the tier,
then by the smaller system (deterministic tie-break, mirroring ``SizingObjective.rank_key``).
``best`` is the indicative winner; ``best_feasible`` is the covenant-gated winner and is honestly
``None`` when no point passes (DSCR unavailable without injected debt terms — B001 — or every
point fails the covenant). Nothing here can make a result bankable; that verdict stays with
finance and the bankability gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from gridq.schemas import SizingGrid
from gridq.sizing.domain import SizingDomain
from gridq.sizing.evaluator import (
    METRIC_LEVERED_RETURN,
    METRIC_PRICED_FLOOR,
    METRIC_PROJECT_IRR,
    ScoredPoint,
)
from gridq.sizing.parallel import EvaluatorFactory, evaluate_points_parallel
from gridq.sizing.search_space import DEFAULT_DURATION_HOURS
from gridq.sizing.sizer import _grid_point

# Metric tiers for ranking: prefer points scored on the better metric before comparing values —
# comparing an IRR% against a floor-£ would be meaningless (see evaluator._score's order).
_METRIC_RANK = {METRIC_LEVERED_RETURN: 2, METRIC_PROJECT_IRR: 1, METRIC_PRICED_FLOOR: 0}


def _rank_key(point: ScoredPoint) -> tuple[int, float, float, float]:
    """Deterministic sort key where the maximum is the best point (metric tier → value → size)."""
    return (
        _METRIC_RANK.get(point.objective_metric, -1),
        point.objective_value,
        -point.power_kw,
        -point.duration_hours,
    )


@dataclass(frozen=True)
class RefinementRound:
    """One point on the adaptive search's convergence curve (dev-legible; never quote data)."""

    round_index: int
    evaluations_total: int
    incumbent_power_kw: float
    incumbent_energy_kwh: float
    incumbent_value: float
    incumbent_feasible: bool


@dataclass(frozen=True)
class SizingSearchResult:
    """The outcome of one sizing search over a continuous domain.

    ``evaluated`` retains every scored point in deterministic evaluation order (the full surface
    — stage 7 persists it for the 3D chart); ``best`` is the indicative winner by the
    metric-honest rank; ``best_feasible`` is the covenant-gated winner (``None`` when no point
    passes — honest, never a substitute). ``method`` names the search that produced it so a
    refined result can never masquerade as the dense reference in the stage-9 bake-off.
    ``rounds`` is the convergence curve (empty for the single-pass dense reference).
    """

    method: str
    evaluated: list[ScoredPoint]
    best: ScoredPoint | None
    best_feasible: ScoredPoint | None
    n_evaluations: int
    warnings: list[str] = field(default_factory=list)
    rounds: list[RefinementRound] = field(default_factory=list)


def dense_grid_search(
    evaluator_factory: EvaluatorFactory,
    domain: SizingDomain,
    *,
    n_power: int,
    n_energy: int,
    max_workers: int | None = None,
) -> SizingSearchResult:
    """Score the full dense in-band grid and select deterministically — the reference method.

    Every in-band point of ``domain.grid(n_power, n_energy)`` is evaluated through the exact
    dispatch+finance path (parallelised, deterministic ordered reduction), so the result is the
    exact objective surface at this resolution — no surrogate, no sampling error. Wall-clock
    scales linearly with points (~0.8 s/point at 4 workers per the stage-4 benchmark).
    """
    candidates = domain.grid(n_power, n_energy)
    points = [(c.power_kw, c.energy_kwh) for c in candidates]
    if not points:
        return SizingSearchResult(
            method="dense_grid",
            evaluated=[],
            best=None,
            best_feasible=None,
            n_evaluations=0,
            warnings=[
                "dense grid produced no in-band points at this resolution (the duration band "
                "filtered every linspace combination) — increase n_power/n_energy."
            ],
        )
    scored = evaluate_points_parallel(evaluator_factory, points, max_workers=max_workers)
    feasible = [p for p in scored if p.feasible]
    warnings: list[str] = []
    if not feasible:
        warnings.append(
            "no covenant-feasible point on the dense grid (DSCR is unavailable without injected "
            "debt terms, or every point fails the covenant) — `best` is indicative only, never "
            "bankable."
        )
    return SizingSearchResult(
        method="dense_grid",
        evaluated=scored,
        best=max(scored, key=_rank_key),
        best_feasible=max(feasible, key=_rank_key) if feasible else None,
        n_evaluations=len(scored),
        warnings=warnings,
    )


# --- surface persistence (stage 7) ------------------------------------------


def search_result_to_sizing_grid(result: SizingSearchResult) -> SizingGrid:
    """Project a search result onto the frozen, persistable `SizingGrid` contract (stage 7).

    The wider continuous surface rides the SAME additive QuoteRecord seam the 3×3 grid does
    (D23) — **no schema change**: every scored point becomes a `SizingGridPoint` (a copy off its
    `CandidateEvaluation`, no maths), in the search's deterministic evaluation order, so the
    Wave-5 3D surface renders the finer surface unchanged. Honesty mirrors `SizingRun`:
    ``selected`` is the covenant-gated winner only (``None`` when no point passes — the
    indicative ``best`` never masquerades as a selection), and ``bankable`` keeps the full
    conjunction (a feasible winner whose finance case is bankable AND that was scored on the
    levered metric) — never ``True`` on placeholders or fallback metrics.
    """
    best_feasible = result.best_feasible
    bankable = bool(
        best_feasible is not None
        and best_feasible.evaluation.bankable
        and best_feasible.objective_metric == METRIC_LEVERED_RETURN
    )
    if best_feasible is not None:
        reason = (
            f"{result.method} search over {result.n_evaluations} continuous candidates: "
            "best feasible candidate (P90 DSCR ≥ covenant) selected."
        )
    else:
        reason = (
            f"{result.method} search over {result.n_evaluations} continuous candidates: no "
            "candidate meets the P90-DSCR covenant (DSCR unavailable without debt terms, or "
            "every candidate fails it) — no selection; the surface is indicative only."
        )
    return SizingGrid(
        # Provenance: name the search that produced this surface (the default would mislabel
        # it as the legacy 3x3 enumeration).
        search_version=f"w6-{result.method}-v1",
        search_space_size=result.n_evaluations,
        covenant=next(
            (p.evaluation.covenant for p in result.evaluated if p.evaluation.covenant is not None),
            None,
        ),
        bankable=bankable,
        selection_reason=reason,
        points=[_grid_point(p.evaluation) for p in result.evaluated],
        selected=None if best_feasible is None else _grid_point(best_feasible.evaluation),
    )


# --- adaptive refinement (stages 5b-6) --------------------------------------


def _incumbent_key(point: ScoredPoint) -> tuple[int, int, float, float, float]:
    """Feasibility-first rank: a covenant-feasible point always outranks an infeasible one.

    The zoom targets the *bankable-objective* optimum when the covenant gate is live (injected
    debt), and degrades to the plain metric-honest rank when it is not (the production B001
    state) — the same policy split `enumerate_sizing`'s selection makes.
    """
    return (1 if point.feasible else 0, *_rank_key(point))


def _step_line_points(
    domain: SizingDomain, n: int, durations: tuple[float, ...]
) -> list[tuple[float, float]]:
    """Sample points ON the duration-key lines (e = p·d for d in the assumption-key durations).

    Per D28 the objective/feasibility surface is piecewise-constant with steps exactly at these
    lines (conservative flooring), so the initial sample must probe them explicitly — a
    smoothness-blind sampler is how the refiner stays step-aware without ever fitting a
    surrogate across a boundary.
    """
    out: list[tuple[float, float]] = []
    for d in durations:
        if not (domain.duration_h_min <= d <= domain.duration_h_max):
            continue
        p_lo = max(domain.power_kw_min, domain.energy_kwh_min / d)
        p_hi = min(domain.power_kw_max, domain.energy_kwh_max / d)
        if p_lo > p_hi:
            continue
        for p in np.linspace(p_lo, p_hi, n):
            pf = round(float(p), 6)
            out.append((pf, round(pf * d, 6)))
    return out


def adaptive_refine_search(
    evaluator_factory: EvaluatorFactory,
    domain: SizingDomain,
    *,
    n_initial: int = 5,
    lhs_initial: int = 8,
    seed: int = 0,
    n_zoom: int = 4,
    zoom_factor: float = 0.5,
    epsilon: float = 1e-3,
    patience: int = 2,
    max_evaluations: int = 120,
    step_durations: tuple[float, ...] = DEFAULT_DURATION_HOURS,
    max_workers: int | None = None,
) -> SizingSearchResult:
    """Adaptive zoom refinement — pins the optimum to sub-grid precision on a budget.

    Method: score a coarse in-band grid + seeded LHS + the duration-key step lines, then
    repeatedly shrink a sub-box (``zoom_factor`` per round) around the incumbent
    (feasibility-first) and score a fresh ``n_zoom×n_zoom`` in-band grid inside it, de-duplicated
    against everything already scored. **No surrogate, no gradient** — pure sampling, so the
    D28 step discontinuities cannot mislead it. Stops when the incumbent fails to improve by
    ``epsilon`` for ``patience`` consecutive rounds, or at the ``max_evaluations`` budget
    (warned). Deterministic: identical (domain, parameters, seed) → identical evaluations,
    rounds and selection. Certified against `dense_grid_search` in the stage-8/9 tests — the
    refiner is the fast path, never the reference.
    """
    if not 0.0 < zoom_factor < 1.0:
        raise ValueError(f"zoom_factor must be in (0, 1), got {zoom_factor}")
    if max_evaluations < 1:
        raise ValueError(f"max_evaluations must be ≥ 1, got {max_evaluations}")

    evaluated: dict[tuple[float, float], ScoredPoint] = {}
    warnings: list[str] = []
    rounds: list[RefinementRound] = []

    def _score_batch(points: list[tuple[float, float]]) -> None:
        fresh: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set(evaluated)
        for pt in points:
            if pt not in seen:
                seen.add(pt)
                fresh.append(pt)
        allowed = max_evaluations - len(evaluated)
        if len(fresh) > allowed:
            fresh = fresh[:allowed]
            warnings.append(
                f"evaluation budget reached (max_evaluations={max_evaluations}) — batch "
                "truncated deterministically; the optimum is the best of the points scored."
            )
        if not fresh:
            return
        for pt, scored in zip(
            fresh,
            evaluate_points_parallel(evaluator_factory, fresh, max_workers=max_workers),
            strict=True,
        ):
            evaluated[pt] = scored

    # Round 0: coarse in-band grid + seeded space-filling LHS + the duration-key step lines.
    initial: list[tuple[float, float]] = [
        (c.power_kw, c.energy_kwh) for c in domain.grid(n_initial, n_initial)
    ]
    if lhs_initial > 0:
        initial += [
            (c.power_kw, c.energy_kwh) for c in domain.latin_hypercube(lhs_initial, seed=seed)
        ]
    initial += _step_line_points(domain, n_initial, step_durations)
    _score_batch(initial)
    if not evaluated:
        return SizingSearchResult(
            method="adaptive_zoom",
            evaluated=[],
            best=None,
            best_feasible=None,
            n_evaluations=0,
            warnings=[
                "adaptive search scored no in-band points (initial sample empty at this "
                "resolution) — increase n_initial/lhs_initial."
            ],
        )

    p_range = domain.power_kw_max - domain.power_kw_min
    e_range = domain.energy_kwh_max - domain.energy_kwh_min
    stale_rounds = 0
    round_index = 0
    incumbent = max(evaluated.values(), key=_incumbent_key)
    rounds.append(
        RefinementRound(
            round_index=round_index,
            evaluations_total=len(evaluated),
            incumbent_power_kw=incumbent.power_kw,
            incumbent_energy_kwh=incumbent.energy_kwh,
            incumbent_value=incumbent.objective_value,
            incumbent_feasible=incumbent.feasible,
        )
    )

    while len(evaluated) < max_evaluations and stale_rounds < patience:
        round_index += 1
        width_p = p_range * (zoom_factor**round_index)
        width_e = e_range * (zoom_factor**round_index)
        p_lo = max(domain.power_kw_min, incumbent.power_kw - width_p / 2)
        p_hi = min(domain.power_kw_max, incumbent.power_kw + width_p / 2)
        e_lo = max(domain.energy_kwh_min, incumbent.energy_kwh - width_e / 2)
        e_hi = min(domain.energy_kwh_max, incumbent.energy_kwh + width_e / 2)
        sub_grid = [
            (round(float(p), 6), round(float(e), 6))
            for p in np.linspace(p_lo, p_hi, n_zoom)
            for e in np.linspace(e_lo, e_hi, n_zoom)
            if domain.contains(round(float(p), 6), round(float(e), 6))
        ]
        _score_batch(sorted(set(sub_grid)))

        challenger = max(evaluated.values(), key=_incumbent_key)
        inc_class = (incumbent.feasible, _METRIC_RANK.get(incumbent.objective_metric, -1))
        cha_class = (challenger.feasible, _METRIC_RANK.get(challenger.objective_metric, -1))
        improved = cha_class > inc_class or (
            cha_class == inc_class
            and challenger.objective_value >= incumbent.objective_value + epsilon
        )
        incumbent = challenger
        stale_rounds = 0 if improved else stale_rounds + 1
        rounds.append(
            RefinementRound(
                round_index=round_index,
                evaluations_total=len(evaluated),
                incumbent_power_kw=incumbent.power_kw,
                incumbent_energy_kwh=incumbent.energy_kwh,
                incumbent_value=incumbent.objective_value,
                incumbent_feasible=incumbent.feasible,
            )
        )

    scored = list(evaluated.values())
    feasible = [p for p in scored if p.feasible]
    if not feasible:
        warnings.append(
            "no covenant-feasible point found by the adaptive search (DSCR is unavailable "
            "without injected debt terms, or every point fails the covenant) — `best` is "
            "indicative only, never bankable."
        )
    return SizingSearchResult(
        method="adaptive_zoom",
        evaluated=scored,
        best=max(scored, key=_rank_key),
        best_feasible=max(feasible, key=_rank_key) if feasible else None,
        n_evaluations=len(scored),
        warnings=warnings,
        rounds=rounds,
    )


# --- seeded PSO baseline (stage 9 comparison method — never the reference) ---


def pso_search(
    evaluator_factory: EvaluatorFactory,
    domain: SizingDomain,
    *,
    n_particles: int = 12,
    seed: int = 0,
    inertia: float = 0.7,
    cognitive: float = 1.5,
    social: float = 1.5,
    epsilon: float = 1e-3,
    patience: int = 3,
    max_evaluations: int = 120,
    max_workers: int | None = None,
) -> SizingSearchResult:
    """Seeded particle-swarm search — the stage-9 comparison baseline, never the reference.

    A deliberately standard PSO over the 2-D (power, energy) box: LHS-initialised particles
    (seeded, in-band), velocity updates from a seeded generator (deterministic draw order), and
    positions clipped back into the band-feasible region every generation (the same window logic
    `latin_hypercube` uses, so no particle ever proposes a degenerate C-rate). Scores each
    generation through the same exact evaluator (batched via the parallel harness, de-duplicated
    against everything already scored, hard ``max_evaluations`` budget). Exists so the bake-off
    can measure the zoom refiner against a recognised stochastic-search baseline on
    evals-to-optimum — identical (domain, parameters, seed) → identical result.
    """
    if n_particles < 2:
        raise ValueError(f"n_particles must be ≥ 2, got {n_particles}")
    if max_evaluations < 1:
        raise ValueError(f"max_evaluations must be ≥ 1, got {max_evaluations}")

    rng = np.random.default_rng(seed)
    evaluated: dict[tuple[float, float], ScoredPoint] = {}
    warnings: list[str] = []
    rounds: list[RefinementRound] = []
    p_win_lo, p_win_hi = domain._band_feasible_power_window()
    p_range = domain.power_kw_max - domain.power_kw_min
    e_range = domain.energy_kwh_max - domain.energy_kwh_min

    def _clip(power: float, energy: float) -> tuple[float, float]:
        # Round power FIRST, then derive the band window from the rounded value and clamp the
        # rounded energy back into it — rounding after clamping can nudge the pair just past the
        # band edge (duration 8.00000005 h), which contains() rightly rejects.
        p = round(min(max(float(power), p_win_lo), p_win_hi), 6)
        e_lo = max(domain.energy_kwh_min, p * domain.duration_h_min)
        e_hi = max(min(domain.energy_kwh_max, p * domain.duration_h_max), e_lo)
        e = min(max(round(min(max(float(energy), e_lo), e_hi), 6), e_lo), e_hi)
        return p, e

    def _score_batch(points: list[tuple[float, float]]) -> bool:
        fresh = [pt for pt in dict.fromkeys(points) if pt not in evaluated]
        allowed = max_evaluations - len(evaluated)
        if len(fresh) > allowed:
            fresh = fresh[:allowed]
            warnings.append(
                f"evaluation budget reached (max_evaluations={max_evaluations}) — generation "
                "truncated deterministically; the optimum is the best of the points scored."
            )
        if not fresh:
            return False
        for pt, scored in zip(
            fresh,
            evaluate_points_parallel(evaluator_factory, fresh, max_workers=max_workers),
            strict=True,
        ):
            evaluated[pt] = scored
        return True

    positions = [(c.power_kw, c.energy_kwh) for c in domain.latin_hypercube(n_particles, seed=seed)]
    velocities = (rng.random((n_particles, 2)) - 0.5) * np.array([p_range, e_range]) * 0.2
    _score_batch(positions)
    if not evaluated:
        return SizingSearchResult(
            method="pso",
            evaluated=[],
            best=None,
            best_feasible=None,
            n_evaluations=0,
            warnings=["PSO scored no points (empty initial swarm at this budget)."],
        )

    pbest = [evaluated[pt] for pt in positions]
    gbest = max(evaluated.values(), key=_incumbent_key)
    generation = 0
    stale = 0
    rounds.append(
        RefinementRound(
            round_index=generation,
            evaluations_total=len(evaluated),
            incumbent_power_kw=gbest.power_kw,
            incumbent_energy_kwh=gbest.energy_kwh,
            incumbent_value=gbest.objective_value,
            incumbent_feasible=gbest.feasible,
        )
    )

    vmax = np.array([p_range, e_range]) * 0.5
    while len(evaluated) < max_evaluations and stale < patience:
        generation += 1
        r1 = rng.random((n_particles, 2))
        r2 = rng.random((n_particles, 2))
        new_positions: list[tuple[float, float]] = []
        for i in range(n_particles):
            pos = np.array(positions[i])
            vel = (
                inertia * velocities[i]
                + cognitive * r1[i] * (np.array([pbest[i].power_kw, pbest[i].energy_kwh]) - pos)
                + social * r2[i] * (np.array([gbest.power_kw, gbest.energy_kwh]) - pos)
            )
            velocities[i] = np.clip(vel, -vmax, vmax)
            new_positions.append(_clip(*(pos + velocities[i])))
        positions = new_positions
        if not _score_batch(positions):
            stale += 1
        else:
            for i, pt in enumerate(positions):
                candidate = evaluated.get(pt)
                if candidate is not None and _incumbent_key(candidate) > _incumbent_key(pbest[i]):
                    pbest[i] = candidate
            challenger = max(evaluated.values(), key=_incumbent_key)
            g_class = (gbest.feasible, _METRIC_RANK.get(gbest.objective_metric, -1))
            c_class = (challenger.feasible, _METRIC_RANK.get(challenger.objective_metric, -1))
            improved = c_class > g_class or (
                c_class == g_class and challenger.objective_value >= gbest.objective_value + epsilon
            )
            gbest = challenger
            stale = 0 if improved else stale + 1
        rounds.append(
            RefinementRound(
                round_index=generation,
                evaluations_total=len(evaluated),
                incumbent_power_kw=gbest.power_kw,
                incumbent_energy_kwh=gbest.energy_kwh,
                incumbent_value=gbest.objective_value,
                incumbent_feasible=gbest.feasible,
            )
        )

    scored = list(evaluated.values())
    feasible = [p for p in scored if p.feasible]
    if not feasible:
        warnings.append(
            "no covenant-feasible point found by the PSO baseline (DSCR is unavailable without "
            "injected debt terms, or every point fails the covenant) — `best` is indicative "
            "only, never bankable."
        )
    return SizingSearchResult(
        method="pso",
        evaluated=scored,
        best=max(scored, key=_rank_key),
        best_feasible=max(feasible, key=_rank_key) if feasible else None,
        n_evaluations=len(scored),
        warnings=warnings,
        rounds=rounds,
    )
