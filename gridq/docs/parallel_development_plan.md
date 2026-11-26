# Parallel Development Plan — Phase 4+

**Created:** 2026-06-17 · **Status:** active (governs Phase 4+ parallel work)

Phase 3 is complete (schemas, assumption library + provenance, engine interface + baseline; 59 tests green). Before Phase 4+ we move from **sequential, one-session-at-a-time** work to **several Claude Code sessions building in parallel**. This document is the rulebook for that. It does **not** build product modules — it sets up the lanes, the merge discipline, and the data-integrity rules that let parallel work converge cleanly.

Read this alongside, and subordinate to:
- [`SKILLS.md`](../SKILLS.md) — how to work here (non-negotiable rules + foundational invariants).
- [`CLAUDE.md`](../CLAUDE.md) — operating guide, scope boundary, architecture principles.
- [`docs/shared_work/`](shared_work/) — the live coordination surface (TEAM_SYNC / ACTIVE_PLAN / HANDOFF_LOG / OPEN_COMMENTS / BLOCKERS).
- [`docs/module_ownership.md`](module_ownership.md) — module → owner → package map.

> **Nothing here overrides the foundational invariants** in `SKILLS.md` (`<!-- SLOW_UPDATE -->` block): deterministic, LLM-free pricing path; identical `input + assumption_version + code_version + model_version + seed` → identical output; no private constants; every number traceable to its input + assumption version + code version; market-agnostic core; `SiteModel`/`OptimisationResult` are the frozen couplings; telemetry/provenance ship from the first quote.

---

## 1. Active parallel branches / workstreams

Each workstream runs on **its own branch and git worktree**. Branch names are `agent/<workstream>`. The owner column is a *responsibility hint, not a wall* (per `SKILLS.md` collaboration workflow) — any session may edit any module when it helps, but must coordinate via `docs/shared_work/`.

| Branch | Module(s) | Owner hint | Package (`src/gridq/`) | Phase 4+ scope (what this lane builds) |
|--------|-----------|-----------|------------------------|-----------------------------------------|
| `agent/value-stack` | M2 — Value & constraint stack | Olly | `optimisation/` (real); mock in `optimisation_contracts/` | Generic market value-function + constraint interface (value, firmness & decay, availability gates, deliverability profile). **No `market == "GB"` branch in core.** |
| `agent/dispatch` | M3 — Dispatch engine (+ M4 sizing outer loop, M7 calibration hook) | Olly | `optimisation/` (real); implements `engine/` `OptimisationEngine` | Half-hourly dispatch MILP (linopy/HiGHS) behind the `OptimisationEngine` Protocol; sizing outer loop; P90-DSCR constraint inside the objective. Swaps in behind the frozen contract with no downstream rewrite. |
| `agent/finance` | M5 — Financial model & underwriting (+ M9 score→rate/tenor mapping) | Oli | `finance/` | `FinancialCase` (P50/P90, DSCR, price-to-target, contracted-vs-merchant) around any `OptimisationResult`; baseline result flows through to a `not_bankable` case. |
| `agent/scenarios` | M6 — Scenario engine | Oli | `scenarios/` | Discrete Low / Central / High cases over assumption + price inputs; deterministic, seeded. |
| `agent/ingestion` | M0 — Ingestion | Oli | `ingestion/` | Parse, validate and **grade** real site data into a `SiteModel` (data tier T0/T1/T2); accept the range of half-hourly load formats; emit validation warnings + ranked data asks (see §3). |
| `agent/quotes-telemetry` | X.3 — Quote telemetry (+ quote lifecycle) | Oli | `quotes/` | `QuoteRecord` lifecycle and `TelemetryEvent` emission **from the first quote** (data tier, human-minutes, iterations, outcome, realised-vs-underwritten). Never retrofitted. |
| `agent/outputs` | M8 — Outputs | Oli | `outputs/` | Underwriting memo + customer proposal + visual layer. **Numbers come from the run record; prose is LLM-drafted only.** |
| `agent/dev-quality` | Dev tooling, tests, CI, **skills governance** | Shared | `src/gridq/dev/`; repo-root `tests/` and `skills/` | Quality bar, `team_sync` helpers, conformance harness, CI; hosts the **skills-maintainer** role (§4). |

