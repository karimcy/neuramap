# 003 · Assumption library

**Owner:** Oli
**Module(s):** X.1
**Milestone:** Milestone 2 — Assumption library & provenance
**Status:** Not started

## Why this matters
X.1 is the single versioned source for every number the pipeline uses — no module holds a private constant, the values are diffable, and a release tag is stamped onto each quote so a quote is valid only against the version that produced it (spec §4.8, §6 X.1). Without it, constants scatter across M5/M6/M8/M9, determinism and provenance silently break (spec §9 "Determinism & traceability discipline"), and external placeholders (capex, price curves, credit mapping, haircut) get mistaken for facts (spec §9 commercial risks). It is foundational to the Milestone 2 exit criterion that a stub run is fully traceable and reproducible, and it is buildable now with only the scaffold in place — it does not wait on Olly.

## Requirements
- Provide a file-based, versioned assumptions store under `assumptions/<version>/` holding every number the deterministic pipeline reads — no private constants anywhere else.
- Resolve and expose a single active **release tag** (version identifier) that downstream code reads and stamps onto each quote.
- Hold the **placeholder installed-capex** assumption (the plan's candidate ~£500k/MW) for M5/006, clearly labelled as a placeholder pending the installer-capex benchmark.
- Hold the **price curves and probability weights** for the discrete Low/Central/High scenario set (M6/007), versioned so changing the version changes the result traceably.
- Hold the deterministic **credit → discount-rate / tenor mapping** for M9 consumed by the financial model (M5/006), versioned in the library.
- Hold the **physical/consent gate thresholds** consumed by ingestion (M0/008) and the data-tier boundary parameters, so grading and gating are deterministic.
- Hold the **placeholder perfect-foresight haircut** constant (M7), clearly labelled, with no asserted confidence claim.
- Loader returns values keyed by the resolved version; changing any value requires a version bump (new `assumptions/<version>/`), making diffs reviewable.

## Acceptance criteria
- [ ] No module holds a private constant; every pipeline number is read from the assumption library.
- [ ] Changing any assumption value bumps the version (a new `assumptions/<version>/` set), and the change is diffable file-to-file.
- [ ] The loader resolves and exposes a single active release tag that a quote can record.
- [ ] A quote/run record stores the assumption version it used.
- [ ] The library contains, at minimum, the placeholder installed-capex (~£500k/MW), the price curves and weights, the credit→rate/tenor mapping, the gate thresholds, and the placeholder haircut — each clearly labelled where it is a placeholder.
- [ ] Loading the same version returns identical values (deterministic).

## Dependencies
- 001-project-scaffold.md — repo layout, Python package `gridq/`, pytest harness, and tooling the loader and its tests run under.

## Out of scope (v1)
- Review-cadence tooling, tolerance-driven repricing triggers, and automated diff review (spec §6 X.1 "Later").
- A working calibration backtest or any asserted confidence behind the haircut — placeholder constant only (spec §6 M7).
- The installer-capex benchmark sanity-check / capex benchmark library — capex is a single placeholder in v1 (spec §6 M5).
- Real external data sources for tariff/DUoS bands, Capacity Market, Piclo/local-flex, and route-to-market — all carried as versioned placeholders, not facts (spec §5, §9, §10 Q3–Q8).
- Full scenario richness (policy/REMA branches, richer curves, portfolio Monte Carlo) — the library only stores the small discrete set v1 uses (spec §6 M6 "Later").

## Suggested files to create or modify
- `gridq/assumptions/library.py` — loader that reads the active version and exposes values; surfaces the release tag for stamping onto quotes.
- `gridq/assumptions/version.py` — version resolution (which `assumptions/<version>/` is active; release-tag handling).
- `assumptions/<version>/capex.yaml` — placeholder installed-capex (~£500k/MW), labelled placeholder.
- `assumptions/<version>/scenarios.yaml` — Low/Central/High price curves and probability weights (for 007).
- `assumptions/<version>/credit.yaml` — deterministic credit→rate/tenor mapping (for 006).
- `assumptions/<version>/gates.yaml` — physical/consent gate thresholds and data-tier boundaries (for 008).
- `assumptions/<version>/calibration.yaml` — placeholder perfect-foresight haircut constant, labelled, no asserted confidence.
- `tests/test_assumption_library.py` — version resolution, no-private-constant access, deterministic load, version-bump-on-change.

> Suggested only — no application code is created by this issue document.
