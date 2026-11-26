# 004 · Provenance & run record

**Owner:** Oli
**Module(s):** X.2
**Milestone:** Milestone 2 — Assumption library & provenance
**Status:** Not started

## Why this matters
The mission requires every number to be traceable to its input, assumption version and code version, and every run to be logged from the first quote. The run record is the spine that makes the deterministic core falsifiable: without it, reproducibility and provenance are claims, not facts, and non-determinism (unseeded randomness, wall-clock, dict ordering) can silently break the pipeline. This is the object the quote (009) wraps and that telemetry (010) and outputs (011/012) read from, so it must land before any downstream artifact is built.

## Requirements
- Define an immutable run record that captures, at minimum: a canonical hash of the inputs (SiteModel), the data tier, the assumption library version, the code version (git SHA or equivalent), and the seed.
- Canonical inputs hashing must be order-independent and stable — identical SiteModel content hashes identically regardless of key ordering or serialisation incidentals.
- Capture the code version (git SHA or equivalent fallback when not in a git tree) and the active assumption library version (from 003) at run time.
- Output numbers attach to / link back to the run record so any figure is traceable to its inputs, assumption version and code version.
- The record is constructed once and is immutable thereafter (no in-place mutation of provenance fields after creation).
- Determinism: identical inputs + assumptions + seed reproduce byte-identical outputs; seed everything that could introduce randomness.
- Serialisable to the storage format chosen at Milestone 0 (file-based in v1 unless the DB decision says otherwise), with deterministic serialisation (stable key ordering).

## Acceptance criteria
- [ ] A run record captures inputs hash, data tier, assumption version, code version (git SHA or equivalent), and seed.
- [ ] Any output number can be traced back to its inputs, assumption version and code version via the record.
- [ ] Identical inputs + assumptions + seed reproduce identical outputs (verified by a reproducibility test running the same stub run twice).
- [ ] The canonical inputs hash is stable across key reordering / serialisation differences for equivalent SiteModel content.
- [ ] The run record is immutable after construction (attempting to mutate provenance fields fails or is structurally prevented).
- [ ] A stub run is fully traceable and reproducible (Milestone 2 exit criterion).

## Dependencies
- 001-project-scaffold.md — repo layout, pytest harness, tooling, and the Milestone 0 DB-vs-files storage decision that fixes the run-record persistence format.
- 002-sitemodel-schema.md — the SiteModel input contract that is canonically hashed and whose schema version is recorded.
- 003-assumption-library.md — the versioned assumption release whose version tag is stamped onto the record.
- Code version (git SHA or equivalent) is captured from the environment at run time.

## Out of scope (v1)
- Per-number lineage / drill-down (X.2 "Later").
- The quote object and its lifecycle states (009) — this issue provides the provenance spine only.
- Telemetry rows (010) and document generation (011/012), which consume the record but are built later.
- Realised-vs-underwritten feedback or any post-COD provenance.

## Suggested files to create or modify
- `gridq/provenance/run_record.py` — immutable run record dataclass: inputs hash, data tier, assumption version, code version, seed, plus links from outputs back to the record.
- `gridq/provenance/hashing.py` — canonical, order-independent inputs hashing for the SiteModel.
- `gridq/provenance/versions.py` — capture code version (git SHA or equivalent fallback), active assumption-library version, and the run seed.
- `tests/test_provenance.py` — reproducibility test (identical inputs+assumptions+seed → identical outputs), hash-stability test, immutability test.

> Suggested only — no application code is created by this issue document.
