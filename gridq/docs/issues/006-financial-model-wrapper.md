# 006 · Financial model & underwriting outputs

**Owner:** Oli
**Module(s):** M5 (+ M9 deterministic credit mapping)
**Milestone:** Milestone 3 — Mock optimisation & financial outputs
**Status:** Not started

## Why this matters
M5 is the underwriting heart of the platform: it turns an `OptimisationResult` into the bankable numbers — P50/P90 cashflows, DSCR, cost lines and the price that hits target return — that the memo, proposal and quote record all depend on. Without it there is no underwriting pack and no "price-to-target" answer, only a dispatch schedule. Because it builds against the mock `OptimisationResult` (005), it can be developed in full now, in parallel with the real engine, with no wait on M3/M4. M9's deterministic credit→rate/tenor mapping lives here too, so that a counterparty's credit actually moves the discount rate and tenor rather than being decorative.

## Requirements
- Consume a schema-valid `OptimisationResult` (from the mock, issue 005) plus the `SiteModel` and produce real underwriting outputs — never a point estimate where a distribution is required.
- Produce **P50/P90 cashflows** derived from the discrete weighted scenario set supplied by the scenario engine (007), not from hand-picked figures.
- Size/sculpt debt and compute **DSCR** on the P90 case (v1: sized on P90; full sculpting is Later).
- Implement core cost lines: levies on charge volume including round-trip losses, business rates (explicit versioned assumption), insurance, metering/IDNO, DNO costs — every number sourced from the assumption library (003).
- Implement commercial structures as plug-ins with the **fixed £/kWh structure as the default**, with term/indexation/buyout explicit on the structure.
- Compute the **price that hits target return** at the chosen dispatch (price-to-target-return).
- Use the single **versioned placeholder installed-capex assumption** from X.1 (003) — the plan's ~£500k/MW candidate; no private constant.
- Implement the **M9 deterministic credit mapping**: a manual/stub credit input (score or band) maps deterministically to discount rate and tenor; mapping values are versioned in the assumption library (003); no live Creditsafe.
- Be deterministic and traceable: identical inputs + assumption version + seed reproduce identical numbers; **no LLM touches a number**.

## Acceptance criteria
- [ ] Produces P50/P90 cashflows **and** DSCR (not point estimates) for a chosen spec from a mock `OptimisationResult`.
- [ ] P50/P90 derive from the scenario engine's discrete weighted cases (007), not hard-coded values.
- [ ] DSCR is computed on the P90 case.
- [ ] The fixed £/kWh structure plug-in produces a price-to-target-return at the chosen dispatch.
- [ ] A given credit input always yields the same rate/tenor; changing the credit input changes rate/tenor deterministically.
- [ ] No "glowing return at a rate the covenant doesn't justify" — rate/tenor reflect the credit input.
- [ ] All assumptions (capex, cost lines, rate/tenor mapping) read from X.1; the version is recorded and changing a value changes the result traceably.
- [ ] Numbers are reproducible from identical inputs + assumption version + seed; no number originates outside the deterministic model.

## Dependencies
- 002-sitemodel-schema.md — the validated `SiteModel` (load, connection limits, tariff stack, counterparty) the model reads.
- 003-assumption-library.md — versioned installed-capex, cost-line, and credit→rate/tenor assumptions; every number reads from this release.
- 005-mock-optimisation-contract.md — the schema-valid `OptimisationResult` (chosen spec, dispatch, `value_by_stream`, energy accounting) this model consumes; building on the mock means M5 does not wait on Olly.
- 007-scenario-engine.md — the discrete Low/Central/High weighted cases that produce P50/P90.
- Shared contract: the `OptimisationResult` schema (M3/M4 → M5) must be ratified at Milestone 1 before build.

## Out of scope (v1)
- Full set of commercial-structure plug-ins beyond fixed £/kWh (savings-share, capacity fee) — Later.
- Full debt sculpting (v1 sizes on P90 only).
- Full cost-line detail and the installer-capex benchmark / sanity-check library — deferred to Later.
- Live Creditsafe API, research agent, and portfolio concentration flags (M9 Later); v1 ships the deterministic mapping only.
- Real production MILP / real dispatch — M5 runs against the mock only.

## Suggested files to create or modify
- `gridq/finance/model.py` — P50/P90 cashflows, DSCR on the P90 case, price-to-target-return.
- `gridq/finance/structures.py` — commercial-structure plug-ins with fixed £/kWh as default (term/indexation/buyout explicit).
- `gridq/finance/credit.py` — deterministic credit input → rate/tenor mapping (M9), values read from X.1.
- `tests/test_financial_model.py` — P50/P90 + DSCR determinism, price-to-target, and credit-mapping checks.
> Suggested only — no application code is created by this issue document.
