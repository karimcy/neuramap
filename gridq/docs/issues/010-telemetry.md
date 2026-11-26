# 010 · Quote telemetry

**Owner:** Oli
**Module(s):** X.3
**Milestone:** Milestone 5 — Quote lifecycle & telemetry
**Status:** Not started

## Why this matters
X.3 is the falsifiable cost-compression dataset: every quote logged from day one so the time-to-quote-vs-manual claim is evidence, not assertion. The named project risk is telemetry-from-day-one slipping — if it is retrofitted, the cost-compression claim is unfalsifiable and the manual quoting benchmark is lost forever, because the system erases the manual baseline the moment it works. This is why telemetry must ship with the first quote (Milestone 5), not later, and why the manual benchmark is captured before automation overwrites it.

## Requirements
- Produce one telemetry record per run, emitted from the first quote — telemetry wraps every run, it is not bolted on afterwards.
- Capture, per run: data tier at intake (T0/T1/T2 from 008), human-minutes (manual entry acceptable in v1), installer/quote iterations, outcome (e.g. win/loss/draft with reason where available), and the assumption-library version + code version that produced the run.
- Read provenance fields (inputs hash, data tier, assumption version, code version, seed) from the quote/run record (004/009) rather than recomputing them, so telemetry agrees with the run record by construction.
- Capture the manual quoting benchmark at the start (a baseline human-minutes figure for the pre-system process) before the system erases it, stored once and clearly labelled as the manual baseline.
- Append-only store: telemetry rows accumulate, never overwrite, in the storage format chosen at Milestone 0 (file-based in v1 unless the DB decision says otherwise), with deterministic serialisation.
- Link each telemetry record back to its quote record (e.g. by inputs hash / quote id) so a row is always traceable to the run it describes.

## Acceptance criteria
- [ ] Every run produces a telemetry record (no run completes without one).
- [ ] The telemetry record captures data tier at intake, human-minutes, iterations, outcome, assumption version and code version.
- [ ] The manual quoting benchmark is captured at the start and stored as a labelled baseline.
- [ ] Telemetry ships with the first quote, not retrofitted (a run through the Milestone 5 pipeline yields both a quote record and a telemetry row).
- [ ] The telemetry store is append-only (writing a new run does not mutate or delete prior rows).
- [ ] Each telemetry row links back to its quote/run record and its tier/version fields match that record.

## Dependencies
- 004-provenance-and-run-record.md — the immutable run record providing inputs hash, data tier, assumption version, code version and seed that telemetry reads and agrees with.
- 009-quote-lifecycle.md — the quote object and lifecycle state (draft → indicative → underwritten…) that supplies the run identity and outcome telemetry logs against.
- 003-assumption-library.md (via 004) — the versioned assumption release whose tag is recorded on each telemetry row.
- 008-ingestion-workflow.md — supplies the data tier at intake (T0/T1/T2) that telemetry records.

## Out of scope (v1)
- Realised-vs-underwritten performance after COD (X.3 "Later").
- Hit-rate-by-tier and error-by-stream analytics or dashboards (X.3 "Later").
- Human-minutes broken down by person (v1 logs human-minutes; per-person attribution is later).
- Any automated capture of human-minutes — manual entry is acceptable in v1.

## Suggested files to create or modify
- `gridq/telemetry/telemetry.py` — per-run telemetry record (data tier, human-minutes, iterations, outcome, assumption/code versions, link to quote record) plus the append-only writer and the one-time manual-benchmark capture.
- `telemetry/` — append-only telemetry store (format per the 001 Milestone 0 storage decision).
- `tests/test_telemetry.py` — asserts every run emits a record, fields match the quote record, the store is append-only, and the manual benchmark is captured at the start.

> Suggested only — no application code is created by this issue document.
