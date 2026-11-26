# Module Ownership

Who owns what. Module IDs follow the System Brief map; owners follow [00_project_boundary.md §4](00_project_boundary.md) and [CLAUDE.md §5](../CLAUDE.md). **Olly owns the whole GridQ engine.** The `OptimisationResult` seam still cleanly separates the underwriting **platform** from the optimisation **engine** — the deterministic mock unblocks the platform, and the real engine drops in behind the frozen contract — but both sides are one codebase, maintained by Olly.

| Module | Name | Owner | Package (`src/gridq/`) | Notes |
|--------|------|-------|------------------------|-------|
| M0 | Ingestion | **Olly** | `ingestion/` | Validate & grade a `SiteModel` (T0/T1/T2). |
| M1 | SiteModel / schema & validation | **Olly** (frozen contract) | `schemas/` | The input contract; changed deliberately and versioned. |
| M2 | Value & constraint stack | **Olly** | `optimisation/` (real) | Mock placeholder in `optimisation_contracts/`. |
| M3 | Dispatch engine | **Olly** | `optimisation/` (real) | Mock in `optimisation_contracts/`; real MILP later. |
| M4 | Sizing optimiser | **Olly** | `optimisation/` (real) | v1 mock mini-enumeration lives with the mock. |
| M5 | Financial model & underwriting outputs | **Olly** | `finance/` | P50/P90, DSCR, price-to-target; builds `FinancialCase`. |
| M6 | Scenario engine | **Olly** | `scenarios/` | Discrete Low/Central/High cases. |
| M7 | Calibration | **Olly** | `optimisation/` (real) | Backtest hook + labelled placeholder haircut in v1. |
| M8 | Outputs | **Olly** | `outputs/` | Memo + proposal; numbers from the record, prose LLM-drafted. |
| M9 | Counterparty credit | **Olly (mapping); tool open** | `finance/` | Olly owns the deterministic score→rate/tenor mapping; ownership of the scoring tool/research agent is **unresolved** (boundary Open Q2). |
| X.1 | Assumption library | **Olly** | `assumptions/` | One versioned source; no private constants. |
| X.2 | Provenance | **Olly** | `provenance/` | Hashing, versions, run record. |
| X.3 | Quote telemetry | **Olly** | `quotes/` | Logged from the first quote; folds into `quotes/` in v1 (decisions D6). |

## Frozen contracts — change deliberately

The coupling between the platform and the engine is deliberately tiny. These are versioned contracts; changing one is a deliberate, recorded, conformance-tested event:

- **`SiteModel` JSON schema** (the input contract) — frozen at Milestone 1.
- **`OptimisationResult` JSON schema** + the **versioned stream registry** (the output contract) — frozen at Milestone 1.
- **Acceptance tests** + the **contract-conformance suite** (guards the mock↔real swap, issue 014).
- **Assumption conventions** (units, versioning, sign conventions — see [03_data_contracts.md](03_data_contracts.md)).

## In one line

**Olly** owns the whole GridQ engine — the underwriting platform (M0, M1, M5, M6, M8, X.1–X.3, the M9 mapping and the deterministic mock) and the optimisation/revenue engine (M2, M3, M4, M7) — behind the frozen `OptimisationResult` interface. The two frozen contracts + acceptance/conformance tests + assumption conventions are changed deliberately and versioned.
