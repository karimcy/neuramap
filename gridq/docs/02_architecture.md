# 02 · Architecture — GridQ Product Engineer v1

**Status:** Working draft · **Date:** 2026-06-16 · **Owners:** Olly
**Reads with:** [docs/01_project_spec.md](01_project_spec.md) (what v1 builds), [docs/00_project_boundary.md](00_project_boundary.md) (scope/ownership), [CLAUDE.md](../CLAUDE.md) (operating rules), [docs/issues/](issues/) (the build issues).

This document describes the architecture for a **local-first MVP that can later become a production web app**. It defines the stack, the folder structure, the data contracts, the ownership boundaries, and how determinism and traceability are guaranteed. It writes no application code and invents no domain assumptions — field shapes for the software contracts are engineering design (labelled where new); domain numbers live only in the versioned assumption library.

---

## 1. Architecture principle

**Deterministic pricing core, agentic perimeter.** The architecture has two zones with a hard boundary between them:

- **Deterministic core** — the pricing path: `SiteModel → Optimisation → Financial model → QuoteRecord`. Pure, versioned, reproducible numerical code. **No LLM and no stochastic AI ever touches a number a lender relies on.** Identical inputs + assumption version + code version + model version + seed → identical outputs.
- **Agentic perimeter** — where the human hours are: ingestion of messy installer data, counterparty/credit research, and drafting the memo/proposal prose. LLM-assisted, **schema-validated, human-confirmed**, and structurally unable to write or alter a figure (numbers are injected into prose from the run record, never authored).

Everything below serves this split: the core is a deterministic Python package; the perimeter calls into it through validated schemas and never the reverse. The core is **market-agnostic**; GB-specific values live as *content* in the assumption library, never as branches in the code (`if market == "GB"` is a design failure).

---

## 2. Proposed initial stack

The defaults below are endorsed — they fit a deterministic numerical core, a Claude-Code-friendly workflow, and a clean path to production. A few small refinements are added with justification.

| Layer | Choice | Notes / justification |
|-------|--------|-----------------------|
| Numerical / domain core | **Python package** | Matches the committed solver path (linopy/HiGHS) and the whole pricing core. Single language for core + perimeter glue. |
| Schemas / contracts | **Pydantic v2** | Typed models with validation. *Refinement:* generate the shared JSON Schema files (referenced in the issues) **from** the Pydantic models, so there is one source of truth, not two. |
| Assumptions store | **YAML/JSON files, versioned** | Human-diffable, reviewable on cadence, easy to tag a release. Validated by a Pydantic `AssumptionSet` on load. No database needed for this. |
| Service layer | **FastAPI — only when needed** | The MVP needs none: a CLI / runner drives the pipeline locally. Add FastAPI when a UI or multi-user access is required (it wraps the *same* package). |
| Frontend | **Next.js / React — only after core contracts exist** | Built once `QuoteRecord` and the outputs are stable, for the 2026-quality proposal/visual layer. Not part of the numerical MVP. |
| Persistence | **Files first; SQLite only if needed** | Quote records and assumptions as files initially. Introduce SQLite when quote-lifecycle querying/concurrency demands it; Postgres later in production. |
| Tests | **pytest** | Plus golden/repro tests for determinism and contract-conformance tests for the mock↔real swap. |
| Dev tooling *(refinement)* | **ruff + mypy + a task runner** | Lint (`ruff`), typecheck (`mypy`), and a `Makefile`/`just` to wire the CLAUDE.md command table (`test`/`lint`/`typecheck`/`run`). Filling that table is a Milestone 0 exit (issue 001). |
| Determinism utilities *(refinement)* | **Seeded RNG + canonical JSON** | A small core utility for seeded randomness and sorted-key canonical serialization (used for hashing). Keeps reproducibility a property of the code, not a hope. |

**Net:** start as a Python package with a CLI and file-based state; add a service layer, a database, and a React frontend only as production needs appear. The numerical core never changes shape across that path.

