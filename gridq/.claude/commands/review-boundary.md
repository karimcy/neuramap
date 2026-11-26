---
description: Decide whether a proposed task is Oli / Olly / Shared scope, or belongs to another workstream and should not be built here.
argument-hint: <proposed task>
---

Proposed task: **$ARGUMENTS**

Decide where this belongs. Read `docs/00_project_boundary.md` (scope & exclusions) and `docs/module_ownership.md` (the platform/engine/Shared table).

Return a clear verdict and short reasoning:

- **Verdict:** one of —
  - `Oli` — Product Engineer platform work (ingestion, schema, finance, scenarios, outputs, telemetry, assumptions, provenance, the deterministic mock, the M9 mapping).
  - `Olly` — optimisation/revenue engine (value stack, dispatch MILP, sizing optimiser, calibration) behind the `OptimisationResult` interface.
  - `Shared` — touches a frozen contract (`SiteModel` / `OptimisationResult`), the stream registry, acceptance/conformance tests, or assumption conventions. **Name the contract.**
  - `Out of scope — Zara (Growth) / Sim (Finance) / Karim (Ops·Partnerships·Legal) / future stage`.
- **Reasoning:** 2–4 sentences grounded in the boundary doc. Cite the module(s) if in scope.
- **If out of scope:** name the owning workstream, say why GridQ must not build it here, and what (if anything) GridQ legitimately *consumes* from it (e.g. a credit score, a tariff feed) versus *owns*.
- **Watch the known grey areas:** the aggregator technical-interface vs commercial-terms split (boundary Open Q1), and M9 credit-tool ownership (Open Q2). If the task lands in one, say so and flag the open question rather than asserting.

Do not start building. This command only classifies.
