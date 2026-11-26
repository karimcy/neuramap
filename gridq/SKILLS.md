# SKILLS.md — GridQ procedural memory

Active, **procedural** memory for working in this repo. It tells a future Claude session *how to do work here well*. Keep it compact and instructional. **This is not a changelog** — facts about *what happened* go in `docs/changelog.md`; this holds *how to act*. It self-evolves via [`skills/skill_update_protocol.md`](skills/skill_update_protocol.md).

## Project mission
Build the Product Engineer layer of GridQ — a deterministic behind-the-meter BESS **sizing & underwriting** engine that turns one site into a system spec, a bankable underwriting pack, and a customer proposal. v1 is a local tool on a mocked optimiser. Scope: [`CLAUDE.md`](CLAUDE.md), [`docs/00_project_boundary.md`](docs/00_project_boundary.md).

## Non-negotiable rules
- No LLM / no stochastic AI on the pricing path. Ever.
- Pricing is deterministic: identical `input + assumption_version + code_version + model_version + seed` → identical output.
- Every output number traces to input + assumption version + code version. No private constants.
- Market-agnostic core; GB specifics are *content* in the assumption library. No `market == "GB"` branch in the core.
- Don't build other workstreams' work (Growth/Finance/Legal) or Stage-2 compute. Don't put secrets in code.

## Build workflow
1. Plan first (`/plan-feature` or `build-planner`): smallest sensible slice, files, deps, risks, acceptance, test plan. Stop for approval if a decision is needed.
2. Build the slice (`feature-builder`): small, testable, matching existing patterns; tests alongside.
3. Run the quality bar (`/run-quality`): `pytest`, `ruff check .`, `mypy`, `ruff format --check .` (via `.venv`). Green before "done".
4. Update docs (`/update-project-docs` or `docs-maintainer`) and leave a handoff.
- Never do a large multi-module build without a written plan. Sequential over parallel.

## Collaboration workflow
Olly maintains this codebase. The platform/engine split ([`docs/module_ownership.md`](docs/module_ownership.md)) is a *structural hint, not a wall* — any module may be edited when it helps:
- **Every session starts with the session briefing.** A `SessionStart` hook prints the briefing (what's been done, when · what's next · open items) into context — **whatever the prompt**. Open your first reply by surfacing it and reconciling the request against `ACTIVE_PLAN.md` + `OPEN_COMMENTS.md` before acting; flag any clash. `GRIDQ_CONTRIBUTOR` is `Olly` (set per machine in `.claude/settings.local.json`); **attribute every change to Olly**. Before ending a session that touched `src/`/`docs/`, log it: `python -m gridq.dev.team_sync add-handoff --summary "…"`. Full guide: [`docs/shared_work/README.md`](docs/shared_work/README.md).
- Before starting, read [`docs/shared_work/ACTIVE_PLAN.md`](docs/shared_work/ACTIVE_PLAN.md) and `BLOCKERS.md`; skim `TEAM_SYNC.md`.
- Run `python -m gridq.dev.team_sync` (or `gridq-team-sync`) to see the current dashboard.
- When you touch the frozen contracts (`SiteModel`, `OptimisationResult`, stream registry), treat it as a deliberate, versioned change and record it in `OPEN_COMMENTS.md`.
- On finishing a session, append a `HANDOFF_LOG.md` entry (use `add_handoff_entry`) and update `ACTIVE_PLAN.md`.
- Preserve clean input/output contracts so the two sides stay decoupled; leave comments/handoffs over silent edits.

## Deterministic pricing rules
- Pricing path = `SiteModel → Optimisation → Financial model → QuoteRecord`. Keep it pure.
- Seed all randomness; record the seed. No wall-clock in computed values (`timestamp` is metadata only). Canonical (sorted-key) ordering before hashing and before float reductions.
- Pin solver/lib versions; byte-identical reproduction is guaranteed only on a fixed platform/arch (tolerance across platforms). Record `reproduction_env`.

