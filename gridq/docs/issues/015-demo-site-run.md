# 015 · End-to-end demo site run

**Owner:** Oli
**Module(s):** End-to-end pipeline (acceptance)
**Milestone:** Milestone 8 — UI polish & demo flow
**Status:** Not started

## Why this matters
This is the v1 acceptance bar: the whole pipeline working end-to-end on one demo site, proving the mission's claim that the deterministic core, the agentic perimeter and the telemetry discipline are real from the first quote. It exercises the single demo flow the spec defines for the web app — load SiteModel → run → inspect quote → view memo/proposal → see telemetry — and demonstrates the cost-compression story (time-to-quote vs the manual benchmark) on a concrete site. Because it runs on the mock `OptimisationResult` engine (issue 005), it does not wait on Olly's real MILP; the optimisation arrow is the only mocked hop, and every other hop is real, so this run is the rehearsal of the full deterministic, traceable pipeline.

## Requirements
- Provide one representative demo `SiteModel` (a single tier, e.g. T1/T2) that loads and grades cleanly through ingestion (008) and validates against the schema (002).
- Drive the full pipeline on the **mock** engine (005) end-to-end: ingest/grade → mock `OptimisationResult` → financial model (006) → quote record (004/009) → memo + proposal (011/012) → telemetry row (010) — buildable without the real engine.
- Run via the UI shell (013) for the demo flow (load → run → inspect quote → view memo/proposal → see telemetry), and also provide a one-command scripted run for repeatability/CI.
- Be deterministic and traceable: identical inputs + assumption version + seed reproduce identical outputs, and every output number traces to its input, assumption version and code version (X.2).
- Demonstrate the cost-compression story: surface time-to-quote against the captured manual benchmark (from 010) for the demo run.
- Document the demo as a written walkthrough/script so the run can be reproduced and shown without tribal knowledge.

## Acceptance criteria
- [ ] The demo `SiteModel` loads, validates and grades to the expected tier through ingestion (008).
- [ ] One run through the UI (013) produces all artifacts: a quote record, a memo, a proposal and a telemetry row.
- [ ] The run uses the mock engine (`meta.solver == mock`) and completes without any dependency on the real engine (014).
- [ ] Every number in the memo and proposal matches the quote record exactly (no LLM-authored figures).
- [ ] Re-running the demo with the same inputs + assumption version + seed reproduces identical outputs.
- [ ] The one-command scripted run executes the same pipeline end-to-end and produces the same artifacts as the UI flow.
- [ ] The telemetry row records the demo run's tier, versions and human-minutes, and time-to-quote is shown against the manual benchmark.
- [ ] `docs/demo.md` walks through the demo so a fresh operator can reproduce the run.

## Dependencies
- 013-ui-shell.md — the thin web app over the pipeline that the demo flow (load → run → inspect → view → telemetry) runs through.
- 005-mock-optimisation-contract.md — the mock `OptimisationResult` the demo runs on, so the end-to-end run does not wait on Olly.
- 002-sitemodel-schema.md — the `SiteModel` contract the demo site must validate against.
- 003-assumption-library.md — the versioned assumption release stamped on the demo run, holding the constants the demo's numbers depend on.

## Out of scope (v1)
- Replacing the mock with Olly's real MILP engine (014 / Milestone 7); the demo runs on the mock.
- Polished 2026-quality layout, animations or branded proposal layer (M8 "Later").
- Multiple demo sites, a fixture matrix of demo scenarios, or live data feeds — one representative site is the v1 bar.
- Realised-vs-underwritten or hit-rate-by-tier analytics for the demo run (X.3 "Later").
- Any production deployment, hosting or multi-user concerns beyond the local developer demo.

## Suggested files to create or modify
- `examples/demo_site/sitemodel.json` — a representative demo `SiteModel` that loads, validates and grades cleanly.
- `scripts/run_demo.*` — one-command end-to-end run driving the full pipeline on the mock engine for repeatability/CI.
- `docs/demo.md` — the demo script/walkthrough (load → run → inspect quote → view memo/proposal → see telemetry, with the cost-compression story).
- `tests/test_demo_site_run.py` — asserts the end-to-end run produces all artifacts, numbers match the record, and outputs are reproducible.

> Suggested only — no application code is created by this issue document.