---

## 3. Folder structure

A `src/`-layout package keeps the importable core isolated from tests, examples and docs. Adopt this as the canonical layout (it supersedes the *illustrative* paths in the issue files, which map onto it).

```
gridq/                              # repo root
├── pyproject.toml                  # package, deps, tool config (ruff/mypy/pytest)
├── README.md
├── Makefile                        # wires test / lint / typecheck / run
├── CLAUDE.md  CHANGELOG.md
├── src/
│   └── gridq/
│       ├── __init__.py
│       ├── core/                   # cross-cutting: hashing, versioning, seeding, determinism utils
│       ├── schemas/                # ALL Pydantic contracts (§4) + generated JSON Schemas
│       ├── assumptions/            # X.1 — loader + versioned YAML/JSON assumption sets
│       │   └── <version>/          #   e.g. 2026-06/*.yaml (capex, curves, rate/tenor map, gates, haircut)
│       ├── ingestion/              # M0 — accept/validate/grade a structured SiteModel
│       ├── optimisation_contracts/ # OptimisationRequest/Result schemas + engine interface + DETERMINISTIC MOCK (Oli)
│       ├── scenarios/              # M6 — discrete Low/Central/High price/policy cases
│       ├── finance/                # M5 (+ M9 credit→rate/tenor mapping) — builds the FinancialCase
│       ├── quotes/                 # X.2 — provenance, run record, QuoteRecord assembly, lifecycle
│       ├── outputs/                # M8 — memo + proposal generation from the run record
│       └── telemetry/              # X.3 — TelemetryEvent capture/store
│       # (production-only, added later: api/  for FastAPI;  see §8)
├── tests/                          # pytest: unit, golden/repro (determinism), contract-conformance
│   └── fixtures/                   # SiteModel fixtures T0/T1/T2 + invalid, reference QuoteRecord
├── examples/                       # demo_site/ SiteModel + run script (issue 015)
└── docs/                           # this folder
```

**The platform/engine seam is a folder seam.** Oli owns `optimisation_contracts/` (the `OptimisationRequest`/`OptimisationResult` schemas, the engine *interface*, and the mock). the real engine lands in a **separate package** — `src/gridq/optimisation/` — that *implements the same interface*. M5/M8 never import the engine; they consume `OptimisationResult`.

> *Reconciliation note:* the issue files suggested `gridq/...` paths (e.g. `gridq/sitemodel/`, `gridq/optimisation/`); those were illustrative. This `src/gridq/` layout is canonical — `schemas/` holds the SiteModel contract, `optimisation_contracts/` holds the mock + interface, and the real engine is `src/gridq/optimisation/`. In particular, the **v1 mock-side M4 mini-enumeration** lives with the mock in `optimisation_contracts/` (Oli, to unblock the pipeline), while Olly's **real M4 search** is `src/gridq/optimisation/sizing.py` — so the two `sizing.py` paths the issue files (005 and 014) suggest resolve to *different* packages and do not collide.

---

## 4. Data contracts

Seven contracts, all defined in `src/gridq/schemas/` as Pydantic models (and exported to JSON Schema). `SiteModel` and `OptimisationResult` are grounded in the spec (M1; §6 sketch); the others are software contracts whose field shapes are engineering design (not domain assumptions). The two **frozen shared coupling contracts** (per the spec and boundary doc) are `SiteModel` (input) and `OptimisationResult` (output). `OptimisationRequest` is the design-level **input envelope** the engine consumes — agreed alongside them, but not a third coupling contract.

### `SiteModel` — *input contract (M1); shared* 
The canonical site, reasoned from its own data (**no archetype field**).
- `schema_version`
- `half_hourly_load` — HH consumption series
- `connection` — MIC/MEC, ASC band, voltage, ANM
- `tariff_stack` — full import/export tariff incl. levy lines, DUoS bands
- `consents` — G98/G99/G100, export MPAN, DNO application state
- `supply` — supply boundary & supplier-consent state
- `physical` — pass/fail gate inputs (footprint, fire separation)
- `counterparty` — identity for the credit mapping