**Cross-cutting (not a parallel lane):**
- **X.1 (`assumptions/`) and X.2 (`provenance/`) are shared infrastructure** — both exist on disk and are owned by Oli. They are not a separate workstream because they are already fully implemented (gb_v0_1, RunContext). Lanes read them via `load_assumptions(version)` and `build_run_context(...)`; they **must not** be patched in-lane without flagging the change in `OPEN_COMMENTS.md` (a version bump is required for any assumption value change). Substantive X.1/X.2 work lands on whichever lane needs it, coordinated via OPEN_COMMENTS.
- **M1 / `schemas/` and the `OptimisationResult` stream registry are frozen shared contracts** ([`docs/module_ownership.md`](module_ownership.md) "Shared"). They are *not* a workstream — a change to either is a deliberate, versioned event raised in `OPEN_COMMENTS.md` (see §3, contract discipline). `agent/finance`, `agent/dispatch`, `agent/value-stack`, `agent/scenarios`, `agent/outputs`, `agent/ingestion` and `agent/quotes-telemetry` all build *against* these contracts.
- **M9 scoring-tool ownership is unresolved** (BLOCKERS B001 / boundary Open Q2). `agent/finance` owns the deterministic mapping only; do not build the scoring tool until ownership is confirmed.
- **Open consolidations** (`optimisation_contracts/` → `engine/`, C003; `docs/03_data_contracts.md` reconciliation, C002/C004) are tracked in `OPEN_COMMENTS.md` and should be resolved by the relevant lane, not duplicated across lanes.

---

## 2. Branch & worktree model

One isolated working tree per lane, so parallel sessions never fight over the same checkout:

```
# from the repo root, once per lane:
git worktree add ../gridq-worktrees/value-stack agent/value-stack
git worktree add ../gridq-worktrees/finance     agent/finance
# ...one per active lane
```

- Each lane branches from the current `main`; rebase on `main` before opening a merge.
- A lane keeps its scope to its package(s); touching another lane's package or a shared contract is allowed but must be flagged in `OPEN_COMMENTS.md` first.
- Worktrees share one `.git`, so `data/` (gitignored real inputs, see §3) is **not** shared between worktrees — each session points at its own local data copy. Never commit real data.

---

## 3. Merge & coordination rules

These are the rules every parallel lane follows. They are binding for Phase 4+; they extend (do not replace) the `SKILLS.md` build + collaboration workflow.

1. **Own branch / worktree.** Each agent works on its own `agent/<workstream>` branch in its own worktree. No direct commits to `main`; `main` only advances by reviewed merge.
2. **Update the handoff log.** On finishing a session, each agent appends a `HANDOFF_LOG.md` entry (via `gridq.dev.team_sync.add_handoff_entry(...)` or by hand in the same format) — summary, files changed, **tests run + result**, remaining tasks, warnings for the next person. Append-only; never rewrite past entries.
3. **Surface open questions.** Each agent records open questions, decisions-needed and cross-lane concerns in `OPEN_COMMENTS.md` (`add_open_comment(...)`), with the module/file and author. Resolve via `resolve_open_comment(...)`; don't silently close.
4. **`SKILLS.md` is single-writer.** **No agent directly edits `SKILLS.md`** except the designated **skills-maintainer** (§4). This keeps procedural memory coherent and prevents merge races on the most-read file in the repo. The protected slow-update block is off-limits to everyone except via an explicit slow-update.
5. **Propose skills edits, don't apply them.** Any agent may *propose* `SKILLS.md` changes by dropping a proposal file in [`skills/proposed_updates/`](../skills/proposed_updates/) (one file per proposal, with evidence). The skills-maintainer runs the [skill update protocol](../skills/skill_update_protocol.md) and applies/rejects them.
6. **Tests before handoff.** Every agent runs the **relevant** tests for its lane and the shared quality bar **before** handing off or opening a merge — `pytest`, `ruff check .`, `mypy`, `ruff format --check .` (via `.venv`). Green before "done". Record the result in the handoff entry. A change lands with its tests; determinism gets a golden/repro test; shared contracts get conformance tests (mock and real engine both satisfy the schema).
7. **No fake data treated as real.** **No generated / synthetic / illustrative data is ever treated as real input.** Synthetic fixtures live under `tests/fixtures/` (or are clearly named `*_synthetic` / `*_example`) and are for tests and demos only — they must never flow into a quote as if they were a real site's data. This is the parallel-work extension of the existing "no invented numbers in outputs" failure mode.
8. **Placeholders stay labelled.** All placeholder assumptions keep their `status: placeholder` / `needs_review` label (and `confidence: low`) until a real source confirms them — and confirming a value **bumps the assumption version** ([`docs/assumption_library.md`](assumption_library.md)), it is never a silent edit. No lane may flip a placeholder to "confirmed", remove its label, or present placeholder-driven output as bankable.

