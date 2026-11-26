# 009 · Quote lifecycle (first-class quote object)

**Owner:** Oli
**Module(s):** X.2 (quote object) + lifecycle
**Milestone:** Milestone 5 — Quote lifecycle & telemetry
**Status:** Not started

## Why this matters
The spec (§4 MVP item 5, §6 X.2) requires a traceable quote object — an immutable run record per quote carrying inputs hash, data tier, assumption version, code version, optimisation result, finance outputs, gate/credit status, and lifecycle state. The quote is the artifact the whole pipeline converges on: outputs (011/012) draft from it, telemetry (010) reads it, and the capital/commercial readers eventually consume it. Without a first-class quote that pins each state to an immutable, versioned snapshot, "consistent by construction" and reproducibility collapse — a number could change under a quote without anyone being able to tell which inputs and versions produced it.

## Requirements
- Define a first-class, immutable `Quote` object that wraps the run record (004): inputs hash, data tier, assumption version, code version, seed, plus optimisation result, finance outputs, and gate/credit status as available.
- Each lifecycle state produces a new immutable snapshot — never an in-place mutation; advancing state appends, it does not overwrite.
- Implement the lifecycle state machine over states `draft → indicative → underwritten → contracted → operating`, with transitions only along that ordered path (no skipping or reversing in v1).
- Carry a `history` of prior states/snapshots so the trajectory of a quote is reconstructable.
- A quote is valid only against the assumption + code versions that produced it; the snapshot records those versions and they do not float.
- Be buildable WITHOUT Olly: the optimisation result a quote carries comes from the mock `OptimisationResult` (005), so the quote object and its lifecycle can be built and tested entirely against the mock seam.
- Serialisable to the Milestone 0 storage format (file-based in v1 unless the DB decision says otherwise) with deterministic serialisation.

## Acceptance criteria
- [ ] A quote is an immutable record carrying inputs hash, data tier, assumption version, code version, seed, and (where present) optimisation result, finance outputs, and gate/credit status.
- [ ] The lifecycle exposes the states `draft → indicative → underwritten → contracted → operating`; a legal transition produces a new snapshot and an illegal transition (skip/reverse) is rejected.
- [ ] Advancing a quote's state does not mutate the prior snapshot; prior states are preserved in the quote's history.
- [ ] Each snapshot pins the assumption version and code version that produced it; the quote is valid only against those versions.
- [ ] The quote can be constructed and lifecycle-tested using only the mock `OptimisationResult` (005), with no dependency on the real engine.
- [ ] Every run produces a versioned quote record (Milestone 5 exit criterion, shared with telemetry 010).

## Dependencies
- 002-sitemodel-schema.md — the SiteModel input contract whose hash and schema version the quote records.
- 004-provenance-and-run-record.md — the immutable run record (inputs hash, data tier, assumption version, code version, seed) that the quote object wraps.
- 005-mock-optimisation-contract.md — supplies the schema-valid mock `OptimisationResult` a quote carries, so this issue is buildable without waiting on the real engine (014).
- Assumption library (003, via 004) — the versioned release each snapshot pins to.

## Out of scope (v1)
- Per-number lineage / drill-down (X.2 "Later").
- Realised-vs-underwritten feedback or any post-COD provenance against the `operating` state (X.2/X.3 "Later").
- Telemetry rows themselves (010) and document generation (011/012), which read the quote but are separate issues.
- Reverse/branch transitions, re-quoting workflows, or human-sign approval gates beyond the linear v1 state path.
- A database-backed quote store unless the Milestone 0 DB-vs-files decision requires it.

## Suggested files to create or modify
- `gridq/quote/quote.py` — immutable `Quote` object wrapping the run record (004), carrying optimisation result, finance outputs, gate/credit status, lifecycle state, and version-pinned snapshot fields.
- `gridq/quote/lifecycle.py` — the `draft → indicative → underwritten → contracted → operating` state machine with legal-transition enforcement and history tracking.
- `tests/test_quote_lifecycle.py` — tests for immutability, legal/illegal transitions, history preservation, version pinning, and construction from the mock `OptimisationResult`.

> Suggested only — no application code is created by this issue document.