### `OptimisationRequest` — *engine input (design); shared*
What Oli hands the engine (mock or real) for one run.
- `site` — `SiteModel` (or its hash + reference)
- `sizing_space` — P·E·duration grid/bounds to search (v1: a small grid)
- `scenario_set` — reference to the scenario cases (M6)
- `objective` — bankable objective + the **P90-DSCR covenant** to embed
- `assumption_version`, `seed` — pin the run for determinism

### `OptimisationResult` — *engine output (§6 sketch); shared*
The seam between the optimisation engine and Oli's finance/outputs.
- `chosen_spec` — `power_kw`, `energy_kwh`, `duration_h`, `coupling` (AC)
- `dispatch` — HH array, units kW, sign convention **+ = discharge, − = charge**; charge counted vs MIC/ASC
- `value_by_stream` — £/yr by stream id (matching the market interface), each tagged `priced | contracted | gated/conditional` (+ unlock condition); plus `total_value` the parts sum to
- `energy_accounting` — round-trip losses, parasitic load, throughput (cycles/yr)
- `meta` — `seed`, `assumption_version`, `code_version`, `model_version` (`mock` | real engine id)

> **The stream-id vocabulary and tag taxonomy are part of the frozen contract — not just the field shape.** A versioned **stream registry** (shared, in `schemas/`) lists every valid `value_by_stream` id and its default tag class. Adding or retagging a stream (e.g. wholesale/P415 moving from `gated` to `priced` as its route-to-market lands) bumps the contract version and trips conformance tests, because M5's contracted-vs-merchant split and M8's firm-vs-conditional table read those ids and tags directly. **M5/M8 reject an unknown stream id** rather than silently dropping it from the split. Without this, a new Olly stream would be a semantic contract change invisible to schema-conformance.

### `FinancialCase` — *finance output (M5; design)*
The underwriting result around an `OptimisationResult`.
- `cashflows` — per-scenario + aggregated **P50/P90** (point estimates are not deliverables)
- `dscr` — series + minimum; the chosen spec satisfies the P90-DSCR covenant
- `structure` — commercial plug-in (fixed £/kWh default), term, indexation, buyout
- `cost_lines` — levies on charge volume incl. losses, business rates, insurance, metering/IDNO, DNO
- `price_to_target` — the £/kWh (or fee) that hits target return
- `credit` — input score/band → `rate`, `tenor` (deterministic M9 mapping)
- `contracted_vs_merchant` — the split, with the firm-vs-conditional stream table

### `QuoteRecord` — *immutable run object (X.2 + quote lifecycle); the traceability anchor*
Binds one run together, immutably, per lifecycle state (see §7).
- identity: `quote_id`, `lifecycle_status` (`draft → indicative → underwritten → contracted → operating`)
- inputs: `input_hash`, `data_tier` (T0/T1/T2), `optimisation_request`
- versions: `assumption_version`, `code_version`, `model_version`, `seed`
- results: `optimisation_result`, `financial_case`, gate/consent/credit status
- provenance: `output_hash`, `timestamp`, `human_minutes`, `history` (prior snapshots)

### `AssumptionSet` — *versioned assumptions (X.1)*
One versioned source for every number; **no private constants anywhere else**.
- `version` — release tag stamped onto every quote
- values: installed-capex placeholder (~£500k/MW), price curves + scenario weights, credit rate/tenor map, gate thresholds, perfect-foresight haircut, cost-line rates
- GB-specific values are content here, isolated from the market-agnostic core

### `TelemetryEvent` — *the falsifiable cost-compression dataset (X.3)*
One record per run, from the first quote.
- `quote_ref`, `data_tier` (at intake), `human_minutes` (by person), `iterations`
- `outcome` — win/loss + reason; `assumption_version`, `code_version`, timestamps
- later: realised-vs-underwritten after COD; hit-rate-by-tier, error-by-stream

