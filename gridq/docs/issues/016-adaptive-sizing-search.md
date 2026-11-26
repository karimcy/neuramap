# 016 — Adaptive sizing search (wider scope, full-year accuracy)

**Owner:** Oli (engine seam shared with Olly) · **Branch:** `agent/sizing-search` · **Status:** in progress

## Problem

The sizer enumerates a fixed **3×3** grid (`POWER_FRACTIONS = {0.25, 0.5, 1.0}` × `{1, 2, 4}h`)
— 9 candidates. That is too narrow a scope to trust the recommended size, and it cannot show a
real surface. We want to **widen the sizing search** while keeping the accuracy that the
**full-8760-hour exact LP dispatch** provides (the thing SAMA gives up by using a rule-based EMS).

## Design (agreed with Karim, 2026-06)

- **Decision space:** continuous **2-D — power (kW) × usable energy (kWh)** — so the C-rate floats
  (duration = energy/power, banded to a sane range) instead of being locked to `{1,2,4}h`.
- **Inner evaluator: unchanged.** Every candidate is still scored by the exact `MilpDispatchSolver`
  over the full metered year + the real finance model. No heuristic dispatch, ever. The only thing
  that changes is the **outer search** over sizes.
- **Method = dense exact-grid reference + adaptive refiner, benchmarked.** Because the space is 2-D,
  a dense exact grid (~thousands of LP solves, parallelised) is affordable and *is* the surface.
  An adaptive surrogate/local search pins the optimum to sub-grid precision and keeps production
  fast. The adaptive optimum is **benchmarked against the dense grid** to *measure* information loss
  (target ≈ 0); that bake-off is how we certify the fast method.
- **Determinism preserved:** all sampling is seeded (`np.random.default_rng(seed)`); identical
  (inputs, assumptions, seed) → identical result. No stochastic AI on the pricing path.
- **Surface:** persist the dense evaluated grid into the existing `SizingGrid` /
  `QuoteRecord.sizing_grid` so the current 3-D surface UI renders the wider, finer surface.

## Stages

1. **Continuous domain** (`gridq/sizing/domain.py`) — `SizingDomain` (power/energy box + derived
   duration band) with a deterministic dense `grid()` and seeded `latin_hypercube()`. *(done;
   band-clamp defect fixed 2026-07-01 — LHS samples power only within the band-feasible window,
   and a box disjoint from the band is rejected at construction)*
2. **Site-scaled bounds** — `SizingDomain.from_site(site)` from the binding scale (observed peak,
   else connection MIC — same basis as `MilpEngine._default_search_space`; labelled structural
   fraction band 0.05–1.0); replaces the `POWER_FRACTIONS` heuristic. *(done 2026-07-01)*
3. **Evaluator wrapper** — `gridq.sizing.evaluator.ContinuousSizingEvaluator`: `f(power, energy) →
   ScoredPoint` at arbitrary continuous points through the identical `_evaluate_spec`
   dispatch+finance path, with the **objective metric recorded** per point (levered / project-IRR
   / priced-floor fallback — the B001 honesty requirement). *(done 2026-07-01)*
4. **Parallel harness + benchmark** — `gridq.sizing.parallel`: `evaluate_points_parallel`
   (one evaluator per worker thread — the stateful solver is never shared; deterministic ordered
   reduction, provably identical to the sequential path) + `benchmark_parallel_evaluation`.
   *(done 2026-07-01 — measured on the demo site's full metered year, real `MilpDispatchSolver`,
   8 LHS points: sequential 2.00 s/solve (~1,800/h); 4 worker threads 0.80 s/solve effective
   (~4,500/h, 2.50× — HiGHS releases the GIL); parallel results identical to sequential; scored
   points honestly labelled `priced_floor_value_gbp` (no DebtTerms on the real path, B001).
   **Verdict: the wider scope is affordable — a ~1,600-point dense exact grid ≈ 21 min at 4
   workers on a dev laptop.** Numbers are machine-dependent dev artifacts, never quote data.)*
5. **Search engine** — dense-grid reference + adaptive refinement (surrogate/local polish); seeded
   PSO/DE selectable as a comparison baseline.
6. **Convergence + budget** — stop on Δ<ε over k iters or a max-eval cap; log the convergence curve.
7. **Surface persistence** — widen `SizingGrid` population to the dense grid; UI unchanged.
   *(done 2026-07-01 — `search_result_to_sizing_grid` projects any `SizingSearchResult` onto the
   frozen `SizingGrid` contract (D23 additive seam, zero schema change): every scored point in
   deterministic order, `selected` = covenant winner only (honest `None` otherwise), `bankable`
   keeps the full SizingRun conjunction. Engine wiring deliberately deferred to the stage-9
   production-method decision — and gated on C025.)*