**Contract-change discipline (shared `SiteModel` / `OptimisationResult` / stream registry):** because many lanes build against these, a change is a coordinated event: raise it in `OPEN_COMMENTS.md`, version it, add/extend conformance tests, and let dependent lanes rebase. Prefer additive, backward-compatible changes (as `SolverStatus.INSUFFICIENT_DATA` was). The M5/M8 lanes must keep working across a mock↔real engine swap with no code change (not identical numbers).

**Merge order suggestion** (lowest coupling first, to minimise rebase churn): `agent/ingestion` and `agent/dev-quality` → `agent/value-stack` → `agent/dispatch` → `agent/finance` / `agent/scenarios` → `agent/quotes-telemetry` → `agent/outputs`. Adjust as reality dictates; the rule is *coordinate*, not a fixed gate.

---

## 4. Skills governance under parallelism

`SKILLS.md` is the most-read, highest-leverage file in the repo. Under parallel work it becomes a merge hotspot and a coherence risk, so:

- **Designated skills-maintainer.** One role (carried by the `agent/dev-quality` lane) is the *only* writer of `SKILLS.md`. The role runs the [skill update protocol](../skills/skill_update_protocol.md): collect evidence → split successes/failures → propose ≤3 bounded edits → validate → accept/reject → log in [`skills/edit_log.md`](../skills/edit_log.md) / [`skills/rejected_edits.md`](../skills/rejected_edits.md).
- **Proposal inbox.** Every other lane proposes edits by adding a file to [`skills/proposed_updates/`](../skills/proposed_updates/) — see that directory's `README.md` for the one-file-per-proposal format (target section/anchor, operation, proposed text, **evidence** from real work). Proposals are queued, not applied.
- **Protected block.** The `<!-- SLOW_UPDATE_START/END -->` invariants change only via an explicit slow-update — never through the proposal inbox or the fast loop.

This gives every parallel lane a voice into procedural memory without letting eight branches edit one file at once.

---

## 5. Shared input-data rule (data integrity — the most important rule)

GridQ underwrites **one real site from its own real data**. Parallel development must not erode that. This rule is binding for every lane, especially `agent/ingestion`, `agent/value-stack`, `agent/dispatch`, `agent/finance` and `agent/scenarios`.

**The repo must support real inputs:**
- **Real half-hourly (HH) commercial load data** — the site's actual metered consumption (and export where present), at the range of installer/meter HH formats M0 ingestion must accept.
- **Real wholesale / Modo forecast files** — real wholesale price curves and Modo-style market/revenue forecasts, loaded as files (gated upside until the RTM partner/API is known — see BLOCKERS B002; never in the priced floor).
- **Data layout convention:** real inputs live under `data/` (already gitignored — files-first storage, [`docs/decisions.md`](decisions.md) D4). Real client data is **never committed** (it is PII/commercially sensitive and falls under "no secrets in code"). Each worktree points at its own local `data/`.

> **Amended by D45 (Olly, 2026-08-12) — [`docs/decisions.md`](decisions.md).** The prohibition below is **sharpened, not repealed: never invent *silently*, always derive and declare.** No missing input may block a validation run. Where one of the values below is absent and the run needs it, the Fable advisor derives it from the site's own evidence, its business type and the literature; it is recorded as a labelled `DerivedAssumption` (kind, value, basis, sources, derivation method, confidence ceiling, confirmation ask); the confidence band is capped; the run **completes**; and the report discloses it in a Modelling assumptions section plus a generated assumption register ([`validation_requirements.md` §8](validation_requirements.md)). Derivation happens **once, at the perimeter, offline** — the deterministic pipeline only ever reads the recorded value, so no LLM sits on the pricing path.
>
> Three things remain absolutely forbidden: an **unrecorded** guess; a **fabricated citation**; and a **`pending_permission` written as granted**. Consents (G99/G100, export MPAN, per-stream consent) are permissions, not facts — they can never be derived. Model the stream, show its value, label it "requires customer consent".
>
> **D45's same-day amendment — a null input is never acceptable, and a null is worse than a blocked run.** A blocked run produces no number; a null produces a **confidently wrong one**. A missing `duos_red_rate` does not read "unknown": it prices `DUOS_RED_BAND_AVOIDANCE` at **£0**, one of only two priced-floor streams, and that zero enters P50, P90 and the DSCR indistinguishable from a measured result. So on the pricing path every null, `None`, empty rate, absent tariff line and unpopulated library key is a **defect for the lane to fix, not a state to report** — a site that underwrites badly *because an input was null* is a finding about the **pipeline**, not about the site, and must not be published as one. Deriving a *published* GB value (a DNO CDCM tariff table, a NESO de-rating factor, an ENA settlement formula, an OBR index) is **sourcing, not invention**; where derivation genuinely fails the key carries `status: needs_review` with the search recorded and the affected £ is **refused, never silently read as zero**.