---

## 5. Ownership boundaries

The contracts make the two-person split a clean interface, not a standing meeting.

- **Oli builds everything around the contracts.** `schemas/`, `assumptions/`, `ingestion/`, `scenarios/`, `finance/` (incl. the M9 credit→rate/tenor mapping), `quotes/`, `outputs/`, `telemetry/`, and `optimisation_contracts/` (the interface **and the deterministic mock**). Because the mock honours `OptimisationResult`, the entire platform — finance, quote, outputs, telemetry, UI — is buildable and testable end-to-end **without Olly**.
- **Olly builds the optimiser behind the `OptimisationResult` interface.** The real value stack (M2), dispatch MILP (M3, linopy/HiGHS), sizing optimiser (M4) and calibration (M7) live in `src/gridq/optimisation/`, consuming `OptimisationRequest` + `SiteModel` and emitting `OptimisationResult`. Nothing downstream imports the engine.
- **No constant back-and-forth once contracts are stable.** The coupling is exactly the two frozen contracts — `SiteModel` (in) and `OptimisationResult` (out) — plus the `OptimisationRequest` input envelope and the stream registry that travel with them, and shared acceptance/conformance tests. Frozen at Milestone 1, each then works independently; the **mock ↔ real swap** is the single integration point, guarded by contract-conformance tests (issue 014). **What conformance asserts:** both mock and real satisfy the schema + stream registry, and M5/M8 need *no code change* and stay deterministic against the real engine's `OptimisationResult` — **not** that the numbers are identical (a real optimiser legitimately produces different dispatch and value than a placeholder mock). If a contract must change, it changes deliberately, versioned, in one place — not by ad-hoc negotiation.

---

## 6. Determinism

Repeated inputs produce repeated outputs because the pricing core is a **pure function of explicitly pinned inputs**:

`output = f(input, assumption_version, code_version, model_version, seed)`

Guarantees:
1. **No LLM / no stochastic AI on the pricing path** — the perimeter cannot influence a number.
2. **All randomness is seeded** — the `seed` is an explicit input and is recorded in `OptimisationResult.meta` and the `QuoteRecord`; there is no unseeded RNG.
3. **No wall-clock in the maths** — `timestamp` is recorded as metadata only; it never feeds a calculation (a clock in the core would break reproducibility).
4. **No private constants — including in the mock** — every number comes from the versioned `AssumptionSet`; the deterministic mock reads every figure it emits from the `AssumptionSet` too (no in-code numeric defaults). Changing a value bumps the version, so behaviour is fully attributable.
5. **Pinned code & model versions, kept orthogonal** — `code_version` is the repo git SHA / build id; `model_version` is the engine's *own* semantic identity (e.g. `mock`, or `highs-milp-v1.2` naming the solver + formulation), which changes only when the engine's mathematics or solver change — even if `code_version` also moved. Keeping the two independent makes "which axis changed the number" answerable. Same code+model+assumptions+seed reproduce exactly.
6. **Canonical serialization & reduction order** — inputs and outputs are serialized with sorted keys / fixed formatting before hashing, and float reductions over maps (e.g. summing `value_by_stream` to `total_value`) iterate in canonical key order before accumulating — so hashes and totals are stable regardless of dict insertion order.
7. **Pinned dependencies & deterministic solver** — solver and library versions are locked and the solver runs in deterministic / single-thread mode. Floating-point results are **bit-reproducible only on a fixed platform/architecture and thread configuration** — BLAS-backed numpy and HiGHS can differ across CPU arch, thread count and library build, so byte-identity is not promised across hosts.
8. **Reproduction environment is recorded** — the `QuoteRecord` carries a reproduction-environment tag (platform/arch + solver mode), so a cross-host hash mismatch is diagnosable rather than alarming.