## Assumption / provenance rules
- Every number the pipeline uses lives in the versioned assumption library (X.1). Changing a value bumps the version.
- In code, read assumptions only via `gridq.assumptions.load_assumptions(version)` (the single door) and stamp every run via `gridq.provenance.build_run_context(...)` — never hardcode numbers or hashes.
- Stamp each run/quote with `input_hash`, `assumption_version`, `code_version`, `model_version`, `seed`, `output_hash`, `reproduction_env`.
- A quote is valid only against the assumption version that produced it; a material change → reprice, not a silent edit.
- **Missing or non-`confirmed` ⇒ degrade, never invent.** When the library lacks a number or carries it as placeholder / `needs_review` (e.g. per-site debt terms, a gated wholesale/BM/ancillary stream), do **not** guess, hardcode or "reasonably assume" it: return the dependent metric **unavailable** / **not bankable** with a warning that *names the missing key(s)*, build and test the machinery so it activates unchanged when a real value lands, and raise a ranked data ask in `OPEN_COMMENTS.md`. Gate a not-yet-real stream on assumption **status** — open it only when the backing assumption is `confirmed` *and* its value positive (read via `load_assumptions(version)`), never a hand-written `True`; promoting it to the priced floor is then a **version bump**, not a code edit. Structural-only params that inject no £/% (an IRR search bound, a projection horizon) may carry a *labelled* default, but it must warn, never sit silent.
- **External forecasts are referenced, not stored as assumptions — and never invented.** Wholesale / ancillary / BM / Modo curves are third-party input data: pin each by `source` + `forecast_version` + content `input_hash` (hash the real file), keep it under gitignored `data/`, and verify the hash before relying on it. A missing forecast or price band is a warning or ranked data ask — never a fabricated spread.

## Testing and validation rules
- Tests land with the change. Determinism gets a golden/repro test. Contracts get conformance tests (mock and real engine both satisfy the schema; M5/M8 need no code change across the swap — *not* identical numbers).
- Validate `SiteModel`/`OptimisationResult` against the schema + stream registry. Don't weaken a check to make it green.
- New schema models inherit `GridQModel` (`extra='forbid'` + `schema_version`); **generate example/fixture JSON from the models**, not by hand, so they stay valid under the strict config.
- `pytest` + `ruff` + `mypy` all pass before "done".

## Documentation update rules
- Meaningful change → `docs/changelog.md` (reverse-chron) + relevant `docs/project_status.md` sections.
- Real, non-obvious decision → `docs/decisions.md` (date · decision · reason · alternatives · consequences).
- New domain term → `docs/glossary.md`. Keep `CLAUDE.md` lean; only permanent operating rules go there.

## Common failure modes (watch for these)
- LLM-authored or invented numbers in outputs → inject every number from the run record.
- Baseline/integration engines drifting toward real output → keep them `implementation_level=baseline` / `bankable_status=not_bankable`, price nothing (revenues 0) until a real assumption-driven calc exists, and never build a spoof "real engine".
- Private constants / unseeded randomness / wall-clock in the core → reproducibility silently breaks.
- Silent change to a frozen contract, or mock↔real shape drift → flag and version it; add conformance tests.
- Retrofitting telemetry instead of emitting from the first quote.
- Big-bang multi-module builds, or building without an approved plan.
- Scope creep into Growth/Finance/Legal/Stage-2.
- Editing the protected slow-update block below outside an explicit slow-update.

<!-- SLOW_UPDATE_START -->
## Foundational invariants (change only via an explicit slow-update)
These are the deepest, slowest-moving rules. The fast self-evolution protocol must **not** edit between these markers; they change only in a deliberate slow-update.
- The pricing path is deterministic and contains no LLM/stochastic AI.
- Every number is traceable to input + assumption version + code version; no private constants.
- The core is market-agnostic; GB content is isolated in the assumption library.
- `SiteModel` and `OptimisationResult` are the frozen platform↔engine coupling contracts; changes are deliberate and versioned.
- Telemetry and provenance ship from the first quote, never retrofitted.
<!-- SLOW_UPDATE_END -->
