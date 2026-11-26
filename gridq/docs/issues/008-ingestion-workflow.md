# 008 · Ingestion workflow

**Owner:** Oli
**Module(s):** M0
**Milestone:** Milestone 4 — Ingestion workflow
**Status:** Not started

## Why this matters
M0 is the perimeter that turns whatever an installer sends into a validated, graded `SiteModel` before any deterministic step runs (spec §6 M0, §7 data flow). In v1 it accepts a structured `SiteModel` JSON directly — no messy-format parsing yet — runs schema + gate validation, and assigns the data tier T0/T1/T2 that the cost-compression telemetry reports against (spec §4.1–4.2, X.3). Without it, invalid input flows into the engine and finance unchecked, tiers are inconsistent, and the Milestone 4 exit (good/bad fixtures handled correctly; tiering correct) cannot be met. It is buildable now against the frozen SiteModel schema (002) and gate thresholds in the assumption library (003) — it does not wait on the optimisation engine.

## Requirements
- Accept a structured `SiteModel` JSON supplied directly (no parsing of arbitrary CSV/PDF/email installer formats in v1).
- Run schema validation against the versioned `SiteModel` schema (002), then run the physical/consent pass-fail gates (002), surfacing each as a checkable step.
- Reject invalid input with specific, actionable errors that name the offending field/gate and the reason — not generic "invalid" messages.
- Assign a data tier from the data actually present, using the v1 working tier boundaries: **T0** = annual kWh + tariff + postcode (no HH data); **T1** = any genuine HH data present below 12 validated months; **T2** = ≥12 months of HH data that pass completeness/plausibility validation.
- Read the gate thresholds and the data-tier boundary parameters from the assumption library (003) so grading and gating are deterministic and versioned — hold no private constants in ingestion.
- Make tier assignment deterministic: identical input yields identical tier, so the grading tests are reproducible.

## Acceptance criteria
- [ ] A valid `SiteModel` JSON loads successfully and is graded; the assigned tier matches the data present (T0/T1/T2 per the working boundaries).
- [ ] An invalid `SiteModel` JSON is rejected with specific, actionable errors that identify the failing field or gate and the reason.
- [ ] A schema-valid site that fails a physical/consent gate is rejected (or flagged) distinctly from a schema-invalid one.
- [ ] T0/T1/T2 boundaries behave as specified: no HH → T0; some genuine HH below 12 validated months → T1; ≥12 months of validation-passing HH → T2.
- [ ] Gate thresholds and tier-boundary parameters are read from the assumption library (003); ingestion holds no private constants.
- [ ] Grading is deterministic — the same input always produces the same tier across runs.

## Dependencies
- 002-sitemodel-schema.md — the versioned `SiteModel` JSON schema, validator, and physical/consent gate functions ingestion runs.
- 003-assumption-library.md — versioned physical/consent gate thresholds and the T0/T1/T2 data-tier boundary parameters; release tag for deterministic, versioned grading.

## Out of scope (v1)
- LLM-assisted ingestion of arbitrary installer formats (CSV/PDF/email) — deferred to M0 "Later" (spec §6 M0, §5).
- The precise "ranked missing-data" ask that improves a quote, and the human-confirm loop — M0 "Later".
- Defining the schema or the gate functions themselves — those are owned by 002; ingestion consumes them.
- Recording the intake tier into telemetry (X.3) — ingestion assigns the tier; logging it is 010's responsibility.
- Pinning which real-world HH/meter formats must eventually be accepted (spec §10 Q2) — an open question, not a v1 build item.

## Suggested files to create or modify
- `gridq/ingestion/ingest.py` — accept a structured `SiteModel` JSON, run schema + gate validation, and return the validated SiteModel or specific, actionable errors.
- `gridq/ingestion/grading.py` — deterministic T0/T1/T2 tier assignment from the data present, reading boundary parameters from the assumption library.
- `tests/fixtures/sitemodels/invalid_*.json` — invalid fixtures (schema failures and gate failures) for rejection tests.
- `tests/test_ingestion.py` — pytest tests for accept/validate/reject behaviour and deterministic tier assignment against the T0/T1/T2 fixtures.

> Suggested only — no application code is created by this issue document.