**Verification:** a golden/repro test asserts that re-running an identical `(input_hash, assumption_version, code_version, model_version, seed)` **on the same platform** yields a byte-identical `output_hash`; across platforms the bar is numerical equality within a documented tolerance (byte-identity is reserved for same-platform re-runs). Determinism is a tested property, not an aspiration.

---

## 7. Traceability

Every quote is self-describing: the `QuoteRecord` records, for each lifecycle state, exactly what produced its numbers.

| Field | What it is | Source |
|-------|-----------|--------|
| **input hash** | Canonical hash of the `SiteModel` + `OptimisationRequest` that drove the run | `core` hashing util |
| **assumption version** | The `AssumptionSet` release tag used | X.1 |
| **code version** | Git SHA / build id of the code that ran | `core` versioning |
| **model version** | The engine's own semantic id (`mock` or e.g. `highs-milp-v1.2`), orthogonal to code version | `OptimisationResult.meta` |
| **seed** | The RNG seed pinning any stochastic step | `OptimisationRequest` → `meta` |
| **output hash** | Canonical hash of the produced `OptimisationResult` + `FinancialCase` (the *verifier* a re-run matched, not a determinant) | `core` hashing util |
| **timestamp** | When the run was assembled (metadata only — never used in the maths) | recorded at assembly |
| **data tier** | T0 / T1 / T2 assigned at intake | M0 ingestion |
| **human minutes** | Effort spent on the quote (by person) | X.3 telemetry |
| **quote status** | Lifecycle state: `draft → indicative → underwritten → contracted → operating` | quote lifecycle |

Because `input_hash + assumption_version + code_version + model_version + seed` fully pin the computation (the `output_hash` is the *verifier* that a re-run matched, not itself a determinant), any number in a memo or proposal can be traced back to its inputs, assumptions and code — and regenerated identically within its recorded reproduction environment. **A quote is valid only against the assumption version that produced it**; a material assumption change beyond tolerance triggers a reprice rather than a silent edit. The same record feeds the telemetry dataset, making the cost-compression claim (time-to-quote vs the manual benchmark, hit-rate by tier) falsifiable.

---

## 8. Future production path

The local MVP becomes a production web app by **wrapping and extending** the same package — not rewriting it. The deterministic core and the contracts are stable across every stage.

- **Stage A — Local-first MVP (now).** Python package + CLI/runner; file-based assumptions and quote records; the deterministic mock engine; single operator (platform/engine). Proves the full pipeline end-to-end.
- **Stage B — Service layer.** Add `src/gridq/api/` (FastAPI) wrapping the *same* package functions; introduce **SQLite** for quote-lifecycle persistence and querying when files stop scaling. The core is untouched — the API just calls it.
- **Stage C — Frontend & multi-user.** A **Next.js/React** frontend for the 2026-quality proposal/visual layer and the installer-facing flow; authentication, roles, and the demo flow become a real UI. Persistence graduates SQLite → **Postgres**; quote artifacts to object storage.
- **Stage D — Real perimeter integrations & real engine.** Swap the mock for **Olly's real MILP** behind the unchanged `OptimisationResult` interface (conformance tests guard the swap). Integrate the real perimeter APIs — **Creditsafe** (M9), the **aggregator / RTM** for wholesale/BM, **tariff data** sources, and the **EMS/M&V** export — all at the agentic perimeter, schema-validated, **never on the pricing path**. Gated streams (wholesale/P415, ancillary) move from placeholder to priced as their route-to-market assumptions land.

**Invariant across all stages:** determinism and traceability hold unchanged — every quote, local or production, records its full provenance (§7) and reproduces from it within its recorded reproduction environment (§6: byte-identical on the same platform, tolerance-equal across platforms). Production adds reach (a web UI, real data, scale, multi-user), not a new way of pricing. The market-agnostic core means a second market is new *content* (a new `AssumptionSet` + GB-equivalent values), not a new system.
