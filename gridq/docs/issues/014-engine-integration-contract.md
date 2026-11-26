# 014 · Engine integration & contract conformance

**Owner:** Olly
**Module(s):** M2/M3/M4/M7 (real engine) + OptimisationResult conformance
**Milestone:** Milestone 7 — engine integration contract
**Status:** Not started

## Why this matters
`OptimisationResult` is the single most load-bearing artifact in the system — the seam between the optimisation engine and Oli's finance/outputs (M5/M8). v1 builds the whole pipeline against a mock (issue 005) so finance and documents are real ahead of the production solver; this issue proves the real engine can drop in behind that same seam without touching M5/M8. Without a conformance bar, the mock and the real MILP can silently diverge and the downstream pipeline gets built against a fiction (spec §9 "Mock ↔ real divergence").

## Requirements
- Build the real value & constraint stack (M2): market mechanics as composable value functions and dispatch constraints under one generic interface; one MWh, one decision; double-counting structurally impossible; failed-gate streams excluded from the priced case and listed as conditional upside with their unlock condition.
- Build the real dispatch engine (M3) as a half-hourly MILP on linopy/HiGHS: value-maximising schedule under physics (charge load vs MIC/ASC, round-trip losses at the price faced, parasitic load at host tariff) and hard warranty/throughput caps; degradation as in-objective marginal-cost proxy plus ex-post SoH.
- Build the real sizing optimiser (M4): search power · energy · duration against the bankable objective (maximise levered return s.t. P90 DSCR ≥ covenant), sized day-one for end-of-life capability.
- Add the calibration hook (M7): a documented backtest interface and a versioned, clearly-labelled placeholder perfect-foresight haircut constant read from the assumption library — no asserted confidence without backtest evidence.
- Real M2/M3/M4 read all priced constants from the assumption library (X.1) — degradation marginal-cost proxy, round-trip loss values, warranty/throughput caps, P90-DSCR covenant — holding no private constants.
- The real engine emits a schema-valid `OptimisationResult` (conforming to the same contract + stream registry the mock (005) satisfies), with `meta.model_version` set to the real engine id.
- Drop in behind the seam WITHOUT changing M5 (finance) or M8 (outputs). The conformance bar is: both mock and real satisfy the schema + stream registry, and M5/M8 need **no code change** and stay deterministic against the real output — **not** identical numbers (the real optimiser legitimately produces different dispatch/value than the mock).

## Acceptance criteria
- [ ] The real engine returns a schema-valid `OptimisationResult` for every valid SiteModel, deterministic for identical inputs+seed.
- [ ] Dispatch never exceeds the SiteModel's MIC/ASC charge limits; annual throughput ≤ the stated duration's warranty/throughput cap; `value_by_stream` sums to `total_value`.
- [ ] Streams implement one interface and the core never branches on `market == "GB"`; a failed-gate stream is excluded from the priced case and listed as conditional upside with its unlock condition.
- [ ] The sizing optimiser returns a single chosen spec with rationale; the chosen spec satisfies the embedded P90-DSCR constraint and at least one grid point is rejected by that constraint.
- [ ] Only a versioned, labelled placeholder haircut is applied; no calibration claim is made without backtest evidence; the M7 hook exists.
- [ ] The conformance suite passes: both the mock and the real engine satisfy the `OptimisationResult` schema + stream registry; M5/M8 consume the real output with no code change and stay deterministic. (A separate golden test holds the *mock's* output fixed and asserts M5/M8 reproduce identically against it — output equality is asserted only against a fixed fixture, never across the mock→real swap.)
- [ ] No M2/M3/M4 module holds a private constant; the engine records the assumption version and code version it used in `meta`.

## Dependencies
- 002-sitemodel-schema.md — the canonical `SiteModel` input contract the engine reads (M1).
- 005-mock-optimisation-contract.md — the ratified `OptimisationResult` schema and the mock that satisfies it; the seam the real engine drops in behind and the parity baseline for conformance.
- Shared contract: the `OptimisationResult` schema must be ratified at Milestone 1 before the real engine is built against it.
- Assumption library (X.1, issue 003): real M2/M3/M4/M7 read degradation/loss/warranty/covenant/haircut values from the versioned store.

## Out of scope (v1)
- The single-site full-year MILP solve-time benchmark (run separately; decides outer-loop search method and whether temporal compression is needed) — spec §10 Q11.
- Full composable stack incl. Capacity Market, local flex, gated wholesale/BM + ancillary with decay and gate enforcement against real consents — spec M2 Later.
- Smarter sizing search beyond enumeration unless the solve-time benchmark demands it — spec M4 Later.
- A working backtesting harness and realised-vs-underwritten feedback (interface/placeholder only in v1) — spec M7 Later.
- Real aggregator/RTM integration; wholesale/P415 stays gated-upside placeholder — spec §5.

## Suggested files to create or modify
- `gridq/optimisation/engine.py` — real half-hourly dispatch MILP on linopy/HiGHS (M3).
- `gridq/optimisation/value_stack.py` — real composable value/constraint streams under the generic market interface (M2).
- `gridq/optimisation/sizing.py` — real P·E·duration search against the bankable objective (M4) (modify).
- `gridq/calibration/` — backtest hook and labelled placeholder haircut wiring (M7).
- `schemas/` — `OptimisationResult` schema referenced for conformance (shared with 002/005).
- `tests/test_optimisation_contract_conformance.py` — both engines satisfy the schema + stream registry; M5/M8 consume the real output with no code change (plus a separate golden test of M5/M8 against a fixed mock fixture).

> Suggested only — no application code is created by this issue document.