8. **Validation** — determinism golden tests; **accuracy tests** (adaptive optimum == dense-grid
   optimum within tolerance) across several synthetic + real load shapes; perf benchmark; the
   "does wider scope change the recommended size vs the old 3×3?" comparison.
   *(substantially done 2026-07-01 — stub-surface certification tests in `test_sizing_search.py`
   (peaked surface: adaptive matches-or-beats the 9×9 dense reference and lands nearer the true
   peak) + the real-site run (demo site, real `MilpDispatchSolver`, 4 workers): legacy 3×3 → 124
   kW × 4 h, project-IRR 21.21% (9.5 s); dense 12×12 (67 in-band) → 162.7 kW × 7.79 h, 23.48%
   (52.7 s); adaptive (117 evals, 9 rounds) → 165.2 kW × 7.99 h, **23.50%** (+0.08% vs dense —
   sub-grid precision on real data; 103.6 s). **The wider scope changes the recommendation:
   +10.8% objective vs the 3×3.** ⚠️ Finding **C025**: the optimum pins to the 8 h band edge
   because `battery_capex.installed_cost` is per-MW only — energy is capex-free on the
   placeholder shape, so long durations are artificially attractive; an energy (£/kWh) capex
   component must land in the assumption library before wide-duration recommendations are
   meaningful, and the engine default must NOT swap to the wider search before then.)*
9. **Bake-off** — dense-grid vs adaptive vs PSO on the test sites; pick the production method on
   evals-to-optimum + final quality. Record the decision in `docs/decisions.md`.
   *(done 2026-07-02 — **D29**: demo site, real MILP: dense 12×12 ref (67 evals, 23.48%);
   adaptive +0.084% vs ref, within 0.5% at 41 evals; PSO +0.088%, within 0.5% at 24 evals —
   both ≈zero information loss. Production shape: dense grid = persisted reference surface,
   adaptive zoom = optimum refiner, PSO = baseline only. **Engine-default swap gated on C025**
   (the per-MW-only capex pins wide searches to the band edge) + a post-C025 re-run on a
   non-degenerate surface. PSO's eval-efficiency win discounted as measured on the degenerate
   corner-pinned surface.)*

## Scope additions from the north-star deep review (2026-07-01)

Recorded here so this issue stays the single build spec for Wave 6 of [docs/north_star.md](../north_star.md); evidence in [docs/reviews/2026-07-01-deep-review.md](../reviews/2026-07-01-deep-review.md).

- **Duration-key policy — DECIDED (D28, 2026-07-01).** Conservative flooring (D22) is **retained** on the continuous domain: no solved-result change, no golden re-baseline; flooring under-states value at off-key durations (honest direction). The surface is therefore **piecewise-constant with steps at the {1,2,4}h key boundaries** — stages 5–6's refiner must be step-aware (never assume smoothness across a key boundary), and the dense exact grid (same evaluator, same steps) stays the certification reference. Interpolation is deferred to a future sourced assumption release (see D28).
- **Covenant-feasibility-aware refinement.** The P90-DSCR covenant gate carves the feasible region; the refiner must treat feasibility as a classifier alongside the objective surrogate, and the stage-9 bake-off must include injected-DebtTerms cases. (The *deliverability* carve — `end_of_life_deliverable` — cannot exist until the Wave-7 closed-form check lands; re-run this bake-off as a Wave-7 acceptance item.)
- **Objective-metric honesty.** With no DebtTerms on the real path (B001), production ranking falls back to priced-floor £, not the advertised levered return — the evaluator wrapper must record which metric it optimised on every run, and the bake-off runs both with and without injected DebtTerms.
- **LHS band-clamp fix (stage 1 defect).** `SizingDomain.latin_hypercube`'s `lo > hi` clamp branch can emit samples whose derived duration falls outside the band that `grid()` strictly filters (`contains()` would reject them) — reconcile the two samplers' band semantics (filter, re-sample, or document the exception) and assert band membership in the accuracy tests.

## Invariants (must hold)

- Inner dispatch stays the exact full-year LP — never a heuristic.
- Frozen `OptimisationResult` untouched (D13); the grid rides on `QuoteRecord` additively.
- `not_bankable` discipline unchanged — placeholders still force `not_bankable`.
- Determinism: seeded everywhere; golden fingerprint tests.
