---
proposed_by: agent/dispatch (Olly)
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
append (one edit)

## Target
"## Common failure modes (watch for these)" — append one bullet.

## Proposed text
- Adding a numerical solver (linopy/HiGHS) to the deterministic core has two non-obvious traps.
  **(a) Pin the solver's transitive *math* deps, not just the solver** — linopy's expression layer
  broke on a future `xarray` `Dataset` constructor, so the reproduction contract needs an explicit
  `xarray` bound (`<2025`) alongside `linopy`/`highspy`. **(b) Match the HiGHS method to the
  constraint structure** — a single dense constraint spanning every period (e.g. an *annual*
  throughput/energy cap over ~17.5k half-hours) makes dual simplex pathologically slow (~15 s/year);
  `solver="ipm"` + `run_crossover="on"` reaches the identical optimum in <1 s and the crossover gives
  a clean vertex schedule. Always set `threads=1` + a fixed `random_seed` and round outputs so the
  optimum is bit-reproducible (the determinism contract).

## Evidence
W4-1 (HANDOFF_LOG 2026-06-24, branch `agent/dispatch`, decision **D22**): built the real half-hourly
dispatch MILP (`gridq.optimisation`). Installing `linopy 0.5.8` pulled `xarray 2026.4.0`, which broke
linopy at import (`Dataset` constructor change) — fixed by pinning `xarray>=2024.2,<2025`. Profiling
a full-year solve showed dual simplex at ~15 s vs HiGHS IPM at ~0.8 s for the identical objective,
with the annual `warranty_cycle_limits` throughput cap as the dense coupler; `MilpDispatchSolver` now
uses IPM + crossover + `threads=1`/fixed seed, and `tests/test_optimisation_milp_solver.py` pins
determinism + the physics invariants. Full benchmark ≈2.5 s/year solve.

## Notes
Procedural ("how to act" when wiring a numerical solver into the deterministic core) — generalises
beyond this lane (any future solver/optimiser milestone). Belongs in SKILLS.md, not the changelog:
the *event* (W4-1 shipped) is logged in `docs/changelog.md`; this is the reusable rule. Bounded to a
single bullet under an existing heading; touches no protected (SLOW_UPDATE) block.
