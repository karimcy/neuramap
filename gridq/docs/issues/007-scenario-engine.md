# 007 · Scenario engine

**Owner:** Oli
**Module(s):** M6
**Milestone:** Milestone 3 — Mock optimisation & financial outputs
**Status:** Not started

## Why this matters
The underwriting pack is P50/P90, not a point estimate — without a scenario layer the financial model (M5) has nothing to derive those percentiles from, and "bankable" claims become indefensible. M6 supplies the discrete, probability-weighted Low/Central/High price cases (and a time-to-energisation switch affecting COD) that feed P50/P90, with every weight and curve versioned in the assumption library so a result is traceable to the inputs that produced it. It is built behind a single interface so portfolio Monte Carlo can later slot in without disturbing downstream consumers.

## Requirements
- Provide a small discrete scenario set — Low / Central / High price cases — each with a probability weight and a price curve, sourced from the versioned assumption library (X.1), never from in-code constants.
- Expose these weighted cases through a stable interface that M5 consumes to derive P50/P90 (e.g. weighted distribution over per-case outcomes), so the percentile computation is reproducible.
- Treat time-to-energisation as a single v1 switch (a scenario axis affecting COD) — one setting, not yet a full branch matrix.
- Be fully deterministic: identical assumption version + inputs yield identical cases and weights; no unseeded randomness, wall-clock or dict-ordering dependence.
- Keep the interface shaped so portfolio Monte Carlo and policy branches (REMA-aware) can later replace the discrete set without changing the M5 contract.
- Stamp the assumption version used so changing the scenario assumptions changes the result traceably.

## Acceptance criteria
- [ ] P50/P90 derive from the discrete weighted Low/Central/High cases (not from point estimates).
- [ ] Cases, weights and price curves live in the assumption library (`assumptions/<version>/scenarios.yaml`), with no scenario constants held privately in code.
- [ ] Changing the assumption version changes the scenario output traceably (same code, different versioned inputs → different, attributable result).
- [ ] Time-to-energisation is exposed as a single switch that affects COD in the produced cases.
- [ ] For identical inputs and assumption version, the engine returns identical cases and weights (deterministic).

## Dependencies
- 001-project-scaffold.md — repo layout, `gridq/` package, pytest harness, tooling.
- 003-assumption-library.md — versioned store holding the price curves, case weights and time-to-energisation setting (X.1); supplies the version stamp.
- Feeds M5 (006-financial-model-wrapper.md) and M2 (Olly) as price/scenario inputs — downstream consumers, not upstream blockers.

## Out of scope (v1)
- Policy branches / REMA-aware scenario matrix (Later).
- Richer or calibrated price curves beyond the placeholder set (Later).
- Portfolio Monte Carlo (deferred to portfolio, behind this same interface).
- Multi-state time-to-energisation modelling — v1 is a single switch only.
- Real wholesale/BM/RTM price feeds — placeholders only, never mistaken for real (see spec §5/§9).

## Suggested files to create or modify
- `gridq/scenarios/engine.py` — builds discrete weighted Low/Central/High cases and the COD switch into the P50/P90 inputs M5 consumes; reads all values from X.1.
- `assumptions/<version>/scenarios.yaml` — the price cases, probability weights, curves and time-to-energisation setting, versioned in X.1.
- `tests/test_scenarios.py` — assert determinism, version-driven change, weighted-case → percentile derivation, and the COD switch behaviour.

> Suggested only — no application code is created by this issue document.