**Claude must NEVER invent missing data *silently*.** Never fabricate, guess, interpolate-as-fact, or "reasonably assume" **without recording it** any of:
- missing **half-hourly load** data (no synthetic load profiles passed off as the site's),
- **price / wholesale / Modo forecast** values,
- **tariffs** (import/export, levy lines, DUoS bands),
- **connection values** (MIC / MEC / ASC, import/export headroom),
- **consents** (G99/G100, export consent, planning),
- **market revenues** (CM, Piclo/local flex, BM/ancillary, wholesale arbitrage).

When any of these is missing, the permitted responses are (the third added by D45):

1. **Validation warning** — ingestion / validation records a typed warning on the `SiteModel` (e.g. data tier downgrade T2→T1→T0, "underwritten from T0 needs a warning"), the output is marked accordingly, and nothing is priced on the absent input — meaning the affected £ is **refused**, never read as zero. Versioned **placeholder** assumptions (`status: placeholder/needs_review`, `confidence: low`) from the assumption library may stand in for the *generic market-shape* values they represent (e.g. a tariff structure) only when the library already provides one — they **must not** be used to fill in a site-specific value that is missing (e.g. using a placeholder tariff rate to substitute for the actual site's measured import cost). The output remains **`not_bankable`** whenever any placeholder stands in. No interpolation of missing site data — not even soft or labelled interpolation.
2. **Ranked data ask** — a prioritised list of the missing inputs the user must supply, ranked by their impact on bankability / quote fidelity (e.g. "1. 12 months HH load — blocks T2; 2. confirmed MIC/MEC — bounds dispatch; 3. export consent — gates export revenue"). The data ask is an output artifact, not a blocker to running the pipeline; the pipeline runs degraded and says so.
3. **A recorded `DerivedAssumption` (D45)** — where the run needs the value to proceed at all. This is what makes the run complete instead of stopping, and it is *additive* to responses 1 and 2, never a replacement: the warning and the ranked data ask both still fire. The value is derived from the site's own evidence and the literature, recorded with full provenance, and disclosed in the report. Structural feasibility inputs (connection capacity, export limit) cap the band at **low**; shape-like inputs (load archetype, within-day shape) cap at **medium**. The output stays **`not_bankable`**, exactly as with a placeholder — `not_bankable` and the band *are* the statement that confirmation is required.

A missing input becomes **a warning, a data ask, or a declared derived assumption — never an invented number and never a silent zero.** This is the hard line that keeps a quote honest under parallel development.

---

## 6. Definition of done (per lane, before merge)

A lane is ready to hand off / merge only when **all** of:

- [ ] Work is on its `agent/<workstream>` branch/worktree, rebased on current `main`.
- [ ] `pytest`, `ruff check .`, `mypy`, `ruff format --check .` all green (via `.venv`); result recorded in the handoff entry.
- [ ] New behaviour lands with tests; determinism has a golden/repro test; shared-contract changes have conformance tests (mock + real both satisfy the schema).
- [ ] No invented data; placeholders still labelled `placeholder`/`needs_review`; nothing baseline/placeholder-driven presented as bankable.
- [ ] No private constants / unseeded randomness / wall-clock in computed values; numbers read via `load_assumptions(version)`; runs stamped via `build_run_context(...)`.
- [ ] `HANDOFF_LOG.md` appended; open questions in `OPEN_COMMENTS.md`; `ACTIVE_PLAN.md` updated.
- [ ] Any `SKILLS.md` learning filed as a proposal in `skills/proposed_updates/` (not edited directly).
- [ ] Shared-contract changes flagged + versioned in `OPEN_COMMENTS.md`; `docs/changelog.md` + `docs/project_status.md` updated for meaningful changes.

---

## 7. What this step does *not* do

This document sets up coordination only. It does **not** build M0/M2/M3/M4/M5/M6/M8 product logic, does not implement the real MILP, and does not change the frozen contracts. Product modules are built per lane, plan-first, under the milestones in [`docs/01_project_spec.md`](01_project_spec.md#8-build-milestones).
