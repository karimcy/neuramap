---
description: Research a feature and propose an implementation plan — then stop and wait for approval (no code).
argument-hint: <feature or task to plan>
---

You are planning, **not** building. Do not write or edit any application code in this turn.

Feature / task to plan: **$ARGUMENTS**

## 1. Read the relevant docs
Read what bears on this task (skip what doesn't):
- `CLAUDE.md` — working style, architecture principles, quality bar.
- `docs/00_project_boundary.md` and `docs/module_ownership.md` — confirm this is in scope (Oli / Olly / Shared). If it smells out-of-scope, run `/review-boundary $ARGUMENTS` first.
- `docs/01_project_spec.md` (module map, milestones), `docs/02_architecture.md`, `docs/03_data_contracts.md` — the contracts and module this touches.
- Any matching file in `docs/issues/`.

## 2. Inspect the repo
Look at the actual code/tests that this change touches under `src/gridq/` and `tests/`. Note current state (stub vs implemented), the relevant contracts, and existing patterns to match.

## 3. Produce the plan
Output, concisely:
- **Summary** — what we're building and why, in 2–3 sentences.
- **Approach** — the design, honouring the non-negotiables: deterministic core (no LLM/stochastic AI on the pricing path), market-agnostic core (no `market == "GB"` branch), every number traceable, telemetry from the first quote.
- **Files to create or modify** — each with a one-line reason. Distinguish new vs edited.
- **Data-contract impact** — does this touch `SiteModel` / `OptimisationResult` / any frozen contract? If so, flag it as a versioned contract change.
- **Risks** — technical, scope, and contract risks; and how each is mitigated.
- **Test plan** — what proves it works (unit / golden / conformance), and which fixtures are needed.
- **Open questions** — anything that needs a decision before or during the build.

## 4. Stop
Keep it small and sequential (prefer the smallest testable slice). **Then STOP and wait for explicit approval before writing any code.** Do not proceed to implementation in the same turn.
