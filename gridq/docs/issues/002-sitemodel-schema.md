# 002 · SiteModel schema & validation

**Owner:** Shared
**Module(s):** M1 (shared contract)
**Milestone:** Milestone 1 — Schema & contracts
**Status:** Not started

## Why this matters
The `SiteModel` is the canonical input contract that every other module reads — ingestion grades it, the optimisation engine reasons from it, finance prices it, and provenance hashes it. It is one of only two coupling points between Olly, so it must be co-agreed and frozen early; ambiguity here stalls both builders and lets schema churn ripple across every module. Without a versioned schema, validators and physical/consent gates, no quote can be validated, graded or reproduced, and the engine has no trustworthy site to reason from.

## Requirements
- Define a versioned JSON Schema for `SiteModel` covering: half-hourly load; connection (MIC/MEC, ASC band, voltage, ANM); the full tariff stack including levy lines; consents (G98/G99/G100, export MPAN, DNO application state); supply boundary & supplier consent; physical pass/fail inputs (footprint, fire separation); and counterparty.
- **No `archetype` field** — the engine reasons from the site's own data, never a category label.
- Provide a typed `SiteModel` representation and a validator that produces clear, specific, actionable errors (not generic "invalid" messages).
- Implement the physical/consent pass-fail gates (e.g. footprint, fire separation, consent state) as checkable functions, distinct from schema validation.
- Carry an explicit schema version on the contract so it can be recorded on every quote.
- Ship example fixtures at data tiers T0, T1 and T2, plus at least one invalid fixture, so grading and gate tests are deterministic.
- The schema is co-agreed with Olly and ratified as a Milestone 1 exit criterion; the net-load interface is left openable for solar later without breaking v1.

## Acceptance criteria
- [ ] The JSON Schema validates the good T0/T1/T2 fixtures and rejects the invalid fixture(s) with specific, actionable errors.
- [ ] Physical/consent gates flag fail conditions (e.g. insufficient footprint, missing/failed consent) and pass valid sites.
- [ ] The schema contains no `archetype` field.
- [ ] The schema is versioned and the version string is exposed for recording on every quote.
- [ ] All required field groups (HH load, connection, tariff+levy stack, consents+export MPAN+DNO state, supply boundary & supplier consent, physical gate inputs, counterparty) are present in the schema.
- [ ] The `OptimisationResult` field shape is ratified jointly with Olly in the same milestone (tracked in 005/014), so Olly can build the real engine against the contract.

## Dependencies
- 001-project-scaffold.md — repo layout, Python package `gridq/`, pytest harness and quality tooling to author and test the schema against.

**Related contracts (not blocking upstreams):**
- Co-agreed with Olly as the M1 shared contract. The *paired* `OptimisationResult` contract is defined later in 005-mock-optimisation-contract.md and conformed in 014-engine-integration-contract.md — those are downstream of this schema, not prerequisites for it.

## Out of scope (v1)
- Net-load interface for solar (v2) — held open in the schema but not implemented.
- A richer consent state machine — v1 carries DNO application state as a field, not a full state machine.
- AI/LLM parsing of arbitrary installer formats into a SiteModel (that is M0/008's "Later").
- Data-tier grading logic itself (that lives in ingestion, 008) beyond providing tier-labelled fixtures.

## Suggested files to create or modify
- `schemas/sitemodel.schema.json` — versioned JSON Schema for the canonical SiteModel contract.
- `gridq/sitemodel/model.py` — typed `SiteModel` representation.
- `gridq/sitemodel/validate.py` — schema validation with specific, actionable error reporting.
- `gridq/sitemodel/gates.py` — physical/consent pass-fail gate functions.
- `tests/fixtures/sitemodels/{t0,t1,t2}.json` — valid example fixtures at each data tier (plus at least one invalid fixture for rejection tests).
- `tests/sitemodel/` — pytest tests for schema validation, gates and fixture grading.

> Suggested only — no application code is created by this issue document.
