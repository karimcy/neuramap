# 005 · OptimisationResult contract & mock engine

**Owner:** Shared
**Module(s):** OptimisationResult contract (shared) · M3 mock · minimal M2 · M4 mini-enumeration
**Milestone:** Milestone 1 (contract) + Milestone 3 (mock)
**Status:** Not started

## Why this matters
The `OptimisationResult` is the single most load-bearing artifact in the system — the seam between the optimisation engine and Oli's finance/outputs (§6). Field-defining and versioning it is a Milestone 1 exit criterion, agreed jointly so both developers build against the same object. The deterministic mock (M3) is what unblocks Oli: it returns schema-valid results now, so the financial model (006), memo (011) and proposal (012) can be built end-to-end **without waiting on the real engine** (which is issue 014). Without this, M5/M8 risk being built against a fiction and the only mocked arrow in the pipeline (§7) has no defined shape.

## Requirements
- **Define & version the `OptimisationResult` contract** as a JSON Schema with these fields (per §6):
  - `chosen_spec` — `power_kw`, `energy_kwh`, `duration_h`, `coupling` (AC in v1).
  - `dispatch` — half-hourly array (length = HH periods in the modelled horizon; units kW; sign convention **+ = discharge, − = charge**); charge load counts against MIC/ASC.
  - `value_by_stream` — £/yr keyed by stream id (matching M2 market-interface ids), each tagged `priced` | `contracted` | `gated/conditional` (gated streams carry an unlock condition); plus a `total_value` the parts must sum to. The stream-id vocabulary and tag taxonomy form a **versioned stream registry** that is part of the frozen contract — adding or retagging a stream (e.g. wholesale/P415 `gated` → `priced`) bumps the contract version, so a new stream cannot silently bypass conformance or fall out of M5's contracted-vs-merchant split.
  - `energy_accounting` — round-trip losses, parasitic load, annual throughput (cycles/yr) for warranty-cap checks.
  - `meta` — `seed`, `assumption_version`, `code_version`, `model_version` (`mock` | real engine semantic id, e.g. `highs-milp-v1.2`).
- **Minimal M2 market interface** — a generic value-stream interface (value function, constraints, firmness/decay, availability gates, deliverability) with bankable-floor streams (tariff/DUoS red-band avoidance, MIC/ASC headroom) as simple deterministic placeholders; gated streams labelled, not priced. The core must never branch on `market == "GB"`.
- **Deterministic mock engine (M3)** — given a valid SiteModel, return a schema-valid `OptimisationResult`; reproducible for identical inputs + seed; fast; no production MILP. Holds no priced constants (the real engine reads X.1; the mock's placeholders stay declared, not private).
- **M4 mini-enumeration** — enumerate a small P·E·duration grid using the mock dispatch + M5 finance to pick a `chosen_spec` with rationale, exercising the "one engine, called twice" loop (grid size is a v1 implementation choice).
- **Respect checkable invariants** — dispatch never exceeds the SiteModel's MIC/ASC charge limits; throughput ≤ stated duration; `value_by_stream` sums to `total_value`.
- Stamp `meta` from the active assumption version (003) and code version so every mock result is traceable.

## Acceptance criteria
- [ ] The `OptimisationResult` JSON Schema is versioned, and `chosen_spec`, `dispatch`, `value_by_stream`, `energy_accounting`, `meta` are all enumerated and ratified.
- [ ] The mock returns a schema-valid `OptimisationResult` for every valid SiteModel fixture (T0/T1/T2).
- [ ] The mock is deterministic: identical SiteModel + seed produces an identical result.
- [ ] Dispatch never exceeds the SiteModel's MIC/ASC charge limits, and annual throughput is ≤ the stated duration's implied cap.
- [ ] `value_by_stream` entries sum exactly to `total_value`.
- [ ] Each stream is tagged `priced` | `contracted` | `gated/conditional`; a failed-gate stream is excluded from the priced case and listed as conditional upside with its unlock condition.
- [ ] The stream-id vocabulary + tag taxonomy are captured as a versioned registry that is part of the frozen contract (adding/retagging a stream bumps the contract version).
- [ ] Streams implement one interface; the core does not branch on `market == "GB"`.
- [ ] M4 mini-enumeration returns a single chosen spec with rationale over the small P·E·duration grid, reproducibly.
- [ ] `meta` records `seed`, `assumption_version`, `code_version`, and `model_version = mock`.

## Dependencies
- 001-project-scaffold.md — repo layout, pytest harness, tooling (test/lint/typecheck).
- 002-sitemodel-schema.md — the SiteModel input the mock consumes (incl. MIC/ASC limits for invariant checks) and the schema-versioning pattern reused here.
- 003-assumption-library.md — versioned assumptions the mock's placeholder streams declare and the `assumption_version` stamped into `meta`.
- This issue **produces** the shared `OptimisationResult` contract consumed by 006/011/012 and conformed to by 014; the M4 mini-enumeration also leans on 006's finance to score grid points (mock-first, so 006 and this issue co-develop without blocking on Olly).

## Out of scope (v1)
- Full production MILP on linopy/HiGHS (the real engine) — issue 014; the solve-time benchmark experiment is separate (§5, §10 Q11).
- Full composable value stack: Capacity Market, local flex, gated wholesale/BM + ancillary with decay; gate enforcement against real consents (M2 Later).
- Real aggregator/RTM integration — wholesale/P415 stays a labelled gated-upside placeholder (§5).
- Production sizing search with embedded P90-DSCR and smarter-than-enumeration methods (M4 Later).
- Degradation marginal-cost proxy and ex-post SoH modelling beyond the throughput accounting the mock needs (M3 Later).

## Suggested files to create or modify
- `schemas/optimisation_result.schema.json` — versioned JSON Schema for the `OptimisationResult` contract.
- `gridq/optimisation/result.py` — typed `OptimisationResult` (and the stream-tag enum) mirroring the schema.
- `gridq/optimisation/market_interface.py` — generic value-stream interface + minimal bankable-floor placeholders (M2).
- `gridq/optimisation/mock_engine.py` — deterministic mock (M3) returning schema-valid results from a SiteModel.
- `gridq/optimisation/sizing.py` — M4 mini-enumeration over the small P·E·duration grid.
- `tests/test_optimisation_result_schema.py` — schema validity, determinism, invariant (MIC/ASC, throughput, value-sum) and tagging tests.

> Suggested only — no application code is created by this issue document.
